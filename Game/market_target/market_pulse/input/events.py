"""Input events, coordinate calibration and impact de-duplication. No pygame."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Impact:
    """A ball touched the wall. x, y are normalised wall coordinates:
    (0, 0) = top-left, (1, 1) = bottom-right, as seen by the players."""

    x: float
    y: float
    t: float  # time.perf_counter() when it was received
    source: str
    raw: tuple[float, float] = (0.0, 0.0)
    trusted: bool = True  # False: rough coordinates (a LiDAR that is not calibrated yet); see input/fusion.py


@dataclass(frozen=True)
class Control:
    """Operator / platform command: start, reset, player, throw_missed, heartbeat."""

    kind: str
    t: float
    source: str
    data: dict = field(default_factory=dict)


@dataclass
class Calibration:
    """Maps the sensor's normalised coordinates onto the wall."""

    swap_xy: bool = False
    flip_x: bool = False
    flip_y: bool = False
    scale_x: float = 1.0
    scale_y: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    def apply(self, x: float, y: float) -> tuple[float, float]:
        if self.swap_xy:
            x, y = y, x
        if self.flip_x:
            x = 1.0 - x
        if self.flip_y:
            y = 1.0 - y
        return x * self.scale_x + self.offset_x, y * self.scale_y + self.offset_y


class ImpactFilter:
    """Turns raw sensor events into one event per ball.

    - merge: a second event close in time and space is the same contact
      (sensors often report a ball for several frames, or flicker).
    - rebound: a ball dropping down along the wall after a hit can cross the
      sensor plane again a little lower; that is not a new throw.
    Positions are in centimetres.
    """

    def __init__(self, merge_window: float, merge_radius: float, rebound_window: float, rebound_dx: float):
        self.merge_window = merge_window
        self.merge_radius = merge_radius
        self.rebound_window = rebound_window
        self.rebound_dx = rebound_dx
        self._last: tuple[float, float, float] | None = None
        self.merged = 0
        self.rebounds = 0

    def accept(self, x: float, y: float, t: float) -> bool:
        last = self._last
        if last is not None:
            lx, ly, lt = last
            dt = t - lt
            if 0.0 <= dt < self.merge_window and math.hypot(x - lx, y - ly) < self.merge_radius:
                self.merged += 1
                return False
            if 0.0 <= dt < self.rebound_window and y > ly and abs(x - lx) < self.rebound_dx:
                self.rebounds += 1
                return False
        self._last = (x, y, t)
        return True

    def reset(self) -> None:
        self._last = None
