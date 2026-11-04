"""
Lidar touch detection (STL27L) -> OSC for TouchDesigner.

How it works
  1. Background: the empty area is scanned for a few seconds. For every
     small angle slice we store the closest stable distance (polar
     background, not an x/y grid).
  2. New point: a point is "new" when it is clearly CLOSER than the
     background at its angle (something is blocking the beam) and it is
     inside the touch area (shape, angle range, min distance).
  3. Blobs: new points are grouped in scan order (neighbour beams that are
     close in space belong to the same hand / finger).
  4. Tracking: blobs are matched to touches between scans. A blob must be
     seen a few scans in a row before TOUCH DOWN (kills noise), a touch is
     released after a short time without a blob (TOUCH UP), positions are
     smoothed with a One Euro filter (still = no jitter, moving = no lag).
     Ball mode: the first scan that sees a new object is the hit (DOWN at
     that x, y, no MOVE, the position never changes), then new hits are
     ignored for "Delay after hit" seconds.

Settings   all tuning values live in settings.py and are changed live in
           the window's settings panel; they are saved to settings.json and
           also used by --headless. The panel starts closed: click the
           "Settings" tab on the right edge, swipe it left / right, or press H.

OSC   /touch [x, y, id, state]   state: 1 = down, 0 = move, -1 = up
      x, y in mm on the map, or 0..1 inside the area ("Send 0..1")

Keys  ESC quit | B scan background again | H open / close settings
      V fit the whole background again | UP / DOWN zoom
Mouse wheel zoom | right drag pan | left drag the area to move it
      the view fits the whole background until you zoom or pan

Run   python use_lib.py                 (window)
      python use_lib.py --headless      (console only)
"""

import argparse
import math
import time
from collections import deque

import numpy as np

from lib import D800
from settings import Settings


# =========================
# SETTINGS
# =========================

PORT = "COM14"

OSC_IP = "127.0.0.1"        # TouchDesigner PC IP if remote
OSC_PORT = 7000

WIDTH = 1300                # window, it can be resized
HEIGHT = 820
PANEL_WIDTH = 340
ZOOM = 0.20                 # screen pixels per mm


# fixed internals (everything you tune is in settings.py / the panel)
BG_BIN = 0.25               # deg per angle slice (STL27L beam step ~0.165 deg)
BG_PERCENTILE = 5           # closest stable distance of each slice
BG_MIN_HITS = 0.10          # slice needs a return in 10% of scans, else it is open space
BG_NEIGHBOURS = 2           # also use slices on each side (edges jitter in angle)
MIN_POINTS = 3              # points a blob needs close to the lidar (fewer far away)



# =========================
# BACKGROUND
# =========================

class Background:

    def __init__(self, cfg):

        self.cfg = cfg
        self.bins = int(round(360 / BG_BIN))
        self.distance = None        # per slice: background distance (inf = open space)
        self.threshold = None       # per slice: closer than this = new point
        self.split_angle = 0.0


    def _bin(self, angle):

        return (angle / BG_BIN).astype(np.int32) % self.bins


    def learn(self, scans):

        angle = np.concatenate([s.angle for s in scans])
        dist = np.concatenate([s.dist for s in scans])

        b = self._bin(angle)
        order = np.lexsort((dist, b))
        b = b[order]
        dist = dist[order]

        starts = np.searchsorted(b, np.arange(self.bins))
        ends = np.searchsorted(b, np.arange(self.bins), side="right")

        min_hits = max(3, BG_MIN_HITS * len(scans))

        background = np.full(self.bins, np.inf)

        for k in range(self.bins):

            d = dist[starts[k]:ends[k]]

            if len(d) >= min_hits:
                background[k] = np.percentile(d, BG_PERCENTILE)

        self.distance = background
        self.update_threshold()
        self.update_split()


    def update_threshold(self):
        """Gap settings changed: rebuild thresholds from the stored background (fast)."""

        if self.distance is None:
            return

        cfg = self.cfg

        # open space (no return) stays inf: everything there is new
        threshold = np.full(self.bins, np.inf)
        wall = np.isfinite(self.distance)
        d = self.distance[wall]
        threshold[wall] = d - (cfg.bg_margin + cfg.bg_margin_ratio / 100 * d)

        # beams jitter a bit in angle, so at an edge (near object next to far
        # wall) use the closest background of the neighbour slices too
        near = threshold.copy()

        for s in range(1, BG_NEIGHBOURS + 1):
            near = np.minimum(near, np.roll(threshold, s))
            near = np.minimum(near, np.roll(threshold, -s))

        self.threshold = near


    def update_split(self):
        """Area settings changed: move the scan seam to where nothing can be touched."""

        if self.threshold is not None:
            self.split_angle = self._find_split_angle()


    def _find_split_angle(self):
        """Middle of the biggest slice range where nothing can be touched."""

        cfg = self.cfg
        centers = (np.arange(self.bins) + 0.5) * BG_BIN

        # walk along every beam (until it hits the background) and check if
        # any part of it is inside the touch area
        far = min(cfg.reach(), 6000) + 20
        d = np.arange(max(cfg.min_distance, 20), far, 20, dtype=np.float64)[None, :]
        d = np.minimum(d, self.threshold[:, None])
        x, y = cfg.to_world(centers[:, None], d)
        dead = ~cfg.in_area(x, y, d).any(axis=1)

        # beams blocked by something right at the lidar (wall, mount) can't be touched
        dead |= self.threshold <= max(cfg.min_distance, 100)

        if not dead.any() or dead.all():
            return 0.0

        # longest run of dead slices, going around 360
        start = int(np.argmin(dead))            # a live slice
        rolled = np.roll(dead, -start)
        best_len, best_mid, run = 0, 0, 0

        for i, is_dead in enumerate(rolled):

            run = run + 1 if is_dead else 0

            if run > best_len:
                best_len = run
                best_mid = i - run // 2

        return float(centers[(best_mid + start) % self.bins])


    def new_points(self, scan, x, y):
        """(new, inside): masks of points in front of the background and inside the area."""

        inside = self.cfg.in_area(x, y, scan.dist)
        new = inside & (scan.dist < self.threshold[self._bin(scan.angle)])

        return new, inside



# =========================
# BLOB DETECTOR
# =========================

def make_blobs(scan, x, y, mask, cfg):
    """Group new points into blobs. x, y = map coordinates. Returns list of (x, y, points, width)."""

    idx = np.flatnonzero(mask)

    if len(idx) == 0:
        return []

    xs = x[idx]
    ys = y[idx]

    # points come in angle order: a new blob starts where two neighbour
    # new points are far apart
    gaps = np.hypot(np.diff(xs), np.diff(ys)) > cfg.cluster_distance
    groups = np.split(np.arange(len(idx)), np.flatnonzero(gaps) + 1)

    # first and last group touch each other when a blob is on the scan seam
    if len(groups) > 1 and math.hypot(xs[0] - xs[-1], ys[0] - ys[-1]) <= cfg.cluster_distance:
        groups[0] = np.concatenate([groups.pop(), groups[0]])

    step = math.radians(scan.step)
    blobs = []

    for g in groups:

        gx = xs[g]
        gy = ys[g]

        width = math.hypot(np.ptp(gx), np.ptp(gy))

        if width > cfg.max_width:
            continue

        dist = float(np.median(scan.dist[idx[g]]))

        # beams a min_width object covers at this distance (fingers far away
        # are hit by only 1-2 beams)
        beams = cfg.min_width / max(dist * step, 1e-6)
        need = int(min(MIN_POINTS, max(1, math.floor(beams))))

        if len(g) < need:
            continue

        # ball: the beams hit its front, the center is half a ball further
        if cfg.mode == "ball":
            dist += cfg.ball_size / 2

        # direction = mean angle, range = median distance (robust to stray
        # edge points behind the finger)
        a = math.atan2(gy.mean(), gx.mean())

        blobs.append((math.cos(a) * dist, math.sin(a) * dist, len(g), width))

    return blobs



# =========================
# SMOOTHING
# =========================

class OneEuro:
    """One Euro filter. Reads min cutoff / beta from the settings every call (live tuning)."""

    def __init__(self, cfg, d_cutoff=1.0):

        self.cfg = cfg
        self.d_cutoff = d_cutoff
        self.t = None


    @staticmethod
    def _alpha(cutoff, dt):

        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)


    def __call__(self, x, y, t):

        if self.t is None:
            self.t, self.x, self.y, self.dx, self.dy = t, x, y, 0.0, 0.0
            return x, y

        dt = max(t - self.t, 1e-3)
        self.t = t

        a = self._alpha(self.d_cutoff, dt)
        self.dx += a * ((x - self.x) / dt - self.dx)
        self.dy += a * ((y - self.y) / dt - self.dy)

        cutoff = self.cfg.smooth_min_cutoff + self.cfg.smooth_beta * math.hypot(self.dx, self.dy)

        a = self._alpha(cutoff, dt)
        self.x += a * (x - self.x)
        self.y += a * (y - self.y)

        return self.x, self.y



# =========================
# ID TRACKER
# =========================

class Touch:

    def __init__(self, x, y, t, cfg):

        self.id = None          # given on TOUCH DOWN
        self.active = False
        self.hits = 1
        self.raw = (x, y)
        self.vel = (0.0, 0.0)
        self.last_seen = t
        self.filter = OneEuro(cfg)
        self.x, self.y = self.filter(x, y, t)


    def predict(self, t):

        if not self.active and self.hits == 1:
            return self.raw

        dt = min(t - self.last_seen, 0.15)

        return (
            self.raw[0] + self.vel[0] * dt,
            self.raw[1] + self.vel[1] * dt
        )


    def update(self, x, y, t, freeze=False):
        """freeze: keep the reported x, y (ball mode: first position is the hit)."""

        dt = max(t - self.last_seen, 1e-3)

        vx = (x - self.raw[0]) / dt
        vy = (y - self.raw[1]) / dt
        self.vel = (0.5 * self.vel[0] + 0.5 * vx, 0.5 * self.vel[1] + 0.5 * vy)

        self.raw = (x, y)
        self.last_seen = t
        self.hits += 1

        if not freeze:
            self.x, self.y = self.filter(x, y, t)



class TouchTracker:

    def __init__(self, cfg):

        self.cfg = cfg
        self.touches = []
        self.next_id = 1
        self.hits = deque(maxlen=100)       # ball mode: (t, x, y, id) of recent hits
        self.hit_count = 0


    def _activate(self, touch, t, events):

        touch.active = True
        touch.id = self.next_id
        self.next_id += 1
        events.append((1, touch))

        if self.cfg.mode == "ball":
            self.hits.append((t, touch.x, touch.y, touch.id))
            self.hit_count += 1


    def blocked(self, x, y, t, alive=()):
        """Ball mode: a new hit this soon (and this close) after a hit is ignored."""

        cfg = self.cfg

        for ht, hx, hy, _ in self.hits:
            if t - ht < cfg.hit_delay and (cfg.hit_radius <= 0 or math.hypot(x - hx, y - hy) < cfg.hit_radius):
                return True

        # a piece of a ball that is still seen (falling down the wall, rolling)
        for other in alive:
            if other.active and math.hypot(x - other.raw[0], y - other.raw[1]) < cfg.match_distance:
                return True

        return False


    def update(self, blobs, t):
        """Returns events: list of (state, touch), state 1 down, 0 move, -1 up."""

        cfg = self.cfg
        ball = cfg.mode == "ball"

        # touches gone longer than release_time are released first, they must
        # not be matched to a new object (gap in the scans)
        events = self.expire(t)

        # all touch / blob pairs, closest first (global, so IDs never swap)
        pairs = []

        for ti, touch in enumerate(self.touches):

            px, py = touch.predict(t)

            for bi, (bx, by, _, _) in enumerate(blobs):

                d = math.hypot(bx - px, by - py)

                if d < cfg.match_distance:
                    pairs.append((d, ti, bi))

        pairs.sort()

        used_touches = set()
        used_blobs = set()

        for d, ti, bi in pairs:

            if ti in used_touches or bi in used_blobs:
                continue

            used_touches.add(ti)
            used_blobs.add(bi)

            touch = self.touches[ti]
            touch.update(blobs[bi][0], blobs[bi][1], t, freeze=ball)

            if touch.active:
                if not ball:
                    events.append((0, touch))   # ball: the hit position never moves

            elif ball or touch.hits >= cfg.confirm_scans:
                self._activate(touch, t, events)

        # touches without blob
        alive = []

        for ti, touch in enumerate(self.touches):

            if ti in used_touches:
                alive.append(touch)

            elif not touch.active:
                pass        # not confirmed yet -> was noise

            elif t - touch.last_seen > cfg.release_time:
                events.append((-1, touch))

            else:
                alive.append(touch)     # hidden for a moment, keep the ID

        # blobs without touch -> new candidates
        for bi, (bx, by, _, _) in enumerate(blobs):

            if bi in used_blobs:
                continue

            # piece of a hand that is already a touch (fingers split apart);
            # raw = where it is now (ball mode keeps x, y at the hit)
            if any(
                other.active and math.hypot(bx - other.raw[0], by - other.raw[1]) < cfg.touch_spacing
                for other in alive
            ):
                continue

            # ball bouncing back / rolling right after a hit
            if ball and self.blocked(bx, by, t, alive):
                continue

            touch = Touch(bx, by, t, cfg)

            # ball: the first scan that sees it is the hit (a throw may be seen only once)
            if ball or cfg.confirm_scans <= 1:
                self._activate(touch, t, events)

            alive.append(touch)

        self.touches = alive

        return events


    def expire(self, t):
        """Call between scans / when no scan arrives (lidar unplugged)."""

        events = []
        alive = []

        for touch in self.touches:

            if t - touch.last_seen <= self.cfg.release_time:
                alive.append(touch)

            elif touch.active:
                events.append((-1, touch))

        self.touches = alive

        return events


    def release_all(self):

        events = [(-1, touch) for touch in self.touches if touch.active]
        self.touches = []

        return events


    def active(self):

        return [touch for touch in self.touches if touch.active]



# =========================
# DETECTOR
# =========================

class TouchDetector:

    def __init__(self, cfg=None):

        self.cfg = cfg if cfg is not None else Settings()
        self.background = Background(self.cfg)
        self.tracker = TouchTracker(self.cfg)
        self.x = self.y = None      # map coordinates of the last scan
        self.mask = None            # new points of the last scan
        self.inside = None          # points inside the touch area
        self.blobs = []


    def calibrate(self, scans):

        self.background.learn(scans)


    def process(self, scan):

        self.x, self.y = self.cfg.to_world(scan.angle, scan.dist)
        self.mask, self.inside = self.background.new_points(scan, self.x, self.y)
        self.blobs = make_blobs(scan, self.x, self.y, self.mask, self.cfg)

        return self.tracker.update(self.blobs, scan.t)



# =========================
# OUTPUT
# =========================

STATE_NAME = {1: "DOWN", 0: "MOVE", -1: "UP"}


BALL_NAME = {1: "HIT", -1: "GONE"}


def send_events(osc, events, cfg):

    ball = cfg.mode == "ball"

    for state, touch in events:

        x, y = cfg.output(touch.x, touch.y)

        if osc is not None:
            osc.send_message("/touch", [float(x), float(y), int(touch.id), state])

        if state != 0:
            d = 3 if cfg.normalize else 0
            name = f"BALL  {BALL_NAME[state]:4s}" if ball else f"TOUCH {STATE_NAME[state]:4s}"
            print(f"{name} id={touch.id:<4d} x={x:7.{d}f} y={y:7.{d}f}")



def scan_background(lidar, seconds, on_progress=None):
    """Collect complete scans for `seconds`. on_progress(left) may return False to abort."""

    lidar.get_scans(timeout=0)          # drop old scans
    scans = []
    skip = 3                            # first scans after (re)start can be partial
    start = time.time()

    while time.time() - start < seconds:

        for scan in lidar.get_scans(timeout=0.1):

            if skip > 0:
                skip -= 1
            else:
                scans.append(scan)

        if on_progress is not None and on_progress(seconds - (time.time() - start)) is False:
            return None

    return scans



# =========================
# HEADLESS
# =========================

def run_headless(lidar, osc, detector, run_time):

    cfg = detector.cfg

    print(f"Scanning background for {cfg.calibration_time} s - keep the area empty...")
    scans = scan_background(lidar, cfg.calibration_time)
    detector.calibrate(scans)
    lidar.split_angle = detector.background.split_angle
    print(f"Background ready: {len(scans)} scans, split angle {lidar.split_angle:.1f} deg")
    print("Ready - touch!")

    start = time.time()

    try:
        while run_time is None or time.time() - start < run_time:

            scans = lidar.get_scans(timeout=0.2)

            if not scans:
                send_events(osc, detector.tracker.expire(time.monotonic()), cfg)
                continue

            for scan in scans:
                send_events(osc, detector.process(scan), cfg)

    except KeyboardInterrupt:
        pass

    send_events(osc, detector.tracker.release_all(), cfg)



# =========================
# WINDOW
# =========================

AREA_FILL = (14, 26, 44)
AREA_LINE = (60, 110, 200)
ANGLE_LINE = (0, 130, 150)
RING = (32, 32, 32)
MIN_RING = (90, 40, 40)
POINT_OUT = (0, 50, 95)
POINT_IN = (40, 130, 230)
BG_POINT = (70, 70, 70)


def run_window(lidar, osc, detector):

    import pygame
    from settings_ui import SettingsPanel

    cfg = detector.cfg
    bg = detector.background

    pygame.init()

    screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("STL27L Touch")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Arial", 18)
    small = pygame.font.SysFont("Arial", 14)
    big_font = pygame.font.SysFont("Arial", 32)

    zoom = ZOOM
    view_x = view_y = 0.0       # map point (mm) in the middle of the map view
    view_auto = True            # the view fits the whole background until you zoom or pan
    drag = None                 # ("area", dx, dy) or ("pan", mouse, view_x, view_y)
    rescan = False
    area_image = None
    area_key = None


    # -------------------------
    # VIEW
    # -------------------------

    def map_size():

        w, h = screen.get_size()

        return max(w - panel.shown_width(), 2), max(h, 2)


    def origin():
        """Screen position of the lidar."""

        mw, mh = map_size()

        return mw / 2 - view_x * zoom, mh / 2 + view_y * zoom


    def to_screen(x, y):

        ox, oy = origin()

        return int(ox + x * zoom), int(oy - y * zoom)


    def to_map(sx, sy):

        ox, oy = origin()

        return (sx - ox) / zoom, (oy - sy) / zoom


    def zoom_at(factor, pos):
        """Zoom and keep the map point under the mouse in place."""

        nonlocal zoom, view_x, view_y, view_auto

        view_auto = False
        wx, wy = to_map(*pos)
        zoom = min(max(zoom * factor, 0.02), 5.0)
        mw, mh = map_size()
        view_x = (mw / 2 - pos[0] + wx * zoom) / zoom
        view_y = (pos[1] + wy * zoom - mh / 2) / zoom


    def fit_view():
        """Zoom and centre so the whole background and the lidar fit the map view."""

        nonlocal zoom, view_x, view_y

        if bg.distance is None or not np.isfinite(bg.distance).any():
            return

        ok = np.isfinite(bg.distance)
        bx, by = cfg.to_world(centers[ok], bg.distance[ok])
        x0, x1 = min(bx.min(), 0.0), max(bx.max(), 0.0)
        y0, y1 = min(by.min(), 0.0), max(by.max(), 0.0)

        mw, mh = map_size()
        top, bottom, side = 64, 32, 40      # room for the text lines and the side tab

        zoom = min((mw - 2 * side) / max(x1 - x0, 1.0), (mh - top - bottom) / max(y1 - y0, 1.0))
        zoom = min(max(zoom, 0.02), 5.0)
        view_x = (x0 + x1) / 2
        view_y = (y0 + y1) / 2 + (top - bottom) / 2 / zoom


    def draw_points(xs, ys, color, size):

        mw, mh = map_size()
        ox, oy = origin()
        sx = (ox + xs * zoom).astype(np.int32)
        sy = (oy - ys * zoom).astype(np.int32)
        ok = (sx >= 0) & (sx < mw - size) & (sy >= 0) & (sy < mh - size)
        sx = sx[ok]
        sy = sy[ok]

        try:
            pixels = pygame.surfarray.pixels3d(screen)

        except (ValueError, pygame.error):
            for px, py in zip(sx.tolist(), sy.tolist()):
                screen.fill(color, (px, py, size, size))
            return

        for dx in range(size):
            for dy in range(size):
                pixels[sx + dx, sy + dy] = color

        del pixels


    def get_area_image():
        """Map background with the touch area filled. Rebuilt only when it changes."""

        nonlocal area_image, area_key

        mw, mh = map_size()
        key = (mw, mh, zoom, view_x, view_y, cfg.shape, cfg.area_x, cfg.area_y,
               cfg.radius, cfg.size, cfg.width, cfg.height,
               cfg.angle_from, cfg.angle_to, cfg.min_distance)

        if key != area_key:

            s = 2       # half resolution: fast enough to follow a drag
            ox, oy = origin()
            xs = (np.arange(0, mw, s) + s / 2 - ox) / zoom
            ys = (oy - (np.arange(0, mh, s) + s / 2)) / zoom
            gx, gy = np.meshgrid(xs, ys, indexing="ij")

            rgb = np.zeros(gx.shape + (3,), dtype=np.uint8)
            rgb[cfg.in_area(gx, gy, np.hypot(gx, gy))] = AREA_FILL

            area_image = pygame.transform.scale(
                pygame.surfarray.make_surface(rgb), (gx.shape[0] * s, gx.shape[1] * s)
            )
            area_key = key

        return area_image


    # -------------------------
    # SETTINGS
    # -------------------------

    def settings_changed(final):

        bg.update_threshold()

        if not final:
            return

        bg.update_split()

        if abs(lidar.split_angle - bg.split_angle) > 1e-6:
            lidar.split_angle = bg.split_angle

        try:
            cfg.save()
            panel.flash("Saved")
        except OSError as e:
            print(f"Settings not saved: {e}")
            panel.flash("Save failed!")


    def ask_rescan():

        nonlocal rescan
        rescan = True


    panel = SettingsPanel(cfg, settings_changed, [("Scan background (B)", ask_rescan)], PANEL_WIDTH)
    panel.extra["Output"] = [f"OSC to {OSC_IP}:{OSC_PORT}   /touch [x, y, id, state]"]


    # -------------------------
    # BACKGROUND SCAN
    # -------------------------

    def calibrate():

        def progress(left):

            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    return False

            w, h = screen.get_size()
            screen.fill((0, 0, 0))
            text = big_font.render(f"Scanning background... {max(left, 0):.0f} s", True, (255, 200, 0))
            screen.blit(text, (w // 2 - text.get_width() // 2, h // 2 - 40))
            text = font.render("keep the touch area empty", True, (200, 200, 200))
            screen.blit(text, (w // 2 - text.get_width() // 2, h // 2 + 10))
            pygame.display.flip()

        print(f"Scanning background for {cfg.calibration_time} s - keep the area empty...")
        scans = scan_background(lidar, cfg.calibration_time, progress)

        if scans is None:
            return False

        detector.calibrate(scans)
        lidar.split_angle = bg.split_angle
        print(f"Background ready: {len(scans)} scans, split angle {lidar.split_angle:.1f} deg")

        return True


    if not calibrate():
        pygame.quit()
        return

    centers = (np.arange(bg.bins) + 0.5) * BG_BIN

    scan = None
    running = True
    panel.place(screen.get_size())

    while running:

        screen = pygame.display.get_surface()

        for event in pygame.event.get():

            if event.type == pygame.QUIT:
                running = False
                continue

            # a drag on the map ends on the map even if the mouse is over the panel
            map_drag = drag is not None and event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONUP)

            if not map_drag and panel.slide(event):
                continue

            if panel.shown_width() > 0 and not map_drag and panel.handle(event):
                continue

            if event.type == pygame.KEYDOWN:

                if event.key == pygame.K_ESCAPE:
                    running = False

                elif event.key == pygame.K_UP:
                    zoom_at(1.2, [v / 2 for v in map_size()])

                elif event.key == pygame.K_DOWN:
                    zoom_at(1 / 1.2, [v / 2 for v in map_size()])

                elif event.key == pygame.K_b:
                    rescan = True

                elif event.key == pygame.K_h:
                    panel.show(not panel.open)

                elif event.key == pygame.K_v:
                    view_auto = True

            elif event.type == pygame.MOUSEWHEEL:
                zoom_at(1.15 ** event.y, pygame.mouse.get_pos())

            elif event.type == pygame.MOUSEBUTTONDOWN:

                if event.button == 1:
                    wx, wy = to_map(*event.pos)
                    if cfg.in_shape(wx, wy):
                        drag = ("area", wx - cfg.area_x, wy - cfg.area_y)

                elif event.button == 3:
                    drag = ("pan", event.pos, view_x, view_y)
                    view_auto = False

            elif event.type == pygame.MOUSEMOTION and drag is not None:

                if drag[0] == "area":
                    wx, wy = to_map(*event.pos)
                    cfg.set("area_x", wx - drag[1])
                    cfg.set("area_y", wy - drag[2])
                    settings_changed(False)

                else:
                    _, start, vx, vy = drag
                    view_x = vx - (event.pos[0] - start[0]) / zoom
                    view_y = vy + (event.pos[1] - start[1]) / zoom

            elif event.type == pygame.MOUSEBUTTONUP and drag is not None:

                if drag[0] == "area":
                    settings_changed(True)

                drag = None

        if rescan:
            rescan = False
            send_events(osc, detector.tracker.release_all(), cfg)
            if not calibrate():
                running = False

        panel.tick()
        panel.place(screen.get_size())

        if view_auto:
            fit_view()

        # every scan is processed (a thrown ball may be in only one of them),
        # only the newest is drawn
        new_scans = lidar.get_scans(timeout=0.03)

        for scan in new_scans:
            send_events(osc, detector.process(scan), cfg)

        if not new_scans:
            send_events(osc, detector.tracker.expire(time.monotonic()), cfg)


        # =========================
        # DRAW
        # =========================

        mw, mh = map_size()
        ox, oy = origin()
        lidar_pos = (int(ox), int(oy))

        screen.fill((0, 0, 0))
        screen.blit(get_area_image(), (0, 0))

        # 1 m rings around the lidar
        for r in range(1000, 6001, 1000):
            pygame.draw.circle(screen, RING, lidar_pos, int(r * zoom), 1)
            screen.blit(small.render(f"{r // 1000} m", True, (70, 70, 70)), (ox + r * zoom + 3, oy + 2))

        # touch area outline
        if cfg.shape == "circle":
            pygame.draw.circle(screen, AREA_LINE, to_screen(cfg.area_x, cfg.area_y), max(1, int(cfg.radius * zoom)), 1)
        else:
            x0, x1, y0, y1 = cfg.area_box()
            sx0, sy0 = to_screen(x0, y1)
            sx1, sy1 = to_screen(x1, y0)
            pygame.draw.rect(screen, AREA_LINE, (sx0, sy0, sx1 - sx0, sy1 - sy0), 1)

        ax, ay = to_screen(cfg.area_x, cfg.area_y)
        pygame.draw.line(screen, AREA_LINE, (ax - 6, ay), (ax + 6, ay), 1)
        pygame.draw.line(screen, AREA_LINE, (ax, ay - 6), (ax, ay + 6), 1)

        # angle range
        if not cfg.all_angles():
            length = cfg.reach() + 300
            for a in (cfg.angle_from, cfg.angle_to):
                r = math.radians(a)
                end = to_screen(math.cos(r) * length, math.sin(r) * length)
                pygame.draw.line(screen, ANGLE_LINE, lidar_pos, end, 1)
                label = to_screen(math.cos(r) * 90 / zoom, math.sin(r) * 90 / zoom)
                screen.blit(small.render(f"{a}°", True, ANGLE_LINE), label)

        if cfg.min_distance > 0:
            pygame.draw.circle(screen, MIN_RING, lidar_pos, max(1, int(cfg.min_distance * zoom)), 1)

        # background
        if cfg.show_background and bg.distance is not None:
            ok = np.isfinite(bg.distance)
            bx, by = cfg.to_world(centers[ok], bg.distance[ok])
            draw_points(bx, by, BG_POINT, 2)

        # lidar 0 deg direction (shows rotation / mirror) and scan seam
        zx, zy = cfg.to_world(0.0, 40 / zoom)
        pygame.draw.line(screen, (200, 60, 60), lidar_pos, to_screen(zx, zy), 2)
        sx, sy = cfg.to_world(bg.split_angle, 400)
        pygame.draw.line(screen, (80, 0, 0), lidar_pos, to_screen(sx, sy), 1)

        if scan is not None and detector.x is not None:

            if cfg.show_points:
                draw_points(detector.x[~detector.inside], detector.y[~detector.inside], POINT_OUT, 2)
                draw_points(detector.x[detector.inside], detector.y[detector.inside], POINT_IN, 2)

            if detector.mask.any():
                draw_points(detector.x[detector.mask], detector.y[detector.mask], (255, 220, 0), 4)

        pygame.draw.circle(screen, (255, 0, 0), lidar_pos, 5)

        # blobs not yet confirmed
        for touch in detector.tracker.touches:
            if not touch.active:
                pygame.draw.circle(screen, (255, 140, 0), to_screen(touch.x, touch.y), 8, 1)

        d = 2 if cfg.normalize else 0
        tracker = detector.tracker
        now = time.monotonic()

        if cfg.mode == "ball":

            # a hit is drawn at once, solid, for "Show hit for" seconds (no animation);
            # the delay zone is drawn while it blocks
            for ht, hx, hy, hid in tracker.hits:

                age = now - ht
                hx_s, hy_s = to_screen(hx, hy)

                if age < cfg.hit_delay and cfg.hit_radius > 0:
                    pygame.draw.circle(screen, (120, 70, 0), (hx_s, hy_s), max(1, int(cfg.hit_radius * zoom)), 1)

                if age > cfg.show_hit:
                    continue

                color = (255, 60, 200)
                pygame.draw.circle(screen, color, (hx_s, hy_s), 12)
                pygame.draw.circle(screen, (255, 255, 255), (hx_s, hy_s), 3)
                screen.blit(font.render(f"HIT {hid}", True, color), (hx_s + 16, hy_s - 18))

                out_x, out_y = cfg.output(hx, hy)
                screen.blit(small.render(f"{out_x:.{d}f}, {out_y:.{d}f}", True, color), (hx_s + 16, hy_s + 2))

            wait = max((cfg.hit_delay - (now - ht) for ht, *_ in tracker.hits), default=0)

            if wait > 0 and cfg.hit_radius <= 0:
                screen.blit(font.render(f"delay: next hit in {wait:.1f} s", True, (255, 160, 0)), (10, 58))

        else:

            for touch in tracker.active():

                tx, ty = to_screen(touch.x, touch.y)
                hidden = touch.last_seen < (scan.t if scan is not None else 0)
                color = (0, 120, 0) if hidden else (0, 255, 0)

                pygame.draw.circle(screen, color, (tx, ty), 14, 2)
                screen.blit(font.render(f"ID {touch.id}", True, color), (tx + 16, ty - 18))

                out_x, out_y = cfg.output(touch.x, touch.y)
                screen.blit(small.render(f"{out_x:.{d}f}, {out_y:.{d}f}", True, color), (tx + 16, ty + 2))

        # info
        hz = scan.hz if scan is not None else 0
        points = len(scan.dist) if scan is not None else 0
        new = int(detector.mask.sum()) if detector.mask is not None else 0
        count = f"BALL mode  Hits:{tracker.hit_count}" if cfg.mode == "ball" else f"Touches:{len(tracker.active())}"

        info = font.render(
            f"FPS:{clock.get_fps():.0f}  Lidar:{hz:.1f}Hz  Points:{points}  New:{new}  "
            f"{count}  CRC err:{lidar.crc_errors}",
            True, (255, 255, 255)
        )
        screen.blit(info, (10, 10))

        mx, my = pygame.mouse.get_pos()

        if mx < mw:
            wx, wy = to_map(mx, my)
            angle = math.degrees(math.atan2(wy, wx)) % 360
            mouse = small.render(
                f"mouse: x={wx:.0f} y={wy:.0f} mm   angle={angle:.0f}°   dist={math.hypot(wx, wy):.0f} mm",
                True, (150, 150, 150)
            )
            screen.blit(mouse, (10, 36))

        keys = small.render(
            "wheel zoom | right drag pan | drag area to move | B background | H / swipe settings | V fit view | ESC quit",
            True, (100, 100, 100)
        )
        screen.blit(keys, (10, mh - 24))

        panel.draw(screen)

        pygame.display.flip()
        clock.tick(60)

    send_events(osc, detector.tracker.release_all(), cfg)
    pygame.quit()



# =========================
# MAIN
# =========================

def main():

    parser = argparse.ArgumentParser(description="STL27L lidar touch")
    parser.add_argument("--port", default=PORT)
    parser.add_argument("--headless", action="store_true", help="no window, print touches only")
    parser.add_argument("--time", type=float, default=None, help="headless: stop after N seconds")
    parser.add_argument("--no-osc", action="store_true")
    parser.add_argument("--settings", default=None, help="settings file (default: settings.json next to this file)")
    args = parser.parse_args()

    cfg = Settings(args.settings) if args.settings else Settings()

    if cfg.load():
        print(f"Settings loaded: {cfg.path}")
    else:
        print("Settings: defaults (change them in the window, they are saved to settings.json)")

    osc = None

    if not args.no_osc:
        from pythonosc.udp_client import SimpleUDPClient
        osc = SimpleUDPClient(OSC_IP, OSC_PORT)

    lidar = D800(args.port)
    detector = TouchDetector(cfg)

    try:
        if args.headless:
            run_headless(lidar, osc, detector, args.time)
        else:
            run_window(lidar, osc, detector)
    finally:
        lidar.close()



if __name__ == "__main__":
    main()
