"""Collects events from every input source and turns them into wall impacts.

Pipeline per impact: sensor thread -> queue -> calibration -> range check -> centimetres ->
camera + LiDAR fusion (input/fusion.py: one hit per throw, LiDAR coordinates first) ->
merge / rebound filter -> game time (minus sensor latency).

Sources other than the camera and the LiDAR (udp_json, tuio, the mouse) pass the fusion unchanged.
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
from .fusion import CAMERA, LIDAR, SENSORS, Detection, FusedHit, FusionManager
from .lidar.source import LidarSource
from .sources import Source, TuioSource, UdpJsonSource

log = logging.getLogger(__name__)

EDGE_TOLERANCE = 0.05  # accept points slightly outside 0..1 (sensor noise at the border)


@dataclass(frozen=True)
class WallImpact:
    x: float  # cm from the left edge of the wall
    y: float  # cm from the top edge
    t: float  # game time of the contact
    source: str  # the sensor whose coordinates these are
    sensors: tuple[str, ...] = ()  # camera + LiDAR fusion: every sensor that saw this throw


class InputManager:
    def __init__(self, cfg: Section, to_game_time: Callable[[float], float], roots: Sequence[Path | None] = (),
                 clock: Callable[[], float] = time.perf_counter):
        """roots: folders a relative input.lidar.settings_file or input.camera.calibration_file is looked
        up in, first match wins; a new camera calibration is written into the first one (next to the
        game, not inside the .exe). clock: the input sources' clock (tests give their own)."""
        self.cfg = cfg
        self.to_game_time = to_game_time
        self.clock = clock
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
        self.fusion = FusionManager(merge_window_s=icfg.merge_window_ms / 1000.0, merge_radius_cm=icfg.merge_radius_cm,
                                    alive=self.sensor_alive)
        self.fusion.configure(icfg.fusion)
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
            elif name == "camera":
                self._add_camera(icfg.camera, roots)

    def _add_camera(self, ccfg: Section, roots: Sequence[Path | None]) -> None:
        try:
            from .wallcam.source import CameraSource  # OpenCV and PyAV: only when the camera is used
        except ImportError as exc:
            log.error("camera input not available: %s (pip install opencv-python av)", exc)
            return
        name = Path(ccfg.calibration_file)
        first = next((r for r in roots if r is not None), None)
        save = name if name.is_absolute() or first is None else first / name
        self.sources.append(CameraSource(self.queue, ccfg, self._find(str(name), roots), save,
                                         self.wall_w, self.wall_h))

    @staticmethod
    def _find(name: str, roots: Sequence[Path | None]) -> Path:
        path = Path(name)
        if path.is_absolute():
            return path
        found = [r / path for r in roots if r is not None]
        return next((f for f in found if f.is_file()), found[0] if found else path)

    def set_wall(self, wall_w: float, wall_h: float) -> bool:
        """The wall has another size (App._wall_changed). False: a sensor's calibration is not for it."""
        self.wall_w, self.wall_h = float(wall_w), float(wall_h)
        ok = True
        for s in self.sources:
            if hasattr(s, "set_wall"):
                ok = s.set_wall(self.wall_w, self.wall_h) and ok
            elif hasattr(s, "wall_w"):
                s.wall_w, s.wall_h = self.wall_w, self.wall_h
        self.fusion.reset()
        return ok

    def source(self, name: str) -> Source | None:
        return next((s for s in self.sources if s.name == name), None)

    @property
    def lidar(self) -> LidarSource | None:
        return next((s for s in self.sources if isinstance(s, LidarSource)), None)

    @property
    def camera(self):
        """The wall camera (wallcam.source.CameraSource), if input.sources has it."""
        return self.source(CAMERA)

    def sensor_alive(self, name: str) -> bool:
        """Whether that sensor can report a hit now: configured, its thread listening, data coming in
        (input.fusion.offline_after_ms), and ready (the LiDAR has its background, the camera a
        calibration). The fusion only waits for a sensor that is."""
        s = self.source(name)
        if s is None or s.state != "listening" or s.last_packet is None:
            return False
        if self.clock() - s.last_packet > float(self.cfg.input.fusion.offline_after_ms) / 1000.0:
            return False
        if name == CAMERA:
            return bool(getattr(s, "calibrated", True))
        if name == LIDAR:
            return bool(getattr(s, "ready", True))
        return True

    def settings_changed(self) -> None:
        """The settings page changed input.fusion."""
        self.fusion.configure(self.cfg.input.fusion)

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
        hits: list[tuple[FusedHit, float]] = []  # (hit, latency to take off)
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
            self.recent.append((ev.raw[0], ev.raw[1], x_cm, y_cm, True, ev.source, ev.t))
            det = Detection(ev.source, x_cm, y_cm, ev.t, ev.raw, bool(getattr(ev, "trusted", True)))
            hits += [(h, latency) for h in self.fusion.feed(det, self.clock())]
        hits += [(h, self.latency) for h in self.fusion.poll(self.clock())]  # camera hits whose window ran out
        hits.sort(key=lambda hl: hl[0].t)  # in the order the balls touched the wall
        for hit, latency in hits:
            if self.filter.accept(hit.x, hit.y, hit.t):
                self.accepted += 1
                out.append(WallImpact(hit.x, hit.y, self.to_game_time(hit.t) - latency, hit.source, hit.sensors))
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
        out = [s.status() for s in self.sources]
        for st in out:
            if st["name"] in SENSORS:
                st["alive"] = self.sensor_alive(st["name"])
        return out
