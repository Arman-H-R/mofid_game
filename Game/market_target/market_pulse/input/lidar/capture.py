"""Taking the lidar's calibration points: a cross is shown on the wall, the next lidar hit is
where the lidar sees that cross. After the last cross the wall map is fitted. No pygame."""
from __future__ import annotations

import math

from .wallmap import WallMap

INSET_CM = 25.0  # the crosses of the F2 grid, this far in from each corner
MIN_APART_MM = 200.0  # a hit this close to a cross already taken is the same spot again, not the next cross
MAX_WORST_CM = 8.0  # refuse the fit beyond this: the fit spreads a miss, 8 cm here is a hit ~30 cm off its cross
FIT_RANGE = (0.6, 1.5)  # from cross 3 on: a hit's distance to each cross taken, over the wall's, must be in here
# A spot that fires again and again by itself is lidar noise, not a throw at a cross (a real one:
# (2910, 38) mm, every few seconds also with nobody at the wall, taken as cross 1 at once):
# a hit with NOISE_REPEATS hits before it within NOISE_RADIUS_MM in the last NOISE_WINDOW_S.
NOISE_RADIUS_MM = 150.0
NOISE_WINDOW_S = 20.0
NOISE_REPEATS = 2
# From cross 2 on, the way from the cross before to this one must point along the wall's edge
# between the two crosses, give or take this much (a real one: someone at the wall's left side,
# taken as crosses 1 and 2 one above the other, then every real throw was refused).
MAX_TURN_DEG = 35.0


def targets(wall_w: float, wall_h: float) -> list[tuple[float, float]]:
    """Top-left, top-right, bottom-right, bottom-left (wall cm)."""
    i = INSET_CM
    return [(i, i), (wall_w - i, i), (wall_w - i, wall_h - i), (i, wall_h - i)]


class Capture:
    def __init__(self, wall_w: float, wall_h: float, since: float, layout: WallMap | None = None):
        self.targets = targets(wall_w, wall_h)
        # Which way the wall lies in the lidar's field: a calibration made before (also one for
        # another wall size), else the tool's own (x to the right, y up, as WallMap.from_area).
        self.layout = layout
        self.seen: list[tuple[float, float]] = []  # lidar mm, one per cross done
        self.since = since  # hits up to this time (perf_counter) are older than the capture
        self.refused = 0  # hits too close to a cross already taken
        self.strays = 0  # hits that do not fit the wall the crosses taken so far show (something else in the field)
        self.noise = 0  # hits on a spot that keeps firing by itself
        self.misses = 0  # strays since the last cross taken: many, and a cross taken may be wrong
        self.noise_at: tuple[float, float] | None = None

    @property
    def index(self) -> int:
        return len(self.seen)

    @property
    def done(self) -> bool:
        return len(self.seen) >= len(self.targets)

    def target(self) -> tuple[float, float] | None:
        return None if self.done else self.targets[self.index]

    def feed(self, recent) -> bool:
        """Take the lidar hits in the input's recent list that came after the last one used.
        Returns True when a cross was taken."""
        took = False
        hits = [(rx, ry, t) for rx, ry, _x, _y, _ok, source, t in list(recent) if source == "lidar"]
        for rx, ry, t in hits:
            if t <= self.since or self.done:
                continue
            self.since = t
            if any(math.hypot(rx - sx, ry - sy) < MIN_APART_MM for sx, sy in self.seen):
                self.refused += 1
                continue
            if self.repeats((rx, ry, t), hits) >= NOISE_REPEATS:
                self.noise += 1
                self.noise_at = (rx, ry)
                continue
            if not self.fits((rx, ry)) or not self.heads((rx, ry)):
                self.strays += 1
                self.misses += 1
                continue
            self.seen.append((rx, ry))
            self.misses = 0
            took = True
        return took

    @staticmethod
    def repeats(hit: tuple[float, float, float], hits) -> int:
        """How many hits came just before this one at the same spot (also before the capture began)."""
        x, y, t = hit
        return sum(1 for hx, hy, ht in hits
                   if t - NOISE_WINDOW_S <= ht < t and math.hypot(hx - x, hy - y) <= NOISE_RADIUS_MM)

    def fits(self, hit: tuple[float, float]) -> bool:
        """Once two crosses are taken the lidar's scale is known (mm per cm along the top edge): the
        next cross is then about that far from each cross taken. A point elsewhere in the field
        (someone beside the wall) is not."""
        if len(self.seen) < 2 or self.done:
            return True
        scale = math.dist(self.seen[0], self.seen[1]) / max(math.dist(self.targets[0], self.targets[1]), 1e-6)
        target = self.targets[self.index]
        low, high = FIT_RANGE
        for seen, cross in zip(self.seen, self.targets):
            want = math.dist(target, cross) * scale
            if not low * want <= math.dist(hit, seen) <= high * want:
                return False
        return True

    def heads(self, hit: tuple[float, float]) -> bool:
        """From the cross before, the hit lies along the wall's edge to this cross."""
        if not self.seen or self.done:
            return True
        (ax, ay), (bx, by) = self.targets[self.index - 1], self.targets[self.index]
        if self.layout is None:
            want = (bx - ax, ay - by)
        else:
            (lax, lay), (lbx, lby) = self.layout.to_lidar(ax, ay), self.layout.to_lidar(bx, by)
            want = (lbx - lax, lby - lay)
        px, py = self.seen[-1]
        got = (hit[0] - px, hit[1] - py)
        turn = math.degrees(abs(math.atan2(want[0] * got[1] - want[1] * got[0], want[0] * got[0] + want[1] * got[1])))
        return turn <= MAX_TURN_DEG

    def undo(self) -> None:
        if self.seen:
            self.seen.pop()
        self.misses = 0

    def pairs(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        return list(zip(self.seen, self.targets))

    def fit(self) -> WallMap:
        """Raises ValueError when the hits do not fit the crosses."""
        wall_map = WallMap.fit(self.pairs())
        if wall_map.worst_cm > MAX_WORST_CM:
            raise ValueError(f"a hit was {wall_map.worst_cm:.0f} cm off its cross")
        return wall_map

    def points(self) -> list[dict]:
        """For input.lidar.calibration_points in the settings file."""
        return [{"wall_cm": [round(w[0], 1), round(w[1], 1)], "lidar_mm": [round(s[0], 1), round(s[1], 1)]}
                for s, w in self.pairs()]
