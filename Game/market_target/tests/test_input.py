"""Input protocol parsing, calibration, de-duplication and the real UDP / TUIO listeners."""
import json
import math
import queue
import socket
import time

import pytest

from market_pulse.input.events import Calibration, Control, Impact, ImpactFilter
from market_pulse.input.manager import InputManager, WallImpact
from market_pulse.input.sources import TuioSource, UdpJsonSource, parse_json_message
from conftest import make_cfg


def parse(obj, units="normalized"):
    return parse_json_message(obj, units, 300.0, 400.0, 1.0, "test")


# -- JSON protocol -------------------------------------------------------------------------
def test_parse_normalized_impact():
    (ev,) = parse({"type": "impact", "x": 0.25, "y": 0.5})
    assert isinstance(ev, Impact) and (ev.x, ev.y) == (0.25, 0.5) and ev.source == "test"


def test_parse_hit_alias_and_units():
    (cm,) = parse({"type": "hit", "x": 150, "y": 100}, units="cm")
    assert (cm.x, cm.y) == (0.5, 0.25) and cm.raw == (150.0, 100.0)
    (m,) = parse({"type": "impact", "x": 1.5, "y": 2.0}, units="m")
    assert (m.x, m.y) == pytest.approx((0.5, 0.5))


def test_parse_batch():
    evs = parse({"type": "impacts", "points": [{"x": 0.1, "y": 0.2}, {"x": 0.9, "y": 0.8}]})
    assert [(e.x, e.y) for e in evs] == [(0.1, 0.2), (0.9, 0.8)]


@pytest.mark.parametrize("kind", ["start", "reset", "player", "throw_missed", "heartbeat"])
def test_parse_controls(kind):
    (ev,) = parse({"type": kind, "id": "42", "name": "Sara"})
    assert isinstance(ev, Control) and ev.kind == kind and ev.data == {"id": "42", "name": "Sara"}


@pytest.mark.parametrize(
    "bad",
    [
        [1, 2],
        "impact",
        {"x": 0.5, "y": 0.5},
        {"type": "explode"},
        {"type": "impact", "x": "0.5", "y": 0.5},
        {"type": "impact", "x": True, "y": 0.5},
        {"type": "impact", "x": 0.5},
        {"type": "impact", "x": float("nan"), "y": 0.5},
        {"type": "impact", "x": float("inf"), "y": 0.5},
        {"type": "impacts", "points": {"x": 1}},
        {"type": "impacts", "points": [3]},
    ],
)
def test_parse_rejects_bad_messages(bad):
    with pytest.raises(ValueError):
        parse(bad)


# -- calibration and filtering --------------------------------------------------------------------
def test_calibration_transforms():
    assert Calibration().apply(0.2, 0.3) == (0.2, 0.3)
    assert Calibration(swap_xy=True).apply(0.2, 0.3) == (0.3, 0.2)
    assert Calibration(flip_x=True, flip_y=True).apply(0.2, 0.3) == pytest.approx((0.8, 0.7))
    assert Calibration(scale_x=0.5, offset_x=0.1, scale_y=2.0, offset_y=-0.1).apply(0.4, 0.3) == pytest.approx(
        (0.3, 0.5)
    )


def test_filter_merges_repeated_reports_of_one_contact():
    f = ImpactFilter(0.25, 35.0, 0.6, 40.0)
    assert f.accept(100, 100, 0.00)
    assert not f.accept(105, 98, 0.05)  # same contact reported again
    assert f.merged == 1
    assert f.accept(250, 100, 0.10)  # somewhere else: a different ball


def test_filter_drops_rebound_below_the_hit():
    f = ImpactFilter(0.25, 35.0, 0.6, 40.0)
    assert f.accept(100, 150, 0.0)
    assert not f.accept(110, 260, 0.45)  # dropped down the wall
    assert f.rebounds == 1
    assert f.accept(100, 260, 1.2)  # after the window it is a new throw


def test_filter_accepts_a_new_throw_above_or_aside():
    f = ImpactFilter(0.25, 35.0, 0.6, 40.0)
    assert f.accept(100, 250, 0.0)
    assert f.accept(100, 120, 0.4)  # higher on the wall: not a rebound
    assert f.accept(220, 200, 0.7)  # far to the side


# -- manager ---------------------------------------------------------------------------------------
def manager(overrides=None, latency_ms=0):
    cfg = make_cfg({"input": {"sources": [], "latency_ms": latency_ms, **(overrides or {})}})
    return InputManager(cfg, to_game_time=lambda raw: raw - 1000.0)


def test_manager_converts_to_wall_cm_and_game_time():
    m = manager(latency_ms=40)
    m.queue.put(Impact(0.5, 0.25, 1010.0, "udp_json", (0.5, 0.25)))
    (ev,) = m.poll()
    assert isinstance(ev, WallImpact)
    assert (ev.x, ev.y) == (150.0, 100.0)
    assert ev.t == pytest.approx(10.0 - 0.04)


def test_manager_applies_calibration_to_sensors_only():
    m = manager({"calibration": {"flip_x": True}, "simulation": True})
    m.queue.put(Impact(0.1, 0.5, 1001.0, "udp_json"))
    m.queue.put(Impact(0.1, 0.5, 1005.0, "simulation"))
    a, b = m.poll()
    assert a.x == pytest.approx(270.0)  # flipped
    assert b.x == pytest.approx(30.0)  # mouse clicks are already in wall space


def test_manager_rejects_out_of_range_and_clamps_edge_noise():
    m = manager()
    m.queue.put(Impact(1.3, 0.5, 1001.0, "udp_json"))
    m.queue.put(Impact(1.03, -0.02, 1003.0, "udp_json"))
    (ev,) = m.poll()
    assert m.out_of_range == 1
    assert (ev.x, ev.y) == (300.0, 0.0)


def test_manager_passes_controls_through():
    m = manager()
    m.queue.put(Control("start", 1.0, "udp_json", {}))
    (ev,) = m.poll()
    assert isinstance(ev, Control) and ev.kind == "start"


def test_simulated_clicks_are_ignored_unless_simulation_is_on():
    m = manager()
    m.push_simulated(0.5, 0.5)
    assert m.poll() == []


def test_sensor_offline_needs_a_heartbeat_timeout():
    cfg = make_cfg({"input": {"sources": ["udp_json"], "heartbeat_timeout_s": 5}})
    m = InputManager(cfg, lambda raw: raw)
    assert m.sensor_offline()  # nothing ever received
    m.sources[0].last_packet = time.perf_counter()
    assert not m.sensor_offline()
    cfg0 = make_cfg({"input": {"sources": ["udp_json"]}})
    assert not InputManager(cfg0, lambda raw: raw).sensor_offline()


# -- real sockets ------------------------------------------------------------------------------------
def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(cond, timeout=3.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_udp_json_listener_end_to_end():
    port = free_udp_port()
    q = queue.SimpleQueue()
    src = UdpJsonSource(q, "127.0.0.1", port, "normalized", 300.0, 400.0)
    src.start()
    try:
        assert wait_for(lambda: src.state == "listening")
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(json.dumps({"type": "impact", "x": 0.3, "y": 0.6}).encode(), ("127.0.0.1", port))
        tx.sendto(b"not json at all", ("127.0.0.1", port))
        tx.sendto(b"\xff\xfe\x00", ("127.0.0.1", port))
        two_lines = json.dumps({"type": "start"}) + "\n" + json.dumps({"type": "impact", "x": 0.7, "y": 0.2})
        tx.sendto(two_lines.encode(), ("127.0.0.1", port))
        tx.close()
        assert wait_for(lambda: src.packets == 4)
        assert wait_for(lambda: src.events == 3)
        evs = drain(q)
        assert [type(e).__name__ for e in evs] == ["Impact", "Control", "Impact"]
        assert (evs[0].x, evs[0].y) == (0.3, 0.6)
        assert src.bad_packets == 2
        assert src.status()["state"] == "listening"
    finally:
        src.stop()
        src._thread.join(2.0)
    assert not src._thread.is_alive()


def test_udp_port_in_use_is_reported_not_raised():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    port = blocker.getsockname()[1]
    q = queue.SimpleQueue()
    src = UdpJsonSource(q, "127.0.0.1", port, "normalized", 300.0, 400.0)
    try:
        src.start()
        assert wait_for(lambda: src.state == "error")
        assert src.error
    finally:
        src.stop()
        src._thread.join(3.0)
        blocker.close()
    assert not src._thread.is_alive()


def test_tuio_listener_one_impact_per_new_session():
    pytest.importorskip("pythonosc")
    from pythonosc.udp_client import SimpleUDPClient

    port = free_udp_port()
    q = queue.SimpleQueue()
    src = TuioSource(q, "127.0.0.1", port)
    src.start()
    try:
        assert wait_for(lambda: src.state == "listening")
        c = SimpleUDPClient("127.0.0.1", port)
        c.send_message("/tuio/2Dcur", ["alive", 7])
        c.send_message("/tuio/2Dcur", ["set", 7, 0.25, 0.75, 0.0, 0.0, 0.0])
        c.send_message("/tuio/2Dcur", ["set", 7, 0.26, 0.76, 0.0, 0.0, 0.0])  # same ball moving
        c.send_message("/tuio/2Dcur", ["alive"])  # ball gone
        c.send_message("/tuio/2Dobj", ["set", 9, 3, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        c.send_message("/tuio/2Dcur", ["set", 8, 0.9, 0.1, 0.0, 0.0, 0.0])
        c.send_message("/tuio/2Dcur", ["set"])  # malformed
        assert wait_for(lambda: src.packets == 7)
        evs = drain(q)
        got = [v for e in evs for v in (e.x, e.y)]
        assert got == pytest.approx([0.25, 0.75, 0.5, 0.5, 0.9, 0.1], abs=1e-6)  # OSC floats are 32-bit
        assert all(e.source == "tuio" for e in evs)
        assert src.bad_packets == 1
        assert all(math.isfinite(e.t) for e in evs)
    finally:
        src.stop()
        src._thread.join(2.0)
    assert not src._thread.is_alive()
