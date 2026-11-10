"""Automatic calibration: the screen (LED wall, projector, monitor) shows a grid of ArUco markers at
known places; the camera finds them; every marker corner is one point pair (picture px <-> wall cm).
A 5 x 4 grid gives 80 points spread over the whole wall, enough to fit the lens as well.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

DICTIONARY = cv2.aruco.DICT_4X4_50


@dataclass(frozen=True)
class Placed:
    id: int
    cx: float  # centre on the wall, cm
    cy: float
    size: float  # side, cm

    def corners(self) -> np.ndarray:
        """TL, TR, BR, BL on the wall (ArUco's corner order)."""
        h = self.size / 2
        return np.array([[self.cx - h, self.cy - h], [self.cx + h, self.cy - h],
                         [self.cx + h, self.cy + h], [self.cx - h, self.cy + h]], np.float64)


def _dictionary():
    return cv2.aruco.getPredefinedDictionary(DICTIONARY)


def grid(wall_w: float, wall_h: float, cols: int = 5, rows: int = 4, fill: float = 0.55) -> list[Placed]:
    """cols x rows markers over the wall, centred in their cells; side = fill x the smaller cell side."""
    cw, ch = wall_w / cols, wall_h / rows
    size = fill * min(cw, ch)
    out = []
    for r in range(rows):
        for c in range(cols):
            out.append(Placed(r * cols + c, (c + 0.5) * cw, (r + 0.5) * ch, size))
    return out


def marker_image(marker_id: int, px: int) -> np.ndarray:
    """One marker, black on white, px x px (the black border is the marker's edge)."""
    return cv2.aruco.generateImageMarker(_dictionary(), marker_id, int(px))


def render(wall_w: float, wall_h: float, placed: list[Placed], width_px: int, height_px: int) -> np.ndarray:
    """The whole board as a grey picture of width_px x height_px (white background)."""
    img = np.full((height_px, width_px), 255, np.uint8)
    sx, sy = width_px / wall_w, height_px / wall_h
    for p in placed:
        side = int(round(p.size * min(sx, sy)))
        x0, y0 = int(round((p.cx - p.size / 2) * sx)), int(round((p.cy - p.size / 2) * sy))
        img[y0:y0 + side, x0:x0 + side] = marker_image(p.id, side)
    return img


def detect(gray: np.ndarray) -> dict[int, np.ndarray]:
    """id -> 4 corners (TL, TR, BR, BL) in picture pixels."""
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(_dictionary(), params)
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2).astype(np.float64) for i, c in zip(ids.ravel(), corners)}


def pairs(found: dict[int, np.ndarray], placed: list[Placed]) -> tuple[np.ndarray, np.ndarray]:
    by_id = {p.id: p for p in placed}
    img, wall = [], []
    for marker_id, corners in found.items():
        p = by_id.get(marker_id)
        if p is None:
            continue
        img.append(corners)
        wall.append(p.corners())
    if not img:
        return np.zeros((0, 2)), np.zeros((0, 2))
    return np.concatenate(img), np.concatenate(wall)


class MarkerCollector:
    """Averages the corners of each marker over several frames (less noise), then fits."""

    def __init__(self, placed: list[Placed]):
        self.placed = placed
        self.sums: dict[int, np.ndarray] = {}
        self.counts: dict[int, int] = {}
        self.frames = 0
        self.last_seen = 0

    def add(self, gray: np.ndarray) -> int:
        found = detect(gray)
        self.frames += 1
        ids = {p.id for p in self.placed}
        n = 0
        for marker_id, c in found.items():
            if marker_id in ids:
                self.sums[marker_id] = self.sums.get(marker_id, 0) + c
                self.counts[marker_id] = self.counts.get(marker_id, 0) + 1
                n += 1
        self.last_seen = n
        return n

    def averaged(self, min_count: int = 3) -> dict[int, np.ndarray]:
        return {i: self.sums[i] / self.counts[i] for i in self.sums if self.counts[i] >= min_count}
