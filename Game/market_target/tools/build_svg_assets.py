"""Generate the starting set of game SVGs from the shapes the game draws.

    python tools/build_svg_assets.py            # write the files that are missing
    python tools/build_svg_assets.py --force    # replace them all (overwrites designer edits)

Run once to create the files; after that the SVGs are the source and designers
edit them directly. Every file is drawn in the default palette
(market_pulse/ui/theme.py) and with the proportions of the code that drew the
shape before, so the game looks the same when it loads them.

Units: wall centimetres for interface parts with a fixed size, and percent of
the side for parts whose size comes from the settings (targets, start button).
Parts that the game still draws in code (rings, bars, backgrounds, operator
overlays) get a reference file at their default size so designers can restyle
them; see assets/documentation/asset-inventory.md for which is which.

Also writes assets/documentation/original/: the symbol and target geometry the
game drew before it followed Brand Book page 23 exactly (for comparison only).
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from market_pulse import config  # noqa: E402
from market_pulse.ui import theme  # noqa: E402
from market_pulse.ui.icons import TARGET_ICONS  # noqa: E402
from svgkit import bar_points, cross_points, element_shape, hex_of, icon_shapes, num, polygon, svg_document, write  # noqa: E402

ASSETS = ROOT / "assets"
H = hex_of
SYMBOL_SCALE = 0.42  # symbol module as a fraction of the target side
TILE_RADIUS = 18  # percent of the side
ORIGINAL_SYMBOLS = {"green": "plus", "blue": "slash", "red": "times"}  # the marks before the target icons


def ease_out(p: float) -> float:
    return 1.0 - (1.0 - min(max(p, 0.0), 1.0)) ** 3


# -- shapes ---------------------------------------------------------------------------------------
def rect(w, h, rx, fill, x=0.0, y=0.0, extra="", id_=None) -> str:
    i = f' id="{id_}"' if id_ else ""
    r = f' rx="{num(rx)}"' if rx else ""
    return f'<rect{i} x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}"{r} fill="{fill}"{extra}/>'


def outline_rect(w, h, rx, stroke, sw, id_=None) -> str:
    """A border drawn inside the box, as pygame draws it (the stroke is centred on the path)."""
    i = f' id="{id_}"' if id_ else ""
    return (f'<rect{i} x="{num(sw / 2)}" y="{num(sw / 2)}" width="{num(w - sw)}" height="{num(h - sw)}" '
            f'rx="{num(max(0.0, rx - sw / 2))}" fill="none" stroke="{stroke}" stroke-width="{num(sw)}"/>')


def ring(cx, cy, r, width, color, id_=None) -> str:
    """pygame's circle outline: the ring lies inside the radius."""
    i = f' id="{id_}"' if id_ else ""
    return (f'<circle{i} cx="{num(cx)}" cy="{num(cy)}" r="{num(r - width / 2)}" fill="none" stroke="{color}" '
            f'stroke-width="{num(width)}"/>')


def old_symbol(kind: str, size: float, cx: float, cy: float) -> str:
    """The symbol geometry the game drew before (bars 0.27 of the size, longer diagonals)."""
    t = size * 0.27
    if kind == "plus":
        return polygon(cross_points(cx, cy, size, t, 0))
    if kind == "times":
        return polygon(cross_points(cx, cy, size * 1.05, t, 45))
    if kind == "slash":
        return polygon(bar_points(cx, cy, size * 1.15, t, -45))
    if kind == "minus":
        return polygon(bar_points(cx, cy, size, t, 0))
    return f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num(size / 2)}"/>'


def g(id_: str, items: list[str], attrs: str = "") -> list[str]:
    return [f'  <g id="{id_}"{attrs}>', *(f"    {s}" for s in items), "  </g>"]


# -- game parts -----------------------------------------------------------------------------------
def target(color_key: str, original: bool = False) -> str:
    kind = ORIGINAL_SYMBOLS[color_key] if original else theme.TARGET_SYMBOLS[color_key]
    shapes = [old_symbol(kind, 100 * SYMBOL_SCALE, 50, 50)] if original else icon_shapes(kind)
    body = ["  " + rect(100, 100, TILE_RADIUS, H(theme.TARGET_COLORS[color_key]), id_="tile"),
            *g("symbol" if original else "icon", shapes, ' fill="#FFFFFF"')]
    what = "previous symbol geometry" if original else TARGET_ICONS[color_key].meaning
    return svg_document(f"Target, {color_key} ({kind}), {what}; 100 units = the side of the box", 100, 100, body)


def pill(w, h, fill, title, rx=None) -> str:
    return svg_document(title, w, h, ["  " + rect(w, h, h / 2 if rx is None else rx, fill, id_="pill")])


def dot_grid(name, x0, y0, x1, y1, spacing, r, color, accents=(), accent_size=0.0, seed=7) -> list[str]:
    """Same placement as market_pulse.ui.draw.dot_grid, in centimetres."""
    w, h = x1 - x0, y1 - y0
    cols, rows = max(1, int(w // spacing)), max(1, int(h // spacing))
    ox, oy = x0 + (w - (cols - 1) * spacing) / 2, y0 + (h - (rows - 1) * spacing) / 2
    rng = random.Random(seed)
    at = {}
    for kind, col in accents:
        at[(rng.randrange(cols), rng.randrange(rows))] = (kind, col)
    dots, marks = [], []
    for row in range(rows):
        for c in range(cols):
            x, y = ox + c * spacing, oy + row * spacing
            if (c, row) in at:
                kind, col = at[(c, row)]
                marks.append(element_shape(kind, accent_size, x, y).replace("/>", f' fill="{H(col)}"/>'))
            else:
                dots.append(f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(r)}"/>')
    out = [f'<g id="{name}-dots" fill="{H(color)}">', *(f"  {d}" for d in dots), "</g>"]
    if marks:
        out += [f'<g id="{name}-accents">', *(f"  {m}" for m in marks), "</g>"]
    return out


def inline_logo(path: Path, cx: float, cy: float, width: float) -> list[str]:
    """The logo file's paths placed at (cx, cy) with the given width (no external reference)."""
    text = path.read_text(encoding="utf-8")
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', text).group(1).split()]
    inner = text[text.index("</title>") + len("</title>"): text.rindex("</svg>")].strip("\n")
    s = width / vb[2]
    tx, ty = cx - width / 2, cy - vb[3] * s / 2
    return [f'<g id="logo" transform="translate({num(tx)} {num(ty)}) scale({num(s)})">',
            *inner.splitlines(), "</g>"]


def background(cfg, kind: str) -> str:
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    hud = float(cfg.layout.hud_height_cm)
    footer_y = Hh - float(cfg.layout.footer_height_cm)
    t = theme
    body: list[str] = []
    if kind == "dark":
        accents = [("plus", t.TURQUOISE), ("times", t.WHITE), ("slash", t.TURQUOISE), ("minus", t.WHITE),
                   ("dot", t.TURQUOISE), ("plus", t.WHITE), ("dot", t.WHITE), ("slash", t.WHITE)]
        body.append(rect(W, Hh, 0, H(t.DARK_BLUE), id_="wall"))
        body += dot_grid("pattern", 4, 4, W - 4, Hh - 4, 12.5, 0.6, t.PATTERN_BLUE, accents, 2.6, seed=5)
        title = "Background, dark (attract and results screens)"
    else:
        marks = [("plus", t.TURQUOISE_RAMP[5]), ("slash", t.BLUE_SOFT), ("minus", t.TURQUOISE_RAMP[5]),
                 ("times", t.BLUE_SOFT), ("dot", t.TURQUOISE_RAMP[5]), ("plus", t.BLUE_SOFT)]
        body.append(rect(W, Hh, 0, H(t.WHITE), id_="wall"))
        body += dot_grid("field", 4, hud + 4, W - 4, footer_y - 3, 12.5, 0.55, t.GREY_LIGHT, marks if kind == "play" else (),
                         2.2, seed=11)
        band = [rect(W, hud, 0, H(t.DARK_BLUE))] + dot_grid("band", 3, 2, W - 3, hud - 2, 6.0, 0.45, t.PATTERN_BLUE)
        body += ['<g id="hud-band">', *(f"  {s}" for s in band), "</g>"]
        body.append(f'<line id="footer-divider" x1="12" y1="{num(footer_y)}" x2="{num(W - 12)}" y2="{num(footer_y)}" '
                    f'stroke="{H(t.GREY_LIGHT)}" stroke-width="0.25"/>')
        body += inline_logo(ASSETS / "brand" / "logo-fa-horizontal.svg", W / 2, (footer_y + Hh) / 2, 58)
        title = ("Background, gameplay screen" if kind == "play"
                 else "Background, instructions screen (no symbol accents; the band title is live text)")
    return svg_document(title + "; units: wall cm", W, Hh, [f"  {s}" for s in body])


def calibration(cfg) -> str:
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    lay = cfg.layout
    sb = lay.start_button
    t = theme
    lines = [f'<line x1="{x}" y1="0" x2="{x}" y2="{num(Hh)}"/>' for x in range(0, int(W) + 1, 50)]
    lines += [f'<line x1="0" y1="{y}" x2="{num(W)}" y2="{y}"/>' for y in range(0, int(Hh) + 1, 50)]
    crosses = []
    for cx, cy in ((25, 25), (W - 25, 25), (25, Hh - 25), (W - 25, Hh - 25), (W / 2, Hh / 2)):
        crosses.append(f'<line x1="{num(cx - 8)}" y1="{num(cy)}" x2="{num(cx + 8)}" y2="{num(cy)}"/>')
        crosses.append(f'<line x1="{num(cx)}" y1="{num(cy - 8)}" x2="{num(cx)}" y2="{num(cy + 8)}"/>')
    fx0, fy0 = float(lay.side_margin_cm), float(lay.hud_height_cm)
    fx1, fy1 = W - fx0, Hh - float(lay.footer_height_cm)
    s = float(sb.size_cm)
    body = ["  " + rect(W, Hh, 0, H(t.WHITE), extra=' fill-opacity="0.588"', id_="shade"),
            *g("grid", lines, f' stroke="{H(t.BLUE_SOFT)}" stroke-width="0.2"'),
            f'  <rect id="field" x="{num(fx0 + 0.2)}" y="{num(fy0 + 0.2)}" width="{num(fx1 - fx0 - 0.4)}" '
            f'height="{num(fy1 - fy0 - 0.4)}" fill="none" stroke="{H(t.PURPLE)}" stroke-width="0.4"/>',
            f'  <rect id="start-box" x="{num(sb.x_cm - s / 2 + 0.1)}" y="{num(sb.y_cm - s / 2 + 0.1)}" '
            f'width="{num(s - 0.2)}" height="{num(s - 0.2)}" fill="none" stroke="{H(t.TURQUOISE)}" stroke-width="0.2"/>',
            *g("crosses", crosses, f' stroke="{H(t.RED)}" stroke-width="0.6"')]
    return svg_document("Calibration overlay (operator, F2); labels are live text; units: wall cm", W, Hh, body)


def ring_doc(title: str, rings: list[tuple[float, float, tuple]]) -> str:
    """Concentric rings centred on the origin: (radius, width, colour)."""
    r_max = max(r for r, _w, _c in rings) + 1
    body = ["  " + ring(0, 0, r, w, H(c), id_=f"ring-{i + 1}") for i, (r, w, c) in enumerate(rings)]
    return svg_document(title, 2 * r_max, 2 * r_max, body, x=-r_max, y=-r_max)


def mid_ripple(r0, r1, width) -> tuple[float, float]:
    """A ripple half-way through its animation: radius eased out, width 70 %."""
    return r0 + (r1 - r0) * ease_out(0.5), width * (1.0 - 0.6 * 0.5)


def build(cfg) -> dict[str, str]:
    t = theme
    out: dict[str, str] = {}
    for c in config.COLORS:
        out[f"gameplay/target-{c}.svg"] = target(c)
        out[f"documentation/original/target-{c}.svg"] = target(c, original=True)
    for kind in t.SYMBOLS:
        out[f"documentation/original/symbol-{kind}.svg"] = svg_document(
            f"Symbol {kind}, previous game geometry (bars 0.27 of the size)", 48, 48,
            g(kind, [old_symbol(kind, 48, 24, 24)], f' fill="{H(t.DARK_BLUE)}"'))

    out["interface/buttons/start-button.svg"] = svg_document(
        "Start box (attract screen; the label is live text); 100 units = the side of the box", 100, 100,
        ["  " + rect(100, 100, TILE_RADIUS, H(t.TURQUOISE), id_="button")])
    out["interface/panels/attract-card.svg"] = svg_document(
        "Attract card (how to play / leaderboard); units: wall cm", 256, 80, ["  " + rect(256, 80, 5, H(t.WHITE), id_="card")])
    out["interface/panels/results-card.svg"] = svg_document(
        "Results card (hits per colour); units: wall cm", 80, 58, ["  " + rect(80, 58, 4, H(t.WHITE), id_="card")])
    out["interface/panels/banner-pill.svg"] = pill(
        110, 11, H(t.WHITE), "Banner pill (reference: width = text + 12 cm, height = 1.2 x text); units: wall cm")
    out["interface/panels/pill-best-multiplier.svg"] = pill(
        70, 10, H(t.PURPLE), "Results pill, best multiplier (reference: width follows the text); units: wall cm")
    out["interface/panels/pill-rank.svg"] = pill(
        80, 12, H(t.TURQUOISE), "Results pill, leaderboard rank (reference: width follows the text); units: wall cm")

    out["interface/indicators/ball-remaining.svg"] = svg_document(
        "Ball still to throw (HUD); units: wall cm", 6, 6, [f'  <circle id="ball" cx="3" cy="3" r="3" fill="{H(t.WHITE)}"/>'])
    out["interface/indicators/ball-used.svg"] = svg_document(
        "Ball already thrown (HUD); units: wall cm", 6, 6, ["  " + ring(3, 3, 3, 0.5, H(t.BLUE_SOFT), id_="ball")])
    out["interface/indicators/multiplier-active.svg"] = pill(
        46, 13, H(t.PURPLE), "Multiplier badge, active (x2 and up; label is live text); units: wall cm")
    out["interface/indicators/multiplier-inactive.svg"] = svg_document(
        "Multiplier badge, inactive (x1; label is live text); units: wall cm", 46, 13,
        ["  " + outline_rect(46, 13, 6.5, H(t.BLUE_SOFT), 0.5, id_="pill")])
    out["interface/indicators/points-pill.svg"] = pill(
        50, 15, H(t.TURQUOISE_PALE), "Points pill (instructions screen; label is live text); units: wall cm")
    out["interface/indicators/rank-badge-first.svg"] = svg_document(
        "Leaderboard rank badge, first place (number is live text); units: wall cm", 9, 9,
        [f'  <circle id="badge" cx="4.5" cy="4.5" r="4.5" fill="{H(t.TURQUOISE)}"/>'])
    out["interface/indicators/rank-badge.svg"] = svg_document(
        "Leaderboard rank badge, places 2-5 (number is live text); units: wall cm", 9, 9,
        [f'  <circle id="badge" cx="4.5" cy="4.5" r="4.5" fill="{H(t.BLUE_PALE)}"/>'])

    W = float(cfg.wall.width_cm)
    out["interface/progress/time-bar.svg"] = svg_document(
        "Time / instructions bar at 100 % (the gradient drains towards the right); units: wall cm", W, 2.2,
        ["  <defs>",
         '    <linearGradient id="brand-gradient" x1="0" y1="0" x2="1" y2="0">',
         f'      <stop offset="0" stop-color="{H(t.DARK_BLUE)}"/>',
         f'      <stop offset="1" stop-color="{H(t.TURQUOISE)}"/>',
         "    </linearGradient>",
         "  </defs>",
         "  " + rect(W, 2.2, 0, H(t.GREY_LIGHT), id_="track"),
         "  " + rect(W, 2.2, 0, "url(#brand-gradient)", id_="fill")])
    out["interface/progress/results-countdown-bar.svg"] = svg_document(
        "Results auto-return bar at 100 % (drains towards the right); units: wall cm", 180, 1.6,
        ["  " + rect(180, 1.6, 0, H(t.BLUE_RAMP[2]), id_="track"), "  " + rect(180, 1.6, 0, H(t.TURQUOISE), id_="fill")])
    for kind in ("play", "instructions", "dark"):
        out[f"interface/backgrounds/background-{kind}.svg"] = background(cfg, kind)

    out["interface/operator/status-simulation.svg"] = pill(
        60, 5.5, H(t.PURPLE), "Status pill, simulation mode (reference: width follows the text); units: wall cm", rx=2.5)
    out["interface/operator/status-sensor-offline.svg"] = pill(
        60, 5.5, H(t.RED), "Status pill, sensor offline (reference: width follows the text); units: wall cm", rx=2.5)
    out["interface/operator/debug-panel.svg"] = svg_document(
        "Debug panel (F3; lines are live text; size follows the text); units: wall cm", 150, 20,
        ["  " + rect(150, 20, 0, H(t.BLUE_DEEP), extra=' fill-opacity="0.882"', id_="panel")])
    out["interface/operator/calibration-overlay.svg"] = calibration(cfg)
    out["interface/operator/impact-accepted.svg"] = svg_document(
        "Calibration impact marker, accepted; units: wall cm", 6, 6,
        [f'  <circle id="marker" cx="3" cy="3" r="3" fill="{H(t.TURQUOISE)}"/>'])
    out["interface/operator/impact-rejected.svg"] = svg_document(
        "Calibration impact marker, rejected (outside the wall); units: wall cm", 6, 6,
        ["  " + ring(3, 3, 3, 0.5, H(t.RED), id_="marker")])

    half = float(cfg.targets.green.size_cm) / 2
    sb = float(cfg.layout.start_button.size_cm)
    a, b = mid_ripple(half, half + 20, 2.2), mid_ripple(half * 0.6, half + 34, 0.9)
    out["feedback/ripple-hit.svg"] = ring_doc(
        "Hit ripple, green box, half-way (colour = box colour); units: wall cm",
        [(a[0], a[1], t.TURQUOISE), (b[0], b[1], t.TURQUOISE)])
    out["feedback/ripple-miss.svg"] = ring_doc(
        "Miss ripple, half-way; units: wall cm", [(*mid_ripple(2, 17, 1.0), t.BLUE_SOFT), (*mid_ripple(1, 7, 1.4), t.GREY)])
    out["feedback/ripple-start.svg"] = ring_doc(
        "Start-box hit ripple, half-way; units: wall cm", [(*mid_ripple(sb / 2, sb / 2 + 40, 2.5), t.TURQUOISE)])
    out["feedback/spawn-ring.svg"] = ring_doc(
        "Spawn ring around a new green box, half-way (colour = box colour); units: wall cm",
        [(*mid_ripple(half * 2 * 0.6, half * 2 * 1.0, 0.9), t.TURQUOISE)])
    out["feedback/countdown-ring.svg"] = ring_doc(
        "Countdown ring, start of each second (grows 10 cm and fades to white); units: wall cm", [(42, 1.6, t.TURQUOISE)])
    out["feedback/start-pulse-ring.svg"] = ring_doc(
        "Start-box pulse ring, start of each beat (grows 16 cm, turns dark blue); units: wall cm",
        [(sb * 0.56, 1.2, t.TURQUOISE)])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="replace files that already exist")
    args = ap.parse_args()
    if not (ASSETS / "brand" / "logo-fa-horizontal.svg").is_file():
        print("run tools/extract_brand_assets.py first (the backgrounds include the logo)", file=sys.stderr)
        return 1
    cfg, _ = config.load(None)
    for rel, text in build(cfg).items():
        write(ASSETS / rel, text, args.force, ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
