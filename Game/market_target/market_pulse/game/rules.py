"""One round of Market Pulse: the scenario's rules, with no pygame.

Rules implemented (from the scenario PDF and the add-on text):
- 60 seconds, with an optional ball limit. Three boxes at the start, one of each colour.
- A valid hit scores that colour's points, replaces the hit box with a new box of
  the same colour, and moves every box to a new random position.
- A red hit also opens one green, one blue and one red box (3 -> 6 -> 9 -> 12),
  up to the cap. At the cap a red hit still scores the most, but no boxes are added.
- A miss uses up one ball. Nothing is subtracted and the field does not change.
- Each ball scores at most once (duplicate/rebound events are filtered upstream).
- The round ends when time runs out or the last ball (if there is a limit) is
  resolved. Hits after the end of time do not count.
- Add-on combo: consecutive hits raise a multiplier; a miss resets it to x1.

Market Rush, a state of the round:
- Starts on the red hit that reaches rush.trigger_red_hits, if the round still has a
  rush left and any cooldown has passed. Red boxes on the wall do nothing by
  themselves. The touch that ends the round starts nothing.
- Lasts rush.duration_s and always stops with the round. A red hit during a rush gives
  a fresh rush.duration_s from that hit (rush.restart_on_red_hit), in the same phase,
  so the picture does not flash.
- While it runs: faster movement (the round's TimeWarp), extra boxes on a timer, and
  rush.score_multiplier on every hit whose impact time falls inside it, so the hit
  that started it is not multiplied.
- Every extra box leaves when it ends, so the field returns to its size before the rush.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import COLORS, Section
from .layout import Footprint, Rect, place, place_one
from .targets import Motion, Target, TimeWarp


@dataclass
class Outcome:
    kind: str  # "hit" | "miss"
    x: float  # impact position, cm
    y: float
    t: float  # impact time (game clock)
    color: str | None = None
    base: int = 0
    multiplier: float = 1  # the total applied to base
    combo_multiplier: float = 1
    rush_multiplier: float = 1
    points: int = 0
    target_id: int | None = None
    target_side: float = 0.0
    spawned: list[int] = field(default_factory=list)
    level_up: bool = False
    combo_broken: bool = False
    streak: int = 0
    throws_left: int = 0
    operator: bool = False  # miss entered by the operator (ball never reached the wall)


@dataclass
class Rush:
    started_at: float
    ends_at: float
    ended_at: float | None = None
    fade_from: float | None = None  # where the fade-in counts from, if not started_at


class Round:
    def __init__(self, cfg: Section, rng: np.random.Generator | None = None):
        self.cfg = cfg
        self.rng = rng or np.random.default_rng()
        wall_w, wall_h = cfg.wall.width_cm, cfg.wall.height_cm
        lay = cfg.layout
        self.field = Rect(
            lay.side_margin_cm,
            lay.hud_height_cm,
            wall_w - lay.side_margin_cm,
            wall_h - lay.footer_height_cm,
        )
        self.balls: int = cfg.round.balls
        self.duration: float = float(cfg.round.duration_s)
        self.max_targets: int = cfg.round.max_targets
        self.levels = [(lv["after_hits"], lv["multiplier"]) for lv in cfg.combo.levels] if cfg.combo.enabled else []

        self.targets: list[Target] = []
        self._next_id = 1
        self.score = 0
        self.throws = 0
        self.hits = {c: 0 for c in COLORS}
        self.points_by_color = {c: 0 for c in COLORS}
        self.streak = 0
        self.best_streak = 0
        self.best_multiplier: float = 1
        self.started_at: float | None = None
        self.ends_at: float | None = None
        self.finished_at: float | None = None
        self.end_reason: str | None = None  # "time" | "balls" | "aborted"
        self.last_layout_fallback = False
        self.last_motion_scale = 1.0

        self.warp = TimeWarp()
        self.rush_enabled: bool = bool(cfg.rush.enabled)
        self.rushes: list[Rush] = []
        self.rush: Rush | None = None  # the running one
        self.rush_hits = 0
        self.rush_points = 0
        self.rush_throws = 0
        self._red_hits_towards_rush = 0
        self._rush_ready_at = float("-inf")
        self._rush_next_spawn = 0.0
        self._rush_spawn_i = 0
        self.events: list[tuple[str, dict]] = []  # rush_start, rush_end, rush_spawn; read by Flow

    # -- state ----------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.started_at is not None and self.end_reason is None

    @property
    def finished(self) -> bool:
        return self.end_reason is not None

    @property
    def throws_left(self) -> int:
        return max(0, self.balls - self.throws)  # stays 0 when there is no ball limit

    @property
    def out_of_balls(self) -> bool:
        return self.balls > 0 and self.throws >= self.balls

    @property
    def total_hits(self) -> int:
        return sum(self.hits.values())

    def time_left(self, now: float) -> float:
        if self.started_at is None:
            return self.duration
        if self.finished_at is not None:
            now = min(now, self.finished_at)
        return max(0.0, self.ends_at - now)

    def multiplier_for(self, streak: int) -> float:
        """Multiplier for a hit that follows ``streak`` consecutive hits."""
        m: float = 1
        for after, mult in self.levels:
            if streak >= after:
                m = mult
        return m

    @property
    def next_multiplier(self) -> float:
        return self.multiplier_for(self.streak)

    @property
    def max_multiplier(self) -> float:
        return max([1] + [m for _, m in self.levels])

    # -- Market Rush ----------------------------------------------------------
    def rush_at(self, t: float) -> Rush | None:
        """The rush running at game time t. Used for scoring with the impact time."""
        for rush in self.rushes:
            if rush.started_at <= t < rush.ends_at:
                return rush
        return None

    def rush_multiplier_at(self, t: float) -> float:
        return float(self.cfg.rush.score_multiplier) if self.rush_at(t) is not None else 1

    def rush_intensity(self, t: float) -> float:
        """0..1 for the screen: fades in from the start and out before the end."""
        rush = self.rush_at(t)
        if rush is None:
            return 0.0
        fade = float(self.cfg.rush.transition_s)
        if fade <= 0.0:
            return 1.0
        fade_from = rush.started_at if rush.fade_from is None else rush.fade_from
        return min(1.0, (t - fade_from) / fade, (rush.ends_at - t) / fade)

    def _note_red_hit(self, now: float) -> None:
        """The only way a rush starts: a red hit."""
        if not self.rush_enabled or self.out_of_balls:
            return
        rc = self.cfg.rush
        if self.rush is not None:
            if rc.restart_on_red_hit:
                self._restart_rush(now)
            return
        if now < self._rush_ready_at:
            return
        if len(self.rushes) >= rc.max_per_round:
            return
        self._red_hits_towards_rush += 1
        if self._red_hits_towards_rush >= rc.trigger_red_hits:
            self._start_rush(now)

    def _start_rush(self, now: float) -> None:
        rc = self.cfg.rush
        rush = Rush(now, min(now + float(rc.duration_s), self.ends_at))
        self.rushes.append(rush)
        self.rush = rush
        self._red_hits_towards_rush = 0
        self._rush_next_spawn = now + float(rc.spawn_interval_s) * 0.5
        self._plan_speed(now, rush)
        self.events.append(("rush_start", {"multiplier": rc.score_multiplier, "ends_at": rush.ends_at}))

    def _plan_speed(self, now: float, rush: Rush) -> None:
        rc = self.cfg.rush
        ramp = min(float(rc.speed_ramp_s), (rush.ends_at - now) / 2)
        self.warp.ramp(now, float(rc.speed), ramp)
        self.warp.ramp(rush.ends_at - ramp, 1.0, ramp)  # back to normal exactly when it ends

    def _restart_rush(self, now: float) -> None:
        """A red hit during a rush: a fresh rush.duration_s from now, in the same phase."""
        rc = self.cfg.rush
        rush = self.rush
        old_end = rush.ends_at
        # if the picture had begun to fade out, it returns from there
        rush.fade_from = now - self.rush_intensity(now) * float(rc.transition_s)
        rush.ends_at = min(now + float(rc.duration_s), self.ends_at)
        self.warp.drop_after(now)
        self._plan_speed(now, rush)
        for tg in self.targets:
            if tg.rush_extra and tg.die_at is not None and tg.die_at >= old_end:
                tg.die_at = rush.ends_at
        self.events.append(("rush_again", {"multiplier": rc.score_multiplier, "ends_at": rush.ends_at}))

    def _end_rush(self, when: float, reason: str) -> None:
        rush = self.rush
        rush.ended_at = when
        rush.ends_at = min(rush.ends_at, when)
        self.rush = None
        self._rush_ready_at = when + float(self.cfg.rush.cooldown_s)
        self.events.append(("rush_end", {"reason": reason}))

    def _update_rush(self, now: float) -> None:
        rush = self.rush
        if rush is None:
            return
        if now >= rush.ends_at:
            self._end_rush(rush.ends_at, "time")
            return
        interval = float(self.cfg.rush.spawn_interval_s)
        while now >= self._rush_next_spawn:
            self._rush_next_spawn += interval
            if rush.ends_at - now > 1.0:  # no box that would only flash up and leave
                self._rush_spawn(now)

    def _rush_spawn(self, now: float) -> None:
        """One extra box, without moving the others: a ball may be in the air."""
        if len(self.targets) >= self.cfg.rush.max_targets:
            return
        others = []
        for tg in self.targets:
            ex, ey = tg.motion.extent()
            half = tg.max_side() / 2.0
            others.append((tg.x, tg.y, half + ex * tg.motion_scale, half + ey * tg.motion_scale))
        colors = self.cfg.rush.spawn_colors
        for _ in range(len(colors)):  # if this colour does not fit, try the next
            color = colors[self._rush_spawn_i % len(colors)]
            self._rush_spawn_i += 1
            tg = self._new_target(color, now)
            ex, ey = tg.motion.extent()
            spot = place_one(Footprint(tg.max_side() / 2.0, ex, ey), others, self.field, float(self.cfg.layout.gap_cm),
                             self.rng)
            if spot is not None:
                tg.x, tg.y, tg.motion_scale = spot
                tg.from_x, tg.from_y, tg.from_scale = spot
                self._mark_rush_extra(tg, now)
                self.targets.append(tg)
                self.events.append(("rush_spawn", {"ids": [tg.id]}))
                return

    def _mark_rush_extra(self, tg: Target, now: float) -> None:
        tg.rush_extra = True
        tg.die_at = min(now + float(self.cfg.rush.spawn_lifetime_s), self.rush.ends_at)

    def drain_events(self) -> list[tuple[str, dict]]:
        ev, self.events = self.events, []
        return ev

    # -- lifecycle --------------------------------------------------------------
    def start(self, now: float) -> None:
        if self.started_at is not None:
            return  # one round has one timer
        self.started_at = now
        self.ends_at = now + self.duration
        for color in self.cfg.round.initial:
            self.targets.append(self._new_target(color, now))
        self._relayout(now, moving=[])

    def update(self, now: float) -> None:
        if not self.running:
            return
        if now >= self.ends_at:
            self._finish("time", self.ends_at)
            return
        self._update_rush(now)
        if any(tg.gone(now) for tg in self.targets):
            self.targets = [tg for tg in self.targets if not tg.gone(now)]

    def abort(self, now: float) -> None:
        if not self.finished:
            self._finish("aborted", now)

    def _finish(self, reason: str, when: float) -> None:
        if self.rush is not None:
            self._end_rush(when, "round")
        self.end_reason = reason
        self.finished_at = when

    # -- throws ---------------------------------------------------------------
    def register_impact(self, x: float, y: float, t: float, now: float) -> Outcome | None:
        """A ball touched the wall at (x, y) cm at game time ``t``.

        Returns None if the impact does not count (round not running, or the
        ball arrived after time ran out).
        """
        if not self.running or self.out_of_balls:
            return None
        if t > self.ends_at:
            return None  # only hits before the end of time count
        self.throws += 1
        if self.rush_at(t) is not None:
            self.rush_throws += 1
        target = self._target_at(x, y, t)
        if target is None:
            out = self._miss(x, y, t)
        else:
            out = self._hit(target, x, y, t, now)
        self._after_throw(now)
        return out

    def register_missed_throw(self, now: float) -> Outcome | None:
        """Operator input: a ball that never reached the wall."""
        if not self.running or self.out_of_balls:
            return None
        self.throws += 1
        if self.rush_at(now) is not None:
            self.rush_throws += 1
        out = self._miss(-1.0, -1.0, now)
        out.operator = True
        self._after_throw(now)
        return out

    def _after_throw(self, now: float) -> None:
        if self.out_of_balls:
            self._finish("balls", now)

    @property
    def tolerance(self) -> float:
        """input.ball_radius_cm: a hit this far outside a box still counts (F5 changes it at once)."""
        return max(0.0, float(self.cfg.input.ball_radius_cm))

    def _target_at(self, x: float, y: float, t: float) -> Target | None:
        tolerance = self.tolerance
        hits = [tg for tg in self.targets if tg.contains(x, y, t, tolerance)]
        if not hits:
            return None
        return min(hits, key=lambda tg: tg.distance_to(x, y, t))

    def _miss(self, x: float, y: float, t: float) -> Outcome:
        broken = self.next_multiplier > 1
        self.streak = 0
        return Outcome("miss", x, y, t, combo_broken=broken, throws_left=self.throws_left)

    def _hit(self, target: Target, x: float, y: float, t: float, now: float) -> Outcome:
        color = target.color
        before = self.next_multiplier
        combo = self.multiplier_for(self.streak)
        rush_mult = self.rush_multiplier_at(t)
        mult = combo * rush_mult if self.cfg.rush.stack_with_combo else max(combo, rush_mult)
        base = int(self.cfg.targets[color].points)
        points = int(round(base * mult))  # the one place a hit's points are worked out
        self.score += points
        if self.rush_at(t) is not None:
            self.rush_hits += 1
            self.rush_points += points
        self.hits[color] += 1
        self.points_by_color[color] += points
        self.streak += 1
        self.best_streak = max(self.best_streak, self.streak)
        self.best_multiplier = max(self.best_multiplier, mult)
        side = target.side_at(t)

        # Replace the hit box with a new one of the same colour (an extra box that was
        # already leaving at the end of a rush is not replaced)...
        self.targets.remove(target)
        rush = self.rush
        spawned = [self._new_target(color, now)] if rush is not None or not target.rush_extra else []
        # ...and, for red, open extra boxes until the cap.
        cap = self.cfg.rush.max_targets if rush is not None else self.max_targets
        for extra in self.cfg.targets[color].spawn_on_hit:
            if len(self.targets) + len(spawned) >= cap:
                break
            tg = self._new_target(extra, now)
            if rush is not None:
                self._mark_rush_extra(tg, now)
            spawned.append(tg)
        if rush is not None and target.rush_extra:
            self._mark_rush_extra(spawned[0], now)
        moving = list(self.targets)
        self.targets.extend(spawned)
        self._relayout(now, moving=moving)
        if color == "red":
            self._note_red_hit(now)

        return Outcome(
            "hit",
            x,
            y,
            t,
            color=color,
            base=base,
            multiplier=mult,
            combo_multiplier=combo,
            rush_multiplier=rush_mult,
            points=points,
            target_id=target.id,
            target_side=side,
            spawned=[s.id for s in spawned],
            level_up=self.next_multiplier > before,
            streak=self.streak,
            throws_left=self.throws_left,
        )

    # -- targets ----------------------------------------------------------------
    def _new_target(self, color: str, now: float) -> Target:
        mc = self.cfg.motion[color]
        enabled = self.cfg.motion.enabled
        motion = Motion(
            pattern=mc.pattern if enabled else "still",
            amplitude=float(mc.amplitude_cm) if enabled else 0.0,
            period=float(mc.period_s),
            pulse=float(mc.pulse) if enabled else 0.0,
            pulse_period=float(mc.pulse_period_s),
            phase=float(self.rng.uniform(0, 6.283185307)),
            direction=int(self.rng.choice((-1, 1))),
        )
        size = float(self.cfg.targets[color].size_cm)
        min_size = float(self.cfg.targets.min_size_cm)
        # Never let the pulse shrink a target below the smallest allowed size.
        if motion.pulse and size * (1 - motion.pulse) < min_size:
            motion.pulse = max(0.0, 1 - min_size / size)
        tg = Target(
            id=self._next_id,
            color=color,
            size=size,
            x=0.0,
            y=0.0,
            motion=motion,
            born=now,
            spawn_dur=float(self.cfg.layout.spawn_s),
            speed=float(self.cfg.motion.speed),
            warp=self.warp,
        )
        self._next_id += 1
        return tg

    def _relayout(self, now: float, moving: list[Target]) -> None:
        moving_ids = {tg.id for tg in moving}
        fps = []
        prev: list[tuple[float, float] | None] = []
        for tg in self.targets:
            ex, ey = tg.motion.extent()
            fps.append(Footprint(tg.max_side() / 2.0, ex, ey))
            prev.append(tg.center_at(now) if tg.id in moving_ids else None)
        res = place(
            fps,
            self.field,
            float(self.cfg.layout.gap_cm),
            self.rng,
            prev=prev,
            min_move=float(self.cfg.layout.min_move_cm),
        )
        self.last_layout_fallback = res.fallback
        self.last_motion_scale = res.motion_scale
        dur = float(self.cfg.layout.transition_s)
        for tg, (cx, cy) in zip(self.targets, res.centers):
            if tg.id in moving_ids:
                tg.move_to(cx, cy, res.motion_scale, now, dur)
            else:
                tg.x, tg.y, tg.motion_scale = cx, cy, res.motion_scale
                tg.from_x, tg.from_y, tg.from_scale = cx, cy, res.motion_scale

    # -- summary ----------------------------------------------------------------
    def summary(self) -> dict:
        return {
            "score": self.score,
            "throws": self.throws,
            "hits": dict(self.hits),
            "points_by_color": dict(self.points_by_color),
            "total_hits": self.total_hits,
            "rush": {
                "count": len(self.rushes),
                "throws": self.rush_throws,
                "hits": self.rush_hits,
                "points": self.rush_points,
            },
            "best_streak": self.best_streak,
            "best_multiplier": self.best_multiplier,
            "end_reason": self.end_reason,
            "duration_s": round(
                (self.finished_at or 0.0) - (self.started_at or 0.0), 2
            ),
            "targets_at_end": len(self.targets),
        }
