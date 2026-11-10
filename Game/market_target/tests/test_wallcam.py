"""The camera hit detector on synthetic camera pictures: a pinhole camera with the real lens's barrel
distortion looks at a textured wall; a ball flies in, hits a known point and bounces off."""
import functools
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from market_pulse.input.wallcam import markers
from market_pulse.input.wallcam.detect import HitDetector, Settings, TouchDetector
from market_pulse.input.wallcam.geometry import DEFAULT_LENS, Lens, WallMap
from market_pulse.input.wallcam.stream import CaptureClock

W, H = 640, 360
WALL = (300.0, 200.0)
LENS = Lens.from_dict(DEFAULT_LENS)


class Cam:
    """Camera at C looking straight at the wall (plane z = 0, x right, y down, z towards the camera)."""

    def __init__(self, c=(150.0, 100.0, 260.0)):
        self.c = np.array(c, float)
        self.f = LENS.f * W

    def pinhole(self, p):
        p = np.atleast_2d(np.asarray(p, float))
        d = self.c[2] - p[:, 2]
        return np.stack([W / 2 + self.f * (p[:, 0] - self.c[0]) / d, H / 2 + self.f * (p[:, 1] - self.c[1]) / d], 1)

    def project(self, p):
        return LENS.distort(self.pinhole(p), (W, H))

    def wall_to_pinhole_h(self):
        src = np.array([[0, 0], [WALL[0], 0], [WALL[0], WALL[1]], [0, WALL[1]]], float)
        dst = self.pinhole(np.c_[src, np.zeros(4)])
        return cv2.getPerspectiveTransform(src.astype(np.float32), dst.astype(np.float32))


def remap_lens(pinhole_img):
    """A pinhole picture as the real (barrel) lens would show it."""
    ys, xs = np.mgrid[0:H, 0:W]
    pts = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float64)
    src = LENS.undistort(pts, (W, H)).astype(np.float32)
    return cv2.remap(pinhole_img, src[:, 0].reshape(H, W), src[:, 1].reshape(H, W), cv2.INTER_LINEAR,
                     borderValue=90)


CAMERAS = {
    "front": ((150.0, 100.0, 260.0), (120.0, 160.0, 210.0)),  # camera position, throw start
    "high": ((150.0, -40.0, 420.0), (130.0, 150.0, 290.0)),  # above the wall's top edge, behind the player
}


@pytest.fixture(scope="module")
def scene():
    return build_scene("front")


@functools.lru_cache(maxsize=None)
def build_scene(name):
    cam = Cam(CAMERAS[name][0])
    rng = np.random.default_rng(1)
    tex = (rng.integers(60, 140, (int(WALL[1]), int(WALL[0]))).astype(np.uint8))
    tex = cv2.GaussianBlur(cv2.resize(tex, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST), (3, 3), 0)
    Hm = cam.wall_to_pinhole_h() @ np.diag([0.5, 0.5, 1.0])
    pin = cv2.warpPerspective(tex, Hm, (W, H), borderValue=90)
    bg = remap_lens(pin)
    corners = np.array([[0, 0, 0], [WALL[0], 0, 0], [WALL[0], WALL[1], 0], [0, WALL[1], 0]], float)
    wmap = WallMap.fit(cam.project(corners), corners[:, :2], (W, H), WALL, LENS)
    return cam, bg, wmap


def throw(cam, bg, start, impact, t_hit, bounce=0.45, fps=25.0, phase=0.013, radius=4.0, seconds=0.8, color=235):
    """Frames (image, t) of a ball from `start` (3D, at t=0) to `impact` (on the wall, at t_hit), bouncing back.
    A colour picture (bg BGR) takes a BGR ball colour."""
    start, impact = np.asarray(start, float), np.asarray(impact, float)
    v_in = (impact - start) / t_hit
    v_out = np.array([v_in[0] * 0.3, v_in[1] * 0.3, -v_in[2] * bounce])
    out = []
    rng = np.random.default_rng(2)
    for k in range(int(seconds * fps)):
        t = phase + k / fps
        if t <= t_hit:
            p = start + v_in * t
        else:
            dt = t - t_hit
            p = impact + v_out * dt + np.array([0, 0.5 * 980 * dt * dt, 0])
        img = bg.copy()
        if t >= 0.06:  # leaves the hand
            u, v = cam.project(p)[0]
            r = cam.f * radius / (cam.c[2] - p[2])
            cv2.circle(img, (int(round(u * 16)), int(round(v * 16))), max(1, int(round(r * 16))), color, -1,
                       cv2.LINE_AA, shift=4)
        img = cv2.add(img, rng.integers(0, 4, img.shape, dtype=np.uint8))
        out.append((cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img, t))
    return out


def run(det, frames, bg):
    empty = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR) if bg.ndim == 2 else bg
    for k in range(3):
        det.process(empty, -0.2 + 0.04 * k)
    hits = []
    for img, t in frames:
        hits += det.process(img, t)
    return hits


def test_mapping_round_trip(scene):
    cam, _, wmap = scene
    pts = np.array([[20, 30, 0], [150, 100, 0], [280, 180, 0], [5, 195, 0]], float)
    got = wmap.to_wall(cam.project(pts))
    assert np.abs(got - pts[:, :2]).max() < 0.5
    back = wmap.to_image(pts[:, :2])
    assert np.abs(back - cam.project(pts)).max() < 0.3


def test_lens_fit_from_many_points(scene):
    cam, _, _ = scene
    xs, ys = np.meshgrid(np.linspace(10, 290, 6), np.linspace(10, 190, 5))
    wall = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], 1)
    wrong = Lens(0.5, -0.05, LENS.k2)  # start from a poor guess: the fit finds k1
    m = WallMap.fit(cam.project(wall), wall[:, :2], (W, H), WALL, wrong)
    assert abs(m.lens.k1 - LENS.k1) < 0.01
    assert m.max_cm < 0.5


@pytest.mark.parametrize("camera", list(CAMERAS))
@pytest.mark.parametrize("impact", [(200.0, 60.0, 0.0), (40.0, 150.0, 0.0), (270.0, 20.0, 0.0), (150.0, 100.0, 0.0)])
@pytest.mark.parametrize("phase", [0.005, 0.025])
def test_ball_hit_point_and_time(camera, impact, phase):
    cam, bg, wmap = build_scene(camera)
    frames = throw(cam, bg, start=CAMERAS[camera][1], impact=impact, t_hit=0.37, phase=phase)
    det = HitDetector(wmap, Settings(ball_cm=8.0))
    hits = run(det, frames, bg)
    assert len(hits) == 1, hits
    h = hits[0]
    assert math.hypot(h.x - impact[0], h.y - impact[1]) < 8.0, (h.kind, h.x, h.y)
    assert abs(h.t - 0.37) < 0.04, (h.kind, h.t)


def test_no_hit_without_a_ball(scene):
    cam, bg, wmap = scene
    det = HitDetector(wmap, Settings())
    img = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    rng = np.random.default_rng(3)
    hits = []
    for k in range(60):
        noisy = cv2.add(img, rng.integers(0, 6, img.shape, dtype=np.uint8))
        hits += det.process(noisy, k / 25)
    assert hits == []


def test_light_change_is_not_a_hit(scene):
    cam, bg, wmap = scene
    det = HitDetector(wmap, Settings())
    img = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    hits = []
    for k in range(40):
        gain = 1.0 if k < 20 else 1.3  # the camera's exposure jumps
        hits += det.process(cv2.convertScaleAbs(img, alpha=gain), k / 25)
    assert hits == []
    assert det.gain < 0.85  # matched back to the background's brightness


def test_touch_by_dwell(scene):
    cam, bg, wmap = scene
    det = TouchDetector(wmap, Settings(touch_dwell_s=0.3))
    img = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    for k in range(5):
        det.process(img, k / 25)
    # an "arm" from the bottom edge reaching up to wall point (150, 80)
    tip = cam.project([(150.0, 80.0, 0.0)])[0]
    base = cam.project([(140.0, 230.0, 0.0)])[0]
    arm = img.copy()
    cv2.line(arm, tuple(int(v) for v in base), tuple(int(v) for v in tip), (20, 20, 20), 9)
    hits = []
    for k in range(5, 30):
        hits += det.process(arm, k / 25)
    assert len(hits) == 1
    assert math.hypot(hits[0].x - 150, hits[0].y - 80) < 6


def test_markers_calibrate_the_screen(scene):
    cam, _, _ = scene
    placed = markers.grid(WALL[0], WALL[1], 5, 4)
    board = markers.render(WALL[0], WALL[1], placed, 1200, 800)
    Hm = cam.wall_to_pinhole_h() @ np.diag([WALL[0] / 1200, WALL[1] / 800, 1.0])
    pin = cv2.warpPerspective(board, Hm, (W, H), borderValue=40)
    view = remap_lens(pin)
    found = markers.detect(view)
    assert len(found) >= 16
    img, wall = markers.pairs(found, placed)
    m = WallMap.fit(img, wall, (W, H), WALL, Lens(0.5, -0.1, LENS.k2))
    assert m.rms_cm < 1.0
    probe = np.array([[150.0, 100.0, 0.0], [20.0, 20.0, 0.0], [280.0, 180.0, 0.0]])
    assert np.abs(m.to_wall(cam.project(probe)) - probe[:, :2]).max() < 2.0


def test_capture_clock_takes_the_fastest_frames():
    clock = CaptureClock(delay_s=0.05)
    # camera stamps every 40 ms; arrival = stamp + 1000 s offset + 0.03..0.09 s jitter
    rng = np.random.default_rng(4)
    stamps = np.arange(0, 4, 0.04)
    out = [clock.capture_time(s, 1000.0 + s + 0.03 + rng.uniform(0, 0.06)) for s in stamps]
    late = np.array(out[25:]) - (1000.0 + stamps[25:])
    assert np.all(np.abs(late - (0.03 - 0.05)) < 0.006)  # jitter gone, fixed delay removed


# -- the game's input source ----------------------------------------------------------------------
class FakeStream:
    """Stands in for CameraStream: hands out the given frames in order, then nothing. Its backlog
    always looks empty, so the source checks every frame (it never skips ahead)."""

    class _Backlog:
        @staticmethod
        def qsize():
            return 0

    def __init__(self, frames=()):
        self.items = []
        self.add(frames)
        self.frames = self._Backlog()
        self.fps, self.status, self.error, self.gap = 25.0, "live", "", False
        self.stopped = False

    def add(self, frames, late=0.1):
        from market_pulse.input.wallcam.stream import Frame
        for img, t in frames:
            self.items.append(Frame(len(self.items) + 1, img, t, late))

    def get(self, timeout=0.5):
        return self.items.pop(0) if self.items else None

    def stop(self):
        self.stopped = True


def camera_source(tmp_path, monkeypatch, frames, wall=(600.0, 400.0), wmap=None, **settings):
    import queue
    from market_pulse import config
    from market_pulse.input.wallcam import source as S
    path = tmp_path / "cam.json"
    if wmap is not None:
        wmap.save(path)
    fake = FakeStream(frames)
    monkeypatch.setattr(S, "open_stream", lambda cfg: fake)
    cfg = config.Section({**config.DEFAULTS["input"]["camera"], "ball_cm": 8, **settings})
    out = queue.SimpleQueue()
    return S.CameraSource(out, cfg, path, tmp_path / "saved.json", *wall), out, fake


def wait_for(cond, seconds=10.0):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end and not cond():
        time.sleep(0.02)
    return cond()


def test_the_camera_source_puts_a_hit_on_the_game_queue_in_game_units(tmp_path, monkeypatch):
    cam, bg, wmap = build_scene("high")
    empty = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    frames = [(empty, -0.2 + 0.04 * k) for k in range(3)]
    frames += throw(cam, bg, start=CAMERAS["high"][1], impact=(200.0, 60.0, 0.0), t_hit=0.37)
    # calibrated on crosses at the 300 x 200 cm wall's corners; the game's wall is 600 x 400
    wmap.method = "click"
    src, out, fake = camera_source(tmp_path, monkeypatch, frames, wmap=wmap)
    src.start()
    try:
        assert wait_for(lambda: not out.empty())
        ev = out.get()
        assert ev.source == "camera"
        assert math.hypot(ev.x * 300 - 200, ev.y * 200 - 60) < 8  # normalised: the same spot on any wall
        assert abs(ev.t - 0.37) < 0.04
        assert src.events == 1 and src.state == "listening" and src.calibrated
    finally:
        src.stop()
    assert wait_for(lambda: fake.stopped)


@pytest.mark.parametrize("late, hits_wanted", [(0.1, 1), (4.5, 0)])
def test_pictures_that_come_seconds_late_are_skipped(tmp_path, monkeypatch, caplog, late, hits_wanted):
    """On the game PC the camera stalled and then sent its pictures 4-5 s late: every hit landed on the
    player's next throw. A picture that late is skipped, and the log says the camera is stalling."""
    import logging
    cam, bg, wmap = build_scene("high")
    empty = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    frames = [(empty, -0.2 + 0.04 * k) for k in range(3)]
    frames += throw(cam, bg, start=CAMERAS["high"][1], impact=(200.0, 60.0, 0.0), t_hit=0.37)
    wmap.method = "click"
    src, out, fake = camera_source(tmp_path, monkeypatch, [], wmap=wmap)
    fake.add(frames, late=late)
    with caplog.at_level(logging.WARNING, logger="market_pulse.input.wallcam.source"):
        hits = run_source(src, out, fake, len(frames))
    assert len(hits) == hits_wanted
    assert src.late_frames == (len(frames) if late > 1 else 0)
    warned = [r.getMessage() for r in caplog.records if "came late" in r.getMessage()]
    assert len(warned) == (1 if late > 1 else 0)  # once, not for every picture
    if warned:
        assert "up to 4.5 s" in warned[0]


def test_markers_shown_by_the_game_calibrate_the_camera(tmp_path, monkeypatch):
    cam, _, _ = build_scene("front")
    placed = markers.grid(WALL[0], WALL[1], 5, 4)  # what start_markers asks the game to draw
    board = markers.render(WALL[0], WALL[1], placed, 1200, 800)
    Hm = cam.wall_to_pinhole_h() @ np.diag([WALL[0] / 1200, WALL[1] / 800, 1.0])
    view = cv2.cvtColor(remap_lens(cv2.warpPerspective(board, Hm, (W, H), borderValue=40)), cv2.COLOR_GRAY2BGR)
    import time
    frames = [(view, k / 25) for k in range(40)]
    src, out, fake = camera_source(tmp_path, monkeypatch, [], wall=WALL)
    assert not src.calibrated
    assert src.start_markers() == placed
    src._marker_t0 = time.monotonic() - 1.0  # the board is up already
    fake.add(frames)
    src.start()
    try:
        assert wait_for(lambda: src.marker_result is not None)
        ok, message = src.marker_result
        assert ok, message
        assert src.calibrated and (tmp_path / "saved.json").is_file()
        assert src.map.rms_cm < 1.0
        probe = np.array([[150.0, 100.0, 0.0], [20.0, 20.0, 0.0], [280.0, 180.0, 0.0]])
        assert np.abs(src.map.to_wall(cam.project(probe)) - probe[:, :2]).max() < 2.0
    finally:
        src.stop()


def markers_map(name="high"):
    """The scene's calibration as F2 makes it: with markers, in the game's own cm (its wall is WALL)."""
    from market_pulse.input.wallcam.geometry import WallMap as WM
    cam, bg, wmap = build_scene(name)
    return cam, bg, WM(wmap.H, wmap.lens, wmap.image_size, wmap.wall_size, method="markers")


def run_source(src, out, fake, n):
    import time
    src.start()
    try:
        wait_for(lambda: src.packets >= n)
        time.sleep(0.2)
        hits = []
        while not out.empty():
            hits.append(out.get())
        return hits
    finally:
        src.stop()


@pytest.mark.parametrize("real_width, found", [(150.0, True), (0.0, False)])
def test_a_marker_calibration_takes_the_pictures_real_size(tmp_path, monkeypatch, real_width, found):
    """The game says its wall is 300 cm wide, but its picture is really 150 cm: a calibration made with
    markers is in the game's cm, so the 4 cm ball looked twice its size (no hit) until the real width
    is given. Hits stay shares of the picture."""
    cam, bg, wmap = markers_map()
    empty = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    frames = [(empty, -0.2 + 0.04 * k) for k in range(3)]
    frames += throw(cam, bg, start=CAMERAS["high"][1], impact=(200.0, 60.0, 0.0), t_hit=0.37)
    src, out, fake = camera_source(tmp_path, monkeypatch, frames, wall=WALL, wmap=wmap, ball_cm=4,
                                   screen_width_cm=real_width)
    assert src.calibrated
    assert src.map.wall_size == pytest.approx((150.0, 100.0) if real_width else WALL)
    hits = run_source(src, out, fake, len(frames))
    assert len(hits) == (1 if found else 0), [(round(h.x, 2), round(h.y, 2)) for h in hits]
    if found:
        assert math.hypot(hits[0].x * 300 - 200, hits[0].y * 200 - 60) < 8


@pytest.mark.parametrize("floor_margin, wanted", [(0.0, 0), (25.0, 1)])
def test_a_ball_below_the_pictures_bottom_edge_is_the_floor_not_a_hit(floor_margin, wanted):
    """Projector, 2026-11-08 20:01: the ball came back off the wall, bounced on the floor towards the
    camera and was counted, seen just below the picture (the margin let it in). Below the bottom edge
    only floor_margin_cm counts; the sides and the top keep the margin."""
    cam, bg, wmap = build_scene("front")
    frames = throw(cam, bg, start=CAMERAS["front"][1], impact=(150.0, 208.0, 0.0), t_hit=0.37)
    det = HitDetector(wmap, Settings(ball_cm=8.0, margin_cm=25.0, floor_margin_cm=floor_margin))
    hits = run(det, frames, bg)
    assert len(hits) == wanted, [(round(h.x), round(h.y), h.kind) for h in hits]
    assert det._inside(-10.0, 100.0) and det._inside(150.0, -10.0)  # the margin at the sides and the top
    assert det._inside(150.0, 199.0) and det._inside(150.0, 210.0) == (floor_margin >= 10)


def colour_scene():
    """The wall's picture blue, nearly even (a projector's), and a ball as bright as it but yellow-green:
    in grey the ball hardly differs from the wall, in colour it does."""
    cam, grey, wmap = build_scene("front")
    g = 100.0 + (grey.astype(np.float32) - 100.0) * 0.15
    bgr = np.stack([g + 60, g - 5, g - 10], -1)
    return cam, np.clip(bgr, 0, 255).astype(np.uint8), wmap


@pytest.mark.parametrize("color, wanted", [(False, 0), (True, 1)])
def test_colour_mode_sees_a_ball_as_bright_as_the_picture_behind_it(color, wanted):
    cam, bg, wmap = colour_scene()
    ball = (30, 110, 120)  # BGR: grey ~104, the wall's ~100
    assert abs(int(cv2.cvtColor(np.uint8([[ball]]), cv2.COLOR_BGR2GRAY)[0, 0]) - 101) < 6
    frames = throw(cam, bg, start=CAMERAS["front"][1], impact=(200.0, 60.0, 0.0), t_hit=0.37, color=ball)
    det = HitDetector(wmap, Settings(ball_cm=8.0, ball_tone="light", color=color))
    hits = run(det, frames, bg)
    assert len(hits) == wanted, [(round(h.x), round(h.y), h.kind) for h in hits]
    if wanted:
        assert math.hypot(hits[0].x - 200, hits[0].y - 60) < 8 and abs(hits[0].t - 0.37) < 0.04


def test_colour_mode_finds_the_same_hits_on_a_grey_wall(scene):
    """A light ball on a grey wall: colour on changes nothing (the brightness part of the distance)."""
    cam, bg, wmap = scene
    frames = throw(cam, bg, start=CAMERAS["front"][1], impact=(40.0, 150.0, 0.0), t_hit=0.37)
    got = []
    for color in (False, True):
        hits = run(HitDetector(wmap, Settings(ball_cm=8.0, color=color)), frames, bg)
        assert len(hits) == 1, (color, hits)
        got.append(hits[0])
    assert math.hypot(got[0].x - got[1].x, got[0].y - got[1].y) < 3 and abs(got[0].t - got[1].t) < 0.02


def game_path(t):
    """A ball-sized thing the game itself animates on the wall: fast, with a sharp turn (wall cm)."""
    a, b, c = np.array([40.0, 40.0]), np.array([150.0, 120.0]), np.array([180.0, 20.0])
    return a + (b - a) * t / 0.4 if t < 0.4 else b + (c - b) * min(1.0, (t - 0.4) / 0.4)


def with_game_drawing(cam, frames, radius_cm=4.0):
    """The camera's frames with the game's moving disk on the wall, and the game's own frames
    (its picture, small and grey, as App hands them to the camera)."""
    seen, drawn = [], []
    for img, t in frames:
        x, y = game_path(t)
        img = img.copy()
        u, v = cam.project([(x, y, 0.0)])[0]
        r = cam.f * radius_cm / cam.c[2]
        cv2.circle(img, (int(round(u * 16)), int(round(v * 16))), max(1, int(round(r * 16))), (235, 235, 235), -1,
                   cv2.LINE_AA, shift=4)
        seen.append((img, t))
        big = np.full((256, 384), 100, np.uint8)
        k = 384 / WALL[0]
        cv2.circle(big, (int(round(x * k)), int(round(y * k))), max(1, int(round(radius_cm * k))), 235, -1)
        drawn.append((t, cv2.resize(big, (96, 64), interpolation=cv2.INTER_AREA)))
    return seen, drawn


@pytest.mark.parametrize("told", [True, False])
def test_what_the_game_draws_on_the_wall_is_not_a_ball(tmp_path, monkeypatch, told):
    """The camera sees the game's picture: a disk the game moves across the wall (and turns) looks
    just like a ball. Told what the game drew, the camera leaves it out."""
    cam, bg, wmap = markers_map()
    empty = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    still = [(empty, -0.2 + 0.04 * k) for k in range(3)]
    moving, drawn = with_game_drawing(cam, [(empty, 0.013 + k / 25) for k in range(25)])
    src, out, fake = camera_source(tmp_path, monkeypatch, still + moving, wall=WALL, wmap=wmap)
    assert src.wants_screen
    if told:
        for t, grey in [(-0.3, drawn[0][1])] + drawn:
            src.screen_drawn(t, grey)
    hits = run_source(src, out, fake, len(still) + len(moving))
    assert (hits == []) if told else hits, hits


def test_a_real_ball_still_hits_while_the_game_animates_elsewhere(scene):
    from market_pulse.input.wallcam.screen import ScreenChanges
    cam, bg, wmap = markers_map("high")
    frames = throw(cam, bg, start=CAMERAS["high"][1], impact=(250.0, 160.0, 0.0), t_hit=0.37)
    seen, drawn = with_game_drawing(cam, frames)  # the game's disk runs in the upper left
    screen = ScreenChanges()
    for t, grey in [(-0.3, drawn[0][1])] + drawn:
        screen.add(t, grey)
    det = HitDetector(wmap, Settings(ball_cm=8.0))
    empty = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    for k in range(3):
        det.process(empty, -0.2 + 0.04 * k)
    hits = []
    for img, t in seen:
        hits += det.process(img, t, ignore=screen.mask(t, wmap, (W, H)))
    assert len(hits) == 1, [(round(h.x), round(h.y), h.kind) for h in hits]
    assert math.hypot(hits[0].x - 250, hits[0].y - 160) < 8


def test_a_typed_switch_is_read_as_a_switch():
    s = Settings()
    s.set_text("edge_reject", "0")
    assert s.edge_reject is False
    s.set_text("edge_reject", "on")
    assert s.edge_reject is True
    s.set_text("threshold", "25")
    assert s.threshold == 25
    with pytest.raises(KeyError):
        s.set_text("no_such", "1")


REAL = Path(__file__).resolve().parents[1] / "data" / "camtest" / "rec_20261108-022435"
BRICK = REAL.parent / "brick_wall_calibration.json"  # the 97 x 83 cm crosses those recordings were made on


@pytest.mark.skipif(not (REAL / "times.csv").is_file(), reason="the real test recording is not on this PC")
def test_the_real_recording_finds_the_throws_and_not_the_thrower():
    """One thrower under the camera, about 10 throws at the 97 x 83 cm crosses (2026-11-08). The live
    test before these fixes found 4 throws and 13 false hits on the thrower's head and hands."""
    import csv
    from market_pulse import config
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "config" / "settings.json")
    det = HitDetector(WallMap.load(BRICK), Settings.from_dict(cfg.input.camera))
    hits = []
    for row in csv.DictReader(open(REAL / "times.csv")):
        img = cv2.imread(str(REAL / f"{int(row['frame']):06d}.jpg"))
        if img is not None:
            hits += det.process(img, float(row["t"]))
    assert 8 <= len(hits) <= 11, [(round(h.x), round(h.y), h.kind) for h in hits]
    assert all(-15 <= h.x <= 112 and -15 <= h.y <= 90 for h in hits), [(round(h.x), round(h.y)) for h in hits]


LOB = REAL.parent / "rec_20261108-024952"


@pytest.mark.skipif(not (LOB / "times.csv").is_file(), reason="the soft-lob recording is not on this PC")
def test_a_soft_lob_that_just_reaches_the_wall_is_a_hit():
    """Live retest (2026-11-08), frames 2018-2030: the ball rises out of the hand (joined to the arm, so
    not seen), touches the wall at the top of its arc just left of the wall (about -9, 53 cm) and drops.
    Too slow for a turn, and no depth peak: the live run missed it."""
    import csv
    from market_pulse import config
    cfg, _ = config.load(Path(__file__).resolve().parents[1] / "config" / "settings.json")
    det = HitDetector(WallMap.load(BRICK), Settings.from_dict(cfg.input.camera))
    hits = []
    for row in csv.DictReader(open(LOB / "times.csv")):
        if 1980 <= int(row["frame"]) <= 2060:
            img = cv2.imread(str(LOB / f"{int(row['frame']):06d}.jpg"))
            if img is not None:
                hits += det.process(img, float(row["t"]))
    assert [h.kind for h in hits] == ["lob"], [(round(h.x), round(h.y), h.kind) for h in hits]
    assert math.hypot(hits[0].x + 9, hits[0].y - 53) < 6


def test_one_ball_bouncing_back_is_one_hit(monkeypatch):
    """Projector, 2026-11-08 17:13:02: the ball hit the wall above a box, then flew back at the camera as
    a new track that turned again 0.17 s later and 26 cm away, and the game counted two throws. Whatever
    sign calls it, a hit that close to the one before is the same ball; the next throw still counts."""
    from market_pulse.input.wallcam.detect import Hit
    _, bg, wmap = build_scene("high")
    det = HitDetector(wmap, Settings(ball_cm=20.0))
    calls = [
        [Hit(36.0, 82.0, 10.00, 0, 0, "turn")],  # the ball on the wall
        [Hit(59.0, 94.0, 10.17, 0, 0, "turn")],  # coming back off it: the same ball
        [Hit(40.0, 60.0, 10.30, 0, 0, "depth"), Hit(45.0, 62.0, 10.32, 0, 0, "slow")],  # still the same
        [Hit(150.0, 60.0, 10.40, 0, 0, "depth")],  # far away: another ball
        [Hit(38.0, 84.0, 11.80, 0, 0, "depth")],  # the next throw, at the same spot
    ]
    monkeypatch.setattr(det, "_track", lambda blobs, t: calls.pop(0))
    empty = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    det.process(empty, 9.96)  # the first picture: the empty wall is learned
    found = []
    for k in range(5):
        found += det.process(empty, 10.0 + 0.04 * k)
    assert [(h.x, h.t) for h in found] == [(36.0, 10.00), (150.0, 10.40), (38.0, 11.80)]


def _hit(kind, angle, t, x=50.0):
    from market_pulse.input.wallcam.detect import Hit
    return Hit(x, 50.0, t, 0.0, 0.0, kind, 300.0, angle, 1.0)


def test_a_soft_bend_waits_for_the_sharper_bend_at_the_wall(scene):
    """A dive: a small bend in the air, then a much sharper one at the wall two frames later."""
    from market_pulse.input.wallcam.detect import Track
    det = HitDetector(scene, Settings(ball_cm=20, wait_frames=2))
    tr = Track(1)
    assert det._sharper(tr, _hit("bend", 40, 1.00, x=40)) is None  # waits
    assert not tr.hit
    assert det._sharper(tr, None) is None
    got = det._sharper(tr, _hit("turn", 110, 1.08, x=60))
    assert got is not None and got.x == 60 and tr.hit


def test_a_soft_bend_counts_when_nothing_sharper_follows(scene):
    from market_pulse.input.wallcam.detect import Track
    det = HitDetector(scene, Settings(ball_cm=20, wait_frames=2))
    tr = Track(1)
    assert det._sharper(tr, _hit("bend", 40, 1.00)) is None
    assert det._sharper(tr, _hit("bend", 45, 1.04)) is None  # the same bend seen again
    got = det._sharper(tr, None)
    assert got is not None and got.angle == 40 and tr.hit


def test_a_sharp_turn_and_an_earlier_depth_fit_do_not_wait_or_replace(scene):
    from market_pulse.input.wallcam.detect import Track
    det = HitDetector(scene, Settings(ball_cm=20, wait_frames=2))
    tr = Track(1)
    assert det._sharper(tr, _hit("turn", 120, 1.0)).angle == 120  # sharp: at once
    tr2 = Track(2)
    assert det._sharper(tr2, _hit("turn", 60, 1.0, x=40)) is None
    assert det._sharper(tr2, _hit("depth", 0, 0.9, x=70)) is None  # an earlier moment: not after the bend
    assert det._sharper(tr2, None).x == 40
