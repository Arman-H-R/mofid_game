"""Export the designer's kit for Adobe Illustrator into design/.

    python tools/export_design_kit.py                     # everything
    python tools/export_design_kit.py --skip-screenshots  # keep the existing screenshots

Every file comes from the game's real sources: config/settings.json for sizes and
messages, assets/design-tokens.json for colours, assets/ for the artwork and the
positions used in market_pulse/ui/screens.py for the layouts. Nothing here is read
by the game; assets/ stays the source of what the game draws. Re-running overwrites
design/, so keep designer edits elsewhere.

Text comes in two forms: live (editable, needs the fonts) and outlined (curves that
always look right). Outlines are shaped with HarfBuzz, one run per direction, the
same way market_pulse/ui/text.py lays out the wall's text. Live text is centred from
the font metrics rather than with dominant-baseline, which Illustrator ignores.

Needs fonttools and uharfbuzz (outlines), Chrome or Edge (the PDFs) and PyMuPDF
(checking the PDFs); see requirements-dev.txt.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from build_svg_assets import dot_grid, inline_logo, mid_ripple, outline_rect, rect, ring, ring_doc  # noqa: E402
from market_pulse import config  # noqa: E402
from market_pulse.ui import theme  # noqa: E402
from market_pulse.ui.text import _NUMBER_GLUE, fa_digits, split_runs  # noqa: E402
from svgkit import element_shape, hex_of, icon_shapes, num  # noqa: E402

ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
OUT = ROOT / "design"
H = hex_of
PX_PER_CM = 5  # the wall artboards are 1500 x 2000
TILE_RADIUS = 0.18  # of the side
SYMBOL_SCALE = 0.42  # symbol module as a fraction of the side
LRM = "‎"
# Sample values for the texts with placeholders (the wall shows the same formats).
SAMPLE = {"balls": 10, "seconds": 60, "max": "×4", "m": "×2", "hits": 7, "throws": 10, "rank": 1, "name": "سارا", "points": 240}
SAMPLE_BOARD = (("سارا", 260), ("", 215), ("Ali", 150))

ROLES = {  # role -> (font file, weight), as config/settings.json resolves them today
    "title": ("IRANSansX-Black", 900),
    "heading": ("IRANSansX-ExtraBold", 800),
    "bold": ("IRANSansX-Bold", 700),
    "body": ("IRANSansX-Medium", 500),
    "numbers": ("IRANSansX-Black", 900),
    "regular": ("IRANSansX-Regular", 400),
    "demibold": ("IRANSansX-DemiBold", 600),
}
ARIAL = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
BG_DARK = H(theme.BLUE_DEEP)
BG_BAND = H(theme.DARK_BLUE)
BG_FIELD = H(theme.WHITE)
BG_PAGE = BG_DARK


# -- text -----------------------------------------------------------------------------------------
class Face:
    def __init__(self, path: Path):
        self.tt = TTFont(path)
        self.glyphs = self.tt.getGlyphSet()
        self.upem = self.tt["head"].unitsPerEm
        self.ascent = self.tt["hhea"].ascent
        self.descent = abs(self.tt["hhea"].descent)
        self.hb = hb.Font(hb.Face(hb.Blob.from_file_path(str(path))))

    def shape(self, run: str, rtl: bool) -> list[tuple[str, float, float, float]]:
        buf = hb.Buffer()
        buf.add_str(run)
        buf.direction = "rtl" if rtl else "ltr"
        buf.script = "Arab" if rtl else "Latn"
        buf.language = "fa" if rtl else "en"
        hb.shape(self.hb, buf)
        return [(self.tt.getGlyphName(i.codepoint), p.x_offset, p.y_offset, p.x_advance)
                for i, p in zip(buf.glyph_infos, buf.glyph_positions)]


class Ink:
    """Draws text as live <text> or as outlined <path>, placed the way the game places it."""

    _faces: dict[str, Face] = {}

    def __init__(self, outlined: bool):
        self.outlined = outlined

    @classmethod
    def face(cls, role: str) -> Face:
        key = "latin" if role == "latin" else ROLES[role][0]
        if key not in cls._faces:
            if role == "latin":
                path = ARIAL if ARIAL.is_file() else FONTS / "IRANSansX-Medium.ttf"
            else:
                path = FONTS / f"{key}.ttf"
            cls._faces[key] = Face(path)
        return cls._faces[key]

    def line_height(self, role: str, size: float) -> float:
        f = self.face(role)
        return (f.ascent + f.descent) * size / f.upem

    def width(self, s: str, role: str, size: float, spacing: float = 0.0, localize: bool = True) -> float:
        f = self.face(role)
        s = fa_digits(s) if localize else s
        total = 0.0
        for run, rtl in split_runs(s):
            total += sum(g[3] for g in f.shape(run, rtl)) * size / f.upem + spacing * len(run)
        return total

    def text(self, s: str, x: float, y: float, role: str, size: float, fill: str, anchor: str = "middle",
             valign: str = "center", localize: bool = True, spacing: float = 0.0, id_: str | None = None) -> str:
        """Text at (x, y). anchor: left / middle / right. valign: center or top, like the game's rects."""
        if not s:
            return ""
        f = self.face(role)
        s = fa_digits(s) if localize else s
        scale = size / f.upem
        top = y if valign == "top" else y - (f.ascent + f.descent) * scale / 2
        baseline = top + f.ascent * scale
        w = self.width(s, role, size, spacing, localize=False)
        left = x if anchor == "left" else x - w if anchor == "right" else x - w / 2
        i = f' id="{id_}"' if id_ else ""
        if self.outlined:
            return f'<path{i} fill="{fill}" d="{self._outline(s, left + w, baseline, f, scale, spacing)}"/>'
        return self._live(s, x, baseline, role, size, fill, anchor, spacing, i)

    @staticmethod
    def _outline(s: str, right: float, baseline: float, f: Face, scale: float, spacing: float) -> str:
        pen = SVGPathPen(f.glyphs, ntos=lambda v: num(v))
        pen_x = right
        for run, rtl in split_runs(s):  # logical order is laid out from the right edge
            glyphs = f.shape(run, rtl)
            run_w = sum(g[3] for g in glyphs) * scale + spacing * len(run)
            pen_x -= run_w
            gx = pen_x
            for name, xo, yo, adv in glyphs:
                f.glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, gx + xo * scale, baseline - yo * scale)))
                gx += adv * scale + spacing
        return pen.getCommands()

    @staticmethod
    def _live(s: str, x: float, baseline: float, role: str, size: float, fill: str, anchor: str, spacing: float, i: str) -> str:
        if role == "latin":
            family, weight = "'Helvetica', 'Arial', sans-serif", 400
        else:
            name, weight = ROLES[role]
            family = f"'{name}', 'IRANSansX', sans-serif"
        runs = split_runs(s)
        rtl = any(r for _, r in runs)
        if rtl:  # a sign glued to a number stays with it, as split_runs decides (the wall shows ×۲, not ۲×)
            s = "".join(LRM + run if not is_rtl and set(run) & _NUMBER_GLUE else run for run, is_rtl in runs)
            ta = {"middle": "middle", "right": "start", "left": "end"}[anchor]
            extra = ' direction="rtl" unicode-bidi="embed"'
        else:
            ta = {"middle": "middle", "right": "end", "left": "start"}[anchor]
            extra = ""
        ls = f' letter-spacing="{num(spacing)}"' if spacing else ""
        return (f'<text{i} x="{num(x)}" y="{num(baseline)}" font-family="{family}" font-weight="{weight}" '
                f'font-size="{num(size)}" fill="{fill}" text-anchor="{ta}"{ls}{extra}>{escape(s, quote=False)}</text>')


# -- documents --------------------------------------------------------------------------------------
def doc(title: str, w: float, h: float, body: list[str], px_w: float | None = None, x: float = 0, y: float = 0) -> str:
    k = (px_w / w) if px_w else 1.0
    joined = "\n".join(i for i in body if i)
    defs = list(dict.fromkeys(re.findall(r"<defs>.*?</defs>", joined, flags=re.S)))  # at the top, where every reader finds them
    body = [*defs, re.sub(r"<defs>.*?</defs>", "", joined, flags=re.S)]
    return "\n".join(['<?xml version="1.0" encoding="UTF-8"?>',
                      f'<svg xmlns="http://www.w3.org/2000/svg" width="{num(w * k)}px" height="{num(h * k)}px" '
                      f'viewBox="{num(x)} {num(y)} {num(w)} {num(h)}">',
                      f"<title>{escape(title, quote=False)}</title>", *body, "</svg>", ""])


def group(id_: str, items: list[str], attrs: str = "") -> str:
    inner = "\n".join(i for i in items if i)
    return f'<g id="{id_}"{attrs}>\n{inner}\n</g>'


def layered(layers: list[tuple[str, list[str]]]) -> list[str]:
    return [group(name, items) for name, items in layers]


def inner_of(svg: str) -> tuple[list[float], str]:
    """The viewBox and contents of an SVG document, to place it inside another one."""
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', svg).group(1).split()]
    body = svg[svg.index(">", svg.index("<svg")) + 1: svg.rindex("</svg>")]
    return vb, re.sub(r"<title>.*?</title>", "", body, flags=re.S).strip()


def placed(svg: str, x: float, y: float, w: float, h: float, id_: str | None = None) -> str:
    """An SVG document fitted and centred in a box (uniform scale). Inner ids are dropped except gradients."""
    vb, body = inner_of(svg)
    k = min(w / vb[2], h / vb[3])
    tx = x + (w - vb[2] * k) / 2 - vb[0] * k
    ty = y + (h - vb[3] * k) / 2 - vb[1] * k
    used = set(re.findall(r"url\(#([^)]+)\)", body))
    body = re.sub(r' id="([^"]+)"', lambda m: m.group(0) if m.group(1) in used else "", body)
    i = f' id="{id_}"' if id_ else ""
    return f'<g{i} transform="translate({num(tx)} {num(ty)}) scale({num(k)})">\n{body}\n</g>'


def safe_id(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


# -- game shapes ------------------------------------------------------------------------------------
def tile_shadow(cx: float, cy: float, side: float) -> list[str]:
    """The game's soft shadow as vector steps: deep blue at 27 %, a little lower, feathered by 6 % of the side."""
    steps = 8
    a = 1 - (1 - 0.27) ** (1 / steps)
    out = []
    for i in range(steps):
        d = side * 0.06 * (1 - 2 * i / (steps - 1))
        s = side + 2 * d
        out.append(rect(s, s, max(0.0, side * TILE_RADIUS + d), BG_DARK, cx - s / 2, cy + side * 0.05 - s / 2,
                        extra=f' fill-opacity="{num(a)}"'))
    return out


def target(color: str, cx: float, cy: float, side: float, shadow: bool = True) -> list[str]:
    kind = theme.TARGET_SYMBOLS[color]
    out = []
    if shadow:
        out.append(group("Shadow", tile_shadow(cx, cy, side)))
    out.append(group("Tile", [rect(side, side, side * TILE_RADIUS, H(theme.TARGET_COLORS[color]), cx - side / 2, cy - side / 2)]))
    out.append(group("Icon", icon_shapes(kind, side, cx, cy), ' fill="#FFFFFF"'))
    return out


def centred_rect(cx, cy, w, h, rx, fill, id_=None) -> str:
    return rect(w, h, rx, fill, cx - w / 2, cy - h / 2, id_=id_)


def time_gradient(W: float) -> str:
    return (f'<defs><linearGradient id="time-gradient" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="{num(W)}" y2="0">'
            f'<stop offset="0" stop-color="{H(theme.DARK_BLUE)}"/><stop offset="1" stop-color="{H(theme.TURQUOISE)}"/>'
            "</linearGradient></defs>")


def time_bar(W: float, y: float, frac: float) -> list[str]:
    """Drains towards the right; the visible part keeps its place in the gradient, as on the wall."""
    w = W * frac
    return [time_gradient(W), rect(W, 2.2, 0, H(theme.GREY_LIGHT), 0, y, id_="Track"),
            rect(w, 2.2, 0, "url(#time-gradient)", W - w, y, id_="Fill") if w > 0 else ""]


def steady_progress(ink: Ink, W: float, Hh: float) -> list[str]:
    a, b = " ".join("STEADY"), " ".join("PROGRESS")
    size, gap = 4.2, 3.0
    wa, wb = ink.width(a, "latin", size, localize=False), ink.width(b, "latin", size, localize=False)
    x = W / 2 - (wa + gap + wb) / 2
    y = Hh - 13
    return [ink.text(a, x, y, "latin", size, H(theme.TURQUOISE), "left", localize=False),
            ink.text(b, x + wa + gap, y, "latin", size, H(theme.WHITE), "left", localize=False)]


def field_pattern(cfg, accents: bool) -> tuple[list[str], list[str]]:
    W, Hh, hud = float(cfg.wall.width_cm), float(cfg.wall.height_cm), float(cfg.layout.hud_height_cm)
    footer_y = Hh - float(cfg.layout.footer_height_cm)
    marks = [("plus", theme.TURQUOISE_RAMP[5]), ("slash", theme.BLUE_SOFT), ("minus", theme.TURQUOISE_RAMP[5]),
             ("times", theme.BLUE_SOFT), ("dot", theme.TURQUOISE_RAMP[5]), ("plus", theme.BLUE_SOFT)]
    lines = dot_grid("field", 4, hud + 4, W - 4, footer_y - 3, 12.5, 0.55, theme.GREY_LIGHT, marks if accents else (), 2.2, seed=11)
    cut = next((i for i, s in enumerate(lines) if s.startswith('<g id="field-accents"')), len(lines))
    return lines[:cut], lines[cut:]


def dark_pattern(W: float, Hh: float) -> list[str]:
    t = theme
    accents = [("plus", t.TURQUOISE), ("times", t.WHITE), ("slash", t.TURQUOISE), ("minus", t.WHITE),
               ("dot", t.TURQUOISE), ("plus", t.WHITE), ("dot", t.WHITE), ("slash", t.WHITE)]
    return dot_grid("pattern", 4, 4, W - 4, Hh - 4, 12.5, 0.6, t.PATTERN_BLUE, accents, 2.6, seed=5)


def hud_band(cfg) -> list[str]:
    W, hud = float(cfg.wall.width_cm), float(cfg.layout.hud_height_cm)
    return [rect(W, hud, 0, BG_BAND), *dot_grid("band", 3, 2, W - 3, hud - 2, 6.0, 0.45, theme.PATTERN_BLUE)]


def footer(cfg) -> list[str]:
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    y = Hh - float(cfg.layout.footer_height_cm)
    return [f'<line id="Divider" x1="12" y1="{num(y)}" x2="{num(W - 12)}" y2="{num(y)}" stroke="{H(theme.GREY_LIGHT)}" stroke-width="0.25"/>',
            *inline_logo(ASSETS / "brand" / "logo-fa-horizontal.svg", W / 2, (y + Hh) / 2, 58)]


def hud_layers(ink: Ink, cfg, score: int, secs: int, throws: int, m: float, frac: float) -> list[tuple[str, list[str]]]:
    T = cfg.text
    W, hud = float(cfg.wall.width_cm), float(cfg.layout.hud_height_cm)
    mid = hud / 2 + 6
    pale, white = H(theme.BLUE_PALE), H(theme.WHITE)
    n = int(cfg.round.balls)
    step = min(8.4, 150.0 / max(1, n))
    d = min(6.0, step * 0.72)
    x0 = W / 2 + (n - 1) * step / 2
    balls = []
    for i in range(n):
        cx = x0 - i * step
        if i < n - throws:
            balls.append(f'<circle cx="{num(cx)}" cy="21" r="{num(d / 2)}" fill="{white}"/>')
        else:
            balls.append(ring(cx, 21, d / 2, 0.5 * d / 6, H(theme.BLUE_SOFT)))
    label = f"{T.multiplier} ×{m:g}"
    if m > 1:
        badge = centred_rect(W / 2, 44, 46, 13, 6.5, H(theme.PURPLE), id_="Badge")
        lcol = white
    else:
        badge = f'<rect id="Badge" x="{num(W / 2 - 22.75)}" y="37.75" width="45.5" height="12.5" rx="6.25" fill="none" stroke="{H(theme.BLUE_SOFT)}" stroke-width="0.5"/>'
        lcol = pale
    return [("HUD_score", [ink.text(T.score, W - 14, 7, "body", 6, pale, "right", "top"),
                           ink.text(str(score), W - 14, mid, "numbers", 31, white, "right")]),
            ("HUD_time", [ink.text(T.time, 14, 7, "body", 6, pale, "left", "top"),
                          ink.text(str(secs), 14, mid, "numbers", 31, white, "left")]),
            ("HUD_balls", balls),
            ("HUD_multiplier", [badge, ink.text(label, W / 2, 44, "heading", 7, lcol)]),
            ("Time_bar", time_bar(W, hud, frac))]


def burst(cx: float, cy: float, color: str, half: float, reach: float, seed: int = 7) -> list[str]:
    """The brand symbols that fly out of a hit, as a frozen frame (same choices as Effects.burst)."""
    rng = random.Random(seed)
    base = theme.TARGET_COLORS[color]
    palette = [base, base, base, theme.DARK_BLUE, theme.TURQUOISE]
    count = 16 if color == "red" else 12
    out = []
    for i in range(count):
        a = (i / count) * math.tau + rng.uniform(-0.3, 0.3)
        r = half + reach * rng.uniform(0.6, 1.0)
        out.append(element_shape(rng.choice(theme.SYMBOLS), rng.uniform(3.0, 5.5), cx + math.cos(a) * r, cy + math.sin(a) * r)
                   .replace("/>", f' fill="{H(rng.choice(palette))}"/>'))
    return out


SAMPLE_FIELD = (("green", 72, 118), ("blue", 172, 112), ("red", 250, 110), ("green", 222, 190), ("blue", 58, 212),
                ("blue", 158, 268), ("green", 238, 290), ("red", 105, 278), ("red", 64, 320))


def field_targets(cfg, placement) -> list[str]:
    out = []
    for i, (color, x, y) in enumerate(placement):
        out.append(group(f"Target_{i + 1}_{color}", target(color, x, y, float(cfg.targets[color].size_cm))))
    return out


def feedback_sample(ink: Ink, cfg) -> list[str]:
    """A red hit on the second multiplier, frozen half-way: ripples, symbols, floating word, points, banner."""
    T = cfg.text
    x, y, half = 128.0, 185.0, float(cfg.targets.red.size_cm) / 2
    red = H(theme.RED)
    lh = ink.line_height("heading", 7)
    txt = T.combo_up.format(m="×3")
    w = ink.width(txt, "heading", 7) + 12
    h = lh * 1.2
    W = float(cfg.wall.width_cm)
    return [ring(x, y, half + 17.5, 1.54, red, id_="Ripple_1"), ring(x, y, half * 0.6 + 34, 0.63, red, id_="Ripple_2"),
            group("Symbols", burst(x, y, "red", half, 36)),
            ink.text(T.word_red, x, y - 18, "title", 15, red, id_="Word"),
            ink.text("+60", x, y + 4, "numbers", 9, H(theme.DARK_BLUE), id_="Points"),
            ink.text("×2", x, y + 15, "numbers", 7, H(theme.PURPLE), id_="Multiplier"),
            group("Banner", [centred_rect(W / 2, float(cfg.layout.hud_height_cm) + 1, w, h, h / 2, H(theme.WHITE)),
                             ink.text(txt, W / 2, float(cfg.layout.hud_height_cm) + 1, "heading", 7, H(theme.PURPLE))])]


# -- wall and screens -------------------------------------------------------------------------------
def wall_layers(ink: Ink, cfg) -> list[tuple[str, list[str]]]:
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    dots, accents = field_pattern(cfg, True)
    return [("Background", [rect(W, Hh, 0, BG_FIELD)]),
            ("Dot_pattern", dots),
            ("Accents", accents),
            ("HUD_band", hud_band(cfg)),
            *hud_layers(ink, cfg, 130, 42, 4, 3, 42 / 60),
            ("Targets", field_targets(cfg, SAMPLE_FIELD)),
            ("Feedback_sample", feedback_sample(ink, cfg)),
            ("Footer", footer(cfg))]


def start_ring(cx: float, cy: float, side: float, k: float) -> str:
    """The start box's pulse half-way through a beat: grown by 14 of its 16 cm and half faded."""
    return ring(cx, cy, side * 0.56 + 14 * k, 1.2 * k, H(theme.lerp_color(theme.TURQUOISE, theme.DARK_BLUE, 0.5)))


def screen_attract(ink: Ink, cfg, board: bool) -> list[tuple[str, list[str]]]:
    T = cfg.text
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    sb = cfg.layout.start_button
    white, pale = H(theme.WHITE), H(theme.BLUE_PALE)
    side = float(sb.size_cm)
    card = [rect(256, 80, 5, white, 22, 142, id_="Card")]
    if board:
        card.append(ink.text(T.leaderboard, 150, 153, "heading", 8, BG_BAND))
        for i, (name, score) in enumerate(SAMPLE_BOARD):
            y = 142 + 26 + i * 12.5
            col = H(theme.TURQUOISE) if i == 0 else pale
            card += [f'<circle cx="264" cy="{num(y)}" r="4.5" fill="{col}"/>',
                     ink.text(str(i + 1), 264, y, "numbers", 5.5, white if i == 0 else BG_BAND),
                     ink.text(name or T.guest, 254, y, "body", 6.5, BG_BAND, "right"),
                     ink.text(str(score), 34, y, "numbers", 7, BG_BAND, "left")]
    else:
        for color, x in (("green", 205), ("blue", 128), ("red", 60)):
            s = float(cfg.targets[color].size_cm) * 0.82
            card += [group(f"How_{color}", target(color, 22 + x, 142 + 32, s, shadow=False)),
                     ink.text(T[f"word_{color}"], 22 + x, 142 + 68, "heading", 8.5, H(theme.TARGET_COLORS[color]))]
    return [("Background", [rect(W, Hh, 0, BG_BAND)]),
            ("Pattern", dark_pattern(W, Hh)),
            ("Logo", inline_logo(ASSETS / "brand" / "logo-fa-stacked-white.svg", W / 2, 44, 84)),
            ("Title", [ink.text(T.title, W / 2, 94, "title", 38, white), ink.text(T.subtitle, W / 2, 129, "body", 8, pale)]),
            ("Start_pulse_ring", [start_ring(sb.x_cm, sb.y_cm, side, 1.0)]),
            ("Leaderboard_card" if board else "How_to_card", card),
            ("Start_box", [centred_rect(sb.x_cm, sb.y_cm, side, side, side * TILE_RADIUS, H(theme.TURQUOISE)),
                           ink.text(T.start, sb.x_cm, sb.y_cm, "title", 21, white)]),
            ("Call_to_action", [ink.text(T.cta_throw, W / 2, sb.y_cm + side / 2 + 14, "body", 7.5, white)]),
            ("Steady_progress", steady_progress(ink, W, Hh))]


def screen_instructions(ink: Ink, cfg) -> list[tuple[str, list[str]]]:
    T = cfg.text
    W, Hh, hud = float(cfg.wall.width_cm), float(cfg.wall.height_cm), float(cfg.layout.hud_height_cm)
    dots, _ = field_pattern(cfg, False)
    rows = []
    for i, color in enumerate(("green", "blue", "red")):
        y = 128 + i * 57
        py = y - (5 if color == "red" else 0)
        c = cfg.targets[color]
        items = [group("Box", target(color, 242, y, float(c.size_cm))),
                 ink.text(T[f"word_{color}"], 200, y - 8, "title", 12, H(theme.TARGET_COLORS[color]), "right"),
                 ink.text(T[f"meaning_{color}"], 200, y + 9, "body", 5.8, H(theme.TEXT_GREY), "right"),
                 centred_rect(42, py, 50, 15, 7.5, H(theme.TURQUOISE_PALE)),
                 ink.text(f"{c.points} {T.points_unit}", 42, py, "heading", 6.5, BG_BAND)]
        if color == "red":
            items.append(ink.text(T.red_extra, 42, y + 10, "body", 5.2, BG_BAND))
        rows.append(group(f"Row_{color}", items))
    top = max(lv["multiplier"] for lv in cfg.combo.levels)
    rules, y = [], 291.0
    for line in (T.rule_move, T.rule_combo.format(max=f"×{top:g}"), T.rule_positive):
        rules.append(ink.text(line, W / 2, y, "body", 6.3, BG_BAND, "middle", "top"))
        y += 12.2
    return [("Background", [rect(W, Hh, 0, BG_FIELD)]), ("Dot_pattern", dots), ("HUD_band", hud_band(cfg)),
            ("Band_title", [ink.text(T.how_to_title, W / 2, hud / 2, "heading", 14, H(theme.WHITE))]),
            ("Time_bar", time_bar(W, hud, 0.5)),
            ("Rule_balls", [ink.text((T.rule_balls if cfg.round.balls else T.rule_time).format(balls=cfg.round.balls, seconds=int(cfg.round.duration_s)), W / 2, 86, "heading", 10, BG_BAND)]),
            ("Rows", rows), ("Rules", rules), ("Footer", footer(cfg))]


def play_base(ink: Ink, cfg, score, secs, throws, m, frac, placement, accents=True) -> list[tuple[str, list[str]]]:
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    dots, marks = field_pattern(cfg, accents)
    out = [("Background", [rect(W, Hh, 0, BG_FIELD)]), ("Dot_pattern", dots), ("Accents", marks), ("Footer", footer(cfg))]
    if placement:
        out.append(("Targets", field_targets(cfg, placement)))
    return out + [("HUD_band", hud_band(cfg)), *hud_layers(ink, cfg, score, secs, throws, m, frac)]


def dim(cfg, alpha: int) -> tuple[str, list[str]]:
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    hud = float(cfg.layout.hud_height_cm)
    h = Hh - float(cfg.layout.footer_height_cm) - hud
    return ("Dim_field", [rect(W, h, 0, BG_FIELD, 0, hud, extra=f' fill-opacity="{num(alpha / 255)}"')])


def screen_countdown(ink: Ink, cfg) -> list[tuple[str, list[str]]]:
    W = float(cfg.wall.width_cm)
    return [*play_base(ink, cfg, 0, int(cfg.round.duration_s), 0, 1, 1.0, ()), dim(cfg, 170),
            ("Countdown", [ring(W / 2, 215, 42, 1.6, H(theme.TURQUOISE), id_="Ring"),
                           ink.text(cfg.text.get_ready, W / 2, 140, "heading", 12, BG_BAND),
                           ink.text("3", W / 2, 215, "numbers", 80, BG_BAND)])]


def screen_go(ink: Ink, cfg) -> list[tuple[str, list[str]]]:
    W = float(cfg.wall.width_cm)
    start = (("green", 72, 150), ("red", 150, 250), ("blue", 228, 150))
    return [*play_base(ink, cfg, 0, int(cfg.round.duration_s), 0, 1, 1.0, start),
            ("Go", [ink.text(cfg.text.go, W / 2, 205, "title", 34, H(theme.TURQUOISE))])]


def screen_ending(ink: Ink, cfg) -> list[tuple[str, list[str]]]:
    W = float(cfg.wall.width_cm)
    return [*play_base(ink, cfg, 245, 0, 7, 1, 0.0, SAMPLE_FIELD), dim(cfg, 228),
            ("End_message", [ink.text(cfg.text.end_title, W / 2, 190, "title", 30, BG_BAND),
                             ink.text(cfg.text.end_time, W / 2, 228, "body", 9, H(theme.TEXT_GREY))])]


def pill(ink: Ink, label: str, bg: str, fg: str, cx: float, cy: float, size: float) -> list[str]:
    w = ink.width(label, "heading", size) + size * 2.2
    h = ink.line_height("heading", size) * 1.25
    return [centred_rect(cx, cy, w, h, h / 2, bg), ink.text(label, cx, cy, "heading", size, fg)]


def screen_results(ink: Ink, cfg) -> list[tuple[str, list[str]]]:
    T = cfg.text
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    white, pale = H(theme.WHITE), H(theme.BLUE_PALE)
    cards = []
    hits = {"green": 2, "blue": 2, "red": 3}
    for color, x in (("green", 245), ("blue", 150), ("red", 55)):
        cards.append(group(f"Card_{color}", [
            centred_rect(x, 202, 80, 58, 4, white), group("Box", target(color, x, 187, 14, shadow=False)),
            ink.text(str(hits[color]), x, 208, "numbers", 15, BG_BAND),
            ink.text(T[f"word_{color}"], x, 222, "body", 5.5, H(theme.TEXT_GREY))]))
    return [("Background", [rect(W, Hh, 0, BG_BAND)]), ("Pattern", dark_pattern(W, Hh)),
            ("Logo", inline_logo(ASSETS / "brand" / "logo-fa-horizontal-white.svg", W / 2, 30, 100)),
            ("Score", [ink.text(T.well_done.format(name=SAMPLE["name"]), W / 2, 60, "heading", 10, H(theme.TURQUOISE_RAMP[5])),
                       ink.text(T.results_title, W / 2, 80, "body", 9, pale),
                       ink.text("245", W / 2, 124, "numbers", 72, white)]),
            ("Hit_cards", cards),
            ("Summary", [ink.text(T.results_hits.format(hits=7, throws=10), W / 2, 252, "heading", 8, white),
                         group("Pill_best", pill(ink, f"{T.results_best} ×3", H(theme.PURPLE), white, W / 2, 270, 6.5)),
                         group("Pill_rank", pill(ink, T.results_rank.format(rank=1), H(theme.TURQUOISE), white, W / 2, 290, 8))]),
            ("Next_player", [ink.text(T.results_next, W / 2, 350, "body", 6.5, pale),
                             rect(W - 120, 1.6, 0, H(theme.BLUE_RAMP[2]), 60, 361), rect(108, 1.6, 0, H(theme.TURQUOISE), W - 60 - 108, 361)]),
            ("Steady_progress", steady_progress(ink, W, Hh))]


# -- artwork lists ----------------------------------------------------------------------------------
ICONS = ("plus", "minus", "times", "slash", "dot", "ball_remaining", "ball_used", "calibration_cross",
         "impact_accepted", "impact_rejected")


def icon_doc(name: str, color: str, variant: str) -> str:
    m = 60.0
    if name in ("plus", "minus", "times", "slash", "dot"):
        shape = f'<g id="Icon_{name}" fill="{color}">{element_shape(name, m, 50, 50)}</g>'
    elif name in ("ball_remaining", "impact_accepted"):
        shape = f'<g id="Icon_{name}" fill="{color}"><circle cx="50" cy="50" r="30"/></g>'
    elif name in ("ball_used", "impact_rejected"):
        shape = f'<g id="Icon_{name}" fill="none" stroke="{color}" stroke-width="5"><circle cx="50" cy="50" r="27.5"/></g>'
    else:  # the five registration crosses on the calibration overlay: lines 16 cm long, 0.6 cm wide
        w = num(m * 0.6 / 16)
        shape = (f'<g id="Icon_{name}" stroke="{color}" stroke-width="{w}"><line x1="20" y1="50" x2="80" y2="50"/>'
                 '<line x1="50" y1="20" x2="50" y2="80"/></g>')
    return doc(f"Icon, {name} ({variant}, 100×100)", 100, 100, [shape])


# kit name, file under assets/, tile background for the sheet
ELEMENTS = (
    ("ball_remaining", "interface/indicators/ball-remaining.svg", BG_BAND),
    ("ball_used", "interface/indicators/ball-used.svg", BG_BAND),
    ("multiplier_active", "interface/indicators/multiplier-active.svg", BG_BAND),
    ("multiplier_inactive", "interface/indicators/multiplier-inactive.svg", BG_BAND),
    ("points_pill", "interface/indicators/points-pill.svg", BG_FIELD),
    ("rank_badge_first", "interface/indicators/rank-badge-first.svg", BG_FIELD),
    ("rank_badge", "interface/indicators/rank-badge.svg", BG_FIELD),
    ("time_bar", "interface/progress/time-bar.svg", BG_FIELD),
    ("results_countdown_bar", "interface/progress/results-countdown-bar.svg", BG_BAND),
    ("attract_card", "interface/panels/attract-card.svg", BG_BAND),
    ("results_card", "interface/panels/results-card.svg", BG_BAND),
    ("banner_pill", "interface/panels/banner-pill.svg", BG_BAND),
    ("pill_best_multiplier", "interface/panels/pill-best-multiplier.svg", BG_BAND),
    ("pill_rank", "interface/panels/pill-rank.svg", BG_BAND),
    ("ripple_hit", "feedback/ripple-hit.svg", BG_FIELD),
    ("ripple_miss", "feedback/ripple-miss.svg", BG_FIELD),
    ("ripple_start", "feedback/ripple-start.svg", BG_BAND),
    ("spawn_ring", "feedback/spawn-ring.svg", BG_FIELD),
    ("countdown_ring", "feedback/countdown-ring.svg", BG_FIELD),
    ("start_pulse_ring", "feedback/start-pulse-ring.svg", BG_BAND),
    ("background_play", "interface/backgrounds/background-play.svg", BG_DARK),
    ("background_instructions", "interface/backgrounds/background-instructions.svg", BG_DARK),
    ("background_dark", "interface/backgrounds/background-dark.svg", BG_DARK),
    ("status_simulation", "interface/operator/status-simulation.svg", BG_FIELD),
    ("status_sensor_offline", "interface/operator/status-sensor-offline.svg", BG_FIELD),
    ("debug_panel", "interface/operator/debug-panel.svg", BG_FIELD),
    ("calibration_overlay", "interface/operator/calibration-overlay.svg", BG_DARK),
    ("impact_accepted", "interface/operator/impact-accepted.svg", BG_FIELD),
    ("impact_rejected", "interface/operator/impact-rejected.svg", BG_FIELD),
)
LOGOS = (("logo_fa_horizontal", "logo-fa-horizontal", BG_FIELD), ("logo_fa_stacked", "logo-fa-stacked", BG_FIELD),
         ("logo_en_horizontal", "logo-en-horizontal", BG_FIELD), ("logo_en_stacked", "logo-en-stacked", BG_FIELD),
         ("logo_fa_horizontal_white", "logo-fa-horizontal-white", BG_BAND), ("logo_fa_stacked_white", "logo-fa-stacked-white", BG_BAND),
         ("logo_en_horizontal_white", "logo-en-horizontal-white", BG_BAND), ("logo_en_stacked_white", "logo-en-stacked-white", BG_BAND),
         ("logo_mark", "logo-mark", BG_FIELD))


def text_elements(ink: Ink, cfg) -> dict[str, tuple[float, float, list[str], str]]:
    """Each game element that carries text, with its real label and sizes: name -> (w, h, body, sheet tile)."""
    T = cfg.text
    white, pale, dark, purple = H(theme.WHITE), H(theme.BLUE_PALE), BG_BAND, H(theme.PURPLE)
    out: dict[str, tuple[float, float, list[str], str]] = {}

    def centred(name, w, h, parts, bg):
        out[name] = (w, h, parts, bg)

    centred("multiplier_active", 46, 13, [centred_rect(23, 6.5, 46, 13, 6.5, purple, "Badge"), ink.text(f"{T.multiplier} ×2", 23, 6.5, "heading", 7, white, id_="Label")], BG_BAND)
    centred("multiplier_inactive", 46, 13, [outline_rect(46, 13, 6.5, H(theme.BLUE_SOFT), 0.5, id_="Badge"), ink.text(f"{T.multiplier} ×1", 23, 6.5, "heading", 7, pale, id_="Label")], BG_BAND)
    for color in ("green", "blue", "red"):
        c = cfg.targets[color]
        centred(f"points_pill_{color}", 50, 15, [centred_rect(25, 7.5, 50, 15, 7.5, H(theme.TURQUOISE_PALE), "Pill"),
                                                 ink.text(f"{c.points} {T.points_unit}", 25, 7.5, "heading", 6.5, dark, id_="Label")], BG_FIELD)
        centred(f"popup_word_{color}", 60, 24, [ink.text(T[f"word_{color}"], 30, 12, "title", 15, H(theme.TARGET_COLORS[color]))], BG_FIELD)
        centred(f"popup_points_{color}", 40, 14, [ink.text(f"+{c.points}", 20, 7, "numbers", 9, dark)], BG_FIELD)
    centred("popup_multiplier", 24, 12, [ink.text("×2", 12, 6, "numbers", 7, purple)], BG_FIELD)
    centred("rank_badge_first", 9, 9, ['<circle id="Badge" cx="4.5" cy="4.5" r="4.5" fill="%s"/>' % H(theme.TURQUOISE), ink.text("1", 4.5, 4.5, "numbers", 5.5, white)], BG_FIELD)
    centred("rank_badge", 9, 9, ['<circle id="Badge" cx="4.5" cy="4.5" r="4.5" fill="%s"/>' % pale, ink.text("2", 4.5, 4.5, "numbers", 5.5, dark)], BG_FIELD)
    for name, txt, col in (("banner_combo", T.combo_up.format(m="×2"), purple), ("banner_market_busier", T.market_busier, dark)):
        w, h = ink.width(txt, "heading", 7) + 12, ink.line_height("heading", 7) * 1.2
        centred(name, w, h, [centred_rect(w / 2, h / 2, w, h, h / 2, white, "Pill"), ink.text(txt, w / 2, h / 2, "heading", 7, col, id_="Label")], BG_BAND)
    for name, txt, col, size in (("pill_best_multiplier", f"{T.results_best} ×3", purple, 6.5), ("pill_rank", T.results_rank.format(rank=1), H(theme.TURQUOISE), 8)):
        w, h = ink.width(txt, "heading", size) + size * 2.2, ink.line_height("heading", size) * 1.25
        centred(name, w, h, pill(ink, txt, col, white, w / 2, h / 2, size), BG_BAND)
    for name, txt, col in (("status_simulation", f"{T.simulation} - SIMULATION", purple), ("status_sensor_offline", T.sensor_offline, H(theme.RED))):
        w, h = ink.width(txt, "bold", 3.4) + 6, ink.line_height("bold", 3.4) * 1.2
        centred(name, w, h, [centred_rect(w / 2, h / 2, w, h, 2.5, col, "Pill"), ink.text(txt, w / 2, h / 2, "bold", 3.4, white, id_="Label")], BG_FIELD)
    centred("hud_time_warning", 50, 40, [ink.text("9", 25, 20, "numbers", 31, H(theme.RED))], BG_BAND)
    return out


def burst_docs(cfg) -> dict[str, str]:
    out = {}
    for color in ("green", "blue", "red"):
        half = float(cfg.targets[color].size_cm) / 2
        base = H(theme.TARGET_COLORS[color])
        r = half + 40
        body = [ring(0, 0, half + 17.5, 1.54, base, id_="Ripple_1"), ring(0, 0, half * 0.6 + 34, 0.63, base, id_="Ripple_2"),
                group("Symbols", burst(0, 0, color, half, 36))]
        out[f"hit_burst_{color}"] = doc(f"Hit burst, {color}, a frozen frame (symbols, ripples); units: wall cm", 2 * r, 2 * r, body, x=-r, y=-r)
    return out


def sheet(title: str, items: list[tuple[str, str, str]], ink: Ink, cols: int, tw: float, th: float) -> str:
    """Contact sheet: (caption, SVG document, tile colour) in rounded tiles, on a dark artboard."""
    gap, cap, pad = 24.0, 30.0, 16.0
    rows = (len(items) + cols - 1) // cols
    W = pad * 2 + cols * tw + (cols - 1) * gap + 16
    Hh = pad * 2 + rows * (th + cap + gap) + 16
    body = [rect(W, Hh, 0, BG_PAGE, id_="Artboard_background")]
    for i, (name, svg, bg) in enumerate(items):
        x = 24 + (i % cols) * (tw + gap)
        y = 24 + (i // cols) * (th + cap + gap)
        body.append(group(f"Tile_{safe_id(name)}", [
            rect(tw, th, 24 * tw / 150 if tw < 150 else 24, bg, x, y),
            placed(svg, x + 14, y + 14, tw - 28, th - 28),
            ink.text(name, x + tw / 2, y + th + cap / 2, "body", 13, H(theme.BLUE_PALE), localize=False)]))
    return doc(title, W, Hh, body)


# -- palette ----------------------------------------------------------------------------------------
def palette_entries() -> list[tuple[str, str, str, str]]:
    """(group, key, hex, name) for every colour in assets/design-tokens.json."""
    c = json.loads((ASSETS / "design-tokens.json").read_text(encoding="utf-8"))["color"]
    out = [("Primary", "turquoise", c["turquoise"], "Turquoise · Mofid colour, green box, CTA"),
           ("Primary", "dark_blue", c["dark_blue"], "Dark blue · blue box, HUD band, backgrounds"),
           ("Secondary", "purple", c["purple"], "Purple · multiplier, highlights"),
           ("Secondary", "red", c["red"], "Red · red box, time warning, volatility"),
           ("Secondary", "white", c["white"], "White · play field, cards, symbols")]
    for i, v in enumerate(c["turquoise_ramp"]):
        out.append(("Turquoise tints", f"turquoise_{i + 1}", v, f"Turquoise tint {i + 1} (dark to light)"))
    for i, v in enumerate(c["blue_ramp"]):
        out.append(("Blue tints", f"blue_{i + 1}", v, f"Blue tint {i + 1} (dark to light)"))
    out += [("Neutrals", "grey", c["grey"], "Grey · miss ripple"),
            ("Neutrals", "grey_light", c["grey_light"], "Light grey · field dots, dividers, bar track"),
            ("Neutrals", "text_grey", c["text_grey"], "Text grey · secondary text"),
            ("Neutrals", "pattern_blue", c["pattern_blue"], "Pattern blue · dots on dark blue")]
    return out


def rgb_of(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def ase_bytes(entries, name: str) -> bytes:
    def block(kind: int, payload: bytes) -> bytes:
        return struct.pack(">HI", kind, len(payload)) + payload

    def utf16(s: str) -> bytes:
        return struct.pack(">H", len(s) + 1) + s.encode("utf-16-be") + b"\0\0"

    blocks = [block(0xC001, utf16(name))]
    for _group, key, hx, _n in entries:
        r, g, b = rgb_of(hx)
        blocks.append(block(0x0001, utf16(key) + b"RGB " + struct.pack(">fff", r / 255, g / 255, b / 255) + struct.pack(">H", 2)))
    blocks.append(block(0xC002, b""))
    return b"ASEF" + struct.pack(">HHI", 1, 0, len(blocks)) + b"".join(blocks)


def palette_svg(entries, ink: Ink) -> str:
    groups: dict[str, list] = {}
    for e in entries:
        groups.setdefault(e[0], []).append(e)
    W = 1080.0
    tw, th, gap = 130.8, 92.0, 14.0
    y = 40.0
    body = [""]
    for gname, items in groups.items():
        body.append(ink.text(gname, 40, y + 12, "heading", 22, BG_BAND, "left", localize=False))
        y += 42
        for i, (_g, key, hx, _n) in enumerate(items):
            x = 40 + i * (tw + gap)
            r, g, b = rgb_of(hx)
            stroke = f' stroke="{H(theme.GREY_LIGHT)}" stroke-width="1"' if hx.upper() == "#FFFFFF" else ""
            body.append(group(f"Swatch_{key}", [
                f'<rect x="{num(x)}" y="{num(y)}" width="{num(tw)}" height="{num(th)}" rx="12" fill="{hx}"{stroke}/>',
                ink.text(key, x, y + th + 15, "bold", 13, BG_BAND, "left", localize=False),
                ink.text(hx, x, y + th + 33, "body", 12, H(theme.TEXT_GREY), "left", localize=False),
                ink.text(f"{r} {g} {b}", x, y + th + 50, "regular", 11, H(theme.TEXT_GREY), "left", localize=False)]))
        y += th + 76
    Hh = y + 10
    body[0] = rect(W, Hh, 0, BG_FIELD, id_="Artboard_background")
    return doc("Market Pulse palette (Mofid Brand Book v0.4)", W, Hh, body)


# -- text pages -------------------------------------------------------------------------------------
def title_lockup(ink: Ink, cfg) -> str:
    T = cfg.text
    W, Hh = 1080.0, 680.0
    body = [rect(W, Hh, 0, BG_BAND, id_="Artboard_background"),
            group("Title", [ink.text(T.title, W / 2, 270, "title", 190, H(theme.WHITE))]),
            group("Subtitle", [ink.text(T.subtitle, W / 2, 425, "body", 40, H(theme.BLUE_PALE))]),
            group("Latin_name", [ink.text("MARKET PULSE", W / 2, 530, "bold", 40, H(theme.TURQUOISE), localize=False, spacing=10)])]
    return doc("Title lockup, Market Pulse", W, Hh, body)


TYPE_ROLES = (
    ("title", "IRANSansX-Black", "38 · 34 · 30 · 21 · 15 · 12", "نبض بازار"),
    ("heading", "IRANSansX-ExtraBold", "14 · 12 · 11 · 10 · 8.5 · 8 · 7.5 · 7 · 6.5", "راهنمای بازی"),
    ("bold", "IRANSansX-Bold", "3.4", "حسگر متصل نیست"),
    ("body", "IRANSansX-Medium", "9 · 8 · 7.5 · 6.5 · 6.3 · 6 · 5.8 · 5.5 · 5.2", "فرصت رشد و انتخاب سنجیده"),
    ("numbers", "IRANSansX-Black", "80 · 72 · 31 · 15 · 9 · 7 · 5.5", "۰۱۲۳۴۵۶۷۸۹"),
)


def typography(ink: Ink, cfg) -> str:
    W = 1080.0
    dark, teal, grey = BG_BAND, H(theme.TURQUOISE), H(theme.TEXT_GREY)
    body = [""]
    body.append(ink.text("تایپوگرافی نبض بازار", 1040, 60, "title", 34, dark, "right"))
    body.append(ink.text(cfg.text.title, 1040, 170, "title", 110, dark, "right"))
    body.append(ink.text("MARKET PULSE", 40, 170, "bold", 40, teal, "left", localize=False, spacing=10))
    y = 300.0
    for role, font, sizes, sample in TYPE_ROLES:
        body.append(group(f"Role_{role}", [
            ink.text(sample, 1040, y, role, 44, dark, "right"),
            ink.text(role, 40, y - 18, "bold", 20, teal, "left", localize=False),
            ink.text(font, 40, y + 8, "body", 17, grey, "left", localize=False),
            ink.text(f"wall sizes (cm): {sizes}", 40, y + 32, "regular", 14, grey, "left", localize=False)]))
        y += 100
    body.append(group("Role_latin", [
        ink.text(" ".join("STEADY") + "   " + " ".join("PROGRESS"), 1040, y, "latin", 34, dark, "right", localize=False),
        ink.text("latin", 40, y - 18, "bold", 20, teal, "left", localize=False),
        ink.text("Helvetica, or Arial where it is missing", 40, y + 8, "body", 17, grey, "left", localize=False),
        ink.text("wall sizes (cm): 4.2 · 3.2", 40, y + 32, "regular", 14, grey, "left", localize=False)]))
    y += 96
    body.append(f'<line x1="40" y1="{num(y)}" x2="1040" y2="{num(y)}" stroke="{H(theme.GREY_LIGHT)}" stroke-width="1"/>')
    y += 44
    for name, role in (("Black", "title"), ("ExtraBold", "heading"), ("Bold", "bold"), ("DemiBold", "demibold"), ("Medium", "body"), ("Regular", "regular")):
        body.append(group(f"Weight_{name}", [ink.text("کارگزاری مفید، پیشرفت مداوم", 1040, y, role, 30, dark, "right"),
                                             ink.text(f"IRANSansX {name}", 40, y, "body", 18, grey, "left", localize=False)]))
        y += 50
    Hh = y + 10
    body[0] = rect(W, Hh, 0, BG_FIELD, id_="Artboard_background")
    return doc("Typography", W, Hh, body)


def messages(ink: Ink, cfg) -> tuple[str, dict[str, str]]:
    raw = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))["text"]
    W, row = 1080.0, 44.0
    Hh = 110 + row * len(raw) + 20
    body = [rect(W, Hh, 0, BG_FIELD, id_="Artboard_background"),
            ink.text("پیام‌های فارسی بازی", 1040, 56, "title", 34, BG_BAND, "right")]
    y = 110.0
    for key, value in raw.items():
        shown = value.format(**SAMPLE) if "{" in value else value
        role = "title" if key in ("title", "go", "end_title") else "heading"
        body.append(group(f"Message_{safe_id(key)}", [
            f'<line x1="40" y1="{num(y)}" x2="1040" y2="{num(y)}" stroke="{H(theme.GREY_LIGHT)}" stroke-width="1"/>',
            ink.text(key, 40, y + row / 2, "regular", 16, H(theme.TEXT_GREY), "left", localize=False),
            ink.text(shown, 1040, y + row / 2, role, 22, BG_BAND, "right")]))
        y += row
    return doc("Persian messages (config/settings.json, sample values in place of placeholders)", W, Hh, body), raw


# -- writing ----------------------------------------------------------------------------------------
def write(rel: str, text: str) -> None:
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_svgs(cfg) -> dict[str, str]:
    """Everything under svg/, palette/ and text/. Returns the outlined pages used in the kit PDF."""
    live, flat = Ink(False), Ink(True)
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    pages: dict[str, str] = {}

    def wall_doc(title: str, layers) -> str:
        return doc(title + "; 1 unit = 1 cm on the wall", W, Hh, layered(layers), px_w=W * PX_PER_CM)

    # wall: the play screen with named layers, and each layer alone on the same artboard
    write("svg/wall/wall_full.svg", wall_doc("Wall, play screen, all layers (sample round)", wall_layers(live, cfg)))
    pages["wall"] = wall_doc("Wall", wall_layers(flat, cfg))
    write("svg/wall/wall_full_outlined.svg", pages["wall"])
    for name, items in wall_layers(live, cfg):
        write(f"svg/wall/layers/{name.lower()}.svg", wall_doc(f"Wall layer, {name}", [(name, items)]))

    # screens: every other layout
    screens = {"01_attract": lambda i: screen_attract(i, cfg, False), "02_attract_leaderboard": lambda i: screen_attract(i, cfg, True),
               "03_instructions": lambda i: screen_instructions(i, cfg), "04_countdown": lambda i: screen_countdown(i, cfg),
               "05_go": lambda i: screen_go(i, cfg), "06_ending": lambda i: screen_ending(i, cfg), "07_results": lambda i: screen_results(i, cfg)}
    thumbs = [("play (wall_full)", pages["wall"], BG_DARK)]
    for name, fn in screens.items():
        write(f"svg/screens/{name}.svg", wall_doc(f"Screen, {name[3:]}", fn(live)))
        out = wall_doc(f"Screen, {name[3:]}", fn(flat))
        write(f"svg/screens/outlined/{name}.svg", out)
        thumbs.append((name[3:], out, BG_DARK))
    pages["screens"] = sheet("Screens", thumbs, flat, 4, 262, 340)

    # targets
    cards = []
    for color in ("green", "blue", "red"):
        kind = theme.TARGET_SYMBOLS[color]
        text = doc(f"Target, {color} ({kind} symbol), 160 units = the side of the box", 240, 240, target(color, 120, 120, 160))
        write(f"svg/targets/target_{color}.svg", text)
        write(f"svg/targets/variants/target_{color}_no_shadow.svg",
              doc(f"Target, {color}, no shadow", 240, 240, target(color, 120, 120, 160, shadow=False)))
        shutil.copyfile(ASSETS / "gameplay" / f"target-{color}.svg", _ensure(OUT / f"svg/targets/variants/target_{color}_game.svg"))
        cards.append((f"target_{color}", text, BG_FIELD))
    sb_flat = None
    for ink, suffix in ((live, "live"), (flat, "outlined")):
        side = 160.0
        body = [group("Pulse_ring", [start_ring(120, 120, side, side / 84)]),
                group("Tile", [centred_rect(120, 120, side, side, side * TILE_RADIUS, H(theme.TURQUOISE))]),
                group("Label", [ink.text(cfg.text.start, 120, 120, "title", 21 * side / 84, H(theme.WHITE))])]
        text = doc("Start box with its pulse ring and label", 240, 240, body)
        write(f"svg/targets/start_box_{suffix}.svg", text)
        if ink is flat:
            sb_flat = text
    shutil.copyfile(ASSETS / "interface" / "buttons" / "start-button.svg", _ensure(OUT / "svg/targets/variants/start_box_game.svg"))
    cards.append(("start_box", sb_flat, BG_BAND))
    write("svg/targets/_all_targets_sheet.svg", sheet("All targets", cards, live, 4, 230, 230))
    pages["targets"] = sheet("All targets", cards, flat, 4, 230, 230)

    # icons
    tiles = []
    for name in ICONS:
        white, dark = icon_doc(name, "#FFFFFF", "white"), icon_doc(name, BG_BAND, "dark")
        write(f"svg/icons/{name}.svg", white)
        write(f"svg/icons/{name}_dark.svg", dark)
        tiles += [(name, white, BG_BAND), (f"{name}_dark", dark, BG_FIELD)]
    write("svg/icons/_all_icons_sheet.svg", sheet("All icons", tiles, live, 5, 150, 150))
    pages["icons"] = sheet("All icons", tiles, flat, 5, 150, 150)

    # elements: one file for each piece of artwork, then those that carry a label
    tiles = []
    for name, rel, bg in ELEMENTS:
        text = (ASSETS / rel).read_text(encoding="utf-8")
        write(f"svg/elements/{name}.svg", text)
        tiles.append((name, text, bg))
    for name, text in burst_docs(cfg).items():
        write(f"svg/elements/{name}.svg", text)
        tiles.append((name, text, BG_FIELD))
    for ink, suffix in ((live, "live"), (flat, "outlined")):
        for name, (w, h, body, _bg) in text_elements(ink, cfg).items():
            write(f"svg/elements/with_text/{name}_{suffix}.svg", doc(f"{name} with its label; units: wall cm", w, h, body))
    live_tiles = tiles + [(f"{n} (text)", doc(n, w, h, b), bg) for n, (w, h, b, bg) in text_elements(live, cfg).items()]
    flat_tiles = tiles + [(f"{n} (text)", doc(n, w, h, b), bg) for n, (w, h, b, bg) in text_elements(flat, cfg).items()]
    write("svg/elements/_all_elements_sheet.svg", sheet("All elements", live_tiles, live, 7, 150, 110))
    pages["elements"] = sheet("All elements", flat_tiles, flat, 7, 150, 110)

    # brand
    tiles = []
    for name, src, bg in LOGOS:
        text = (ASSETS / "brand" / f"{src}.svg").read_text(encoding="utf-8")
        write(f"svg/brand/{name}.svg", text)
        tiles.append((name, text, bg))
    write("svg/brand/_all_brand_sheet.svg", sheet("All logos", tiles, live, 3, 300, 200))
    pages["brand"] = sheet("All logos", tiles, flat, 3, 300, 200)

    # palette
    entries = palette_entries()
    write("palette/colors.json", json.dumps({k: {"hex": h, "rgb": list(rgb_of(h)), "name": n} for _g, k, h, n in entries}, ensure_ascii=False, indent=2) + "\n")
    write("palette/colors.css", ":root {\n" + "".join(f"  --{k.replace('_', '-')}: {h};  /* {n} */\n" for _g, k, h, n in entries) + "}\n")
    (OUT / "palette" / "MarketPulse_colors.ase").write_bytes(ase_bytes(entries, "Market Pulse"))
    write("palette/palette.svg", palette_svg(entries, live))
    pages["palette"] = palette_svg(entries, flat)

    # text
    write("text/title_lockup_live.svg", title_lockup(live, cfg))
    pages["title"] = title_lockup(flat, cfg)
    write("text/title_lockup_outlined.svg", pages["title"])
    write("text/typography_live.svg", typography(live, cfg))
    pages["typography"] = typography(flat, cfg)
    write("text/typography_outlined.svg", pages["typography"])
    text, raw = messages(live, cfg)
    write("text/persian_messages_live.svg", text)
    pages["messages"], _ = messages(flat, cfg)
    write("text/persian_messages_outlined.svg", pages["messages"])
    write("text/persian_messages.json", json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
    return pages


def _ensure(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# -- one asset per file -----------------------------------------------------------------------------
SYMBOL_COLORS = (("white", theme.WHITE), ("dark_blue", theme.DARK_BLUE), ("turquoise", theme.TURQUOISE),
                 ("turquoise_soft", theme.TURQUOISE_RAMP[5]), ("blue_soft", theme.BLUE_SOFT), ("red", theme.RED))
# Which folder each piece of artwork from ELEMENTS and text_elements() belongs to.
ASSET_FOLDERS = {"indicators": "indicators", "progress": "progress", "panels": "panels", "feedback": "feedback",
                 "backgrounds": "backgrounds", "operator": "operator"}
TEXT_FOLDERS = (("multiplier", "indicators"), ("points_pill", "indicators"), ("rank_badge", "indicators"), ("hud_", "indicators"),
                ("popup_", "feedback"), ("banner_", "panels"), ("pill_", "panels"), ("status_", "operator"))
# Message -> (font role, colour) for the messages that are not plain headings.
MESSAGE_ROLES = {"title": "title", "start": "title", "go": "title", "end_title": "title", "word_green": "title", "word_blue": "title",
                 "word_red": "title", "subtitle": "body", "cta_throw": "body", "meaning_green": "body", "meaning_blue": "body",
                 "meaning_red": "body", "rule_positive": "body", "rule_move": "body", "rule_combo": "body", "red_extra": "body",
                 "end_time": "body", "end_balls": "body", "results_title": "body", "results_next": "body", "score": "body",
                 "time": "body", "simulation": "bold", "sensor_offline": "bold"}
MESSAGE_COLORS = {"word_green": theme.TURQUOISE, "word_blue": theme.DARK_BLUE, "word_red": theme.RED}


def text_doc(ink: Ink, title: str, s: str, role: str, fill: str, localize: bool = True, spacing: float = 0.0, size: float = 40.0) -> str:
    """One piece of text on an artboard as tall as the font's line and just wider than the text."""
    pad = size * 0.15
    w = ink.width(s, role, size, spacing, localize) + 2 * pad
    h = ink.line_height(role, size)
    return doc(title, w, h, [ink.text(s, w / 2, h / 2, role, size, fill, localize=localize, spacing=spacing)])


def split_groups(lines: list[str], marker: str) -> tuple[list[str], list[str]]:
    cut = next((i for i, x in enumerate(lines) if x.startswith(marker)), len(lines))
    return lines[:cut], lines[cut:]


def build_separate(cfg) -> None:
    """svg_separate/: every asset alone in its own file, on its own artboard, with a clear background."""
    live, flat = Ink(False), Ink(True)
    W, Hh, hud = float(cfg.wall.width_cm), float(cfg.wall.height_cm), float(cfg.layout.hud_height_cm)
    footer_y = Hh - float(cfg.layout.footer_height_cm)
    t = theme

    def out(rel: str, text: str) -> None:
        write(f"svg_separate/{rel}.svg", text)

    def cm(title: str, w: float, h: float, body: list[str], x: float = 0, y: float = 0) -> str:
        return doc(f"{title}; units: wall cm", w, h, body, x=x, y=y)

    # icons: every graphic element in every colour the game draws it in, and the small marks
    for kind in t.SYMBOLS:
        for cname, col in SYMBOL_COLORS:
            out(f"icons/{kind}_{cname}", doc(f"Icon, {kind} ({cname}, 100×100)", 100, 100, [f'<g id="Icon_{kind}" fill="{H(col)}">{element_shape(kind, 60, 50, 50)}</g>']))
    for name in ICONS[5:]:
        out(f"icons/{name}_white", icon_doc(name, "#FFFFFF", "white"))
        out(f"icons/{name}_dark_blue", icon_doc(name, BG_BAND, "dark blue"))

    # the box icons alone, in the same colours
    for kind in sorted(set(t.TARGET_SYMBOLS.values())):
        for cname, col in SYMBOL_COLORS:
            out(f"icons/box_icon_{kind}_{cname}", doc(f"Box icon, {kind} ({cname}); 100 units = the side of the box", 100, 100,
                                                      [group(f"Icon_{kind}", icon_shapes(kind, 100, 50, 50), f' fill="{H(col)}"')]))

    # targets
    for color in ("green", "blue", "red"):
        kind = t.TARGET_SYMBOLS[color]
        out(f"targets/target_{color}", doc(f"Target, {color} ({kind}), with shadow; 100 units = the side of the box", 128, 128, target(color, 64, 64, 100)))
        out(f"targets/target_{color}_no_shadow", doc(f"Target, {color} ({kind}), no shadow", 100, 100, target(color, 50, 50, 100, shadow=False)))
        out(f"targets/tile_{color}", doc(f"Tile only, {color} (no symbol)", 100, 100, [rect(100, 100, 100 * TILE_RADIUS, H(t.TARGET_COLORS[color]), id_="Tile")]))
    out("targets/shadow", doc("Box shadow only (the same for every colour)", 128, 128, tile_shadow(64, 64, 100)))
    side = 100.0
    out("targets/start_box", doc("Start box, no label", side, side, [rect(side, side, side * TILE_RADIUS, H(t.TURQUOISE), id_="Tile")]))
    for ink, suffix in ((flat, "outlined"), (live, "live")):
        out(f"targets/start_box_with_label_{suffix}", doc("Start box with its label", side, side, [
            rect(side, side, side * TILE_RADIUS, H(t.TURQUOISE), id_="Tile"), ink.text(cfg.text.start, 50, 50, "title", 21 * side / 84, H(t.WHITE), id_="Label")]))

    # logos
    for name, src, _bg in LOGOS:
        out(f"logos/{name}", (ASSETS / "brand" / f"{src}.svg").read_text(encoding="utf-8"))

    # the artwork in assets/, in folders of the same names
    for name, rel, _bg in ELEMENTS:
        if name in ("ripple_hit", "spawn_ring"):  # drawn below in each box's colour
            continue
        out(f"{ASSET_FOLDERS[rel.split('/')[-2]]}/{name}", (ASSETS / rel).read_text(encoding="utf-8"))

    # pieces that carry their real label
    for ink, suffix in ((flat, "outlined"), (live, "live")):
        for name, (w, h, body, _bg) in text_elements(ink, cfg).items():
            folder = next(f for prefix, f in TEXT_FOLDERS if name.startswith(prefix))
            out(f"{folder}/{name}_with_text_{suffix}", doc(f"{name} with its label; units: wall cm", w, h, body))

    # bars: track and fill apart
    out("progress/time_bar_track", cm("Time bar, track", W, 2.2, [rect(W, 2.2, 0, H(t.GREY_LIGHT), id_="Track")]))
    out("progress/time_bar_fill", cm("Time bar, fill at 100 %", W, 2.2, [time_gradient(W), rect(W, 2.2, 0, "url(#time-gradient)", id_="Fill")]))
    out("progress/results_bar_track", cm("Results countdown bar, track", 180, 1.6, [rect(180, 1.6, 0, H(t.BLUE_RAMP[2]), id_="Track")]))
    out("progress/results_bar_fill", cm("Results countdown bar, fill at 100 %", 180, 1.6, [rect(180, 1.6, 0, H(t.TURQUOISE), id_="Fill")]))

    # backgrounds: each layer alone
    dots, marks = field_pattern(cfg, True)
    wall = lambda title, body: doc(title + "; 1 unit = 1 cm on the wall", W, Hh, body, px_w=W * PX_PER_CM)  # noqa: E731
    out("backgrounds/field_dots", wall("Field dot pattern", dots))
    out("backgrounds/field_accents", wall("Field symbol accents", marks))
    dark_dots, dark_marks = split_groups(dark_pattern(W, Hh), '<g id="pattern-accents"')
    out("backgrounds/dark_dots", wall("Dark screen dot pattern", dark_dots))
    out("backgrounds/dark_accents", wall("Dark screen symbol accents", dark_marks))
    band = hud_band(cfg)
    out("backgrounds/hud_band", cm("HUD band with its dots", W, hud, band))
    out("backgrounds/hud_band_solid", cm("HUD band, colour only", W, hud, band[:1]))
    out("backgrounds/hud_band_dots", cm("HUD band, dots only", W, hud, band[1:]))
    out("backgrounds/footer_divider", cm("Footer divider line", W - 24, 0.25, [rect(W - 24, 0.25, 0, H(t.GREY_LIGHT), id_="Divider")]))

    # feedback: the rings in each box's colour and size, and the hit bursts
    for color in ("green", "blue", "red"):
        half, col = float(cfg.targets[color].size_cm) / 2, t.TARGET_COLORS[color]
        a, b = mid_ripple(half, half + 20, 2.2), mid_ripple(half * 0.6, half + 34, 0.9)
        out(f"feedback/ripple_hit_{color}", ring_doc(f"Hit ripple, {color} box, half-way; units: wall cm", [(a[0], a[1], col), (b[0], b[1], col)]))
        out(f"feedback/spawn_ring_{color}", ring_doc(f"Spawn ring, new {color} box, half-way; units: wall cm", [(*mid_ripple(half * 1.2, half * 2, 0.9), col)]))
    for name, text in burst_docs(cfg).items():
        out(f"feedback/{name}", text)

    # operator: the calibration overlay's parts
    lay = cfg.layout
    fx0, s = float(lay.side_margin_cm), float(lay.start_button.size_cm)
    lines = [f'<line x1="{x}" y1="0" x2="{x}" y2="{num(Hh)}"/>' for x in range(0, int(W) + 1, 50)]
    lines += [f'<line x1="0" y1="{y}" x2="{num(W)}" y2="{y}"/>' for y in range(0, int(Hh) + 1, 50)]
    out("operator/calibration_grid", wall("Calibration grid", [group("Grid", lines, f' stroke="{H(t.BLUE_SOFT)}" stroke-width="0.2"')]))
    fw, fh = W - 2 * fx0, footer_y - hud
    out("operator/calibration_field_frame", cm("Calibration, frame of the play field", fw, fh, [
        f'<rect x="0.2" y="0.2" width="{num(fw - 0.4)}" height="{num(fh - 0.4)}" fill="none" stroke="{H(t.PURPLE)}" stroke-width="0.4"/>']))
    out("operator/calibration_start_frame", cm("Calibration, frame of the start box", s, s, [
        f'<rect x="0.1" y="0.1" width="{num(s - 0.2)}" height="{num(s - 0.2)}" fill="none" stroke="{H(t.TURQUOISE)}" stroke-width="0.2"/>']))
    out("operator/calibration_cross", cm("Calibration registration cross", 16, 16, [group("Cross", [
        '<line x1="0" y1="8" x2="16" y2="8"/>', '<line x1="8" y1="0" x2="8" y2="16"/>'], f' stroke="{H(t.RED)}" stroke-width="0.6"')]))

    # text: every message on its own, and the Latin lines
    raw = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))["text"]
    for ink, suffix in ((flat, "outlined"), (live, "live")):
        for key, value in raw.items():
            shown = value.format(**SAMPLE) if "{" in value else value
            col = H(MESSAGE_COLORS.get(key, t.DARK_BLUE))
            out(f"text/{suffix}/{key}", text_doc(ink, f"Message, {key}", shown, MESSAGE_ROLES.get(key, "heading"), col))
        for name, line, role, col, spacing in (("market_pulse", "MARKET PULSE", "bold", t.TURQUOISE, 10.0),
                                               ("steady", " ".join("STEADY"), "latin", t.TURQUOISE, 0.0),
                                               ("progress", " ".join("PROGRESS"), "latin", t.DARK_BLUE, 0.0)):
            out(f"text/{suffix}/{name}", text_doc(ink, f"Latin line, {name}", line, role, H(col), localize=False, spacing=spacing))
        for key, style in cfg.profiles.styles.raw().items():
            out(f"text/{suffix}/profile_{key}_label", text_doc(ink, f"Play style, {key}", style["label"], "title", H(t.TURQUOISE_RAMP[0])))
            for i, sentence in enumerate(style["statements"], 1):
                out(f"text/{suffix}/profile_{key}_sentence_{i}", text_doc(ink, f"Play style, {key}, sentence {i}", sentence, "body", H(t.DARK_BLUE)))

    # numbers: the digits and signs the wall builds its scores from
    for d in range(10):
        out(f"numbers/digit_fa_{d}", text_doc(flat, f"Persian digit {d}", str(d), "numbers", BG_BAND))
        out(f"numbers/digit_en_{d}", text_doc(flat, f"Latin digit {d}", str(d), "numbers", BG_BAND, localize=False))
    out("numbers/sign_plus", text_doc(flat, "Plus sign (points)", "+", "numbers", BG_BAND, localize=False))
    out("numbers/sign_times", text_doc(flat, "Times sign (multiplier)", "×", "numbers", BG_BAND, localize=False))
    for m in (1, 2, 3, 4, 6, 8):  # ×6 and ×8 only during Market Rush
        out(f"numbers/multiplier_x{m}", text_doc(flat, f"Multiplier ×{m}", f"×{m}", "numbers", H(t.PURPLE)))

    build_separate_upgrade(cfg, out, cm)
    write("svg_separate/README.md", SEPARATE_README)


def wrap_words(ink: Ink, s: str, role: str, size: float, max_w: float) -> list[str]:
    lines, line = [], ""
    for word in s.split():
        trial = f"{line} {word}".strip()
        if line and ink.width(trial, role, size) > max_w:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line]


def rush_price_line(W: float, h: float, amp: float, speed: float) -> str:
    """The moving price line, as the game draws it at the start of its movement."""
    pts = []
    for i in range(97):
        u = i / 96
        y = 0.55 * math.sin(8.0 * u) + 0.3 * math.sin(21.0 * u + 1.0) + 0.15 * math.sin(53.0 * u)
        pts.append(f"{num(u * W)} {num(h / 2 + amp * y)}")
    return "M" + " L".join(pts)


def rush_candles(W: float, h: float, up: str, down: str) -> tuple[list[str], list[str]]:
    """The candlestick strip, from the same random sequence as the game (screens._build_candles)."""
    rng = random.Random(23)
    n = max(1, int(W / 11))
    step = W / n
    y_of = lambda p: h * (0.14 + 0.72 * (1.0 - min(max(p, 0.0), 1.0)))  # noqa: E731
    price = 0.5
    ups, downs = [], []
    for i in range(n):
        close = price + rng.gauss(0.0, 0.11) + (0.5 - price) * 0.2
        lo, hi = min(price, close) - rng.uniform(0.01, 0.06), max(price, close) + rng.uniform(0.01, 0.06)
        x = (i + 0.5) * step
        top, bottom = y_of(max(price, close)), y_of(min(price, close))
        parts = [f'<rect x="{num(x - 0.4)}" y="{num(y_of(hi))}" width="0.8" height="{num(y_of(lo) - y_of(hi))}"/>',
                 f'<rect x="{num(x - 2.8)}" y="{num(top)}" width="5.6" height="{num(max(1.0, bottom - top))}"/>']
        (ups if close >= price else downs).extend(parts)
        price = close
    return ups, downs


def build_separate_upgrade(cfg, out, cm) -> None:
    """market_rush/, registration/ and results/: the parts added with name entry, Market Rush and the play style."""
    t = theme
    live, flat = Ink(False), Ink(True)
    W, Hh, hud = float(cfg.wall.width_cm), float(cfg.wall.height_cm), float(cfg.layout.hud_height_cm)
    footer_y = Hh - float(cfg.layout.footer_height_cm)
    T = cfg.text
    white, deep = H(t.WHITE), H(t.BLUE_DEEP)
    mult = f"×{float(cfg.rush.score_multiplier):g}"
    field_h = footer_y - hud

    # Market Rush: the background, whole and layer by layer
    field_solid = [rect(W, Hh, 0, deep, id_="Field")]
    field_dots = dot_grid("rush-field", 4, hud + 4, W - 4, footer_y - 3, 12.5, 0.55, t.BLUE_RAMP[1])
    band_solid = [rect(W, hud, 0, H(t.PURPLE), id_="HUD_band")]
    band_dots = dot_grid("rush-band", 3, 2, W - 3, hud - 2, 6.0, 0.45, t.lerp_color(t.PURPLE, t.WHITE, 0.16))
    divider = f'<line id="Divider" x1="12" y1="{num(footer_y)}" x2="{num(W - 12)}" y2="{num(footer_y)}" stroke="{H(t.BLUE_RAMP[2])}" stroke-width="0.25"/>'
    logo = inline_logo(ASSETS / "brand" / "logo-fa-horizontal-white.svg", W / 2, (footer_y + Hh) / 2, 58)
    up, down = H(t.lerp_color(t.BLUE_DEEP, t.TURQUOISE, 0.28)), H(t.lerp_color(t.BLUE_DEEP, t.RED, 0.28))
    ups, downs = rush_candles(W, field_h, up, down)
    candle_groups = [group("Candles_up", ups, f' fill="{up}"'), group("Candles_down", downs, f' fill="{down}"')]
    lines = ((f"price_line_main", 52.0, 0.9, 1.0, H(t.lerp_color(t.BLUE_DEEP, t.TURQUOISE, 0.62))),
             (f"price_line_thin", 34.0, 0.5, 1.45, H(t.lerp_color(t.BLUE_DEEP, t.WHITE, 0.2))))
    line_paths = {name: f'<path id="{name}" d="{rush_price_line(W, field_h, amp, speed)}" fill="none" stroke="{col}" stroke-width="{num(sw)}" stroke-linejoin="round"/>'
                  for name, amp, sw, speed, col in lines}
    whole = [*field_solid, group("Dot_pattern", field_dots),
             f'<g id="Candles" transform="translate(0 {num(hud)})">{"".join(candle_groups)}</g>',
             f'<g id="Price_lines" transform="translate(0 {num(hud)})">{"".join(line_paths.values())}</g>',
             *band_solid, group("HUD_dots", band_dots), divider, *logo]
    wall = lambda title, body: doc(title + "; 1 unit = 1 cm on the wall", W, Hh, body, px_w=W * PX_PER_CM)  # noqa: E731
    out("market_rush/background_market_rush", wall("Market Rush background, all layers (the candles and lines scroll in the game)", whole))
    out("market_rush/background_field_solid", wall("Market Rush field colour", field_solid))
    out("market_rush/background_field_dots", wall("Market Rush field dots", field_dots))
    out("market_rush/hud_band", cm("Market Rush HUD band with its dots", W, hud, [*band_solid, *band_dots]))
    out("market_rush/hud_band_solid", cm("Market Rush HUD band, colour only", W, hud, band_solid))
    out("market_rush/hud_band_dots", cm("Market Rush HUD band, dots only", W, hud, band_dots))
    out("market_rush/footer_divider", cm("Market Rush footer line", W - 24, 0.25, [rect(W - 24, 0.25, 0, H(t.BLUE_RAMP[2]), id_="Divider")]))
    out("market_rush/candles_strip", cm("Candlestick strip; repeats sideways", W, field_h, candle_groups))
    for name, col in (("up", up), ("down", down)):
        out(f"market_rush/candle_{name}", cm(f"One candle, {name}", 5.6, 24, [group("Candle", [
            '<rect x="2.4" y="0" width="0.8" height="24"/>', '<rect x="0" y="6" width="5.6" height="12"/>'], f' fill="{col}"')]))
    for name, _amp, _sw, _speed, _col in lines:
        out(f"market_rush/{name}", cm(f"Moving price line, {name}", W, field_h, [line_paths[name]]))
    out("market_rush/rush_bar_track", cm("Market Rush time bar, track", W, 1.6, [rect(W, 1.6, 0, H(t.BLUE_RAMP[1]), id_="Track")]))
    out("market_rush/rush_bar_fill", cm("Market Rush time bar, fill at 100 %", W, 1.6, [rect(W, 1.6, 0, H(t.RED), id_="Fill")]))

    # boxes with the white edge they get on the dark field, at their real size
    for color in ("green", "blue", "red"):
        side = float(cfg.targets[color].size_cm)
        w = side + 2.6
        edge = rect(w, w, w * 0.18, white, id_="White_edge")
        out(f"market_rush/box_edge_{color}", cm(f"White edge, {color} box", w, w, [edge]))
        out(f"market_rush/target_{color}_with_edge", cm(f"{color} box on the Market Rush field", w, w, [edge, *target(color, w / 2, w / 2, side, shadow=False)]))

    # registration: shapes
    fw, fh, r = 220.0, 34.0, 5.0
    fill = rect(fw, fh, r, H(t.TURQUOISE_PALE), id_="Fill")
    border = lambda col: outline_rect(fw, fh, r, H(col), 0.7, id_="Border")  # noqa: E731
    size = float(cfg.layout.start_button.size_cm)
    out("registration/name_field", cm("Name field", fw, fh, [fill, border(t.TURQUOISE)]))
    out("registration/name_field_error", cm("Name field, empty name refused", fw, fh, [fill, border(t.RED)]))
    out("registration/name_caret", cm("Typing caret", 0.7, 16, [rect(0.7, 16, 0, H(t.DARK_BLUE), id_="Caret")]))
    out("registration/card", cm("Registration card", 256, 114, [rect(256, 114, 5, white, id_="Card")]))
    out("registration/start_button_ready", cm("Start Game button, name is valid", size, size, [rect(size, size, size * 0.18, H(t.TURQUOISE), id_="Button")]))
    out("registration/start_button_disabled", cm("Start Game button, no name yet", size, size, [rect(size, size, size * 0.18, H(t.BLUE_MID), id_="Button")]))
    out("results/profile_card", cm("Play style card", W - 32, 84, [rect(W - 32, 84, 5, white, id_="Card")]))
    out("results/hit_card", cm("Hits card", 80, 46, [rect(80, 46, 4, white, id_="Card")]))

    # pieces with their real label
    style = cfg.profiles.styles.balanced
    for ink, suffix in ((flat, "outlined"), (live, "live")):
        def labelled(rel, title, w, h, body):
            out(f"{rel}_{suffix}", cm(title, w, h, body))

        def wide_pill(rel, label, bg, fg, size_cm):
            w, h = ink.width(label, "heading", size_cm) + size_cm * 2.2, ink.line_height("heading", size_cm) * 1.25
            labelled(rel, f"Pill: {rel}", w, h, pill(ink, label, bg, fg, w / 2, h / 2, size_cm))

        rush_pill = T.rush_pill.format(m=mult)
        labelled("market_rush/multiplier_pill_with_text", "Market Rush multiplier pill", 46, 13,
                 [centred_rect(23, 6.5, 46, 13, 6.5, white, "Pill"), ink.text(rush_pill, 23, 6.5, "heading", 7, BG_BAND, id_="Label")])
        labelled("market_rush/combo_badge_with_text", "Combo badge on the purple band", 46, 13,
                 [centred_rect(23, 6.5, 46, 13, 6.5, deep, "Badge"), ink.text(f"{T.multiplier} ×4", 23, 6.5, "heading", 7, white, id_="Label")])
        for name, txt, col in (("banner_start_with_text", T.rush_start.format(m=mult), H(t.PURPLE)), ("banner_end_with_text", T.rush_end, BG_BAND)):
            w, h = ink.width(txt, "heading", 7) + 12, ink.line_height("heading", 7) * 1.2
            labelled(f"market_rush/{name}", f"Banner: {name}", w, h, [centred_rect(w / 2, h / 2, w, h, h / 2, white, "Pill"), ink.text(txt, w / 2, h / 2, "heading", 7, col, id_="Label")])
        for name, txt, role, sz, col in (("title_with_text", T.rush_title, "title", 30, white),
                                         ("hud_seconds_with_text", T.rush_hud.format(seconds=9), "heading", 6.5, white),
                                         ("popup_word_blue_with_text", T.word_blue, "title", 15, H(t.BLUE_SOFT)),
                                         ("popup_points_with_text", "+30", "numbers", 11, white),
                                         ("popup_multiplier_with_text", "×4", "numbers", 7, white)):
            w, h = ink.width(txt, role, sz) + sz * 0.3, ink.line_height(role, sz)
            labelled(f"market_rush/{name}", f"Text: {name}", w, h, [ink.text(txt, w / 2, h / 2, role, sz, col, id_="Label")])

        labelled("registration/name_field_empty_with_text", "Name field with its placeholder", fw, fh,
                 [fill, border(t.TURQUOISE), ink.text(T.register_placeholder, fw / 2, fh / 2, "body", 10, H(t.GREY), id_="Label")])
        labelled("registration/name_field_filled_with_text", "Name field with a sample name", fw, fh,
                 [fill, border(t.TURQUOISE), ink.text(SAMPLE["name"], fw / 2, fh / 2, "heading", 14, BG_BAND, id_="Label")])
        for name, col, tc in (("ready", t.TURQUOISE, t.WHITE), ("disabled", t.BLUE_MID, t.BLUE_PALE)):
            labelled(f"registration/start_button_{name}_with_text", f"Start Game button, {name}", size, size,
                     [rect(size, size, size * 0.18, H(col), id_="Button"), ink.text(T.register_start, size / 2, size / 2, "title", 14, H(tc), id_="Label")])
        wide_pill("registration/error_pill_with_text", T.register_empty, H(t.RED), white, 6.5)

        cw = W - 32
        body = [rect(cw, 84, 5, white, id_="Card"), ink.text(T.profile_title, cw / 2, 11, "body", 6, H(t.TEXT_GREY), id_="Title"),
                ink.text(style.label, cw / 2, 28, "title", 15, H(t.TURQUOISE_RAMP[0]), id_="Style")]
        for i, line in enumerate(wrap_words(ink, style.statements[1], "body", 7.5, 246)):
            body.append(ink.text(line, cw / 2, 46 + i * 7.5 * 1.35, "body", 7.5, BG_BAND))
        labelled("results/profile_card_with_text", "Play style card with a sample", cw, 84, body)
        for color in ("green", "blue", "red"):
            labelled(f"results/hit_card_{color}_with_text", f"Hits card, {color}, with a sample", 80, 46,
                     [rect(80, 46, 4, white, id_="Card"), *target(color, 40, 11, 13, shadow=False),
                      ink.text("3", 40, 26, "numbers", 11, BG_BAND, id_="Hits"), ink.text(T[f"word_{color}"], 40, 38, "body", 5, H(t.TEXT_GREY), id_="Word")])
        wide_pill("results/best_multiplier_pill_with_text", f"{T.results_best} ×8", H(t.PURPLE), white, 6.5)
        wide_pill("results/rank_pill_with_text", T.results_rank.format(rank=1), H(t.TURQUOISE), white, 6.5)


SEPARATE_README = """# همه‌ی SVGهای جدا

در این پوشه هر فایل **فقط یک چیز** است: یک نماد، یک باکس، یک قطعه یا یک متن، روی آرت‌بورد خودش و با پس‌زمینه‌ی شفاف. صفحه‌ی کامل و برگه‌ی نمونه اینجا نیست؛ آن‌ها در `svg/` هستند.

| پوشه | چه چیزی دارد |
|---|---|
| `icons/` | پنج عنصر برند (+ − × / •) و سه نماد باکس (میله‌های رو به رشد، حلقه‌ی سبد، خط قیمت) در شش رنگی که بازی استفاده می‌کند، و نشانگرهای توپ و کالیبراسیون. روی ۱۰۰×۱۰۰. |
| `market_rush/` | طوفان بازار: پس‌زمینه‌ی کامل و هر لایه‌ی آن، نوار شمعی، خط قیمت، نوار زمان، پیل‌ها، بنرها، و باکس‌ها با لبه‌ی سفید. |
| `registration/` | ثبت‌نام: کادر نام در هر حالت، نشانگر تایپ، کارت، دکمه‌ی شروع بازی فعال و غیرفعال و پیام خطا. |
| `results/` | نتیجه: کارت سبک بازی، کارت برخوردها و پیل‌ها. |
| `targets/` | هر باکس با سایه، بدون سایه و فقط بدنه؛ سایه‌ی باکس؛ و مربع شروع با برچسب و بدون برچسب. |
| `logos/` | هر لوگوی برند. |
| `indicators/` | توپ‌ها، ضریب، پیل امتیاز و نشان رتبه. |
| `panels/` | کارت‌ها، بنرها و پیل‌ها. |
| `progress/` | نوار زمان و نوار نتیجه، هم کامل و هم جدا (مسیر و پرشدگی). |
| `backgrounds/` | سه پس‌زمینه، و هر لایه‌ی آن‌ها جدا: نقطه‌ها، نمادها، نوار بالا و خط پایین. |
| `feedback/` | موج‌ها و حلقه‌ها به رنگ و اندازه‌ی هر باکس، انفجار برخورد و متن‌های شناور. |
| `operator/` | پیل‌های وضعیت، پنل عیب‌یابی و هر بخش صفحه‌ی کالیبراسیون. |
| `text/outlined/` و `text/live/` | هر پیام فارسی در یک فایل جدا: منحنی‌شده، و متن زنده که به فونت‌های پوشه‌ی `fonts/` نیاز دارد. |
| `numbers/` | رقم‌های فارسی و لاتین، علامت‌های + و ×، و ضریب‌های ×۱ تا ×۸. |

فایل‌هایی که نامشان `_outlined` دارد متن منحنی دارند و فایل‌های `_live` متن قابل ویرایش. رنگ متن‌های `text/` برای خوانایی آبی تیره است، جز کلمه‌ی هر باکس که رنگ همان باکس را دارد. نمادهای سفید روی زمینه‌ی سفید دیده نمی‌شوند؛ پشتشان یک رنگ تیره بگذارید. مقدارهای داخل متن‌ها مثل «سارا» یا «۷ برخورد» نمونه‌اند.
"""


# -- PDFs -------------------------------------------------------------------------------------------
def find_browser() -> str | None:
    for p in (Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
              Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
              Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe"):
        if p.is_file():
            return str(p)
    return shutil.which("chrome") or shutil.which("msedge") or shutil.which("google-chrome") or shutil.which("chromium")


def print_pdf(html: str, out: Path) -> None:
    browser = find_browser()
    if browser is None:
        raise RuntimeError("the PDFs need Chrome or Edge")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kit_") as tmp:
        src = Path(tmp) / "page.html"
        src.write_text(html, encoding="utf-8")
        target_pdf = Path(tmp) / "out.pdf"
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
                        f"--user-data-dir={tmp}/profile", f"--print-to-pdf={target_pdf}", src.as_uri()],
                       check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.copyfile(target_pdf, out)


def kit_pdf(pages: dict[str, str]) -> None:
    labels = (("wall", "Wall, all layers (sample round)"), ("screens", "Screens"), ("targets", "Targets and start box"),
              ("icons", "Icons (white and dark)"), ("elements", "Elements"), ("brand", "Brand logos"), ("palette", "Palette"),
              ("title", "Title lockup"), ("typography", "Typography"), ("messages", "Persian messages"))
    label_ink = Ink(True)
    parts = []
    for key, label in labels:
        body = [rect(1200, 1200, 0, BG_PAGE), label_ink.text(label, 36, 34, "body", 15, H(theme.GREY), "left", localize=False),
                placed(pages[key], 36, 64, 1128, 1100)]
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1200" viewBox="0 0 1200 1200">{"".join(body)}</svg>'
        parts.append(f'<div class="p">{svg}</div>')
    html = ('<!doctype html><meta charset="utf-8"><style>@page{size:900pt 900pt;margin:0}html,body{margin:0;padding:0}'
            '.p{width:1200px;height:1200px;overflow:hidden;break-after:page}.p:last-child{break-after:auto}svg{display:block}</style>'
            + "".join(parts))
    print_pdf(html, OUT / "MarketPulse_DesignKit.pdf")


def font_css() -> str:
    out = []
    for name, weight in (("Regular", 400), ("Medium", 500), ("DemiBold", 600), ("Bold", 700), ("ExtraBold", 800), ("Black", 900)):
        data = base64.b64encode((FONTS / f"IRANSansX-{name}.ttf").read_bytes()).decode("ascii")
        out.append(f"@font-face{{font-family:IRANSansX;font-weight:{weight};src:url(data:font/ttf;base64,{data}) format('truetype')}}")
    return "".join(out)


def inline(svg: str, css_w: str) -> str:
    vb, body = inner_of(svg)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" style="width:{css_w};display:block" '
            f'viewBox="{num(vb[0])} {num(vb[1])} {num(vb[2])} {num(vb[3])}">{body}</svg>')


def box_icon(color: str, size: str) -> str:
    return inline(doc("", 100, 100, target(color, 50, 50, 100, shadow=False)), size)


def guide_pdf(cfg) -> None:
    flat = Ink(True)
    c = {k: H(v) for k, v in (("teal", theme.TURQUOISE), ("blue", theme.DARK_BLUE), ("red", theme.RED), ("purple", theme.PURPLE),
                              ("grey", theme.TEXT_GREY), ("line", theme.GREY_LIGHT), ("pale", theme.TURQUOISE_PALE), ("sky", theme.BLUE_PALE))}
    wall = doc("", 300, 400, layered([l for l in wall_layers(flat, cfg) if l[0] != "Feedback_sample"]))
    marks = ((254, 13, "۱"), (46, 13, "۲"), (204, 21, "۳"), (186, 44, "۴"), (24, 65, "۵"), (130, 190, "۶"), (98, 385, "۷"))
    pins = "".join(f'<circle cx="{x}" cy="{y}" r="8.5" fill="{c["purple"]}" stroke="#fff" stroke-width="1.2"/>'
                   + flat.text(n, x, y, "bold", 10, "#FFFFFF", localize=False) for x, y, n in marks)
    wall_pinned = wall.replace("</svg>", pins + "</svg>")
    legend = (("امتیاز", "جمع امتیاز این دور، بالا سمت راست."), ("زمان", "ثانیه‌های باقی‌مانده. در ۱۰ ثانیه‌ی آخر قرمز می‌تپد."),
              ("توپ‌ها", "ده دایره؛ دایره‌ی پر یعنی توپی که هنوز مانده."), ("ضریب", "ضریب برخورد بعدی. از ×۲ به بالا بنفش می‌شود."),
              ("نوار زمان", "از چپ به راست خالی می‌شود."), ("میدان بازی", "باکس‌ها فقط این‌جا ظاهر می‌شوند."),
              ("لوگو", "پایین دیوار؛ این بخش جای باکس نیست."))
    legend_html = "".join(f'<div class="lg"><b>{"۱۲۳۴۵۶۷"[i]}</b><div><strong>{a}</strong><span>{b}</span></div></div>' for i, (a, b) in enumerate(legend))

    def sec(n: str, title: str) -> str:
        return f'<h2><i>{n}</i>{title}</h2>'

    def box_card(color: str, word: str, meaning: str, size: str, pts: str, extra: str = "") -> str:
        return (f'<div class="card box"><div class="bi">{box_icon(color, "54px")}</div><h3 style="color:{c[{"green": "teal", "blue": "blue", "red": "red"}[color]]}">{word}</h3>'
                f'<p>{meaning}</p><div class="kv"><span>اندازه</span><b>{size} سانتی‌متر</b></div><div class="kv"><span>امتیاز پایه</span><b>{pts}</b></div>{extra}</div>')

    def tip(t: str, d: str) -> str:
        return f'<div class="card tip"><h3>{t}</h3><p>{d}</p></div>'

    combo = "".join(f'<div class="cb{" on" if m > 1 else ""}"><span>برخورد {fa_digits(str(i + 1))}</span><b>×{fa_digits(str(m))}</b></div>'
                    for i, m in enumerate((1, 1, 2, 2, 3, 3, 4, 4)))
    motion = (("green", "رشد", "آرام و یکنواخت به چپ و راست حرکت می‌کند."), ("blue", "تنوع سبد", "مسیری متعادل به شکل عدد هشت لاتین دارد."),
              ("red", "نوسان", "نامنظم حرکت می‌کند و اندازه‌اش تغییر می‌کند."))
    motion_html = "".join(f'<div class="card mo">{box_icon(col, "40px")}<div><h3>{w}</h3><p>{d}</p></div></div>' for col, w, d in motion)
    worked = (("green", "رشد", "۱۰ × ۱", "۱۰"), ("red", "نوسان", "۳۰ × ۱", "۳۰"), ("blue", "تنوع سبد", "۱۵ × ۲", "۳۰"), ("red", "نوسان", "۳۰ × ۲", "۶۰"))
    worked_html = "".join(f'<div class="card wk">{box_icon(col, "26px")}<div><span>{w}</span><bdi dir="ltr">{calc}</bdi></div><b>{pts}</b></div>' for col, w, calc, pts in worked)
    css = f"""{font_css()}
@page{{size:A4;margin:0}}*{{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
html,body{{margin:0;padding:0;font-family:IRANSansX;color:{c['blue']};font-size:11.5px;line-height:1.65;direction:rtl}}
.page{{width:210mm;height:297mm;position:relative;overflow:hidden;break-after:page;padding:0 13mm}}.page:last-child{{break-after:auto}}
header{{margin:0 -13mm;padding:9mm 13mm 8mm;background:linear-gradient(90deg,{c['blue']},{c['teal']});color:#fff}}
header h1{{margin:0;font-size:38px;font-weight:900;line-height:1.25}}header .en{{direction:ltr;font-size:12px;font-weight:700;letter-spacing:4px;opacity:.95;text-align:right}}
header p{{margin:3px 0 0;font-size:13px;font-weight:500}}
.call{{margin:5mm 0 0;background:{c['pale']};border-radius:10px;padding:9px 14px;font-size:12.5px}}.call b{{font-weight:800;color:{c['blue']}}}
h2{{margin:5mm 0 2.5mm;font-size:15.5px;font-weight:800;display:flex;align-items:center;gap:8px;line-height:1.3}}
h2 i{{font-style:normal;width:24px;height:24px;border-radius:50%;background:{c['teal']};color:#fff;font-size:12.5px;display:flex;align-items:center;justify-content:center;font-weight:800}}
p{{margin:0}}.row{{display:flex;gap:8px}}.card{{flex:1;background:#fff;border:1px solid {c['line']};border-radius:10px;padding:9px 11px}}
h3{{margin:0 0 2px;font-size:13.5px;font-weight:800}}
.stat{{text-align:center;background:{c['blue']};color:#fff;border:none;padding:8px 4px}}.stat b{{display:block;font-size:24px;font-weight:900;line-height:1.3}}.stat span{{font-size:11px;font-weight:500}}
.box p{{color:{c['grey']};min-height:36px}}.bi{{margin-bottom:5px}}.kv{{display:flex;justify-content:space-between;border-top:1px solid {c['line']};padding-top:3px;margin-top:3px}}.kv span{{color:{c['grey']}}}.kv b{{font-weight:700}}
.extra{{margin-top:5px;background:{c['pale']};border-radius:6px;text-align:center;font-weight:700;padding:2px}}
.map{{display:flex;gap:6mm;align-items:flex-start}}.map .w{{flex:none;border:1px solid {c['line']};border-radius:6px;overflow:hidden}}
.lg{{display:flex;gap:7px;margin-bottom:6px;align-items:flex-start}}.lg>b{{flex:none;width:19px;height:19px;border-radius:50%;background:{c['purple']};color:#fff;font-size:11px;display:flex;align-items:center;justify-content:center;margin-top:2px}}.lg strong{{font-weight:800;display:block;line-height:1.35}}.lg span{{color:{c['grey']};line-height:1.45;display:block}}
.foot{{position:absolute;left:13mm;right:13mm;bottom:7mm;display:flex;justify-content:space-between;color:{c['grey']};font-size:10px;border-top:1px solid {c['line']};padding-top:4px}}
.combo{{display:flex;gap:4px}}.cb{{flex:1;text-align:center;border:1px solid {c['line']};border-radius:8px;padding:5px 0}}.cb span{{display:block;font-size:9px;color:{c['grey']}}}.cb b{{font-size:17px;font-weight:900;color:{c['grey']};display:block;direction:ltr;line-height:1.3}}.cb.on{{background:{c['purple']};border-color:{c['purple']}}}.cb.on b,.cb.on span{{color:#fff}}
.note{{margin-top:6px;color:{c['grey']}}}.mo{{display:flex;gap:9px;align-items:center}}.mo p{{color:{c['grey']};line-height:1.45}}
.grow{{display:flex;align-items:center;justify-content:center;gap:10px;margin-top:8px;background:{c['pale']};border-radius:10px;padding:7px}}.gr{{text-align:center;line-height:1.2}}.gr b{{display:block;font-size:20px;font-weight:900}}.gr span{{font-size:10px;color:{c['grey']}}}.grow em{{font-style:normal;font-size:16px;color:{c['teal']}}}
.wk{{display:flex;align-items:center;gap:7px;padding:6px 9px}}.wk div{{flex:1;line-height:1.3}}.wk span{{display:block;font-weight:800;font-size:11.5px}}.wk bdi{{display:block;color:{c['grey']};font-size:10.5px;text-align:right}}.wk>b{{font-size:17px;font-weight:900}}
.tip p{{color:{c['grey']};line-height:1.5}}.page2{{padding-top:9mm}}.page2 h2{{margin-top:7.5mm}}"""
    html = f"""<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>راهنمای نبض بازار</title><style>{css}</style><body>
<section class="page">
<header><h1>نبض بازار</h1><div class="en">MARKET PULSE</div><p>راهنمای بازیکن، بازی تعاملی کارگزاری مفید</p></header>
<div class="call"><b>چطور شروع کنیم؟</b> توپ را به مربع بزرگ «شروع» روی دیوار بزنید. بعد از راهنما، نام خود را با صفحه‌کلید بنویسید و «شروع بازی» را بزنید. سپس شمارش معکوس و بازی.</div>
{sec("۱", "هدف بازی")}
<p>با توپ به باکس‌های رنگی روی دیوار بزنید و تا می‌توانید امتیاز جمع کنید. امتیاز منفی وجود ندارد.</p>
<div class="row" style="margin-top:7px"><div class="card stat"><b>۶۰</b><span>ثانیه</span></div><div class="card stat"><b>۱۰</b><span>توپ</span></div><div class="card stat"><b>۴</b><span>ثانیه طوفان بازار</span></div><div class="card stat"><b>۱۲</b><span>بیشترین تعداد باکس</span></div><div class="card stat"><b>×۸</b><span>بیشترین ضریب در طوفان</span></div></div>
{sec("۲", "سه باکس")}
<div class="row">{box_card("green", "رشد", "رشد پایدار؛ ریسک کم، بازده کم", "۶۰", "۱۰")}{box_card("blue", "تنوع سبد", "مدیریت ریسک؛ ریسک و بازده متوسط", "۴۵", "۱۵")}{box_card("red", "نوسان", "نوسان و هیجان؛ ریسک و بازده احتمالی زیاد", "۲۵", "۳۰", '<div class="extra">+ سه باکس تازه و طوفان بازار</div>')}</div>
{sec("۳", "نقشه‌ی دیوار")}
<div class="map"><div class="w">{inline(wall_pinned, "74mm")}</div><div>{legend_html}</div></div>
<div class="foot"><span>نبض بازار · کارگزاری مفید</span><span>صفحه‌ی ۱ از ۲</span></div>
</section>
<section class="page page2">
{sec("۴", "ضریب امتیاز")}
<div class="combo">{combo}</div>
<p class="note">برخوردهای پشت‌سرهم ضریب را بالا می‌برند و امتیاز هر برخورد در ضریب همان لحظه ضرب می‌شود. یک پرتاب خطا ضریب را به ×۱ برمی‌گرداند، ولی از امتیازتان چیزی کم نمی‌کند.</p>
<div class="row" style="margin-top:8px">{worked_html}</div>
<p class="note">نمونه: چهار برخورد پشت‌سرهم، روی هم <b>۱۳۰ امتیاز</b>. برخورد بعدی با ضریب ×۳ حساب می‌شود.</p>
{sec("۵", "حرکت باکس‌ها")}
<div class="row">{motion_html}</div>
<p class="note">بعد از هر برخورد، همه‌ی باکس‌ها به جای تازه‌ای می‌روند. هر برخورد به نوسان سه باکس تازه اضافه می‌کند: یک رشد، یک تنوع سبد و یک نوسان.</p>
{sec("۶", "طوفان بازار")}
<p>هر برخورد به نوسان، طوفان بازار را شروع می‌کند: ۴ ثانیه با پس‌زمینه‌ی تیره، حرکت سریع‌تر، باکس‌های بیشتر و امتیاز ×۲ که در ضریب برخوردها هم ضرب می‌شود. برخورد دوباره به نوسان در این مدت، ۴ ثانیه‌ی تازه می‌دهد. بعد از آن، بازی به حالت عادی برمی‌گردد.</p>
{sec("۷", "پایان بازی")}
<div class="row"><div class="card"><h3>زمان یا توپ</h3><p>بازی با توپ دهم یا بعد از ۶۰ ثانیه تمام می‌شود، هر کدام زودتر برسد.</p></div><div class="card"><h3>برخورد دیرهنگام</h3><p>برخوردی که بعد از پایان زمان به دیوار برسد حساب نمی‌شود.</p></div><div class="card"><h3>پرتاب خطا</h3><p>یک توپ کم می‌شود و ضریب به ×۱ برمی‌گردد، ولی امتیازی کم نمی‌شود.</p></div></div>
<p class="note">در صفحه‌ی نتیجه، نام، امتیاز کل، سبک بازی و تعداد برخورد با هر رنگ را می‌بینید. سبک بازی فقط روش بازی شما را توصیف می‌کند و ارزیابی مالی یا توصیه‌ی سرمایه‌گذاری نیست.</p>
{sec("۸", "نکته‌ها")}
<div class="row">{tip("دقت مهم‌تر از سرعت", "با هر خطا ضریب از نو شروع می‌شود، پس زنجیره‌ی برخوردها را حفظ کنید.")}{tip("نوسان را از دست ندهید", "کوچک‌ترین باکس است، ولی بیشترین امتیاز را دارد و باکس‌های بیشتری به میدان می‌آورد.")}</div>
<div class="foot"><span>نبض بازار · کارگزاری مفید</span><span>صفحه‌ی ۲ از ۲</span></div>
</section></body></html>"""
    print_pdf(html.replace("×", LRM + "×"), OUT / "guide" / "MarketPulse_Guide_FA.pdf")  # keeps ×۲ the way the wall shows it


# -- README -----------------------------------------------------------------------------------------
README = """# کیت طراحی نبض بازار (برای Adobe Illustrator)

همه‌ی بخش‌های بصری بازی در این پوشه به‌صورت **وکتور قابل ویرایش** آمده‌اند. هر فایل از هندسه، رنگ‌ها و تنظیمات واقعی بازی ساخته شده (`config/settings.json` و `assets/design-tokens.json`)، پس دقیقاً با چیزی که روی دیوار نمایش داده می‌شود هم‌اندازه و هم‌تراز است. دیوار ۳۰۰ در ۴۰۰ سانتی‌متر است و در فایل‌های دیوار هر واحد برابر یک سانتی‌متر است.

## قبل از شروع

1. فونت‌های پوشه‌ی `fonts/` (خانواده‌ی IRANSansX) را روی سیستم **نصب کنید**. بدون آن‌ها، متن‌های زنده در Illustrator با فونت دیگری باز می‌شوند.
2. برای اینکه متن فارسی در Illustrator درست نمایش داده شود، در پنل Paragraph گزینه‌ی
   **Middle Eastern & South Asian Every-line Composer** را انتخاب کنید.
3. اعداد بازی **فارسی** هستند (۶۰، ۱۰، ۳·۲·۱). این رفتار با `ui.digits` در `config/settings.json` عوض می‌شود.
4. فایل‌هایی که متن دارند دو نسخه دارند: `live` یا بدون پسوند (متن قابل ویرایش) و `outlined` (متن تبدیل‌شده به منحنی که همیشه درست دیده می‌شود).

## محتویات پوشه

| پوشه / فایل | چه چیزی دارد |
|---|---|
| `MarketPulse_DesignKit.pdf` | همه‌ی صفحه‌ها در یک PDF وکتور: دیوار، صفحه‌های بازی، باکس‌ها، نمادها، المان‌ها، لوگوها، پالت رنگ، لوگوتایپ، تایپوگرافی و پیام‌ها. متن‌های این PDF منحنی هستند، پس در Illustrator همیشه درست باز می‌شوند. |
| `svg_separate/` | **هر نماد و هر قطعه در یک SVG جدا**، روی آرت‌بورد خودش و با پس‌زمینه‌ی شفاف. راهنمای آن در `svg_separate/README.md` است. |
| `svg/wall/wall_full.svg` | کل دیوار در صفحه‌ی بازی (۱۵۰۰×۲۰۰۰) با **لایه‌های نام‌گذاری‌شده**: Background، Dot_pattern، Accents، HUD_band، HUD_score، HUD_time، HUD_balls، HUD_multiplier، Time_bar، Targets، Feedback_sample و Footer. مقدارها نمونه‌اند: ۴ برخورد پشت‌سرهم و ۹ باکس. نسخه‌ی منحنی: `wall_full_outlined.svg`. |
| `svg/wall/layers/` | هر لایه‌ی دیوار به‌صورت فایل جدا، روی همان آرت‌بورد. وقتی روی هم بگذارید، دقیقاً جفت می‌شوند. |
| `svg/screens/` | بقیه‌ی صفحه‌های بازی با گروه‌های نام‌گذاری‌شده: شروع، برترین‌ها، راهنما، شمارش معکوس، «شروع!»، پایان دور و نتیجه. نسخه‌های منحنی در `outlined/` هستند. |
| `svg/targets/` | هر باکس کامل (سایه، بدنه و نماد) و مربع «شروع». در `variants/` نسخه‌ی بدون سایه و نسخه‌ی `_game` را می‌بینید که بازی همان را می‌خواند. |
| `svg/icons/` | هر نماد روی آرت‌بورد ۱۰۰×۱۰۰ در دو نسخه‌ی سفید و تیره: پنج عنصر گرافیکی برند (+ − × / •)، نشانگرهای توپ و نشانگرهای کالیبراسیون. |
| `svg/elements/` | هر قطعه‌ی رابط: توپ‌ها، ضریب، نوارها، کارت‌ها، پیل‌ها، حلقه‌ها و موج‌ها، انفجار برخورد، پس‌زمینه‌ها و ابزار اپراتور. قطعه‌هایی که متن دارند با متن واقعی‌شان در `with_text/` هستند. |
| `svg/brand/` | لوگوهای فارسی و انگلیسی، افقی و عمودی، رنگی و سفید، و نشانه‌ی مفید. برگرفته از کتاب برند، بدون تغییر. |
| `text/` | لوگوتایپ «نبض بازار»، صفحه‌ی تایپوگرافی و **همه‌ی پیام‌های فارسی بازی**، هر کدام در دو نسخه‌ی `_live` و `_outlined`. متن پیام‌ها در `persian_messages.json` هم هست. |
| `palette/` | پالت رنگ: `MarketPulse_colors.ase` (در Illustrator از پنل Swatches: Open Swatch Library → Other Library)، و نسخه‌های `colors.json`، `colors.css` و `palette.svg`. |
| `fonts/` | فونت‌های IRANSansX که بازی استفاده می‌کند. |
| `reference_screenshots/` | تصویر واقعی همه‌ی مراحل بازی، برای مقایسه و الهام. صفحه‌ی ثبت‌نام، طوفان بازار و صفحه‌ی نتیجه‌ی جدید فعلاً فقط در این پوشه‌اند و نسخه‌ی وکتور لایه‌دار ندارند. |
| `guide/` | راهنمای دو صفحه‌ای بازیکن (PDF وکتور)، راهنمای ساده‌ی اجرا و قوانین به فارسی و انگلیسی (`MarketPulse_Quick_Guide_*.pdf`)، و برگه‌ی ساده‌ی «چطور بازی کنم؟» (`MarketPulse_HowToPlay_FA.pdf`). |

## برگرداندن طراحی به بازی

| چه چیزی | چطور وارد بازی می‌شود |
|---|---|
| **باکس‌ها، لوگوها و قطعه‌های رابط** | فایل هم‌نام را در پوشه‌ی `assets/` جایگزین کنید. `svg/asset_map.json` نشان می‌دهد هر فایل کیت به کدام فایل بازی می‌رود، و `assets/manifest.json` فهرست چیزهایی است که بازی می‌خواند. خروجی SVG باید فقط شکل باشد: بدون متن زنده، تصویر، فیلتر، ماسک و `<use>`. اگر فایلی خراب باشد، بازی آن را در لاگ می‌نویسد و همان شکل را با کد می‌کشد. |
| **رنگ‌ها** | مقدار HEX را در `assets/design-tokens.json` عوض کنید و بازی را دوباره اجرا کنید. لوگوها هرگز تغییر رنگ نمی‌دهند. |
| **فونت** | فایل TTF را در `assets/fonts/` بگذارید و نامش را در `config/settings.json`، بخش `fonts`، بالای فهرست همان نقش بنویسید. |
| **متن پیام‌ها** | در `config/settings.json`، بخش `text`. نگه‌دارنده‌هایی مثل `{balls}` را حذف نکنید. |
| **اندازه‌ها** | اندازه‌ی باکس‌ها در `targets.*.size_cm` و نوار بالا، پایین و مربع شروع در `layout` از `config/settings.json`. |
| **بقیه‌ی جزئیات** | حلقه‌ها، نوارها، نقش نقطه‌ای، پس‌زمینه‌ها و پیل‌هایی که اندازه‌شان به متن بستگی دارد با کد رسم می‌شوند. فایل‌هایشان مرجع طراحی‌اند. طرح نهایی را به‌صورت SVG یا PDF به تیم برنامه‌نویسی بدهید تا در بازی پیاده شود. |

## نکته‌های برند

- عنوان‌ها در بازی با IRANSansX Black نمایش داده می‌شوند، چون فونت Dana (فونت عنوان در کتاب برند) در دسترس نبود. اگر فایل Dana را در `assets/fonts/` بگذارید، بازی خودش از آن استفاده می‌کند.
- طبق کتاب برند، قرمز و بنفش نباید رنگ متن باشند. بازی فعلاً کلمه‌ی «نوسان» و ضریب شناور را با این رنگ‌ها نشان می‌دهد و کیت همان را بازتاب می‌دهد. این مورد نیاز به تصمیم طراح دارد.
- نام لاتین MARKET PULSE در لوگوتایپ فقط برای مصارف بیرون از بازی است و روی دیوار نمایش داده نمی‌شود.
- امتیاز باکس‌ها (۱۰، ۱۵ و ۳۰) مقدار پیش‌فرض است و در سناریو عدد قطعی ندارد.

## ساختن دوباره‌ی کیت

اگر تنظیمات بازی عوض شد، مثلاً اندازه‌ی باکس‌ها یا رنگ‌ها، کیت را با تنظیمات جدید دوباره بسازید. داخل پوشه‌ی بازی اجرا کنید:

```bash
.venv\\Scripts\\python tools/export_design_kit.py
```

این کار پوشه‌ی `design/` را بازنویسی می‌کند. برای ساختن PDF به Chrome یا Edge نیاز است. نسخه‌های outlined به fonttools و uharfbuzz و بررسی PDFها به PyMuPDF نیاز دارند (`pip install -r requirements-dev.txt`).
"""


def asset_map() -> dict[str, str]:
    out = {f"svg/elements/{name}.svg": f"assets/{rel}" for name, rel, _ in ELEMENTS}
    out.update({f"svg/brand/{name}.svg": f"assets/brand/{src}.svg" for name, src, _ in LOGOS})
    out.update({f"svg/targets/variants/target_{c}_game.svg": f"assets/gameplay/target-{c}.svg" for c in ("green", "blue", "red")})
    out["svg/targets/variants/start_box_game.svg"] = "assets/interface/buttons/start-button.svg"
    return out


# -- checks -----------------------------------------------------------------------------------------
def validate() -> list[str]:
    """Every SVG parses and is pure vector; outlined files have no live text; the PDFs have no images."""
    problems = []
    svgs = sorted(OUT.rglob("*.svg"))
    for path in svgs:
        rel = path.relative_to(OUT).as_posix()
        data = path.read_text(encoding="utf-8")
        try:
            root = ET.fromstring(data.encode("utf-8"))
        except ET.ParseError as exc:
            problems.append(f"{rel}: {exc}")
            continue
        tags = {e.tag.rsplit("}", 1)[-1] for e in root.iter()}
        if tags & {"image", "foreignObject"} or "data:image" in data:
            problems.append(f"{rel}: raster content")
        if "viewBox" not in root.attrib:
            problems.append(f"{rel}: no viewBox")
        if "outlined" in rel and "text" in tags:
            problems.append(f"{rel}: live text in an outlined file")
    import pymupdf

    for key, want in (("MarketPulse_DesignKit.pdf", 10), ("guide/MarketPulse_Guide_FA.pdf", 2)):
        with pymupdf.open(OUT / key) as pdf:
            if pdf.page_count != want:
                problems.append(f"{key}: {pdf.page_count} pages, expected {want}")
            if any(p.get_images() for p in pdf):
                problems.append(f"{key}: contains raster images")
    print(f"checked {len(svgs)} SVG files and 2 PDFs")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-screenshots", action="store_true", help="keep the existing reference_screenshots")
    args = ap.parse_args()
    cfg, _ = config.load(None)
    OUT.mkdir(exist_ok=True)
    pages = build_svgs(cfg)
    build_separate(cfg)
    write("svg/asset_map.json", json.dumps(asset_map(), indent=2) + "\n")
    fonts = OUT / "fonts"
    fonts.mkdir(exist_ok=True)
    for f in FONTS.glob("*.ttf"):
        shutil.copyfile(f, fonts / f.name)
    write("README.md", README)
    if not args.skip_screenshots:
        subprocess.run([sys.executable, str(ROOT / "main.py"), "--gallery", str(OUT / "reference_screenshots")], check=True, cwd=ROOT, timeout=300)
    kit_pdf(pages)
    guide_pdf(cfg)
    problems = validate()
    for p in problems:
        print("PROBLEM:", p, file=sys.stderr)
    print(f"kit written to {OUT}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
