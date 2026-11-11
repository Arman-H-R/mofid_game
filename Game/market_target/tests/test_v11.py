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
        assert rec["lottery_points"] == (3 if score >= 3000 else 2 if score >= 1500 else 1)
        for _ in range(60):  # the results page draws through its whole entrance
            step(app, clock, 0.1)
        assert app.frame_errors == 0
    finally:
        app.shutdown()


def test_the_settings_have_the_sizes_points_and_no_multiplier_or_rush_tabs():
    titles = [title for title, _, _ in S.pages(config.DEFAULTS)]
    assert "امتیاز" in titles and "ضریب‌ها" not in titles and "طوفان بازار" not in titles
    keys = [it.key for _, items, _ in S.pages(config.DEFAULTS) for it in items]
    assert ("targets", "tiny", "points") in keys
    assert ("results", "lottery_two_from") in keys and ("results", "lottery_three_from") in keys


@pytest.mark.parametrize("score,points", [(0, 1), (1499, 1), (1500, 2), (2999, 2), (3000, 3), (99999, 3)])
def test_only_1_2_or_3_lottery_points_by_the_two_scores(score, points):
    assert config.lottery_points(config.DEFAULTS, score) == points


def test_the_two_lottery_scores_can_be_set_and_stay_in_order(tmp_path):
    raw = __import__("copy").deepcopy(config.DEFAULTS)
    raw["results"].update({"lottery_two_from": 800, "lottery_three_from": 2000})
    assert [config.lottery_points(raw, s) for s in (799, 800, 1999, 2000)] == [1, 2, 2, 3]
    raw["results"]["lottery"] = False
    assert config.lottery_points(raw, 5000) == 0
    bad = __import__("copy").deepcopy(config.DEFAULTS)
    bad["results"].update({"lottery_two_from": 2000, "lottery_three_from": 1000})
    warnings: list[str] = []
    config._validate(bad, warnings)
    assert bad["results"]["lottery_three_from"] == 2001 and warnings


def test_typing_the_lottery_scores_on_the_settings_page_keeps_them_in_order(tmp_path):
    cfg = game_cfg({"display": {"resolution": [768, 1024]}, "input": {"sources": []}, "camera": {"enabled": False},
                    "results": {"log_file": str(tmp_path / "r.jsonl")}})
    app = App(cfg, Paths(ROOT), clock_source=FakeTime(), start_inputs=False, settings_file=tmp_path / "settings.json")
    try:
        page = app.settings
        app.open_settings()
        tab = next(i for i, (_, items, _) in enumerate(page.pages) if any(it.key == ("results", "lottery_two_from") for it in items))
        page.select_tab(tab)
        page.draw(app.screen, app.renderer.vp_menu)  # the notes with the three ranges draw
        two = next(it for it in page.items if it.key == ("results", "lottery_two_from"))
        page._set(two, 4000)  # above the score for 3: that one moves along
        raw = app.cfg.raw()
        assert raw["results"]["lottery_two_from"] == 4000 and raw["results"]["lottery_three_from"] == 4001
        assert page.save()
        import json
        saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert saved["results"]["lottery_two_from"] == 4000 and saved["results"]["lottery_three_from"] == 4001
    finally:
        app.shutdown()
