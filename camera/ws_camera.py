"""
Low-latency reader for the camera's own web-player stream (the "Camera Web Service" page).

The web page does not use RTSP: it opens a WebSocket to ws://<camera>:80, sends a binary
"start preview" request and gets every H.264 / H.265 frame in its own WebSocket message.
This module speaks the same protocol (captured from the page, no login needed), pipes the
frames into ffmpeg and hands decoded BGR frames to OpenCV.

Needs ffmpeg on PATH (no extra Python packages).

Measured on this PC (Ryzen 7 5825U): the web stream is the same video as RTSP, so it is not faster.
    sub  (H.265 640x360)  : works, ~0.15 s decode delay through the ffmpeg pipe
    main (H.264 1920x1080): too heavy - 6 MB BGR frames through a pipe fall behind (> 1 s and growing)
camera_viewer.py (RTSP decoded inside OpenCV, 1 thread) is as fast or faster; use this module only
when you specifically need the web-player stream.

Packet layout (little-endian), as seen on the wire:
    24-byte header : 77 88 00 00 | payload length (u32) | command (u32) | 12 bytes unused
        command 0x66 = start preview (request and its ack), 0xC8 = media frame
    media payload  : 28-byte frame header + raw Annex-B video
        "ht" | frame type (1 = keyframe, 3 = P-frame) | pad | frame no (u32) | timestamp ms (u32)
        | 4 bytes ? | codec (1 = H.264, 6 = H.265) | ? | width (u16) | height (u16) | fps (u8) | pad
        | data length (u32)

Run directly for a plain live view:  python ws_camera.py [main|sub]
"""
import base64
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

import numpy as np

MAGIC = b"\x77\x88\x00\x00"
CMD_START = 0x66
CMD_MEDIA = 0xC8
HEADER = struct.Struct("<4sII12x")              # magic, payload length, command
FRAME = struct.Struct("<2sBxII4xBxHHBxI")       # "ht", type, frame no, timestamp, codec, w, h, fps, data length
CODECS = {1: "h264", 6: "hevc"}
STREAM_IDS = {"main": 1, "sub": 2}

# Access Unit Delimiter NAL: written after each frame so ffmpeg knows the frame is complete
# and decodes it immediately instead of waiting for the start of the next frame (= 40 ms).
AUD = {"h264": b"\x00\x00\x00\x01\x09\xf0", "hevc": b"\x00\x00\x00\x01\x46\x01\x50"}
DECODER_THREADS = 1


def start_request(stream):
    """The 'start preview' request exactly as the camera's web player sends it."""
    req = bytearray(HEADER.size + 44)
    HEADER.pack_into(req, 0, MAGIC, 44, CMD_START)
    req[12:16] = bytes.fromhex("0f8f0080")  # copied from the web player, meaning unknown
    req[24] = 0                             # channel
    req[25] = STREAM_IDS[stream]
    req[36:38] = b"ht"
    return bytes(req)


class WebSocket:
    """Just enough of a WebSocket client for the camera (binary messages, ping/pong)."""

    def __init__(self, host, port=80, timeout=5):
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

    def _exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(262144)
            if not chunk:
                raise ConnectionError("camera closed the connection")
            self.buf += chunk
        data = bytes(self.buf[:n])
        del self.buf[:n]
        return data

    def _send_frame(self, opcode, payload):
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

    def send_binary(self, payload):
        self._send_frame(0x2, payload)

    def recv_message(self):
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

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()


class FfmpegDecoder:
    """Pipes raw H.264 / H.265 frames through ffmpeg and calls on_frame(bgr_image) per decoded picture."""

    def __init__(self, codec, width, height, on_frame):
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg not found on PATH")
        self.aud = AUD[codec]
        self.shape = (height, width, 3)
        self.on_frame = on_frame
        self.proc = subprocess.Popen(
            # no "-fflags nobuffer": with raw H.264 input it drops the probed SPS and nothing decodes
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-flags", "low_delay", "-probesize", "32", "-analyzeduration", "0",
             "-threads", str(DECODER_THREADS), "-f", codec, "-i", "pipe:0",
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-flush_packets", "1", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def feed(self, data):
        self.proc.stdin.write(data + self.aud)

    def _read(self):
        size = self.shape[0] * self.shape[1] * 3
        while True:
            buf = bytearray(size)
            view, got = memoryview(buf), 0
            while got < size:
                n = self.proc.stdout.readinto(view[got:])
                if not n:
                    return
                got += n
            self.on_frame(np.frombuffer(buf, np.uint8).reshape(self.shape))

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.kill()
        self.proc.wait(timeout=3)


class WsCameraStream:
    """Same interface as camera_viewer.CameraStream (read / fps / status / stop),
    but fed from the web-player WebSocket stream. Reconnects on its own."""

    def __init__(self, host, stream="main"):
        self.host = host
        self.stream = stream
        self.lock = threading.Lock()
        self.frame = None
        self.frame_id = 0
        self.fps = 0.0
        self.status = "connecting"
        self.running = True
        self._count, self._t0 = 0, time.time()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while self.running:
            try:
                self._session()
            except (OSError, ConnectionError, RuntimeError) as e:
                self.status = "reconnecting"
                print(f"[web stream] {e} - reconnecting")
            time.sleep(1)

    def _session(self):
        ws = WebSocket(self.host)
        decoder = None
        try:
            ws.send_binary(start_request(self.stream))
            while self.running:
                msg = ws.recv_message()
                if len(msg) < HEADER.size + FRAME.size or not msg.startswith(MAGIC):
                    continue
                _, _, command = HEADER.unpack_from(msg)
                if command != CMD_MEDIA:
                    continue
                tag, ftype, _, _, codec_id, width, height, _, length = FRAME.unpack_from(msg, HEADER.size)
                codec = CODECS.get(codec_id)
                if tag != b"ht" or codec is None:
                    continue
                if decoder is None:
                    if ftype != 1:  # start decoding at a keyframe
                        continue
                    decoder = FfmpegDecoder(codec, width, height, self._on_frame)
                    self.status = "live"
                    print(f"[web stream] connected {width}x{height} {codec.upper()}  ws://{self.host} ({self.stream})")
                start = HEADER.size + FRAME.size
                decoder.feed(msg[start:start + length])
        finally:
            ws.close()
            if decoder is not None:
                decoder.close()

    def _on_frame(self, frame):
        with self.lock:
            self.frame = frame
            self.frame_id += 1
        self._count += 1
        now = time.time()
        if now - self._t0 >= 1.0:
            self.fps = self._count / (now - self._t0)
            self._count, self._t0 = 0, now

    def read(self):
        with self.lock:
            return self.frame_id, self.frame

    def stop(self):
        self.running = False
        self.thread.join(timeout=3)


if __name__ == "__main__":
    import cv2

    cam = WsCameraStream("192.168.70.66", sys.argv[1] if len(sys.argv) > 1 else "main")
    cv2.namedWindow("Web stream", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Web stream", 1280, 720)
    last_id = 0
    while cv2.getWindowProperty("Web stream", cv2.WND_PROP_VISIBLE) >= 1:
        frame_id, frame = cam.read()
        if frame is not None and frame_id != last_id:
            last_id = frame_id
            cv2.imshow("Web stream", frame)
        if cv2.waitKey(5) & 0xFF in (ord("q"), 27):
            break
    cam.stop()
    cv2.destroyAllWindows()
