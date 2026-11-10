"""Camera + LiDAR fusion (market_pulse/input/fusion.py): one hit per throw, LiDAR coordinates first.

The FusionManager is tested alone with a fake clock, and through the InputManager with fake sensor
sources (no serial port, no camera), the way the game uses it.
"""
import math
import queue

import pytest

from market_pulse import config
from market_pulse.input.events import Impact
from market_pulse.input.fusion import CAMERA, LIDAR, Detection, FusionManager
from market_pulse.input.manager import InputManager, WallImpact
from market_pulse.input.sources import Source
from conftest import game_cfg

WINDOW = 0.5


class Sensors:
    """Which sensors are able to report now (FusionManager's alive callback)."""

    def __init__(self, lidar=True, camera=True):
        self.on = {LIDAR: lidar, CAMERA: camera}

    def __call__(self, name):
        return self.on.get(name, False)


def fusion(lidar=True, camera=True, **kw):
    sensors = Sensors(lidar, camera)
    return FusionManager(window_s=WINDOW, match_radius_cm=60, keep_s=1.0, alive=sensors, **kw), sensors


def cam(x, y, t):
    return Detection(CAMERA, x, y, t, (x, y))


def lid(x, y, t, trusted=True):
    return Detection(LIDAR, x, y, t, (x * 10, y * 10), trusted)


def run(fm, events, until):
    """events: (arrival time, detection). Feeds them in order, polling every 1/60 s like the game
    does, up to ``until``. Returns [(registration time, hit)]."""
    out = []
    events = sorted(events, key=lambda e: e[0])
    now, step, i = 0.0, 1 / 60, 0
    while now <= until + 1e-9:
        while i < len(events) and events[i][0] <= now + 1e-9:
            out += [(now, h) for h in fm.feed(events[i][1], now)]
            i += 1
        out += [(now, h) for h in fm.poll(now)]
        now += step
    return out


# -- the eight scenarios ------------------------------------------------------------------------------
def test_1_camera_first_lidar_within_window_uses_lidar_and_registers_on_match():
    fm, _ = fusion()
    # contact at t=1.00; camera arrives at 1.10, LiDAR (its own clock, 40 ms off) at 1.30
    hits = run(fm, [(1.10, cam(100, 200, 1.00)), (1.30, lid(104, 197, 1.04))], until=3)
    assert len(hits) == 1
    at, h = hits[0]
    assert (h.x, h.y, h.t, h.source) == (104, 197, 1.04, LIDAR)
    assert h.sensors == (CAMERA, LIDAR) and h.reason == "matched"
    assert at == pytest.approx(1.30, abs=1 / 60)  # as soon as the LiDAR came, not at the end of the window
    assert fm.stats.fused == 1 and fm.stats.camera_only == 0


def test_2_lidar_first_camera_within_window_registers_at_once_and_absorbs_camera():
    fm, _ = fusion()
    hits = run(fm, [(1.05, lid(50, 60, 1.00)), (1.40, cam(70, 40, 1.02))], until=3)
    assert len(hits) == 1
    at, h = hits[0]
    assert at == pytest.approx(1.05, abs=1 / 60)  # no waiting for the camera
    assert (h.x, h.y, h.source) == (50, 60, LIDAR)
    assert fm.stats.duplicates == 1


def test_3_camera_alone_counts_with_camera_coordinates_when_the_window_runs_out():
    fm, _ = fusion()
    hits = run(fm, [(1.10, cam(120, 80, 1.00))], until=3)
    assert len(hits) == 1
    at, h = hits[0]
    assert (h.x, h.y, h.t, h.source, h.reason) == (120, 80, 1.00, CAMERA, "timeout")
    assert at == pytest.approx(1.10 + WINDOW, abs=1 / 60)  # waited the window, no longer
    assert fm.stats.camera_only == 1


def test_4_lidar_alone_counts_at_once():
    fm, _ = fusion()
    hits = run(fm, [(2.0, lid(10, 20, 1.95))], until=4)
    assert [(round(at, 3), h.source, h.reason) for at, h in hits] == [(2.0, LIDAR, "lidar")]
    assert fm.stats.lidar_only == 1


def test_5_both_see_one_throw_exactly_one_hit_in_either_order_and_any_delay_inside_the_window():
    for cam_arrival in (1.01, 1.2, 1.45):
        for lidar_arrival in (1.01, 1.2, 1.45):
            fm, _ = fusion()
            hits = run(fm, [(cam_arrival, cam(150, 150, 1.0)), (lidar_arrival, lid(140, 160, 1.0))], until=4)
            assert len(hits) == 1, (cam_arrival, lidar_arrival)
            assert hits[0][1].source == LIDAR and (hits[0][1].x, hits[0][1].y) == (140, 160)


def test_6_two_quick_throws_are_two_hits_each_with_its_own_lidar_point():
    fm, _ = fusion()
    # throw A at 1.00 (left), throw B 0.30 s later (right). The camera reports both before the
    # LiDAR's first report arrives: the pairs must not cross.
    events = [(1.08, cam(60, 200, 1.00)), (1.36, cam(240, 210, 1.30)),
              (1.40, lid(64, 205, 1.01)), (1.42, lid(236, 214, 1.31))]
    hits = [h for _, h in run(fm, events, until=4)]
    assert sorted((h.x, h.y) for h in hits) == [(64, 205), (236, 214)]
    assert all(h.source == LIDAR and h.sensors == (CAMERA, LIDAR) for h in hits)


def test_6b_two_throws_at_the_same_spot_half_a_second_apart_stay_two():
    fm, _ = fusion()
    events = [(1.05, lid(100, 100, 1.00)), (1.15, cam(103, 98, 1.02)),
              (1.55, lid(102, 104, 1.50)), (1.65, cam(99, 101, 1.52))]
    hits = [h for _, h in run(fm, events, until=4)]
    assert [(h.x, h.y) for h in hits] == [(100, 100), (102, 104)]
    assert fm.stats.duplicates == 2


def test_7_camera_false_detections_never_replace_or_double_a_lidar_hit():
    fm, _ = fusion()
    # a real throw seen by both, and the camera also "sees" a person moving far away
    events = [(1.05, lid(80, 300, 1.00)), (1.10, cam(85, 296, 1.01)), (1.12, cam(260, 40, 1.03))]
    hits = [h for _, h in run(fm, events, until=4)]
    assert [(h.x, h.y, h.source) for h in hits[:1]] == [(80, 300, LIDAR)]
    # the far one matches nothing: it is a camera-alone hit (camera_alone "always", the default) ...
    assert [(h.x, h.y, h.source) for h in hits[1:]] == [(260, 40, CAMERA)]
    # ... and with camera_alone "lidar_offline" the working LiDAR vetoes it
    fm, _ = fusion(camera_alone="lidar_offline")
    hits = [h for _, h in run(fm, events, until=4)]
    assert [(h.x, h.y, h.source) for h in hits] == [(80, 300, LIDAR)]
    assert fm.stats.refused == 1


def test_7b_lidar_false_detection_and_invalid_values():
    fm, _ = fusion()
    hits = run(fm, [(1.0, lid(float("nan"), 10, 1.0)), (1.1, cam(30, 30, float("inf"))),
                    (1.2, lid(200, 100, 1.2))], until=3)
    assert [h.source for _, h in hits] == [LIDAR]  # the stray LiDAR point is one hit; nothing else
    assert fm.stats.rejected == 2


def test_8_a_disconnected_lidar_is_never_waited_for():
    fm, sensors = fusion(lidar=False)
    hits = run(fm, [(1.1, cam(100, 100, 1.0))], until=3)
    assert len(hits) == 1 and hits[0][0] == pytest.approx(1.1, abs=1 / 60)  # at once
    assert hits[0][1].reason == "alone"


def test_8b_lidar_drops_out_while_a_camera_hit_is_waiting():
    fm, sensors = fusion()
    fm.feed(cam(100, 100, 1.0), 1.1)
    assert fm.pending == 1
    sensors.on[LIDAR] = False
    (h,) = fm.poll(1.2)  # before the window ran out
    assert (h.source, h.reason) == (CAMERA, "alone")


def test_8c_a_disconnected_camera_does_not_matter_and_reconnecting_works():
    fm, sensors = fusion(camera=False)
    assert len(run(fm, [(1.0, lid(10, 10, 1.0))], until=2)) == 1
    sensors.on[CAMERA] = True
    hits = run(fm, [(3.0, cam(200, 200, 2.9)), (3.2, lid(205, 205, 2.95))], until=5)
    assert len(hits) == 1 and hits[0][1].sensors == (CAMERA, LIDAR)


# -- the shipped timing (150 ms wait), with the delays measured on the wall ------------------------------
def shipped(**kw):
    sensors = Sensors()
    return FusionManager(window_s=0.15, match_time_s=0.3, match_radius_cm=60, keep_s=1.0, alive=sensors, **kw)


@pytest.mark.parametrize("lag", [0.07, 0.12, 0.19, 0.25])
def test_shipped_lidar_stamping_later_than_the_camera_still_matches(lag):
    """On the wall the LiDAR stamped a contact 0.07-0.19 s after the camera did; the camera's picture
    arrived 110-230 ms after the contact, the LiDAR's report about 10 ms after its stamp."""
    for cam_delay in (0.11, 0.23):
        fm = shipped()
        events = [(1.0 + cam_delay, cam(150, 300, 1.0)), (1.0 + lag + 0.01, lid(160, 330, 1.0 + lag))]
        hits = run(fm, events, until=4)
        assert len(hits) == 1, (lag, cam_delay)
        assert hits[0][1].source == LIDAR


def test_shipped_camera_alone_waits_150_ms_not_more():
    fm = shipped()
    hits = run(fm, [(1.13, cam(150, 300, 1.0))], until=3)
    assert len(hits) == 1 and hits[0][0] == pytest.approx(1.13 + 0.15, abs=2 / 60)  # the game checks once a frame


def test_shipped_lidar_after_the_wait_is_absorbed_not_doubled():
    # camera first (arrives 1.11); the LiDAR's report comes 0.2 s later, after the 150 ms wait
    fm = shipped()
    hits = run(fm, [(1.11, cam(150, 300, 1.0)), (1.31, lid(158, 320, 1.18))], until=4)
    assert len(hits) == 1 and hits[0][1].source == CAMERA
    assert fm.stats.late == 1


# -- more rules -------------------------------------------------------------------------------------
def test_late_lidar_after_a_timed_out_camera_hit_is_absorbed_not_a_second_hit():
    fm, _ = fusion()
    # the LiDAR's report of the same contact comes 0.7 s after the camera's (a stall)
    hits = run(fm, [(1.1, cam(100, 100, 1.0)), (1.8, lid(102, 98, 1.02))], until=4)
    assert len(hits) == 1 and hits[0][1].source == CAMERA
    assert fm.stats.duplicates == 1 and fm.stats.late == 1


def test_far_apart_points_are_two_throws_even_at_the_same_moment():
    fm, _ = fusion()
    hits = run(fm, [(1.05, lid(20, 20, 1.0)), (1.06, cam(280, 380, 1.0))], until=3)
    assert sorted(h.source for _, h in hits) == [CAMERA, LIDAR]


def test_contact_times_too_far_apart_do_not_match():
    fm, _ = fusion()
    hits = run(fm, [(1.05, cam(100, 100, 1.0)), (1.5, lid(100, 100, 1.6))], until=4)
    assert sorted(h.source for _, h in hits) == [CAMERA, LIDAR]


def test_a_sensor_reporting_one_ball_twice_is_one_hit():
    fm, _ = fusion()
    hits = run(fm, [(1.0, cam(100, 100, 0.95)), (1.05, cam(104, 102, 1.0)), (1.3, lid(101, 99, 0.97))], until=3)
    assert len(hits) == 1 and hits[0][1].source == LIDAR
    assert fm.stats.repeats == 1


def test_an_uncalibrated_lidar_waits_for_the_camera_and_takes_its_coordinates():
    fm, _ = fusion()
    # a LiDAR that is not calibrated is mapped through its rough area: tens of cm off
    hits = run(fm, [(1.05, lid(140, 95, 1.0, trusted=False)), (1.2, cam(110, 120, 1.01))], until=3)
    assert len(hits) == 1 and (hits[0][1].x, hits[0][1].source) == (110, CAMERA)
    fm, _ = fusion()
    hits = run(fm, [(1.05, lid(90, 90, 1.0, trusted=False))], until=3)  # no camera: the rough point after all
    assert len(hits) == 1 and hits[0][1].source == LIDAR and hits[0][0] == pytest.approx(1.55, abs=1 / 60)


def test_window_zero_and_disabled_fusion():
    fm, _ = fusion()
    fm.window_s = 0
    assert len(fm.feed(cam(1, 1, 1), 1.0)) == 1
    fm, _ = fusion(enabled=False)
    assert len(fm.feed(cam(1, 1, 1), 1.0)) == 1 and len(fm.feed(lid(1, 1, 1), 1.0)) == 1  # both count


def test_other_sources_pass_straight_through():
    fm, _ = fusion()
    (h,) = fm.feed(Detection("udp_json", 5, 6, 1.0), 1.0)
    assert (h.source, h.reason) == ("udp_json", "passthrough")


def test_old_throws_are_forgotten():
    fm, _ = fusion()
    run(fm, [(1.0, lid(1, 1, 1.0))], until=1.0)
    fm.poll(10.0)
    assert fm.throws == []


def test_many_throws_each_counted_once():
    """30 throws 0.7 s apart, random sensor delays and misses: every throw is one hit, LiDAR whenever it saw it."""
    import random
    rng = random.Random(7)
    events, expect = [], []
    for k in range(30):
        t = 1.0 + 0.7 * k
        x, y = rng.uniform(20, 280), rng.uniform(20, 460)
        sees_l, sees_c = rng.random() > 0.15, rng.random() > 0.15
        if not (sees_l or sees_c):
            sees_c = True
        if sees_l:
            events.append((t + rng.uniform(0.02, 0.3), lid(x, y, t + rng.uniform(-0.03, 0.03))))
        if sees_c:
            events.append((t + rng.uniform(0.05, 0.35), cam(x + rng.uniform(-15, 15), y + rng.uniform(-15, 15), t)))
        expect.append(LIDAR if sees_l else CAMERA)
    fm, _ = fusion()
    hits = [h for _, h in run(fm, events, until=30)]
    hits.sort(key=lambda h: h.t)
    assert [h.source for h in hits] == expect


# -- through the InputManager (as the game uses it) ---------------------------------------------------
class FakeSensor(Source):
    def __init__(self, out, name):
        super().__init__(out)
        self.name = name
        self.state = "listening"
        self.ready = self.calibrated = True

    def start(self):
        pass

    def stop(self):
        pass


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def wired(**fusion_cfg):
    cfg = game_cfg({"input": {"sources": [], "fusion": fusion_cfg}})
    clock = Clock()
    m = InputManager(cfg, to_game_time=lambda raw: raw - 100.0, clock=clock)
    for name in (LIDAR, CAMERA):
        s = FakeSensor(m.queue, name)
        s.last_packet = clock.t
        m.sources.append(s)
    return m, clock, cfg


def put(m, sensor, x_cm, y_cm, t, cfg, trusted=True):
    W, H = cfg.wall.width_cm, cfg.wall.height_cm
    m.queue.put(Impact(x_cm / W, y_cm / H, t, sensor, (x_cm, y_cm), trusted))


def advance(m, clock, seconds, step=1 / 60):
    out = []
    end = clock.t + seconds
    while clock.t < end - 1e-9:
        clock.t += step
        for s in m.sources:
            s.last_packet = clock.t  # both keep sending data
        out += m.poll()
    return out


def test_manager_camera_then_lidar_is_one_wall_impact_with_lidar_cm_and_game_time():
    m, clock, cfg = wired()
    put(m, CAMERA, 100, 200, 100.00, cfg)
    assert advance(m, clock, 0.1) == []  # the camera hit waits
    put(m, LIDAR, 110, 190, 100.03, cfg)
    (ev,) = advance(m, clock, 0.1)
    assert isinstance(ev, WallImpact)
    assert (round(ev.x), round(ev.y), ev.source, ev.sensors) == (110, 190, LIDAR, (CAMERA, LIDAR))
    assert ev.t == pytest.approx(0.03)  # the LiDAR's contact time, in game time
    assert advance(m, clock, 2.0) == []


def test_manager_camera_alone_after_the_window_and_lidar_offline_immediately():
    m, clock, cfg = wired()  # the shipped wait: 150 ms
    put(m, CAMERA, 100, 200, 100.0, cfg)
    out = advance(m, clock, 0.12)
    assert out == []
    (ev,) = advance(m, clock, 0.1)
    assert ev.source == CAMERA and (round(ev.x), round(ev.y)) == (100, 200)

    m, clock, cfg = wired()
    m.sources[0].state = "error"  # the LiDAR is unplugged
    put(m, CAMERA, 100, 200, 100.0, cfg)
    (ev,) = advance(m, clock, 1 / 60)
    assert ev.source == CAMERA


def test_manager_silent_lidar_counts_as_offline():
    m, clock, cfg = wired(offline_after_ms=300)
    lidar = m.sources[0]
    clock.t += 1.0  # no LiDAR packet for a second
    m.sources[1].last_packet = clock.t
    assert m.sensor_alive(CAMERA) and not m.sensor_alive(LIDAR)
    lidar.last_packet = clock.t
    lidar.ready = False  # learning its background
    assert not m.sensor_alive(LIDAR)
    m.sources[1].calibrated = False
    assert not m.sensor_alive(CAMERA)


def test_manager_settings_change_takes_effect():
    m, clock, cfg = wired()
    cfg.raw()["input"]["fusion"]["window_ms"] = 200
    cfg.raw()["input"]["fusion"]["camera_alone"] = "never"
    m.settings_changed()
    assert m.fusion.window_s == pytest.approx(0.2) and m.fusion.camera_alone == "never"


def test_fusion_settings_are_validated():
    raw = config.Section(config.DEFAULTS).raw()
    assert raw["input"]["fusion"]["window_ms"] == 150 and raw["input"]["fusion"]["match_time_ms"] == 300
    import copy
    bad = copy.deepcopy(config.DEFAULTS)
    warnings = []
    config._merge(bad, {"input": {"fusion": {"window_ms": 99999, "camera_alone": "sometimes"}}}, (), warnings)
    config._validate(bad, warnings)
    assert bad["input"]["fusion"]["window_ms"] == 3000 and bad["input"]["fusion"]["camera_alone"] == "always"
    assert len(warnings) == 2
