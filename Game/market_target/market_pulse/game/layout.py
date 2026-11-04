"""Random, non-overlapping placement of targets.

The scenario asks for no fixed grid, so every layout is drawn at random. Each
target reserves its whole footprint: its largest pulsed size plus the area its
movement sweeps, plus a gap. If a dense field (12 targets) cannot fit that,
movement is shrunk step by step for that layout, then the gap, and only as a
last resort a jittered grid is used. Placement is vectorised with numpy so a
full re-layout takes well under a millisecond, with no frame hitch on a hit.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class Footprint:
    half: float  # half of the largest side, cm
    ext_x: float  # motion reach before scaling, cm
    ext_y: float


@dataclass
class LayoutResult:
    centers: list[tuple[float, float]]
    motion_scale: float
    gap: float
    fallback: bool = False
    moved_ok: bool = True


MOTION_STEPS = (1.0, 0.7, 0.45, 0.2, 0.0)
CANDIDATES = 160
RESTARTS = 6


def _try(
    fps: list[Footprint],
    field: Rect,
    gap: float,
    scale: float,
    rng: np.random.Generator,
    prev: list[tuple[float, float] | None],
    min_move: float,
) -> tuple[list[tuple[float, float]], bool] | None:
    n = len(fps)
    hx = np.array([f.half + f.ext_x * scale for f in fps])
    hy = np.array([f.half + f.ext_y * scale for f in fps])
    order = np.argsort(-(hx * hy))  # biggest first
    placed_x = np.empty(n)
    placed_y = np.empty(n)
    placed_hx = np.empty(n)
    placed_hy = np.empty(n)
    out: list[tuple[float, float] | None] = [None] * n
    moved_ok = True
    for k, i in enumerate(order):
        lo_x, hi_x = field.x0 + hx[i], field.x1 - hx[i]
        lo_y, hi_y = field.y0 + hy[i], field.y1 - hy[i]
        if lo_x > hi_x or lo_y > hi_y:
            return None
        cx = rng.uniform(lo_x, hi_x, CANDIDATES)
        cy = rng.uniform(lo_y, hi_y, CANDIDATES)
        ok = np.ones(CANDIDATES, dtype=bool)
        if k:
            px, py = placed_x[:k, None], placed_y[:k, None]
            sep_x = np.abs(cx[None, :] - px) >= (hx[i] + placed_hx[:k, None] + gap)
            sep_y = np.abs(cy[None, :] - py) >= (hy[i] + placed_hy[:k, None] + gap)
            ok = np.all(sep_x | sep_y, axis=0)
        if not ok.any():
            return None
        choice = ok
        p = prev[i]
        if p is not None and min_move > 0:
            far = np.hypot(cx - p[0], cy - p[1]) >= min_move
            if (ok & far).any():
                choice = ok & far
            else:
                moved_ok = False
        j = int(np.flatnonzero(choice)[0])  # candidates are already random
        placed_x[k], placed_y[k], placed_hx[k], placed_hy[k] = cx[j], cy[j], hx[i], hy[i]
        out[i] = (float(cx[j]), float(cy[j]))
    return [c for c in out if c is not None], moved_ok


def _grid(fps: list[Footprint], field: Rect, rng: np.random.Generator) -> list[tuple[float, float]]:
    n = len(fps)
    aspect = field.w / field.h
    cols = max(1, int(round(np.sqrt(n * aspect))))
    rows = int(np.ceil(n / cols))
    cw, ch = field.w / cols, field.h / rows
    cells = rng.permutation(cols * rows)[:n]
    out = []
    for f, cell in zip(fps, cells):
        r, c = divmod(int(cell), cols)
        slack_x = max(0.0, cw / 2 - f.half)
        slack_y = max(0.0, ch / 2 - f.half)
        out.append(
            (
                field.x0 + (c + 0.5) * cw + rng.uniform(-slack_x, slack_x) * 0.8,
                field.y0 + (r + 0.5) * ch + rng.uniform(-slack_y, slack_y) * 0.8,
            )
        )
    return out


def place_one(
    fp: Footprint,
    others: list[tuple[float, float, float, float]],
    field: Rect,
    gap: float,
    rng: np.random.Generator,
) -> tuple[float, float, float] | None:
    """A free spot for one more box, leaving the others where they are. ``others``
    holds each box's centre and reserved half-sizes. Returns (x, y, motion scale), or
    None if the field is too full (a new box must never cover an existing one)."""
    for scale in MOTION_STEPS:
        hx, hy = fp.half + fp.ext_x * scale, fp.half + fp.ext_y * scale
        lo_x, hi_x = field.x0 + hx, field.x1 - hx
        lo_y, hi_y = field.y0 + hy, field.y1 - hy
        if lo_x > hi_x or lo_y > hi_y:
            continue
        cx = rng.uniform(lo_x, hi_x, CANDIDATES)
        cy = rng.uniform(lo_y, hi_y, CANDIDATES)
        ok = np.ones(CANDIDATES, dtype=bool)
        for ox, oy, ohx, ohy in others:
            ok &= (np.abs(cx - ox) >= hx + ohx + gap) | (np.abs(cy - oy) >= hy + ohy + gap)
        if ok.any():
            j = int(np.flatnonzero(ok)[0])
            return float(cx[j]), float(cy[j]), scale
    return None


def place(
    fps: list[Footprint],
    field: Rect,
    gap: float,
    rng: np.random.Generator,
    prev: list[tuple[float, float] | None] | None = None,
    min_move: float = 0.0,
) -> LayoutResult:
    """Place every footprint inside ``field`` without overlap."""
    if not fps:
        return LayoutResult([], 1.0, gap)
    prev = prev if prev is not None else [None] * len(fps)
    for scale in MOTION_STEPS:
        gaps = (gap, gap * 0.5, 0.0) if scale == 0.0 else (gap,)
        for g in gaps:
            for _ in range(RESTARTS):
                res = _try(fps, field, g, scale, rng, prev, min_move)
                if res is not None:
                    centers, moved_ok = res
                    return LayoutResult(centers, scale, g, moved_ok=moved_ok)
    return LayoutResult(_grid(fps, field, rng), 0.0, 0.0, fallback=True, moved_ok=False)
