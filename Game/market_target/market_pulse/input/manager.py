"""Collects events from every input source and turns them into wall impacts.

Pipeline per impact: sensor thread -> queue -> calibration -> range check ->
merge / rebound filter -> centimetres + game time (minus sensor latency).
"""
from __future__ import annotations

import logging
import queue
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..config import Section
from .events import Calibration, Control, Impact, ImpactFilter
from .lidar.source import LidarSource
from .sources import Source, TuioSource, UdpJsonSource

log = logging.getLogger(__name__)

EDGE_TOLERANCE = 0.05  # accept points slightly outside 0..1 (sensor noise at the border)


@dataclass(frozen=True)
class WallImpact:
    x: float  # cm from the left edge of the wall
    y: float  # cm from the top edge
    t: float  # game time of the contact
    source: str


class InputManager:
    def __init__(self, cfg: Section, to_game_time: Callable[[float], float], roots: Sequence[Path | None] = ()):
        """roots: folders a relative input.lidar.settings_file is looked up in, first match wins."""
        self.cfg = cfg
        self.to_game_time = to_game_time
        self.wall_w = float(cfg.wall.width_cm)
        self.wall_h = float(cfg.wall.height_cm)
        icfg = cfg.input
        self.latency = icfg.latency_ms / 1000.0
        self.calibration = Calibration(**icfg.calibration.raw())
        self.filter = ImpactFilter(
            icfg.merge_window_ms / 1000.0,
            icfg.merge_radius_cm,
            icfg.rebound_window_ms / 1000.0,
            icfg.rebound_dx_cm,
        )
        self.queue: queue.SimpleQueue = queue.SimpleQueue()
        self.sources: list[Source] = []
        self.simulation = bool(icfg.simulation)
        self.out_of_range = 0
        self.accepted = 0
        # (x_norm_raw, y_norm_raw, x_cm, y_cm, accepted, source, perf_counter) for the calibration view
        self.recent: deque = deque(maxlen=40)
        for name in icfg.sources:
            if name == "udp_json":
                u = icfg.udp_json
                self.sources.append(UdpJsonSource(self.queue, u.host, u.port, u.units, self.wall_w, self.wall_h))
            elif name == "tuio":
                self.sources.append(TuioSource(self.queue, icfg.tuio.host, icfg.tuio.port))
            elif name == "lidar":
                self.sources.append(LidarSource(self.queue, icfg.lidar, self._find(icfg.lidar.settings_file, roots),
                                                self.wall_w, self.wall_h))

    @staticmethod
    def _find(name: str, roots: Sequence[Path | None]) -> Path:
        path = Path(name)
        if path.is_absolute():
            return path
        found = [r / path for r in roots if r is not None]
        return next((f for f in found if f.is_file()), found[0] if found else path)

    def set_wall(self, wall_w: float, wall_h: float) -> bool:
        """The wall has another size (App._wall_changed). False: the lidar's calibration is not for it."""
        self.wall_w, self.wall_h = float(wall_w), float(wall_h)
        ok = True
        for s in self.sources:
            if isinstance(s, LidarSource):
                ok = s.set_wall(self.wall_w, self.wall_h)
            elif hasattr(s, "wall_w"):
                s.wall_w, s.wall_h = self.wall_w, self.wall_h
        return ok

    @property
    def lidar(self) -> LidarSource | None:
        return next((s for s in self.sources if isinstance(s, LidarSource)), None)

    def start(self) -> None:
        for s in self.sources:
            s.start()

    def stop(self) -> None:
        for s in self.sources:
            s.stop()

    # -- simulation (mouse) -------------------------------------------------------
    def push_simulated(self, x_norm: float, y_norm: float) -> None:
        """Simulation mode only: a mouse click stands in for a ball impact.
        Calibration is not applied, because the click is already in wall space."""
        if self.simulation:
            self.queue.put(Impact(x_norm, y_norm, time.perf_counter(), "simulation", (x_norm, y_norm)))

    # -- per frame ------------------------------------------------------------------
    def poll(self) -> list[WallImpact | Control]:
        out: list[WallImpact | Control] = []
        while True:
            try:
                ev = self.queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(ev, Control):
                out.append(ev)
                continue
            if ev.source == "simulation":
                nx, ny = ev.x, ev.y
                latency = 0.0
            else:
                nx, ny = self.calibration.apply(ev.x, ev.y)
                latency = self.latency
            if not (-EDGE_TOLERANCE <= nx <= 1 + EDGE_TOLERANCE and -EDGE_TOLERANCE <= ny <= 1 + EDGE_TOLERANCE):
                self.out_of_range += 1
                self.recent.append((ev.raw[0], ev.raw[1], nx * self.wall_w, ny * self.wall_h, False, ev.source, ev.t))
                continue
            x_cm = min(max(nx, 0.0), 1.0) * self.wall_w
            y_cm = min(max(ny, 0.0), 1.0) * self.wall_h
            ok = self.filter.accept(x_cm, y_cm, ev.t)
            self.recent.append((ev.raw[0], ev.raw[1], x_cm, y_cm, ok, ev.source, ev.t))
            if ok:
                self.accepted += 1
                out.append(WallImpact(x_cm, y_cm, self.to_game_time(ev.t) - latency, ev.source))
        return out

    # -- health -------------------------------------------------------------------
    def sensor_offline(self) -> bool:
        """True if a heartbeat timeout is configured and no hardware packet arrived in time."""
        timeout = self.cfg.input.heartbeat_timeout_s
        if timeout <= 0 or not self.sources:
            return False
        ages = [s.status()["last_packet_age"] for s in self.sources]
        return all(a is None or a > timeout for a in ages)

    def status(self) -> list[dict]:
        return [s.status() for s in self.sources]
