"""Mofid brand palette (Brand Book v0.4, pages 8-9) and wall-to-pixel mapping."""
from __future__ import annotations

import pygame

from .icons import TARGET_ICONS


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# Primary colours
TURQUOISE = hex_rgb("#00A796")  # Mofid colour; positive market, profit, growth; CTA; titles
DARK_BLUE = hex_rgb("#004C6A")  # CTA, backgrounds, titles
# Secondary colours
PURPLE = hex_rgb("#832C73")  # highlight / index colour (not text, not CTA, <30% background)
RED = hex_rgb("#832C73")  # volatility / decline (not text, not background)
WHITE = (255, 255, 255)

# Tints and shades printed under the primary swatches (page 8), dark -> light
TURQUOISE_RAMP = [hex_rgb(h) for h in ("#00817D", "#008F8A", "#009B97", "#00AAA5", "#00B6B5", "#58C5C7", "#DEF1EE")]
BLUE_RAMP = [hex_rgb(h) for h in ("#00243D", "#003754", "#004667", "#004C6A", "#006F95", "#3D8DB2", "#B5D5E5")]
BLUE_DEEP = BLUE_RAMP[0]
BLUE_MID = BLUE_RAMP[4]
BLUE_SOFT = BLUE_RAMP[5]
BLUE_PALE = BLUE_RAMP[6]
TURQUOISE_PALE = TURQUOISE_RAMP[6]
# Neutrals used across the brand book layouts (dividers, letterhead, pattern)
GREY = hex_rgb("#B1BAC0")
GREY_LIGHT = hex_rgb("#CED8DE")
TEXT_GREY = hex_rgb("#546670")
PATTERN_BLUE = hex_rgb("#005379")  # dot pattern colour (page 22)

# MOFID_UI v4.1 (the redesign): every screen on the dark field, with these on it.
FIELD = DARK_BLUE  # #004C6A, the background of every screen
MIST = hex_rgb("#B5D5E5")  # secondary type, labels
TURQ = hex_rgb("#77C2BE")  # action, positive, multiplier
CORAL = hex_rgb("#832C73")  # time warning, Market Rush
DEEP = hex_rgb("#00374E")  # ink on turquoise; the Market Rush field

# The old three colours (the scenario's green, blue and red boxes), kept for the design-kit tools.
TARGET_COLORS = {"green": TURQUOISE, "blue": DARK_BLUE, "red": RED}
# The icon inside each box, by meaning; see icons.py.
TARGET_SYMBOLS = {color: icon.kind for color, icon in TARGET_ICONS.items()}
SYMBOLS = ("plus", "minus", "times", "slash", "dot")  # brand elements (page 23): pattern and hit bursts

# Colour tokens that assets/design-tokens.json may override, by their lower-case
# name ("turquoise", "blue_ramp", ...). The derived names above follow them.
COLOR_TOKENS = ("TURQUOISE", "DARK_BLUE", "PURPLE", "RED", "WHITE", "GREY", "GREY_LIGHT", "TEXT_GREY", "PATTERN_BLUE")
RAMP_TOKENS = ("TURQUOISE_RAMP", "BLUE_RAMP")
DEFAULTS = {name: tuple(globals()[name]) for name in COLOR_TOKENS + RAMP_TOKENS}  # brand values, never changed


def _derive() -> None:
    global BLUE_DEEP, BLUE_MID, BLUE_SOFT, BLUE_PALE, TURQUOISE_PALE, TARGET_COLORS
    BLUE_DEEP, BLUE_MID, BLUE_SOFT, BLUE_PALE = BLUE_RAMP[0], BLUE_RAMP[4], BLUE_RAMP[5], BLUE_RAMP[6]
    TURQUOISE_PALE = TURQUOISE_RAMP[6]
    TARGET_COLORS = {"green": TURQUOISE, "blue": DARK_BLUE, "red": RED}


def _parse_hex(value) -> tuple[int, int, int] | None:
    if isinstance(value, str) and len(value) == 7 and value[0] == "#":
        try:
            return hex_rgb(value)
        except ValueError:
            return None
    return None


def apply_tokens(colors: dict) -> list[str]:
    """Set the palette from the "color" section of the design tokens and return
    any problems. Tokens that are missing or invalid keep their brand default."""
    values = {name: list(v) if name in RAMP_TOKENS else v for name, v in DEFAULTS.items()}
    problems: list[str] = []
    for key, raw in colors.items():
        if key.startswith("_"):
            continue
        name = key.upper()
        if name in COLOR_TOKENS:
            rgb = _parse_hex(raw)
            if rgb is None:
                problems.append(f"color.{key}: {raw!r} is not a #RRGGBB colour; using {to_hex(values[name])}")
            else:
                values[name] = rgb
        elif name in RAMP_TOKENS:
            ramp = [_parse_hex(v) for v in raw] if isinstance(raw, list) else []
            if len(ramp) != len(DEFAULTS[name]) or None in ramp:
                problems.append(f"color.{key}: needs {len(DEFAULTS[name])} #RRGGBB colours, dark to light; using the default")
            else:
                values[name] = ramp
        else:
            problems.append(f"color.{key}: unknown colour token (ignored)")
    globals().update(values)
    _derive()
    return problems


def to_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(rgb)


def recolor_map() -> dict[str, str]:
    """Default hex -> current hex for every colour token that was changed.

    The SVG assets are drawn in the default palette; applying this map to their
    text makes them follow the tokens. A value shared by two tokens (dark blue is
    also the fourth step of the blue ramp) belongs to the first one listed."""
    owner: dict[str, str] = {}
    for name in COLOR_TOKENS + RAMP_TOKENS:
        cur, default = globals()[name], DEFAULTS[name]
        pairs = zip(default, cur) if name in RAMP_TOKENS else [(default, cur)]
        for d, c in pairs:
            owner.setdefault(to_hex(d), to_hex(c))
    return {d: c for d, c in owner.items() if d != c}


def lerp_color(a, b, t: float) -> tuple[int, int, int]:
    t = min(max(t, 0.0), 1.0)
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


class Viewport:
    """Maps wall centimetres to window pixels.

    The wall rectangle keeps the physical aspect ratio (3:4 by default) and is
    centred in the window; on a display with another aspect ratio the rest is
    letterboxed, so nothing is ever stretched. ``scale`` (display.scale) makes it smaller than
    the window, still in the middle: a projector that spills over the wall.
    """

    def __init__(self, win_w: int, win_h: int, wall_w_cm: float, wall_h_cm: float, scale: float = 1.0):
        self.win_w, self.win_h = win_w, win_h
        self.wall_w, self.wall_h = wall_w_cm, wall_h_cm
        self.scale = scale = min(max(float(scale), 0.1), 1.0)
        aspect = wall_w_cm / wall_h_cm
        if win_w / win_h > aspect:
            h = int(round(win_h * scale))
            w = int(round(h * aspect))
        else:
            w = int(round(win_w * scale))
            h = int(round(w / aspect))
        self.rect = pygame.Rect((win_w - w) // 2, (win_h - h) // 2, w, h)
        self.ppc = w / wall_w_cm  # pixels per centimetre

    def s(self, cm: float) -> int:
        """Length in cm -> pixels (at least 1)."""
        return max(1, int(round(cm * self.ppc)))

    def sf(self, cm: float) -> float:
        return cm * self.ppc

    def px(self, x_cm: float, y_cm: float) -> tuple[int, int]:
        return (
            self.rect.x + int(round(x_cm * self.ppc)),
            self.rect.y + int(round(y_cm * self.ppc)),
        )

    def cm_rect(self, x0: float, y0: float, x1: float, y1: float) -> pygame.Rect:
        a = self.px(x0, y0)
        b = self.px(x1, y1)
        return pygame.Rect(a[0], a[1], b[0] - a[0], b[1] - a[1])

    def to_norm(self, px: int, py: int) -> tuple[float, float] | None:
        """Window pixel -> normalised wall coordinates (None if outside the wall)."""
        if not self.rect.collidepoint(px, py):
            return None
        return (px - self.rect.x) / self.rect.w, (py - self.rect.y) / self.rect.h
