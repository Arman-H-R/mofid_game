"""The three target icons: one table says what each box means, and one set of
shapes is used by the SVG generator and by the code fallback. No pygame.

To replace an icon, edit assets/gameplay/target-<colour>.svg; the game reads it
directly. Sizes and points are in settings ("targets"), movement in "motion".

Shapes are in tile units: 100 = the side of the box.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TargetIcon:
    kind: str
    category: str
    meaning: str
    file: str  # under assets/


TARGET_ICONS = {
    "green": TargetIcon("bars", "growth", "rising bars: steady growth, a step at a time", "gameplay/target-green.svg"),
    "blue": TargetIcon("ring", "balanced portfolio", "allocation ring in three parts: diversification, managed risk",
                       "gameplay/target-blue.svg"),
    "red": TargetIcon("zigzag", "high volatility", "sharp price swings: unpredictable, high risk", "gameplay/target-red.svg"),
}

BARS = ((23.0, 58.0, 15.0, 19.0), (42.5, 42.0, 15.0, 35.0), (62.0, 23.0, 15.0, 54.0))  # x, y, w, h, shortest first
RING_CENTER = 50.0
RING_OUTER = 31.5
RING_INNER = 16.5
RING_SHARES = (0.5, 0.3, 0.2)  # unequal, as a real allocation is
RING_START_DEG = -90.0
RING_GAP_DEG = 15.0
ZIGZAG_POINTS = ((13.0, 63.0), (29.0, 37.0), (44.0, 60.0), (60.0, 28.0), (74.0, 69.0), (88.0, 44.0))  # uneven, as a price is
ZIGZAG_WIDTH = 12.0


def ring_arcs() -> list[tuple[float, float]]:
    """Start and end angle (degrees) of each part of the ring, clockwise on screen."""
    arcs = []
    start = RING_START_DEG
    for share in RING_SHARES:
        arcs.append((start + RING_GAP_DEG / 2, start + 360.0 * share - RING_GAP_DEG / 2))
        start += 360.0 * share
    return arcs


def _polar(r: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return RING_CENTER + r * math.cos(a), RING_CENTER + r * math.sin(a)


def zigzag_outline() -> list[tuple[float, float]]:
    """The zigzag as one closed polygon: the line widened, with mitred corners and square ends."""
    pts = ZIGZAG_POINTS
    hw = ZIGZAG_WIDTH / 2
    normals = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        n = math.hypot(dx, dy)
        normals.append((-dy / n, dx / n))
    left, right = [], []
    for i, (x, y) in enumerate(pts):
        if i == 0:
            nx, ny = normals[0]
            k = hw
        elif i == len(pts) - 1:
            nx, ny = normals[-1]
            k = hw
        else:
            ax, ay = normals[i - 1]
            bx, by = normals[i]
            mx, my = ax + bx, ay + by
            m = math.hypot(mx, my)
            nx, ny = mx / m, my / m
            k = hw / (nx * ax + ny * ay)  # mitre length
        left.append((x + nx * k, y + ny * k))
        right.append((x - nx * k, y - ny * k))
    return left + right[::-1]


def polygons(kind: str, arc_step_deg: float = 4.0) -> list[list[tuple[float, float]]]:
    """The icon as polygons in tile units (the ring's curves as short straight parts)."""
    if kind == "bars":
        return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h)] for x, y, w, h in BARS]
    if kind == "zigzag":
        return [zigzag_outline()]
    if kind == "ring":
        out = []
        for a0, a1 in ring_arcs():
            steps = max(2, int(math.ceil((a1 - a0) / arc_step_deg)))
            angles = [a0 + (a1 - a0) * i / steps for i in range(steps + 1)]
            out.append([_polar(RING_OUTER, a) for a in angles] + [_polar(RING_INNER, a) for a in reversed(angles)])
        return out
    raise ValueError(kind)
