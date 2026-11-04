"""
Lidar test (STL27L): a small, separate test program.
It does not use or change the main program (use_lib.py, lib.py, settings).

    1. Keep the laser plane empty: the first 10 s learn the background.
    2. After that, every point closer than the background is a sighting,
       even a single point. No noise filter: every point is drawn BIG.
    3. A thrown ball can be seen twice: on its way in and on its way back.
       After the first sighting the program waits TOUCH_WINDOW seconds for
       the way back, then shows TOUCHED once, at the middle of all the
       sightings (in only, out only, or in + out).

Run:   python lidar_test.py              (port COM14)
       python lidar_test.py --port COM5

Keys:  B = learn the background again    C = clear the list
       Up / Down = background threshold (lowest = OFF, raw)
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
MIN_RANGE = 20          # mm, 0 = no echo
MAX_RANGE = 25000       # mm, the sensor's full range

# A point is new when it is closer than the background by
# MARGIN_MM + MARGIN_PCT % of the background distance (40 mm at 3 m).
# Measured with the area empty: 0 false points in 20 s. Without it the wall
# itself flickers. Up / Down scale it live, the lowest level turns it off.
MARGIN_MM = 10
MARGIN_PCT = 1.0
LEVELS = [0, 0.25, 0.5, 1, 1.5, 2, 3]

BIN_DEG = 0.25          # background angle resolution
GROUP_GAP = 100         # mm, new points closer than this are one object

TOUCH_WINDOW = 0.25     # s after the first sighting: wait for the way back, then decide
MATCH_RADIUS = 400      # mm, sightings this close (in the window) are the same ball
FOLLOW_GAP = 0.3        # s, a touched ball still seen near its touch (falling, rolling)
                        # makes no new touch until it is gone this long
HOLD_TIME = 1.0         # s, a touch stays on screen this long

WIDTH, HEIGHT = 1280, 800
PANEL_WIDTH = 520       # list of touches on the right, never over the map

PACKET_SIZE = 47
HEADER = b"\x54\x2C"
N_BINS = int(round(360 / BIN_DEG))

GREY = (90, 90, 100)
RING = (45, 45, 55)
CYAN = (0, 220, 255)
RED = (255, 40, 40)
ORANGE = (255, 150, 0)
YELLOW = (255, 230, 0)
GREEN = (60, 220, 90)
WHITE = (240, 240, 240)

# one colour per ball: #1, #2, #3 ... (in and out of one ball share it)
BALL_COLORS = [
    (255, 40, 40),
    (40, 140, 255),
    (40, 230, 90),
    (255, 60, 255),
    (255, 150, 0),
    (0, 230, 230),
]


def ball_color(num):

    return BALL_COLORS[(num - 1) % len(BALL_COLORS)]


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

# One scan's new points, and the ball number of each point (for colours).
Mark = namedtuple("Mark", "t x y ball")

# A decided touch: where (middle of all sightings), and every sighting [t, cx, cy, n].
Touch = namedtuple("Touch", "t num x y seen")


class Ball:
    """One ball while we wait for its way back: one sighting per scan."""

    def __init__(self, num, t):

        self.num = num
        self.t0 = t
        self.seen = []          # [t, cx, cy, n points] per scan


    def add(self, t, cx, cy, n):

        if self.seen and self.seen[-1][0] == t:
            # a second group of the same ball in the same scan: merge
            last = self.seen[-1]
            m = last[3] + n
            last[1] = (last[1] * last[3] + cx * n) / m
            last[2] = (last[2] * last[3] + cy * n) / m
            last[3] = m
        else:
            self.seen.append([t, cx, cy, n])


class LidarTest:

    def __init__(self):

        self.zoom = 0.1             # pixels per mm
        self.pan = [0.0, 0.0]       # pixels
        self.fit = 5000             # mm to fit in the window at the next draw

        self.level = LEVELS.index(1)

        self.marks = deque()        # sightings, drawn as dots
        self.balls = []             # seen, waiting for the way back
        self.followed = []          # touched and still seen: [num, x, y, last time]
        self.touches = deque()      # decided, on screen for HOLD_TIME
        self.log = deque(maxlen=16) # (text, colour)
        self.touch_count = 0
        self.ball_num = 0

        self.hz = 0.0
        self.beams = 0
        self.live = None            # newest scan: x, y

        self.fonts = {}

        self.learn_again()


    def learn_again(self):

        self.near = np.full(N_BINS, np.inf)     # closest distance seen per angle
        self.bg = None
        self.limit = None                       # None = still learning
        self.learn_start = None
        self.learn_scans = 0
        self.marks.clear()
        self.balls = []
        self.followed = []
        self.touches.clear()


    def clear(self):

        self.marks.clear()
        self.balls = []
        self.followed = []
        self.touches.clear()
        self.log.clear()
        self.touch_count = 0


    def set_level(self, step):

        self.level = min(max(self.level + step, 0), len(LEVELS) - 1)

        if self.bg is not None:
            self.update_limit()


    def update_limit(self):

        # angles where the background had no echo: anything there is new
        margin = np.where(np.isfinite(self.bg), MARGIN_MM + self.bg * MARGIN_PCT / 100, 0)
        self.limit = self.bg - LEVELS[self.level] * margin


    def threshold_text(self):

        k = LEVELS[self.level]

        if k == 0:
            return "Threshold OFF (raw: any point closer than the background)"

        at1 = k * (MARGIN_MM + 1000 * MARGIN_PCT / 100)
        at3 = k * (MARGIN_MM + 3000 * MARGIN_PCT / 100)

        return f"Threshold x{k:g}: {at1:.0f} mm at 1 m, {at3:.0f} mm at 3 m"


    def add_scan(self, scan, now):

        self.hz = scan.hz
        self.beams = scan.beams

        r = np.radians(scan.angle)
        x = np.cos(r) * scan.dist
        y = np.sin(r) * scan.dist
        bins = (scan.angle / BIN_DEG).astype(int) % N_BINS

        self.live = (x, y)

        # ---- learning the background ----
        if self.limit is None:

            if self.learn_start is None:
                self.learn_start = now

            np.minimum.at(self.near, bins, scan.dist)
            self.learn_scans += 1

            if now - self.learn_start >= BACKGROUND_TIME and self.learn_scans >= 20:
                # each angle also takes its neighbours (beam angles jitter a little)
                self.bg = np.minimum(self.near, np.minimum(np.roll(self.near, 1), np.roll(self.near, -1)))
                self.update_limit()
                seen = self.bg[np.isfinite(self.bg)]
                self.fit = np.percentile(seen, 75) if len(seen) else 5000

            return

        # ---- detecting: every point closer than the background ----
        self.update(now)

        new = scan.dist < self.limit[bins]

        if not new.any():
            return

        nx = x[new]
        ny = y[new]

        # points in scan order; a jump bigger than GROUP_GAP starts a new object
        cuts = np.flatnonzero(np.hypot(np.diff(nx), np.diff(ny)) > GROUP_GAP) + 1
        ball_of_point = np.zeros(len(nx), int)

        for idx in np.split(np.arange(len(nx)), cuts):

            cx = nx[idx].mean()
            cy = ny[idx].mean()

            # the same ball as a sighting just before (way in -> way back)?
            ball = self.match(cx, cy)

            if ball is None:
                # a ball that already made its touch, still in view
                old = self.match_followed(cx, cy)
                if old is not None:
                    old[1:] = [cx, cy, now]
                    ball_of_point[idx] = old[0]
                    continue

                self.ball_num += 1
                ball = Ball(self.ball_num, now)
                self.balls.append(ball)

            ball.add(now, cx, cy, len(idx))
            ball_of_point[idx] = ball.num

        self.marks.append(Mark(now, nx, ny, ball_of_point))


    def match(self, cx, cy):
        """Nearest waiting ball within MATCH_RADIUS of its last sighting, or None."""

        best = None
        best_d = MATCH_RADIUS

        for ball in self.balls:
            d = math.hypot(cx - ball.seen[-1][1], cy - ball.seen[-1][2])
            if d < best_d:
                best = ball
                best_d = d

        return best


    def match_followed(self, cx, cy):

        best = None
        best_d = MATCH_RADIUS

        for old in self.followed:
            d = math.hypot(cx - old[1], cy - old[2])
            if d < best_d:
                best = old
                best_d = d

        return best


    def update(self, now):
        """Decide the balls whose window is over, forget old dots and touches."""

        for ball in [b for b in self.balls if now - b.t0 >= TOUCH_WINDOW]:

            self.balls.remove(ball)

            # middle of all sightings: with in + out, the wall is hit between them
            x = float(np.mean([s[1] for s in ball.seen]))
            y = float(np.mean([s[2] for s in ball.seen]))

            self.touches.append(Touch(now, ball.num, x, y, ball.seen))
            self.followed.append([ball.num, ball.seen[-1][1], ball.seen[-1][2], ball.seen[-1][0]])
            self.touch_count += 1

            stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
            first, last = ball.seen[0], ball.seen[-1]

            if len(ball.seen) == 1:
                detail = f"seen once ({first[3]} pt)"
            elif len(ball.seen) == 2:
                detail = f"in {first[1]:.0f},{first[2]:.0f}  out {last[1]:.0f},{last[2]:.0f}"
            else:
                detail = f"seen {len(ball.seen)}x  {first[1]:.0f},{first[2]:.0f} .. {last[1]:.0f},{last[2]:.0f}"

            line = f"{stamp} #{ball.num} TOUCHED x{x:6.0f} y{y:6.0f}"
            self.log.appendleft(("    " + detail, WHITE))
            self.log.appendleft((line, ball_color(ball.num)))
            print(line, "|", detail)

        self.followed = [old for old in self.followed if now - old[3] < FOLLOW_GAP]

        while self.marks and now - self.marks[0].t > TOUCH_WINDOW + HOLD_TIME:
            self.marks.popleft()

        while self.touches and now - self.touches[0].t > HOLD_TIME:
            self.touches.popleft()


    # -------------------------
    # VIEW
    # -------------------------

    def map_size(self, size):

        return max(size[0] - PANEL_WIDTH, 200), size[1]


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


    def text(self, screen, s, pos, size, color, center=False, mono=False, box=None):

        img = self.font(size, mono).render(s, True, color, box)
        rect = img.get_rect(center=pos) if center else img.get_rect(topleft=pos)
        screen.blit(img, rect)


    def point(self, x, y, size):

        sx, sy = self.to_screen(x, y, size)

        return int(sx), int(sy)


    def draw(self, screen, now, crc_errors=0, error=None):

        full_w = screen.get_width()
        size = w, h = self.map_size(screen.get_size())

        if self.fit:
            self.zoom = 0.45 * min(w, h) / max(self.fit, 500)
            self.pan = [0.0, 0.0]
            self.fit = None

        touched = bool(self.touches)
        waiting = bool(self.balls)

        screen.fill((45, 8, 8) if touched else (35, 22, 0) if waiting else (14, 14, 20))

        # distance rings, 1 m apart
        ox, oy = self.point(0, 0, size)

        for m in range(1, int(MAX_RANGE / 1000) + 1):
            r = int(m * 1000 * self.zoom)
            if 4 < r < w + h:
                pygame.draw.circle(screen, RING, (ox, oy), r, 1)
                self.text(screen, f"{m} m", (ox + r + 4, oy - 22), 16, RING)

        pygame.draw.line(screen, RING, (0, oy), (w, oy))
        pygame.draw.line(screen, RING, (ox, 0), (ox, h))

        # newest scan, small grey points
        if self.live is not None:
            sx, sy = self.to_screen(self.live[0], self.live[1], size)
            for px, py in zip(sx.astype(int).tolist(), sy.astype(int).tolist()):
                screen.fill(GREY, (px - 1, py - 1, 3, 3))

        pygame.draw.circle(screen, CYAN, (ox, oy), 9)

        # balls seen, still waiting for the way back: thin ring
        for ball in self.balls:
            color = ball_color(ball.num)
            bx, by = self.point(ball.seen[-1][1], ball.seen[-1][2], size)
            pygame.draw.circle(screen, color, (bx, by), 45, 2)
            self.text(screen, f"#{ball.num} seen {len(ball.seen)}x, waiting...", (bx + 50, by - 12), 22, color, box=(0, 0, 0))

        # touches: each sighting a small ring (in -> out joined by a line),
        # the touch point (middle) a cross with a thick ring and a label
        for touch in self.touches:

            color = ball_color(touch.num)
            spots = [self.point(s[1], s[2], size) for s in touch.seen]

            if len(spots) > 1:
                pygame.draw.lines(screen, color, False, spots, 3)
            for spot in spots:
                pygame.draw.circle(screen, color, spot, 16, 3)

            tx, ty = self.point(touch.x, touch.y, size)
            pygame.draw.line(screen, color, (ox, oy), (tx, ty), 2)
            pygame.draw.circle(screen, color, (tx, ty), 45, 6)
            pygame.draw.line(screen, WHITE, (tx - 30, ty), (tx + 30, ty), 3)
            pygame.draw.line(screen, WHITE, (tx, ty - 30), (tx, ty + 30), 3)

            how = "in + out" if len(touch.seen) == 2 else "seen once" if len(touch.seen) == 1 else f"seen {len(touch.seen)}x"
            self.text(screen, f"#{touch.num} TOUCHED  {how}", (tx + 52, ty - 16), 26, color, box=(0, 0, 0))

        # every new point: a big dot in its ball's colour (drawn last, nothing covers it)
        for mark in self.marks:
            sx, sy = self.to_screen(mark.x, mark.y, size)
            for px, py, num in zip(sx.astype(int).tolist(), sy.astype(int).tolist(), mark.ball.tolist()):
                pygame.draw.circle(screen, ball_color(num), (px, py), 11)
                pygame.draw.circle(screen, YELLOW, (px, py), 5)

        # big status, in the bottom half: the lidar is near the floor,
        # touches show up in the top half
        if self.limit is None:

            left = BACKGROUND_TIME if self.learn_start is None else max(BACKGROUND_TIME - (now - self.learn_start), 0)
            done = 1 - left / BACKGROUND_TIME

            self.text(screen, "KEEP THE AREA EMPTY", (w / 2, h / 2 - 90), 64, YELLOW, center=True)
            msg = "waiting for lidar data..." if self.learn_start is None else f"learning background  {left:4.1f} s"
            self.text(screen, msg, (w / 2, h / 2 - 30), 32, WHITE, center=True)

            bar = pygame.Rect(w / 2 - 250, h / 2 + 10, 500, 24)
            pygame.draw.rect(screen, GREY, bar, 2)
            pygame.draw.rect(screen, YELLOW, (bar.x + 3, bar.y + 3, (bar.w - 6) * done, bar.h - 6))

        elif touched:

            newest = self.touches[-1]
            count = len(self.touches)
            how = "in + out" if len(newest.seen) == 2 else "seen once" if len(newest.seen) == 1 else f"seen {len(newest.seen)}x"

            self.text(screen, "TOUCHED" if count == 1 else f"TOUCHED  x{count}", (w / 2, h - 230), 96, YELLOW, center=True)
            self.text(screen, f"#{newest.num}   x {newest.x:.0f}   y {newest.y:.0f}   ({how})", (w / 2, h - 160), 36, YELLOW, center=True)

        elif waiting:
            self.text(screen, "BALL SEEN  -  waiting for the way back", (w / 2, h - 200), 40, ORANGE, center=True)

        else:
            self.text(screen, "READY  -  not touched", (w / 2, h - 200), 40, GREEN, center=True)

        # list of touches, in its own panel on the right
        pygame.draw.rect(screen, (8, 8, 12), (w, 0, full_w - w, h))
        pygame.draw.line(screen, GREY, (w, 0), (w, h), 2)
        self.text(screen, "Touches (newest first)", (w + 16, 16), 26, WHITE)

        for i, (line, color) in enumerate(self.log):
            self.text(screen, line, (w + 16, 60 + i * 28), 20, color, mono=True)

        if touched:
            pygame.draw.rect(screen, RED, (0, 0, full_w, h), 14)

        # info and keys
        self.text(screen, f"Lidar {self.hz:.1f} Hz   {self.beams} points   CRC errors {crc_errors}   touches {self.touch_count}",
                  (16, h - 92), 20, WHITE)
        self.text(screen, self.threshold_text() + "   (Up / Down)", (16, h - 64), 20, YELLOW if self.level == 0 else WHITE)
        self.text(screen, "B = learn background again   C = clear   wheel = zoom   drag = move   Esc = quit",
                  (16, h - 36), 18, GREY)

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
                elif event.key in (pygame.K_UP, pygame.K_DOWN):
                    test.set_level(1 if event.key == pygame.K_UP else -1)
                    print(test.threshold_text())

            elif event.type == pygame.MOUSEWHEEL:
                test.zoom_at(1.2 ** event.y, pygame.mouse.get_pos(), test.map_size(screen.get_size()))

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

        now = time.monotonic()

        if test.limit is not None:
            test.update(now)

        test.draw(screen, now, lidar.crc_errors, lidar.error)
        pygame.display.flip()
        clock.tick(60)

    lidar.close()
    pygame.quit()


if __name__ == "__main__":
    main()
