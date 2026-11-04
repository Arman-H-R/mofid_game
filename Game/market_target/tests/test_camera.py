"""The player's camera: choosing and following the player, recording the round, the replay
reaching the results of its own round, the still's looks, and the card around it."""
import random
import threading

import numpy as np
import pygame
import pytest

from market_pulse import camera as C
from market_pulse import replay as R
from market_pulse.app import App, Paths
from market_pulse.game import flow as F
from conftest import ROOT, game_cfg, make_cfg
from test_app import FakeTime, make_app, step

SHAPE = (1080, 1920, 3)
WHOLE = [0.0, 0.0, 1.0, 1.0]


# -- choosing the player ---------------------------------------------------------------------
def test_nearest_person_is_the_player():
    spectator = ((259, 206, 268, 380), 0.9)  # further back: shorter in the picture
    player = ((932, 311, 618, 738), 0.8)
    assert C.pick_player([spectator, player], SHAPE, WHOLE, None) == 1


def test_nobody_in_the_picture():
    assert C.pick_player([], SHAPE, WHOLE, None) is None


def test_play_zone_beats_size():
    outside = ((0, 100, 400, 900), 0.9)  # tall, but feet on the far left
    inside = ((900, 300, 300, 700), 0.9)
    zone = [0.3, 0.0, 1.0, 1.0]
    assert C.pick_player([outside, inside], SHAPE, zone, None) == 1
    assert C.pick_player([outside, inside], SHAPE, WHOLE, None) == 0


def test_the_same_player_is_kept_when_someone_slightly_taller_appears():
    player = ((900, 300, 300, 700), 0.9)
    passer = ((100, 280, 300, 740), 0.9)
    assert C.pick_player([passer, player], SHAPE, WHOLE, None) == 0
    assert C.pick_player([passer, player], SHAPE, WHOLE, (890, 310, 300, 690)) == 1


def test_iou():
    assert C.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert C.iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0.0
    assert C.iou((0, 0, 10, 10), (5, 0, 10, 10)) == pytest.approx(50 / 150)


# -- following the player, recording ------------------------------------------------------------
class FakeStream:
    status = "live"

    def __init__(self):
        self.asked = 0
        self.frame = np.zeros((90, 160, 3), np.uint8)
        self.recorder = None

    def latest(self, timeout=1.0):
        self.asked += 1
        return self.frame


class FakeJudge:
    """Hands out the player boxes in order (None: nobody)."""

    def __init__(self, *boxes):
        self.boxes = list(boxes) or [(10, 10, 40, 80)]
        self.n = 0

    def track(self, frame, previous):
        box = self.boxes[min(self.n, len(self.boxes) - 1)]
        self.n += 1
        return box

    def face(self, img, region, min_frac=0.15):
        return None

    def sharpness(self, crop):
        return 100.0


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


CAM = game_cfg().camera


def operator(judge=None, **cam):
    clock = Clock()
    p = C.PlayerCam(game_cfg({"camera": cam}).camera, FakeStream(), judge or FakeJudge(), None,
                    random.Random(3), start_thread=False, clock=clock)
    return p, clock


def feed(p, clock, seconds, fps=25):
    """Frames from the camera, as the camera thread hands them to the recorder."""
    for _ in range(int(seconds * fps)):
        clock.t += 1 / fps
        if p.recorder.wants_frame(clock.t):
            p.recorder.store(clock.t, b"jpeg")


def test_the_recorder_is_handed_to_the_stream():
    p, _ = operator()
    assert p.stream.recorder is p.recorder


def test_nothing_is_looked_at_or_recorded_outside_a_round():
    p, clock = operator()
    assert not p.step()
    feed(p, clock, 2.0)
    assert p.finish() is None and p.stream.asked == 0


def test_the_player_is_followed_while_the_round_is_played():
    p, clock = operator(FakeJudge((5, 6, 30, 70), None, (7, 8, 30, 70)))
    p.begin(0.0)
    assert p.step() and p.recorder._target == (5, 6, 30, 70)
    assert p.step() and p.recorder._target == (5, 6, 30, 70)  # nobody this time: the last place is kept
    p.step()
    assert p._player == (7, 8, 30, 70)
    p.tick(1.0, False)  # paused
    assert not p.step()


def test_a_scoring_hit_gives_the_replay_around_it():
    p, clock = operator(clip_before_s=4.0, clip_after_s=1.5, delay_s=0.5)
    p.begin(0.0)
    feed(p, clock, 6.0)
    hit = clock.t
    p.scored(40)
    feed(p, clock, 3.0)
    clip = p.finish()
    assert clip.points == 40 and clip.hit == pytest.approx(hit + 0.5)  # where the hit shows in the picture
    assert clip.start >= clip.hit - 4.0 - 1e-6 and clip.end <= clip.hit + 1.5 + 1e-6
    assert clip.end - clip.start == pytest.approx(5.5, abs=0.1)
    assert p.clips == 1


def test_misses_and_penalties_are_not_replayed():
    p, clock = operator()
    p.begin(0.0)
    feed(p, clock, 3.0)
    p.scored(0)
    p.scored(-20)
    feed(p, clock, 1.0)
    clip = p.finish()
    assert clip.hit is None and clip.points == 0  # the round's last seconds instead
    assert p.unscored == 1


def test_bigger_hits_are_replayed_more_often():
    picks = []
    for seed in range(300):
        rec = R.ClipRecorder(game_cfg().camera, None, random.Random(seed))
        rec.begin()
        rec.mark(10, 1.0)
        rec.mark(90, 2.0)
        picks.append(rec._pending[1])
    big = picks.count(90) / len(picks)
    assert 0.8 < big < 0.97  # usually the big one, but not always: rounds differ


def test_the_ring_keeps_only_the_last_seconds():
    p, clock = operator(clip_before_s=3.0, clip_after_s=1.0, delay_s=0.0)
    p.begin(0.0)
    feed(p, clock, 30.0)
    frames = p.recorder._frames
    assert frames[-1][0] - frames[0][0] <= p.recorder.keep + 0.05
    assert len(frames) <= int((p.recorder.keep + 0.1) * 25)


def test_recording_runs_at_clip_fps_from_a_faster_camera():
    p, clock = operator(clip_fps=25)
    p.begin(0.0)
    feed(p, clock, 10.0, fps=27)
    n = len(p.recorder._frames)
    assert 240 <= n <= 255


def test_the_highlight_is_kept_while_the_round_goes_on():
    p, clock = operator(clip_before_s=3.0, clip_after_s=1.0, delay_s=0.0)
    p.recorder.rng = random.Random(1)
    p.begin(0.0)
    feed(p, clock, 4.0)
    hit = clock.t
    p.scored(50)
    feed(p, clock, 20.0)  # long after: the ring has moved on, the highlight was set aside
    p.recorder.rng = type("Never", (), {"random": lambda self: 0.0})()  # later hits never win
    p.scored(10)
    clip = p.finish()
    assert clip.hit == pytest.approx(hit) and clip.points == 50


def test_a_hit_at_the_very_end_still_gets_its_replay():
    p, clock = operator(clip_after_s=2.0)
    p.begin(0.0)
    feed(p, clock, 5.0)
    p.scored(30)
    feed(p, clock, 0.4)  # the results came before the seconds after it were in
    clip = p.finish()
    assert clip.points == 30 and clip.end < clip.hit


def test_a_new_round_never_gets_the_last_players_replay():
    p, clock = operator()
    p.begin(0.0)
    feed(p, clock, 3.0)
    p.scored(30)
    p.begin(10.0)  # the next round starts
    assert p.finish() is None  # nothing of its own recorded yet


def test_cancel_drops_the_recording():
    p, clock = operator()
    p.begin(0.0)
    feed(p, clock, 3.0)
    p.cancel()
    assert p.finish() is None and not p.step()
    assert not p.recorder.wants_frame(clock.t)


def test_frame_judged_while_the_round_ends_is_thrown_away():
    gate, done = threading.Event(), threading.Event()

    class SlowJudge(FakeJudge):
        def track(self, frame, previous):
            gate.wait(2)
            return (1, 2, 3, 4)

    p, clock = operator(SlowJudge())
    p.begin(0.0)
    th = threading.Thread(target=lambda: (p.step(), done.set()))
    th.start()
    p.finish()  # the round ends while the frame is being looked at
    p.begin(30.0)
    gate.set()
    th.join(2)
    assert done.is_set() and p._player is None  # it did not land in the new round


def test_analysis_errors_and_missing_frames_do_not_stop_the_round():
    class Broken(FakeJudge):
        def track(self, frame, previous):
            raise ValueError("bad frame")

    p, clock = operator(Broken())
    p.begin(0.0)
    assert not p.step()
    p.stream.frame = None
    assert not p.step()


def test_encoding_errors_do_not_stop_the_round():
    def broken(frame, rect):
        raise ValueError("no")

    rec = R.ClipRecorder(CAM, broken)
    rec.begin()
    rec.add(np.zeros((90, 160, 3), np.uint8), 1.0)
    rec.add(np.zeros((90, 160, 3), np.uint8), 1.1)
    assert len(rec._frames) == 0


def test_background_thread_follows_the_player_and_stops():
    cam = game_cfg({"camera": {"samples_per_s": 10}}).camera
    p = C.PlayerCam(cam, FakeStream(), FakeJudge((3, 4, 20, 50)), None, random.Random(1))
    try:
        p.begin(0.0)
        for _ in range(100):
            if p.recorder._target is not None:
                break
            threading.Event().wait(0.02)
        assert p.recorder._target == (3, 4, 20, 50)
    finally:
        p.stop()
    assert not p.thread.is_alive()


def test_without_opencv_there_is_no_replay():
    p, clock = operator()
    p.begin(0.0)
    feed(p, clock, 3.0)
    assert p.replay(p.finish()) is None  # made without cv2
    assert p.replay(None) is None


def test_create_reports_missing_models(tmp_path):
    if C.import_cv2() is None:
        pytest.skip("OpenCV not installed")
    p, problem = C.PlayerCam.create(CAM, [tmp_path])
    assert p is None and "model files" in problem


def test_create_without_opencv(monkeypatch):
    monkeypatch.setattr(C, "import_cv2", lambda: None)
    p, problem = C.PlayerCam.create(CAM, [ROOT / "models"])
    assert p is None and "OpenCV" in problem


# -- settings --------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"play_zone": [0.5, 0, 0.4, 1]},
    {"play_zone": [0, 0, 1, 2]},
])
def test_bad_play_zone_falls_back(bad):
    import copy
    from market_pulse import config
    raw = copy.deepcopy(config.DEFAULTS)
    warnings = []
    config._merge(raw, {"camera": bad}, (), warnings)
    config._validate(raw, warnings)
    assert warnings and raw["camera"]["play_zone"] == [0.0, 0.0, 1.0, 1.0]


@pytest.mark.parametrize("key,value", [("clip_before_s", 60), ("clip_after_s", 0), ("clip_fps", 100),
                                       ("slow_motion", 0), ("delay_s", 9)])
def test_replay_settings_are_kept_in_range(key, value):
    import copy
    from market_pulse import config
    raw = copy.deepcopy(config.DEFAULTS)
    warnings = []
    config._merge(raw, {"camera": {key: value}}, (), warnings)
    config._validate(raw, warnings)
    assert any(key in w for w in warnings)


# -- the game ----------------------------------------------------------------------------------
class FakeReplay:
    """Solid frames: red while the video plays, blue once the still is in."""

    def __init__(self, points=40):
        self.timeline = R.Timeline(0.0, 4.0, 3.0, 0.3)
        self.length = self.timeline.length
        self.points, self.scored = points, True
        self.size = self.look = None
        self.closed = False
        self._red = self._blue = None

    def start(self, size, look="natural"):
        self.size, self.look = tuple(size), look
        self._red = pygame.Surface(size)
        self._red.fill((220, 0, 0))
        self._blue = pygame.Surface(size)
        self._blue.fill((0, 0, 220))

    def info(self, tau):
        return self.timeline.moment(tau)

    def surface(self, tau):
        if self.size is None:
            return None
        return self._blue if self.info(tau).poster >= 1 else self._red

    def close(self):
        self.closed = True


class FakeCam:
    """Stands in for PlayerCam inside the App."""

    def __init__(self, replay=None):
        self.next_replay = replay
        self.calls = []
        self.points = []

    def begin(self, now):
        self.calls.append("begin")

    def scored(self, points):
        self.points.append(points)

    def tick(self, now, active):
        pass

    def finish(self):
        self.calls.append("finish")
        return "clip" if self.next_replay is not None else None

    def replay(self, clip):
        return self.next_replay if clip is not None else None

    def cancel(self):
        self.calls.append("cancel")

    def status(self):
        return "camera fake"

    def stop(self):
        self.calls.append("stop")


def end_round(app, clock, hits=0):
    rnd = app.flow.round
    for i in range(rnd.balls):  # a few hits, the rest thrown and missed
        if i < hits:
            tg = next(t for t in rnd.targets if t.color == "green")
            x, y = tg.center_at(app.clock.now())
            app.flow.handle_impact(x, y, app.clock.now(), app.clock.now())
            step(app, clock, 0.8)
        else:
            app.flow.operator_missed_throw(app.clock.now())
            step(app, clock, 0.1)
    step(app, clock, 1.0)


def play_round(app, clock, hits=0):
    app.flow.start_for("Sara", app.clock.now())
    step(app, clock, 1.5)  # countdown
    assert app.flow.state == F.PLAYING
    end_round(app, clock, hits)
    assert app.flow.state == F.RESULTS


def test_replay_reaches_the_results_of_its_own_round(tmp_path):
    app, clock = make_app(tmp_path, [600, 800], {"camera": {"effect_chance": 0.0}})
    rep = FakeReplay()
    app.camera = FakeCam(rep)
    play_round(app, clock, hits=2)
    assert app.camera.calls[:2] == ["cancel", "begin"] and "finish" in app.camera.calls
    assert len(app.camera.points) == 2 and all(p > 0 for p in app.camera.points)  # every hit is offered
    assert app.renderer.replay is rep and app.renderer.replay_round == app.flow.summary["round_id"]
    step(app, clock, 1.3)  # the replay has risen into its frame (results last 2 s here)
    x0, y0, x1, y1 = app.renderer.PHOTO_RECT
    c = app.vp.px((x0 + x1) / 2, (y0 + y1) / 2 - 40)  # the middle of the picture, above the points
    assert rep.size == app.vp.cm_rect(*app.renderer.PHOTO_RECT).size
    assert app.screen.get_at(c)[0] > 150  # the red test video is on the screen

    # the next round's results never show it, even when its own recording failed
    app.camera.next_replay = None
    step(app, clock, 3)  # back to the attract screen
    assert app.renderer.replay is None and rep.closed  # its frames are let go
    play_round(app, clock)
    assert app.renderer.replay is None
    step(app, clock, 1.3)
    assert app.screen.get_at(c)[0] < 150  # the placeholder
    app.shutdown()


def test_aborted_round_drops_the_recording(tmp_path):
    app, clock = make_app(tmp_path, [600, 800])
    app.camera = FakeCam(FakeReplay())
    app.flow.start_for("Sara", app.clock.now())
    step(app, clock, 2.0)
    app.flow.reset(app.clock.now())
    step(app, clock, 0.2)
    assert app.camera.calls[-1] == "cancel" and "finish" not in app.camera.calls
    app.shutdown()


@pytest.mark.parametrize("enabled", [True, False])
def test_results_screen_with_and_without_the_photo_slot(tmp_path, enabled):
    app, clock = make_app(tmp_path, [450, 600], {"camera": {"enabled": enabled}})
    assert app.camera is None  # no inputs started in tests: no camera either
    assert app.renderer.photo_slot is enabled
    play_round(app, clock)
    step(app, clock, 1.0)
    assert app.flow.summary["score"] == 0
    app.shutdown()


def test_game_runs_when_the_camera_cannot_start(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "import_cv2", lambda: None)
    over = {"display": {"resolution": [450, 600]}, "results": {"log_file": str(tmp_path / "r.jsonl")},
            "input": {"sources": []}, "web": {"enabled": False},
            "flow": {"countdown_s": 1.0, "results_s": 2.0}, "round": {"end_delay_s": 0.5}}
    clock = FakeTime()
    app = App(make_cfg(over), Paths(ROOT), clock_source=clock, start_inputs=True)
    try:
        assert app.camera is None
        assert any("OpenCV" in w for w in app.warnings)
        play_round(app, clock)
    finally:
        app.shutdown()


def test_long_name_stays_in_the_text_column(tmp_path):
    app, clock = make_app(tmp_path, [600, 800], {"flow": {"name_max_chars": 40}})
    name = "نام بسیار بسیار طولانی بازیکن مسابقه برای آزمایش"
    app.flow.start_for(name, app.clock.now())
    step(app, clock, 1.5)
    end_round(app, clock)
    step(app, clock, 0.8)  # the name has been read in
    assert app.flow.state == F.RESULTS
    r = app.renderer
    band = range(app.vp.px(0, 46)[1], app.vp.px(0, 62)[1])  # the name line
    white = [x for y in band for x in range(app.vp.win_w) if app.screen.get_at((x, y))[:3] == (255, 255, 255)]
    left = app.vp.px(r.COL_X - r.COL_W / 2 - 1, 0)[0]
    right = app.vp.px(r.COL_X + r.COL_W / 2 + 1, 0)[0]
    text = [x for x in white if x > app.vp.px(r.PHOTO_RECT[2] + 8, 0)[0]]  # not the photo's white frame
    assert text and min(text) >= left and max(text) <= right
    app.shutdown()
    pygame.init()


# -- the still's looks ---------------------------------------------------------------------------
from market_pulse.ui import photo_fx  # noqa: E402


@pytest.mark.parametrize("name", photo_fx.EFFECTS)
def test_every_look_keeps_the_photo_shape(name):
    rng = np.random.default_rng(1)
    rgb = rng.integers(0, 256, (120, 60, 3), dtype=np.uint8)
    out = photo_fx.apply(rgb, name)
    assert out.shape == rgb.shape and out.dtype == np.uint8
    if name == "natural":
        assert out is rgb
    else:
        assert not np.array_equal(out, rgb)


def test_mono_is_grey_and_duotone_uses_the_brand_colours():
    rgb = np.random.default_rng(2).integers(0, 256, (40, 20, 3), dtype=np.uint8)
    mono = photo_fx.apply(rgb, "mono")
    assert np.array_equal(mono[..., 0], mono[..., 1]) and np.array_equal(mono[..., 1], mono[..., 2])
    black = photo_fx.apply(np.zeros((4, 4, 3), np.uint8), "duotone")
    from market_pulse.ui import theme
    assert tuple(black[0, 0]) == theme.BLUE_DEEP


def test_unknown_look_returns_the_photo():
    rgb = np.zeros((4, 4, 3), np.uint8)
    assert photo_fx.apply(rgb, "sepia") is rgb


def test_looks_are_picked_per_round(tmp_path):
    app, clock = make_app(tmp_path, [300, 400], {"camera": {"effect_chance": 0.5}})
    r = app.renderer
    r.fx_rng.seed(4)
    seen = set()
    for i in range(60):
        r.set_replay(f"r{i}", FakeReplay())
        seen.add(r.photo_effect)
    assert seen == {"natural", *app.cfg.camera.photo_effects}
    r.set_replay(None, None)
    assert r.photo_effect == "natural"  # the placeholder never gets a look
    app.shutdown()


def test_the_round_look_reaches_the_replay(tmp_path):
    app = card_app(tmp_path, {"effect_chance": 1.0, "photo_effects": ["mono"]})
    app.renderer.draw_results(app.screen, app.flow, 0.5)
    assert app.renderer.replay.look == "mono"
    app.shutdown()


def test_unknown_look_in_settings_is_reported():
    import copy
    from market_pulse import config
    raw = copy.deepcopy(config.DEFAULTS)
    warnings = []
    config._merge(raw, {"camera": {"photo_effects": ["mono", "sepia"]}}, (), warnings)
    config._validate(raw, warnings)
    assert warnings and raw["camera"]["photo_effects"] == ["mono"]


# -- the card ------------------------------------------------------------------------------------
def card_app(tmp_path, cam=None):
    app, clock = make_app(tmp_path, [450, 600], {"camera": {"effect_chance": 0.0, **(cam or {})}})
    from market_pulse.game.profile import classify
    summary = {"score": 260, "hits": {"green": 2, "blue": 1, "red": 3}, "rush": {"count": 1, "hits": 2, "points": 80},
               "total_hits": 6, "throws": 10, "best_multiplier": 3}
    app.flow.summary = {"round_id": "r9", "player_name": "Sara", **summary, "profile": classify(summary, app.cfg.profiles)}
    app.flow.rank, app.flow.since = 2, 0.0
    app.renderer.set_replay("r9", FakeReplay())
    app.renderer.results_t0 = 0.0
    return app


def test_entrance_is_a_function_of_time(tmp_path):
    app = card_app(tmp_path)
    r = app.renderer
    shots = {}
    for t in (0.0, 0.6, 1.2, 2.5, 6.0, 0.6, 6.0):  # out of order: no state is carried between frames
        r.draw_results(app.screen, app.flow, t)
        shot = pygame.image.tobytes(app.screen, "RGB")
        assert shots.setdefault(t, shot) == shot
    assert len(set(shots.values())) == 5  # and it does move
    app.shutdown()


def test_replay_rises_from_the_bottom(tmp_path):
    app = card_app(tmp_path)
    r, vp = app.renderer, app.vp
    x0, y0, x1, y1 = r.PHOTO_RECT
    top, bottom = vp.px((x0 + x1) / 2, y0 + 30), vp.px((x0 + x1) / 2, y1 - 30)
    t = r.T_PHOTO + 0.4  # half way
    r.draw_results(app.screen, app.flow, t)
    assert app.screen.get_at(bottom)[0] > 180 and app.screen.get_at(top)[0] < 100
    r.draw_results(app.screen, app.flow, 2.0)
    assert app.screen.get_at(top)[0] > 180
    app.shutdown()


def test_replay_overlays_slow_motion_points_and_the_still(tmp_path):
    app = card_app(tmp_path)
    r, vp, rep = app.renderer, app.vp, app.renderer.replay
    inner = vp.cm_rect(*r.PHOTO_RECT)
    lower = pygame.Rect(inner.x, inner.centery, inner.w, inner.h // 2 - vp.s(8))

    def at(tau):
        r.draw_results(app.screen, app.flow, r.T_PHOTO + tau)
        return app.screen

    def white(rect):
        px = pygame.surfarray.pixels3d(app.screen.subsurface(rect))
        return int((px.min(axis=2) > 230).sum())

    from market_pulse.ui import theme
    tl = rep.timeline
    at(0.8)
    assert white(lower) == 0  # no points before the hit
    at(tl.tau_hit + 0.5)
    assert white(lower) > 50  # the points rise at the hit
    # in slow motion the film bars close in at the top of the picture
    slow_tau = float(np.interp(tl.hit - 0.3, tl.ss, tl.tau))
    assert rep.info(slow_tau).slow > 0.9
    assert at(slow_tau).get_at((inner.centerx, inner.y + 2))[:3] == theme.BLUE_DEEP
    assert at(0.8).get_at((inner.centerx, inner.y + 2))[0] > 180  # no bars at normal speed
    # the still: the overlays have gone
    surf = at(rep.length + 2.0)
    assert surf.get_at(inner.center)[2] > 180 and white(lower) == 0
    app.shutdown()


def test_text_appears_in_order(tmp_path):
    app = card_app(tmp_path)
    r, vp = app.renderer, app.vp

    def white_in(y0, y1, t):
        r.draw_results(app.screen, app.flow, t)
        x_from = vp.px(r.COL_X - r.COL_W / 2, 0)[0]
        return sum(app.screen.get_at((x, y))[:3] == (255, 255, 255)
                   for y in range(vp.px(0, y0)[1], vp.px(0, y1)[1], 2) for x in range(x_from, vp.win_w, 2))

    assert white_in(46, 62, 0.3) == 0 and white_in(46, 62, 1.2) > 0  # the name
    assert white_in(80, 118, 0.6) == 0 and white_in(80, 118, 2.6) > 0  # the score
    assert white_in(160, 200, 1.6) == 0 and white_in(160, 200, 3.5) > 0  # the sentence
    app.shutdown()


def test_entrance_fits_a_frame(tmp_path):
    import time as _time
    app = card_app(tmp_path)
    app.renderer.draw_results(app.screen, app.flow, 0.0)  # builds the round's layers once
    times = []
    for k in range(int(8 * 30)):
        t0 = _time.perf_counter()
        app.renderer.draw_results(app.screen, app.flow, k / 30)
        times.append(_time.perf_counter() - t0)
    p95 = sorted(times)[int(len(times) * 0.95)]
    assert p95 < 0.010, p95  # of 16.7 ms at 60 FPS
    app.shutdown()
