"""Market Rush as a state of the round: trigger (a tiny box hit), duration, exit, speed,
density, score. A medium or small box hit never starts it."""
import numpy as np
import pytest

from market_pulse.game.rules import Round
from market_pulse.game.targets import Motion, Target, TimeWarp
from conftest import game_cfg

T0 = 100.0

# Most of these tests run one long phase, which the settings still allow, because it leaves
# room to check each part. The game as it ships is tested in shipped() below.
ONE_LONG = {"round": {"balls": 0},
            "rush": {"duration_s": 15, "restart_on_tiny_hit": False, "max_per_round": 1, "cooldown_s": 10, "speed_ramp_s": 0.6,
                     "spawn_interval_s": 1.2, "spawn_lifetime_s": 6.0, "transition_s": 0.8}}


def started(overrides=None, seed=11):
    raw = {section: dict(values) for section, values in ONE_LONG.items()}
    for section, values in (overrides or {}).items():
        raw[section] = {**raw.get(section, {}), **values}
    rnd = Round(game_cfg(raw), np.random.default_rng(seed))
    rnd.start(T0)
    return rnd


def shipped(seed=11, **round_over):
    rnd = Round(game_cfg({"round": round_over} if round_over else None), np.random.default_rng(seed))
    rnd.start(T0)
    return rnd


def box(rnd, tier, now, extras_only=False):
    """A box of that size on the wall. The sizes are drawn at random, so if there is none, a
    box that is not tiny (there is always a tiny one, targets.min_tiny) takes that size."""
    pick = [t for t in rnd.targets if (t.rush_extra or not extras_only) and not t.gone(now)]
    tg = next((t for t in pick if t.tier == tier), None)
    if tg is None:
        tg = next((t for t in pick if t.tier != "tiny"), pick[0])
        tg.tier, tg.size = tier, float(rnd.cfg.targets[tier].size_cm)
    return tg


def hit(rnd, tier, now, extras_only=False):
    tg = box(rnd, tier, now, extras_only)
    return rnd.register_impact(*tg.center_at(now), now, now)


def run(rnd, start, end, step=1 / 60):
    t = start
    while t < end:
        t += step
        rnd.update(t)
    return t


def names(rnd):
    return [name for name, _ in rnd.drain_events()]


def start_rush(rnd, now=T0 + 1.0):
    out = hit(rnd, "tiny", now)
    assert rnd.rush is not None
    return out, now


# -- as it ships: 10 touches or 60 seconds, 4 seconds for every tiny-box touch ---------------------
def test_shipped_rules():
    cfg = game_cfg()
    assert cfg.round.balls == 10 and cfg.round.duration_s == 60
    assert cfg.rush.duration_s == 4 and cfg.rush.restart_on_tiny_hit is True


def test_every_tiny_hit_starts_4_seconds():
    rnd = shipped()
    now = T0 + 1.0
    for n in range(1, 4):
        hit(rnd, "tiny", now)
        assert rnd.rush is not None and len(rnd.rushes) == n
        assert rnd.rush.ends_at == pytest.approx(now + 4.0)
        rnd.update(now + 3.99)
        assert rnd.rush is not None
        rnd.update(now + 4.0)
        assert rnd.rush is None
        assert rnd.warp.rate(now + 4.0) == pytest.approx(1.0)
        assert rnd.rush_intensity(now + 4.0) == 0.0
        now += 6.0
    assert names(rnd).count("rush_start") == 3


def test_a_tiny_hit_during_it_gives_a_fresh_4_seconds_without_a_flash():
    rnd = shipped()
    t0 = T0 + 1.0
    hit(rnd, "tiny", t0)
    t1 = t0 + 3.0
    rnd.update(t1)
    rnd.drain_events()
    hit(rnd, "tiny", t1)
    assert names(rnd) == ["rush_again"]
    assert len(rnd.rushes) == 1 and rnd.rush.ends_at == pytest.approx(t1 + 4.0)
    now, seen = t1, []
    while now < t1 + 3.5:
        now += 1 / 60
        rnd.update(now)
        seen.append((rnd.warp.rate(now), rnd.rush_intensity(now)))
    assert all(rate == pytest.approx(rnd.cfg.rush.speed) and shade == 1.0 for rate, shade in seen)  # past the old end
    rnd.update(t1 + 4.0)
    assert rnd.rush is None and rnd.warp.rate(t1 + 4.0) == pytest.approx(1.0)


def test_a_tiny_hit_as_it_fades_out_does_not_jump_the_picture():
    rnd = shipped()
    t0 = T0 + 1.0
    hit(rnd, "tiny", t0)
    t1 = t0 + 3.8
    rnd.update(t1)
    shade = rnd.rush_intensity(t1 - 1e-6)
    assert 0.4 < shade < 0.6
    hit(rnd, "tiny", t1)
    assert rnd.rush_intensity(t1) == pytest.approx(shade, abs=0.01)
    assert rnd.rush_intensity(t1 + 0.4) == 1.0


def test_boxes_do_not_jump_when_it_restarts():
    rnd = shipped()
    t0 = T0 + 1.0
    hit(rnd, "tiny", t0)
    t1 = t0 + 3.8  # already slowing down
    rnd.update(t1)
    tg = next(t for t in rnd.targets if t.tier == "medium")
    before = rnd.warp.at(t1)
    rnd._restart_rush(t1)
    assert rnd.warp.at(t1) == pytest.approx(before)
    assert tg.center_at(t1 + 1e-3)[0] == pytest.approx(tg.center_at(t1)[0], abs=0.1)


def test_extra_boxes_stay_when_it_restarts_and_leave_when_it_ends():
    rnd = shipped()
    t0 = T0 + 1.0
    hit(rnd, "tiny", t0)
    now = run(rnd, t0, t0 + 3.5)
    extras = [t for t in rnd.targets if t.rush_extra]
    assert extras
    hit(rnd, "tiny", now)
    old_end = t0 + 4.0
    run(rnd, now, old_end + 0.5)
    assert rnd.rush is not None and any(t.rush_extra for t in rnd.targets)
    run(rnd, old_end + 0.5, now + 4.5)
    assert rnd.rush is None and not any(t.rush_extra for t in rnd.targets)


def test_the_10th_touch_ends_the_round_hits_and_misses_alike():
    rnd = shipped(ball_limit=True, balls=10)  # with the ball limit on
    now = T0
    for n in range(1, 11):
        now += 1.0
        rnd.update(now)
        if n % 2:
            hit(rnd, "medium", now)
        else:
            rnd.register_impact(150.0, 390.0, now, now)  # touched the wall, no box
        assert rnd.running == (n < 10)
    assert rnd.finished and rnd.end_reason == "balls" and rnd.finished_at == now
    assert rnd.throws == 10
    assert rnd.register_impact(150.0, 200.0, now + 1, now + 1) is None
    assert rnd.throws == 10


def test_the_10th_touch_ends_a_rush_and_starts_none():
    rnd = shipped(ball_limit=True, balls=10)  # with the ball limit on
    now = T0
    for _ in range(9):
        now += 1.0
        rnd.update(now)
        hit(rnd, "medium", now)
    now += 1.0
    rnd.update(now)
    rnd.drain_events()
    hit(rnd, "tiny", now)
    assert rnd.finished and rnd.rushes == [] and "rush_start" not in names(rnd)

    rnd = shipped(ball_limit=True, balls=10)  # with the ball limit on
    now = T0
    for _ in range(10):
        now += 0.5
        rnd.update(now)
        hit(rnd, "tiny", now)
    assert rnd.finished and rnd.end_reason == "balls" and rnd.rush is None
    assert rnd.rushes[-1].ended_at == now


def test_no_ball_limit_as_it_ships_only_the_time_ends_the_round():
    rnd = shipped()
    assert rnd.balls == 0 and rnd.timed
    now = T0
    for _ in range(25):  # far past the old 10 balls
        now += 1.0
        rnd.update(now)
        rnd.register_impact(150.0, 390.0, now, now)
    assert rnd.running and rnd.throws == 25
    run(rnd, now, T0 + 60.1)
    assert rnd.end_reason == "time"


def test_balls_only_no_time_limit():
    rnd = shipped(time_limit=False, ball_limit=True, balls=3)
    assert not rnd.timed and rnd.time_left(T0 + 500) == float("inf")
    run(rnd, T0, T0 + 200)  # minutes pass: no end without the time limit
    assert rnd.running
    for k in range(3):
        rnd.register_impact(150.0, 390.0, T0 + 201 + k, T0 + 201 + k)
    assert rnd.end_reason == "balls" and rnd.summary()["throws"] == 3


def test_60_seconds_end_the_round_with_touches_left():
    rnd = shipped()
    hit(rnd, "medium", T0 + 1)
    run(rnd, T0 + 1, T0 + 60.1)
    assert rnd.finished and rnd.end_reason == "time" and rnd.finished_at == pytest.approx(T0 + 60.0)


def test_tiny_on_the_wall_does_not_start_it():
    rnd = started()
    run(rnd, T0, T0 + 10)
    assert rnd.rush is None and rnd.rushes == []


def test_medium_and_small_hits_do_not_start_it():
    rnd = started()
    hit(rnd, "medium", T0 + 1)
    hit(rnd, "small", T0 + 2)
    assert rnd.rush is None


def test_a_tiny_hit_starts_it_and_it_lasts_exactly_15_seconds():
    rnd = started()
    _, t0 = start_rush(rnd)
    assert names(rnd) == ["rush_start"]
    rnd.update(t0 + 14.99)
    assert rnd.rush is not None
    rnd.update(t0 + 15.0)
    assert rnd.rush is None
    rush = rnd.rushes[0]
    assert rush.ended_at - rush.started_at == pytest.approx(15.0)
    assert "rush_end" in names(rnd)


def test_tiny_hits_during_it_neither_restart_nor_extend_it():
    rnd = started()
    _, t0 = start_rush(rnd)
    end = rnd.rush.ends_at
    now = t0
    for _ in range(6):
        now += 2.0
        rnd.update(now)
        hit(rnd, "tiny", now)
    assert len(rnd.rushes) == 1 and rnd.rush.ends_at == end
    assert names(rnd).count("rush_start") == 1


def test_it_does_not_come_back_after_it_ends_by_default():
    rnd = started()
    _, t0 = start_rush(rnd)
    now = run(rnd, t0, t0 + 16)
    for _ in range(5):
        now += 1.0
        rnd.update(now)
        hit(rnd, "tiny", now)
    assert len(rnd.rushes) == 1 and rnd.rush is None


def test_a_second_one_needs_the_setting_the_cooldown_and_new_tiny_hits():
    rnd = started({"rush": {"max_per_round": 2, "cooldown_s": 5, "trigger_tiny_hits": 2}})
    hit(rnd, "tiny", T0 + 1)
    assert rnd.rush is None  # one of two
    hit(rnd, "tiny", T0 + 2)
    t0 = T0 + 2
    assert rnd.rush is not None
    now = run(rnd, t0, t0 + 15.5)
    assert rnd.rush is None
    hit(rnd, "tiny", now)
    hit(rnd, "tiny", now + 0.5)
    assert len(rnd.rushes) == 1  # still cooling down: those hits did not count
    now = run(rnd, now, t0 + 21)
    hit(rnd, "tiny", now)
    assert rnd.rush is None  # one of two again
    hit(rnd, "tiny", now + 0.5)
    assert len(rnd.rushes) == 2 and rnd.rush is not None


def test_the_round_still_ends_at_60_seconds_during_a_rush():
    rnd = started()
    t0 = T0 + 55.0
    run(rnd, T0, t0)
    hit(rnd, "tiny", t0)
    assert rnd.rush.ends_at == pytest.approx(T0 + 60.0)  # cut to what is left of the round
    rnd.drain_events()
    run(rnd, t0, T0 + 60.5)
    assert rnd.finished and rnd.end_reason == "time"
    assert rnd.finished_at == pytest.approx(T0 + 60.0)
    assert rnd.rush is None and rnd.rushes[0].ended_at == pytest.approx(T0 + 60.0)


def test_nothing_spawns_or_scores_after_the_round_ends():
    rnd = started()
    t0 = T0 + 55.0
    run(rnd, T0, t0)
    hit(rnd, "tiny", t0)
    run(rnd, t0, T0 + 60.5)
    rnd.drain_events()
    count, score = len(rnd.targets), rnd.score
    run(rnd, T0 + 60.5, T0 + 70)
    tg = rnd.targets[0]
    assert rnd.register_impact(*tg.center_at(T0 + 70), T0 + 70, T0 + 70) is None
    assert rnd.register_missed_throw(T0 + 70) is None
    assert len(rnd.targets) == count and rnd.score == score and rnd.drain_events() == []


def test_the_multiplier_applies_once_and_only_inside_the_phase():
    rnd = started()
    trigger, t0 = start_rush(rnd)
    assert trigger.rush_multiplier == 1 and trigger.points == 30  # the hit that started it is not multiplied
    rnd2 = started({"combo": {"enabled": False}})
    start_rush(rnd2)
    out = hit(rnd2, "medium", t0 + 1)
    assert (out.base, out.rush_multiplier, out.multiplier, out.points) == (10, 2, 2, 20)
    assert rnd2.score == 30 + 20
    run(rnd2, t0 + 1, t0 + 15.1)
    assert rnd2.rush is None
    out = hit(rnd2, "medium", t0 + 15.2)
    assert out.rush_multiplier == 1 and out.points == 10


def test_it_combines_with_the_combo_without_doubling_twice():
    rnd = started()
    _, t0 = start_rush(rnd)  # streak 1
    hit(rnd, "small", t0 + 0.5)  # streak 2: combo x1, rush x2
    out = hit(rnd, "medium", t0 + 1.0)  # combo x2, rush x2
    assert (out.combo_multiplier, out.rush_multiplier, out.multiplier, out.points) == (2, 2, 4, 40)
    assert rnd.score == 30 + 30 + 40 == sum(rnd.points_by_tier.values())
    assert rnd.best_multiplier == 4


def test_not_stacking_uses_the_larger_multiplier():
    rnd = started({"rush": {"stack_with_combo": False}})
    _, t0 = start_rush(rnd)
    hit(rnd, "small", t0 + 0.5)
    assert hit(rnd, "medium", t0 + 1.0).multiplier == 2


def test_score_uses_the_time_the_ball_touched_the_wall():
    rnd = started({"combo": {"enabled": False}})
    _, t0 = start_rush(rnd)
    end = rnd.rush.ends_at
    tg = next(t for t in rnd.targets if t.tier == "medium" and not t.rush_extra)
    before = end - 0.05
    rnd.update(before)
    out = rnd.register_impact(*tg.center_at(before), before, end + 0.03)  # touched in time, processed late
    assert out.rush_multiplier == 2


def test_speed_ramps_up_and_is_exactly_normal_when_it_ends():
    rnd = started()
    _, t0 = start_rush(rnd)
    cfg = rnd.cfg.rush
    assert rnd.warp.rate(t0 - 0.1) == 1.0
    assert 1.0 < rnd.warp.rate(t0 + cfg.speed_ramp_s / 2) < cfg.speed
    assert rnd.warp.rate(t0 + 5.0) == pytest.approx(cfg.speed)
    assert rnd.warp.rate(t0 + 15.0) == pytest.approx(1.0)
    assert rnd.warp.rate(t0 + 40.0) == pytest.approx(1.0)


def test_boxes_do_not_jump_when_the_speed_changes():
    rnd = started()
    _, t0 = start_rush(rnd)
    now = t0 + rnd.cfg.layout.transition_s + 0.01  # after the move every hit causes
    tg = next(t for t in rnd.targets if t.tier == "medium" and not t.rush_extra)
    prev = tg.center_at(now)
    moved = 0.0
    while now < t0 + 16.5:
        now += 1 / 240
        cur = tg.center_at(now)
        moved = max(moved, np.hypot(cur[0] - prev[0], cur[1] - prev[1]))
        prev = cur
    assert moved < 0.3  # cm in 1/240 s: no jump at either end of the phase


def test_boxes_really_move_faster_during_it():
    warp = TimeWarp()
    warp.ramp(10.0, 2.0, 0.0)
    warp.ramp(20.0, 1.0, 0.0)
    assert warp.at(5.0) == 5.0
    assert warp.at(15.0) == pytest.approx(10.0 + 5.0 * 2.0)
    assert warp.at(25.0) == pytest.approx(10.0 + 20.0 + 5.0)
    tg = Target(1, "tiny", 25.0, 0.0, 0.0, Motion("sway_x", 10.0, 4.0), born=0.0, warp=warp)
    plain = Target(1, "tiny", 25.0, 0.0, 0.0, Motion("sway_x", 10.0, 4.0), born=0.0)
    assert tg.center_at(5.0) == plain.center_at(5.0)
    assert tg.center_at(15.0) == plain.center_at(20.0)


def test_more_boxes_appear_during_it_and_the_field_returns_afterwards():
    rnd = started()
    _, t0 = start_rush(rnd)
    baseline = len(rnd.targets)
    assert baseline == rnd.cfg.targets.active == 4  # a tiny hit replaces its box; no more boxes outside the rush
    run(rnd, t0, t0 + 7.5)
    assert len(rnd.targets) > baseline
    assert len(rnd.targets) <= rnd.cfg.rush.max_targets
    assert any(t.rush_extra for t in rnd.targets)
    assert "rush_spawn" in names(rnd)
    run(rnd, t0 + 7.5, t0 + 15.5)
    assert rnd.rush is None and len(rnd.targets) == baseline
    assert not any(t.rush_extra for t in rnd.targets)


def test_timed_boxes_do_not_move_the_others():
    rnd = started()
    _, t0 = start_rush(rnd)
    before = {t.id: (t.x, t.y) for t in rnd.targets}
    run(rnd, t0, t0 + 3.5)
    assert len(rnd.targets) > len(before)
    for t in rnd.targets:
        if t.id in before:
            assert (t.x, t.y) == before[t.id]


def test_extra_boxes_can_be_hit_and_a_hit_on_one_keeps_the_field_size():
    rnd = started()
    _, t0 = start_rush(rnd)
    now = run(rnd, t0, t0 + 3.0)
    color = next(t.tier for t in rnd.targets if t.rush_extra)
    n = len(rnd.targets)
    out = hit(rnd, color, now, extras_only=True)
    assert out.kind == "hit"
    assert len(rnd.targets) == n  # an extra box hit is replaced by another extra box
    run(rnd, now, t0 + 15.5)
    assert not any(t.rush_extra for t in rnd.targets)


@pytest.mark.parametrize("seed", range(12))
def test_random_rush_rounds_never_overlap_and_stay_on_the_field(seed):
    rng = np.random.default_rng(seed)
    rnd = Round(game_cfg({"rush": {"max_per_round": 3, "cooldown_s": 1}}), rng)
    rnd.start(T0)
    cfg = rnd.cfg
    settle = cfg.layout.transition_s + cfg.layout.spawn_s + 0.05
    now = T0
    while rnd.running:
        now += settle
        rnd.update(now)
        for k in range(8):
            t = now + k * 0.2
            rnd.update(t)
            if not rnd.running:
                break
            live = [a for a in rnd.targets
                    if t >= a.born + a.spawn_dur and (a.die_at is None or t < a.die_at)]
            for i, a in enumerate(live):
                ax, ay = a.center_at(t)
                half = a.side_at(t) / 2
                assert rnd.field.x0 - 1e-6 <= ax - half and ax + half <= rnd.field.x1 + 1e-6
                assert rnd.field.y0 - 1e-6 <= ay - half and ay + half <= rnd.field.y1 + 1e-6
                for b in live[i + 1:]:
                    bx, by = b.center_at(t)
                    reach = (a.side_at(t) + b.side_at(t)) / 2
                    assert not (abs(ax - bx) < reach - 1e-6 and abs(ay - by) < reach - 1e-6), (seed, len(rnd.targets))
        now += 8 * 0.2
        if rnd.running:
            pick = rng.random()
            color = "tiny" if pick < 0.5 else str(rng.choice(["medium", "small"]))
            hit(rnd, color, now)
    assert rnd.finished


def test_it_can_be_switched_off():
    rnd = started({"rush": {"enabled": False}})
    hit(rnd, "tiny", T0 + 1)
    assert rnd.rush is None and rnd.rushes == [] and rnd.warp.rate(T0 + 5) == 1.0


def test_one_round_has_one_timer():
    rnd = started()
    rnd.start(T0 + 30)
    assert rnd.started_at == T0 and rnd.ends_at == T0 + 60.0


def test_summary_records_the_phase():
    rnd = started({"combo": {"enabled": False}})
    _, t0 = start_rush(rnd)
    hit(rnd, "medium", t0 + 1)
    rnd.register_impact(150.0, 390.0, t0 + 2, t0 + 2)
    s = rnd.summary()
    assert s["rush"] == {"count": 1, "throws": 2, "hits": 1, "points": 20}
    assert s["points_by_tier"] == {"medium": 20, "small": 0, "tiny": 30}
