"""A new box and a hit box never look alike: a hit box shatters at once, the new box drops in
a moment later (layout.spawn_delay_s), and it cannot be hit before it is on the wall."""
import numpy as np
import pytest

from market_pulse import config
from market_pulse.game.rules import Round
from market_pulse.game.targets import Motion, Target
from conftest import make_cfg

T0 = 100.0


def still_cfg(**layout):
    return make_cfg({"motion": {"enabled": False}, "layout": layout})


def started(cfg):
    rnd = Round(cfg, np.random.default_rng(3))
    rnd.start(T0)
    return rnd


def settled(rnd):
    return T0 + 2.0


def test_the_new_box_appears_after_the_delay_and_not_before():
    rnd = started(still_cfg(spawn_delay_s=0.4))
    now = settled(rnd)
    tg = rnd.targets[0]
    o = rnd.register_impact(*tg.center_at(now), now, now)
    assert o.kind == "hit" and o.spawned
    new = next(t for t in rnd.targets if t.id == o.spawned[0])
    assert new.born == pytest.approx(now + 0.4)
    x, y = new.center_at(new.born + new.spawn_dur + 0.1)
    assert not new.shown(now + 0.2) and new.side_at(now + 0.2) == 0.0
    assert not new.contains(x, y, now + 0.2, tolerance=30.0)  # not even with the ball's margin
    assert new.contains(x, y, new.born + new.spawn_dur + 0.1)


def test_a_throw_at_the_new_box_before_it_appears_is_a_miss():
    rnd = started(still_cfg(spawn_delay_s=0.4))
    now = settled(rnd)
    o = rnd.register_impact(*rnd.targets[0].center_at(now), now, now)
    new = next(t for t in rnd.targets if t.id == o.spawned[0])
    x, y = new.center_at(new.born + 1.0)
    early = now + 0.1
    others = [t for t in rnd.targets if t is not new and t.contains(x, y, early, 0.0)]
    out = rnd.register_impact(x, y, early, early)
    assert others or out is None or out.kind != "hit" or out.target_id != new.id


def test_no_delay_keeps_the_old_timing():
    rnd = started(still_cfg(spawn_delay_s=0.0))
    now = settled(rnd)
    o = rnd.register_impact(*rnd.targets[0].center_at(now), now, now)
    new = next(t for t in rnd.targets if t.id == o.spawned[0])
    assert new.born == pytest.approx(now)


def test_a_new_box_drops_in_with_a_bounce_and_the_hit_test_follows():
    tg = Target(1, "medium", 30.0, 100.0, 200.0, Motion(pattern="still"), born=10.0, spawn_dur=0.32, drop=14.0)
    assert tg.center_at(10.0)[1] == pytest.approx(200.0 - 14.0)  # above its place at first
    ys = [tg.center_at(10.0 + 0.32 * k / 40)[1] for k in range(41)]
    assert max(ys) > 200.0  # a small bounce past its place
    assert tg.center_at(10.4)[1] == pytest.approx(200.0)  # then it rests there
    assert tg.side_at(10.0) == pytest.approx(30.0)  # at its full size the whole time
    x, y = tg.center_at(10.05)
    assert tg.contains(x, y, 10.05)  # it can be hit where it is drawn
    assert not tg.contains(100.0, 200.0 - 14.0, 9.9)  # nothing before it is born


def test_zero_drop_pops_up_as_before():
    tg = Target(1, "medium", 30.0, 100.0, 200.0, Motion(pattern="still"), born=10.0, spawn_dur=0.32, drop=0.0)
    assert tg.center_at(10.0) == (100.0, 200.0) and tg.side_at(10.0) == pytest.approx(0.0)


def test_the_settings_have_the_delay_and_the_drop():
    assert config.DEFAULTS["layout"]["spawn_delay_s"] == 0.4 and config.DEFAULTS["layout"]["spawn_drop_cm"] > 0
    import copy
    raw = copy.deepcopy(config.DEFAULTS)
    raw["layout"]["spawn_delay_s"] = 99
    warnings: list[str] = []
    config._validate(raw, warnings)
    assert raw["layout"]["spawn_delay_s"] == 3 and any("spawn_delay_s" in w for w in warnings)
