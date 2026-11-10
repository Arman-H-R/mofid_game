"""Ball hits from the camera picture.

HitDetector, per frame (gray, inside the wall area plus a margin):
  1. what is new: |frame - background| AND |frame - previous frame| over a threshold. The background is
     a running average (it learns slow changes: light, a screen's slow motion); the second test keeps
     only what moved since the last frame, so a fast ball stands out and still things do not.
     With colour on (Settings.color) the change is the distance in Lab, brightness and colour together:
     a ball as bright as the picture behind it but of another colour is seen too, and for ball_tone
     light a clear colour change counts like "lighter" (a shadow only gets darker). It costs more CPU,
     so it is worked on the area's box only.
  2. blobs of about a ball's size (bigger when the ball is near the camera, longer with motion blur);
     pieces close together are one blob (at the wall the ball is often seen in parts). What moves and
     reaches the edge of the area (a person, an arm, a ball still in the hand) is not a ball. Their
     centres are mapped onto the wall (WallMap: lens + homography).
  3. tracks: each blob joins the track whose next position it fits, else starts one.
  4. the hit, whichever sign comes first:
     - depth: the ball is farthest from the camera when it touches the wall, so its width in the picture
       (across its motion, so motion blur does not count) is smallest then. 1/width ~ distance rises
       while it flies in and falls after the bounce; the peak of a two-line fit is the moment of the hit.
     - turn: the picture of a ball mapped onto the wall runs towards the point it will hit and, after
       the bounce, its depth motion reverses, which bends the mapped path sharply (strongest when the
       camera is high or to the side, so throws do not run along its line of sight).
     - bend: the same, seen over more points: a ball dropping steeply onto the wall (thrown high) bends
       its path only 30-45 degrees, and one noisy point hides that from the turn sign. A straight
       line before (3-4 points) and after (3 points) that meet at bend_deg or more.
     - rebound: the way in was hidden (thrown straight away from the camera, behind the thrower's arm);
       a track that starts on the wall at a ball's size there and then grows is the ball coming back.
       Not when it starts at the edge of the area looked at: there it flew in from outside.
     - lob: a soft throw rises out of the hand (not seen), just reaches the wall at the top of its arc
       and drops: a track that appears on the wall at a ball's size there, not shrinking, nearly still.
     A soft sign (a turn or bend under strong_deg, a slow-down) waits up to wait_frames more points: a
     ball thrown in a dive bends a little in the air before the wall and much more at the wall, and
     the sharper bend that follows is the hit. Sharp turns, depth, rebound and lob count at once.
     At the moment of the hit the ball is on the wall, so the mapped point is exact there. The point is
     taken from the track on both sides of that moment; the time comes from the camera's own frame
     times (jitter-free).

A hit counts up to margin_cm outside the wall (the picture), but below its bottom edge only
floor_margin_cm: there is the floor, and a ball bouncing on it towards the camera is no wall hit.

One hit per track. If more than max_fg_share of the wall changes at once (the light changed, the
camera adjusted its exposure), the frame is skipped and the background relearns quickly.

TouchDetector is for testing the calibration by hand: the tip of anything that reaches into the wall
area (the point farthest from where it comes in) held still for dwell_s is a "touch".
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, fields

import cv2
import numpy as np

from .geometry import WallMap

TONE_SHARE = 0.18  # ball_tone light (dark): at least this share of a blob is clearly lighter (darker) than the wall
LOB_SIZE_MAX = 1.25  # a lob's ball must look its size on the wall (a ball still in the air looks bigger)
SAME_BALL_S = 0.6  # a hit this soon after another one, and
SAME_BALL_BALLS = 3.0  # this many ball widths from it: the same ball bouncing back, not a new throw


@dataclass
class Settings:
    ball_cm: float = 8.0  # ball diameter
    threshold: int = 18  # grey-level change that counts as "new"
    learn: float = 0.05  # background learning per frame
    min_speed: float = 120.0  # cm/s (picture mapped on the wall) before the turn, for a throw
    turn_deg: float = 50.0  # direction change that is a hit
    bend_deg: float = 32.0  # ...or this much between the straight lines before and after (the bend sign)
    strong_deg: float = 70.0  # a turn/bend this sharp counts at once; a softer one waits for a sharper one
    wait_frames: int = 2  # ...this many more points at most (0: never wait)
    slow_ratio: float = 0.35  # ...or the speed drops to this share of the speed before
    min_track: int = 4  # points in a track before a hit can be called (3 before the turn + 1 after)
    depth_rise: float = 0.12  # the depth sign: distance must rise at least this share before the peak
    margin_cm: float = 25.0  # hits this far outside the wall still count (clamped to the edge by the game)
    floor_margin_cm: float = 0.0  # ...but below the wall's bottom edge (the floor side) only this far
    color: bool = False  # what is new: colour and brightness (Lab distance), not brightness alone
    max_fg_share: float = 0.25
    max_speed: float = 3000.0  # cm/s: the farthest a blob may jump between frames and stay on one track
    max_region: float = 12.0  # a ball is a separate blob: what is new around it at most this x a ball (a person is more)
    # At the hit the ball is on the wall, so its width in the picture must be a ball's width there:
    size_min: float = 0.4  # x the expected width (a dark, blurred ball is measured small: only parts of it differ)
    size_max: float = 1.6
    ball_tone: str = "any"  # any | light | dark: the ball against the wall (a dark sleeve is not a light ball)
    edge_reject: bool = True  # what reaches in from the edge of the area (a person, an arm) is not a ball
    end_is_hit: bool = False  # a fast track that vanishes inside the wall counts as a hit at its end
    touch_dwell_s: float = 0.4
    touch_still_cm: float = 4.0

    @classmethod
    def from_dict(cls, d) -> "Settings":
        s = cls()
        get = d.get if hasattr(d, "get") else (lambda k, default=None: getattr(d, k, default))
        for f in fields(cls):
            v = get(f.name, None)
            if v is not None:
                setattr(s, f.name, type(getattr(s, f.name))(v))
        return s

    def set_text(self, name: str, text: str) -> None:
        """One value typed as text (camtool --set key=value). A switch takes 1/0, true/false, on/off."""
        if name not in {f.name for f in fields(self)}:
            raise KeyError(f"no detector setting {name!r}")
        old = getattr(self, name)
        if isinstance(old, bool):
            low = text.strip().lower()
            if low not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
                raise ValueError(f"{name} is a switch: 1 or 0, not {text!r}")
            setattr(self, name, low in ("1", "true", "yes", "on"))
        else:
            setattr(self, name, type(old)(text))


@dataclass
class Blob:
    px: float
    py: float
    area: int
    box: tuple[int, int, int, int]
    diam: float  # width across the motion, px
    tone: float = 0.0  # share clearly lighter than the wall minus share clearly darker (-1..1)


@dataclass
class Point:
    t: float
    x: float  # wall cm
    y: float
    px: float  # picture
    py: float
    area: int
    diam: float = 0.0
    tone: float = 0.0


@dataclass
class Hit:
    x: float  # wall cm
    y: float
    t: float  # time.perf_counter() of the impact
    px: float  # where in the picture
    py: float
    kind: str  # "depth" | "turn" | "bend" | "slow" | "rebound" | "lob" | "end" | "touch"
    speed: float = 0.0  # cm/s before the hit
    angle: float = 0.0  # direction change, degrees
    size: float = 0.0  # its width at the hit / a ball's width on the wall there


@dataclass
class Track:
    id: int
    pts: list[Point] = field(default_factory=list)
    missed: int = 0
    hit: bool = False
    pending: "Hit | None" = None  # a soft hit, waiting for a sharper bend just after it
    waited: int = 0

    def velocity(self, i: int = -1) -> tuple[float, float, float]:
        """(vx, vy, speed) of the segment ending at point i, cm/s."""
        a, b = self.pts[i - 1], self.pts[i]
        dt = max(b.t - a.t, 1e-3)
        vx, vy = (b.x - a.x) / dt, (b.y - a.y) / dt
        return vx, vy, math.hypot(vx, vy)

    def predict(self, t: float) -> tuple[float, float]:
        last = self.pts[-1]
        if len(self.pts) < 2:
            return last.x, last.y
        vx, vy, _ = self.velocity()
        dt = t - last.t
        return last.x + vx * dt, last.y + vy * dt


def _angle(ax, ay, bx, by) -> float:
    na, nb = math.hypot(ax, ay), math.hypot(bx, by)
    if na < 1e-9 or nb < 1e-9:
        return 180.0
    c = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(c))


def _intersect(p1, p2, p3, p4):
    """Crossing of line p1-p2 and line p3-p4, or None if (nearly) parallel."""
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (p4[0] - p3[0], p4[1] - p3[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    n1, n2 = math.hypot(*d1), math.hypot(*d2)
    if n1 < 1e-9 or n2 < 1e-9 or abs(den) < 0.25 * n1 * n2:  # less than ~15 degrees apart
        return None
    s = ((p3[0] - p1[0]) * d2[1] - (p3[1] - p1[1]) * d2[0]) / den
    return p1[0] + s * d1[0], p1[1] + s * d1[1]


class _Base:
    extra_balls = 0.0  # the area looked at reaches this many ball widths past the margin

    def __init__(self, wall_map: WallMap, settings: Settings | None = None):
        self.map = wall_map
        self.s = settings or Settings()
        self.size: tuple[int, int] | None = None
        self.bg: np.ndarray | None = None
        self.prev: np.ndarray | None = None
        self.roi: np.ndarray | None = None
        self.roi_count = 1
        self.mask: np.ndarray | None = None  # last foreground (for a debug view)
        self.skipped = 0  # frames skipped for a light change
        self.gain = 1.0  # exposure compensation of the last frame
        self._sample: np.ndarray | None = None  # pixel indices (inside the area) for the brightness match
        self.bgc: np.ndarray | None = None  # colour mode: the background in Lab (float), the area's box only
        self.prevc: np.ndarray | None = None
        self._box = (0, 0, 0, 0)  # x, y, w, h: the area's bounding box

    def _gray(self, image: np.ndarray) -> np.ndarray:
        g = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        g = cv2.GaussianBlur(g, (3, 3), 0)
        if self.bg is not None and self._sample is not None and g.shape == self.bg.shape:
            # the camera changes its exposure when a dark person walks in: match the background's brightness
            cur = float(np.median(g.ravel()[self._sample]))
            ref = float(np.median(self.bg.ravel()[self._sample]))
            gain = min(1.4, max(0.7, ref / max(cur, 1.0)))
            if abs(gain - 1.0) > 0.02:
                g = cv2.convertScaleAbs(g, alpha=gain)
            self.gain = gain
        return g

    def _init(self, gray: np.ndarray) -> None:
        h, w = gray.shape
        self.size = (w, h)
        self.bg = gray.astype(np.float32)
        self.prev = gray
        roi = np.zeros((h, w), np.uint8)
        cv2.fillPoly(roi, [self.map.outline(self.size, self.s.margin_cm + self.extra_balls * self.s.ball_cm)], 255)
        self.roi = roi
        self.roi_count = max(1, int(np.count_nonzero(roi)))
        bx, by, bw, bh = cv2.boundingRect(roi)
        self._box = (bx, by, bw, bh) if bw > 0 and bh > 0 else (0, 0, w, h)
        self.bgc = self.prevc = None
        self.roi_inner = cv2.erode(roi, np.ones((9, 9), np.uint8))  # a tip on the area's edge is a cut, not a tip
        cut = roi.copy()  # the picture's own border is an edge of the area too
        cut[:3, :] = cut[-3:, :] = 0
        cut[:, :3] = cut[:, -3:] = 0
        self._edge_band = (roi > 0) & (cv2.erode(cut, np.ones((5, 5), np.uint8)) == 0)
        self._edge_dist = cv2.distanceTransform(cut, cv2.DIST_L2, 3)  # px to the area's edge (or the picture's)
        idx = np.flatnonzero(roi.ravel())
        self._sample = np.random.default_rng(0).choice(idx, size=min(4000, len(idx)), replace=False) if len(idx) else None
        ppc = self.map.px_per_cm(self.size)
        d = self.s.ball_cm * ppc
        self.ball_area = math.pi / 4 * d * d
        self.ppc = ppc

    def reset(self) -> None:
        self.bg = None


class HitDetector(_Base):
    # A ball hitting near the margin's edge must not reach the area's edge (what reaches the edge is a
    # person coming in); hits still count only inside the margin (_inside).
    extra_balls = 1.5

    def __init__(self, wall_map: WallMap, settings: Settings | None = None):
        super().__init__(wall_map, settings)
        self.tracks: list[Track] = []
        self._next_track = 0
        self.hits: deque[Hit] = deque(maxlen=50)
        self.blobs: list[Blob] = []
        self.ended: deque[Track] = deque(maxlen=20)  # recent tracks, for a debug view
        self.debug = False  # keep what was thrown away and why (balltool.py)
        self.rejects: list[tuple[tuple[int, int, int, int], str]] = []  # (box, reason), last frame

    def reset(self) -> None:
        super().reset()
        self.tracks.clear()

    def process(self, image: np.ndarray, t: float, ignore: np.ndarray | None = None) -> list[Hit]:
        """ignore: pixels (non-zero) where the game's own picture on the wall just changed (screen.py):
        what moves there is the game, not a ball."""
        gray = self._gray(image)
        if self.bg is None or self.size != (gray.shape[1], gray.shape[0]):
            self._init(gray)
            if self.s.color:
                self.bgc = self.prevc = self._lab(image)
            return []
        s = self.s
        lab = self._lab(image) if s.color else None
        bg8 = cv2.convertScaleAbs(self.bg)
        colour = None
        if lab is not None:
            new, moved, colour = self._colour_changes(lab)
        else:
            new = cv2.absdiff(gray, bg8) > s.threshold
            moved = cv2.absdiff(gray, self.prev) > s.threshold
        if ignore is not None:
            new &= ignore == 0
            moved &= ignore == 0
        if self.debug:
            self.dbg_new, self.dbg_moved = new, moved
        fg = (new & moved).astype(np.uint8) * 255
        fg &= self.roi
        if np.count_nonzero(fg) > s.max_fg_share * self.roi_count:  # light change, not a ball
            cv2.accumulateWeighted(gray.astype(np.float32), self.bg, 0.5)
            self._learn_colour(lab, 0.5, None)
            self.prev, self.prevc = gray, lab
            self.tracks.clear()
            self.skipped += 1
            self.mask = fg
            return []
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        fg = cv2.dilate(fg, np.ones((3, 3), np.uint8))
        self.mask = fg
        signed = gray.astype(np.int16) - bg8.astype(np.int16)
        if colour is not None:  # a clear colour change counts like "lighter" (a shadow only gets darker)
            signed = np.where(colour, np.maximum(signed, s.threshold + 1), signed).astype(np.int16)
        self._signed = signed
        self.blobs = self._blobs(fg, new)
        hits = self._new_balls(self._track(self.blobs, t))
        # learn the background where nothing is moving (a ball does not burn in)
        still = cv2.bitwise_not(fg)
        cv2.accumulateWeighted(gray.astype(np.float32), self.bg, s.learn, mask=still)
        self._learn_colour(lab, s.learn, still)
        if ignore is not None:  # the game's picture changed there: the empty wall looks different now
            cv2.accumulateWeighted(gray.astype(np.float32), self.bg, 0.3, mask=ignore)
            self._learn_colour(lab, 0.3, ignore)
        self.prev, self.prevc = gray, lab
        self.hits.extend(hits)
        return hits

    # -- colour mode: Lab, on the area's box only ---------------------------------------------------
    def _lab(self, image: np.ndarray) -> np.ndarray:
        x, y, w, h = self._box
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        crop = np.ascontiguousarray(image[y:y + h, x:x + w])
        lab = cv2.cvtColor(cv2.GaussianBlur(crop, (3, 3), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
        if abs(self.gain - 1.0) > 0.02:  # the same exposure match as the grey picture (_gray)
            lab[..., 0] *= self.gain
        return lab

    def _colour_changes(self, lab: np.ndarray):
        """(new, moved, colour) over the whole picture: the Lab distance (lightness and both colour
        axes) to the background and to the last frame over the threshold, and where the colour alone
        changed that much. Outside the area's box nothing is new."""
        if self.bgc is None or self.bgc.shape != lab.shape:
            self.bgc = self.prevc = lab
        t2 = float(self.s.threshold) ** 2
        d = lab - self.bgc
        e = lab - self.prevc
        x, y, w, h = self._box
        out = [np.zeros(self.bg.shape, bool) for _ in range(3)]
        out[0][y:y + h, x:x + w] = np.einsum("ijk,ijk->ij", d, d) > t2
        out[1][y:y + h, x:x + w] = np.einsum("ijk,ijk->ij", e, e) > t2
        out[2][y:y + h, x:x + w] = np.einsum("ijk,ijk->ij", d[..., 1:], d[..., 1:]) > t2
        return out

    def _learn_colour(self, lab: np.ndarray | None, rate: float, mask: np.ndarray | None) -> None:
        if lab is None or self.bgc is None:
            return
        if self.bgc is self.prevc:  # the first frames shared one array: the background gets its own
            self.bgc = self.bgc.copy()
        x, y, w, h = self._box
        m = None if mask is None else np.ascontiguousarray(mask[y:y + h, x:x + w])
        cv2.accumulateWeighted(lab, self.bgc, rate, mask=m)

    def _new_balls(self, hits: list[Hit]) -> list[Hit]:
        """One ball, one hit. Coming back off the wall the ball flies at the camera and grows, often as a
        new track that turns or slows again: on the projector one ball was called twice, 0.17 s and
        26 cm apart. A hit close to one called just before is that bounce."""
        out: list[Hit] = []
        reach = SAME_BALL_BALLS * self.s.ball_cm
        for h in hits:
            if not any(abs(h.t - o.t) < SAME_BALL_S and math.hypot(h.x - o.x, h.y - o.y) < reach
                       for o in (*self.hits, *out)):
                out.append(h)
        return out

    def _blobs(self, fg: np.ndarray, new: np.ndarray) -> list[Blob]:
        a_min = max(4.0, 0.2 * self.ball_area)
        a_max = max(80.0, self.s.max_region * self.ball_area)
        # Pieces close together are one thing: at the wall the ball hardly moves in the picture (it moves
        # in depth) and a light ball looks like light bricks, so only parts of it differ - in pieces.
        ball_px = math.sqrt(4 / math.pi * self.ball_area)
        g = max(3, int(round(0.3 * ball_px)))
        group = cv2.dilate(fg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (g, g)))
        n, glabels, gstats, _ = cv2.connectedComponentsWithStats(group, connectivity=8)
        # what moves and reaches the edge of the area came in from outside it: a person, an arm (a ball
        # still in the hand). The moving pixels, not everything new: a light change can join anything.
        edge = set(np.unique(glabels[self._edge_band]).tolist()) - {0} if self.s.edge_reject else set()
        labels = np.where(fg > 0, glabels, 0)
        # everything new (not only what moved), joined: a person is one big region, a flying ball a small one
        new_u8 = cv2.dilate((new.astype(np.uint8) * 255) & self.roi, np.ones((3, 3), np.uint8))
        rn, rlabels, rstats, _ = cv2.connectedComponentsWithStats(new_u8, connectivity=8)
        self.regions = rstats
        out = []
        H, W = fg.shape
        rej = self.rejects
        rej.clear()
        debug = self.debug
        for i in range(1, n):
            gx, gy, gw, gh, _ = gstats[i]
            if i in edge:
                if debug:
                    rej.append(((gx, gy, gw, gh), "edge"))
                continue
            mine = labels[gy:gy + gh, gx:gx + gw] == i
            area = int(np.count_nonzero(mine))
            if not a_min <= area <= a_max:
                if debug and area >= 4:
                    rej.append(((gx, gy, gw, gh), "small" if area < a_min else "big"))
                continue
            ys, xs = np.nonzero(mine)
            x, y = gx + int(xs.min()), gy + int(ys.min())
            w, h = int(xs.max() - xs.min()) + 1, int(ys.max() - ys.min()) + 1
            cx, cy = gx + float(xs.mean()), gy + float(ys.mean())
            if max(w, h) > 8 * max(1, min(w, h)):  # a line, not a (blurred) ball
                if debug:
                    rej.append(((x, y, w, h), "line"))
                continue
            if area < 0.2 * w * h and gstats[i][4] < 0.5 * gw * gh:  # a ragged outline, not a filled blob
                if debug:
                    rej.append(((x, y, w, h), "ragged"))
                continue
            region = rlabels[gy:gy + gh, gx:gx + gw][mine]
            region = region[region > 0]
            if region.size:
                r = int(np.bincount(region).argmax())
                rx, ry, rw, rh, ra = rstats[r]
                if ra > a_max or ra < 0.25 * rw * rh or max(rw, rh) > 4 * max(1, min(rw, rh)):
                    if debug:
                        rej.append(((x, y, w, h), "part of big"))
                    continue  # part of something big or long (a person, an arm), not a ball on its own
            # its width across the motion, from everything new there (not cut by the motion test or dilated)
            sub = new[max(0, y - 1):min(H, y + h + 1), max(0, x - 1):min(W, x + w + 1)]
            ys, xs = np.nonzero(sub)
            if len(xs) >= 5:
                lam = np.linalg.eigvalsh(np.cov(np.stack([xs, ys]).astype(np.float64)))[0]
                diam = 4.0 * math.sqrt(max(lam, 1.0 / 16))
            else:
                diam = 2.0 * math.sqrt(area / math.pi)
            # its tone: the share clearly lighter than the wall minus the share clearly darker. Not the mean:
            # the ball's own shadow and its darker panels are part of the blob too. From everything new
            # where it is, not only what moved: at the wall the ball hardly moves, and what moves there is
            # mostly its edge and its shadow.
            foot = (glabels[gy:gy + gh, gx:gx + gw] == i) & new[gy:gy + gh, gx:gx + gw]
            signed = self._signed[gy:gy + gh, gx:gx + gw][foot]
            nf = max(1, signed.size)
            light = float(np.count_nonzero(signed > self.s.threshold)) / nf
            dark = float(np.count_nonzero(signed < -self.s.threshold)) / nf
            tone = light - dark
            if self.s.ball_tone == "light" and light < TONE_SHARE:
                if debug:
                    rej.append(((x, y, w, h), f"not light {light:.0%}"))
                continue
            if self.s.ball_tone == "dark" and dark < TONE_SHARE:
                if debug:
                    rej.append(((x, y, w, h), f"not dark {dark:.0%}"))
                continue
            out.append(Blob(cx, cy, area, (x, y, w, h), float(diam), tone))
        return out

    def _track(self, blobs: list[Blob], t: float) -> list[Hit]:
        s = self.s
        wall = self.map.to_wall([(b.px, b.py) for b in blobs], self.size) if blobs else np.zeros((0, 2))
        cands = []
        for ti, tr in enumerate(self.tracks):
            last = tr.pts[-1]
            dt = max(t - last.t, 1e-3)
            px, py = tr.predict(t)
            speed = tr.velocity()[2] if len(tr.pts) >= 2 else 0.0
            reach = max(3 * s.ball_cm, 1.3 * speed * dt + 2 * s.ball_cm) if len(tr.pts) >= 2 else s.max_speed * dt
            reach = min(reach, s.max_speed * dt + 2 * s.ball_cm)
            for bi, (wx, wy) in enumerate(wall):
                if math.hypot(wx - last.x, wy - last.y) <= reach:
                    cands.append((math.hypot(wx - px, wy - py), ti, bi))
        cands.sort()
        used_t, used_b = set(), set()
        for _, ti, bi in cands:
            if ti in used_t or bi in used_b:
                continue
            used_t.add(ti)
            used_b.add(bi)
            b = blobs[bi]
            self.tracks[ti].pts.append(Point(t, float(wall[bi][0]), float(wall[bi][1]), b.px, b.py, b.area, b.diam, b.tone))
            self.tracks[ti].missed = 0
        hits = []
        for ti, tr in enumerate(self.tracks):
            if ti in used_t:
                h = (self._check_turn(tr) or self._check_depth(tr) or self._check_bend(tr) or self._check_rebound(tr)
                     or self._check_lob(tr))
                h = self._sharper(tr, h)
                if h is not None:
                    hits.append(h)
            else:
                tr.missed += 1
        keep = []
        for tr in self.tracks:
            if tr.missed > 2 and tr.pending is not None:  # it is gone: the soft hit was the hit
                hits.append(tr.pending)
                tr.pending, tr.hit = None, True
            if tr.missed > 2:
                if s.end_is_hit and not tr.hit:
                    h = self._check_end(tr)
                    if h is not None:
                        hits.append(h)
                if len(tr.pts) >= 3:
                    self.ended.append(tr)
            else:
                keep.append(tr)
        self.tracks = keep
        for bi, b in enumerate(blobs):
            if bi not in used_b:
                self._next_track += 1
                tr = Track(self._next_track)
                tr.pts.append(Point(t, float(wall[bi][0]), float(wall[bi][1]), b.px, b.py, b.area, b.diam, b.tone))
                self.tracks.append(tr)
        for tr in self.tracks:  # long tracks: only the recent part matters
            if len(tr.pts) > 40:
                del tr.pts[:-40]
        return hits

    def _soft(self, h: Hit) -> bool:
        return h.kind in ("turn", "bend", "slow") and h.angle < self.s.strong_deg and self.s.wait_frames > 0

    def _sharper(self, tr: Track, h: Hit | None) -> Hit | None:
        """A soft hit waits (tr.pending) for up to wait_frames more points; a sharper one in that time,
        or a depth / rebound / lob, takes its place. Returns the hit to call now, if any."""
        if tr.pending is None:
            if h is not None and self._soft(h):
                tr.pending, tr.waited, tr.hit = h, 0, False  # look on: the checks run again
                return None
            return h
        tr.waited += 1
        if h is not None:
            later = h.t > tr.pending.t + 1e-3  # an earlier moment (a depth fit's peak) is not the sharper bend after
            if later and (not self._soft(h) or h.angle >= tr.pending.angle + 20.0):
                if self._soft(h) and tr.waited < self.s.wait_frames:
                    tr.pending, tr.hit = h, False  # sharper, but maybe not the sharpest yet
                else:
                    tr.pending, tr.hit = None, True
                    return h
            else:
                tr.hit = False  # the same bend seen again: keep the first
        if tr.waited >= self.s.wait_frames:
            h, tr.pending, tr.hit = tr.pending, None, True
            return h
        return None

    def _near_edge(self, px: float, py: float) -> bool:
        """Within about two ball widths of the edge of the area looked at (or of the picture)."""
        h, w = self._edge_dist.shape
        x, y = min(max(int(px), 0), w - 1), min(max(int(py), 0), h - 1)
        return float(self._edge_dist[y, x]) < 2.0 * self.s.ball_cm * self.ppc

    def _inside(self, x: float, y: float) -> bool:
        """In the area where a hit counts: the margin all round, but below the bottom edge (the floor
        side) only floor_margin_cm. A ball bouncing on the floor towards the camera is seen below
        the picture, and the margin there made it a hit."""
        w, h = self.map.wall_size
        m = self.s.margin_cm
        return -m <= x <= w + m and -m <= y <= h + min(m, max(0.0, self.s.floor_margin_cm))

    def _size_ratio(self, x: float, y: float, diam: float) -> float:
        """Width seen / width a ball would have on the wall at (x, y). Blur and the mask's dilation make
        every blob ~2 px wider; that matters for a far, small ball, so it is taken off first."""
        r = self.s.ball_cm / 2
        ends = self.map.to_image([(x - r, y), (x + r, y), (x, y - r), (x, y + r)], self.size)
        expected = 0.5 * (np.linalg.norm(ends[1] - ends[0]) + np.linalg.norm(ends[3] - ends[2]))
        return max(diam - 2.0, 0.0) / max(expected, 1e-6)

    def _size_ok(self, ratio: float) -> bool:
        return self.s.size_min <= ratio <= self.s.size_max

    def _check_turn(self, tr: Track) -> Hit | None:
        s = self.s
        p = tr.pts
        if tr.hit or len(p) < max(3, s.min_track):
            return None
        a, b, c = p[-3], p[-2], p[-1]
        vix, viy, s_in = tr.velocity(-2)
        vox, voy, s_out = tr.velocity(-1)
        if s_in < s.min_speed:
            return None
        if len(p) >= 4:  # it came in straight (a real throw, not noise jumping about)
            vpx, vpy, s_prev = tr.velocity(-3)
            if s_prev < 0.4 * s.min_speed or _angle(vpx, vpy, vix, viy) > 40.0:
                return None
        ang = _angle(vix, viy, vox, voy) if s_out > 1e-6 else 180.0
        kind = "turn" if ang >= s.turn_deg else "slow" if s_out <= s.slow_ratio * s_in else ""
        if not kind:
            return None
        # the point: where the line it came in on meets the line it left on
        x, y, ti = b.x, b.y, b.t
        if len(p) >= 4:
            q = p[-4]
            cross = _intersect((q.x, q.y), (a.x, a.y), (b.x, b.y), (c.x, c.y))
            seg = max(math.hypot(b.x - a.x, b.y - a.y), math.hypot(c.x - b.x, c.y - b.y), s.ball_cm)
            if cross is not None and math.hypot(cross[0] - b.x, cross[1] - b.y) <= 1.5 * seg:
                x, y = cross
                _, _, s_q = tr.velocity(-3)
                ti = a.t + math.hypot(x - a.x, y - a.y) / max(s_q, 1e-3)
                ti = min(max(ti, a.t), c.t)
        if not self._inside(x, y):
            return None
        ratio = self._size_ratio(x, y, max(a.diam, b.diam, c.diam))  # the fullest view: at the wall it is often in parts
        if not self._size_ok(ratio):
            return None
        tr.hit = True
        ipx, ipy = self.map.to_image([(x, y)], self.size)[0]
        return Hit(float(x), float(y), float(ti), float(ipx), float(ipy), kind, float(s_in), float(ang), float(ratio))

    def _check_depth(self, tr: Track) -> Hit | None:
        """The distance to the camera (~ 1 / width in the picture) peaked: rose, then fell."""
        s = self.s
        if tr.hit or len(tr.pts) < 5:
            return None
        pts = tr.pts[-9:]
        if len(pts) == len(tr.pts) and len(pts) > 6:
            pts = pts[1:]  # a track's first blob is often cut (leaving the hand, entering the area)
        t = np.array([p.t for p in pts])
        z = np.array([1.0 / max(p.diam, 0.5) for p in pts])
        n = len(pts)
        best = None
        for k in range(2, n - 2):  # the peak between point k and k+1: 3+ points before it, 2+ after
            for tau in np.linspace(t[k], t[k + 1], 6):
                A = np.stack([np.ones(n), np.minimum(t - tau, 0.0), np.maximum(t - tau, 0.0)], 1)
                coef = np.linalg.lstsq(A, z, rcond=None)[0]
                r = float(np.sum((A @ coef - z) ** 2))
                if best is None or r < best[0]:
                    best = (r, float(tau), coef, k)
        if best is None:
            return None
        r, tau, (a, b1, b2), k = best
        if b1 <= 0 or b2 >= 0 or k < n - 6:  # not a peak, or an old one (the track is well past it)
            return None
        noise = math.sqrt(r / max(1, n - 3))
        rise, fall = b1 * (tau - t[0]), -b2 * (t[-1] - tau)
        # a soft ball comes back slower than it went in: the fall may be much smaller than the rise
        if rise < max(3 * noise, s.depth_rise * a) or fall < max(2 * noise, 0.25 * s.depth_rise * a):
            return None
        # it must have come in at a throw's speed
        p0, p1 = pts[k - 1], pts[k]
        s_in = math.hypot(p1.x - p0.x, p1.y - p0.y) / max(p1.t - p0.t, 1e-3)
        z_rate = b1 / max(a, 1e-9)  # share of the distance per second
        if s_in < 0.25 * s.min_speed and z_rate < 1.0:
            return None
        # where it was at tau: from the points on both sides of it
        pa, pb = pts[k], pts[k + 1]
        def vel(i, j):
            return ((pts[j].x - pts[i].x) / max(pts[j].t - pts[i].t, 1e-3),
                    (pts[j].y - pts[i].y) / max(pts[j].t - pts[i].t, 1e-3))
        vin = vel(k - 1, k)
        vout = vel(k + 1, k + 2) if k + 2 < n else vel(k, k + 1)
        xin, yin = pa.x + vin[0] * (tau - pa.t), pa.y + vin[1] * (tau - pa.t)
        xout, yout = pb.x - vout[0] * (pb.t - tau), pb.y - vout[1] * (pb.t - tau)
        x, y = (xin + xout) / 2, (yin + yout) / 2
        if not self._inside(x, y):
            return None
        ratio = self._size_ratio(x, y, max(pa.diam, pb.diam))
        if not self._size_ok(ratio):
            return None
        tr.hit = True
        ipx, ipy = self.map.to_image([(x, y)], self.size)[0]
        return Hit(float(x), float(y), float(tau), float(ipx), float(ipy), "depth", float(s_in), 0.0, float(ratio))

    def _check_bend(self, tr: Track) -> Hit | None:
        """A straight line in (3-4 points), a straight line out (the last 3), bend_deg or more between
        them. Called two points after the bend: one more than the turn sign needs."""
        s = self.s
        p = tr.pts
        if tr.hit or len(p) < 6:
            return None
        before, after = p[-7:-3] if len(p) >= 7 else p[-6:-3], p[-3:]

        dts = [q.t - o.t for o, q in zip(p[-len(before) - 3:-1], p[-len(before) - 2:])]
        if max(dts) > 2.5 * min(dts):
            return None  # frames missing in between: the points may not be one ball

        def line(q):
            dx, dy = q[-1].x - q[0].x, q[-1].y - q[0].y
            n = math.hypot(dx, dy)
            if n < 1e-6:
                return None
            off = max(abs((a.x - q[0].x) * dy - (a.y - q[0].y) * dx) / n for a in q)  # cm off the chord
            for o, a in zip(q, q[1:]):  # every step goes along the line, about evenly: no jumping about
                sx, sy = a.x - o.x, a.y - o.y
                if (sx * dx + sy * dy) / n < 0.25 * n / (len(q) - 1) or _angle(sx, sy, dx, dy) > 50.0:
                    return None
            return dx, dy, n, off

        lb, la = line(before), line(after)
        if lb is None or la is None:
            return None
        straight = 0.5 * s.ball_cm
        if lb[3] > straight or la[3] > straight:
            return None  # not two straight runs: noise, or a ball curving in the air
        s_in = lb[2] / max(before[-1].t - before[0].t, 1e-3)
        s_out = la[2] / max(after[-1].t - after[0].t, 1e-3)
        if s_in < s.min_speed or s_out < 0.3 * s.min_speed:
            return None
        ang = _angle(lb[0], lb[1], la[0], la[1])
        if ang < s.bend_deg:
            return None
        b, c = before[-1], after[0]
        x, y, ti = b.x, b.y, b.t
        cross = _intersect((before[0].x, before[0].y), (b.x, b.y), (c.x, c.y), (after[-1].x, after[-1].y))
        seg = max(math.hypot(c.x - b.x, c.y - b.y), s.ball_cm)
        if cross is not None and math.hypot(cross[0] - b.x, cross[1] - b.y) <= 1.5 * seg:
            x, y = cross
            ti = min(max(b.t + math.hypot(x - b.x, y - b.y) / max(s_in, 1e-3), b.t), c.t)
        if not self._inside(x, y):
            return None
        ratio = self._size_ratio(x, y, max(b.diam, c.diam, before[-2].diam))
        if not self._size_ok(ratio):
            return None
        tr.hit = True
        ipx, ipy = self.map.to_image([(x, y)], self.size)[0]
        return Hit(float(x), float(y), float(ti), float(ipx), float(ipy), "bend", float(s_in), float(ang), float(ratio))

    def _check_rebound(self, tr: Track) -> Hit | None:
        """The way in was not seen (behind the thrower's arm: thrown straight away from the camera), but
        the way back is: a track that starts on the wall at a ball's size there and then grows (comes
        back towards the camera). It left the wall at its first point."""
        s = self.s
        p = tr.pts
        if tr.hit or not 4 <= len(p) <= 6:
            return None
        p0 = p[0]
        if not self._inside(p0.x, p0.y):
            return None
        if self._near_edge(p0.px, p0.py):
            return None  # it first showed up at the area's edge: it flew in from outside, not off the wall
        first = min(p[0].diam, p[1].diam)
        if not self._size_ok(self._size_ratio(p0.x, p0.y, first)):
            return None
        if p[-1].diam < 1.06 * first or p[-1].diam < max(q.diam for q in p[:-1]) * 0.97:
            return None  # not growing: not coming back
        if math.hypot(p[-1].x - p0.x, p[-1].y - p0.y) < 0.5 * s.ball_cm:
            return None  # hardly moving: a hand, a thing held still
        for h in self.hits:  # the same bounce, already called from the way in
            if abs(h.t - p0.t) < 0.6 and math.hypot(h.x - p0.x, h.y - p0.y) < 3 * s.ball_cm:
                return None
        tr.hit = True
        ratio = self._size_ratio(p0.x, p0.y, first)
        vx, vy, sp = tr.velocity(1)
        return Hit(p0.x, p0.y, p0.t, p0.px, p0.py, "rebound", sp, 0.0, float(ratio))

    def _check_lob(self, tr: Track) -> Hit | None:
        """A soft lob: the ball rises out of the hand (joined to the arm in the picture, so not seen),
        just reaches the wall at the top of its arc and drops. Too slow for a turn, and no depth peak or
        growth is seen: a track that appears on the wall at a ball's size there and stays nearly still."""
        s = self.s
        p = tr.pts
        if tr.hit or not 4 <= len(p) <= 6:
            return None
        q = p[-4:]
        if not all(self._inside(a.x, a.y) for a in q):
            return None
        mx, my = sum(a.x for a in q) / 4, sum(a.y for a in q) / 4
        if max(math.hypot(a.x - mx, a.y - my) for a in q) > 0.5 * s.ball_cm:
            return None  # moving: a flying ball (the other signs), not one at the top of its arc
        if q[2].diam + q[3].diam < 0.97 * (q[0].diam + q[1].diam):
            return None  # shrinking: still flying in, straight away from the camera (the depth sign's case)
        ratios = sorted(self._size_ratio(a.x, a.y, a.diam) for a in q)
        ratio = 0.5 * (ratios[1] + ratios[2])
        if not s.size_min <= ratio <= LOB_SIZE_MAX:
            return None  # nearer the camera than the wall: a ball still in the air, a hand
        for h in self.hits:  # the same bounce, already called
            if abs(h.t - q[0].t) < 0.6 and math.hypot(h.x - mx, h.y - my) < 3 * s.ball_cm:
                return None
        tr.hit = True
        top = min(q, key=lambda a: a.y)  # the top of the arc: where it touched (lower ones: falling, or its shadow)
        sp = max(tr.velocity(-k)[2] for k in (1, 2, 3))
        return Hit(top.x, top.y, top.t, top.px, top.py, "lob", sp, 0.0, float(ratio))

    def _check_end(self, tr: Track) -> Hit | None:
        p = tr.pts
        if len(p) < max(3, self.s.min_track - 1):
            return None
        _, _, sp = tr.velocity(-1)
        last = p[-1]
        w, h = self.map.wall_size
        if sp < self.s.min_speed or not (0 <= last.x <= w and 0 <= last.y <= h):
            return None
        tr.hit = True
        return Hit(last.x, last.y, last.t, last.px, last.py, "end", sp, 0.0)


class TouchDetector(_Base):
    """A hand (or anything) reaching into the wall area and held still: a touch at its tip."""

    def __init__(self, wall_map: WallMap, settings: Settings | None = None):
        super().__init__(wall_map, settings)
        self.tip: tuple[float, float, float, float] | None = None  # px, py, x cm, y cm
        self._hist: deque[tuple[float, float, float]] = deque()
        self._armed = True
        self._last_touch: tuple[float, float] | None = None
        self._gone_since: float | None = None

    def process(self, image: np.ndarray, t: float) -> list[Hit]:
        gray = self._gray(image)
        if self.bg is None or self.size != (gray.shape[1], gray.shape[0]):
            self._init(gray)
            return []
        s = self.s
        diff = cv2.absdiff(gray, cv2.convertScaleAbs(self.bg))
        fg = ((diff > s.threshold).astype(np.uint8) * 255) & self.roi
        if np.count_nonzero(fg) > 0.6 * self.roi_count:  # light change
            cv2.accumulateWeighted(gray.astype(np.float32), self.bg, 0.5)
            self.mask = fg
            return []
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        self.mask = fg
        # a slow background: a still hand stays "new" for many seconds
        cv2.accumulateWeighted(gray.astype(np.float32), self.bg, 0.002, mask=fg)
        cv2.accumulateWeighted(gray.astype(np.float32), self.bg, 0.05, mask=cv2.bitwise_not(fg))
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        min_area = max(30.0, 150.0 * self.ppc * self.ppc)  # a hand is about 10 x 15 cm
        contours = [c for c in contours if min_area <= cv2.contourArea(c) <= 0.5 * self.roi_count]
        if not contours:
            self.tip = None
            if self._gone_since is None:
                self._gone_since = t
            elif t - self._gone_since > 0.3:
                self._armed = True
            self._hist.clear()
            return []
        self._gone_since = None
        c = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)
        # where it comes into the area: its points on the area's edge (else its lowest points);
        # the tip is its point farthest from there, away from the edge
        ci = c.astype(np.int32)
        inner = self.roi_inner[ci[:, 1], ci[:, 0]] > 0
        if not inner.any():
            self.tip = None
            return []
        base = c[~inner].mean(axis=0) if (~inner).any() else c[c[:, 1] >= c[:, 1].max() - 3].mean(axis=0)
        d = np.hypot(c[:, 0] - base[0], c[:, 1] - base[1])
        d[~inner] = -1.0
        tpx, tpy = c[int(np.argmax(d))]
        wx, wy = self.map.to_wall([(tpx, tpy)], self.size)[0]
        self.tip = (float(tpx), float(tpy), float(wx), float(wy))
        self._hist.append((t, float(wx), float(wy)))
        while self._hist and self._hist[0][0] < t - s.touch_dwell_s:
            self._hist.popleft()
        if self._last_touch is not None and math.hypot(wx - self._last_touch[0], wy - self._last_touch[1]) > 3 * s.touch_still_cm:
            self._armed = True
        if not self._armed or len(self._hist) < 3 or self._hist[-1][0] - self._hist[0][0] < 0.8 * s.touch_dwell_s:
            return []
        xs = np.array([h[1] for h in self._hist])
        ys = np.array([h[2] for h in self._hist])
        if np.hypot(xs - xs.mean(), ys - ys.mean()).max() > s.touch_still_cm:
            return []
        self._armed = False
        mx, my = float(xs.mean()), float(ys.mean())
        self._last_touch = (mx, my)
        mpx, mpy = self.map.to_image([(mx, my)], self.size)[0]
        return [Hit(mx, my, self._hist[0][0], float(mpx), float(mpy), "touch")]
