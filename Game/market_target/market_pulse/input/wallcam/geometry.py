"""Camera picture -> wall coordinates.

The camera's wide lens bends straight lines (barrel distortion), so a plain 4-point homography is
centimetres off near the edges. WallMap first straightens the picture with a radial lens model
(k1, k2, focal length as a share of the picture width, centre in the middle) and then maps it onto
the wall with a homography. With 8 or more calibration points (the markers give dozens) the fit
also finds the lens's k1 itself; with 4 points it uses the lens it is given.

All picture points carry the size of the picture they come from, so a calibration made on the
640x360 sub stream also works on the 1920x1080 main stream (same view, other resolution).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# Measured on the Witness camera (192.168.70.66) from its view of a brick wall: the k1/k2 that make
# the brick rows straight. f is the focal length as a share of the picture width.
DEFAULT_LENS = {"f": 0.5, "k1": -0.175, "k2": 0.025}


@dataclass
class Lens:
    f: float = 0.5
    k1: float = 0.0
    k2: float = 0.0

    @classmethod
    def from_dict(cls, d: dict | None) -> "Lens":
        d = d or {}
        return cls(float(d.get("f", 0.5)), float(d.get("k1", 0.0)), float(d.get("k2", 0.0)))

    def to_dict(self) -> dict:
        return {"f": self.f, "k1": self.k1, "k2": self.k2}

    def matrix(self, size: tuple[int, int]) -> np.ndarray:
        w, h = size
        fx = self.f * w
        return np.array([[fx, 0.0, w / 2.0], [0.0, fx, h / 2.0], [0.0, 0.0, 1.0]], np.float64)

    def coeffs(self) -> np.ndarray:
        return np.array([self.k1, self.k2, 0.0, 0.0], np.float64)

    def undistort(self, pts, size: tuple[int, int]) -> np.ndarray:
        """Picture pixels -> where they would be with a perfect (pinhole) lens, same pixel scale."""
        pts = np.asarray(pts, np.float64).reshape(-1, 1, 2)
        if self.k1 == 0.0 and self.k2 == 0.0:
            return pts.reshape(-1, 2).copy()
        K = self.matrix(size)
        crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 1e-7)
        return cv2.undistortPointsIter(pts, K, self.coeffs(), None, K, crit).reshape(-1, 2)

    def distort(self, pts, size: tuple[int, int]) -> np.ndarray:
        """The inverse: pinhole pixels -> picture pixels."""
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        K = self.matrix(size)
        x = (pts[:, 0] - K[0, 2]) / K[0, 0]
        y = (pts[:, 1] - K[1, 2]) / K[1, 1]
        r2 = x * x + y * y
        s = 1.0 + self.k1 * r2 + self.k2 * r2 * r2
        return np.stack([x * s * K[0, 0] + K[0, 2], y * s * K[1, 1] + K[1, 2]], axis=1)


def _scale(pts, src: tuple[int, int], dst: tuple[int, int]) -> np.ndarray:
    pts = np.asarray(pts, np.float64).reshape(-1, 2)
    if tuple(src) == tuple(dst):
        return pts.copy()
    return pts * np.array([dst[0] / src[0], dst[1] / src[1]])


def _homography(src: np.ndarray, dst: np.ndarray, robust: bool) -> tuple[np.ndarray, np.ndarray]:
    if robust and len(src) >= 8:
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if H is not None and mask is not None and mask.sum() >= 4:
            keep = mask.ravel().astype(bool)
            H2, _ = cv2.findHomography(src[keep], dst[keep], 0)
            if H2 is not None:
                return H2, keep
    H, _ = cv2.findHomography(src, dst, 0)
    if H is None:
        raise ValueError("the points do not give a mapping (three of them in a line?)")
    return H, np.ones(len(src), bool)


def _apply_h(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, H).reshape(-1, 2)


class WallMap:
    """Picture (pixels of a picture of `image_size`) <-> wall (cm, 0..wall_w x 0..wall_h)."""

    def __init__(self, H, lens: Lens, image_size, wall_size, rms_cm: float = 0.0, max_cm: float = 0.0,
                 points: int = 0, method: str = ""):
        self.H = np.asarray(H, np.float64).reshape(3, 3)
        self.Hinv = np.linalg.inv(self.H)
        self.lens = lens
        self.image_size = (int(image_size[0]), int(image_size[1]))
        self.wall_size = (float(wall_size[0]), float(wall_size[1]))
        self.rms_cm, self.max_cm, self.points, self.method = rms_cm, max_cm, points, method

    # -- mapping ------------------------------------------------------------------------------
    def to_wall(self, pts, size=None) -> np.ndarray:
        size = size or self.image_size
        p = _scale(pts, size, self.image_size)
        return _apply_h(self.H, self.lens.undistort(p, self.image_size))

    def to_image(self, wall_pts, size=None) -> np.ndarray:
        size = size or self.image_size
        p = self.lens.distort(_apply_h(self.Hinv, wall_pts), self.image_size)
        return _scale(p, self.image_size, size)

    def px_per_cm(self, size=None) -> float:
        """The picture's scale on the wall (median over the wall), pixels per centimetre."""
        w, h = self.wall_size
        xs, ys = np.meshgrid(np.linspace(0.1, 0.9, 5) * w, np.linspace(0.1, 0.9, 5) * h)
        a = np.stack([xs.ravel(), ys.ravel()], 1)
        b = a + np.array([10.0, 0.0])
        d = np.linalg.norm(self.to_image(b, size) - self.to_image(a, size), axis=1) / 10.0
        return float(np.median(d))

    def outline(self, size=None, margin_cm: float = 0.0, steps: int = 24) -> np.ndarray:
        """The wall's edge in the picture (curved by the lens), as a polygon (int32 Nx2)."""
        w, h = self.wall_size
        m = margin_cm
        t = np.linspace(0.0, 1.0, steps, endpoint=False)
        x0, y0, x1, y1 = -m, -m, w + m, h + m
        edge = np.concatenate([
            np.stack([x0 + (x1 - x0) * t, np.full_like(t, y0)], 1),
            np.stack([np.full_like(t, x1), y0 + (y1 - y0) * t], 1),
            np.stack([x1 - (x1 - x0) * t, np.full_like(t, y1)], 1),
            np.stack([np.full_like(t, x0), y1 - (y1 - y0) * t], 1),
        ])
        return np.round(self.to_image(edge, size)).astype(np.int32)

    def grid_lines(self, size=None, step_cm: float = 50.0, steps: int = 30) -> list[np.ndarray]:
        """Lines of a step_cm grid on the wall, in the picture (for drawing over the camera view)."""
        w, h = self.wall_size
        lines = []
        t = np.linspace(0.0, 1.0, steps)
        for x in list(np.arange(0.0, w, step_cm)) + [w]:
            lines.append(np.round(self.to_image(np.stack([np.full_like(t, x), t * h], 1), size)).astype(np.int32))
        for y in list(np.arange(0.0, h, step_cm)) + [h]:
            lines.append(np.round(self.to_image(np.stack([t * w, np.full_like(t, y)], 1), size)).astype(np.int32))
        return lines

    # -- fitting ------------------------------------------------------------------------------
    @classmethod
    def fit(cls, img_pts, wall_pts, image_size, wall_size, lens: Lens | None = None,
            refine_lens: bool | None = None, method: str = "") -> "WallMap":
        """Fit from point pairs. refine_lens (default: with 8 or more points) also fits k1."""
        img = np.asarray(img_pts, np.float64).reshape(-1, 2)
        wall = np.asarray(wall_pts, np.float64).reshape(-1, 2)
        if len(img) != len(wall) or len(img) < 4:
            raise ValueError("needs at least 4 point pairs")
        lens = lens or Lens.from_dict(DEFAULT_LENS)
        if refine_lens is None:
            refine_lens = len(img) >= 8
        robust = len(img) >= 12

        def solve(lz: Lens):
            und = lz.undistort(img, image_size)
            H, keep = _homography(und, wall, robust)
            err = np.linalg.norm(_apply_h(H, und) - wall, axis=1)
            return float(np.sqrt(np.mean(err[keep] ** 2))), H, keep, err

        best = (*solve(lens), lens)
        if refine_lens:
            for span, n in ((0.2, 21), (0.02, 21)):
                base = best[4]
                for k1 in np.linspace(base.k1 - span, base.k1 + span, n):
                    lz = Lens(base.f, float(k1), base.k2)
                    try:
                        cand = (*solve(lz), lz)
                    except (ValueError, cv2.error):
                        continue
                    if cand[0] < best[0] and cand[2].sum() >= best[2].sum() * 0.9:
                        best = cand
        rms, H, keep, err, lz = best
        if not np.isfinite(rms):
            raise ValueError("the fit failed")
        return cls(H, lz, image_size, wall_size, rms, float(err[keep].max()), int(keep.sum()), method)

    # -- file ----------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"H": self.H.tolist(), "lens": self.lens.to_dict(), "image_size": list(self.image_size),
                "wall_size": list(self.wall_size), "rms_cm": round(self.rms_cm, 2), "max_cm": round(self.max_cm, 2),
                "points": self.points, "method": self.method}

    @classmethod
    def from_dict(cls, d: dict) -> "WallMap":
        return cls(d["H"], Lens.from_dict(d.get("lens")), d["image_size"], d["wall_size"],
                   float(d.get("rms_cm", 0)), float(d.get("max_cm", 0)), int(d.get("points", 0)), d.get("method", ""))

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "WallMap | None":
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, np.linalg.LinAlgError):
            return None

    def scaled_to(self, wall_size) -> "WallMap":
        """The same calibration for a wall of another size in cm (the same screen, other units)."""
        sx, sy = wall_size[0] / self.wall_size[0], wall_size[1] / self.wall_size[1]
        if math.isclose(sx, 1.0) and math.isclose(sy, 1.0):
            return self
        S = np.diag([sx, sy, 1.0])
        return WallMap(S @ self.H, self.lens, self.image_size, wall_size, self.rms_cm, self.max_cm, self.points,
                       self.method)
