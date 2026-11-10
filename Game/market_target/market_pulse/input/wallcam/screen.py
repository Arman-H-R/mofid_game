"""What the game itself draws on the wall, so that the camera does not take it for a ball.

When the camera sees the game's picture (a projector, an LED wall), a box sliding in, a hit's burst
or a moving logo changes the wall like a ball does, and every false hit draws a new burst. The game
hands each frame it draws (small and grey) to ScreenChanges; for each camera frame, the places where
the picture changed shortly before are left out of the detector's motion (HitDetector.process).

Only for a calibration made with markers: then the calibrated wall is the game's picture.
"""
from __future__ import annotations

import threading
from collections import deque

import cv2
import numpy as np

from .geometry import WallMap

THUMB_W = 96  # the drawn picture, px wide (about 3 px of the camera's picture each)
CHANGE = 16  # grey levels: a change of the drawn picture
BEFORE_S = 0.35  # changes drawn this long before a camera frame (the projector's and camera's delays)
AFTER_S = 0.05  # ...and this long after it (the camera's clock may run a little early)
KEEP_S = 1.0


class ScreenChanges:
    def __init__(self):
        self._frames: deque[tuple[float, np.ndarray]] = deque()  # (perf_counter, grey HxW uint8)
        self._lock = threading.Lock()
        self._lookup: tuple | None = None  # (key, camera pixels inside the picture, their thumb pixel)

    def add(self, t: float, grey: np.ndarray) -> None:
        """A frame the game just showed (main thread). grey: the wall area, THUMB_W wide."""
        with self._lock:
            if self._frames and self._frames[-1][1].shape != grey.shape:
                self._frames.clear()  # another window size
            self._frames.append((t, grey))
            while self._frames and self._frames[0][0] < t - KEEP_S:
                self._frames.popleft()

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def changed(self, t: float) -> np.ndarray | None:
        """Where the drawn picture changed around time t (bool, thumb pixels), or None if nowhere."""
        with self._lock:
            frames = list(self._frames)
        sel = [g for ft, g in frames if t - BEFORE_S <= ft <= t + AFTER_S]
        before = [g for ft, g in frames if ft < t - BEFORE_S]
        if before:
            sel.insert(0, before[-1])  # what it changed from
        if len(sel) < 2:
            return None
        stack = np.stack(sel)
        moved = (stack.max(axis=0) - stack.min(axis=0)) > CHANGE
        return moved if moved.any() else None

    def mask(self, t: float, wall_map: WallMap, size: tuple[int, int], grow_px: int = 3) -> np.ndarray | None:
        """The camera pixels (uint8 255, picture of `size`) where the game's picture changed around t."""
        moved = self.changed(t)
        if moved is None:
            return None
        inside, where = self._camera_lookup(wall_map, size, moved.shape)
        out = np.zeros(size[1] * size[0], np.uint8)
        out[inside] = moved[where[0], where[1]].astype(np.uint8) * 255
        out = out.reshape(size[1], size[0])
        if grow_px > 0:  # blur, and a calibration a pixel or two off
            k = 2 * grow_px + 1
            out = cv2.dilate(out, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        return out

    def _camera_lookup(self, wall_map: WallMap, size, shape):
        key = (id(wall_map), wall_map.wall_size, tuple(size), shape)
        if self._lookup is None or self._lookup[0] != key:
            w, h = size
            xs, ys = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
            wall = wall_map.to_wall(np.stack([xs.ravel(), ys.ravel()], 1), size)
            mw, mh = wall_map.wall_size
            th, tw = shape
            ix = np.floor(wall[:, 0] / mw * tw)
            iy = np.floor(wall[:, 1] / mh * th)
            ok = np.isfinite(ix) & np.isfinite(iy) & (ix >= 0) & (ix < tw) & (iy >= 0) & (iy < th)
            inside = np.flatnonzero(ok)
            self._lookup = (key, inside, (iy[ok].astype(np.intp), ix[ok].astype(np.intp)))
        return self._lookup[1], self._lookup[2]
