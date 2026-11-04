"""Hardware input sources. Each runs on its own daemon thread and only puts
events on a queue, so a slow, silent or broken sensor can never block or crash
the render loop. Sockets that fail are reopened with a back-off.

udp_json  JSON datagrams (the convention used by Mofid's other installations;
          easy to send from TouchDesigner, a LiDAR bridge or a camera tracker).
tuio      TUIO 1.1 over OSC/UDP (/tuio/2Dcur, /tuio/2Dblb, /tuio/2Dobj), the
          standard output of most LiDAR / IR "touch wall" software. Each new
          session id (a new blob crossing the sensor plane) is one impact.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import socket
import threading
import time

from .events import Control, Impact

log = logging.getLogger(__name__)

CONTROL_KINDS = {"start", "reset", "player", "throw_missed", "heartbeat"}


def parse_json_message(obj: object, units: str, wall_w_cm: float, wall_h_cm: float, t: float, source: str) -> list:
    """Turn one decoded JSON message into Impact / Control events.

    Raises ValueError for anything malformed, so the caller can count it.
    """
    if not isinstance(obj, dict):
        raise ValueError("message is not a JSON object")
    kind = obj.get("type")
    if kind in ("impact", "hit"):
        points = [obj]
    elif kind == "impacts":
        points = obj.get("points")
        if not isinstance(points, list):
            raise ValueError("'impacts' needs a 'points' list")
    elif kind in CONTROL_KINDS:
        data = {k: v for k, v in obj.items() if k != "type"}
        return [Control(kind, t, source, data)]
    else:
        raise ValueError(f"unknown message type {kind!r}")

    if units == "cm":
        sx, sy = 1.0 / wall_w_cm, 1.0 / wall_h_cm
    elif units == "m":
        sx, sy = 100.0 / wall_w_cm, 100.0 / wall_h_cm
    else:
        sx = sy = 1.0
    out = []
    for p in points:
        if not isinstance(p, dict):
            raise ValueError("impact point is not an object")
        x, y = p.get("x"), p.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or isinstance(x, bool):
            raise ValueError("impact needs numeric x and y")
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("impact coordinates must be finite")
        out.append(Impact(float(x) * sx, float(y) * sy, t, source, (float(x), float(y))))
    return out


class Source:
    name = "source"

    def __init__(self, out: queue.SimpleQueue):
        self.out = out
        self.state = "stopped"  # stopped | listening | error
        self.error = ""
        self.packets = 0
        self.bad_packets = 0
        self.events = 0
        self.last_packet: float | None = None  # perf_counter
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._guarded_run, name=f"input-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _guarded_run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._run()
                backoff = 1.0
            except Exception as exc:  # never let a sensor problem escape the thread
                self.state = "error"
                self.error = f"{type(exc).__name__}: {exc}"
                log.error("%s input failed: %s (retrying in %.0fs)", self.name, self.error, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2.0, 10.0)
        self.state = "stopped"

    def _run(self) -> None:
        raise NotImplementedError

    def status(self) -> dict:
        age = None if self.last_packet is None else time.perf_counter() - self.last_packet
        return {
            "name": self.name,
            "state": self.state,
            "error": self.error,
            "packets": self.packets,
            "bad": self.bad_packets,
            "events": self.events,
            "last_packet_age": age,
        }


class UdpJsonSource(Source):
    name = "udp_json"

    def __init__(self, out: queue.SimpleQueue, host: str, port: int, units: str, wall_w: float, wall_h: float):
        super().__init__(out)
        self.host, self.port, self.units = host, port, units
        self.wall_w, self.wall_h = wall_w, wall_h

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No SO_REUSEADDR: on Windows it would let a second copy of the game
            # silently share the port. A busy port shows up as an input error (F3).
            sock.bind((self.host, self.port))
            sock.settimeout(0.25)
            self.state, self.error = "listening", ""
            log.info("udp_json listening on %s:%d", self.host, self.port)
            while not self._stop.is_set():
                try:
                    data, _addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except ConnectionResetError:
                    continue  # Windows reports ICMP port-unreachable this way; harmless
                t = time.perf_counter()
                self.packets += 1
                self.last_packet = t
                self._handle(data, t)
        finally:
            sock.close()

    def _handle(self, data: bytes, t: float) -> None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self.bad_packets += 1
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events = parse_json_message(json.loads(line), self.units, self.wall_w, self.wall_h, t, self.name)
            except (json.JSONDecodeError, ValueError) as exc:
                self.bad_packets += 1
                if self.bad_packets <= 20 or self.bad_packets % 100 == 0:
                    log.warning("udp_json: ignored bad message (%s): %.120s", exc, line)
                continue
            for ev in events:
                self.events += 1
                self.out.put(ev)


class TuioSource(Source):
    """TUIO 1.1 listener. A new session id = one impact at its first position.

    TUIO coordinates are already normalised 0..1 with (0, 0) at the top-left,
    the same convention the game uses; use input.calibration for any flips.
    """

    name = "tuio"
    # profile -> index of x in a "set" message (after the "set" string itself)
    PROFILES = {"/tuio/2Dcur": 1, "/tuio/2Dblb": 1, "/tuio/2Dobj": 2}

    def __init__(self, out: queue.SimpleQueue, host: str, port: int):
        super().__init__(out)
        self.host, self.port = host, port
        self._seen: dict[str, set] = {p: set() for p in self.PROFILES}

    def _run(self) -> None:
        try:
            from pythonosc.dispatcher import Dispatcher
            from pythonosc.osc_server import BlockingOSCUDPServer
        except ImportError:  # optional dependency
            self.state, self.error = "error", "python-osc is not installed (pip install python-osc)"
            log.error(self.error)
            self._stop.wait()
            return

        disp = Dispatcher()
        for profile in self.PROFILES:
            disp.map(profile, self._on_message, profile)
        server = BlockingOSCUDPServer((self.host, self.port), disp)
        server.timeout = 0.25  # handle_request() returns at least this often to check for stop
        self.state, self.error = "listening", ""
        log.info("tuio listening on %s:%d", self.host, self.port)
        try:
            while not self._stop.is_set():
                server.handle_request()
        finally:
            server.server_close()

    def _on_message(self, address: str, fixed: list, *args) -> None:
        profile = fixed[0]
        t = time.perf_counter()
        self.packets += 1
        self.last_packet = t
        if not args:
            return
        cmd = args[0]
        seen = self._seen[profile]
        try:
            if cmd == "alive":
                seen.intersection_update(args[1:])
            elif cmd == "set":
                sid = args[1]
                if sid in seen:
                    return
                xi = 1 + self.PROFILES[profile]
                x, y = float(args[xi]), float(args[xi + 1])
                if not (math.isfinite(x) and math.isfinite(y)):
                    raise ValueError("non-finite coordinates")
                seen.add(sid)
                self.events += 1
                self.out.put(Impact(x, y, t, self.name, (x, y)))
        except (IndexError, TypeError, ValueError):
            self.bad_packets += 1
