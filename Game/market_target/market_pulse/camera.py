"""The player's score replay for the results screen, from the IP camera in front of the wall.

The camera is read the way camera/camera_viewer.py reads it (RTSP through OpenCV's
FFmpeg, one decoder thread, only the newest frame, reconnecting on its own), and
people are found with the same YOLOv4-tiny model through cv2.dnn. The game's main
loop only flips a few flags; the work runs in background threads:

    camera thread   holds the one RTSP connection. Every frame is decoded (H.264 needs
                    that); while a round is played, camera.clip_fps of them a second are
                    turned into pictures and recorded as a strip that follows the player
                    (replay.ClipRecorder). At 1920x1080 that conversion is most of the cost.
    tracker thread  while a round is played, a few frames a second: find the people and
                    pick the player, so the strip knows where to be.
    replay thread   on the results screen: the face, the slow motion and the zoom
                    (replay.Replay).

The player is the person nearest the wall. With one camera facing the players that
is the tallest person in the picture whose feet are inside camera.play_zone. The
choice is kept from frame to frame by overlap, so someone walking past, or a
spectator further back, does not take over.

Every scoring hit may become the round's replay (one is drawn at random, bigger scores
more often): the camera.clip_before_s before it and camera.clip_after_s after. A round
without a score shows its last seconds. Frames live in memory only and are dropped
with the round.

OpenCV is optional: without it, the model files or the camera, the results screen
shows the placeholder and the game runs as before.
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .config import Section
from .replay import JPEG_QUALITY, STRIP_SIZE, Clip, ClipRecorder, Replay

log = logging.getLogger(__name__)

YOLO = ("yolov4-tiny.cfg", "yolov4-tiny.weights")
FACES = ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")

Box = tuple[int, int, int, int]  # x, y, w, h in frame pixels


def import_cv2() -> Any | None:
    # As camera_viewer.py: TCP (no grey frames from lost UDP packets) and no buffering.
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay")
    try:
        import cv2
    except ImportError:
        return None
    return cv2


# -- choosing the player (pure, no OpenCV) -----------------------------------------------------
def iou(a: Box, b: Box) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def pick_player(people: list[tuple[Box, float]], shape: tuple[int, ...], zone, previous: Box | None) -> int | None:
    """Index of the person most likely playing, or None if nobody is in the picture.

    Height in the picture stands for distance (the nearest person is the tallest).
    Feet outside the play zone count for a third, and the person who overlaps last
    frame's player counts half again, so the choice does not flicker."""
    h, w = shape[:2]
    best, best_score = None, 0.0
    for i, ((x, y, bw, bh), _conf) in enumerate(people):
        fx, fy = (x + bw / 2) / w, min(y + bh, h) / h
        in_zone = zone[0] <= fx <= zone[2] and zone[1] <= fy <= zone[3]
        score = bh / h * (1.0 if in_zone else 0.3)
        if previous is not None and iou(previous, (x, y, bw, bh)) >= 0.3:
            score *= 1.5
        if score > best_score:
            best, best_score = i, score
    return best


# -- seeing ---------------------------------------------------------------------------------------
class Judge:
    """Finds the player in a frame, and faces in the recorded strips."""

    def __init__(self, cv2, model_dir: Path, cam: Section):
        net = cv2.dnn.readNetFromDarknet(str(model_dir / YOLO[0]), str(model_dir / YOLO[1]))
        self.model = cv2.dnn.DetectionModel(net)
        self.model.setInputParams(size=(416, 416), scale=1 / 255.0, swapRB=True)
        self.faces = []
        for name in FACES:
            cascade = cv2.CascadeClassifier(str(model_dir / name))
            if cascade.empty():
                raise RuntimeError(f"could not read {name}")
            self.faces.append(cascade)
        self.cv2 = cv2
        self.conf = float(cam.person_confidence)
        self.zone = list(cam.play_zone)
        self.min_height = float(cam.min_person_height)
        self._lock = threading.Lock()  # the tracker and a replay may both look for faces

    def people(self, frame: np.ndarray) -> list[tuple[Box, float]]:
        scale = min(1.0, 640 / frame.shape[1])  # the network sees 416 px anyway
        small = self.cv2.resize(frame, None, fx=scale, fy=scale, interpolation=self.cv2.INTER_AREA) if scale < 1 else frame
        class_ids, scores, boxes = self.model.detect(small, confThreshold=self.conf, nmsThreshold=0.4)
        return [(tuple(int(v / scale) for v in box), float(score))
                for class_id, score, box in zip(np.ravel(class_ids), np.ravel(scores), boxes)
                if class_id == 0]  # COCO class 0 = person

    def track(self, frame: np.ndarray, previous: Box | None) -> Box | None:
        """The player's box in this frame, or None if nobody near enough is in it."""
        people = self.people(frame)
        i = pick_player(people, frame.shape, self.zone, previous)
        if i is None:
            return None
        box = people[i][0]
        return box if box[3] / frame.shape[0] >= self.min_height / 2 else None

    def face(self, frame: np.ndarray, region: Box, min_frac: float = 0.15) -> Box | None:
        """The largest face in the region: frontal first, then profile facing either way.
        min_frac: the smallest face, as a share of the region's width."""
        cv2 = self.cv2
        fh, fw = frame.shape[:2]
        x0, y0 = max(0, region[0]), max(0, region[1])
        x1, y1 = min(fw, region[0] + region[2]), min(fh, region[1] + region[3])
        if x1 - x0 < 24 or y1 - y0 < 24:
            return None
        gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        scale = min(1.0, 320 / gray.shape[1])
        if scale < 1:
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        gray = cv2.equalizeHist(gray)
        min_side = max(20, int(gray.shape[1] * min_frac))
        found: list[Box] = []
        with self._lock:
            for i, cascade in enumerate(self.faces):
                for flip in ((False, True) if i else (False,)):
                    img = cv2.flip(gray, 1) if flip else gray
                    for (x, y, w, h) in cascade.detectMultiScale(img, 1.1, 5, minSize=(min_side, min_side)):
                        if flip:
                            x = img.shape[1] - x - w
                        found.append((int(x), int(y), int(w), int(h)))
                if found:
                    break
        if not found:
            return None
        x, y, w, h = max(found, key=lambda f: f[2] * f[3])
        return (int(x0 + x / scale), int(y0 + y / scale), int(w / scale), int(h / scale))

    def sharpness(self, crop: np.ndarray) -> float:
        cv2 = self.cv2
        gray = cv2.cvtColor(cv2.resize(crop, (256, 256), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def strip_encoder(cv2) -> Callable[[np.ndarray, Box], bytes]:
    """Cuts the strip out of a camera frame, at STRIP_SIZE, as JPEG."""
    def encode(frame: np.ndarray, rect: Box) -> bytes:
        x, y, w, h = rect
        part = frame[y:y + h, x:x + w]
        interp = cv2.INTER_AREA if part.shape[1] > STRIP_SIZE[0] else cv2.INTER_LINEAR
        ok, jpeg = cv2.imencode(".jpg", cv2.resize(part, STRIP_SIZE, interpolation=interp),
                                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        return jpeg.tobytes()
    return encode


# -- the camera -----------------------------------------------------------------------------------
class CameraStream:
    """The RTSP stream in a background thread; camera_viewer.CameraStream, except that
    a frame becomes a BGR picture only when latest() asks for one or the recorder wants it."""

    MAX_LAG = 1.0  # seconds behind live before reconnecting with more decoder threads

    def __init__(self, cv2, url: str):
        self.cv2 = cv2
        self.url = url
        self.status = "connecting"
        self.size: tuple[int, int] | None = None
        self.frames = 0
        self.recorder: ClipRecorder | None = None
        self._frame: np.ndarray | None = None
        self._want = threading.Event()
        self._got = threading.Event()
        self._stop = threading.Event()
        self._rec_error = False
        self.thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self.thread.start()

    def latest(self, timeout: float = 1.0) -> np.ndarray | None:
        """The next frame from the camera (BGR), or None if none comes in time."""
        if self.status != "live":
            return None
        self._got.clear()
        self._want.set()
        if not self._got.wait(timeout):
            self._want.clear()
            return None
        frame, self._frame = self._frame, None
        return frame

    def _run(self) -> None:
        cv2 = self.cv2
        threads, warned = 1, False
        while not self._stop.is_set():
            try:
                cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
                    cv2.CAP_PROP_N_THREADS, threads,
                ])
                if not cap.isOpened():
                    cap.release()
                    self.status = "offline"
                    if not warned:  # the game runs for days; say it once, not every 3 s
                        log.warning("camera: cannot open %s; the results screen shows the placeholder until it answers",
                                    self.url)
                        warned = True
                    self._stop.wait(3.0)
                    continue
                warned = False
                self.size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
                log.info("camera: connected %dx%d, %d decoder thread(s), %s", *self.size, threads, self.url)
                self.status = "live"
                threads = self._read(cap, threads)
                cap.release()
            except Exception:  # never let the camera take the game down
                log.exception("camera: stream error")
            if self.status == "live":
                self.status = "reconnecting"
            self._stop.wait(1.0)

    def _read(self, cap, threads: int) -> int:
        cv2 = self.cv2
        min_offset = None
        while not self._stop.is_set():
            if not cap.grab():
                log.warning("camera: stream lost; reconnecting")
                return threads
            self.frames += 1
            now = time.monotonic()
            rec = self.recorder
            record = rec is not None and rec.wants_frame(now)
            if record or self._want.is_set():
                ok, frame = cap.retrieve()
                if ok and frame is not None and frame.size:
                    if record:
                        try:
                            rec.add(frame, now)
                        except Exception:
                            if not self._rec_error:
                                log.exception("camera: recording failed")
                                self._rec_error = True
                    if self._want.is_set():
                        self._frame = frame
                        self._want.clear()
                        self._got.set()
            # wall clock minus stream time stays constant while decoding keeps up
            pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if pos > 0:
                offset = time.time() - pos
                min_offset = offset if min_offset is None else min(min_offset, offset)
                if offset - min_offset > self.MAX_LAG and threads < 8:
                    log.warning("camera: decoding %.1f s behind; reconnecting with %d threads",
                                offset - min_offset, threads * 2)
                    self.status = "catching up"
                    return threads * 2
        return threads

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=3)


# -- one replay per round ---------------------------------------------------------------------------
class PlayerCam:
    """begin() at "go", scored() for every hit, tick() every frame, finish() for the results,
    cancel() on abort; replay() turns the round's clip into the card's video."""

    def __init__(self, cam: Section, stream, judge, cv2=None, rng: random.Random | None = None,
                 start_thread: bool = True, clock: Callable[[], float] = time.monotonic):
        self.cam = cam
        self.stream = stream
        self.judge = judge
        self.cv2 = cv2
        self.clock = clock
        self.interval = 1.0 / float(cam.samples_per_s)
        self.recorder = ClipRecorder(cam, strip_encoder(cv2) if cv2 is not None else None, rng)
        if hasattr(stream, "recorder"):
            stream.recorder = self.recorder
        self._lock = threading.Lock()
        self._session = 0
        self._tracking = False
        self._player: Box | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._error_logged = False
        self.clips = 0  # replays of a hit / of a round without a score / none, since start
        self.unscored = 0
        self.missed = 0
        self.thread = None
        if start_thread:
            self.thread = threading.Thread(target=self._run, name="player-tracker", daemon=True)
            self.thread.start()

    @classmethod
    def create(cls, cam: Section, model_dirs: list[Path]) -> tuple[PlayerCam | None, str]:
        """The camera operator, or None and the reason it cannot work on this PC."""
        cv2 = import_cv2()
        if cv2 is None:
            return None, "camera: OpenCV is not installed (pip install opencv-python-headless); no replays"
        model_dir = next((d for d in model_dirs if all((d / n).is_file() for n in YOLO + FACES)), None)
        if model_dir is None:
            return None, f"camera: model files {', '.join(YOLO + FACES)} not found; no replays"
        try:
            cv2.setNumThreads(2)  # leave the other cores to the game and the decoder
            judge = Judge(cv2, model_dir, cam)
        except Exception as exc:
            return None, f"camera: could not load the person detector ({exc}); no replays"
        log.info("camera: person detector from %s", model_dir)
        return cls(cam, CameraStream(cv2, cam.url), judge, cv2), ""

    # -- main thread ----------------------------------------------------------------------------
    def begin(self, now: float) -> None:
        """The round has started ("go"): forget the last player, start recording."""
        with self._lock:
            self._session += 1
            self._player = None
            self._tracking = True
        self.recorder.begin()
        self._wake.set()

    def scored(self, points: int) -> None:
        """A hit has just scored; it may become the replay."""
        if self.recorder.mark(points, self.clock()):
            log.info("camera: replay of the hit for %d points", points)

    def tick(self, now: float, active: bool) -> None:
        """Every frame; the player is followed while the round is played (not paused)."""
        tracking = active and self.recorder.active
        if tracking != self._tracking:
            self._tracking = tracking
            self._wake.set()

    def finish(self) -> Clip | None:
        """The results are up: the round's clip, or None. Recording stops."""
        with self._lock:
            self._session += 1
            self._tracking = False
        clip = self.recorder.finish()
        if clip is None:
            self.missed += 1
            log.info("camera: nothing recorded this round; placeholder")
        else:
            if clip.hit is None:
                self.unscored += 1
            else:
                self.clips += 1
            log.info("camera: replay of %.1f s, %d frames%s", clip.end - clip.start, len(clip.frames),
                     "" if clip.hit is not None else " (no score: the round's last seconds)")
        return clip

    def replay(self, clip: Clip | None) -> Replay | None:
        if clip is None or self.cv2 is None:
            return None
        judge = self.judge
        return Replay(self.cv2, clip, float(self.cam.slow_motion),
                      find_face=lambda img, region: judge.face(img, region, 0.08),
                      sharpness=judge.sharpness, min_sharpness=float(self.cam.min_sharpness))

    def cancel(self) -> None:
        with self._lock:
            self._session += 1
            self._tracking = False
            self._player = None
        self.recorder.cancel()

    def status(self) -> str:
        return (f"camera {getattr(self.stream, 'status', '?')}  {self.recorder.status()}  "
                f"replays {self.clips} unscored {self.unscored} none {self.missed}")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self.thread is not None:
            self.thread.join(timeout=3)
        self.recorder.cancel()
        stop = getattr(self.stream, "stop", None)
        if stop is not None:
            stop()

    # -- tracker thread ------------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            if not self.step():
                self._wake.wait(0.25)
                self._wake.clear()
                continue
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - t0)))

    def step(self) -> bool:
        """Look for the player in one frame if a round is played. False when there is nothing to do."""
        with self._lock:
            if not self._tracking:
                return False
            session, previous = self._session, self._player
        frame = self.stream.latest(1.0)
        if frame is None:
            return False
        try:
            box = self.judge.track(frame, previous)
        except Exception:
            if not self._error_logged:
                log.exception("camera: could not analyse a frame")
                self._error_logged = True
            return False
        with self._lock:
            if session != self._session:  # the round ended (or a new one began) meanwhile
                return True
            if box is not None:
                self._player = box
        self.recorder.follow(box)
        return True
