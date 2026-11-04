"""Screen flow, the pausable clock and the results log."""
import json
import time

import numpy as np

from market_pulse.game import flow as F
from market_pulse.game.results import ResultsStore
from conftest import make_cfg


class FakeTime:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def names(flow):
    return [e.name for e in flow.drain_events()]


def run(flow, start, seconds, step=1 / 60):
    """Call update() every frame for ``seconds``; return the time reached."""
    t = start
    for _ in range(int(round(seconds / step))):
        t += step
        flow.update(t)
    return t


def new_flow(tmp_path=None, overrides=None):
    cfg = make_cfg(overrides)
    store = ResultsStore(tmp_path / "results.jsonl", 5) if tmp_path else None
    return cfg, F.Flow(cfg, store, rng=np.random.default_rng(2))


def test_clock_stands_still_while_paused():
    src = FakeTime(10.0)
    clock = F.Clock(src)
    assert clock.now() == 10.0
    clock.pause()
    src.t = 15.0
    assert clock.now() == 10.0 and clock.paused
    clock.resume()
    assert clock.now() == 10.0
    src.t = 16.0
    assert clock.now() == 11.0
    assert clock.to_game(16.0) == 11.0
    assert clock.to_game(99.0) == 11.0  # never ahead of the game clock


def test_full_round_through_every_screen(tmp_path):
    cfg, flow = new_flow(tmp_path)
    got = []
    flow.on_result = got.append
    assert flow.state == F.ATTRACT
    assert flow.request_start(0.0)
    assert flow.state == F.INSTRUCTIONS
    t = run(flow, 0.0, cfg.flow.instructions_s + 0.02)
    assert flow.state == F.COUNTDOWN
    t = run(flow, t, cfg.flow.countdown_s + 0.05)
    ev = flow.drain_events()
    counts = [e.data["n"] for e in ev if e.name == "count"]
    assert counts == [3, 2, 1]
    assert "go" in [e.name for e in ev] and flow.state == F.PLAYING
    assert flow.round.running

    rnd = flow.round
    tg = next(x for x in rnd.targets if x.color == "red")
    t += 1.0
    flow.update(t)
    out = flow.handle_impact(*tg.center_at(t), t, t)
    assert out.kind == "hit" and "hit" in names(flow)

    t = run(flow, t, 60.0)
    ev = flow.drain_events()
    warnings = [e.data["seconds"] for e in ev if e.name == "time_warning"]
    assert warnings == list(range(10, 0, -1))
    assert flow.state == F.ENDING
    assert [e.data for e in ev if e.name == "end"] == [{"reason": "time"}]

    t = run(flow, t, cfg.round.end_delay_s + 0.05)
    assert flow.state == F.RESULTS
    assert flow.summary["score"] == 30 and flow.rank == 1
    assert got and got[0]["score"] == 30
    lines = (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["score"] == 30

    run(flow, t, cfg.flow.results_s + 0.05)
    assert flow.state == F.ATTRACT


def test_round_ends_early_when_balls_run_out(tmp_path):
    cfg, flow = new_flow(tmp_path, {"flow": {"show_instructions": False}})
    flow.request_start(0.0)
    assert flow.state == F.COUNTDOWN
    t = run(flow, 0.0, cfg.flow.countdown_s + 0.05)
    for _ in range(cfg.round.balls):
        t += 0.5
        flow.handle_impact(150.0, 390.0, t, t)
    flow.update(t + 0.01)
    assert flow.state == F.ENDING and flow.round.end_reason == "balls"


def test_start_by_throw_only_on_the_start_box():
    cfg, flow = new_flow()
    sb = cfg.layout.start_button
    flow.handle_impact(20.0, 20.0, 1.0, 1.0)
    assert flow.state == F.ATTRACT
    flow.handle_impact(sb.x_cm + sb.size_cm / 2, sb.y_cm, 1.0, 1.0)  # edge of the box
    assert flow.state == F.INSTRUCTIONS
    assert "start_hit" in names(flow)


def test_start_by_throw_can_be_disabled():
    cfg, flow = new_flow(overrides={"flow": {"start_by_throw": False}})
    sb = cfg.layout.start_button
    flow.handle_impact(sb.x_cm, sb.y_cm, 1.0, 1.0)
    assert flow.state == F.ATTRACT


def test_impacts_outside_play_do_not_count():
    cfg, flow = new_flow(overrides={"flow": {"show_instructions": False}})
    flow.request_start(0.0)
    assert flow.handle_impact(150.0, 200.0, 0.5, 0.5) is None  # during the countdown
    assert flow.round.throws == 0


def test_operator_start_skips_instructions_and_cannot_restart_a_running_round():
    cfg, flow = new_flow()
    flow.request_start(0.0)
    assert flow.request_start(1.0)  # second press skips the instructions
    assert flow.state == F.COUNTDOWN
    run(flow, 1.0, cfg.flow.countdown_s + 0.05)
    assert flow.state == F.PLAYING
    assert not flow.request_start(5.0)


def test_reset_aborts_the_round(tmp_path):
    cfg, flow = new_flow(tmp_path, {"flow": {"show_instructions": False}})
    flow.request_start(0.0)
    t = run(flow, 0.0, cfg.flow.countdown_s + 0.05)
    rnd = flow.round
    flow.handle_control("reset", {}, t)
    assert flow.state == F.ATTRACT and flow.round is None
    assert rnd.end_reason == "aborted"
    assert not (tmp_path / "results.jsonl").exists()  # aborted rounds are not recorded


def test_player_identity_is_attached_to_the_next_round_only(tmp_path):
    cfg, flow = new_flow(tmp_path, {"flow": {"show_instructions": False}})
    flow.handle_control("player", {"id": "u-17", "name": "  Sara  "}, 0.0)
    assert flow.player == {"id": "u-17", "name": "Sara"}
    flow.request_start(0.0)
    assert flow.player is None and flow.round_player["id"] == "u-17"
    t = run(flow, 0.0, cfg.flow.countdown_s + 0.05)
    flow.operator_missed_throw(t)
    t = run(flow, t, cfg.round.duration_s + cfg.round.end_delay_s + 0.2)
    assert flow.state == F.RESULTS
    assert flow.summary["player_id"] == "u-17" and flow.summary["player_name"] == "Sara"
    assert flow.summary["throws"] == 1


def test_start_control_can_carry_the_player():
    cfg, flow = new_flow()
    flow.handle_control("start", {"player": {"id": "9", "name": "Ali"}}, 0.0)
    assert flow.state == F.INSTRUCTIONS
    flow.request_start(0.1)
    assert flow.round_player == {"id": "9", "name": "Ali"}


# -- results store ------------------------------------------------------------------------------
def rec(score, ts=None, name=""):
    return {"score": score, "timestamp": time.time() if ts is None else ts, "player_name": name}


def test_results_rank_and_leaderboard(tmp_path):
    store = ResultsStore(tmp_path / "r.jsonl", 3)
    assert store.add(rec(100)) == 1
    assert store.add(rec(300)) == 1
    assert store.add(rec(200)) == 2
    assert store.add(rec(50)) == 4
    assert [r["score"] for r in store.leaderboard()] == [300, 200, 100]
    again = ResultsStore(tmp_path / "r.jsonl", 3)  # survives a restart
    assert [r["score"] for r in again.leaderboard()] == [300, 200, 100]


def test_results_load_skips_garbage_and_other_days(tmp_path):
    path = tmp_path / "r.jsonl"
    yesterday = time.time() - 86400 * 2
    path.write_text(
        "\n".join([json.dumps(rec(999, yesterday)), "{broken", "", json.dumps(rec(10)), "[1,2]"]) + "\n",
        encoding="utf-8",
    )
    store = ResultsStore(path, 5)
    assert [r["score"] for r in store.leaderboard()] == [10]


def test_results_unwritable_path_does_not_raise(tmp_path):
    blocked = tmp_path / "is_a_dir"
    blocked.mkdir()
    store = ResultsStore(blocked, 5)
    assert store.add(rec(10)) == 1  # kept in memory, error logged


def test_webhook_posts_results(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from market_pulse.game.results import WebhookSender

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(json.loads(body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    try:
        sender = WebhookSender(f"http://127.0.0.1:{server.server_port}/scores", 2.0)
        sender.submit({"round_id": "x", "score": 120, "player_name": "سارا"})
        end = time.time() + 5
        while sender.sent == 0 and time.time() < end:
            time.sleep(0.02)
        assert sender.sent == 1 and sender.failed == 0
        assert received == [{"round_id": "x", "score": 120, "player_name": "سارا"}]
        sender.stop()
    finally:
        server.shutdown()
        server.server_close()


def test_webhook_failure_is_counted_not_raised(monkeypatch):
    from market_pulse.game import results

    monkeypatch.setattr(results.WebhookSender, "RETRIES", 1)
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here any more
    sender = results.WebhookSender(f"http://127.0.0.1:{port}/", 0.5)
    sender.submit({"round_id": "y", "score": 1})
    end = time.time() + 8
    while sender.failed == 0 and time.time() < end:
        time.sleep(0.05)
    assert sender.failed == 1 and sender.sent == 0 and sender.last_error
    sender.stop()


def test_set_player_ignores_empty_identity():
    _, flow = new_flow()
    flow.set_player({"id": "", "name": "  "})
    assert flow.player is None
