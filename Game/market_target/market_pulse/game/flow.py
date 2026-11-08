"""Screen flow (state machine) and the pausable game clock. No pygame.

ATTRACT -> INSTRUCTIONS -> REGISTER -> INTRO -> COUNTDOWN -> PLAYING -> ENDING -> RESULTS -> ATTRACT

A round is started by the operator (key or UDP "start" message) or, when
``flow.start_by_throw`` is on, by throwing a ball at the start box on the
attract screen. "Reset" from any state aborts the round and returns to ATTRACT.

INSTRUCTIONS is shown only with ``flow.show_instructions``. INTRO (the Mofid logo,
``flow.intro_s``) comes right before every countdown, whichever way the round started.

REGISTER needs a name before the countdown can begin. A name already sent by a
registration system over the network skips it.
"""
from __future__ import annotations

import math
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..config import Section, layout_left, layout_top
from .profile import classify
from .results import ResultsStore
from .rules import Outcome, Round

ATTRACT = "attract"
INSTRUCTIONS = "instructions"
REGISTER = "register"
INTRO = "intro"
COUNTDOWN = "countdown"
PLAYING = "playing"
ENDING = "ending"
RESULTS = "results"


ZWNJ = "\u200c"


def allowed_in_name(ch: str) -> bool:
    """Letters, digits, spaces, the Persian half-space and a few name signs. No
    control characters or symbols, which the wall's fonts could not draw anyway."""
    return ch == ZWNJ or ch in " -.'" or unicodedata.category(ch)[0] in "LMN"


def clean_name(text: str) -> str:
    return " ".join(text.split()).strip(ZWNJ + " ")


def name_is_valid(text: str) -> bool:
    return any(c.isalnum() for c in clean_name(text))


def tidy_name(text: str, max_chars: int) -> str:
    """A name that did not come through the wall's keyboard field."""
    return clean_name(clean_name("".join(c for c in text if allowed_in_name(c)))[:max_chars])


class Clock:
    """Game time: monotonic seconds that stand still while paused."""

    def __init__(self, source: Callable[[], float] = time.perf_counter):
        self._src = source
        self._paused_total = 0.0
        self._paused_at: float | None = None

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    def now(self) -> float:
        raw = self._paused_at if self._paused_at is not None else self._src()
        return raw - self._paused_total

    def to_game(self, raw: float) -> float:
        """Convert a raw perf_counter timestamp (e.g. from an input thread)."""
        return min(raw - self._paused_total, self.now())

    def pause(self) -> None:
        if self._paused_at is None:
            self._paused_at = self._src()

    def resume(self) -> None:
        if self._paused_at is not None:
            self._paused_total += self._src() - self._paused_at
            self._paused_at = None


@dataclass
class UiEvent:
    name: str
    data: dict = field(default_factory=dict)


class Flow:
    def __init__(
        self,
        cfg: Section,
        store: ResultsStore | None = None,
        on_result: Callable[[dict], None] | None = None,
        rng: np.random.Generator | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.on_result = on_result
        self.rng = rng or np.random.default_rng()
        self.state = ATTRACT
        self.since = 0.0
        self.round: Round | None = None
        self.events: list[UiEvent] = []
        self.player: dict | None = None  # identity for the next round (from registration)
        self.round_player: dict | None = None
        self.summary: dict | None = None
        self.rank: int | None = None
        self.rounds_played = 0
        self.name_input = ""  # what has been typed on the registration screen
        self.register_error_at: float | None = None
        self._register_touched = 0.0
        self._last_count = -1
        self._last_warn = -1

    # -- helpers --------------------------------------------------------------
    def _emit(self, name: str, **data) -> None:
        self.events.append(UiEvent(name, data))

    def drain_events(self) -> list[UiEvent]:
        ev, self.events = self.events, []
        return ev

    def _set(self, state: str, now: float) -> None:
        self.state = state
        self.since = now
        self._emit("state", state=state)

    def elapsed(self, now: float) -> float:
        return now - self.since

    def start_button_hit(self, x: float, y: float) -> bool:
        sb = self.cfg.layout.start_button
        half = sb.size_cm / 2 + self.cfg.input.ball_radius_cm
        return abs(x - sb.x_cm - layout_left(self.cfg)) <= half and abs(y - sb.y_cm - layout_top(self.cfg)) <= half

    # -- commands ---------------------------------------------------------------
    def request_start(self, now: float) -> bool:
        if self.state in (ATTRACT, RESULTS):
            if self.cfg.flow.show_instructions:
                self._set(INSTRUCTIONS, now)
            else:
                self._after_instructions(now)
            return True
        if self.state == INSTRUCTIONS:
            self._after_instructions(now)
            return True
        if self.state == REGISTER:
            return self.submit_registration(now)
        return False

    def start_for(self, name: str, now: float) -> bool:
        """The operator's phone: a name and Play go straight to the countdown, from
        any screen where no round is running. False if one is, or the name is empty."""
        name = tidy_name(name, self.cfg.flow.name_max_chars)
        if self.state not in (ATTRACT, INSTRUCTIONS, REGISTER, RESULTS) or not name_is_valid(name):
            return False
        self.player = {"id": "", "name": name}
        self.name_input = ""
        self._begin_countdown(now)
        return True

    def status(self, now: float) -> dict:
        """What the operator's phone shows."""
        out: dict = {"state": self.state, "rounds_played": self.rounds_played}
        rnd = self.round
        if self.state == INTRO:
            out["name"] = (self.round_player or {}).get("name", "")
        elif self.state in (COUNTDOWN, PLAYING, ENDING) and rnd is not None:
            out["name"] = (self.round_player or {}).get("name", "")
            out["score"] = rnd.score
            out["seconds"] = math.ceil(round(rnd.time_left(now), 3)) if rnd.timed else None  # None: no time limit
            out["balls_left"] = rnd.throws_left
            out["balls_limit"] = rnd.balls > 0
        elif self.state == RESULTS and self.summary is not None:
            out["result"] = {"name": self.summary["player_name"], "score": self.summary["score"], "rank": self.rank}
        return out

    def _after_instructions(self, now: float) -> None:
        if self.cfg.flow.registration and not (self.player and self.player.get("name")):
            self.name_input = ""
            self.register_error_at = None
            self._register_touched = now
            self._set(REGISTER, now)
        else:
            self._begin_countdown(now)

    # -- registration -----------------------------------------------------------
    def register_type(self, text: str, now: float) -> None:
        if self.state != REGISTER:
            return
        self._register_touched = now
        typed = self.name_input + "".join(c for c in text if allowed_in_name(c))
        self.name_input = typed[: self.cfg.flow.name_max_chars]
        self.register_error_at = None

    def register_backspace(self, now: float) -> None:
        if self.state == REGISTER:
            self._register_touched = now
            self.name_input = self.name_input[:-1]

    def submit_registration(self, now: float) -> bool:
        """Start the countdown if the name is valid; otherwise show the error and stay."""
        if self.state != REGISTER:
            return False
        self._register_touched = now
        name = clean_name(self.name_input)
        if not name_is_valid(name):
            self.register_error_at = now
            self._emit("register_error")
            return False
        self.player = {"id": self.player.get("id", "") if self.player else "", "name": name}
        self.name_input = ""
        self._begin_countdown(now)
        return True

    def register_press(self, x: float, y: float, now: float) -> None:
        """A ball or a mouse click at (x, y) cm on the registration screen."""
        if self.state == REGISTER and self.start_button_hit(x, y) and self.submit_registration(now):
            self._emit("start_hit", x=x, y=y)

    def reset(self, now: float) -> None:
        if self.round is not None and not self.round.finished:
            self.round.abort(now)
        self.round = None
        self.name_input = ""
        self._set(ATTRACT, now)

    def set_player(self, player: dict | None) -> None:
        if player is None:
            self.player = None
            return
        pid = str(player.get("id", "")).strip()[:64]
        name = str(player.get("name", "")).strip()[:40]
        self.player = {"id": pid, "name": name} if (pid or name) else None
        if self.state == REGISTER and name:
            self.name_input = name[: self.cfg.flow.name_max_chars]
            self.register_error_at = None
        self._emit("player", player=self.player)

    def handle_control(self, kind: str, data: dict, now: float) -> None:
        if kind == "start":
            if isinstance(data.get("player"), dict):
                self.set_player(data["player"])
            self.request_start(now)
        elif kind == "reset":
            self.reset(now)
        elif kind == "player":
            self.set_player(data)
        elif kind == "throw_missed":
            self.operator_missed_throw(now)

    def operator_missed_throw(self, now: float) -> None:
        if self.state == PLAYING and self.round is not None:
            out = self.round.register_missed_throw(now)
            if out is not None:
                self._emit("miss", outcome=out)
            self._drain_round()

    def handle_impact(self, x: float, y: float, t: float, now: float) -> Outcome | None:
        """A deduplicated ball impact at (x, y) cm, game time t."""
        if self.state == ATTRACT and self.cfg.flow.start_by_throw:
            if self.start_button_hit(x, y):
                self._emit("start_hit", x=x, y=y)
                self.request_start(now)
            return None
        if self.state == REGISTER:
            self.register_press(x, y, now)
            return None
        if self.state == PLAYING and self.round is not None:
            out = self.round.register_impact(x, y, t, now)
            if out is not None:
                self._emit(out.kind, outcome=out)
            self._drain_round()
            return out
        return None

    def _drain_round(self) -> None:
        for name, data in self.round.drain_events():
            self._emit(name, **data)

    # -- time -------------------------------------------------------------------
    def _begin_countdown(self, now: float) -> None:
        self.round_player, self.player = self.player, None
        self.round = Round(self.cfg, self.rng)
        self.summary = None
        self.rank = None
        self._last_count = -1
        self._last_warn = -1
        self._set(INTRO if self.cfg.flow.intro_s > 0 else COUNTDOWN, now)

    def countdown_value(self, now: float) -> int:
        left = self.cfg.flow.countdown_s - self.elapsed(now)
        return max(1, math.ceil(left))

    def update(self, now: float) -> None:
        st = self.state
        if st == INSTRUCTIONS:
            if self.elapsed(now) >= self.cfg.flow.instructions_s:
                self._after_instructions(now)
        elif st == REGISTER:
            if now - self._register_touched >= self.cfg.flow.registration_timeout_s:
                self.player = None
                self.reset(now)
        elif st == INTRO:
            if self.elapsed(now) >= self.cfg.flow.intro_s:
                self._set(COUNTDOWN, now)
        elif st == COUNTDOWN:
            if self.elapsed(now) >= self.cfg.flow.countdown_s:
                self.round.start(now)
                self._set(PLAYING, now)
                self._emit("go")
            else:
                n = self.countdown_value(now)
                if n != self._last_count:
                    self._last_count = n
                    self._emit("count", n=n)
        elif st == PLAYING:
            rnd = self.round
            rnd.update(now)
            self._drain_round()
            if rnd.finished:
                self._set(ENDING, now)
                self._emit("end", reason=rnd.end_reason)
            elif rnd.timed:
                sec = math.ceil(rnd.time_left(now))
                if 0 < sec <= self.cfg.flow.time_warning_s and sec != self._last_warn:
                    self._last_warn = sec
                    self._emit("time_warning", seconds=sec)
        elif st == ENDING:
            if self.elapsed(now) >= self.cfg.round.end_delay_s:
                self._finalize()
                self._set(RESULTS, now)
        elif st == RESULTS:
            if self.elapsed(now) >= self.cfg.flow.results_s:
                self._set(ATTRACT, now)

    def _finalize(self) -> None:
        rnd = self.round
        self.rounds_played += 1
        summary = rnd.summary()
        player = self.round_player or {}
        record = {
            "round_id": f"{time.strftime('%Y%m%d-%H%M%S')}-{self.rounds_played}",
            "timestamp": time.time(),
            "player_id": player.get("id", ""),
            "player_name": player.get("name", ""),
            **summary,
            "profile": classify(summary, self.cfg.profiles),
            "balls": rnd.balls,
            "round_duration_s": rnd.duration if rnd.timed else 0,  # 0: no time limit
        }
        self.summary = record
        self.rank = self.store.add(record) if self.store is not None else None
        if self.on_result is not None:
            self.on_result(record)
        self._emit("results", record=record, rank=self.rank)
