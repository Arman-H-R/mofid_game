"""
Lidar test (STL27L): a small, separate test program.
It does not use or change the main program (use_lib.py, lib.py, settings).

    1. Keep the laser plane empty: the first 10 s learn the background.
    2. After that, every point closer than the background is shown BIG on the
       window, even a single point. Each detection stays on screen for
       HOLD_TIME seconds, so an object seen in only one scan (a fast ball)
       can still be seen.

Run:   python lidar_test.py              (port COM14)
       python lidar_test.py --port COM5

Keys:  B = learn the background again    C = clear the list
       mouse wheel = zoom    drag = move    Esc = quit

Only one program can open the port: close use_lib.py first.
"""

import argparse
import math
import threading
import time
from collections import deque, namedtuple
from queue import Empty, Queue

import numpy as np
import pygame
import serial


PORT = "COM14"
BAUD = 921600

BACKGROUND_TIME = 10.0  # s, the plane must be empty while it learns
MARGIN = 40             # mm, a point must be this much closer than the background
MIN_RANGE = 20          # mm, closer = no echo
MAX_RANGE = 5000        # mm, farther points are ignored
BIN_DEG = 0.25          # background angle resolution
GROUP_GAP = 100         # mm, new points closer than this are one object
HOLD_TIME = 1.0         # s, a detection stays on screen this long

WIDTH, HEIGHT = 1280, 800

PACKET_SIZE = 47
HEADER = b"\x54\x2C"
N_BINS = int(round(360 / BIN_DEG))

GREY = (90, 90, 100)
RING = (45, 45, 55)
CYAN = (0, 220, 255)
RED = (255, 40, 40)
YELLOW = (255, 230, 0)
GREEN = (60, 220, 90)
WHITE = (240, 240, 240)


# =========================
# LIDAR
# =========================

def make_crc_table():
    """crc8, polynomial 0x4D (same as lib.py)."""

    table = []

    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ 0x4D) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        table.append(c)

    return table


CRC_TABLE = make_crc_table()


def crc8(data):

    crc = 0
    for b in data:
        crc = CRC_TABLE[crc ^ b]

    return crc


# One rotation: angle (deg) and dist (mm) of the valid points,
# hz = rotation speed, beams = all points in the rotation.
Scan = namedtuple("Scan", "angle dist hz beams")


def make_scan(packets):

    raw = np.frombuffer(b"".join(packets), dtype=np.uint8).reshape(-1, PACKET_SIZE).astype(np.int32)

    speed = (raw[:, 2] | raw[:, 3] << 8).mean()     # deg/s
    start = (raw[:, 4] | raw[:, 5] << 8) / 100
    end = (raw[:, 42] | raw[:, 43] << 8) / 100
    end = np.where(end < start, end + 360, end)

    angle = (start[:, None] + (end - start)[:, None] / 11 * np.arange(12)) % 360

    pts = raw[:, 6:42].reshape(-1, 12, 3)
    dist = (pts[:, :, 0] | pts[:, :, 1] << 8).astype(np.float64)

    angle = angle.ravel()
    dist = dist.ravel()
    ok = (dist >= MIN_RANGE) & (dist <= MAX_RANGE)

    return Scan(angle[ok], dist[ok], speed / 360, len(dist))


class Lidar:
    """Reads the port in a thread; every full rotation goes into self.scans."""

    def __init__(self, port):

        self.ser = serial.Serial(port, BAUD, timeout=0.05)

        try:
            self.ser.set_buffer_size(rx_size=1 << 16)   # Windows: bigger driver buffer
        except (AttributeError, serial.SerialException):
            pass

        self.ser.reset_input_buffer()

        self.scans = Queue()
        self.crc_errors = 0
        self.error = None
        self.running = True

        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()


    def _read(self):

        buf = bytearray()
        packets = []
        last = None

        while self.running:

            try:
                buf.extend(self.ser.read(self.ser.in_waiting or 1))
            except (serial.SerialException, OSError):
                self.error = "Lidar disconnected"
                return

            pos = 0

            while True:

                pos = buf.find(HEADER, pos)

                if pos == -1:
                    # keep the last byte, it may be the first half of a header
                    pos = max(len(buf) - 1, 0)
                    break

                if len(buf) - pos < PACKET_SIZE:
                    break

                packet = bytes(buf[pos:pos + PACKET_SIZE])

                if crc8(packet[:46]) != packet[46]:
                    self.crc_errors += 1
                    pos += 1
                    continue

                pos += PACKET_SIZE

                start = (packet[4] | packet[5] << 8) / 100

                # the start angle jumps back (359 -> 0): one rotation is done
                if last is not None and start < last - 180:
                    if len(packets) >= 10:
                        self.scans.put(make_scan(packets))
                    packets = []

                last = start
                packets.append(packet)

            del buf[:pos]


    def close(self):

        self.running = False
        self.thread.join(timeout=1)
        self.ser.close()


# =========================
# TEST
# =========================

# One scan that saw something: its new points and the objects (cx, cy, n points).
Mark = namedtuple("Mark", "t x y groups")


class LidarTest:

    def __init__(self):

        self.zoom = 0.1             # pixels per mm
        self.pan = [0.0, 0.0]       # pixels
        self.fit = MAX_RANGE        # mm to fit in the window at the next draw

        self.marks = deque()
        self.log = deque(maxlen=14)
        self.detections = 0

        self.hz = 0.0
        self.beams = 0
        self.live = None            # newest scan: x, y, new

        self.fonts = {}

        self.learn_again()


    def learn_again(self):

        self.near = np.full(N_BINS, np.inf)     # closest distance seen per angle
        self.limit = None                       # None = still learning
        self.learn_start = None
        self.learn_scans = 0
        self.marks.clear()


    def clear(self):

        self.marks.clear()
        self.log.clear()
        self.detections = 0


    def add_scan(self, scan, now):

        self.hz = scan.hz
        self.beams = scan.beams

        r = np.radians(scan.angle)
        x = np.cos(r) * scan.dist
        y = np.sin(r) * scan.dist
        bins = (scan.angle / BIN_DEG).astype(int) % N_BINS

        # ---- learning the background ----
        if self.limit is None:

            if self.learn_start is None:
                self.learn_start = now

            np.minimum.at(self.near, bins, scan.dist)
            self.learn_scans += 1

            if now - self.learn_start >= BACKGROUND_TIME and self.learn_scans >= 20:
                # each angle also takes its neighbours (beam angles jitter a little)
                near = np.minimum(self.near, np.minimum(np.roll(self.near, 1), np.roll(self.near, -1)))
                self.limit = near - MARGIN
                seen = near[np.isfinite(near)]
                self.fit = np.percentile(seen, 95) if len(seen) else MAX_RANGE

            self.live = (x, y, np.zeros(len(x), bool))
            return

        # ---- detecting: anything closer than the background ----
        new = scan.dist < self.limit[bins]
        self.live = (x, y, new)

        if not new.any():
            return

        nx = x[new]
        ny = y[new]

        # points in scan order; a jump bigger than GROUP_GAP starts a new object
        cuts = np.flatnonzero(np.hypot(np.diff(nx), np.diff(ny)) > GROUP_GAP) + 1
        groups = [(nx[i].mean(), ny[i].mean(), len(i)) for i in np.split(np.arange(len(nx)), cuts)]

        self.marks.append(Mark(now, nx, ny, groups))
        self.detections += 1

        stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"

        for cx, cy, n in groups:
            line = f"{stamp} {n:4d} pt {math.hypot(cx, cy):5.0f} mm {math.degrees(math.atan2(cy, cx)) % 360:5.1f} deg"
            self.log.appendleft(line)
            print(line)


    # -------------------------
    # VIEW
    # -------------------------

    def to_screen(self, x, y, size):

        w, h = size

        return w / 2 + self.pan[0] + x * self.zoom, h / 2 + self.pan[1] - y * self.zoom


    def zoom_at(self, factor, pos, size):
        """Zoom and keep the point under the mouse in place."""

        w, h = size
        mx = (pos[0] - w / 2 - self.pan[0]) / self.zoom
        my = (h / 2 + self.pan[1] - pos[1]) / self.zoom

        self.zoom = min(max(self.zoom * factor, 0.01), 5.0)
        self.pan[0] = pos[0] - w / 2 - mx * self.zoom
        self.pan[1] = pos[1] - h / 2 + my * self.zoom


    def font(self, size, mono=False):

        key = (size, mono)

        if key not in self.fonts:
            self.fonts[key] = pygame.font.SysFont("Consolas" if mono else "Arial", size, bold=True)

        return self.fonts[key]


    def text(self, screen, s, pos, size, color, center=False, mono=False):

        img = self.font(size, mono).render(s, True, color)
        rect = img.get_rect(center=pos) if center else img.get_rect(topleft=pos)
        screen.blit(img, rect)


    def draw(self, screen, now, crc_errors=0, error=None):

        size = w, h = screen.get_size()

        if self.fit:
            self.zoom = 0.45 * min(w, h) / max(self.fit, 500)
            self.pan = [0.0, 0.0]
            self.fit = None

        while self.marks and now - self.marks[0].t > HOLD_TIME:
            self.marks.popleft()

        seeing = bool(self.marks)

        screen.fill((45, 8, 8) if seeing else (14, 14, 20))

        # distance rings, 1 m apart
        ox, oy = self.to_screen(0, 0, size)

        for m in range(1, int(MAX_RANGE / 1000) + 1):
            r = int(m * 1000 * self.zoom)
            if r > 4:
                pygame.draw.circle(screen, RING, (int(ox), int(oy)), r, 1)
                self.text(screen, f"{m} m", (ox + r + 4, oy - 22), 16, RING)

        pygame.draw.line(screen, RING, (0, oy), (w, oy))
        pygame.draw.line(screen, RING, (ox, 0), (ox, h))

        # newest scan, small grey points
        if self.live is not None:
            sx, sy = self.to_screen(self.live[0], self.live[1], size)
            for px, py in zip(sx.astype(int).tolist(), sy.astype(int).tolist()):
                screen.fill(GREY, (px - 1, py - 1, 3, 3))

        pygame.draw.circle(screen, CYAN, (int(ox), int(oy)), 9)

        # detections: every new point is a big dot, every object gets a ring and a label
        for mark in self.marks:

            sx, sy = self.to_screen(mark.x, mark.y, size)

            for px, py in zip(sx.astype(int).tolist(), sy.astype(int).tolist()):
                pygame.draw.circle(screen, RED, (px, py), 11)
                pygame.draw.circle(screen, YELLOW, (px, py), 5)

            for cx, cy, n in mark.groups:
                gx, gy = self.to_screen(cx, cy, size)
                gx, gy = int(gx), int(gy)
                pygame.draw.line(screen, YELLOW, (ox, oy), (gx, gy), 2)
                pygame.draw.circle(screen, WHITE, (gx, gy), 45, 4)
                label = f"{n} pt  {math.hypot(cx, cy):.0f} mm"
                self.text(screen, label, (gx + 52, gy - 16), 30, WHITE)

        # big status line
        if self.limit is None:

            left = BACKGROUND_TIME if self.learn_start is None else max(BACKGROUND_TIME - (now - self.learn_start), 0)
            done = 1 - left / BACKGROUND_TIME

            self.text(screen, "KEEP THE AREA EMPTY", (w / 2, h / 2 - 90), 64, YELLOW, center=True)
            msg = "waiting for lidar data..." if self.learn_start is None else f"learning background  {left:4.1f} s"
            self.text(screen, msg, (w / 2, h / 2 - 30), 32, WHITE, center=True)

            bar = pygame.Rect(w / 2 - 250, h / 2 + 10, 500, 24)
            pygame.draw.rect(screen, GREY, bar, 2)
            pygame.draw.rect(screen, YELLOW, (bar.x + 3, bar.y + 3, (bar.w - 6) * done, bar.h - 6))

        elif seeing:

            newest = self.marks[-1]
            points = sum(n for _, _, n in newest.groups)
            objects = len(newest.groups)
            what = f"{objects} OBJECTS" if objects > 1 else "OBJECT"

            pygame.draw.rect(screen, RED, (0, 0, w, h), 14)
            self.text(screen, f"{what} DETECTED  -  {points} point{'s' if points > 1 else ''}", (w / 2, 60), 64, YELLOW, center=True)

        else:
            self.text(screen, "READY  -  nothing in the plane", (w / 2, 50), 40, GREEN, center=True)

        # info, list of detections, keys
        self.text(screen, f"Lidar {self.hz:.1f} Hz   {self.beams} points   CRC errors {crc_errors}   detections {self.detections}",
                  (16, h - 64), 20, WHITE)
        self.text(screen, "B = learn background again   C = clear   wheel = zoom   drag = move   Esc = quit",
                  (16, h - 36), 18, GREY)

        for i, line in enumerate(self.log):
            self.text(screen, line, (w - 560, 110 + i * 30), 22, YELLOW if i == 0 else WHITE, mono=True)

        if error:
            self.text(screen, error, (w / 2, h / 2), 48, RED, center=True)


# =========================
# MAIN
# =========================

def main():

    parser = argparse.ArgumentParser(description="Simple lidar test")
    parser.add_argument("--port", default=PORT)
    args = parser.parse_args()

    try:
        lidar = Lidar(args.port)
    except serial.SerialException as e:
        print(f"Cannot open {args.port}: {e}")
        print("Is use_lib.py (or another program) still using the port?")
        return

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("Lidar test")
    clock = pygame.time.Clock()

    test = LidarTest()
    drag = None
    running = True

    print("Keep the area empty: learning the background for", BACKGROUND_TIME, "s")

    while running:

        for event in pygame.event.get():

            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key == pygame.K_b:
                    test.learn_again()
                    print("Learning the background again")
                elif event.key == pygame.K_c:
                    test.clear()

            elif event.type == pygame.MOUSEWHEEL:
                test.zoom_at(1.2 ** event.y, pygame.mouse.get_pos(), screen.get_size())

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                drag = event.pos

            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                drag = None

            elif event.type == pygame.MOUSEMOTION and drag:
                test.pan[0] += event.pos[0] - drag[0]
                test.pan[1] += event.pos[1] - drag[1]
                drag = event.pos

        # every scan is checked, not only the newest
        while True:
            try:
                scan = lidar.scans.get_nowait()
            except Empty:
                break
            test.add_scan(scan, time.monotonic())

        test.draw(screen, time.monotonic(), lidar.crc_errors, lidar.error)
        pygame.display.flip()
        clock.tick(60)

    lidar.close()
    pygame.quit()


if __name__ == "__main__":
    main()
