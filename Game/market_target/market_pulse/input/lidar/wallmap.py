"""Lidar map (mm) -> wall (cm), and the touch area that follows the wall.

The lidar scans a plane just in front of the wall, so a point of that plane maps onto the wall by
an affine map (turn, scale, shift, mirror). It is fitted by least squares through calibration
points: the wall position of a cross (cm) and where the lidar saw the ball hit it (mm), taken with
F2 then C in the game. Four points give two spare equations, so one bad throw shows up as a large
error instead of a silently wrong wall.

Without calibration points the touch area of the lidar tool stands for the wall: its box, with
the top of the map at the top of the wall.
"""
from __future__ import annotations

import math

import numpy as np

from .tuning import Settings

CAPTURE_GROW = 1.2  # without a calibration, the area while the crosses are taken is this much bigger all round
CAPTURE_MARGIN_CM = 60.0  # with one, the area while the crosses are taken: the wall and this much around it
GUIDE_MARGIN_CM = 100.0  # the same with a calibration made for another wall size (the picture's shape changed)


class WallMap:
    def __init__(self, m: np.ndarray, error_cm: float = 0.0, worst_cm: float = 0.0):
        self.m = np.asarray(m, dtype=np.float64)  # 2 x 3: wall = m @ (x, y, 1)
        full = np.vstack([self.m, [0.0, 0.0, 1.0]])
        if abs(np.linalg.det(full)) < 1e-12:
            raise ValueError("the points do not span the wall (all on one line?)")
        self.inv = np.linalg.inv(full)[:2]
        self.error_cm = error_cm  # root mean square distance between the crosses and the fitted hits
        self.worst_cm = worst_cm

    @classmethod
    def fit(cls, pairs: list[tuple[tuple[float, float], tuple[float, float]]]) -> WallMap:
        """pairs: ((lidar x mm, lidar y mm), (wall x cm, wall y cm)), at least three, not on one line."""
        if len(pairs) < 3:
            raise ValueError("at least 3 points are needed")
        src = np.array([[lx, ly, 1.0] for (lx, ly), _ in pairs])
        dst = np.array([w for _, w in pairs], dtype=np.float64)
        if np.linalg.matrix_rank(src) < 3:
            raise ValueError("the lidar saw the points on one line")
        sol, *_ = np.linalg.lstsq(src, dst, rcond=None)  # 3 x 2
        m = sol.T
        miss = np.hypot(*(src @ sol - dst).T)
        return cls(m, float(np.sqrt(np.mean(miss ** 2))), float(miss.max()))

    @classmethod
    def from_area(cls, tuning: Settings, wall_w: float, wall_h: float) -> WallMap:
        x0, x1, y0, y1 = tuning.area_box()
        sx = wall_w / max(x1 - x0, 1e-6)
        sy = wall_h / max(y1 - y0, 1e-6)
        return cls([[sx, 0.0, -x0 * sx], [0.0, -sy, y1 * sy]])

    def to_wall(self, x, y):
        m = self.m
        return m[0, 0] * x + m[0, 1] * y + m[0, 2], m[1, 0] * x + m[1, 1] * y + m[1, 2]

    def to_lidar(self, wx, wy):
        m = self.inv
        return m[0, 0] * wx + m[0, 1] * wy + m[0, 2], m[1, 0] * wx + m[1, 1] * wy + m[1, 2]


class GameTuning(Settings):
    """The lidar tool's settings, with the touch area taken from the wall once it is calibrated.

    capture: while calibration points are taken the area is
      - calibrated: the wall with CAPTURE_MARGIN_CM around it, plus the tool's area, so a corner
        is seen even when the lidar was moved a little since;
      - not calibrated: the box around the tool's area, made as wide as the wall's shape (a wall
        in the screen's shape, display.fill_screen, is much wider than the tool's circle) and 20 %
        bigger all round.
    Not the whole field: slices with no background (open space) report stray points far away
    (a real one: 2 m right of the wall, taken as cross 1 when the big box was used with a calibration).
    """

    def __init__(self, path: str, wall_w: float, wall_h: float, margin_cm: float, max_range: float):
        super().__init__(path)
        self.wall_w, self.wall_h = wall_w, wall_h
        self.margin_cm = margin_cm
        self.max_range = max_range
        self.fitted: WallMap | None = None  # from the calibration points
        # A calibration made for another wall size (display.fill_screen, a new picture shape): not
        # for the hits, but it shows where the wall is while the crosses are taken again.
        self.guide: WallMap | None = None
        self.guide_size = (wall_w, wall_h)
        self.capture = False

    @property
    def calibrated(self) -> bool:
        return self.fitted is not None

    @property
    def wall_map(self) -> WallMap:
        """The fitted map, or else the tool's area box as it is now (it may be loaded or changed later)."""
        return self.fitted if self.fitted is not None else WallMap.from_area(self, self.wall_w, self.wall_h)

    def capture_box(self) -> tuple[float, float, float, float]:
        """(x_min, x_max, y_min, y_max) mm: the area while the crosses are taken."""
        x0, x1, y0, y1 = self.area_box()
        hw = max(x1 - x0, (y1 - y0) * self.wall_w / self.wall_h) / 2 * CAPTURE_GROW
        hh = (y1 - y0) / 2 * CAPTURE_GROW
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return cx - hw, cx + hw, cy - hh, cy + hh

    def in_area(self, x, y, dist):
        dist = np.asarray(dist)
        if self.capture and self.calibrated:
            return self._on_wall(x, y, dist, CAPTURE_MARGIN_CM) | super().in_area(x, y, dist)
        if self.capture and self.guide is not None:
            return self._on_wall(x, y, dist, GUIDE_MARGIN_CM, self.guide, self.guide_size) | super().in_area(x, y, dist)
        if self.capture:
            x0, x1, y0, y1 = self.capture_box()
            ok = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1) & (dist >= self.min_distance)
            if not self.all_angles():
                ok &= self.in_angle(x, y)
            return ok
        if not self.calibrated:
            return super().in_area(x, y, dist)
        return self._on_wall(x, y, dist)

    def _on_wall(self, x, y, dist, margin_cm=None, wall_map=None, size=None):
        wx, wy = (wall_map or self.wall_map).to_wall(x, y)
        m = self.margin_cm if margin_cm is None else margin_cm
        w, h = size or (self.wall_w, self.wall_h)
        ok = (dist >= self.min_distance) & (wx >= -m) & (wx <= w + m) & (wy >= -m) & (wy <= h + m)
        if not self.all_angles():
            ok &= self.in_angle(x, y)
        return ok

    def reach(self):
        if not self.calibrated and self.capture and self.guide is not None:
            m, (w, h) = GUIDE_MARGIN_CM, self.guide_size
            corners = ((-m, -m), (w + m, -m), (-m, h + m), (w + m, h + m))
            return max(max(math.hypot(*self.guide.to_lidar(cx, cy)) for cx, cy in corners), super().reach())
        if not self.calibrated:
            if not self.capture:
                return super().reach()
            x0, x1, y0, y1 = self.capture_box()
            return min(max(math.hypot(x, y) for x in (x0, x1) for y in (y0, y1)), self.max_range)
        m = CAPTURE_MARGIN_CM if self.capture else self.margin_cm
        corners = ((-m, -m), (self.wall_w + m, -m), (-m, self.wall_h + m), (self.wall_w + m, self.wall_h + m))
        wall = max(math.hypot(*self.wall_map.to_lidar(cx, cy)) for cx, cy in corners)
        return max(wall, super().reach()) if self.capture else wall
