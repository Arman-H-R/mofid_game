"""The wall camera as an input source of the game.

On its own thread: read the camera (CameraStream reconnects by itself), learn the empty wall, and turn
every hit of the detector into an Impact in wall coordinates. Nothing here can block or crash the
render loop; problems show in F2 / F3 and the log.

The calibration (picture -> wall cm) is input.camera.calibration_file. It comes from camtool.py
calibrate (crosses on a real wall) or from F2 then C in the game: the wall shows a grid of markers,
the camera finds them, and the fit is saved. A calibration made on crosses covers the game's whole
wall (its four crosses are the wall's corners); one made with markers is in the game's own cm, so it
only fits a wall of the same size. Those cm are real only if the game's wall size is the picture's
real size; if not, input.camera.screen_width_cm / screen_height_cm give the real size (the ball's
size and speed are real cm).

With markers the camera sees the game's own picture: what the game draws is left out (screen.py).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ..events import Impact
from ..sources import Source
from . import markers
from .detect import HitDetector, Settings, TouchDetector
from .geometry import DEFAULT_LENS, Lens, WallMap
from .screen import THUMB_W, ScreenChanges
from .stream import open_stream

log = logging.getLogger(__name__)

MARKER_WAIT_S = 0.5  # the board is on the wall from the next frame or two
MARKER_TIMEOUT_S = 20.0
PREVIEW_W = 320  # the F2 picture of the camera, px wide
BEHIND_FRAMES = 10  # more frames than this waiting: skip to the newest (a hit late is worse than none)
LATE_S = 0.6  # a picture that came this long after the camera took it (the camera or its network stalled):
#               skipped, or its hit lands seconds late, on the next throw
STALL_S = 0.5  # no picture for this long: a ball track does not go on across the gap
LATE_LOG_S = 10.0  # at most one warning about late pictures this often


class CameraSource(Source):
    name = "camera"
    THUMB_W = THUMB_W  # the game hands its frames this wide (screen_drawn)

    def __init__(self, out: queue.SimpleQueue, ccfg, calibration_file: Path, save_file: Path,
                 wall_w: float, wall_h: float):
        """ccfg: the live input.camera section (the settings page changes it while the game runs).
        calibration_file is read; a new calibration is written to save_file (next to the game)."""
        super().__init__(out)
        self.cfg = ccfg
        self.calibration_file = calibration_file
        self.save_file = save_file
        self.wall_w, self.wall_h = float(wall_w), float(wall_h)
        self.detail = ""  # what the source is doing, for F2 / F3
        self.fps = 0.0
        self.saved_map: WallMap | None = WallMap.load(calibration_file)
        if self.saved_map is None:
            log.warning("camera: no calibration in %s - calibrate it (F2 then C, or camtool.py calibrate)",
                        calibration_file)
        else:
            m = self.saved_map
            log.info("camera calibration from %s: %s, %.0f x %.0f cm, error %.1f cm", calibration_file,
                     m.method or "?", *m.wall_size, m.rms_cm)
        self.map: WallMap | None = None
        self.map_fits = True  # the calibration is for a wall of this size
        self.screen = ScreenChanges()  # what the game draws on the wall (with markers: the camera sees it)
        self._apply_map()
        self._rebuild = threading.Event()  # the map or a setting changed: a new detector
        self._rescan = threading.Event()
        self._placed: list[markers.Placed] | None = None  # markers on the wall: calibrating
        self._collector: markers.MarkerCollector | None = None
        self._marker_t0 = 0.0
        self._marker_wall = (self.wall_w, self.wall_h)
        self.markers_seen = 0
        self.marker_result: tuple[bool, str] | None = None  # the game shows it, then sets it back to None
        self.want_preview = False  # F2 is open: keep a small picture of the camera
        self.preview: np.ndarray | None = None  # RGB, preview_w wide
        self.preview_w = PREVIEW_W  # the sensor test shows it bigger...
        self.preview_every_s = 0.2  # ...and with every frame (0)
        self._preview_at = 0.0
        self.late_frames = 0  # pictures skipped because they came seconds late (a stalling camera)
        self._late_logged, self._late_counted, self._late_worst = -LATE_LOG_S, 0, 0.0

    # -- the calibration --------------------------------------------------------------------------
    def _apply_map(self) -> None:
        """The detector works in the calibration's own cm (real cm for crosses: the ball's size and
        speed are real); a hit is then reported as a share of that wall, which is the game's wall."""
        m = self.saved_map
        if m is None:
            self.map, self.map_fits = None, True
            return
        same = abs(m.wall_size[0] - self.wall_w) < 1.0 and abs(m.wall_size[1] - self.wall_h) < 1.0
        self.map_fits = same or m.method != "markers"
        self.map = m
        real = self.real_screen_size(m)
        if real is not None:
            self.map = m.scaled_to(real)

    def real_screen_size(self, m: WallMap) -> tuple[float, float] | None:
        """The real size of the game's picture (cm), for a calibration made with markers in the game's
        cm: screen_width_cm and screen_height_cm (one of them alone keeps the picture's shape)."""
        if m.method != "markers":
            return None
        sw = float(self.cfg.get("screen_width_cm", 0) or 0)
        sh = float(self.cfg.get("screen_height_cm", 0) or 0)
        w, h = m.wall_size
        if sw > 0 and sh > 0:
            return sw, sh
        if sw > 0:
            return sw, h * sw / w
        if sh > 0:
            return w * sh / h, sh
        return None

    @property
    def wants_screen(self) -> bool:
        """The camera sees the game's picture (a calibration made with markers): the game hands it
        every frame it shows (screen_drawn), and the detector leaves out what changed there."""
        return (self.map is not None and self.map.method == "markers" and bool(self.cfg.get("ignore_screen", True))
                and str(self.cfg.get("mode", "ball")) == "ball")

    def screen_drawn(self, t: float, grey: np.ndarray) -> None:
        """Main thread: the frame the game just showed (its wall area, grey, THUMB_W wide)."""
        self.screen.add(t, grey)

    @property
    def calibrated(self) -> bool:
        return self.map is not None and self.map_fits

    def set_wall(self, wall_w: float, wall_h: float) -> bool:
        """The wall has another size (display.fill_screen and a new display shape). Returns False
        when the calibration was made with markers on a wall of another size (calibrate again)."""
        self.wall_w, self.wall_h = float(wall_w), float(wall_h)
        self._apply_map()
        return self.map_fits

    # -- from the game (main thread) -------------------------------------------------------------
    def rescan(self) -> None:
        """Learn the empty wall again (it must be clear for a moment)."""
        self._rescan.set()

    def settings_changed(self) -> None:
        """A value of input.camera changed (settings page): the detector is made again from it."""
        self._rebuild.set()

    def start_markers(self) -> list[markers.Placed]:
        """Calibrate with markers: the game draws these on the wall until marker_result is set."""
        cols, rows = (int(v) for v in self.cfg.markers)
        placed = markers.grid(self.wall_w, self.wall_h, cols, rows)
        self.marker_result = None
        self.markers_seen = 0
        self._marker_wall = (self.wall_w, self.wall_h)
        self._collector = markers.MarkerCollector(placed)
        self._marker_t0 = time.monotonic()
        self._placed = placed  # last: the thread starts collecting now
        return placed

    def stop_markers(self) -> None:
        self._placed = None

    def status(self) -> dict:
        st = super().status()
        st["detail"] = self.detail
        st["fps"] = self.fps
        st["late"] = self.late_frames
        return st

    # -- the thread ------------------------------------------------------------------------------
    def _make_detector(self):
        s = Settings.from_dict(self.cfg)
        mode = str(self.cfg.get("mode", "ball"))
        det = TouchDetector(self.map, s) if mode == "touch" else HitDetector(self.map, s)
        log.info("camera: %s detector, ball %.0f cm (%s), threshold %d, min speed %.0f cm/s, colour %s, "
                 "picture %.0f x %.0f cm, floor margin %.0f cm", mode, s.ball_cm, s.ball_tone, s.threshold,
                 s.min_speed, "on" if s.color else "off", *self.map.wall_size, s.floor_margin_cm)
        return det

    def _run(self) -> None:
        try:
            stream = open_stream(self.cfg)
        except (RuntimeError, ValueError) as exc:  # PyAV missing, a wrong setting
            self.state, self.error = "error", str(exc)
            log.error("camera: %s", exc)
            self._stop.wait()
            return
        det = None
        warned_behind = False
        last_t = None
        try:
            self.state, self.detail = "connecting", f"waiting for the camera ({self.cfg.host})"
            while not self._stop.is_set():
                fr = stream.get(timeout=0.25)
                self.fps = stream.fps
                if fr is None:
                    if stream.status != "live":
                        self.state = "connecting"
                        self.detail = f"camera {stream.status}: {stream.error or self.cfg.host}"
                    continue
                if stream.frames.qsize() > BEHIND_FRAMES:  # the PC fell behind: only the newest frame
                    while stream.frames.qsize() > 1:
                        stream.get(timeout=0)
                    stream.gap = True
                    if not warned_behind:
                        log.warning("camera: frames came faster than they were checked; some were skipped")
                        warned_behind = True
                    continue
                self.packets += 1
                self.last_packet = time.perf_counter()
                placed = self._placed
                if placed is not None:
                    self._marker_frame(fr, placed)
                    det = None  # the wall showed the board: learn it again afterwards
                    self._update_preview(fr, None)
                    continue
                if self.map is None:
                    self.state, self.detail = "listening", "not calibrated: F2 then C (or camtool.py calibrate)"
                    self._update_preview(fr, None)
                    continue
                if det is None or self._rebuild.is_set():
                    self._rebuild.clear()
                    self._rescan.clear()
                    self._apply_map()  # the picture's real size may have changed
                    det = self._make_detector()
                if self._rescan.is_set():
                    self._rescan.clear()
                    det.reset()
                    log.info("camera: learning the empty wall again")
                if self._too_late(fr):
                    stream.gap = True
                    self._update_preview(fr, None)
                    continue
                stalled = last_t is not None and fr.t - last_t > STALL_S
                last_t = fr.t
                if stream.gap or stalled:
                    stream.gap = False
                    if isinstance(det, HitDetector):
                        det.tracks.clear()  # frames are missing: no track across the gap
                if isinstance(det, HitDetector):
                    drawn = None
                    if self.wants_screen:
                        size = (fr.image.shape[1], fr.image.shape[0])
                        drawn = self.screen.mask(fr.t, self.map, size)
                    hits = det.process(fr.image, fr.t, ignore=drawn)
                else:
                    hits = det.process(fr.image, fr.t)
                for h in hits:
                    self._emit(h)
                self.state = "listening"
                self.detail = "" if self.map_fits else "the calibration is for another wall size: F2 then C"
                self._update_preview(fr, det)
        finally:
            stream.stop()

    def _too_late(self, fr) -> bool:
        """A picture that came seconds after it was taken: the camera (or its network) stalled and is
        catching up. Its hit would count on the player's next throw, so it is skipped; logged now and then."""
        late = float(getattr(fr, "late", 0.0))
        if late <= LATE_S:
            return False
        self.late_frames += 1
        self._late_worst = max(self._late_worst, late)
        now = time.monotonic()
        if now - self._late_logged >= LATE_LOG_S:
            log.warning("camera: %d pictures came late (up to %.1f s after they were taken) and were skipped: "
                        "the camera or its network is stalling (cable, other viewers, a busy PC)",
                        self.late_frames - self._late_counted, self._late_worst)
            self._late_logged, self._late_counted, self._late_worst = now, self.late_frames, 0.0
        return True

    def _emit(self, h) -> None:
        self.events += 1
        mw, mh = self.map.wall_size
        x, y = min(max(h.x / mw, 0.0), 1.0), min(max(h.y / mh, 0.0), 1.0)  # inside the margin: at the edge
        log.info("camera hit %d (%s) at (%.0f, %.0f) cm of the calibrated %.0f x %.0f, game (%.0f, %.0f) cm, "
                 "picture (%.0f, %.0f), size %.2f, %.0f ms ago", self.events, h.kind, h.x, h.y, mw, mh,
                 x * self.wall_w, y * self.wall_h, h.px, h.py, h.size, (time.perf_counter() - h.t) * 1000)
        self.out.put(Impact(x, y, h.t, self.name, (float(h.px), float(h.py))))

    def _marker_frame(self, fr, placed: list[markers.Placed]) -> None:
        elapsed = time.monotonic() - self._marker_t0
        self.state = "calibrating"
        self.detail = f"markers: the camera sees {self.markers_seen} of {len(placed)}"
        if elapsed < MARKER_WAIT_S:
            return
        col = self._collector
        self.markers_seen = col.add(cv2.cvtColor(fr.image, cv2.COLOR_BGR2GRAY))
        seen = col.averaged(min_count=5)
        enough = len(seen) >= max(4, int(0.7 * len(placed))) or (elapsed > 10.0 and len(seen) >= 4)
        if not enough:
            if elapsed > MARKER_TIMEOUT_S:
                self._placed = None
                self.marker_result = (False, f"the camera found only {len(seen)} of {len(placed)} markers "
                                             f"(is the whole wall in its picture?)")
                log.warning("camera calibration failed: %s", self.marker_result[1])
            return
        img, wall = markers.pairs(seen, placed)
        size = (fr.image.shape[1], fr.image.shape[0])
        lens = self.saved_map.lens if self.saved_map is not None else Lens.from_dict(DEFAULT_LENS)
        self._placed = None
        try:
            m = WallMap.fit(img, wall, size, self._marker_wall, lens, method="markers")
        except (ValueError, cv2.error) as exc:
            self.marker_result = (False, f"the fit failed ({exc})")
            log.warning("camera calibration failed: %s", exc)
            return
        saved = ", saved"
        try:
            m.save(self.save_file)
        except OSError as exc:
            saved = ", NOT saved (see the log)"
            log.error("camera calibration not saved to %s: %s", self.save_file, exc)
        log.info("camera calibrated with %d markers: error %.2f cm, worst %.2f cm, lens k1 %+.3f -> %s", len(seen),
                 m.rms_cm, m.max_cm, m.lens.k1, self.save_file)
        self.saved_map = m
        self._apply_map()
        self._rebuild.set()
        self.marker_result = (True, f"Camera calibrated with {len(seen)} markers: off by {m.rms_cm:.1f} cm "
                                    f"on average, {m.max_cm:.1f} cm at most{saved}")

    def _update_preview(self, fr, det) -> None:
        """A small picture for F2: what the camera sees, the wall's outline (green) and the last hits."""
        if not self.want_preview:
            return
        now = time.perf_counter()
        if now - self._preview_at < self.preview_every_s:
            return
        self._preview_at = now
        h, w = fr.image.shape[:2]
        pw = min(int(self.preview_w), w)
        ph = max(1, int(round(h * pw / w)))
        small = cv2.resize(fr.image, (pw, ph), interpolation=cv2.INTER_AREA)
        if self.map is not None:
            cv2.polylines(small, [self.map.outline((pw, ph))], True, (0, 255, 0), 1, cv2.LINE_AA)
        if det is not None:
            k = pw / w
            for hit in list(getattr(det, "hits", ()))[-5:]:
                cv2.circle(small, (int(hit.px * k), int(hit.py * k)), 5, (0, 0, 255), 2, cv2.LINE_AA)
        self.preview = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
