"""Small helpers shared by the asset tools: number formatting, the SVG wrapper
and the brand graphic elements (Brand Book page 23) as SVG shapes."""
from __future__ import annotations

import math
from pathlib import Path

from market_pulse.ui import icons

MODULE = 48.0  # SVG units per module in the element files (the side of the plus)


def num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def hex_of(rgb) -> str:
    return "#%02X%02X%02X" % tuple(rgb)


def svg_document(title: str, w: float, h: float, body: list[str], x: float = 0, y: float = 0) -> str:
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{num(x)} {num(y)} {num(w)} {num(h)}" '
            f'width="{num(w)}" height="{num(h)}">',
            f"  <title>{title}</title>",
            *body,
            "</svg>",
            "",
        ]
    )


def bar_points(cx: float, cy: float, length: float, thick: float, angle_deg: float) -> list[tuple[float, float]]:
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    hl, ht = length / 2, thick / 2
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in ((-hl, -ht), (hl, -ht), (hl, ht), (-hl, ht))]


def cross_points(cx: float, cy: float, length: float, thick: float, angle_deg: float) -> list[tuple[float, float]]:
    """Outline of two crossed bars (a plus, or a times sign when turned 45 degrees) as one 12-point polygon."""
    h, ht = length / 2, thick / 2
    pts = [(-ht, -h), (ht, -h), (ht, -ht), (h, -ht), (h, ht), (ht, ht), (ht, h), (-ht, h), (-ht, ht), (-h, ht),
           (-h, -ht), (-ht, -ht)]
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in pts]


def polygon(pts) -> str:
    return '<polygon points="' + " ".join(f"{num(x)},{num(y)}" for x, y in pts) + '"/>'


def element_shape(kind: str, m: float = MODULE, cx: float | None = None, cy: float | None = None) -> str:
    """A graphic element of module m centred on (cx, cy): the plus is m x m, every
    bar is m / 3 thick with square ends, the times sign is the plus turned 45
    degrees and the slash is one bar rising to the right."""
    cx = m / 2 if cx is None else cx
    cy = m / 2 if cy is None else cy
    t = m / 3
    if kind == "dot":
        return f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num(m / 2)}"/>'
    if kind == "minus":
        return f'<rect x="{num(cx - m / 2)}" y="{num(cy - t / 2)}" width="{num(m)}" height="{num(t)}"/>'
    if kind == "slash":
        return polygon(bar_points(cx, cy, m, t, -45))
    if kind in ("plus", "times"):
        return polygon(cross_points(cx, cy, m, t, 45.0 if kind == "times" else 0.0))
    raise ValueError(kind)


def icon_shapes(kind: str, side: float = 100.0, cx: float = 50.0, cy: float = 50.0) -> list[str]:
    """A target icon on a tile of `side` centred on (cx, cy), as editable shapes: bars
    as rectangles, the ring as true arcs, the zigzag as one polygon."""
    k = side / 100.0
    px = lambda x: cx + (x - 50.0) * k  # noqa: E731
    py = lambda y: cy + (y - 50.0) * k  # noqa: E731
    if kind == "bars":
        return [f'<rect x="{num(px(x))}" y="{num(py(y))}" width="{num(w * k)}" height="{num(h * k)}"/>' for x, y, w, h in icons.BARS]
    if kind == "zigzag":
        return [polygon([(px(x), py(y)) for x, y in icons.zigzag_outline()])]
    if kind == "ring":
        out = []
        for a0, a1 in icons.ring_arcs():
            (ox0, oy0), (ox1, oy1) = icons._polar(icons.RING_OUTER, a0), icons._polar(icons.RING_OUTER, a1)
            (ix0, iy0), (ix1, iy1) = icons._polar(icons.RING_INNER, a0), icons._polar(icons.RING_INNER, a1)
            ro, ri = num(icons.RING_OUTER * k), num(icons.RING_INNER * k)
            large = 1 if a1 - a0 > 180.0 else 0
            out.append(f'<path d="M{num(px(ox0))} {num(py(oy0))} A{ro} {ro} 0 {large} 1 {num(px(ox1))} {num(py(oy1))} '
                       f'L{num(px(ix1))} {num(py(iy1))} A{ri} {ri} 0 {large} 0 {num(px(ix0))} {num(py(iy0))} Z"/>')
        return out
    raise ValueError(kind)


def write(path: Path, text: str, force: bool, root: Path) -> bool:
    """Write a generated file unless it exists (designer edits win over a re-run)."""
    if path.exists() and not force:
        print(f"  kept   {path.relative_to(root)} (exists; --force to replace)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"  wrote  {path.relative_to(root)}")
    return True
