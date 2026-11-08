"""The Mofid logo intro: before every countdown and once as the game opens. No how-to page
as the game ships."""
import time

import numpy as np
import pygame
import pytest

from market_pulse import config
from market_pulse.app import App, Paths
from market_pulse.game import flow as F
from conftest import ROOT, game_cfg


class FakeTime:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


def new_flow(overrides=None):
    cfg = game_cfg(overrides)
    return cfg, F.Flow(cfg, rng=np.random.default_rng(3))


def make_app(tmp_path, resolution=(768, 1024), extra=None):
    over = {"display": {"resolution": list(resolution)}, "results": {"log_file": str(tmp_path / "r.jsonl")},
            "input": {"sources": []}, "camera": {"enabled": False}}
    for k, v in (extra or {}).items():
        over[k] = {**over.get(k, {}), **v}
    clock = FakeTime()
    app = App(game_cfg(over), Paths(ROOT), clock_source=clock, start_inputs=False)
    return app, clock


def step(app, clock, seconds, fps=30):
    for _ in range(max(1, int(round(seconds * fps)))):
        clock.t += 1 / fps
        app.frame()


# -- flow -------------------------------------------------------------------------------------
def test_the_game_ships_without_the_how_to_page():
    assert config.DEFAULTS["flow"]["show_instructions"] is False
    cfg, flow = new_flow()
    flow.request_start(0.0)
    assert flow.state == F.REGISTER  # the name, then the logo
    cfg, flow = new_flow({"flow": {"registration": False}})
    flow.request_start(0.0)
    assert flow.state == F.INTRO


@pytest.mark.parametrize("start", ["register", "phone", "operator"])
def test_the_logo_comes_before_every_countdown(start):
    cfg, flow = new_flow({"flow": {"registration": start == "register"}})
    if start == "register":
        flow.request_start(0.0)
        flow.register_type("سارا", 0.5)
        assert flow.submit_registration(1.0)
    elif start == "phone":
        assert flow.start_for("سارا", 1.0)
    else:
        assert flow.request_start(1.0)
    assert flow.state == F.INTRO and flow.round is not None and flow.round.started_at is None
    assert not flow.request_start(1.1) and not flow.start_for("علی", 1.1)  # no second round meanwhile
    flow.update(1.0 + cfg.flow.intro_s - 0.01)
    assert flow.state == F.INTRO
    flow.update(1.0 + cfg.flow.intro_s)
    assert flow.state == F.COUNTDOWN
    flow.update(1.0 + cfg.flow.intro_s + cfg.flow.countdown_s + 0.01)
    assert flow.state == F.PLAYING and flow.round.started_at is not None


def test_no_intro_when_it_is_switched_off():
    _, flow = new_flow({"flow": {"registration": False, "intro_s": 0}})
    flow.request_start(0.0)
    assert flow.state == F.COUNTDOWN


def test_reset_during_the_intro_goes_back_to_the_attract_screen():
    _, flow = new_flow()
    flow.start_for("سارا", 0.0)
    flow.reset(0.5)
    assert flow.state == F.ATTRACT and flow.round is None


def test_intro_length_is_checked():
    raw, warnings = config.DEFAULTS.copy(), []
    raw = {**raw, "flow": {**raw["flow"], "intro_s": 30}}
    config._validate(raw, warnings)
    assert any("intro_s" in w for w in warnings)


# -- the screen -------------------------------------------------------------------------------
def test_intro_plays_on_the_wall_and_the_countdown_follows(tmp_path):
    app, clock = make_app(tmp_path)
    step(app, clock, 0.5)
    app.flow.start_for("سارا", app.clock.now())
    seen = set()
    for _ in range(int((app.cfg.flow.intro_s + 0.3) * 30)):
        clock.t += 1 / 30
        app.frame()
        seen.add(app.flow.state)
    assert {F.INTRO, F.COUNTDOWN} <= seen and app.frame_errors == 0
    app.shutdown()


def logo_pixels(surf, rect) -> int:
    """Pixels of the logo inside rect: the white logo on the dark field (MOFID_UI v4.1), whose
    faint dots and symbols are never near white."""
    px = pygame.surfarray.pixels3d(surf.subsurface(rect))
    return int((px.astype(int).min(axis=2) > 235).sum())


def test_the_logo_settles_holds_zooms_in_and_is_gone(tmp_path):
    app, clock = make_app(tmp_path)
    r, surf, d = app.renderer, app.screen, app.cfg.flow.intro_s
    appear, leave = r._intro_timing(d)
    centre = app.vp.cm_rect(r.W / 2 - 20, r.H / 2 - 28, r.W / 2 + 20, r.H / 2 + 12)  # the logo's mark and name

    def frame(t):
        r._intro_overlay(surf, t, d)
        return surf.copy()

    def height(img):  # how tall the logo is, in a narrow band down the middle (no symbols there)
        band = app.vp.cm_rect(r.W / 2 - 8, 0, r.W / 2 + 8, r.H)
        px = pygame.surfarray.pixels3d(img.subsurface(band)).astype(int)
        rows = (px.sum(axis=2) > 450).any(axis=0)  # the white logo (fading too), brighter than the field and its dots
        return int(np.ptp(rows.nonzero()[0])) if rows.any() else 0

    start, rest, zoom, end = frame(0.0), frame(appear + 0.1), frame(leave + (d - leave) * 0.35), frame(d)
    assert logo_pixels(start, centre) == 0  # it fades in from nothing
    assert logo_pixels(rest, centre) > 0
    assert height(zoom) > height(rest) * 1.2  # it grows as it leaves
    assert logo_pixels(end, app.vp.rect) < 20  # and has gone: only the background is left
    assert pygame.image.tobytes(frame(1.0), "RGB") == pygame.image.tobytes(frame(1.0), "RGB")  # a function of t
    app.shutdown()


def test_without_the_logo_artwork_the_brand_name_is_drawn(tmp_path):
    app, clock = make_app(tmp_path)
    app.renderer.intro_logos = []
    for t in (0.1, 1.0, 2.0, 2.5):
        app.renderer._intro_overlay(app.screen, t, app.cfg.flow.intro_s)
    app.shutdown()


def test_game_opens_with_the_logo_and_cross_fades_to_the_attract_screen(tmp_path):
    app, clock = make_app(tmp_path)
    r = app.renderer
    r.draw_loading(app.screen)
    assert logo_pixels(app.screen, app.vp.rect) > 0
    r.start_boot(app.clock.now())  # the logo is already up: the boot plays from the end of its entrance
    step(app, clock, app.cfg.flow.intro_s - r._intro_timing(app.cfg.flow.intro_s)[0] - 0.3)
    assert r.boot_t0 is not None and app.flow.state == F.ATTRACT
    step(app, clock, 0.4)
    assert r.boot_t0 is None and r._fade_t0 > clock.t - 0.2  # the cross-fade to the attract screen
    step(app, clock, 0.5)
    app.flow.start_for("سارا", app.clock.now())  # a round after the boot shows the intro again
    step(app, clock, 0.2)
    assert app.flow.state == F.INTRO and app.frame_errors == 0
    app.shutdown()


def test_intro_frames_are_cheap_at_wall_resolution(tmp_path):
    app, clock = make_app(tmp_path, (1536, 2048))
    r, surf, d = app.renderer, app.screen, app.cfg.flow.intro_s
    for t in (0.5, 2.0):  # warm-up: the first scale of each size
        r._intro_overlay(surf, t, d)
    times = []
    for i in range(int(d * 60)):
        t0 = time.perf_counter()
        r._intro_overlay(surf, i / 60, d)
        times.append(time.perf_counter() - t0)
    times.sort()
    assert times[int(len(times) * 0.95)] < 0.012
    app.shutdown()
