"""The score replay: the timeline (slow motion, zoom, the still), the strip that follows the
player, and composing the frames with OpenCV."""
import time

import numpy as np
import pygame
import pytest

from market_pulse import replay as R
from conftest import game_cfg

cv2 = pytest.importorskip("cv2")


# -- the timeline --------------------------------------------------------------------------------
def test_slow_motion_around_the_hit_normal_speed_before():
    tl = R.Timeline(0.0, 12.0, 10.0, slow=0.3)
    assert tl.speed(2.0) == pytest.approx(1.0)
    assert tl.speed(9.9) == pytest.approx(0.3)
    assert tl.speed(11.9) == pytest.approx(0.5)  # the reaction at half speed
    assert np.all(np.diff(tl.tau) > 0)
    assert tl.length > 12.0 + 4.0  # slower than the clip
    assert tl.moment(0.0).s == 0.0 and tl.moment(tl.length).s == 12.0


def test_zoom_points_and_the_still_follow_each_other():
    tl = R.Timeline(0.0, 12.0, 10.0)
    start, before, after, end = (tl.moment(t) for t in (0.0, tl.tau_hit - 0.5, tl.tau_hit + 0.5, tl.length - 0.01))
    assert start.zoom == 0.0 and before.zoom < 0.2 and end.zoom == pytest.approx(1.0)
    assert before.since_hit is None and after.since_hit == pytest.approx(0.5)
    assert before.slow == pytest.approx(1.0) and start.slow == 0.0
    assert end.poster == 0.0 and tl.moment(tl.length + tl.POSTER_IN).poster == 1.0
    assert tl.moment(tl.length + 30).s == 12.0  # it holds on the still
    assert [round(tl.moment(t).progress, 2) for t in (0, tl.length / 2, tl.length)] == [0.0, 0.5, 1.0]


def test_a_round_without_a_score_slows_at_its_end():
    tl = R.Timeline(0.0, 6.0, None)
    assert not tl.scored and tl.hit == 4.0
    assert all(tl.moment(t).since_hit is None for t in np.linspace(0, tl.length + 2, 40))


@pytest.mark.parametrize("hit", [-5.0, 0.0, 0.3, 30.0])
def test_a_hit_at_or_outside_the_clip_edges(hit):
    tl = R.Timeline(0.0, 4.0, hit)
    assert 0.0 <= tl.hit <= 4.0 and tl.length > 0 and np.all(np.diff(tl.tau) > 0)


# -- the strip -----------------------------------------------------------------------------------
SHAPE = (1080, 1920, 3)


def test_the_strip_holds_the_player_whole_and_is_1_to_2():
    box = (900, 300, 300, 700)
    rect = R.fit_strip(SHAPE, *R.strip_rect(SHAPE, box, [0, 0, 1, 1]))
    x, y, w, h = rect
    assert h == pytest.approx(2 * w, abs=2)
    assert x <= 900 and x + w >= 1200 and y <= 300 and y + h >= 1000
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080


def test_the_strip_waits_in_the_play_zone_until_someone_is_found():
    cx, top, h = R.strip_rect(SHAPE, None, [0.5, 0, 1, 1])
    assert cx == pytest.approx(0.75 * 1920) and h == 1080


@pytest.mark.parametrize("box", [(0, 0, 100, 1080), (1880, 0, 40, 200), (-50, -40, 300, 2000)])
def test_the_strip_stays_inside_the_frame(box):
    x, y, w, h = R.fit_strip(SHAPE, *R.strip_rect(SHAPE, box, [0, 0, 1, 1]))
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080 and w > 0


def test_recorded_frames_are_strips_and_glide_after_the_player():
    from market_pulse.camera import strip_encoder
    rects = []
    enc = strip_encoder(cv2)

    def encode(frame, rect):
        rects.append(rect)
        return enc(frame, rect)

    rec = R.ClipRecorder(game_cfg().camera, encode)
    rec.begin()
    frame = np.zeros(SHAPE, np.uint8)
    rec.follow((100, 300, 200, 700))
    t = 0.0
    for _ in range(5):
        rec.add(frame, t)
        t += 0.04
    rec.follow((1500, 300, 200, 700))  # the player has moved across
    for _ in range(40):
        rec.add(frame, t)
        t += 0.04
    img = cv2.imdecode(np.frombuffer(rec._frames[-1][1], np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (R.STRIP_SIZE[1], R.STRIP_SIZE[0], 3)
    xs = [r[0] for r in rects]
    assert xs[5] - xs[4] < 300  # no jump
    assert abs(xs[-1] + rects[-1][2] / 2 - 1600) < 60  # but it got there


# -- composing -----------------------------------------------------------------------------------
FACE = (230, 160, 80, 80)  # in the strip


def make_clip(n=60, fps=25, hit_at=1.6):
    """Frames whose brightness counts up, with a bright green 'face' in every one."""
    frames = []
    for i in range(n):
        img = np.full((R.STRIP_SIZE[1], R.STRIP_SIZE[0], 3), (40, 30 + i * 3, 90), np.uint8)
        x, y, w, h = FACE
        img[y:y + h, x:x + w] = (40, 230, 60)
        ok, jpeg = cv2.imencode(".jpg", img)
        frames.append((i / fps, jpeg.tobytes()))
    return R.Clip(frames, hit_at, 30)


def replay(clip=None, **kw):
    return R.Replay(cv2, clip or make_clip(), 0.3, find_face=lambda img, region: FACE, threaded=False, **kw)


def green_share(surf):
    px = pygame.surfarray.pixels3d(surf).astype(int)
    return float(((px[..., 1] > 150) & (px[..., 0] < 120)).mean())


def test_frames_are_the_pictures_size_and_a_function_of_time():
    rep = replay()
    rep.start((120, 240))
    a, b = rep.surface(0.2), rep.surface(1.0)
    assert a.get_size() == (120, 240)
    assert pygame.image.tobytes(a, "RGB") != pygame.image.tobytes(b, "RGB")
    fresh = replay()  # the same time on another copy: the same picture
    fresh.start((120, 240))
    assert pygame.image.tobytes(fresh.surface(0.2), "RGB") == pygame.image.tobytes(rep.surface(0.2), "RGB")


def test_it_zooms_onto_the_face():
    rep = replay()
    rep.start((120, 240))
    wide = green_share(rep.surface(0.0))
    close = green_share(rep.surface(rep.length - 0.05))
    assert close > wide * 3


def test_it_ends_on_the_still_in_the_rounds_look():
    rep = replay()
    rep.start((120, 240), "mono")
    still = pygame.surfarray.pixels3d(rep.surface(rep.length + 5)).astype(int)
    assert np.abs(still[..., 0] - still[..., 1]).max() <= 2  # grey
    assert rep.surface(rep.length + 5) is rep.surface(rep.length + 9)  # built once


def test_without_a_face_it_zooms_where_the_head_should_be():
    rep = R.Replay(cv2, make_clip(), 0.3, find_face=lambda img, region: None, threaded=False)
    rep.start((120, 240))
    assert rep._faces == [] and rep.surface(rep.length + 2) is not None


def test_a_broken_frame_is_black_not_a_crash():
    clip = make_clip(10)
    clip.frames[3] = (clip.frames[3][0], b"not a jpeg")
    rep = replay(clip)
    rep.start((60, 120))
    for k in range(int((rep.length + 2) * 30)):
        rep.surface(k / 30)


def test_threaded_replay_never_makes_the_screen_wait():
    rep = R.Replay(cv2, make_clip(), 0.3, find_face=lambda img, region: FACE)
    rep.start((300, 600))
    try:
        worst, got = 0.0, None
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < R.Replay.FIND_AFTER_S + 0.8:
            s = time.perf_counter()
            got = rep.surface(time.perf_counter() - t0)
            worst = max(worst, time.perf_counter() - s)
            time.sleep(1 / 60)
        assert got is not None and got.get_size() == (300, 600)
        assert worst < 0.005
        assert rep._faces  # the face was found meanwhile, after the card's entrance
    finally:
        rep.close()
    assert rep._thread is None and not rep._ready


def test_composing_keeps_up_at_wall_resolution():
    rep = replay(make_clip(120))
    rep.start((676, 1352))
    rep.surface(0.0)
    for k in range(int(rep.length * 30)):
        rep.surface(k / 30)
    ms = sorted(rep.compose_ms)
    assert ms[int(len(ms) * 0.9)] < 30  # a frame every 33 ms, in the background
