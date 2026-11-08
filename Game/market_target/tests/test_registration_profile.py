"""The registration screen's rules, the end-screen play style, and the whole game
as it ships, played through the real application."""
import pygame
import pytest
import numpy as np

from market_pulse.app import App, Paths
from market_pulse.game import flow as F
from market_pulse.game.profile import classify, playstyle
from market_pulse.input.events import Impact
from market_pulse.ui import theme
from conftest import ROOT, game_cfg


def new_flow(overrides=None):
    cfg = game_cfg(overrides)
    return cfg, F.Flow(cfg, rng=np.random.default_rng(4))


def to_register(flow, now=0.0):
    flow.request_start(now)
    flow.request_start(now)
    assert flow.state == F.REGISTER


# -- names -------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["", "   ", "‌", " ‌ ", "--", "..."])
def test_empty_names_cannot_start(name):
    _, flow = new_flow()
    to_register(flow)
    flow.register_type(name, 1.0)
    flow.drain_events()
    assert not flow.submit_registration(1.5)
    assert flow.state == F.REGISTER and flow.round is None
    assert flow.register_error_at == 1.5
    assert "register_error" in [e.name for e in flow.drain_events()]


@pytest.mark.parametrize("name, stored", [("سارا", "سارا"), ("  علی   رضا ", "علی رضا"), ("Sara", "Sara"), ("نیمه‌فاصله", "نیمه‌فاصله")])
def test_valid_names_start_the_round(name, stored):
    _, flow = new_flow()
    to_register(flow)
    flow.register_type(name, 1.0)
    assert flow.submit_registration(1.5)
    assert flow.state == F.INTRO and flow.round_player["name"] == stored


def test_typing_filters_limits_and_clears_the_error():
    cfg, flow = new_flow()
    to_register(flow)
    flow.submit_registration(0.5)
    assert flow.register_error_at is not None
    flow.register_type("a\n\t\x07😀b", 1.0)
    assert flow.name_input == "ab" and flow.register_error_at is None
    flow.register_type("x" * 100, 1.0)
    assert len(flow.name_input) == cfg.flow.name_max_chars
    flow.register_backspace(1.0)
    assert len(flow.name_input) == cfg.flow.name_max_chars - 1


def test_a_ball_or_click_on_the_button_needs_a_name_too():
    cfg, flow = new_flow()
    to_register(flow)
    sb = cfg.layout.start_button
    flow.handle_impact(sb.x_cm, sb.y_cm, 1.0, 1.0)
    assert flow.state == F.REGISTER and flow.register_error_at == 1.0
    flow.register_type("سارا", 1.5)
    flow.handle_impact(20.0, 20.0, 2.0, 2.0)  # elsewhere on the wall: nothing
    assert flow.state == F.REGISTER
    flow.handle_impact(sb.x_cm, sb.y_cm, 2.5, 2.5)
    assert flow.state == F.INTRO and flow.round_player["name"] == "سارا"


def test_pressing_start_again_cannot_start_a_second_round_or_timer():
    cfg, flow = new_flow()
    to_register(flow)
    flow.register_type("سارا", 1.0)
    assert flow.submit_registration(1.0)
    rnd = flow.round
    assert not flow.submit_registration(1.1)
    assert not flow.request_start(1.2)
    assert flow.round is rnd
    t = 1.0
    for _ in range(int((cfg.flow.intro_s + cfg.flow.countdown_s + 0.1) * 60)):
        t += 1 / 60
        flow.update(t)
    assert flow.state == F.PLAYING and rnd.ends_at == pytest.approx(rnd.started_at + 60.0)
    started = rnd.started_at
    assert not flow.request_start(t)
    assert not flow.submit_registration(t)
    flow.update(t + 1)
    assert rnd.started_at == started and flow.round is rnd


def test_a_name_from_the_registration_system_skips_the_screen():
    _, flow = new_flow()
    flow.handle_control("player", {"id": "9", "name": "Ali"}, 0.0)
    flow.request_start(0.0)
    flow.request_start(0.1)
    assert flow.state == F.INTRO and flow.round_player == {"id": "9", "name": "Ali"}


def test_an_id_alone_still_asks_for_the_name_and_keeps_the_id():
    _, flow = new_flow()
    flow.handle_control("player", {"id": "9"}, 0.0)
    to_register(flow)
    flow.register_type("علی", 0.5)
    flow.submit_registration(0.5)
    assert flow.round_player == {"id": "9", "name": "علی"}


def test_a_name_arriving_during_the_screen_fills_the_field():
    _, flow = new_flow()
    to_register(flow)
    flow.handle_control("player", {"id": "", "name": "Ali"}, 1.0)
    assert flow.name_input == "Ali"


def test_nobody_typing_goes_back_to_the_attract_screen():
    cfg, flow = new_flow()
    to_register(flow)
    flow.update(cfg.flow.registration_timeout_s - 1)
    assert flow.state == F.REGISTER
    flow.register_type("a", cfg.flow.registration_timeout_s - 1)
    flow.update(cfg.flow.registration_timeout_s + 1)
    assert flow.state == F.REGISTER  # typing keeps it open
    flow.update(cfg.flow.registration_timeout_s * 2)
    assert flow.state == F.ATTRACT and flow.name_input == ""


def test_registration_can_be_switched_off():
    _, flow = new_flow({"flow": {"registration": False}})
    flow.request_start(0.0)
    flow.request_start(0.1)
    assert flow.state == F.INTRO


# -- play styles -------------------------------------------------------------------------------
def summary(medium=0, small=0, tiny=0, rush_hits=0, score=0):
    return {"hits": {"medium": medium, "small": small, "tiny": tiny}, "rush": {"hits": rush_hits}, "score": score}


@pytest.mark.parametrize("data, expected", [
    (summary(), "watchful"),
    (summary(medium=2), "watchful"),
    (summary(tiny=5, medium=3, small=2), "aggressive"),
    (summary(tiny=3, medium=4, small=3), "risk_tolerant"),
    (summary(tiny=1, medium=3, small=2, rush_hits=4), "risk_tolerant"),
    (summary(tiny=1, medium=8, small=1), "cautious"),
    (summary(medium=5, small=5), "cautious"),
    (summary(tiny=2, medium=6, small=2), "growth"),
    (summary(tiny=2, medium=4, small=4), "balanced"),
    (summary(small=8, medium=2), "balanced"),
])
def test_play_style_rules(data, expected):
    assert playstyle(data, game_cfg().profiles) == expected


def test_a_high_score_alone_never_means_high_risk():
    p = game_cfg().profiles
    careful = summary(medium=9, small=1, score=100000)
    assert playstyle(careful, p) == "cautious"
    bold_but_low = summary(tiny=4, medium=1, score=10)
    assert playstyle(bold_but_low, p) == "aggressive"


def test_the_score_picks_the_wording_within_a_style():
    cfg = game_cfg()
    said = {classify(summary(tiny=2, medium=4, small=4, score=s), cfg.profiles)["statement"] for s in (0, 120, 5000)}
    assert len(said) == 3
    tiers = cfg.profiles.score_tiers
    style = cfg.profiles.styles.balanced.statements
    assert classify(summary(tiny=2, medium=4, small=4, score=tiers[0] - 1), cfg.profiles)["statement"] == style[0]
    assert classify(summary(tiny=2, medium=4, small=4, score=tiers[0]), cfg.profiles)["statement"] == style[1]
    assert classify(summary(tiny=2, medium=4, small=4, score=tiers[1]), cfg.profiles)["statement"] == style[2]


def test_every_statement_is_distinct_and_makes_no_promise():
    styles = game_cfg().profiles.styles.raw()
    texts = [s for style in styles.values() for s in style["statements"]]
    assert len(texts) == len(set(texts)) >= 16
    for text in texts:
        for word in ("سود", "تضمین", "مطمئن", "حتماً"):
            assert word not in text, text


# -- the real application, as it ships ---------------------------------------------------------
class FakeTime:
    def __init__(self):
        self.t = 7000.0

    def __call__(self):
        return self.t


@pytest.fixture
def game(tmp_path):
    cfg = game_cfg({"display": {"resolution": [768, 1024]}, "results": {"log_file": str(tmp_path / "r.jsonl")},
                    "input": {"sources": []}})
    clock = FakeTime()
    app = App(cfg, Paths(ROOT), clock_source=clock, start_inputs=False)
    yield app, clock
    app.shutdown()


def tick(app, clock, seconds, seen=None):
    for _ in range(max(1, int(round(seconds * 30)))):
        clock.t += 1 / 30
        app.frame()
        if seen is not None:
            seen.add(app.flow.state)


def ball(app, clock, x_cm, y_cm):
    app.input.queue.put(Impact(x_cm / 300.0, y_cm / 400.0, clock.t, "udp_json", (0.0, 0.0)))


def key(app, k):
    app._handle_event(pygame.event.Event(pygame.KEYDOWN, key=k), app.clock.now())


def typed(app, text):
    app._handle_event(pygame.event.Event(pygame.TEXTINPUT, text=text), app.clock.now())


def play_to_registration(app, clock):
    tick(app, clock, 0.3)
    sb = app.cfg.layout.start_button
    ball(app, clock, sb.x_cm, sb.y_cm)
    tick(app, clock, 0.2)
    assert app.flow.state == F.REGISTER  # no how-to page as the game ships


def test_keys_type_into_the_name_instead_of_running_operator_commands(game):
    app, clock = game
    play_to_registration(app, clock)
    for k in (pygame.K_r, pygame.K_p, pygame.K_m, pygame.K_SPACE):
        key(app, k)
    assert app.flow.state == F.REGISTER and not app.clock.paused
    key(app, pygame.K_RETURN)
    assert app.flow.state == F.REGISTER and app.flow.register_error_at is not None
    tick(app, clock, 0.2)
    typed(app, "س")
    typed(app, "ا")
    typed(app, "x")
    key(app, pygame.K_BACKSPACE)
    assert app.flow.name_input == "سا"
    tick(app, clock, 0.2)
    key(app, pygame.K_RETURN)
    assert app.flow.state == F.INTRO and app.flow.round_player["name"] == "سا"
    typed(app, "z")  # no longer typing
    assert app.flow.name_input == ""
    assert app.frame_errors == 0


def test_mouse_click_on_the_start_button_without_simulation(game):
    app, clock = game
    play_to_registration(app, clock)
    sb = app.cfg.layout.start_button
    typed(app, "Sara")
    pos = app.vp.px(sb.x_cm, sb.y_cm)
    app._handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos), app.clock.now())
    assert app.flow.state == F.INTRO


def test_f4_cancels_registration(game):
    app, clock = game
    play_to_registration(app, clock)
    key(app, pygame.K_F4)
    assert app.flow.state == F.ATTRACT


def test_a_complete_game_as_it_ships(game):
    """Register, the logo intro, countdown, 60 s with Market Rush, and the results, through App.frame()."""
    app, clock = game
    seen = set()
    play_to_registration(app, clock)
    typed(app, "سارا")
    key(app, pygame.K_RETURN)
    assert app.flow.state == F.INTRO
    tick(app, clock, app.cfg.flow.intro_s + 0.05, seen)
    assert app.flow.state == F.COUNTDOWN
    countdown_at = clock.t
    tick(app, clock, app.cfg.flow.countdown_s + 0.1, seen)
    assert app.flow.state == F.PLAYING and clock.t - countdown_at == pytest.approx(app.cfg.flow.countdown_s + 0.1, abs=0.05)
    rnd = app.flow.round
    assert rnd.started_at is not None
    game_start = rnd.started_at

    def throw(tier):
        """At a box of that size; a medium or small one means any box that is not tiny."""
        now = app.clock.now()
        live = [t for t in rnd.targets if not t.gone(now) and t.die_at is None]
        tg = next((t for t in live if t.tier == tier), None) or next(t for t in live if t.tier != "tiny")
        ball(app, clock, *tg.center_at(now))
        tick(app, clock, 0.8, seen)  # past the merge and rebound windows
        return tg

    assert len(rnd.targets) == app.cfg.targets.active == 4
    throw("medium")
    assert rnd.rush is None and app.renderer.fx is not None  # a box that is not tiny starts nothing
    assert len(rnd.targets) == 4  # replaced at once
    before_rush = len(rnd.targets)
    throw("tiny")
    rush = rnd.rush
    assert rush is not None and len(rnd.rushes) == 1
    assert rush.ends_at - rush.started_at == pytest.approx(4.0)
    assert rnd.warp.rate(app.clock.now() + 1) == pytest.approx(app.cfg.rush.speed)

    # during the phase: the picture changes, more boxes, x2
    tick(app, clock, 0.4, seen)
    x, y = app.vp.px(4, 340)
    field = sum(theme.FIELD)
    assert sum(app.screen.get_at((x, y))[:3]) < field - 30  # the field has gone deep
    throw("medium")
    assert rnd.rush_hits == 1 and rnd.rush_points >= 20
    assert len(rnd.targets) > before_rush

    # a tiny hit during it: a fresh 4 seconds, the same phase
    throw("tiny")
    assert len(rnd.rushes) == 1
    assert rush.ends_at - app.clock.now() == pytest.approx(4.0 - 0.8, abs=0.05)

    # after it: normal again
    tick(app, clock, rush.ends_at - app.clock.now() + 0.6, seen)
    assert rnd.rush is None and rnd.warp.rate(app.clock.now()) == 1.0
    assert not any(t.rush_extra for t in rnd.targets)
    assert sum(app.screen.get_at((x, y))[:3]) == pytest.approx(field, abs=6)  # the field's own colour again
    assert len(rnd.targets) == 4

    # and the next tiny hit: another one, of 4 seconds
    throw("tiny")
    assert len(rnd.rushes) == 2 and rnd.rush is not None
    tick(app, clock, 4.5, seen)
    assert rnd.rush is None and rnd.rushes[1].ended_at - rnd.rushes[1].started_at == pytest.approx(4.0)

    # no ball limit as it ships: balls keep counting past 10, only the time ends the game
    assert not app.cfg.round.ball_limit and rnd.balls == 0 and rnd.throws == 5
    while rnd.throws < 12:
        ball(app, clock, 150.0, 390.0)
        tick(app, clock, 0.8, seen)
    assert rnd.running and app.flow.state == F.PLAYING
    score = rnd.score
    tick(app, clock, game_start + app.cfg.round.duration_s - clock.t + 0.05, seen)
    assert rnd.finished and rnd.end_reason == "time" and rnd.throws == 12
    assert app.flow.state == F.ENDING
    count = len(rnd.targets)
    ball(app, clock, *rnd.targets[0].center_at(app.clock.now()))
    tick(app, clock, app.cfg.round.end_delay_s + 0.1, seen)
    assert app.flow.state == F.RESULTS
    assert rnd.score == score and len(rnd.targets) == count

    rec = app.flow.summary
    assert rec["player_name"] == "سارا" and rec["score"] == rnd.score and rec["end_reason"] == "time"
    assert rec["profile"]["key"] in app.cfg.profiles.styles.raw()
    assert rec["profile"]["statement"] in app.cfg.profiles.styles[rec["profile"]["key"]].statements
    assert rec["rush"]["count"] == 2
    tick(app, clock, 2.0, seen)
    assert {F.INTRO, F.COUNTDOWN, F.PLAYING, F.ENDING, F.RESULTS} <= seen
    assert app.frame_errors == 0
