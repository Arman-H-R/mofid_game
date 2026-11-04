"""Touch / ball detection for the STL27L lidar, copied from LIdar/use_lib.py (the tuning tool).

The algorithm is the same as in the tool, so values tuned there work here:
  1. Background: the empty area is scanned for a few seconds; for every small angle slice the
     closest stable distance is stored.
  2. New point: clearly closer than the background at its angle, and inside the area.
  3. Blobs: new points grouped in scan order.
  4. Tracking: ball mode reports the first scan that sees a new object as the hit (at that x, y),
     then ignores new hits for "Delay after hit" seconds.
Only the window, the OSC output and the command line of the tool are left out.
"""

import math
import time
from collections import deque

import numpy as np

from .tuning import Settings


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
