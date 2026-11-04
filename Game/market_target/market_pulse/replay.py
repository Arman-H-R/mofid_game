"""The player's score replay on the results card: recorded during the round, played back
like a sports replay, and ending on a still of the player's face that becomes the photo.

Recording (ClipRecorder, fed by the camera thread): every frame of the round, a tall strip
(STRIP_SIZE, 1:2, the card's shape) that follows the player like a camera operator would,
kept as JPEG for the last few seconds. Every scoring hit may become the round's highlight:
one is drawn at random, the more points the likelier, so the replay differs from round to
round but is usually a good one. Once the seconds after it are in, its frames are set aside.

Playback (Replay): a timeline that is a function of time only (Timeline),

    lead-up      the camera.clip_before_s before the hit at normal speed, pushing in slowly
    the throw    slows to camera.slow_motion, neighbouring frames blended so it stays smooth,
                 the edges darken and black bars close in like a film
    the hit      the points rise over the picture and the picture zooms onto the face
    reaction     at half speed
    the still    it holds on the clearest face of the clip, which takes the round's look
                 (camera.photo_effects) and is the card's photo from then on.

The frames are decoded and composed in a background thread just ahead of the screen; the
game's main loop only blits the newest one. Frames stay in memory and go with the round.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

STRIP_SIZE = (540, 1080)  # width, height of a recorded frame: the card's 1:2, full camera height
JPEG_QUALITY = 80

Box = tuple[int, int, int, int]  # x, y, w, h


def _smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


# -- the strip that follows the player (pure) --------------------------------------------------
def strip_rect(shape: tuple[int, ...], box: Box | None, zone) -> tuple[float, float, float]:
    """Where the strip should be: (centre x, top, height) in frame pixels. The player's whole
    body with some room above the head; the middle of the play zone while nobody is found."""
    h, w = shape[:2]
    if box is None:
        return (zone[0] + zone[2]) / 2 * w, 0.0, float(h)
    x, y, bw, bh = box
    sh = min(float(h), w * 2.0, max(bh * 1.3, h * 0.5))
    return x + bw / 2, y - sh * 0.08, sh


def fit_strip(shape: tuple[int, ...], cx: float, top: float, sh: float) -> Box:
    """The strip as a 1:2 rectangle inside the frame."""
    h, w = shape[:2]
    sh = min(sh, h, w * 2)
    sw = sh / 2
    left = int(round(min(max(cx - sw / 2, 0), w - sw)))
    top = int(round(min(max(top, 0), h - sh)))
    return left, top, max(1, int(sw)), max(2, int(sh))


# -- recording ------------------------------------------------------------------------------------
@dataclass
class Clip:
    frames: list[tuple[float, bytes]]  # (arrival time, JPEG of the strip), oldest first
    hit: float | None  # when the highlight shows in the frames; None: a round without a score
    points: int = 0

    @property
    def start(self) -> float:
        return self.frames[0][0]

    @property
    def end(self) -> float:
        return self.frames[-1][0]


class ClipRecorder:
    """Thread-safe: add() from the camera thread, follow() from the tracker, the rest from
    the game loop. Times are time.monotonic() seconds (the camera stamps frames with it)."""

    def __init__(self, cam, encode: Callable[[np.ndarray, Box], bytes] | None, rng: random.Random | None = None):
        self.cam = cam
        self._read_settings()
        self.encode = encode
        self.rng = rng or random.Random()
        self._lock = threading.Lock()
        self._frames: deque[tuple[float, bytes]] = deque()
        self._active = False
        self._target: Box | None = None
        self._strip: tuple[float, float, float] | None = None
        self._last_t: float | None = None
        self._due: float | None = None
        self._best_key = -1.0
        self._pending: tuple[float, int] | None = None  # the chosen hit while its after-part is recorded
        self._highlight: Clip | None = None
        self._error_logged = False

    def _read_settings(self) -> None:
        """Again for every round, so the settings page's changes apply from the next one."""
        cam = self.cam
        self.before = float(cam.clip_before_s)
        self.after = float(cam.clip_after_s)
        self.delay = float(cam.delay_s)  # how far the picture is behind the sensor
        self.interval = 1.0 / float(cam.clip_fps)
        self.zone = list(cam.play_zone)
        self.keep = self.before + self.after + self.delay + 1.0

    def begin(self) -> None:
        with self._lock:
            self._reset()
            self._read_settings()
            self._active = True

    def cancel(self) -> None:
        with self._lock:
            self._reset()

    def _reset(self) -> None:
        self._active = False
        self._frames.clear()
        self._target = self._strip = self._last_t = self._due = None
        self._best_key = -1.0
        self._pending = self._highlight = None

    @property
    def active(self) -> bool:
        return self._active

    def follow(self, box: Box | None) -> None:
        """The tracker's latest idea of where the player is (frame pixels)."""
        if box is not None:
            self._target = box

    def wants_frame(self, t: float) -> bool:
        return self._active and (self._due is None or t >= self._due)

    def add(self, frame: np.ndarray, t: float) -> None:
        """A camera frame (BGR) that arrived at t."""
        if not self._active:
            return
        dt = 0.0 if self._last_t is None else t - self._last_t
        self._last_t = t
        want = strip_rect(frame.shape, self._target, self.zone)
        if self._strip is None:
            self._strip = want
        else:  # glide after the player (about a third of a second behind), no jumps
            k = 1.0 - np.exp(-dt / 0.35)
            self._strip = tuple(a + (b - a) * k for a, b in zip(self._strip, want))
        rect = fit_strip(frame.shape, *self._strip)
        try:
            jpeg = self.encode(frame, rect) if self.encode is not None else b""
        except Exception:
            if not self._error_logged:
                log.exception("replay: could not encode a frame")
                self._error_logged = True
            return
        self.store(t, jpeg)

    def store(self, t: float, jpeg: bytes) -> None:
        with self._lock:
            if not self._active:
                return
            # the next frame is due one interval on; a frame that comes a little late does not push it back
            self._due = t + self.interval if self._due is None else max(self._due + self.interval, t - self.interval / 2)
            self._frames.append((t, jpeg))
            while self._frames and self._frames[0][0] < t - self.keep:
                self._frames.popleft()
            if self._pending is not None and t >= self._pending[0] + self.after:
                self._highlight = self._cut(*self._pending)
                self._pending = None

    def mark(self, points: int, t: float) -> bool:
        """A scoring hit at t (sensor time). True if it is now the round's highlight."""
        if points <= 0:
            return False
        with self._lock:
            if not self._active:
                return False
            key = self.rng.random() ** (1.0 / points)  # weighted random choice, one pass
            if key <= self._best_key:
                return False
            self._best_key = key
            self._pending = (t + self.delay, int(points))
            return True

    def finish(self) -> Clip | None:
        """The round's clip, and recording stops. A round without a score gets its last seconds."""
        with self._lock:
            if not self._active:
                return None
            clip = self._highlight
            if self._pending is not None:  # its after-part was cut short by the end of the round
                clip = self._cut(*self._pending)
            if clip is None and self._frames:
                end = self._frames[-1][0]
                clip = self._cut(end - self.after, 0, scored=False)
            self._reset()
        return clip if clip is not None and len(clip.frames) >= 2 else None

    def _cut(self, at: float, points: int, scored: bool = True) -> Clip | None:
        frames = [f for f in self._frames if at - self.before <= f[0] <= at + self.after]
        if len(frames) < 2:
            return None
        return Clip(frames, at if scored else None, points)

    def status(self) -> str:
        with self._lock:
            span = self._frames[-1][0] - self._frames[0][0] if len(self._frames) > 1 else 0.0
            return f"rec {len(self._frames)} frames {span:.0f}s{' +hl' if self._highlight else ''}"


# -- playback -----------------------------------------------------------------------------------
@dataclass
class Moment:
    s: float  # the clip time on screen
    zoom: float  # 0 the whole strip .. 1 the face
    slow: float  # 0 normal speed .. 1 full slow motion
    poster: float  # 0 the video .. 1 the still
    since_hit: float | None  # playback seconds since the hit; None before it (or without one)
    progress: float  # of the video, 0..1


def _smooth_arr(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


class Timeline:
    """Playback time (seconds since the replay started) to clip time, zoom and the rest."""

    STEP = 1 / 200
    SLOW_IN = (1.8, 0.9)  # slowing down starts this long before the hit, and takes this long
    REACTION = (0.4, 0.5)  # after the hit: back up to half speed, over this long
    POSTER_IN = 0.9  # seconds the still takes to come in after the video

    def __init__(self, start: float, end: float, hit: float | None, slow: float = 0.3):
        self.start, self.end = start, max(end, start + 0.1)
        self.scored = hit is not None
        self.hit = min(max(hit, start), self.end) if hit is not None else max(start, self.end - 2.0)
        self.slow = min(1.0, max(0.05, slow))
        ss = np.linspace(self.start, self.end, max(2, int((self.end - self.start) / self.STEP) + 1))
        tau = np.concatenate([[0.0], np.cumsum(np.diff(ss) / self.speed(ss)[:-1])])
        self.ss, self.tau = ss, tau
        self.length = float(tau[-1])
        self.tau_hit = float(np.interp(self.hit, ss, tau))

    def speed(self, s):
        h = self.hit
        into = _smooth_arr((np.asarray(s, float) - (h - self.SLOW_IN[0])) / self.SLOW_IN[1])
        out = _smooth_arr((np.asarray(s, float) - (h + self.REACTION[0])) / self.REACTION[1])
        v = 1.0 + (self.slow - 1.0) * into
        return v + (0.5 - v) * out

    def zoom(self, s: float) -> float:
        h = self.hit
        lead = max(0.1, h - self.SLOW_IN[0] - self.start)
        push = 0.12 * min(1.0, max(0.0, (s - self.start) / lead))  # a slow push in during the lead-up
        face = _smooth((s - (h - 0.25)) / 0.9)
        return push + (1.0 - push) * face

    def moment(self, tau: float) -> Moment:
        tau = max(0.0, tau)
        if tau >= self.length:
            s, poster = self.end, _smooth((tau - self.length) / self.POSTER_IN)
        else:
            s, poster = float(np.interp(tau, self.tau, self.ss)), 0.0
        slow = float((1.0 - self.speed(s)) / (1.0 - self.slow)) if self.slow < 1 else 0.0
        since = tau - self.tau_hit if self.scored and tau >= self.tau_hit else None
        return Moment(s, self.zoom(s), min(1.0, max(0.0, slow)), poster, since,
                      min(1.0, tau / max(self.length, 1e-6)))


def grader(cv2) -> Callable[[np.ndarray], np.ndarray]:
    """Makes a camera frame read from across the room: local contrast on the lightness
    (indoor pictures are flat) and a little more colour. BGR in and out."""
    clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(4, 4))

    def grade(img: np.ndarray) -> np.ndarray:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        lab[..., 0] = clahe.apply(lab[..., 0])
        hsv = cv2.cvtColor(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), cv2.COLOR_BGR2HSV)
        hsv[..., 1] = cv2.convertScaleAbs(hsv[..., 1], alpha=1.15)
        return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    return grade


class Replay:
    """One round's replay. start() when the card knows the picture's size; surface(tau) every
    frame (never waits); close() when the round is gone."""

    FPS = 30  # frames composed per second of playback
    AHEAD = 5  # frames prepared ahead of the screen
    FACE_GUESS = (0.5, 0.17, 0.24)
    ZOOM_FACES = 2.4  # the close-up is this many face widths wide  # centre x, centre y, width of the head, as fractions of the strip

    def __init__(self, cv2, clip: Clip, slow: float = 0.3, find_face: Callable | None = None,
                 sharpness: Callable | None = None, min_sharpness: float = 60.0, threaded: bool = True):
        self.cv2 = cv2
        self.clip = clip
        self.times = np.array([t for t, _ in clip.frames], float)
        self.timeline = Timeline(clip.start, clip.end, clip.hit, slow)
        self.length = self.timeline.length
        self.points = clip.points
        self.scored = clip.hit is not None
        self._find_face = find_face
        self._sharpness = sharpness
        self._min_sharp = max(1.0, float(min_sharpness))
        self._grade = grader(cv2)
        self.threaded = threaded
        self.size: tuple[int, int] | None = None
        self.look = "natural"
        self._decoded: dict[int, np.ndarray] = {}
        self._faces: list[tuple[float, float, float, float]] | None = None  # (t, cx, cy, width) in strip px
        self._poster_i = len(self.times) - 1
        self._poster: np.ndarray | None = None
        self._still = None
        self._vignettes: dict[int, np.ndarray] = {}
        self._ready: dict[int, Any] = {}
        self._shown = None
        self._want = -1
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._finder: threading.Thread | None = None
        self.compose_ms: deque[float] = deque(maxlen=240)

    # -- the game loop -----------------------------------------------------------------------------
    def start(self, size: tuple[int, int], look: str = "natural") -> None:
        size = (max(2, int(size[0])), max(2, int(size[1])))
        if self.size == size and self.look == look:
            return
        self.close()
        self._stop.clear()
        self.size, self.look = size, look
        self._vignettes.clear()
        self._poster = self._still = self._shown = None
        self._ready, self._want = {}, -1
        if not self.threaded:
            self._analyse()
        else:
            self._thread = threading.Thread(target=self._run, name="replay", daemon=True)
            self._thread.start()
            if self._faces is None and self._finder is None:
                self._finder = threading.Thread(target=self._find_later, name="replay-face", daemon=True)
                self._finder.start()

    def info(self, tau: float) -> Moment:
        return self.timeline.moment(tau)

    def surface(self, tau: float):
        """The picture for playback time tau, or None before the first one is ready."""
        if self.size is None:
            return None
        k = max(0, int(tau * self.FPS))
        if not self.threaded:
            return self._surface(k)
        with self._lock:
            self._want = k
            ready = [n for n in self._ready if n <= k]
            if ready:
                n = max(ready)
                self._shown = self._ready[n][0]
                for old in [m for m in self._ready if m < n]:
                    del self._ready[old]
        self._wake.set()
        return self._shown

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in (self._thread, self._finder):
            if thread is not None:
                thread.join(timeout=2)
        self._thread = self._finder = None
        with self._lock:
            self._ready.clear()
        self._decoded.clear()

    # -- the replay thread ---------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                with self._lock:
                    want = self._want
                    k = max(want, 0)
                    while k in self._ready:
                        k += 1
                if want < 0 or k > want + self.AHEAD:
                    self._wake.wait(0.02 if want >= 0 else 0.1)
                    self._wake.clear()
                    continue
                item = self._surface(k, keep=False)
                with self._lock:
                    self._ready[k] = item  # surface() drops the ones the screen has passed
        except Exception:
            log.exception("replay: stopped; the card keeps the last picture")

    # -- composing a frame -----------------------------------------------------------------------------
    def _surface(self, k: int, keep: bool = True):
        import pygame
        t0 = time.perf_counter()
        m = self.timeline.moment(k / self.FPS)
        if m.poster >= 1.0:
            if self._still is None:
                img = self._poster_image()
                self._still = (pygame.image.frombuffer(img.data, self.size, "BGR"), img)
            item = self._still
        else:
            img = np.ascontiguousarray(self._compose(m))
            item = (pygame.image.frombuffer(img.data, self.size, "BGR"), img)  # the array stays with it
        self.compose_ms.append((time.perf_counter() - t0) * 1000)
        if keep:
            self._shown = item[0]
            return item[0]
        return item

    def _frame(self, i: int) -> np.ndarray:
        img = self._decoded.get(i)
        if img is None:
            cv2 = self.cv2
            img = cv2.imdecode(np.frombuffer(self.clip.frames[i][1], np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                img = np.zeros((STRIP_SIZE[1], STRIP_SIZE[0], 3), np.uint8)
            img = self._grade(img)
            if len(self._decoded) >= 8:
                del self._decoded[next(iter(self._decoded))]
            self._decoded[i] = img
        return img

    def _compose(self, m: Moment) -> np.ndarray:
        cv2 = self.cv2
        n = len(self.times)
        i = int(min(max(np.searchsorted(self.times, m.s, "right") - 1, 0), n - 1))
        j = min(i + 1, n - 1)
        w = 0.0 if j == i else float(np.clip((m.s - self.times[i]) / max(1e-6, self.times[j] - self.times[i]), 0, 1))
        if m.slow > 0.05 and 0.04 < w < 0.96:  # slow motion: blend the two frames either side
            img = cv2.addWeighted(self._frame(i), 1 - w, self._frame(j), w, 0)
        else:
            img = self._frame(j if w >= 0.5 else i)
        out = self._crop(img, m.s, m.zoom)
        out = self._vignette(out, 0.18 + 0.32 * m.slow)
        if m.poster > 0:
            out = cv2.addWeighted(out, 1 - m.poster, self._poster_image(), m.poster, 0)
        return out

    def _crop(self, img: np.ndarray, s: float, zoom: float) -> np.ndarray:
        """The part of the strip on screen: all of it at zoom 0, the head and shoulders at 1."""
        cv2 = self.cv2
        H, W = img.shape[:2]
        cx, cy, fw = self._face_at(s, W, H)
        rh = min(float(np.clip(fw * self.ZOOM_FACES, W * 0.3, W)) * 2.0, H)
        rw = rh / 2.0
        rx = min(max(cx - rw / 2, 0), W - rw)
        ry = min(max(cy - rh * 0.36, 0), H - rh)
        z = _smooth(zoom)
        x0, y0 = rx * z, ry * z
        x1, y1 = W + (rx + rw - W) * z, H + (ry + rh - H) * z
        part = img[int(y0):max(int(y0) + 2, int(round(y1))), int(x0):max(int(x0) + 2, int(round(x1)))]
        interp = cv2.INTER_AREA if part.shape[1] > self.size[0] else cv2.INTER_LINEAR
        return cv2.resize(part, self.size, interpolation=interp)

    def _vignette(self, img: np.ndarray, strength: float) -> np.ndarray:
        level = int(round(min(1.0, max(0.0, strength)) * 20))
        vmap = self._vignettes.get(level)
        if vmap is None:
            w, h = self.size
            y = np.linspace(-1.0, 1.0, h, dtype=np.float32)[:, None]
            x = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
            r = np.sqrt(x * x * 1.2 + (y + 0.2) ** 2 * 0.8)
            v = 1.0 - level / 20 * np.clip(r - 0.4, 0.0, 1.0) ** 1.5
            vmap = np.repeat((v * 255).astype(np.uint8)[..., None], 3, axis=2)
            self._vignettes[level] = vmap
        return self.cv2.multiply(img, vmap, scale=1 / 255)

    def _poster_image(self) -> np.ndarray:
        """The still: the clearest face of the clip, close up, in the round's look."""
        if self._poster is None:
            from .ui import photo_fx
            if self._finder is not None:
                self._finder.join(timeout=2)
            if self._faces is None:
                self._analyse()
            i = self._poster_i
            img = self._crop(self._frame(i), self.times[i], 1.0)
            if self.look != "natural":
                rgb = self.cv2.cvtColor(img, self.cv2.COLOR_BGR2RGB)
                img = self.cv2.cvtColor(photo_fx.apply(np.ascontiguousarray(rgb), self.look), self.cv2.COLOR_RGB2BGR)
            self._poster = np.ascontiguousarray(self._vignette(img, 0.25))
        return self._poster

    # -- the face ----------------------------------------------------------------------------------------
    FIND_AFTER_S = 1.2  # the face search waits until the card's entrance is over (the zoom is seconds away)

    def _find_later(self) -> None:
        if not self._stop.wait(self.FIND_AFTER_S):
            self._analyse()

    def _analyse(self) -> None:
        """Find the face around the hit and after it, a few frames a second."""
        if self._faces is not None:
            return
        faces, best = [], (0.0, len(self.times) - 1)
        if self._find_face is not None:
            last = -1e9
            for i, t in enumerate(self.times):
                if self._stop.is_set() and self.threaded:
                    break
                if t < self.timeline.hit - 0.6 or t - last < 0.2:
                    continue
                last = t
                img = self.cv2.imdecode(np.frombuffer(self.clip.frames[i][1], np.uint8), self.cv2.IMREAD_COLOR)
                if img is None:
                    continue
                H, W = img.shape[:2]
                try:
                    face = self._find_face(img, (0, 0, W, int(H * 0.62)))
                except Exception:
                    log.exception("replay: face search failed")
                    break
                if face is None:
                    continue
                x, y, fw, fh = face
                faces.append((float(t), x + fw / 2, y + fh / 2, float(fw)))
                sharp = 1.0
                if self._sharpness is not None and fw > 4 and fh > 4:
                    sharp = min(self._sharpness(img[y:y + fh, x:x + fw]) / self._min_sharp, 2.0)
                score = fw * fh * sharp
                if score > best[0]:
                    best = (score, i)
                if len(faces) >= 14:
                    break
        self._faces = faces
        self._poster_i = best[1]
        log.info("replay: %d face(s) found around the hit", len(faces))

    def _face_at(self, s: float, W: int, H: int) -> tuple[float, float, float]:
        faces = self._faces
        if not faces:
            gx, gy, gw = self.FACE_GUESS
            return W * gx, H * gy, W * gw
        ts = [f[0] for f in faces]
        cx, cy, fw = (float(np.interp(s, ts, [f[k] for f in faces])) for k in (1, 2, 3))
        return cx, cy, fw
