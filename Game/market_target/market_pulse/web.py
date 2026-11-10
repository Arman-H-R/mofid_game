"""The operator's phone page: a small web server on the local network.

Somebody types the player's name on a phone and presses Play; the round starts on
the wall. The page also shows the saved rounds and downloads them as a CSV file.

The server runs on its own threads and never touches the game: a request leaves a
Command in a queue, and the main loop answers it between two frames (App._serve_panel).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import socket
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import Section
from .game.flow import name_is_valid, tidy_name
from .game.results import ResultsStore, history_csv

log = logging.getLogger(__name__)

ANSWER_S = 3.0  # how long a request waits for the game
MAX_BODY = 4096
HISTORY_SHOWN = 30
FILES = {  # the only files the page may fetch: name -> (folder under assets, type)
    "IRANSansX-Regular.ttf": ("fonts", "font/ttf"),
    "IRANSansX-Bold.ttf": ("fonts", "font/ttf"),
    "IRANSansX-Black.ttf": ("fonts", "font/ttf"),
    "logo_fa_horizontal.png": ("images", "image/png"),
}


def lan_address() -> str:
    """This computer's address on the network. Connecting a UDP socket sends nothing."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def phone_address(web: Section) -> str:
    """Where phones open the operator's page (name, Play), "" when it is turned off."""
    if not web.enabled:
        return ""
    return f"http://{lan_address()}:{web.port}"


class Command:
    def __init__(self, kind: str, name: str = ""):
        self.kind = kind
        self.name = name
        self.at = time.monotonic()
        self.ok = False
        self.done = threading.Event()
        self._lock = threading.Lock()
        self._state = "waiting"  # waiting -> taken (by the game) or expired (the page gave up), never both

    def claim(self) -> bool:
        with self._lock:
            if self._state == "waiting":
                self._state = "taken"
            return self._state == "taken"

    def expire(self) -> bool:
        with self._lock:
            if self._state == "waiting":
                self._state = "expired"
            return self._state == "expired"

    def answer(self, ok: bool) -> None:
        self.ok = ok
        self.done.set()


class Panel:
    def __init__(self, cfg: Section, store: ResultsStore, assets: Path):
        self.cfg = cfg
        self.store = store
        self.assets = assets
        self.url = ""
        self.error = ""
        self.status: dict = {"state": "attract"}  # replaced as a whole by the main loop
        self._commands: queue.SimpleQueue = queue.SimpleQueue()
        self._server: _Server | None = None

    def start(self) -> bool:
        web = self.cfg.web
        try:
            self._server = _Server((web.host, web.port), _Handler)
        except OSError as exc:
            self.error = str(exc)
            log.error("phone page: cannot listen on port %s: %s", web.port, exc)
            return False
        self._server.panel = self
        threading.Thread(target=self._server.serve_forever, name="web", daemon=True).start()
        self.url = f"http://{lan_address()}:{web.port}"
        log.info("phone page: %s", self.url)
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- main loop's side ---------------------------------------------------------
    def take(self) -> list[Command]:
        """What the page has asked for since the last frame. A request that already
        gave up is dropped, or a game that hung for a moment would start it late."""
        out = []
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return out
            if cmd.claim():
                out.append(cmd)

    # -- server's side ------------------------------------------------------------
    def ask(self, kind: str, name: str = "") -> bool | None:
        """True or False from the game, or None if it did not answer in time."""
        cmd = Command(kind, name)
        self._commands.put(cmd)
        if cmd.done.wait(ANSWER_S) or not cmd.expire():  # taken just now: its answer follows in this frame
            return cmd.ok if cmd.done.wait(ANSWER_S) else None
        return None

    def history(self) -> dict:
        records = self.store.history()
        records.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
        return {"total": len(records), "rounds": [{
            "name": r.get("player_name", ""),
            "score": r.get("score", 0),
            "hits": r.get("total_hits", 0),
            "throws": r.get("throws", 0),
            "style": (r.get("profile") or {}).get("label", ""),
            "timestamp": r.get("timestamp", 0),
        } for r in records[:HISTORY_SHOWN]]}


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # on Windows it would hide a port already in use
    panel: Panel


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    timeout = 10  # a phone that walks out of range does not hold a thread for long

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log.debug("phone page: %s %s", self.address_string(), format % args)

    def _send(self, code: int, body: bytes, kind: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _allowed(self) -> bool:
        """The optional PIN. The page keeps it in a cookie, so the CSV link works too."""
        pin = str(self.server.panel.cfg.web.pin)
        if not pin:
            return True
        try:
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get("pin")
        except CookieError:
            morsel = None
        given = unquote(morsel.value) if morsel else ""
        if hmac.compare_digest(given.encode("utf-8"), pin.encode("utf-8")):
            return True
        self._json(401, {"ok": False, "error": "pin"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        panel = self.server.panel
        path = urlsplit(self.path).path
        if path in ("/api/state", "/api/history", "/export.csv") and not self._allowed():
            return
        try:
            if path == "/":
                self._send(200, (panel.assets / "web" / "panel.html").read_bytes(), "text/html; charset=utf-8")
            elif path.startswith("/files/") and path[7:] in FILES:
                folder, kind = FILES[path[7:]]
                self._send(200, (panel.assets / folder / path[7:]).read_bytes(), kind)
            elif path == "/api/state":
                web, flow = panel.cfg.web, panel.cfg.flow
                self._json(200, {**panel.status, "title": web.title, "name_max": flow.name_max_chars})
            elif path == "/api/history":
                self._json(200, panel.history())
            elif path == "/export.csv":
                stamp = time.strftime("%Y%m%d-%H%M")
                self._send(200, history_csv(panel.store.history()), "text/csv; charset=utf-8",
                           {"Content-Disposition": f'attachment; filename="market_target_history_{stamp}.csv"'})
            else:
                self._json(404, {"ok": False, "error": "not found"})
        except OSError as exc:
            log.warning("phone page: GET %s failed: %s", path, exc)

    def do_POST(self) -> None:  # noqa: N802
        panel = self.server.panel
        path = urlsplit(self.path).path
        # Another website open on the phone cannot send this type without asking first.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(415, {"ok": False, "error": "type"})
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
        except ValueError:
            size = -1
        if not 0 <= size <= MAX_BODY:
            self._json(413, {"ok": False, "error": "size"})
            return
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
        except (ValueError, OSError):
            body = None
        if not isinstance(body, dict):
            self._json(400, {"ok": False, "error": "body"})
            return
        if not self._allowed():
            return
        if path == "/api/play":
            name = tidy_name(str(body.get("name", "")), panel.cfg.flow.name_max_chars)
            if not name_is_valid(name):
                self._json(400, {"ok": False, "error": "name"})
                return
            self._reply(panel.ask("play", name), name)
        elif path == "/api/cancel":
            self._reply(panel.ask("cancel"))
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def _reply(self, answer: bool | None, name: str = "") -> None:
        if answer is None:
            self._json(503, {"ok": False, "error": "no_answer"})
        elif answer:
            self._json(200, {"ok": True, "name": name})
        else:
            self._json(409, {"ok": False, "error": "busy"})
