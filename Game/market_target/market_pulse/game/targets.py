"""Targets and their motion. Pure Python; all lengths in centimetres on the wall.

A target's drawn position and size are analytic functions of time, so the hit
test can ask "where was this target when the ball touched the wall?" and get
exactly what was on screen at that moment, including the glide to a new
position, the pop-in after spawning, the sway and the size pulse.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

TAU = math.tau


def ease_out_cubic(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1.0 - (1.0 - p) ** 3


def ease_out_back(p: float, overshoot: float = 1.6) -> float:
    p = min(max(p, 0.0), 1.0) - 1.0
    return 1.0 + (overshoot + 1.0) * p**3 + overshoot * p**2


class TimeWarp:
    """Motion time: game time run faster or slower, as a function of game time.

    Market Rush speeds the boxes up by changing this rate, with a ramp, instead of
    changing a speed value: a box's position stays continuous and the hit test
    can still ask where it was at any earlier moment.
    """

    def __init__(self) -> None:
        self._ramps: list[tuple[float, float, float, float, float]] = []  # start, time there, from, to, duration

    def _ramp_at(self, t: float) -> tuple[float, float, float, float, float] | None:
        for ramp in reversed(self._ramps):
            if t >= ramp[0]:
                return ramp
        return None

    def rate(self, t: float) -> float:
        ramp = self._ramp_at(t)
        if ramp is None:
            return 1.0
        t0, _, r0, r1, dur = ramp
        return r1 if t - t0 >= dur else r0 + (r1 - r0) * (t - t0) / dur

    def at(self, t: float) -> float:
        ramp = self._ramp_at(t)
        if ramp is None:
            return t
        t0, m0, r0, r1, dur = ramp
        dt = t - t0
        if dt < dur:
            return m0 + r0 * dt + 0.5 * (r1 - r0) / dur * dt * dt
        return m0 + 0.5 * (r0 + r1) * dur + r1 * (dt - dur)

    def drop_after(self, t: float) -> None:
        """Forget ramps planned for after game time t."""
        self._ramps = [ramp for ramp in self._ramps if ramp[0] <= t]

    def ramp(self, t0: float, rate: float, dur: float) -> None:
        """From game time t0 (not earlier than the last ramp), change to ``rate`` over ``dur`` seconds."""
        self._ramps.append((t0, self.at(t0), self.rate(t0), rate, max(0.0, dur)))


@dataclass
class Motion:
    pattern: str = "still"  # still | sway_x | sway_y | orbit | figure8 | volatile
    amplitude: float = 0.0  # cm
    period: float = 4.0  # s
    pulse: float = 0.0  # size change, fraction of the side
    pulse_period: float = 2.0  # s
    phase: float = 0.0  # rad, randomised per target so targets do not move in sync
    direction: int = 1  # +1 / -1, randomised per target

    def extent(self) -> tuple[float, float]:
        """Largest |dx|, |dy| this motion can reach (before layout scaling)."""
        a = self.amplitude
        return {
            "still": (0.0, 0.0),
            "sway_x": (a, 0.0),
            "sway_y": (0.0, a),
            "orbit": (a, a),
            "figure8": (a, a * 0.5),
            "volatile": (a, a),
        }.get(self.pattern, (0.0, 0.0))

    def offset(self, t: float, speed: float = 1.0) -> tuple[float, float]:
        if self.pattern == "still" or self.amplitude == 0.0:
            return 0.0, 0.0
        w = TAU * speed / self.period
        a = self.amplitude
        th = w * t + self.phase
        if self.pattern == "sway_x":
            return a * math.sin(th) * self.direction, 0.0
        if self.pattern == "sway_y":
            return 0.0, a * math.sin(th) * self.direction
        if self.pattern == "orbit":
            return a * math.cos(th), a * math.sin(th) * self.direction
        if self.pattern == "figure8":
            return a * math.sin(th) * self.direction, 0.5 * a * math.sin(2.0 * th)
        if self.pattern == "volatile":  # irregular, like a price; the weights add up to 1, so it stays within a
            return (a * (0.62 * math.sin(th) + 0.38 * math.sin(2.63 * th + 1.0)) * self.direction,
                    a * (0.55 * math.sin(1.37 * th + 2.0) + 0.45 * math.sin(3.1 * th)))
        return 0.0, 0.0

    def scale(self, t: float, speed: float = 1.0) -> float:
        if self.pulse == 0.0:
            return 1.0
        return 1.0 + self.pulse * math.sin(TAU * speed * t / self.pulse_period + self.phase * 0.7)


@dataclass(eq=False)  # identity comparison: two targets are never "equal"
class Target:
    id: int
    tier: str  # medium | small | tiny: sets the size and the points
    size: float  # base width of the tile, cm
    x: float  # layout centre, cm
    y: float
    motion: Motion = field(default_factory=Motion)
    motion_scale: float = 1.0  # dense layouts shrink the movement so targets never overlap
    born: float = 0.0  # game time the target appeared
    spawn_dur: float = 0.3
    # Glide from the previous layout position (set by move_to).
    from_x: float = 0.0
    from_y: float = 0.0
    from_scale: float = 1.0
    move_start: float = -1.0
    move_dur: float = 0.0
    speed: float = 1.0  # global motion speed multiplier
    warp: TimeWarp | None = None  # the round's motion time (Market Rush); None = game time
    die_at: float | None = None  # game time this box starts to leave (Market Rush extras)
    die_dur: float = 0.3
    rush_extra: bool = False
    product: str = ""  # which of the 13 product tiles it shows
    aspect: float = 1.0  # the tile's height / width: the hit area is the tile, not a square around it

    def __post_init__(self) -> None:
        self.from_x, self.from_y, self.from_scale = self.x, self.y, self.motion_scale

    def _age(self, t: float) -> float:
        """Time on the motion clock since this box appeared."""
        if self.warp is None:
            return t - self.born
        return self.warp.at(t) - self.warp.at(self.born)

    # -- layout -------------------------------------------------------------
    def max_side(self) -> float:
        return self.size * (1.0 + self.motion.pulse)

    def move_to(self, x: float, y: float, motion_scale: float, now: float, dur: float) -> None:
        cx, cy = self._layout_pos(now)
        self.from_x, self.from_y = cx, cy
        self.from_scale = self._motion_scale(now)
        self.x, self.y, self.motion_scale = x, y, motion_scale
        self.move_start, self.move_dur = now, dur

    def _move_progress(self, t: float) -> float:
        if self.move_dur <= 0.0 or self.move_start < 0.0:
            return 1.0
        return ease_out_cubic((t - self.move_start) / self.move_dur)

    def _layout_pos(self, t: float) -> tuple[float, float]:
        p = self._move_progress(t)
        return self.from_x + (self.x - self.from_x) * p, self.from_y + (self.y - self.from_y) * p

    def _motion_scale(self, t: float) -> float:
        p = self._move_progress(t)
        return self.from_scale + (self.motion_scale - self.from_scale) * p

    # -- what is on screen --------------------------------------------------
    def spawn_factor(self, t: float) -> float:
        if self.spawn_dur <= 0.0:
            return 1.0
        return max(0.0, ease_out_back((t - self.born) / self.spawn_dur))

    def gone(self, t: float) -> bool:
        return self.die_at is not None and t >= self.die_at + self.die_dur

    def _leave_factor(self, t: float) -> float:
        if self.die_at is None or t <= self.die_at:
            return 1.0
        return max(0.0, 1.0 - (t - self.die_at) / self.die_dur)

    def center_at(self, t: float) -> tuple[float, float]:
        lx, ly = self._layout_pos(t)
        dx, dy = self.motion.offset(self._age(t), self.speed)
        s = self._motion_scale(t)
        return lx + dx * s, ly + dy * s

    def full_side_at(self, t: float) -> float:
        """The width without the pop-in (the drawing's appear morph grows it by itself)."""
        return self.size * self.motion.scale(self._age(t), self.speed) * self._leave_factor(t)

    def side_at(self, t: float) -> float:
        return self.size * self.motion.scale(self._age(t), self.speed) * self.spawn_factor(t) * self._leave_factor(t)

    def contains(self, px: float, py: float, t: float, tolerance: float = 0.0) -> bool:
        if self.gone(t):
            return False
        cx, cy = self.center_at(t)
        side = self.side_at(t)
        return abs(px - cx) <= side / 2.0 + tolerance and abs(py - cy) <= side * self.aspect / 2.0 + tolerance

    def distance_to(self, px: float, py: float, t: float) -> float:
        cx, cy = self.center_at(t)
        return math.hypot(px - cx, py - cy)
