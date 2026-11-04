"""The STL27L lidar as an input source of the game.

On its own thread: open the serial port (and wait while it is unplugged), scan the empty wall
for the background, then turn every hit of the detector into an Impact in wall coordinates.
Nothing here can block or crash the render loop; problems show in F3 and the log.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
from pathlib import Path

from ...config import Section
from ..events import Impact
from ..sources import Source
from .capture import INSET_CM, targets
from .detect import TouchDetector, scan_background
from .wallmap import GameTuning, WallMap

log = logging.getLogger(__name__)


def read_points(points: object) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """input.lidar.calibration_points -> ((lidar mm), (wall cm)) pairs; bad entries are left out."""
    pairs = []
    for p in points if isinstance(points, list) else []:
        try:
            lx, ly = (float(v) for v in p["lidar_mm"])
            wx, wy = (float(v) for v in p["wall_cm"])
        except (TypeError, ValueError, KeyError):
            continue
        if all(math.isfinite(v) for v in (lx, ly, wx, wy)):
            pairs.append(((lx, ly), (wx, wy)))
    return pairs


class LidarSource(Source):
    name = "lidar"

    def __init__(self, out: queue.SimpleQueue, lcfg: Section, tuning_file: Path, wall_w: float, wall_h: float):
        super().__init__(out)
        self.port = str(lcfg.port)
        self.baud = int(lcfg.baud)
        self.wall_w, self.wall_h = wall_w, wall_h
        self.max_range = max(1000.0, float(lcfg.max_range_mm))
        self.tuning = GameTuning(str(tuning_file), wall_w, wall_h, max(0.0, float(lcfg.wall_margin_cm)), self.max_range)
        if self.tuning.load():
            log.info("lidar tuning from %s (mode %s)", tuning_file, self.tuning.mode)
        else:
            self.tuning.set("mode", "ball")  # one hit per throw
            log.warning("lidar tuning file %s not found; using the lidar defaults in ball mode", tuning_file)
        self.tuning_file = tuning_file
        self.detail = ""  # what the source is doing, for F2 / F3
        self.ready = False  # background scanned, hits are reported
        self._rescan = threading.Event()
        self._area_changed = threading.Event()
        self._tuning_changed = threading.Event()
        self.lcfg = lcfg
        self._load_calibration()

    def _load_calibration(self) -> bool:
        """input.lidar.calibration_points, if they were taken on a wall of this size. Returns True if used."""
        pairs = read_points(self.lcfg.calibration_points)
        crosses = targets(self.wall_w, self.wall_h)
        self.tuning.guide = None
        if pairs and any(min(math.dist(w, c) for c in crosses) > 1.0 for _, w in pairs):
            log.warning("input.lidar.calibration_points are for a wall of another size (display.fill_screen?): "
                        "not used - calibrate again (F2 then C)")
            try:  # still where the wall is, while the crosses are taken again
                self.tuning.guide = WallMap.fit(pairs)
                self.tuning.guide_size = (max(w[0] for _, w in pairs) + INSET_CM, max(w[1] for _, w in pairs) + INSET_CM)
            except ValueError:
                pass
            pairs = []
        if pairs:
            try:
                self.set_calibration(pairs)
                return True
            except ValueError as exc:
                log.warning("input.lidar.calibration_points not used: %s", exc)
        return False

    def set_wall(self, wall_w: float, wall_h: float) -> bool:
        """The wall has another size (display.fill_screen and a new display shape). The saved
        calibration counts again if it was made for this size. Returns True when it does."""
        self.wall_w, self.wall_h = wall_w, wall_h
        self.tuning.wall_w, self.tuning.wall_h = wall_w, wall_h
        self.tuning.fitted = None
        ok = self._load_calibration()
        self._area_changed.set()
        return ok

    # -- from the game (main thread) -------------------------------------------------------------
    def rescan(self) -> None:
        """Scan the background again (the wall must be clear meanwhile)."""
        self._rescan.set()

    def tuning_changed(self) -> None:
        """A value of the tuning changed (the LiDAR tab of F5): the thread rebuilds what is made from it."""
        self._tuning_changed.set()

    def set_capture(self, on: bool) -> None:
        self.tuning.capture = on
        self._area_changed.set()

    def set_calibration(self, pairs, wall_map: WallMap | None = None) -> WallMap:
        wall_map = wall_map or WallMap.fit(pairs)
        self.tuning.fitted = wall_map
        self._area_changed.set()
        return wall_map

    def status(self) -> dict:
        st = super().status()
        st["detail"] = self.detail
        return st

    # -- the thread ------------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            from .driver import D800
        except ImportError:  # optional dependency
            self.state, self.error = "error", "pyserial is not installed (pip install pyserial)"
            log.error(self.error)
            self._stop.wait()
            return
        lidar = D800(self.port, self.baud, max_range=self.max_range)
        detector = TouchDetector(self.tuning)
        try:
            while not self._stop.is_set():
                if lidar.ser is None:
                    self.state, self.detail = "connecting", f"waiting for the lidar on {self.port}"
                    self._stop.wait(0.2)
                    continue
                if not self.ready or self._rescan.is_set():
                    self._rescan.clear()
                    detector.tracker.release_all()
                    self.ready = self._scan_background(lidar, detector)
                    continue
                if self._tuning_changed.is_set():
                    self._tuning_changed.clear()
                    detector.background.update_threshold()  # the background gap
                    self._area_changed.set()  # the area, the angles, the turn
                if self._area_changed.is_set():
                    self._area_changed.clear()
                    detector.background.update_split()
                    lidar.split_angle = detector.background.split_angle
                self.state = "listening"
                self.detail = "" if self.tuning.calibrated or self.tuning.capture else "not calibrated: F2 then C"
                scans = lidar.get_scans(timeout=0.1)
                if not scans:
                    detector.tracker.expire(time.monotonic())
                    continue
                for scan in scans:
                    self._process(detector, scan)
        finally:
            self.ready = False
            lidar.close()

    def _scan_background(self, lidar, detector: TouchDetector) -> bool:
        seconds = self.tuning.calibration_time
        self.detail = f"background: keep the wall clear, {seconds:.0f} s"
        self.state = "background"
        log.info("lidar: scanning the background for %s s - keep the wall clear", seconds)
        seen = [lidar.scan_count]

        def progress(left: float) -> bool:
            if lidar.scan_count != seen[0]:
                seen[0] = lidar.scan_count
                self.last_packet = time.perf_counter()
            self.detail = f"background: keep the wall clear, {max(left, 0):.0f} s"
            return not self._stop.is_set() and lidar.ser is not None

        scans = scan_background(lidar, seconds, progress)
        if not scans:
            return False
        detector.calibrate(scans)
        self._area_changed.clear()
        lidar.split_angle = detector.background.split_angle
        log.info("lidar background ready: %d scans, split angle %.1f deg", len(scans), lidar.split_angle)
        return True

    def _process(self, detector: TouchDetector, scan) -> None:
        now = time.perf_counter()
        self.packets += 1
        self.last_packet = now
        t = now - max(0.0, time.monotonic() - scan.t)  # when the rotation that saw it ended
        wall_map = self.tuning.wall_map
        for state, touch in detector.process(scan):
            if state != 1:  # only the hit (DOWN); moves and releases mean nothing to the game
                continue
            wx, wy = wall_map.to_wall(touch.x, touch.y)
            self.events += 1
            log.info("lidar hit %d at (%.0f, %.0f) mm -> wall (%.0f, %.0f) cm%s", self.events, touch.x, touch.y, wx, wy,
                     "" if self.tuning.calibrated else " (not calibrated)")
            self.out.put(Impact(wx / self.wall_w, wy / self.wall_h, t, self.name, (float(touch.x), float(touch.y))))
