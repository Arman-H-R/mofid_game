"""The scenario's rules for one round (pure logic, no pygame)."""
from collections import Counter

import numpy as np
import pytest

from market_pulse.game.rules import Round
from conftest import make_cfg

T0 = 100.0


def started(cfg, rng):
    rnd = Round(cfg, rng)
    rnd.start(T0)
    return rnd


def throw_at(rnd, color, now):
    """Throw a ball at the centre of a box of ``color`` as it is drawn at ``now``."""
    tg = next(t for t in rnd.targets if t.color == color)
    x, y = tg.center_at(now)
    return rnd.register_impact(x, y, now, now)


def throw_away(rnd, now):
    """Throw a ball where no box is (the footer strip, below the play field)."""
    return rnd.register_impact(150.0, 390.0, now, now)


def colors(rnd):
    return Counter(t.color for t in rnd.targets)


def test_round_starts_with_one_box_of_each_colour(cfg, rng):
    rnd = started(cfg, rng)
    assert colors(rnd) == Counter({"green": 1, "red": 1, "blue": 1})
    assert rnd.throws_left == 10
    assert rnd.time_left(T0) == 60.0


def test_green_hit_replaces_box_with_same_colour_and_moves_all(cfg, rng):
    rnd = started(cfg, rng)
    now = T0 + 1.0
    before = {t.id: t.center_at(now) for t in rnd.targets}
    hit_id = next(t.id for t in rnd.targets if t.color == "green")
    out = throw_at(rnd, "green", now)
    assert out.kind == "hit" and out.color == "green" and out.points == 10
    assert colors(rnd) == Counter({"green": 1, "red": 1, "blue": 1})
    assert hit_id not in {t.id for t in rnd.targets}
    assert out.spawned and all(i not in before for i in out.spawned)
    for t in rnd.targets:
        if t.id in before:  # every remaining box glides to a new spot at least min_move away
            old = before[t.id]
            assert np.hypot(t.x - old[0], t.y - old[1]) >= cfg.layout.min_move_cm


def test_blue_hit_replaces_box_with_blue(cfg, rng):
    rnd = started(cfg, rng)
    out = throw_at(rnd, "blue", T0 + 1.0)
    assert out.color == "blue" and out.points == 15
    assert colors(rnd) == Counter({"green": 1, "red": 1, "blue": 1})


def test_red_hits_grow_field_3_6_9_12_then_cap(cfg, rng):
    rnd = started(cfg, rng)
    counts = []
    now = T0
    for _ in range(5):
        now += 1.0
        out = throw_at(rnd, "red", now)
        assert out.kind == "hit" and out.color == "red"
        counts.append(len(rnd.targets))
    assert counts == [6, 9, 12, 12, 12]
    assert colors(rnd) == Counter({"green": 4, "blue": 4, "red": 4})


def test_red_hit_at_cap_still_scores(cfg, rng):
    rnd = started(cfg, rng)
    now = T0
    for _ in range(3):
        now += 1.0
        throw_at(rnd, "red", now)
    score = rnd.score
    now += 1.0
    out = throw_at(rnd, "red", now)
    assert out.points > 0 and rnd.score == score + out.points
    assert len(out.spawned) == 1  # only the replacement
    assert len(rnd.targets) == 12


def test_miss_uses_a_ball_and_changes_nothing_else(cfg, rng):
    rnd = started(cfg, rng)
    now = T0 + 1.0
    layout = [(t.id, t.x, t.y) for t in rnd.targets]
    out = throw_away(rnd, now)
    assert out.kind == "miss" and out.points == 0
    assert rnd.score == 0 and rnd.throws == 1 and rnd.throws_left == 9
    assert [(t.id, t.x, t.y) for t in rnd.targets] == layout


def test_combo_multiplier_and_reset_on_miss(cfg, rng):
    rnd = started(cfg, rng)
    now = T0
    points, mults, level_ups = [], [], []
    for _ in range(8):
        now += 1.0
        out = throw_at(rnd, "green", now)
        points.append(out.points)
        mults.append(out.multiplier)
        level_ups.append(out.level_up)
    # levels: after 2 hits x2, after 4 x3, after 6 x4
    assert mults == [1, 1, 2, 2, 3, 3, 4, 4]
    assert points == [10, 10, 20, 20, 30, 30, 40, 40]
    assert level_ups == [False, True, False, True, False, True, False, False]
    assert rnd.best_multiplier == 4 and rnd.best_streak == 8

    now += 1.0
    miss = throw_away(rnd, now)
    assert miss.combo_broken and rnd.streak == 0
    now += 1.0
    out = throw_at(rnd, "green", now)
    assert out.multiplier == 1 and out.points == 10
    assert rnd.score == 200 + 10


def test_combo_can_be_disabled():
    cfg = make_cfg({"combo": {"enabled": False}})
    rnd = started(cfg, np.random.default_rng(3))
    now = T0
    for _ in range(6):
        now += 1.0
        assert throw_at(rnd, "green", now).multiplier == 1


def test_round_ends_after_last_ball(cfg, rng):
    rnd = started(cfg, rng)
    now = T0
    for i in range(10):
        now += 1.0
        assert rnd.register_impact(150.0, 390.0, now, now) is not None
    assert rnd.finished and rnd.end_reason == "balls"
    assert rnd.register_impact(150.0, 390.0, now + 1, now + 1) is None
    assert rnd.throws == 10


def test_round_ends_when_time_runs_out(cfg, rng):
    rnd = started(cfg, rng)
    rnd.update(T0 + 59.99)
    assert rnd.running
    rnd.update(T0 + 60.0)
    assert rnd.finished and rnd.end_reason == "time"
    assert rnd.time_left(T0 + 70) == 0.0
    assert throw_away(rnd, T0 + 60.5) is None


def test_impact_after_time_is_rejected_but_impact_before_time_counts(cfg, rng):
    rnd = started(cfg, rng)
    end = T0 + 60.0
    # Arrives 0.1 s late (processed before update() noticed the end): rejected.
    assert rnd.register_impact(150.0, 390.0, end + 0.1, end + 0.1) is None
    assert rnd.throws == 0
    # Touched the wall just before the end, processed just after: counts.
    tg = next(t for t in rnd.targets if t.color == "green")
    x, y = tg.center_at(end - 0.05)
    out = rnd.register_impact(x, y, end - 0.05, end + 0.02)
    assert out is not None and out.kind == "hit"


def test_operator_missed_throw_counts_as_ball(cfg, rng):
    rnd = started(cfg, rng)
    out = rnd.register_missed_throw(T0 + 1)
    assert out.kind == "miss" and out.operator
    assert rnd.throws == 1 and rnd.score == 0


def test_hit_test_uses_impact_time_for_moving_boxes(cfg, rng):
    rnd = started(cfg, rng)
    tg = next(t for t in rnd.targets if t.color == "red")  # orbit + pulse
    t_hit = T0 + 2.0
    x, y = tg.center_at(t_hit)
    # The event is processed 0.3 s later (sensor + frame latency); the box has moved since.
    out = rnd.register_impact(x, y, t_hit, t_hit + 0.3)
    assert out.kind == "hit" and out.color == "red"


def test_ball_radius_tolerance(cfg, rng):
    rnd = started(cfg, rng)
    tg = next(t for t in rnd.targets if t.color == "green")  # no pulse
    now = T0 + 2.0
    cx, cy = tg.center_at(now)
    half = tg.side_at(now) / 2
    r = cfg.input.ball_radius_cm
    assert tg.contains(cx + half + r - 0.1, cy, now, r)
    assert not tg.contains(cx + half + r + 0.1, cy, now, r)


def test_pulse_never_shrinks_a_box_below_min_size():
    cfg = make_cfg({"targets": {"red": {"size_cm": 21}}, "motion": {"red": {"pulse": 0.3}}})
    rnd = started(cfg, np.random.default_rng(5))
    tg = next(t for t in rnd.targets if t.color == "red")
    sides = [tg.side_at(T0 + 1 + k * 0.01) for k in range(400)]
    assert min(sides) >= cfg.targets.min_size_cm - 1e-6


def test_summary_fields(cfg, rng):
    rnd = started(cfg, rng)
    throw_at(rnd, "red", T0 + 1)
    throw_away(rnd, T0 + 2)
    rnd.update(T0 + 61)
    s = rnd.summary()
    assert s["score"] == 30 and s["throws"] == 2 and s["total_hits"] == 1
    assert s["hits"] == {"green": 0, "blue": 0, "red": 1}
    assert s["end_reason"] == "time" and s["targets_at_end"] == 6
    assert s["duration_s"] == pytest.approx(60.0)


def _overlap(a, b, t):
    (ax, ay), (bx, by) = a.center_at(t), b.center_at(t)
    reach = (a.side_at(t) + b.side_at(t)) / 2
    return abs(ax - bx) < reach - 1e-6 and abs(ay - by) < reach - 1e-6


@pytest.mark.parametrize("seed", range(40))
def test_random_rounds_never_overlap_and_stay_on_the_field(seed):
    """Play random rounds and sample the field after every move settles."""
    cfg = make_cfg()
    rng = np.random.default_rng(seed)
    rnd = started(cfg, rng)
    field = rnd.field
    settle = cfg.layout.transition_s + cfg.layout.spawn_s + 0.05
    now = T0
    while rnd.running:
        for k in range(12):  # samples across a whole motion cycle
            t = now + settle + k * 0.4
            for i, a in enumerate(rnd.targets):
                cx, cy = a.center_at(t)
                half = a.side_at(t) / 2
                assert field.x0 - 1e-6 <= cx - half and cx + half <= field.x1 + 1e-6
                assert field.y0 - 1e-6 <= cy - half and cy + half <= field.y1 + 1e-6
                for b in rnd.targets[i + 1 :]:
                    assert not _overlap(a, b, t), (seed, len(rnd.targets))
        now += settle + 12 * 0.4
        pick = rng.random()
        if pick < 0.6:
            throw_at(rnd, "red", now)
        elif pick < 0.85:
            throw_at(rnd, str(rng.choice(["green", "blue"])), now)
        else:
            throw_away(rnd, now)
