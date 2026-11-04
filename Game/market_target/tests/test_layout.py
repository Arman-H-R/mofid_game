"""Random non-overlapping placement."""
import time

import numpy as np
import pytest

from market_pulse.game.layout import Footprint, Rect, place

FIELD = Rect(12, 64, 288, 370)  # the default play field, cm
# Footprints of the full 12-box field with the default sizes and motion.
TWELVE = (
    [Footprint(30.0, 14.0, 0.0)] * 4  # green 60 cm, sway_x 14 cm
    + [Footprint(45 * 1.08 / 2, 0.0, 12.0)] * 4  # blue 45 cm, pulse 8 %, sway_y 12 cm
    + [Footprint(25 * 1.12 / 2, 10.0, 10.0)] * 4  # red 25 cm, pulse 12 %, orbit 10 cm
)


def check(result, fps, field):
    s = result.motion_scale
    boxes = []
    for (cx, cy), f in zip(result.centers, fps):
        hx = f.half + f.ext_x * s
        hy = f.half + f.ext_y * s
        assert field.x0 - 1e-9 <= cx - hx and cx + hx <= field.x1 + 1e-9
        assert field.y0 - 1e-9 <= cy - hy and cy + hy <= field.y1 + 1e-9
        boxes.append((cx, cy, hx, hy))
    for i, (ax, ay, ahx, ahy) in enumerate(boxes):
        for bx, by, bhx, bhy in boxes[i + 1 :]:
            sep_x = abs(ax - bx) >= ahx + bhx + result.gap - 1e-9
            sep_y = abs(ay - by) >= ahy + bhy + result.gap - 1e-9
            assert sep_x or sep_y


@pytest.mark.parametrize("n", [3, 6, 9, 12])
def test_layouts_never_overlap(n):
    rng = np.random.default_rng(n)
    fps = TWELVE[:n] if n == 12 else (TWELVE[0:4][: n // 3] + TWELVE[4:8][: n // 3] + TWELVE[8:12][: n // 3])
    for _ in range(300):
        res = place(fps, FIELD, 12.0, rng)
        assert len(res.centers) == len(fps)
        if not res.fallback:
            check(res, fps, FIELD)


def test_twelve_boxes_keep_some_motion_most_of_the_time():
    rng = np.random.default_rng(7)
    scales = [place(TWELVE, FIELD, 12.0, rng).motion_scale for _ in range(300)]
    fallbacks = sum(1 for _ in range(300) if place(TWELVE, FIELD, 12.0, rng).fallback)
    assert fallbacks == 0
    assert np.median(scales) > 0.0


def test_min_move_is_respected_when_possible():
    rng = np.random.default_rng(11)
    fps = TWELVE[:1] + TWELVE[4:5] + TWELVE[8:9]
    prev = [(100.0, 150.0), (200.0, 250.0), (150.0, 320.0)]
    for _ in range(200):
        res = place(fps, FIELD, 12.0, rng, prev=prev, min_move=35.0)
        assert res.moved_ok
        for (cx, cy), p in zip(res.centers, prev):
            assert np.hypot(cx - p[0], cy - p[1]) >= 35.0


def test_impossible_layout_falls_back_to_grid_without_crashing():
    rng = np.random.default_rng(0)
    fps = [Footprint(60.0, 10.0, 10.0)] * 12  # 12 boxes of 120 cm cannot fit
    res = place(fps, FIELD, 12.0, rng)
    assert res.fallback and len(res.centers) == 12


def test_empty_layout():
    assert place([], FIELD, 12.0, np.random.default_rng()).centers == []


def test_layout_is_fast_enough_for_a_frame():
    rng = np.random.default_rng(1)
    t0 = time.perf_counter()
    for _ in range(100):
        place(TWELVE, FIELD, 12.0, rng)
    per_layout_ms = (time.perf_counter() - t0) * 10.0
    assert per_layout_ms < 8.0, per_layout_ms  # half a 60 FPS frame, on a slow CI box
