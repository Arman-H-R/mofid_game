"""The rules for one round (pure logic, no pygame): the 10 products at three sizes."""
import copy
from collections import Counter

import numpy as np
import pytest

from market_pulse import config
from market_pulse.config import PRODUCTS, TIERS
from market_pulse.game.rules import Round
from conftest import make_cfg

T0 = 100.0
ONLY = {tier: {"medium": {"weight": int(tier == "medium")}, "small": {"weight": int(tier == "small")},
               "tiny": {"weight": int(tier == "tiny")}, "min_tiny": 0} for tier in TIERS}


def only(tier, **more):
    """Settings where every new box has this size."""
    targets = {**ONLY[tier], **more.pop("targets", {})}
    return make_cfg({"targets": targets, **more})


def started(cfg, rng):
    rnd = Round(cfg, rng)
    rnd.start(T0)
    return rnd


def throw_at(rnd, tier, now):
    """Throw a ball at the centre of a box of ``tier`` (any box for None, or when there is none
    of that size) as it is drawn at ``now``."""
    tg = next((t for t in rnd.targets if tier is None or t.tier == tier), rnd.targets[0])
    x, y = tg.center_at(now)
    return rnd.register_impact(x, y, now, now)


def throw_away(rnd, now):
    """Throw a ball where no box is (the footer strip, below the play field)."""
    return rnd.register_impact(150.0, 390.0, now, now)


def products(rnd):
    return [t.product for t in rnd.targets]


def test_round_starts_with_the_configured_number_of_different_products(cfg, rng):
    assert cfg.targets.active == 4  # the default
    rnd = started(cfg, rng)
    assert len(rnd.targets) == 4
    assert len(set(products(rnd))) == 4
    assert all(t.tier in TIERS for t in rnd.targets)
    assert rnd.throws_left == 10
    assert rnd.time_left(T0) == 60.0


@pytest.mark.parametrize("active", [1, 2, 3, 6, 9, 10])
def test_any_number_of_active_boxes_up_to_every_product(active):
    cfg = make_cfg({"targets": {"active": active}})
    rnd = started(cfg, np.random.default_rng(active))
    assert len(rnd.targets) == active and len(set(products(rnd))) == active
    now = T0
    for _ in range(8):
        now += 1.0
        out = throw_at(rnd, None, now)
        assert out.kind == "hit"
        assert len(rnd.targets) == active  # replaced at once: the number never changes
        assert len(set(products(rnd))) == active  # never the same product twice on the wall


def test_active_cannot_exceed_the_products():
    raw = copy.deepcopy(config.DEFAULTS)
    raw["products"] = raw["products"][:5]
    raw["targets"]["active"] = 9
    warnings = []
    config._validate(raw, warnings)
    assert raw["targets"]["active"] == 5 and warnings


def test_after_a_hit_the_other_boxes_stay_and_the_new_one_takes_a_free_spot(cfg, rng):
    """As in the reference clip: nothing moves away; only the new box appears."""
    assert cfg.layout.move_on_hit is False
    rnd = started(cfg, rng)
    now = T0 + 1.0
    before = {t.id: (t.x, t.y) for t in rnd.targets}
    hit = rnd.targets[0]
    out = rnd.register_impact(*hit.center_at(now), now, now)
    assert out.kind == "hit" and len(out.spawned) == 1
    for t in rnd.targets:
        if t.id in before:
            assert (t.x, t.y) == before[t.id] and t.move_dur == 0.0
    new = next(t for t in rnd.targets if t.id == out.spawned[0])
    for t in rnd.targets:  # the new box covers none of the others
        if t is not new:
            gap = (t.max_side() + new.max_side()) / 2
            assert abs(t.x - new.x) >= gap or abs(t.y - new.y) >= gap * max(t.aspect, new.aspect)


def test_a_hit_replaces_the_box_with_another_product_and_moves_all(rng):
    cfg = make_cfg({"layout": {"move_on_hit": True}})
    rnd = started(cfg, rng)
    now = T0 + 1.0
    before = {t.id: t.center_at(now) for t in rnd.targets}
    hit = rnd.targets[0]
    out = rnd.register_impact(*hit.center_at(now), now, now)
    assert out.kind == "hit" and out.product == hit.product and out.tier == hit.tier
    assert hit.id not in {t.id for t in rnd.targets}
    assert len(out.spawned) == 1 and out.spawned[0] not in before
    new = next(t for t in rnd.targets if t.id == out.spawned[0])
    assert new.product != hit.product
    for t in rnd.targets:
        if t.id in before:  # every remaining box glides to a new spot at least min_move away
            old = before[t.id]
            assert np.hypot(t.x - old[0], t.y - old[1]) >= cfg.layout.min_move_cm


@pytest.mark.parametrize("tier,points", [("medium", 10), ("small", 15), ("tiny", 30)])
def test_smaller_boxes_score_more(tier, points):
    rnd = started(only(tier), np.random.default_rng(2))
    out = throw_at(rnd, tier, T0 + 1)
    assert out.tier == tier and out.points == points
    assert out.base == points


def test_sizes_are_visibly_different_and_tiny_pays_most(cfg):
    t = cfg.targets
    assert t.medium.size_cm > t.small.size_cm > t.tiny.size_cm
    assert t.medium.points < t.small.points < t.tiny.points
    assert t.tiny.points >= 2 * t.medium.points


def test_a_tiny_box_is_always_on_the_wall(cfg):
    rnd = started(cfg, np.random.default_rng(9))
    now = T0
    for _ in range(40):
        assert any(t.tier == "tiny" for t in rnd.targets)
        now += 1.0
        throw_at(rnd, None, now)
        if rnd.finished:  # 10 balls: start again
            rnd = started(cfg, np.random.default_rng(int(now)))


def test_the_sizes_are_mixed():
    cfg = make_cfg({"round": {"balls": 0}, "targets": {"min_tiny": 0}})
    rnd = started(cfg, np.random.default_rng(4))
    seen = Counter(t.tier for t in rnd.targets)
    now = T0
    for _ in range(60):
        now += 1.0
        out = throw_at(rnd, None, now)
        seen[next(t.tier for t in rnd.targets if t.id == out.spawned[0])] += 1
    assert all(seen[t] >= 10 for t in TIERS), seen  # weights 3 : 3 : 2


def test_every_product_comes_up_before_any_repeats_much():
    cfg = make_cfg({"round": {"balls": 0}})
    rnd = started(cfg, np.random.default_rng(11))
    shown = Counter(products(rnd))
    now = T0
    for _ in range(26):
        now += 1.0
        out = throw_at(rnd, None, now)
        shown[next(t.product for t in rnd.targets if t.id == out.spawned[0])] += 1
    assert set(shown) == {p["key"] for p in PRODUCTS}  # all 10
    assert max(shown.values()) <= 4  # a shuffled bag, not the same few again and again


def test_miss_uses_a_ball_and_changes_nothing_else(cfg, rng):
    rnd = started(cfg, rng)
    now = T0 + 1.0
    layout = [(t.id, t.x, t.y) for t in rnd.targets]
    out = throw_away(rnd, now)
    assert out.kind == "miss" and out.points == 0
    assert rnd.score == 0 and rnd.throws == 1 and rnd.throws_left == 9
    assert [(t.id, t.x, t.y) for t in rnd.targets] == layout


def test_combo_multiplier_and_reset_on_miss():
    rnd = started(only("medium"), np.random.default_rng(1234))
    now = T0
    points, mults, level_ups = [], [], []
    for _ in range(8):
        now += 1.0
        out = throw_at(rnd, "medium", now)
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
    out = throw_at(rnd, "medium", now)
    assert out.multiplier == 1 and out.points == 10
    assert rnd.score == 200 + 10


def test_combo_can_be_disabled():
    cfg = only("medium", combo={"enabled": False})
    rnd = started(cfg, np.random.default_rng(3))
    now = T0
    for _ in range(6):
        now += 1.0
        assert throw_at(rnd, "medium", now).multiplier == 1


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
    tg = rnd.targets[0]
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
    tg = next(t for t in rnd.targets if t.tier == "tiny")  # irregular motion + pulse
    t_hit = T0 + 2.0
    x, y = tg.center_at(t_hit)
    # The event is processed 0.3 s later (sensor + frame latency); the box has moved since.
    out = rnd.register_impact(x, y, t_hit, t_hit + 0.3)
    assert out.kind == "hit" and out.tier == "tiny" and out.product == tg.product


def test_the_hit_area_is_the_tile_itself():
    """Tiles are a little wider than tall (MOFID_UI v4.1): the hit area follows that shape."""
    cfg = only("medium", input={"ball_radius_cm": 0})
    rnd = started(cfg, np.random.default_rng(6))
    tg = rnd.targets[0]
    now = T0 + 2.0
    cx, cy = tg.center_at(now)
    side = tg.side_at(now)
    assert tg.aspect == pytest.approx(0.92)
    assert tg.contains(cx + side / 2 - 0.1, cy, now)
    assert not tg.contains(cx + side / 2 + 0.1, cy, now)
    assert tg.contains(cx, cy + side * tg.aspect / 2 - 0.1, now)
    assert not tg.contains(cx, cy + side * tg.aspect / 2 + 0.1, now)  # inside a square, outside the tile


def test_ball_radius_tolerance(cfg, rng):
    rnd = started(cfg, rng)
    tg = next(t for t in rnd.targets if t.motion.pulse == 0)
    now = T0 + 2.0
    cx, cy = tg.center_at(now)
    half = tg.side_at(now) / 2
    r = cfg.input.ball_radius_cm
    assert tg.contains(cx + half + r - 0.1, cy, now, r)
    assert not tg.contains(cx + half + r + 0.1, cy, now, r)


def test_pulse_never_shrinks_a_box_below_min_size():
    cfg = only("tiny", targets={"tiny": {"size_cm": 21}}, motion={"tiny": {"pulse": 0.3}})
    rnd = started(cfg, np.random.default_rng(5))
    tg = rnd.targets[0]
    sides = [tg.side_at(T0 + 1 + k * 0.01) for k in range(400)]
    assert min(sides) >= cfg.targets.min_size_cm - 1e-6


def test_every_box_moves_a_little_differently():
    rnd = started(only("medium", targets={"active": 6}), np.random.default_rng(8))
    periods = {round(t.motion.period, 3) for t in rnd.targets}
    reach = {round(t.motion.amplitude, 3) for t in rnd.targets}
    assert len(periods) == 6 and len(reach) == 6
    base = rnd.cfg.motion.medium
    v = rnd.cfg.motion.variation
    assert all(base.period_s * (1 - v) <= t.motion.period <= base.period_s * (1 + v) for t in rnd.targets)


def test_summary_fields():
    rnd = started(only("tiny"), np.random.default_rng(1))
    hit = rnd.targets[0].product
    throw_at(rnd, "tiny", T0 + 1)
    throw_away(rnd, T0 + 2)
    rnd.update(T0 + 61)
    s = rnd.summary()
    assert s["score"] == 30 and s["throws"] == 2 and s["total_hits"] == 1
    assert s["hits"] == {"medium": 0, "small": 0, "tiny": 1}
    assert s["points_by_tier"] == {"medium": 0, "small": 0, "tiny": 30}
    assert s["products"] == {hit: 1}
    assert s["product_points"] == {hit: 30}  # the results' bars: points per product
    assert s["end_reason"] == "time" and s["targets_at_end"] == 4
    assert s["duration_s"] == pytest.approx(60.0)


def _overlap(a, b, t):
    (ax, ay), (bx, by) = a.center_at(t), b.center_at(t)
    rx = (a.side_at(t) + b.side_at(t)) / 2
    ry = (a.side_at(t) * a.aspect + b.side_at(t) * b.aspect) / 2
    return abs(ax - bx) < rx - 1e-6 and abs(ay - by) < ry - 1e-6


def check_field(rnd, t, seed=None):
    field = rnd.field
    for i, a in enumerate(rnd.targets):
        cx, cy = a.center_at(t)
        hx, hy = a.side_at(t) / 2, a.side_at(t) * a.aspect / 2
        assert field.x0 - 1e-6 <= cx - hx and cx + hx <= field.x1 + 1e-6
        assert field.y0 - 1e-6 <= cy - hy and cy + hy <= field.y1 + 1e-6
        for b in rnd.targets[i + 1:]:
            assert not _overlap(a, b, t), (seed, len(rnd.targets))


@pytest.mark.parametrize("seed", range(40))
def test_random_rounds_never_overlap_and_stay_on_the_field(seed):
    """Play random rounds and sample the field after every move settles."""
    rng = np.random.default_rng(seed)
    cfg = make_cfg({"targets": {"active": int(rng.integers(1, len(PRODUCTS) + 1))}})
    rnd = started(cfg, rng)
    settle = cfg.layout.transition_s + cfg.layout.spawn_s + 0.05
    now = T0
    while rnd.running:
        for k in range(12):  # samples across a whole motion cycle
            check_field(rnd, now + settle + k * 0.4, seed)
        assert len(rnd.targets) == cfg.targets.active
        now += settle + 12 * 0.4
        if rng.random() < 0.85:
            throw_at(rnd, None if rng.random() < 0.5 else "tiny", now)
        else:
            throw_away(rnd, now)
