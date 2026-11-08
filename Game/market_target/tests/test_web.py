"""The operator's phone page: name and Play start a round, every round goes into the CSV."""
import csv
import io
import json
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from conftest import game_cfg
from market_pulse import web
from market_pulse.app import App, Paths
from market_pulse.game import flow as F
from market_pulse.game.results import ResultsStore, history_csv

ROOT = Path(__file__).resolve().parent.parent
RECORD = {
    "round_id": "r1", "timestamp": 1793379633.0, "player_id": "", "player_name": "سارا", "score": 400, "throws": 10,
    "hits": {"medium": 3, "small": 2, "tiny": 1}, "points_by_tier": {"medium": 30, "small": 30, "tiny": 60}, "total_hits": 6,
    "products": {"gold": 2, "jet": 1, "hami": 3},
    "rush": {"count": 1, "throws": 4, "hits": 2, "points": 90}, "best_streak": 3, "best_multiplier": 4.0, "end_reason": "balls",
    "duration_s": 18.2, "targets_at_end": 9, "profile": {"key": "balanced", "label": "متعادل", "statement": "..."},
    "balls": 10, "round_duration_s": 60.0,
}


def rows_of(data: bytes) -> list[dict]:
    assert data.startswith(b"\xef\xbb\xbf")  # or Excel garbles Persian
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- the flow ---------------------------------------------------------------------
@pytest.mark.parametrize("state", [F.ATTRACT, F.INSTRUCTIONS, F.REGISTER, F.RESULTS])
def test_play_goes_straight_to_the_logo_intro_then_the_countdown(state):
    flow = F.Flow(game_cfg(), rng=np.random.default_rng(1))
    flow.state = state
    assert flow.start_for("  سارا  ", 5.0)
    assert flow.state == F.INTRO and flow.round_player["name"] == "سارا"
    flow.update(flow.since + flow.cfg.flow.intro_s + 0.01)
    assert flow.state == F.COUNTDOWN


@pytest.mark.parametrize("state", [F.COUNTDOWN, F.PLAYING, F.ENDING])
def test_play_never_replaces_a_running_round(state):
    flow = F.Flow(game_cfg(), rng=np.random.default_rng(1))
    flow.start_for("اول", 1.0)
    first = flow.round
    flow.state = state
    assert not flow.start_for("دوم", 2.0)
    assert flow.round is first and flow.round_player["name"] == "اول" and flow.state == state


@pytest.mark.parametrize("name", ["", "   ", "...", "<>=+", "‌"])
def test_play_needs_a_real_name(name):
    flow = F.Flow(game_cfg(), rng=np.random.default_rng(1))
    assert not flow.start_for(name, 1.0)
    assert flow.state == F.ATTRACT


def test_name_is_cleaned_and_cut():
    assert F.tidy_name(" <b>Ali</b>\n Reza ", 40) == "bAlib Reza"
    assert F.tidy_name("ab cd", 3) == "ab"


def test_status_for_the_phone():
    cfg = game_cfg()
    flow = F.Flow(cfg, rng=np.random.default_rng(1))
    assert flow.status(0.0) == {"state": F.ATTRACT, "rounds_played": 0}
    flow.start_for("سارا", 1.0)
    assert flow.status(1.0) == {"state": F.INTRO, "rounds_played": 0, "name": "سارا"}
    flow.update(1.0 + cfg.flow.intro_s)
    now = 1.0 + cfg.flow.intro_s + cfg.flow.countdown_s + 0.01
    flow.update(now)
    s = flow.status(now)
    assert s["state"] == F.PLAYING and s["name"] == "سارا" and s["score"] == 0
    assert s["seconds"] == cfg.round.duration_s and s["balls_left"] == 0 and s["balls_limit"] is False  # no ball limit


# -- the history ------------------------------------------------------------------
def test_every_saved_value_has_a_csv_column():
    row = rows_of(history_csv([RECORD]))[0]
    assert row["player_name"] == "سارا" and row["score"] == "400" and row["round_id"] == "r1"
    assert (row["hits_medium"], row["hits_small"], row["hits_tiny"]) == ("3", "2", "1")
    assert (row["points_by_tier_medium"], row["points_by_tier_tiny"]) == ("30", "60")
    assert (row["products_gold"], row["products_jet"]) == ("2", "1")  # hits per product: a column each
    assert (row["rush_count"], row["rush_throws"], row["rush_hits"], row["rush_points"]) == ("1", "4", "2", "90")
    assert (row["profile_key"], row["profile_label"]) == ("balanced", "متعادل")
    assert (row["best_streak"], row["best_multiplier"], row["end_reason"]) == ("3", "4.0", "balls")
    assert (row["duration_s"], row["targets_at_end"], row["balls"], row["round_duration_s"]) == ("18.2", "9", "10", "60.0")
    assert row["misses"] == "4" and row["rank_of_day"] == "1" and row["date"] and row["time"]
    assert all(v != "" for k, v in row.items() if k != "player_id")


def test_csv_ranks_within_each_day_and_keeps_every_round():
    day = 86400.0
    records = [{**RECORD, "round_id": rid, "player_name": "سارا", "score": s, "timestamp": RECORD["timestamp"] + t}
               for rid, s, t in (("a", 100, 0), ("b", 300, 10), ("c", 50, day))]
    rows = rows_of(history_csv(records))
    assert [(r["round_id"], r["rank_of_day"]) for r in rows] == [("a", "2"), ("b", "1"), ("c", "1")]


def test_csv_gets_new_values_and_survives_old_records():
    rows = rows_of(history_csv([{"round_id": "old", "timestamp": 1.0}, {**RECORD, "new_value": 7}]))
    assert rows[0]["score"] == "" and rows[0]["round_id"] == "old"
    assert rows[1]["new_value"] == "7"
    assert history_csv([]).decode("utf-8-sig").startswith("round_id,date,time,")


def test_csv_does_not_let_a_name_become_a_formula():
    rows = rows_of(history_csv([{**RECORD, "player_name": "=1+1"}, {**RECORD, "player_name": "-cmd"}]))
    assert [r["player_name"] for r in rows] == ["'=1+1", "'-cmd"]


def test_history_reads_all_days_and_skips_a_half_written_line(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps({"round_id": "old", "timestamp": 1.0, "score": 5}) + "\n{\"round_id\": \"hal\n", encoding="utf-8")
    store = ResultsStore(path)
    assert [r["round_id"] for r in store.history()] == ["old"]
    assert store.today == []  # the old day still does not count for today's leaderboard
    assert ResultsStore(tmp_path / "none.jsonl").history() == []


# -- the server -------------------------------------------------------------------
class Site:
    def __init__(self, tmp_path, overrides=None, answer_with=None):
        self.cfg = game_cfg({"web": {"host": "127.0.0.1", "port": free_port(), **(overrides or {})}})
        self.store = ResultsStore(tmp_path / "results.jsonl")
        self.panel = web.Panel(self.cfg, self.store, ROOT / "assets")
        assert self.panel.start()
        self.base = f"http://127.0.0.1:{self.cfg.web.port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # not the system's proxy
        self.answer_with = answer_with
        self.asked = []
        self.running = answer_with is not None
        self.loop = threading.Thread(target=self._main_loop, daemon=True)
        self.loop.start()

    def _main_loop(self):  # stands in for App._serve_panel
        while self.running:
            for cmd in self.panel.take():
                self.asked.append((cmd.kind, cmd.name))
                cmd.answer(self.answer_with)
            threading.Event().wait(0.005)

    def get(self, path, headers=None):
        return self._open(urllib.request.Request(self.base + path, headers=headers or {}))

    def post(self, path, body=None, kind="application/json", headers=None):
        data = json.dumps(body if body is not None else {}).encode("utf-8")
        return self._open(urllib.request.Request(self.base + path, data=data, headers={"Content-Type": kind, **(headers or {})}))

    def _open(self, req):
        try:
            with self.opener.open(req, timeout=8) as res:
                return res.status, res.read(), res.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def close(self):
        self.running = False
        self.panel.stop()


@pytest.fixture
def site(tmp_path):
    made = []

    def make(**kwargs):
        made.append(Site(tmp_path, **kwargs))
        return made[-1]

    yield make
    for s in made:
        s.close()


def test_page_and_its_files_are_served(site):
    s = site()
    code, body, headers = s.get("/")
    assert code == 200 and "text/html" in headers["Content-Type"] and "شروع بازی".encode() in body
    for name in web.FILES:
        assert s.get(f"/files/{name}")[0] == 200


@pytest.mark.parametrize("path", ["/files/settings.json", "/files/../../config/settings.json", "/files/..%2F..%2Fmain.py", "/data/results.jsonl", "/api/nope"])
def test_nothing_else_can_be_read(site, path):
    code, body, _ = site().get(path)
    assert code == 404 and b"round_id" not in body


def test_play_reaches_the_game_with_the_cleaned_name(site):
    s = site(answer_with=True)
    code, body, _ = s.post("/api/play", {"name": "  سارا\n"})
    assert code == 200 and json.loads(body) == {"ok": True, "name": "سارا"}
    assert s.asked == [("play", "سارا")]


def test_play_while_a_round_runs_says_busy(site):
    code, body, _ = site(answer_with=False).post("/api/play", {"name": "سارا"})
    assert code == 409 and json.loads(body)["error"] == "busy"


def test_empty_name_never_reaches_the_game(site):
    s = site(answer_with=True)
    assert s.post("/api/play", {"name": "  "})[0] == 400
    assert s.post("/api/play", {})[0] == 400
    assert s.asked == []


def test_a_game_that_does_not_answer_is_reported_and_not_started_later(site, monkeypatch):
    monkeypatch.setattr(web, "ANSWER_S", 0.05)
    s = site()
    code, body, _ = s.post("/api/play", {"name": "سارا"})
    assert code == 503 and json.loads(body)["error"] == "no_answer"
    assert s.panel.take() == []


def test_bad_requests_are_refused(site):
    s = site(answer_with=True)
    assert s.post("/api/play", {"name": "x"}, kind="text/plain")[0] == 415  # another website's form
    assert s.post("/api/nope", {})[0] == 404
    too_big = "x" * (web.MAX_BODY + 10)
    assert s.post("/api/play", {"name": too_big})[0] == 413
    assert s.asked == []


def test_state_and_history_and_csv(site):
    s = site()
    s.panel.status = {"state": F.PLAYING, "score": 40}
    state = json.loads(s.get("/api/state")[1])
    assert state["state"] == F.PLAYING and state["score"] == 40 and state["title"] == s.cfg.web.title
    assert state["name_max"] == s.cfg.flow.name_max_chars
    assert json.loads(s.get("/api/history")[1]) == {"total": 0, "rounds": []}

    s.store.add(dict(RECORD))
    s.store.add({**RECORD, "round_id": "r2", "player_name": "Ali", "score": 90, "timestamp": RECORD["timestamp"] + 60})
    history = json.loads(s.get("/api/history")[1])
    assert history["total"] == 2 and [r["name"] for r in history["rounds"]] == ["Ali", "سارا"]  # newest first

    code, body, headers = s.get("/export.csv")
    assert code == 200 and "text/csv" in headers["Content-Type"] and "attachment" in headers["Content-Disposition"]
    assert [r["round_id"] for r in rows_of(body)] == ["r1", "r2"]


def test_pin_protects_names_and_starts_but_not_the_empty_page(site):
    s = site(overrides={"pin": "1234"}, answer_with=True)
    assert s.get("/")[0] == 200
    for path in ("/api/state", "/api/history", "/export.csv"):
        assert s.get(path)[0] == 401
        assert s.get(path, {"Cookie": "pin=9999"})[0] == 401
        assert s.get(path, {"Cookie": "pin=1234"})[0] == 200
    assert s.post("/api/play", {"name": "سارا"})[0] == 401
    assert s.asked == []
    assert s.post("/api/play", {"name": "سارا"}, headers={"Cookie": "pin=1234"})[0] == 200


def test_a_port_in_use_does_not_stop_the_game(tmp_path, site):
    s = site()
    other = web.Panel(s.cfg, s.store, ROOT / "assets")
    assert not other.start()
    assert other.error and other.url == ""
    other.stop()


# -- the whole path ---------------------------------------------------------------
def test_phone_starts_and_cancels_a_round_on_the_wall(tmp_path):
    cfg = game_cfg({"results": {"log_file": str(tmp_path / "results.jsonl")}})
    clock = {"t": 100.0}
    app = App(cfg, Paths(ROOT), clock_source=lambda: clock["t"], start_inputs=False)
    app.panel = web.Panel(cfg, app.store, ROOT / "assets")  # the server itself stays off
    try:
        def send(kind, name=""):
            cmd = web.Command(kind, name)
            app.panel._commands.put(cmd)
            app.frame()
            assert cmd.done.is_set()
            return cmd.ok

        app.frame()
        assert app.panel.status["state"] == F.ATTRACT
        assert not send("cancel")  # no round to cancel
        assert send("play", "سارا")
        assert app.flow.state == F.INTRO
        assert not send("play", "علی")
        assert app.flow.round_player["name"] == "سارا" and app.panel.status["name"] == "سارا"

        clock["t"] += cfg.flow.intro_s + 0.05
        app.frame()
        assert app.flow.state == F.COUNTDOWN
        clock["t"] += cfg.flow.countdown_s + 0.1
        app.frame()
        assert app.flow.state == F.PLAYING
        app.clock.pause()
        app.frame()
        assert app.panel.status["paused"] is True
        assert send("cancel")  # also from a paused round
        assert app.flow.state == F.ATTRACT and not app.clock.paused
        assert app.store.history() == []  # a cancelled round is not saved
    finally:
        app.shutdown()


def test_finished_round_is_in_the_csv_with_its_name(tmp_path):
    cfg = game_cfg({"round": {"ball_limit": True, "balls": 1}, "results": {"log_file": str(tmp_path / "results.jsonl")}})
    clock = {"t": 100.0}
    app = App(cfg, Paths(ROOT), clock_source=lambda: clock["t"], start_inputs=False)
    app.panel = web.Panel(cfg, app.store, ROOT / "assets")
    try:
        cmd = web.Command("play", "سارا")
        app.panel._commands.put(cmd)
        app.frame()
        clock["t"] += cfg.flow.intro_s + 0.05
        app.frame()
        clock["t"] += cfg.flow.countdown_s + 0.1
        app.frame()
        target = app.flow.round.targets[0]
        clock["t"] += 0.1
        app.flow.handle_impact(target.x, target.y, clock["t"], clock["t"])
        app.frame()
        clock["t"] += cfg.round.end_delay_s + 0.1
        app.frame()
        assert app.flow.state == F.RESULTS
        app.frame()
        result = app.panel.status["result"]
        assert result["name"] == "سارا" and result["rank"] == 1

        row = rows_of(history_csv(app.store.history()))[0]
        assert row["player_name"] == "سارا" and row["throws"] == "1" and int(row["score"]) == result["score"]
        assert row["end_reason"] == "balls"
    finally:
        app.shutdown()
