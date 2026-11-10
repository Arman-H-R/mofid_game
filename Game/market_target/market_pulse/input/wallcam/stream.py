"""Camera frames for the hit detector: every frame (a ball track needs them all), each with the time
it was taken, on time.perf_counter()'s clock (the game's input clock).

Two ways in:
  "ws"    the camera's own web-player stream (WebSocket on port 80, what its web page plays), decoded
          with PyAV the moment a frame arrives. ~60 ms sooner than RTSP on the Witness camera, and every
          frame carries the camera's timestamp, so the capture time does not jitter with the network.
  "rtsp"  any camera, through OpenCV's FFmpeg reader; capture time = arrival - delay.

Capture time from the camera's timestamp: arrival - stamp is smallest for the frames that came
fastest; that minimum over the last seconds (so a drifting camera clock is followed) plus the stamp,
minus `delay_s` (encoding + network for those fastest frames), is when the frame was taken.

The WebSocket packet layout (little-endian):
    24-byte header : 77 88 00 00 | payload length (u32) | command (u32) | 12 bytes unused
        command 0x66 = start preview (request and its ack), 0xC8 = media frame
    media payload  : 28-byte frame header + raw Annex-B video
        "ht" | frame type (1 = keyframe) | pad | frame no (u32) | timestamp ms (u32) | 4 bytes ?
        | codec (1 = H.264, 6 = H.265) | ? | width (u16) | height (u16) | fps (u8) | pad | data length (u32)
"""
from __future__ import annotations

import base64
import logging
import os
import queue
import select
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

try:
    import av
except ImportError:  # only the "ws" source needs it
    av = None

log = logging.getLogger(__name__)

MAGIC = b"\x77\x88\x00\x00"
CMD_START = 0x66
CMD_MEDIA = 0xC8
HEADER = struct.Struct("<4sII12x")
FRAME = struct.Struct("<2sBxII4xBxHHBxI")
CODECS = {1: "h264", 6: "hevc"}
STREAM_IDS = {"main": 1, "sub": 2}


@dataclass
class Frame:
    id: int
    image: np.ndarray  # BGR
    t: float  # time.perf_counter() when the camera took it (estimated)
    late: float = 0.0  # s from taking it to handing it out (normally ~0.1; seconds when the camera stalls)


class CaptureClock:
    """Camera timestamps (s) -> perf_counter time of capture. Sliding-window minimum of arrival - stamp."""

    def __init__(self, delay_s: float, window_s: float = 8.0):
        self.delay_s = delay_s
        self.window_s = window_s
        self._mins: deque[tuple[float, float]] = deque()  # (arrival, offset), offsets increasing

    def reset(self) -> None:
        self._mins.clear()

    def capture_time(self, stamp_s: float, arrival: float) -> float:
        off = arrival - stamp_s
        if self._mins and abs(off - self._mins[0][1]) > 5.0:  # camera clock reset / reconnect
            self._mins.clear()
        while self._mins and self._mins[-1][1] >= off:
            self._mins.pop()
        self._mins.append((arrival, off))
        while self._mins[0][0] < arrival - self.window_s:
            self._mins.popleft()
        return stamp_s + self._mins[0][1] - self.delay_s


def start_request(stream: str) -> bytes:
    req = bytearray(HEADER.size + 44)
    HEADER.pack_into(req, 0, MAGIC, 44, CMD_START)
    req[12:16] = bytes.fromhex("0f8f0080")  # copied from the web player, meaning unknown
    req[24] = 0  # channel
    req[25] = STREAM_IDS[stream]
    req[36:38] = b"ht"
    return bytes(req)


class WebSocket:
    """Just enough of a WebSocket client for the camera (binary messages, ping/pong)."""

    def __init__(self, host: str, port: int = 80, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("camera closed the connection during the WebSocket handshake")
            resp += chunk
        head, rest = resp.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(f"WebSocket handshake refused: {head.splitlines()[0]!r}")
        self.buf = bytearray(rest)

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(262144)
            if not chunk:
                raise ConnectionError("camera closed the connection")
            self.buf += chunk
        data = bytes(self.buf[:n])
        del self.buf[:n]
        return data

    def pending(self) -> bool:
        return bool(self.buf) or bool(select.select([self.sock], [], [], 0)[0])

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def send_binary(self, payload: bytes) -> None:
        self._send_frame(0x2, payload)

    def recv_message(self) -> bytes:
        parts = []
        while True:
            b0, b1 = self._exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._exact(8))[0]
            mask = self._exact(4) if b1 & 0x80 else None
            data = self._exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise ConnectionError("camera closed the WebSocket")
            if opcode == 0x9:
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:
                continue
            parts.append(data)
            if b0 & 0x80:
                return b"".join(parts)

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()


class CameraStream:
    """Background reader. get() hands out every frame in order; latest() the newest one.
    If the reader of get() falls behind by more than `queue_size` frames, the oldest are dropped
    (counted in `dropped`, and `gap` is set so a tracker can start afresh)."""

    def __init__(self, source: str = "ws", host: str = "192.168.70.66", stream: str = "sub", url: str = "",
                 delay_s: float = 0.06, queue_size: int = 50, width: int = 0):
        """width: frames wider than this are made this wide (keeping the shape) as they arrive, so the
        main stream (1920x1080, a steady 30 fps on the Witness camera; the sub stream drops to ~25) costs
        the detector no more than the sub stream. 0: as the camera sends them."""
        if source == "ws":
            if av is None:
                raise RuntimeError("the camera's web stream needs PyAV:  pip install av")
            if stream not in STREAM_IDS:
                raise ValueError(f"stream must be one of {', '.join(STREAM_IDS)}, not {stream!r}")
        elif source == "rtsp":
            if not url:
                raise ValueError("source rtsp needs a url")
        else:
            raise ValueError(f"unknown camera source {source!r} (ws or rtsp)")
        self.source, self.host, self.stream, self.url = source, host, stream, url
        self.clock = CaptureClock(delay_s)
        self.delay_s = delay_s
        self.width = max(0, int(width))
        self.frames: queue.Queue[Frame] = queue.Queue(maxsize=queue_size)
        self.cond = threading.Condition()
        self._latest: Frame | None = None
        self.size: tuple[int, int] | None = None
        self.status = "connecting"
        self.error = ""
        self.fps = 0.0
        self.dropped = 0
        self.gap = False
        self.frame_count = 0
        self._next_id = 0
        self._count, self._t0 = 0, time.perf_counter()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="wallcam-stream", daemon=True)
        self.thread.start()

    # -- consumer side ---------------------------------------------------------------------------
    def get(self, timeout: float = 0.5) -> Frame | None:
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def latest(self) -> Frame | None:
        return self._latest

    def wait_latest(self, last_id: int, timeout: float) -> Frame | None:
        with self.cond:
            self.cond.wait_for(lambda: (self._latest is not None and self._latest.id != last_id)
                               or self._stop.is_set(), timeout)
            return self._latest

    def stop(self) -> None:
        self._stop.set()
        with self.cond:
            self.cond.notify_all()
        self.thread.join(timeout=3)

    # -- producer side ---------------------------------------------------------------------------
    def _publish(self, image: np.ndarray, t: float) -> None:
        if self.width and image.shape[1] > self.width:
            import cv2  # only the "ws" source gets here without OpenCV loaded; the game has it anyway
            h = max(1, int(round(image.shape[0] * self.width / image.shape[1])))
            image = cv2.resize(image, (self.width, h), interpolation=cv2.INTER_AREA)
        self._next_id += 1
        frame = Frame(self._next_id, image, t, time.perf_counter() - t)
        self.size = (image.shape[1], image.shape[0])
        while True:
            try:
                self.frames.put_nowait(frame)
                break
            except queue.Full:
                try:
                    self.frames.get_nowait()
                    self.dropped += 1
                    self.gap = True
                except queue.Empty:
                    pass
        with self.cond:
            self._latest = frame
            self.cond.notify_all()
        self.status = "live"
        self.frame_count += 1
        self._count += 1
        now = time.perf_counter()
        if now - self._t0 >= 1.0:
            self.fps = self._count / (now - self._t0)
            self._count, self._t0 = 0, now

    def _run(self) -> None:
        warned = False
        while not self._stop.is_set():
            try:
                if self.source == "ws":
                    self._session_ws()
                else:
                    self._session_rtsp()
            except Exception as exc:  # never let the thread die
                self.error = f"{type(exc).__name__}: {exc}"
                if not warned or self.status == "live":
                    log.warning("camera: %s - reconnecting", self.error)
                warned = True
            self.status = "reconnecting" if self.status == "live" else "offline"
            self.clock.reset()
            self._stop.wait(1.0)

    def _session_ws(self) -> None:
        ws = WebSocket(self.host)
        try:
            ws.send_binary(start_request(self.stream))
            decoder, fmt = None, None
            while not self._stop.is_set():
                msg = ws.recv_message()
                arrival = time.perf_counter()
                if len(msg) < HEADER.size + FRAME.size or not msg.startswith(MAGIC):
                    continue
                if HEADER.unpack_from(msg)[2] != CMD_MEDIA:
                    continue
                tag, ftype, _, stamp, codec_id, width, height, _, length = FRAME.unpack_from(msg, HEADER.size)
                codec = CODECS.get(codec_id)
                if tag != b"ht" or codec is None:
                    continue
                if decoder is None or fmt != (codec, width, height):
                    if ftype != 1:  # (re)start decoding at a keyframe
                        continue
                    decoder = av.CodecContext.create(codec, "r")
                    decoder.options = {"flags": "low_delay", "threads": "1"}
                    fmt = (codec, width, height)
                    log.info("camera: %dx%d %s from ws://%s (%s stream)", width, height, codec.upper(), self.host,
                             self.stream)
                start = HEADER.size + FRAME.size
                try:
                    pictures = decoder.decode(av.Packet(msg[start:start + length]))
                except av.FFmpegError as exc:
                    log.warning("camera: decode error (%s) - waiting for the next keyframe", exc)
                    decoder = None
                    self.gap = True
                    continue
                t = self.clock.capture_time(stamp / 1000.0, arrival)
                for picture in pictures:
                    self._publish(picture.to_ndarray(format="bgr24"), t)
        finally:
            ws.close()

    def _session_rtsp(self) -> None:
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay")
        import cv2
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                                                          cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
                                                          cv2.CAP_PROP_N_THREADS, 1])
        try:
            if not cap.isOpened():
                raise ConnectionError(f"cannot open {self.url}")
            log.info("camera: connected %s", self.url)
            while not self._stop.is_set():
                ok, image = cap.read()
                if not ok or image is None:
                    raise ConnectionError("stream lost")
                self._publish(image, time.perf_counter() - self.delay_s)
        finally:
            cap.release()


def open_stream(cfg) -> CameraStream:
    """From a settings section with source, host, stream, url, delay_ms, process_width."""
    get = cfg.get if hasattr(cfg, "get") else (lambda k, d=None: getattr(cfg, k, d))
    return CameraStream(str(get("source", "ws")), str(get("host", "192.168.70.66")), str(get("stream", "sub")),
                        str(get("url", "")), float(get("delay_ms", 60)) / 1000.0,
                        width=int(get("process_width", 0) or 0))
