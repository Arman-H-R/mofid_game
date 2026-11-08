"""The whole application, headless: full sessions at several display sizes,
operator keys, pause, recovery after errors, and frame time."""
import time

import pygame
import pytest

from market_pulse.app import App, Paths
from market_pulse.game import flow as F
from market_pulse.input.events import Control, Impact
from market_pulse.ui import theme
from conftest import ROOT, make_cfg

FAST = {
    "flow": {"instructions_s": 2.0, "countdown_s": 1.0, "results_s": 2.0},
    "round": {"end_delay_s": 0.5},
}


class FakeTime:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


def make_app(tmp_path, resolution, extra=None):
    over = {**FAST, "display": {"resolution": resolution},
            "results": {"log_file": str(tmp_path / "results.jsonl")}, "input": {"sources": []}}
    for k, v in (extra or {}).items():
        over[k] = {**over.get(k, {}), **v}
    cfg = make_cfg(over)
    clock = FakeTime()
    app = App(cfg, Paths(ROOT), clock_source=clock, start_inputs=False)
    return app, clock


def step(app, clock, seconds, fps=30):
    for _ in range(max(1, int(seconds * fps))):
        clock.t += 1.0 / fps
        app.frame()


def sensor_hit(app, clock, x_cm, y_cm):
    """A ball impact as the UDP/TUIO thread would deliver it (normalised, raw time)."""
    app.input.queue.put(Impact(x_cm / 300.0, y_cm / 400.0, clock.t, "udp_json", (0.0, 0.0)))


def play_session(app, clock):
    seen = set()

    def tick(seconds):
        for _ in range(max(1, int(seconds * 30))):
            clock.t += 1 / 30
            app.frame()
            seen.add(app.flow.state)

    tick(0.5)
    sb = app.cfg.layout.start_button
    sensor_hit(app, clock, sb.x_cm, sb.y_cm)  # start by throwing at the start box
    tick(0.2)
    assert app.flow.state == F.INSTRUCTIONS
    tick(2.0 + 1.0 + 0.2)
    assert app.flow.state == F.PLAYING
    rnd = app.flow.round
    for i in range(10):
        if i % 3 == 2:
            sensor_hit(app, clock, 150.0, 390.0)  # a miss
        else:
            want = "tiny" if i % 3 == 0 else "medium"
            tg = next((t for t in rnd.targets if t.tier == want and t.die_at is None),
                      next(t for t in rnd.targets if t.die_at is None))
            x, y = tg.center_at(app.clock.now())
            sensor_hit(app, clock, x, y)
        tick(0.8)  # past the merge / rebound windows
    assert rnd.end_reason == "balls" and rnd.throws == 10
    assert rnd.total_hits == 7 and rnd.score > 0
    tick(0.6)
    assert app.flow.state == F.RESULTS
    tick(2.2)
    assert app.flow.state == F.ATTRACT
    return seen


@pytest.mark.parametrize("res", [[1536, 2048], [1920, 1080], [1080, 1920], [768, 1024]])
def test_full_session_at_display_size(tmp_path, res):
    app, clock = make_app(tmp_path, res)
    try:
        assert app.screen.get_size() == tuple(res)
        vp = app.vp
        # The 3:4 wall is fitted without stretching and centred.
        assert abs(vp.rect.w / vp.rect.h - 0.75) < 0.01
        assert vp.rect.centerx == pytest.approx(res[0] / 2, abs=1)
        assert vp.rect.centery == pytest.approx(res[1] / 2, abs=1)
        seen = play_session(app, clock)
        assert {F.ATTRACT, F.INSTRUCTIONS, F.COUNTDOWN, F.PLAYING, F.ENDING, F.RESULTS} <= seen
        assert app.frame_errors == 0
        assert app.board and app.board[0]["score"] > 0  # the leaderboard picked up the round
        if vp.rect.x > 0:  # letterbox in the brand's deep blue
            assert tuple(app.screen.get_at((2, res[1] // 2)))[:3] == theme.BLUE_DEEP
    finally:
        app.shutdown()


def test_operator_keys_pause_missed_ball_and_reset(tmp_path):
    app, clock = make_app(tmp_path, [768, 1024], {"flow": {"show_instructions": False}})
    try:
        key = lambda k: app._handle_event(pygame.event.Event(pygame.KEYDOWN, key=k), app.clock.now())  # noqa: E731
        key(pygame.K_SPACE)
        assert app.flow.state == F.COUNTDOWN
        step(app, clock, 1.2)
        assert app.flow.state == F.PLAYING
        left = app.flow.round.time_left(app.clock.now())

        key(pygame.K_p)
        assert app.clock.paused
        sensor_hit(app, clock, 150.0, 390.0)  # ignored while paused
        step(app, clock, 3.0)
        assert app.flow.round.throws == 0
        assert app.flow.round.time_left(app.clock.now()) == pytest.approx(left)
        key(pygame.K_p)
        assert not app.clock.paused

        key(pygame.K_m)  # ball that never reached the wall
        assert app.flow.round.throws == 1
        app.input.queue.put(Control("throw_missed", clock.t, "udp_json"))
        step(app, clock, 0.1)
        assert app.flow.round.throws == 2

        key(pygame.K_F3)
        key(pygame.K_F2)
        step(app, clock, 0.2)  # debug + calibration overlays draw
        sensor_hit(app, clock, 150.0, 390.0)
        step(app, clock, 0.1)
        assert app.flow.round.throws == 2  # not counted while calibrating
        key(pygame.K_F2)
        key(pygame.K_F3)

        key(pygame.K_r)
        assert app.flow.state == F.ATTRACT and app.flow.round is None
        app.input.queue.put(Control("start", clock.t, "udp_json", {"player": {"id": "7", "name": "Sara"}}))
        step(app, clock, 0.1)
        assert app.flow.state == F.COUNTDOWN and app.flow.round_player["name"] == "Sara"
        assert app.frame_errors == 0
    finally:
        app.shutdown()


def test_escape_needs_a_double_press(tmp_path):
    app, clock = make_app(tmp_path, [768, 1024])
    try:
        app.running = True
        esc = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)
        app._handle_event(esc, 0.0)
        assert app.running
        app._handle_event(esc, 0.0)
        assert not app.running
    finally:
        app.shutdown()


def test_simulation_click_becomes_an_impact(tmp_path):
    app, clock = make_app(tmp_path, [768, 1024], {"input": {"simulation": True}})
    try:
        sb = app.cfg.layout.start_button
        pos = app.vp.px(sb.x_cm, sb.y_cm)
        app._handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos), 0.0)
        step(app, clock, 0.1)
        assert app.flow.state == F.INSTRUCTIONS
    finally:
        app.shutdown()


def test_frame_errors_recover_to_attract_then_exit_when_repeated(tmp_path):
    app, clock = make_app(tmp_path, [768, 1024])
    calls = {"n": 0}
    real_frame = app.frame

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("boom")
        real_frame()
        app.running = False

    app.frame = flaky
    assert app.run() == 0  # three errors, recovered each time
    assert app.frame_errors == 3

    app2, _ = make_app(tmp_path, [768, 1024])

    def always():
        raise RuntimeError("broken")

    app2.frame = always
    assert app2.run() == 2  # exit code for the kiosk launcher to restart the game


def test_frame_time_budget(tmp_path):
    """Busiest screen (all 13 products moving, Market Rush, the hit badges) at the reference wall resolution."""
    app, clock = make_app(tmp_path, [1536, 2048], {"flow": {"show_instructions": False}, "targets": {"active": 13},
                                                   "rush": {"enabled": True}})
    try:
        app.flow.request_start(app.clock.now())
        step(app, clock, 1.3)
        rnd = app.flow.round
        for _ in range(3):
            tg = next(t for t in rnd.targets if t.tier == "tiny")
            sensor_hit(app, clock, *tg.center_at(app.clock.now()))
            step(app, clock, 0.7)
        assert len(rnd.targets) == 13 and rnd.rush is not None
        step(app, clock, 1.0)  # caches warm
        times = []
        for _ in range(120):
            clock.t += 1 / 60
            t0 = time.perf_counter()
            app.frame()
            times.append(time.perf_counter() - t0)
        times.sort()
        p95_ms = times[int(len(times) * 0.95)] * 1000
        print(f"frame p95 {p95_ms:.1f} ms (software renderer, includes flip)")
        assert p95_ms < 16.7
    finally:
        app.shutdown()


def test_paths_read_artwork_from_the_bundle_and_write_next_to_the_exe(tmp_path):
    paths = Paths(tmp_path / "beside_exe", tmp_path / "inside_exe")
    assert paths.fonts == tmp_path / "inside_exe" / "assets" / "fonts"
    assert paths.sounds == tmp_path / "inside_exe" / "assets" / "sounds"
    assert paths.logs == tmp_path / "beside_exe" / "logs"
    assert Paths(tmp_path).assets == tmp_path / "assets"  # from source


def test_a_miss_logs_how_far_the_nearest_box_was(tmp_path, caplog):
    import logging
    app, clock = make_app(tmp_path, [768, 1024])
    with caplog.at_level(logging.INFO, logger="market_pulse.app"):
        play_session(app, clock)
    lines = [r.getMessage() for r in caplog.records if "nearest box" in r.getMessage()]
    assert lines and "outside its hit box" in lines[0]
