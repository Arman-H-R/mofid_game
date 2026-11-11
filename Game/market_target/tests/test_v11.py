"""MOFID_UI v11 («چالش تعاملی / پرتاب هدفمند»): a simple game of aiming at the boxes. The box's
size sets the points (+100 / +150 / +300), any product at any size, no multiplier, no Market Rush,
every box moves after a hit, and the results show the lottery points, the hits and today's rank."""
import numpy as np
import pygame
import pytest

from market_pulse import config
from market_pulse.app import App, Paths
from market_pulse.game import flow as F
from market_pulse.game.rules import Round
from market_pulse.ui import settings as S
from conftest import ROOT, game_cfg
from test_app import FakeTime, sensor_hit, step

T0 = 100.0


def still(**more):
    return game_cfg({"motion": {"enabled": False}, **more})


def started(cfg, seed=4):
    rnd = Round(cfg, np.random.default_rng(seed))
    rnd.start(T0)
    return rnd


def test_the_game_has_its_new_name_and_no_multiplier_or_market_rush():
    d = config.DEFAULTS
    assert d["text"]["title"] == "پرتاب هدفمند" and d["text"]["subtitle"] == "چالش تعاملی"
    assert d["web"]["title"] == "پرتاب هدفمند"
    assert d["combo"]["enabled"] is False and d["rush"]["enabled"] is False
    assert d["layout"]["move_on_hit"] is True and d["targets"]["points_by_size"] is True


@pytest.mark.parametrize("tier,points", [("medium", 100), ("small", 150), ("tiny", 300)])
def test_the_size_sets_the_points_and_hits_never_multiply(tier, points):
    weights = {t: {"weight": int(t == tier)} for t in ("medium", "small", "tiny")}
    rnd = started(still(targets={**weights, "min_tiny": 0}))
    now = T0 + 2.0
    for k in range(6):  # six hits in a row: the same points every time
        tg = next(t for t in rnd.targets if t.shown(now))
        o = rnd.register_impact(*tg.center_at(now), now, now)
        assert o.kind == "hit" and o.points == points and o.multiplier == 1
        now += 1.5
    assert rnd.score == 6 * points and rnd.rush is None and not rnd.rushes


def test_any_product_shows_at_any_size():
    rnd = started(still(), seed=1)
    seen: dict[str, set] = {}
    now = T0 + 2.0
    for _ in range(80):
        for t in rnd.targets:
            seen.setdefault(t.product, set()).add(t.tier)
        tg = next(t for t in rnd.targets if t.shown(now))
        rnd.register_impact(*tg.center_at(now), now, now)
        now += 1.5
    assert any(len(tiers) > 1 for tiers in seen.values())  # a product is not tied to one size


def test_after_a_hit_every_box_moves():
    rnd = started(still())
    now = T0 + 2.0
    others = [t for t in rnd.targets]
    hit = others.pop(0)
    before = {t.id: t.center_at(now) for t in others}
    rnd.register_impact(*hit.center_at(now), now, now)
    later = now + 2.0
    moved = [t for t in others if t in rnd.targets and t.center_at(later) != before[t.id]]
    assert len(moved) == len(others)


def test_the_round_records_its_lottery_points(tmp_path):
    cfg = game_cfg({"display": {"resolution": [450, 600]}, "input": {"sources": []}, "camera": {"enabled": False},
                    "results": {"log_file": str(tmp_path / "r.jsonl")}, "flow": {"registration": False, "intro_s": 0}})
    clock = FakeTime()
    app = App(cfg, Paths(ROOT), clock_source=clock, start_inputs=False)
    try:
        app.flow.start_for("آرمان", app.clock.now())
        step(app, clock, app.cfg.flow.countdown_s + 0.5)
        rnd = app.flow.round
        while rnd.score < 2400:  # a few hits: well past 2000 points
            now = app.clock.now()
            tg = next(t for t in rnd.targets if t.shown(now))
            sensor_hit(app, clock, *tg.center_at(now))
            step(app, clock, 1.0)
        score = rnd.score
        step(app, clock, rnd.time_left(app.clock.now()) + app.cfg.round.end_delay_s + 0.5)
        assert app.flow.state == F.RESULTS
        rec = app.flow.summary
        assert rec["lottery_points"] == score // 1000 * 2
        for _ in range(60):  # the results page draws through its whole entrance
            step(app, clock, 0.1)
        assert app.frame_errors == 0
    finally:
        app.shutdown()


def test_the_settings_have_the_sizes_points_and_no_multiplier_or_rush_tabs():
    titles = [title for title, _, _ in S.pages(config.DEFAULTS)]
    assert "امتیاز" in titles and "ضریب‌ها" not in titles and "طوفان بازار" not in titles
    keys = [it.key for _, items, _ in S.pages(config.DEFAULTS) for it in items]
    assert ("targets", "tiny", "points") in keys and ("results", "lottery_per_1000") in keys
