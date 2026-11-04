"""The STL27L lidar inside the game: packets to scans, the detector on made-up scans, the wall
map, the source's thread with a stand-in for the serial port, and the game using its hits
(only in a round, calibration with F2 then C, saving the points)."""
import json
import math
import queue
import struct
import time

import numpy as np
import pygame
import pytest

from market_pulse.app import App, Paths
from market_pulse.config import Section
from market_pulse.game import flow as F
from market_pulse.input.events import Impact
from market_pulse.input.lidar import driver
from market_pulse.input.lidar.capture import Capture, targets
from market_pulse.input.lidar.detect import TouchDetector
from market_pulse.input.lidar.source import LidarSource, read_points
from market_pulse.input.lidar.wallmap import WallMap
from market_pulse.input.manager import InputManager
from conftest import ROOT, game_cfg
from test_app import FakeTime, step

W, H = 300.0, 400.0
STEP = 0.165  # degrees between two beams of the STL27L


def lidar_to_wall(x, y):
    """The lidar of these tests: at the bottom-left corner of the wall, x to the right, y up."""
    return x / 10.0, H - y / 10.0


def wall_to_lidar(wx, wy):
    return wx * 10.0, (H - wy) * 10.0


def make_scan(ball=None, radius=60.0, wall=4500.0, t=None):
    """One rotation: everything at `wall` mm, plus a ball (x, y mm) in front of it."""
    angle = np.arange(0.0, 360.0, STEP)
    dist = np.full(angle.shape, wall)
    if ball is not None:
        bx, by = ball
        a = np.radians(angle)
        ux, uy = np.cos(a), np.sin(a)
        along = ux * bx + uy * by  # ray-circle intersection
        off2 = bx * bx + by * by - along ** 2
        hit = (along > 0) & (off2 < radius ** 2)
        dist[hit] = along[hit] - np.sqrt(radius ** 2 - off2[hit])
    r = np.radians(angle)
    return driver.Scan(angle=angle, dist=dist, intensity=np.full(angle.shape, 200, np.uint8),
                       x=np.cos(r) * dist, y=np.sin(r) * dist,
                       t=time.monotonic() if t is None else t, step=STEP, hz=10.0)


def lidar_cfg(**over):
    raw = {"port": "COM_TEST", "baud": 921600, "settings_file": "config/lidar.json", "max_range_mm": 8000,
           "wall_margin_cm": 15, "round_only": True, "calibration_points": []}
    raw.update(over)
    return Section(raw)


def tuning_file(tmp_path, **over):
    data = {"mode": "ball", "shape": "rectangle", "area_x": 1500, "area_y": 2000, "width": 3000, "height": 4000,
            "min_width": 30, "ball_size": 120, "hit_delay": 0.4, "calibration_time": 2}
    data.update(over)
    path = tmp_path / "lidar.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def points_for(cross_hits):
    return [{"wall_cm": list(w), "lidar_mm": list(m)} for w, m in cross_hits]


def exact_points():
    return points_for((w, wall_to_lidar(*w)) for w in targets(W, H))


def make_source(tmp_path, tuning=None, **over):
    return LidarSource(queue.SimpleQueue(), lidar_cfg(**over), tuning_file(tmp_path, **(tuning or {})), W, H)


def ready_detector(source):
    det = TouchDetector(source.tuning)
    det.calibrate([make_scan(t=i * 0.1) for i in range(10)])
    return det


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


# -- driver: bytes to scans ----------------------------------------------------------------
def packet(start_deg, dist_mm=1000):
    end = (start_deg + 11 * 0.165) % 360
    body = struct.pack("<BBHH", 0x54, 0x2C, 3600, int(round(start_deg * 100)))
    body += b"".join(struct.pack("<HB", dist_mm, 200) for _ in range(12))
    body += struct.pack("<HH", int(round(end * 100)), 0)
    return body + bytes([driver.crc8(body)])


def test_driver_builds_scans_from_packets_and_skips_bad_ones():
    asm = driver.ScanAssembler(max_range=8000)
    starts = [i * 12 * 0.165 for i in range(int(360 / (12 * 0.165)))]
    data = b"".join(packet(s) for s in starts)
    corrupt = bytearray(packet(10.0))
    corrupt[10] ^= 0xFF
    scans = asm.feed(data + bytes(corrupt) + data + packet(0.0), now=1.0)
    assert len(scans) == 2 and asm.crc_errors >= 1
    scan = scans[0]
    assert len(scan.dist) == len(starts) * 12 and np.all(scan.dist == 1000)
    assert scan.hz == pytest.approx(10.0) and scan.t == 1.0


def test_driver_waits_for_a_missing_port_without_holding_up(monkeypatch):
    class Missing:
        def __init__(self, *a, **k):
            raise driver.serial.SerialException("no port")

    monkeypatch.setattr(driver.serial, "Serial", Missing)
    t0 = time.monotonic()
    lidar = driver.D800("COM_NOPE")
    assert time.monotonic() - t0 < 0.5 and lidar.ser is None  # the reader thread keeps trying
    lidar.close()
    assert not lidar.thread.is_alive()


# -- wall map ---------------------------------------------------------------------------------
def test_wall_map_fits_turn_mirror_and_shift_exactly():
    a = math.radians(30)

    def true(x, y):  # turned 30 degrees, mirrored, in cm, shifted
        return (math.cos(a) * x - math.sin(a) * y) / 10 + 40, -(math.sin(a) * x + math.cos(a) * y) / 10 + 380

    lidar = [(100.0, 200.0), (2800.0, 150.0), (2600.0, 3900.0), (300.0, 3700.0)]
    m = WallMap.fit([(p, true(*p)) for p in lidar])
    assert m.error_cm < 1e-6
    for p in [(1234.0, 567.0), (0.0, 0.0)]:
        assert m.to_wall(*p) == pytest.approx(true(*p))
        assert m.to_lidar(*m.to_wall(*p)) == pytest.approx(p)


def test_wall_map_reports_a_bad_point_and_refuses_a_line():
    pairs = [(wall_to_lidar(*w), w) for w in targets(W, H)]
    (lx, ly), w = pairs[2]
    pairs[2] = ((lx + 300, ly), w)  # one throw 30 cm off
    m = WallMap.fit(pairs)
    assert 5 < m.worst_cm < 30 and m.error_cm > 3
    with pytest.raises(ValueError):
        WallMap.fit([((0, 0), (0, 0)), ((100, 0), (10, 0)), ((200, 0), (20, 0))])
    with pytest.raises(ValueError):
        WallMap.fit(pairs[:2])


def test_read_points_leaves_out_broken_entries():
    good = {"wall_cm": [25, 25], "lidar_mm": [250, 3750]}
    assert read_points([good, {"wall_cm": [1]}, "x", {"wall_cm": [1, 2], "lidar_mm": [float("nan"), 1]}]) == \
        [((250.0, 3750.0), (25.0, 25.0))]
    assert read_points(None) == []


# -- detector + source on made-up scans -------------------------------------------------------
def test_a_ball_becomes_one_impact_at_its_wall_position(tmp_path):
    src = make_source(tmp_path, calibration_points=exact_points())
    assert src.tuning.calibrated
    det = ready_detector(src)
    t0 = time.monotonic()
    src._process(det, make_scan(t=t0))  # empty wall: nothing
    for i in range(3):  # the ball is seen in 3 rotations: still one hit
        src._process(det, make_scan(ball=(1000.0, 2000.0), t=t0 + 0.1 * (i + 1)))
    (ev,) = drain(src.out)
    assert isinstance(ev, Impact) and ev.source == "lidar"
    assert (ev.x * W, ev.y * H) == pytest.approx(lidar_to_wall(1000.0, 2000.0), abs=3.0)
    assert ev.raw[0] == pytest.approx(1000.0, abs=40) and abs(ev.t - time.perf_counter()) < 1.0
    assert src.packets == 4 and src.events == 1


def test_hits_off_the_wall_do_not_count_once_calibrated(tmp_path):
    # the tool's area reaches 1 m past the left of the wall
    src = make_source(tmp_path, {"area_x": 1000, "width": 4000}, calibration_points=exact_points())
    det = ready_detector(src)
    src._process(det, make_scan(ball=(-800.0, 2000.0)))  # 80 cm left of the wall
    assert drain(src.out) == []
    src.set_capture(True)  # while calibrating the tool's area counts too
    src._process(det, make_scan(ball=(-800.0, 2000.0), t=time.monotonic() + 1))
    assert len(drain(src.out)) == 1


def test_without_calibration_the_tool_area_stands_for_the_wall(tmp_path):
    src = make_source(tmp_path)
    assert not src.tuning.calibrated
    det = ready_detector(src)
    src._process(det, make_scan(ball=(1000.0, 2000.0)))
    (ev,) = drain(src.out)
    assert (ev.x * W, ev.y * H) == pytest.approx(lidar_to_wall(1000.0, 2000.0), abs=3.0)


def test_missing_tuning_file_uses_ball_mode(tmp_path):
    src = LidarSource(queue.SimpleQueue(), lidar_cfg(), tmp_path / "none.json", W, H)
    assert src.tuning.mode == "ball"


class FakeLidar:
    """Stands in for driver.D800: a clear wall, then one ball once the test asks for it."""

    instances = []

    def __init__(self, port, baud=921600, max_range=5000, **kw):
        self.port, self.max_range = port, max_range
        self.ser = object()
        self.scan_count = 0
        self.split_angle = 0.0
        self.ball = None
        self.closed = False
        FakeLidar.instances.append(self)

    def get_scans(self, timeout=0.2):
        time.sleep(0.01)
        self.scan_count += 1
        ball, self.ball = self.ball, None
        return [make_scan(ball=ball)]

    def close(self):
        self.closed = True


def wait_for(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_the_thread_scans_the_background_then_reports_hits(tmp_path, monkeypatch):
    FakeLidar.instances.clear()
    monkeypatch.setattr(driver, "D800", FakeLidar)
    src = make_source(tmp_path, calibration_points=exact_points())
    src.tuning.calibration_time = 0.3  # the tool's minimum is 2 s
    src.start()
    try:
        assert wait_for(lambda: src.state == "background")
        assert "keep the wall clear" in src.status()["detail"]
        assert wait_for(lambda: src.state == "listening")
        fake = FakeLidar.instances[0]
        assert fake.port == "COM_TEST" and fake.max_range == 8000
        fake.ball = (1500.0, 1000.0)
        assert wait_for(lambda: src.events == 1)
        (ev,) = drain(src.out)
        assert (ev.x * W, ev.y * H) == pytest.approx(lidar_to_wall(1500.0, 1000.0), abs=3.0)
        src.rescan()
        assert wait_for(lambda: src.state == "background")
        assert wait_for(lambda: src.state == "listening")
    finally:
        src.stop()
    assert wait_for(lambda: FakeLidar.instances[0].closed)


def test_manager_finds_the_tuning_file_in_the_game_folder(tmp_path):
    cfg = game_cfg({"input": {"sources": ["lidar"]}})
    m = InputManager(cfg, lambda raw: raw, roots=(tmp_path, ROOT))
    assert m.lidar is not None and m.lidar.tuning_file == ROOT / "config" / "lidar.json"
    assert InputManager(game_cfg({"input": {"sources": []}}), lambda raw: raw).lidar is None


# -- in the game ------------------------------------------------------------------------------
def make_game(tmp_path, lidar_over=None, settings=True):
    over = {"display": {"resolution": [768, 1024]}, "input": {"sources": [], "lidar": lidar_over or {}},
            "camera": {"enabled": False}, "flow": {"registration": False, "intro_s": 0, "countdown_s": 1.0},
            "results": {"log_file": str(tmp_path / "results.jsonl")}}
    clock = FakeTime()
    file = tmp_path / "settings.json" if settings else None
    app = App(game_cfg(over), Paths(ROOT), clock_source=clock, start_inputs=False, settings_file=file)
    app.running = True  # as run() does
    src = LidarSource(app.input.queue, app.cfg.input.lidar, tuning_file(tmp_path), W, H)
    app.input.sources.append(src)
    return app, clock, src


def lidar_hit(app, clock, wx, wy):
    """What the lidar thread puts on the queue for a ball at (wx, wy) cm."""
    app.input.queue.put(Impact(wx / W, wy / H, clock.t, "lidar", wall_to_lidar(wx, wy)))


def key(app, k):
    app._handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0), app.clock.now())


def test_lidar_hits_count_only_in_the_round(tmp_path):
    app, clock, _ = make_game(tmp_path)
    sb = app.cfg.layout.start_button
    lidar_hit(app, clock, sb.x_cm, sb.y_cm)  # on the start square, before the round
    step(app, clock, 0.2)
    assert app.flow.state == F.ATTRACT
    app.flow.request_start(app.clock.now())  # as the phone page does
    step(app, clock, 1.6)
    assert app.flow.state == F.PLAYING
    lidar_hit(app, clock, 150, 380)  # a miss in the footer
    step(app, clock, 0.2)
    assert app.flow.round.throws == 1


def test_round_only_off_lets_the_lidar_press_start(tmp_path):
    app, clock, _ = make_game(tmp_path, {"round_only": False})
    sb = app.cfg.layout.start_button
    lidar_hit(app, clock, sb.x_cm, sb.y_cm)
    step(app, clock, 0.2)
    assert app.flow.state != F.ATTRACT


def test_calibration_with_f2_then_c_fits_saves_and_applies(tmp_path):
    app, clock, src = make_game(tmp_path)
    key(app, pygame.K_F2)
    key(app, pygame.K_c)
    assert app.capture is not None and src.tuning.capture
    crosses = targets(W, H)
    # the lidar is turned a little compared to the made-up one: the fit has to find it
    seen = [(x + 0.02 * y, y - 0.02 * x) for x, y in (wall_to_lidar(*c) for c in crosses)]
    for i, (mx, my) in enumerate(seen[:2]):
        clock.t += 0.5
        app.input.recent.append((mx, my, 0, 0, True, "lidar", time.perf_counter()))
        step(app, clock, 0.1)
        assert app.capture.index == i + 1
    key(app, pygame.K_BACKSPACE)  # redo the second cross
    assert app.capture.index == 1
    for mx, my in seen[1:]:
        app.input.recent.append((mx, my, 0, 0, True, "lidar", time.perf_counter()))
        step(app, clock, 0.1)
    assert app.capture is None and not src.tuning.capture and src.tuning.calibrated
    assert src.tuning.wall_map.error_cm < 0.5
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    points = saved["input"]["lidar"]["calibration_points"]
    assert [p["wall_cm"] for p in points] == [list(c) for c in crosses]
    assert app.cfg.input.lidar.calibration_points == points
    assert "calibrated" in app._notice[0] and "saved" in app._notice[0]
    assert app.running


def test_capture_ignores_older_and_other_hits_and_esc_cancels(tmp_path):
    app, clock, src = make_game(tmp_path, settings=False)
    app.input.recent.append((1.0, 2.0, 0, 0, True, "lidar", time.perf_counter() - 5))  # before C
    key(app, pygame.K_F2)
    key(app, pygame.K_c)
    app.input.recent.append((0.5, 0.5, 0, 0, True, "udp_json", time.perf_counter()))
    step(app, clock, 0.1)
    assert app.capture.index == 0
    key(app, pygame.K_ESCAPE)
    assert app.capture is None and app.running and not src.tuning.capture
    key(app, pygame.K_c)
    key(app, pygame.K_F2)  # closing F2 also cancels
    assert app.capture is None and not app.calibration


def test_f6_rescans_and_overlays_draw(tmp_path):
    app, clock, src = make_game(tmp_path)
    key(app, pygame.K_F6)
    assert src._rescan.is_set() and "background" in app._notice[0]
    key(app, pygame.K_F2)
    key(app, pygame.K_c)
    app.input.recent.append((900.0, 1200.0, 90, 280, False, "lidar", time.perf_counter()))
    app.debug = True
    step(app, clock, 0.1)
    app.draw(app.clock.now())  # F2 with the capture, F3 with the lidar's line


def test_c_without_a_lidar_says_so(tmp_path):
    app, clock, src = make_game(tmp_path)
    app.input.sources.remove(src)
    key(app, pygame.K_F2)
    key(app, pygame.K_c)
    assert app.capture is None and "No LiDAR" in app._notice[0]


def test_capture_targets_are_the_grid_crosses():
    cap = Capture(W, H, since=0.0)
    assert cap.target() == (25.0, 25.0) and len(cap.targets) == 4
    cap.feed([(100, 200, 0, 0, True, "lidar", 1.0), (3000, 400, 0, 0, True, "lidar", 2.0)])
    assert cap.seen == [(100, 200), (3000, 400)] and cap.since == 2.0
    cap.feed([(3000, 400, 0, 0, True, "lidar", 2.0)])  # the same entry again
    assert cap.index == 2


# -- the first calibration at the wall (2026-11-03): crosses 3 and 4 both took a stray spot ---------
REAL_BAD = [(-568.9, 1103.5), (1063.1, 1320.2), (2743.9, 871.5), (2757.3, 872.4)]


def test_a_spot_already_taken_is_not_the_next_cross():
    cap = Capture(W, H, since=0.0)
    cap.feed([(x, y, 0, 0, True, "lidar", i + 1.0) for i, (x, y) in enumerate(REAL_BAD)])
    # the stray 3rd lies right of cross 2, not below it: left out, and its twin 13 mm away too
    assert cap.seen == REAL_BAD[:2] and cap.strays == 2 and not cap.done


# -- the live test (2026-11-03 23:20): someone at the wall's left side was taken as crosses 1 and 2 -----
LEFT_SIDE = [(-629, 812), (-602, 1261)]
REAL_GOOD = [(-621.8, 1783.7), (409.6, 1805.1), (509.2, 330.4), (-585.6, 304.5)]  # the try after: 3.7 cm


def test_cross_2_one_above_cross_1_is_not_the_top_edge():
    cap = Capture(W, H, since=0.0)
    cap.feed([(x, y, 0, 0, True, "lidar", i + 1.0) for i, (x, y) in enumerate(LEFT_SIDE)])
    assert cap.seen == LEFT_SIDE[:1] and cap.strays == 1 and cap.misses == 1


def test_crosses_along_the_wall_edges_are_taken():
    cap = Capture(W, H, since=0.0)
    cap.feed([(x, y, 0, 0, True, "lidar", i + 1.0) for i, (x, y) in enumerate(REAL_GOOD)])
    assert cap.done and cap.strays == 0 and cap.fit().worst_cm < 8


def test_the_way_along_the_edges_comes_from_the_calibration_made_before():
    """A lidar turned a quarter round: the top edge runs along its y. The old calibration knows."""
    turned = [((y, -x), w) for (x, y), w in [(wall_to_lidar(*w), w) for w in targets(W, H)]]
    layout = WallMap.fit(turned)
    hits = [p for p, _ in turned]
    cap =Capture(W, H, since=0.0, layout=layout)
    cap.feed([(x, y, 0, 0, True, "lidar", i + 1.0) for i, (x, y) in enumerate(hits)])
    assert cap.done and cap.strays == 0
    plain = Capture(W, H, since=0.0)  # without it, x is taken as the wall's right
    plain.feed([(x, y, 0, 0, True, "lidar", i + 1.0) for i, (x, y) in enumerate(hits)])
    assert plain.index == 1


def test_misses_after_a_wrong_cross_say_backspace_takes_it_back(tmp_path):
    app, clock, src = make_game(tmp_path)
    key(app, pygame.K_F2)
    key(app, pygame.K_c)
    for mx, my in [REAL_GOOD[0], (-600.0, 1000.0), (-500.0, 600.0), (-650.0, 400.0)]:  # all below cross 1
        app.input.recent.append((mx, my, 0, 0, True, "lidar", time.perf_counter()))
        step(app, clock, 0.1)
    assert app.capture.index == 1 and app.capture.misses == 3 and "Backspace" in app._notice[0]
    key(app, pygame.K_BACKSPACE)
    assert app.capture.index == 0 and app.capture.misses == 0


def test_a_fit_far_off_the_crosses_is_refused():
    cap = Capture(W, H, since=0.0)
    cap.seen = [REAL_BAD[0], REAL_BAD[1], REAL_BAD[2], (300.0, 3000.0)]
    with pytest.raises(ValueError, match="cm off its cross"):
        cap.fit()
    cap.seen = [wall_to_lidar(*w) for w in cap.targets]
    cap.seen[2] = (cap.seen[2][0] + 150, cap.seen[2][1])  # 15 cm off: a normal throw
    assert cap.fit().worst_cm < 8


def test_capture_keeps_to_the_tool_area_and_the_wall(tmp_path):
    src = make_source(tmp_path)  # tool area: the rectangle 0..3000 x 0..4000 mm
    src.set_capture(True)
    far = np.array([4800.0]), np.array([1500.0])  # outside both: open space a few metres away
    near = np.array([1000.0]), np.array([2000.0])
    assert not src.tuning.in_area(*far, np.hypot(*far))[0] and src.tuning.in_area(*near, np.hypot(*near))[0]
    src.set_calibration([(wall_to_lidar(*w), w) for w in targets(W, H)])
    shifted = np.array([3100.0]), np.array([2000.0])  # just past the tool area, on the wall's margin
    assert src.tuning.in_area(*shifted, np.hypot(*shifted))[0]


def test_bad_calibration_in_the_game_is_not_saved(tmp_path, monkeypatch):
    app, clock, src = make_game(tmp_path)
    key(app, pygame.K_F2)
    key(app, pygame.K_c)
    for mx, my in [REAL_BAD[0], REAL_BAD[1], REAL_BAD[2], (300.0, 3000.0)]:
        app.input.recent.append((mx, my, 0, 0, True, "lidar", time.perf_counter()))
        step(app, clock, 0.1)
    assert app.capture.index == 2 and app.capture.strays == 2 and "not near cross 3" in app._notice[0]
    monkeypatch.setattr("market_pulse.input.lidar.capture.FIT_RANGE", (0.0, float("inf")))
    monkeypatch.setattr("market_pulse.input.lidar.capture.MAX_TURN_DEG", 180.0)
    app.input.recent.append((2743.9, 871.0, 0, 0, True, "lidar", time.perf_counter()))
    step(app, clock, 0.1)
    app.input.recent.append((300.0, 2900.0, 0, 0, True, "lidar", time.perf_counter()))  # taken now: the fit fails
    step(app, clock, 0.1)
    assert not src.tuning.calibrated and app.capture is not None and app.capture.index == 0
    assert "failed" in app._notice[0] and not (tmp_path / "settings.json").exists()


def test_a_repeated_spot_in_the_game_says_so(tmp_path):
    app, clock, src = make_game(tmp_path)
    key(app, pygame.K_F2)
    key(app, pygame.K_c)
    for mx, my in REAL_BAD[2:]:
        app.input.recent.append((mx, my, 0, 0, True, "lidar", time.perf_counter()))
        step(app, clock, 0.1)
    assert app.capture.index == 1 and "already taken" in app._notice[0]


def test_a_new_wall_shape_drops_the_calibration_until_the_wall_is_that_size_again(tmp_path):
    src = make_source(tmp_path, calibration_points=exact_points())
    assert src.tuning.calibrated
    assert src.set_wall(W, H * 1.2) is False  # a portrait screen made the wall taller: calibrate again
    assert not src.tuning.calibrated and (src.tuning.wall_w, src.tuning.wall_h) == (W, H * 1.2)
    assert src.set_wall(W, H) is True and src.tuning.calibrated  # back to the size it was made for


def test_a_spot_that_fires_by_itself_is_not_taken_as_a_cross():
    # the real log: a phantom at about (2910, 38) mm every few seconds, also before C was pressed
    cap = Capture(W, H, since=39.9)
    recent = [(2896, 35, 0, 0, False, "lidar", 27.0), (2913, 36, 0, 0, False, "lidar", 27.9),
              (2930, 38, 0, 0, False, "lidar", 30.9), (2875, 38, 0, 0, False, "lidar", 40.6)]
    assert not cap.feed(recent) and cap.index == 0 and cap.noise == 1 and cap.noise_at == (2875, 38)
    recent.append((448, 1746, 0, 0, False, "lidar", 50.9))  # the player's throw at cross 1
    assert cap.feed(recent) and cap.seen == [(448, 1746)]
    cap2 = Capture(W, H, since=0.0)  # once, or twice, at one spot is still a throw
    assert cap2.feed([(2896, 35, 0, 0, False, "lidar", 1.0)]) and cap2.noise == 0


def test_an_old_calibration_for_another_shape_shows_where_the_wall_is_while_calibrating_again(tmp_path):
    src = make_source(tmp_path, calibration_points=exact_points())
    assert src.set_wall(W, H * 1.2) is False and src.tuning.guide is not None
    assert src.tuning.guide_size == pytest.approx((W, H))
    src.set_capture(True)
    beside, far = wall_to_lidar(W + 50, 200), wall_to_lidar(W + 200, 200)  # both outside the tool's area
    assert src.tuning.in_area(np.array([beside[0], far[0]]), np.array([beside[1], far[1]]), np.array([2000.0, 2000.0])).tolist() == [True, False]
    assert src.set_wall(W, H) is True and src.tuning.guide is None
