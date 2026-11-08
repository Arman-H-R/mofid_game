import math
"""The redesign's idle loop (ui/attract.py) and the hit feedback: the product's name and points."""
import pygame
import pytest

from market_pulse.app import App, Paths
from market_pulse.game import flow as F
from conftest import ROOT, game_cfg
from test_app import FakeTime, sensor_hit, step

BOARD = [{"player_name": "سارا", "score": 260, "timestamp": 1}, {"player_name": "Ali", "score": 150, "timestamp": 2}]


@pytest.fixture
def app(tmp_path):
    cfg = game_cfg({"display": {"resolution": [600, 800]}, "input": {"sources": []}, "camera": {"enabled": False},
                    "results": {"log_file": str(tmp_path / "r.jsonl")}})
    clock = FakeTime()
    app = App(cfg, Paths(ROOT), clock_source=clock, start_inputs=False)
    yield app, clock
    app.shutdown()


def test_the_loop_runs_title_demo_and_board(app):
    app, _ = app
    a = app.renderer.attract
    panel, demo = app.cfg.flow.attract_panel_s, app.cfg.flow.attract_demo_s
    assert [s for s, _ in a.scenes(False)] == ["title", "demo"]
    assert [s for s, _ in a.scenes(True)] == ["title", "demo", "board"]
    total = 2 * panel + demo
    assert a.scene_at(0.5, True)[0] == "title"
    assert a.scene_at(panel + 0.5, True)[:2] == ("demo", pytest.approx(0.5))
    assert a.scene_at(panel + demo + 0.5, True)[0] == "board"
    assert a.scene_at(total + 0.5, True)[0] == "title"  # and round again
    assert a.scene_at(panel + demo + 0.5, False)[0] == "title"  # nobody has played today: no board
    t = a.start_of("demo", 1000.0, True)
    assert t >= 1000.0 and a.scene_at(t + 0.01, True)[0] == "demo"


def test_the_demo_shows_a_hit_a_new_product_the_sizes_and_market_rush(app):
    app, _ = app
    a = app.renderer.attract
    assert a.hit1.scale(a.HIT1 - 0.1) > 0.9 and a.hit1.scale(a.HIT1 + 0.3) == 0.0  # the hit box goes
    new = next(t for t in a.tiles if t.born > a.NEW)
    assert new.scale(a.NEW) == 0.0 and new.scale(a.NEW + 0.6) == pytest.approx(1.0, abs=0.05)  # a new one comes
    assert a.tiny.tier == "tiny" and a.tiny.scale(a.HIT2 + 0.3) == 0.0
    assert a.HIT1 < a.NEW < a.SIZES < a.HIT2 < a.CALM < app.cfg.flow.attract_demo_s
    assert {t.tier for t in a.tiles} == {"medium", "small", "tiny"}
    assert any(t.extra for t in a.tiles)  # Market Rush brings more boxes


def test_every_moment_of_the_loop_draws_and_never_touches_the_game(app):
    app, clock = app
    app.board = BOARD
    total = sum(d for _, d in app.renderer.attract.scenes(True))
    t, end = clock.t, clock.t + total + 1
    while t < end:  # the whole loop at 10 frames a second
        clock.t = t = t + 0.1
        app.frame()
        app.board = BOARD
        assert app.flow.state == F.ATTRACT and app.flow.round is None
    assert app.frame_errors == 0 and app.flow.drain_events() == []


def test_a_moment_of_the_loop_always_looks_the_same(app):
    app, clock = app
    a = app.renderer.attract
    surf = app.screen
    shots = []
    for _ in range(2):
        a._scene = "demo"
        surf.blit(app.renderer.bg_dark, (0, 0))
        with app.renderer.menu():
            a._demo(surf, a.HIT1 + 0.2, app.cfg.flow.attract_demo_s)
        shots.append(pygame.image.tobytes(surf, "RGB"))
    assert shots[0] == shots[1]


def test_starting_a_game_leaves_the_loop_and_it_comes_back_after(app):
    app, clock = app
    step(app, clock, 1.0)
    sb = app.cfg.layout.start_button
    sensor_hit(app, clock, sb.x_cm, sb.y_cm)  # the start box, as on the attract screen
    step(app, clock, 0.2)
    assert app.flow.state == F.REGISTER
    app.flow.reset(app.clock.now())
    step(app, clock, 0.2)
    assert app.flow.state == F.ATTRACT and app.frame_errors == 0


def test_a_hit_shows_the_products_name_and_its_points(app):
    app, clock = app
    app.flow.start_for("سارا", app.clock.now())
    step(app, clock, app.cfg.flow.intro_s + app.cfg.flow.countdown_s + 0.3)
    rnd = app.flow.round
    tg = rnd.targets[0]
    name = next(p["name"] for p in app.cfg.raw()["products"] if p["key"] == tg.product)
    sensor_hit(app, clock, *tg.center_at(app.clock.now()))
    step(app, clock, 0.05)
    badge = app.renderer.fx.badges[-1]
    assert badge.name == name and badge.points == f"+{app.cfg.targets[tg.tier].points}"
    step(app, clock, 2.0)
    assert not app.renderer.fx.badges  # gone after a moment
    assert app.frame_errors == 0


def test_a_badge_is_quick_and_the_next_hit_sends_the_last_one_away():
    from market_pulse.ui.effects import BADGE_HANDOVER, Badge, Effects
    fx = Effects.__new__(Effects)
    fx.badges = []
    fx.badge(100, 100, "a", "+10", "", (0, 0, 0))
    first = fx.badges[0]
    assert first.dur <= 1.3  # about a second on the wall
    first.age = 0.3
    fx.badge(200, 200, "b", "+15", "", (0, 0, 0))  # far away: it still makes room
    assert first.out == 0.3 and fx.badges[-1].out is None
    first.age = 0.3 + BADGE_HANDOVER + 0.01
    assert first.leaving() == 1.0 and fx.badges[-1].leaving() == 0.0
    b = Badge(0, 0, "c", "+30", "", (0, 0, 0), False)
    b.age = b.dur
    assert b.leaving() == 1.0


def test_the_results_have_no_photo_by_default_and_a_score_line(app):
    app, clock = app
    assert not app.renderer.photo_slot and app.camera is None
    app.flow.start_for("سارا", app.clock.now())
    step(app, clock, app.cfg.flow.intro_s + app.cfg.flow.countdown_s + 0.3)
    rnd = app.flow.round
    sensor_hit(app, clock, *rnd.targets[0].center_at(app.clock.now()))
    step(app, clock, 0.3)
    sensor_hit(app, clock, 1.0, 1.0)  # a miss
    step(app, clock, 0.3)
    assert rnd.score > 0
    assert rnd.score_path == [rnd.score, rnd.score] and rnd.summary()["score_path"] == rnd.score_path


def test_the_hit_morphs_out_of_the_box_and_zooms_away():
    from market_pulse.ui import effects as E
    sq = E._superellipse(0, 0, 10, 8, E.SQUIRCLE_N, 64)
    assert max(abs(x) for x, _ in sq) == pytest.approx(10) and max(abs(y) for _, y in sq) == pytest.approx(8)
    corner = max(sq, key=lambda p: p[0] + p[1])  # a rounded square reaches far into its corner
    assert corner[0] > 8.0
    circle = E._superellipse(0, 0, 10, 10, 2.0, 64)
    assert all(math.hypot(x, y) == pytest.approx(10) for x, y in circle)


def test_a_hit_badge_starts_from_the_box_that_was_hit(app):
    app, clock = app
    app.flow.start_for("سارا", app.clock.now())
    step(app, clock, app.cfg.flow.intro_s + app.cfg.flow.countdown_s + 0.3)
    tg = app.flow.round.targets[0]
    x, y = tg.center_at(app.clock.now())
    sensor_hit(app, clock, x, y)
    step(app, clock, 0.02)
    b = app.renderer.fx.badges[-1]
    assert b.tile is not None and b.tile[0] == pytest.approx(x, abs=1.0) and b.tile[2] > 0
    for _ in range(40):  # through the morph, the hold and the zoom out
        step(app, clock, 0.035)
    assert app.frame_errors == 0
