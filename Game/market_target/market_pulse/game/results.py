"""Round results: local JSON-lines log, today's leaderboard, optional webhook.

The JSONL file is the source of truth (one record per finished round). The
webhook is how a registration platform / landing page (add-ons 1.3 and 1.4)
receives scores: each record is POSTed as JSON from a background thread, so a
slow or offline server never stalls the game.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


class ResultsStore:
    def __init__(self, path: Path, leaderboard_size: int = 5):
        self.path = path
        self.size = leaderboard_size
        self.today: list[dict] = []
        self._load()

    @staticmethod
    def _day(ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

    def _load(self) -> None:
        today = self._day(time.time())
        self.today = [r for r in self.history() if self._day(r.get("timestamp", 0)) == today]

    def history(self) -> list[dict]:
        """Every round in the file, oldest first. Safe to call from another thread."""
        if not self.path.is_file():
            return []
        out = []
        bad = 0
        try:
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        bad += 1
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except OSError as exc:
            log.error("cannot read results file %s: %s", self.path, exc)
        if bad:
            log.warning("skipped %d unreadable lines in %s", bad, self.path)
        return out

    def add(self, record: dict) -> int | None:
        """Append a record; return its rank among today's rounds (1 = best)."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.error("cannot write results file %s: %s", self.path, exc)
        if self.today and self._day(self.today[-1]["timestamp"]) != self._day(record["timestamp"]):
            self.today.clear()  # the installation ran past midnight
        self.today.append(record)
        ranked = sorted(self.today, key=lambda r: (-r["score"], r["timestamp"]))
        return next((i + 1 for i, r in enumerate(ranked) if r is record), None)

    def leaderboard(self) -> list[dict]:
        if self.today and self._day(self.today[-1]["timestamp"]) != self._day(time.time()):
            self.today.clear()
        return sorted(self.today, key=lambda r: (-r["score"], r["timestamp"]))[: self.size]


CSV_FIRST = (
    "round_id", "date", "time", "player_id", "player_name", "score", "rank_of_day",
    "throws", "total_hits", "misses", "hits_medium", "hits_small", "hits_tiny",
    "points_by_tier_medium", "points_by_tier_small", "points_by_tier_tiny",
    "rush_count", "rush_throws", "rush_hits", "rush_points",
    "best_streak", "best_multiplier", "end_reason", "duration_s", "targets_at_end",
    "profile_key", "profile_label", "profile_statement", "balls", "round_duration_s", "timestamp",
)


def _flat(record: dict) -> dict:
    out = {}
    for key, value in record.items():
        if isinstance(value, dict):
            out.update(_flat({f"{key}_{k}": v for k, v in value.items()}))
        else:
            out[key] = value
    return out


def _cell(value):
    """Text a spreadsheet would run as a formula is kept as plain text."""
    return "'" + value if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def history_csv(records: list[dict]) -> bytes:
    """One row per round, one column per value saved for it. New values in the records
    get their own column by themselves. With a BOM, so Excel shows Persian correctly."""
    days: dict[str, list[dict]] = {}
    for rec in records:
        days.setdefault(ResultsStore._day(rec.get("timestamp", 0)), []).append(rec)
    ranks: dict[int, int] = {}
    for rounds in days.values():
        ranked = sorted(rounds, key=lambda r: (-r.get("score", 0), r.get("timestamp", 0)))
        ranks.update((id(r), i + 1) for i, r in enumerate(ranked))

    rows = []
    for rec in records:
        when = datetime.fromtimestamp(rec.get("timestamp", 0))
        rows.append({**_flat(rec), "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M:%S"), "rank_of_day": ranks[id(rec)],
                     "misses": rec.get("throws", 0) - rec.get("total_hits", 0)})
    columns = list(dict.fromkeys([*CSV_FIRST, *(k for row in rows for k in row)]))
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c, "")) for c in columns])
    return text.getvalue().encode("utf-8-sig")


class WebhookSender:
    """POSTs result records to ``url`` from a daemon thread, with retries."""

    RETRIES = 3

    def __init__(self, url: str, timeout: float):
        self.url = url
        self.timeout = timeout
        self.sent = 0
        self.failed = 0
        self.last_error = ""
        self._q: queue.Queue[dict | None] = queue.Queue(maxsize=500)
        self._thread = threading.Thread(target=self._run, name="webhook", daemon=True)
        self._thread.start()

    def submit(self, record: dict) -> None:
        try:
            self._q.put_nowait(record)
        except queue.Full:
            self.failed += 1
            log.error("webhook queue full; result kept only in the local log")

    def stop(self) -> None:
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    def _run(self) -> None:
        while True:
            record = self._q.get()
            if record is None:
                return
            body = json.dumps(record, ensure_ascii=False).encode("utf-8")
            for attempt in range(1, self.RETRIES + 1):
                try:
                    req = urllib.request.Request(
                        self.url, data=body, headers={"Content-Type": "application/json"}, method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        resp.read()
                    self.sent += 1
                    break
                except (urllib.error.URLError, OSError, ValueError) as exc:
                    self.last_error = str(exc)
                    log.warning("webhook attempt %d failed: %s", attempt, exc)
                    time.sleep(2.0 * attempt)
            else:
                self.failed += 1
                log.error("webhook gave up on round %s; it is still in the local log", record.get("round_id"))
