"""Camera + LiDAR fusion: one hit per thrown ball, LiDAR coordinates whenever there are valid ones.

The two sensors keep working on their own (input/lidar, input/wallcam) and each reports every ball
it sees. The FusionManager sits between them and the game (InputManager.poll), after the
calibration to wall centimetres and before the merge / rebound filter.

A detection either
  - joins the throw of the other sensor that it matches: close in contact time (|dt| <= match_time_s)
    and on the wall (<= match_radius_cm), and that throw has no detection of this sensor yet. The
    sensors stamp one contact up to ~0.2 s apart (measured: the LiDAR 0.07-0.19 s after the
    camera), so this tolerance is its own setting, larger than the wait;
  - is the same sensor reporting the same ball twice (within merge_window_s and merge_radius_cm of
    that sensor's detection of a throw): dropped;
  - or starts a new throw.

When a throw is registered (at most once):
  - a trusted LiDAR detection (calibrated) is registered at once, with its coordinates; a camera
    detection of the same throw that comes later is absorbed;
  - a camera detection waits for the LiDAR, at most window_s (the wait) after it arrived. A matching LiDAR
    detection registers the throw at once with the LiDAR's coordinates; if none comes, the camera's
    coordinates are used when the window runs out;
  - a detection whose partner cannot come (the other sensor is not configured, disconnected, not
    calibrated or still learning its background) is registered at once: a missing sensor never
    holds up the other;
  - an untrusted LiDAR detection (LiDAR not calibrated) waits for the camera like the camera waits
    for the LiDAR, and the camera's coordinates are used if they come.

The hit keeps the contact time of the detection whose coordinates it uses, so the hit test against
the moving boxes stays exact however long the throw waited. After registration a throw is kept for
keep_s more, so a late detection of the other sensor (also one arriving after the window) is
absorbed and never becomes a second hit.

Times are time.perf_counter() seconds (the clock every input source stamps its events with),
positions wall centimetres. No pygame, no threads: InputManager calls feed() and poll() on the main
thread once per frame.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)

CAMERA = "camera"
LIDAR = "lidar"
SENSORS = (CAMERA, LIDAR)
CAMERA_ALONE = ("always", "lidar_offline", "never")


@dataclass(frozen=True)
class Detection:
    sensor: str  # "camera" | "lidar" (any other source passes straight through)
    x: float  # wall cm
    y: float
    t: float  # contact time, perf_counter seconds
    raw: tuple[float, float] = (0.0, 0.0)
    trusted: bool = True  # False: rough coordinates (a LiDAR that is not calibrated)


@dataclass(frozen=True)
class FusedHit:
    x: float  # wall cm
    y: float
    t: float  # contact time of the detection the coordinates come from
    source: str  # the sensor whose coordinates are used
    sensors: tuple[str, ...]  # the sensors that saw this throw when it was registered
    waited: float  # seconds between the first detection's arrival and the registration
    reason: str  # "lidar" | "matched" | "timeout" | "alone" | "passthrough"


@dataclass
class Throw:
    """One physical ball: the detections of each sensor, and whether it was registered."""

    first: Detection
    arrived: float
    deadline: float
    dets: dict[str, Detection] = field(default_factory=dict)
    registered: FusedHit | None = None
    registered_at: float | None = None

    def has(self, sensor: str) -> bool:
        return sensor in self.dets


@dataclass
class FusionStats:
    lidar_only: int = 0  # registered with the LiDAR alone
    camera_only: int = 0  # registered with the camera alone (the window ran out, or no LiDAR)
    fused: int = 0  # registered with both: LiDAR coordinates
    duplicates: int = 0  # a second detection of a registered throw, absorbed
    late: int = 0  # ...of which arrived after the window (the throw was already registered alone)
    repeats: int = 0  # the same sensor reporting the same ball twice
    rejected: int = 0  # invalid coordinates or times
    refused: int = 0  # camera-alone hits refused by camera_alone
    wait_total: float = 0.0
    wait_max: float = 0.0
    waits: int = 0

    def as_dict(self) -> dict:
        avg = self.wait_total / self.waits if self.waits else 0.0
        return {"lidar_only": self.lidar_only, "camera_only": self.camera_only, "fused": self.fused,
                "duplicates": self.duplicates, "late": self.late, "repeats": self.repeats,
                "rejected": self.rejected, "refused": self.refused,
                "wait_avg_ms": round(avg * 1000), "wait_max_ms": round(self.wait_max * 1000)}


def _valid(d: Detection) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in (d.x, d.y, d.t))


class FusionManager:
    def __init__(
        self,
        window_s: float = 0.15,
        match_radius_cm: float = 60.0,
        match_time_s: float = 0.3,
        keep_s: float = 1.0,
        merge_window_s: float = 0.25,
        merge_radius_cm: float = 35.0,
        camera_alone: str = "always",
        alive: Callable[[str], bool] | None = None,
        enabled: bool = True,
    ):
        """alive(sensor): whether that sensor is configured, connected and able to report a hit now.
        Its partner's detections only wait for it while it is."""
        self.window_s = max(0.0, float(window_s))  # how long a camera hit waits for the LiDAR
        self.match_time_s = max(0.0, float(match_time_s))  # contact times this close can be one throw
        self.match_radius_cm = max(0.0, float(match_radius_cm))
        self.keep_s = max(0.0, float(keep_s))
        self.merge_window_s = max(0.0, float(merge_window_s))
        self.merge_radius_cm = max(0.0, float(merge_radius_cm))
        self.camera_alone = camera_alone if camera_alone in CAMERA_ALONE else "always"
        self.alive = alive or (lambda sensor: False)
        self.enabled = bool(enabled)
        self.throws: list[Throw] = []
        self.stats = FusionStats()

    def configure(self, cfg) -> None:
        """Take the values of input.fusion (the settings page changes them while the game runs)."""
        self.enabled = bool(cfg.get("enabled", True))
        self.window_s = max(0.0, float(cfg.get("window_ms", 150)) / 1000.0)
        self.match_time_s = max(0.0, float(cfg.get("match_time_ms", 300)) / 1000.0)
        self.match_radius_cm = max(0.0, float(cfg.get("match_radius_cm", 60)))
        self.keep_s = max(0.0, float(cfg.get("keep_ms", 1000)) / 1000.0)
        alone = str(cfg.get("camera_alone", "always"))
        self.camera_alone = alone if alone in CAMERA_ALONE else "always"

    # -- the decisions ------------------------------------------------------------------------------------
    @staticmethod
    def _other(sensor: str) -> str:
        return LIDAR if sensor == CAMERA else CAMERA

    @staticmethod
    def _best(d: Detection) -> bool:
        """Coordinates nothing can improve on: a calibrated LiDAR."""
        return d.sensor == LIDAR and d.trusted

    def _coords(self, throw: Throw) -> Detection:
        """The detection whose coordinates the hit takes: a trusted LiDAR, else a camera, else the LiDAR."""
        lidar, camera = throw.dets.get(LIDAR), throw.dets.get(CAMERA)
        if lidar is not None and lidar.trusted:
            return lidar
        if camera is not None:
            return camera
        return lidar if lidar is not None else throw.first

    def _match(self, d: Detection) -> Throw | None:
        """The throw of the other sensor this detection belongs to: the closest one in time and on the
        wall, within match_time_s and the radius, that has no detection of this sensor yet."""
        best, score = None, math.inf
        for th in self.throws:
            if th.has(d.sensor):
                continue
            o = th.dets[self._other(d.sensor)]
            dt = abs(d.t - o.t)
            dist = math.hypot(d.x - o.x, d.y - o.y)
            if dt > self.match_time_s or dist > self.match_radius_cm:
                continue
            s = dist / max(self.match_radius_cm, 1e-6) + dt / max(self.match_time_s, 1e-6)
            if s < score:
                best, score = th, s
        return best

    def _repeat(self, d: Detection) -> Throw | None:
        """The same sensor seeing the same ball again (a flicker, a second sign of one hit)."""
        for th in self.throws:
            o = th.dets.get(d.sensor)
            if o is None:
                continue
            if abs(d.t - o.t) < self.merge_window_s and math.hypot(d.x - o.x, d.y - o.y) < self.merge_radius_cm:
                return th
        return None

    def _register(self, th: Throw, now: float, reason: str) -> FusedHit | None:
        c = self._coords(th)
        sensors = tuple(s for s in SENSORS if th.has(s))
        th.registered_at = now
        if sensors == (CAMERA,) and not self._camera_alone_ok():
            self.stats.refused += 1
            th.registered = FusedHit(c.x, c.y, c.t, c.sensor, sensors, now - th.arrived, "refused")
            log.info("fusion: camera hit at (%.0f, %.0f) cm refused: no LiDAR detection (camera_alone %s)",
                     c.x, c.y, self.camera_alone)
            return None
        hit = FusedHit(c.x, c.y, c.t, c.sensor, sensors, now - th.arrived, reason)
        th.registered = hit
        if len(sensors) == 2:
            self.stats.fused += 1
        elif sensors == (LIDAR,):
            self.stats.lidar_only += 1
        else:
            self.stats.camera_only += 1
        self.stats.waits += 1
        self.stats.wait_total += hit.waited
        self.stats.wait_max = max(self.stats.wait_max, hit.waited)
        log.info("fusion: hit at (%.0f, %.0f) cm from %s (%s; seen by %s), registered after %.0f ms",
                 hit.x, hit.y, hit.source, reason, "+".join(sensors), hit.waited * 1000)
        return hit

    def _camera_alone_ok(self) -> bool:
        if self.camera_alone == "always":
            return True
        if self.camera_alone == "never":
            return False
        return not self.alive(LIDAR)  # lidar_offline

    # -- per detection, per frame ---------------------------------------------------------------------------
    def feed(self, d: Detection, now: float) -> list[FusedHit]:
        """A detection of one sensor, in wall cm. Returns the hits registered by it (none or one)."""
        if not _valid(d):
            self.stats.rejected += 1
            log.info("fusion: %s detection with invalid values left out: %s", d.sensor, d)
            return []
        if d.sensor not in SENSORS or not self.enabled:
            return [FusedHit(d.x, d.y, d.t, d.sensor, (d.sensor,), 0.0, "passthrough")]
        out = self.poll(now)  # windows that ran out before this detection came come first
        th = self._match(d)
        if th is not None:
            th.dets[d.sensor] = d
            if th.registered is not None:
                self.stats.duplicates += 1
                if th.registered.reason in ("timeout", "alone"):
                    self.stats.late += 1
                log.info("fusion: %s detection at (%.0f, %.0f) cm is the throw already registered from %s: absorbed",
                         d.sensor, d.x, d.y, th.registered.source)
                return out
            hit = self._register(th, now, "matched")
            return out + ([hit] if hit is not None else [])
        if self._repeat(d) is not None:
            self.stats.repeats += 1
            return out
        th = Throw(d, now, now + self.window_s, {d.sensor: d})
        self.throws.append(th)
        partner_can_come = self.alive(self._other(d.sensor))
        if self._best(d) or not partner_can_come or self.window_s <= 0:
            hit = self._register(th, now, "lidar" if self._best(d) else "alone")
            if hit is not None:
                out.append(hit)
        return out

    def poll(self, now: float) -> list[FusedHit]:
        """Registers the throws whose window ran out (camera coordinates, or a rough LiDAR's), and
        forgets the old ones. Called every frame, also when no detection came."""
        out = []
        for th in self.throws:
            if th.registered is None and now >= th.deadline:
                hit = self._register(th, now, "timeout")
                if hit is not None:
                    out.append(hit)
            elif th.registered is None and not self.alive(self._other(th.first.sensor)):
                hit = self._register(th, now, "alone")  # its partner went away meanwhile: no use waiting
                if hit is not None:
                    out.append(hit)
        self.throws = [th for th in self.throws
                       if th.registered_at is None
                       or now - th.registered_at < max(self.window_s, self.match_time_s) + self.keep_s]
        return out

    @property
    def pending(self) -> int:
        return sum(1 for th in self.throws if th.registered is None)

    def reset(self) -> None:
        self.throws.clear()
