"""All screens, laid out in wall centimetres for a 3 m x 4 m portrait LED wall.

Visual language (Mofid Brand Book v0.4):
- palette: turquoise #00A796, dark blue #004C6A, red #F3716D, purple #832C73,
  plus the tints printed in the book; nothing outside it.
- the dot-grid pattern with + - x / o accents (pages 22-24) as the background,
- flat shapes with soft rounded corners, lots of empty space (page 18),
- the logo is never distorted or recoloured beyond the allowed white version on
  turquoise / dark-blue (pages 6-7),
- Dana for main titles when available, IRANSans for everything else and numbers.

Layout (wall cm, origin top-left): HUD band 0-64, field 64-370, footer 370-400.
The top of the wall is where the audience looks, the field is where balls go.

Market Rush swaps the white field for deep blue with a moving candlestick chart
and price line, and the HUD band for purple (under the book's 30 % limit). It
fades in and out with no flashing. Boxes get a white edge there, so the dark blue
box stays clear on the dark field.

Artwork (logos, targets, the start box, cards, badges, pills of a fixed size)
comes from the SVG files named in assets/manifest.json; every one has a code
fallback with the same shape. Shapes whose size follows the text or an
animation (rings, bars, banners, text pills) and the dot pattern are drawn here;
assets/documentation/asset-inventory.md lists which is which and why.
"""
from __future__ import annotations

import logging
import math
import random
import re
import time
from contextlib import contextmanager
from pathlib import Path

import pygame

from ..config import TIERS, Section, layout_left, layout_top, lottery_points, points_of, product_for
from ..game import flow as F
from ..game.rules import Outcome
from . import theme
from .attract import Attract
from .draw import PRODUCT_RADIUS, Sprites, dot_grid, draw_symbol, gradient_bar
from .effects import BADGE_R, Effects
from .text import Text, split_runs
from .theme import Viewport

log = logging.getLogger(__name__)

LOGOS = {  # key: (SVG asset in assets/manifest.json, raster fallback in assets/images)
    "fa_h": ("logo.fa_horizontal", "logo_fa_horizontal.png"),
    "fa_h_white": ("logo.fa_horizontal_white", "logo_fa_horizontal_white.png"),
    "fa_h_white_small": ("logo.fa_horizontal_white", "logo_fa_horizontal_white.png"),
    "fa_s_white": ("logo.fa_stacked_white", "logo_fa_stacked_white.png"),
    "fa_s": ("logo.fa_stacked", "logo_fa_stacked.png"),
    "fa_s_white_small": ("logo.fa_stacked_white", "logo_fa_stacked_white.png"),
}

BRAND_NAME = "گروه مالی مفید"  # the name shown with the logo (the artwork itself says «کارگزاری مفید»)
BRAND_WORD = "گروه مالی"  # drawn in place of the artwork's «کارگزاری»; its «مفید» stays

_WORDMARK = re.compile(rb'<g id="wordmark">(.*?)</g>', re.S)
_PATH = re.compile(rb'<path\b[^>]*?fill="(#[0-9A-Fa-f]{6})"[^>]*?\sd="([^"]*)"[^>]*/>')


def _split_wordmark(svg: bytes):
    """Split the logo's wordmark at its widest gap into «مفید» (left) and the word before it
    (right, «کارگزاری»). Returns the SVG without that word, its box (x0, y0, x1, y1) in the
    viewBox, the box of «مفید», its colour and the viewBox width; None when the artwork is not laid out that way."""
    m = _WORDMARK.search(svg)
    vb = re.search(rb'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', svg)
    if not m or not vb:
        return None
    parts = []
    for p in _PATH.finditer(m.group(1)):
        if re.search(rb"[^MLCZmlcz\s\d.,eE-]", p.group(2)) or re.search(rb"[mlc]", p.group(2)):
            return None  # only absolute M/L/C paths can be measured this simply
        n = [float(v) for v in re.findall(rb"-?\d*\.?\d+(?:[eE]-?\d+)?", p.group(2))]
        if len(n) < 2:
            continue
        parts.append((min(n[0::2]), max(n[0::2]), min(n[1::2]), max(n[1::2]), p))
    if len(parts) < 2:
        return None
    parts.sort(key=lambda q: q[0])
    best_gap, cut, reach = 0.0, None, parts[0][1]
    for i in range(1, len(parts)):
        gap = parts[i][0] - reach
        if gap > best_gap:
            best_gap, cut = gap, i
        reach = max(reach, parts[i][1])
    if cut is None or best_gap < 1.0:
        return None
    word, rest = parts[cut:], parts[:cut]
    box = (min(q[0] for q in word), min(q[2] for q in word), max(q[1] for q in word), max(q[3] for q in word))
    other = (min(q[0] for q in rest), min(q[2] for q in rest), max(q[1] for q in rest), max(q[3] for q in rest))
    color = theme.hex_rgb(word[-1][4].group(1).decode())
    body = m.group(1)
    for q in word:
        body = body.replace(q[4].group(0), b"")
    bare = svg[:m.start(1)] + body + svg[m.end(1):]
    return bare, box, other, color, float(vb.group(1))


def _ease_out(p: float) -> float:
    return 1.0 - (1.0 - min(max(p, 0.0), 1.0)) ** 3


def _ease_in_out(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def _ease_back(p: float) -> float:
    """Ease out with a small overshoot (about 6 %), for things that pop into place."""
    p = min(max(p, 0.0), 1.0)
    c = 1.2
    return 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2 if p > 0 else 0.0


def _phase(t: float, start: float, dur: float) -> float:
    """0..1 progress of a step that starts at start and takes dur seconds."""
    return min(max((t - start) / dur, 0.0), 1.0)


def _letterspaced(word: str) -> str:
    return " ".join(word)


def _short(name: str, n: int) -> str:
    """Player names come from outside (registration platform); keep them on the wall."""
    name = " ".join(name.split())
    return name if len(name) <= n else name[: n - 1].rstrip() + "…"


class Renderer:
    def __init__(self, cfg: Section, images_dir: Path, text: Text, sprites: Sprites, effects: Effects):
        self.cfg = cfg
        self.text = text
        self.sprites = sprites
        self.fx = effects
        self.T = cfg.text
        self.hud_h = float(cfg.layout.hud_height_cm)
        self.set_wall()
        self.shadow = bool(cfg.ui.target_shadow)
        self.images_dir = images_dir
        self.logo_src: dict[str, pygame.Surface | None] = {}  # raster fallbacks, read only if needed
        self.vp: Viewport | None = None
        self.vp_menu: Viewport | None = None
        self._wall_vp: Viewport | None = None  # the wall's own, while menu() has swapped self.vp
        self.score_pop_t = -10.0
        self._score_shown = 0.0  # the HUD's rolling score
        self._flying = 0  # points of hit badges still on their way to it
        self._score_t: float | None = None
        self.end_t0 = -10.0  # when the round ended (the pile of tiles falls from then)
        self.badge_pop_t = -10.0
        self.badge_shake_t = -10.0
        self.results_t0 = 0.0
        self.rush_title_t = -10.0
        self._leaving: set[int] = set()  # kept for older callers; boxes leave without rings now
        self._shatters: list[dict] = []  # the boxes that were hit, breaking apart (on_event "hit")
        self._fade_pending = False
        self._fade_t0 = -10.0
        self._prewarm: list[tuple[str, int]] = []
        self.dim: pygame.Surface | None = None
        # The player's replay on the results screen (camera.enabled), or a placeholder.
        self.photo_slot = bool(cfg.camera.enabled) and bool(cfg.results.get("show_photo", False))
        self.replay_round: str | None = None
        self.replay = None
        self.photo_img: tuple | None = None
        self.photo_effect = "natural"
        self.fx_rng = random.Random()
        self.card_done_at = 0.0
        self.boot_t0: float | None = None  # the logo intro as the game opens (start_boot)
        self.attract = Attract(self)  # the idle loop

    def set_wall(self) -> None:
        """The wall's size (again, after App.fit_wall). The menus (attract, instructions, name,
        results) are laid out for the 3:4 wall; when display.fill_screen makes the wall wider or
        taller, they are drawn in that frame, centred. set_viewport follows."""
        self.W = float(self.cfg.wall.width_cm)
        self.H = float(self.cfg.wall.height_cm)
        self.menu_w = float(self.cfg.wall.get("layout_width_cm", self.W))
        self.menu_h = float(self.cfg.wall.get("layout_height_cm", self.H))
        self.footer_y = self.H - float(self.cfg.layout.footer_height_cm)

    # -- viewport-dependent caches --------------------------------------------------------
    def set_viewport(self, vp: Viewport) -> None:
        self.vp = vp
        self.vp_menu = vp
        if self.menu_w < self.W or self.menu_h < self.H:  # the same scale, a smaller frame in the middle
            self.vp_menu = Viewport(vp.win_w, vp.win_h, self.menu_w, self.menu_h, vp.scale)
        self.text.clear()
        self.sprites.clear()
        canvas_bytes = vp.win_w * vp.win_h * 4
        self.text.set_budget(canvas_bytes)
        self.sprites.set_budget(canvas_bytes)
        self.logos = {
            "fa_h": self._logo("fa_h", width_cm=58),
            "fa_h_white": self._logo("fa_h_white", width_cm=100),
            "fa_h_white_small": self._logo("fa_h_white_small", width_cm=58),
            "fa_s_white": self._logo("fa_s_white", width_cm=84),
            "fa_s_white_small": self._logo("fa_s_white_small", width_cm=46),
        }
        self.bg_field = self._build_field()
        # The instructions page is dense text: plain dot grid, no symbol accents behind it. On a
        # taller wall its title band and footer are those of the menus' frame, in the middle.
        if self.menu_h < self.H:
            with self.menu():
                self.bg_light = self._build_light(band_title=self.T.how_to_title, accents=False)
        else:
            self.bg_light = self._build_light(band_title=self.T.how_to_title, accents=False)
        self.bg_dark = self._build_dark()
        self._build_intro()
        # the profile card: symbols only beside the photo and in the margins, never behind text
        mv = self.vp_menu  # the card is laid out in the menus' frame
        self.bg_card = self._build_dark([mv.cm_rect(60, 6, 240, 28), mv.cm_rect(4, 28, 162, 318),
                                         mv.cm_rect(154, 40, 292, 330), mv.cm_rect(10, 316, 290, 368),
                                         mv.cm_rect(90, 368, 210, 400)]) if self.photo_slot else self.bg_dark
        self.rush_rect = vp.cm_rect(0, self.hud_h, self.W, self.footer_y)
        self.bg_rush = self._build_rush()
        self.candles = self._build_candles()
        self.rush_layer = pygame.Surface((vp.win_w, vp.win_h)).convert()
        self.fade_src = pygame.Surface((vp.win_w, vp.win_h)).convert()
        # Market Rush starts in the middle of play: have its large text ready, so no frame stalls.
        self.text.render(self.T.rush_title, "title", vp.s(30), theme.WHITE)
        self.text.render(self.T.rush_start.format(m=f"×{float(self.cfg.rush.score_multiplier):g}"), "heading", vp.s(7), theme.PURPLE)
        self.dim = pygame.Surface(vp.rect.size).convert()
        self.dim.fill(theme.FIELD)  # the field's own colour: things fade into it (MOFID_UI v4.1)
        self.time_grad = gradient_bar(vp.rect.w, vp.s(2.2), theme.DARK_BLUE, theme.TURQUOISE)
        self._plan_prewarm()

    @contextmanager
    def menu(self):
        """Draw a menu screen in its 3:4 frame: its backgrounds still cover the whole wall."""
        if self.vp_menu is self.vp:
            yield
            return
        keep = self.vp, self.W, self.H, self.footer_y, self._wall_vp
        self._wall_vp = self.vp
        self.vp, self.W, self.H = self.vp_menu, self.menu_w, self.menu_h
        self.footer_y = self.H - float(self.cfg.layout.footer_height_cm)
        try:
            yield
        finally:
            self.vp, self.W, self.H, self.footer_y, self._wall_vp = keep

    def refresh_settings(self) -> None:
        """After the settings page changed a value kept here: the boxes' shadow and sizes."""
        self.shadow = bool(self.cfg.ui.target_shadow)
        if self.vp is not None:
            self._plan_prewarm()

    def _plan_prewarm(self) -> None:
        """Every product at every size (and the steps of the tiny boxes' gentle pulse)."""
        self._prewarm = []
        for tier in TIERS:
            base = self.vp.s(self.cfg.targets[tier].size_cm)
            pulse = float(self.cfg.motion[tier].pulse)
            steps = range(-int(pulse / 0.02 + 0.999), int(pulse / 0.02 + 0.999) + 1)
            for p in self.cfg.raw()["products"]:
                for i in steps:
                    self._prewarm.append((p["key"], int(round(base * (1 + i * 0.02)))))

    def prewarm(self, budget_s: float = 0.003) -> None:
        """Build the tile sprites a few at a time, so no frame stalls."""
        t0 = time.perf_counter()
        while self._prewarm and time.perf_counter() - t0 < budget_s:
            key, w = self._prewarm.pop()
            self.sprites.product(key, w, ring=True)

    def product_name(self, key: str | None) -> str:
        return next((p["name"] for p in self.cfg.raw()["products"] if p["key"] == key), "")

    def _logo(self, key: str, width_cm: float) -> pygame.Surface | None:
        w = self.vp.s(width_cm)
        img = self._brand_logo(LOGOS[key][0], w)  # height from the artwork: uniform scale only
        if img is not None:
            return img
        if key not in self.logo_src:
            path = self.images_dir / LOGOS[key][1]
            try:
                self.logo_src[key] = pygame.image.load(str(path)).convert_alpha()
            except (pygame.error, FileNotFoundError) as exc:
                log.warning("logo %s unavailable (%s); a text fallback is drawn", path, exc)
                self.logo_src[key] = None
        src = self.logo_src[key]
        if src is None:
            return None
        h = int(round(src.get_height() * w / src.get_width()))
        return pygame.transform.smoothscale(src, (w, h))

    def _brand_logo(self, name: str, w: int) -> pygame.Surface | None:
        """The logo at w pixels wide, its name reading «گروه مالی مفید»: the artwork's own «مفید»
        and the mark and tagline stay; the first word of the wordmark («کارگزاری») is taken
        out and BRAND_WORD drawn in its place, in its colour, filling the same box."""
        lib = self.sprites.assets
        src = lib.source(name)
        split = _split_wordmark(src) if src is not None else None
        if split is None:
            return lib.render(name, w)
        bare, (x0, y0, x1, y1), (m0, n0, m1, n1), color, view_w = split
        bare_name = name + "#group"
        if not lib.has(bare_name):
            lib.add(bare_name, bare)
        img = lib.render(bare_name, w)
        if img is None:
            return lib.render(name, w)
        s = img.get_width() / view_w
        bw, bh = (x1 - x0) * s, (y1 - y0) * s
        word = self.text.render(BRAND_WORD, "title", max(6, int(bh * 1.4)), color)
        ink = word.get_bounding_rect()
        if ink.w <= 0 or ink.h <= 0:
            return img
        word = word.subsurface(ink).copy()
        f = min(bw / ink.w, bh / ink.h)
        word = pygame.transform.smoothscale(word, (max(1, int(ink.w * f)), max(1, int(ink.h * f))))
        # the two words as close as in the artwork, the pair centred where the wordmark was
        gap = (x0 - m1) * s
        total = word.get_width() + gap + (m1 - m0) * s
        mid = (m0 + x1) / 2 * s
        left = mid - total / 2
        mofid = pygame.Rect(int(m0 * s) - 1, int(n0 * s) - 1, int((m1 - m0) * s) + 3, int((n1 - n0) * s) + 3)
        mofid = mofid.clip(img.get_rect())
        piece = img.subsurface(mofid).copy()
        img.fill((0, 0, 0, 0), mofid)
        img.blit(piece, (int(round(left)) - 1, mofid.y))
        img.blit(word, word.get_rect(midright=(int(round(left + total)), int((y0 + y1) / 2 * s))))  # Persian: from the right
        return img

    def _art(self, name: str, w: int, h: int, fallback) -> pygame.Surface:
        """The SVG asset `name` at w x h pixels; fallback() draws the same shape in code."""
        img = self.sprites.image(name, w, h)
        return img if img is not None else fallback()

    def _blit_logo(self, surf, key: str, center: tuple[int, int], fallback_color) -> None:
        img = self.logos.get(key)
        if img is not None:
            surf.blit(img, img.get_rect(center=center))
        else:
            self.text.blit(surf, BRAND_NAME, "heading", self.vp.s(8), fallback_color, center)

    def _wall(self, color) -> pygame.Surface:
        vp = self._wall_vp or self.vp  # the whole wall, also inside menu()
        bg = pygame.Surface((vp.win_w, vp.win_h)).convert()
        bg.fill(theme.BLUE_DEEP)  # letterbox outside the wall
        bg.fill(color, vp.rect)
        return bg

    def _band(self, bg: pygame.Surface, title: str | None) -> None:
        vp = self.vp
        band = vp.cm_rect(0, 0, self.W, self.hud_h)
        bg.fill(theme.DARK_BLUE, band)
        dot_grid(bg, self.sprites, band.inflate(-vp.s(6), -vp.s(4)), vp.sf(6.0), vp.sf(0.45), theme.PATTERN_BLUE)
        if title:
            self.text.blit(bg, title, "heading", vp.s(14), theme.WHITE, vp.px(self.W / 2, self.hud_h / 2))

    def _footer(self, bg: pygame.Surface) -> None:
        vp = self.vp
        y = self.footer_y
        pygame.draw.line(bg, theme.GREY_LIGHT, vp.px(12, y), vp.px(self.W - 12, y), max(1, vp.s(0.25)))
        self._blit_logo(bg, "fa_h", vp.px(self.W / 2, (y + self.H) / 2), theme.DARK_BLUE)

    def _build_light(self, band_title: str | None, accents: bool = True) -> pygame.Surface:
        vp = self.vp
        bg = self._wall(theme.WHITE)
        field = vp.cm_rect(4, self.hud_h + 4, self.W - 4, self.footer_y - 3)
        marks = [("plus", theme.TURQUOISE_RAMP[5]), ("slash", theme.BLUE_SOFT), ("minus", theme.TURQUOISE_RAMP[5]),
                 ("times", theme.BLUE_SOFT), ("dot", theme.TURQUOISE_RAMP[5]), ("plus", theme.BLUE_SOFT)]
        dot_grid(bg, self.sprites, field, vp.sf(12.5), vp.sf(0.55), theme.GREY_LIGHT, marks if accents else None,
                 vp.s(2.2), seed=11)
        self._band(bg, band_title)
        self._footer(bg)
        return bg

    def _micro(self, surf, text: str, pos, anchor: str = "midleft", color=None, size: float = 3.0) -> None:
        """The design's small letter-spaced Latin captions (MARKET PULSE, 01 — ATTRACT)."""
        self.text.blit(surf, _letterspaced(text).replace("   ", "  "), "latin", self.vp.s(size), color or theme.TURQ,
                       pos, anchor, localize=False)

    def _frame_marks(self, bg: pygame.Surface, caption: str | None) -> None:
        """Header captions with the logo, and the footer rule with its two captions: the frame
        every MOFID_UI v4.1 screen shares. On the menus' frame (self.vp)."""
        vp, W, H = self.vp, self.W, self.H
        if caption is not None:
            self._micro(bg, "GROUP MOFID", vp.px(16, 16))
            self._micro(bg, caption, vp.px(16, 23))
            logo = self.logos.get("fa_h_white_small")
            if logo is not None:
                bg.blit(logo, logo.get_rect(midright=vp.px(W - 16, 19)))
        y = H - 20
        pygame.draw.line(bg, theme.lerp_color(theme.FIELD, theme.MIST, 0.25), vp.px(16, y), vp.px(W - 16, y),
                         max(1, vp.s(0.25)))
        self._micro(bg, "GROUP MOFID · INTERACTIVE CHALLENGE" if caption is None else "GROUP MOFID",
                    vp.px(16, y + 8), color=theme.MIST, size=2.8)
        self.text.blit(bg, self.T.slogan, "body", vp.s(5), theme.MIST, vp.px(W - 16, y + 8), "midright")

    def _build_field(self) -> pygame.Surface:
        """The play screen: the dark field, faint plus marks on a loose grid, the footer."""
        vp = self.vp
        bg = self._wall(theme.FIELD)
        mark = theme.lerp_color(theme.FIELD, theme.MIST, 0.16)
        x0, y0, x1, y1 = 40.0, self.hud_h + 30, self.W - 30, self.footer_y - 20
        cols, rows = max(1, int((x1 - x0) / 52)), max(1, int((y1 - y0) / 52))
        for r in range(rows + 1):
            for c in range(cols + 1):
                if (r + c) % 2:
                    continue
                x, y = x0 + (x1 - x0) * c / cols, y0 + (y1 - y0) * r / rows
                draw_symbol(bg, "plus", vp.px(x, y), vp.s(2.4), mark)
        self._frame_marks(bg, None)
        return bg

    def _build_dark(self, keep_clear: list | None = None) -> pygame.Surface:
        vp = self.vp
        bg = self._wall(theme.FIELD)  # MOFID_UI v4.1: the plain field, a faint dot grid at most
        dot_grid(bg, self.sprites, vp.rect.inflate(-vp.s(8), -vp.s(8)), vp.sf(12.5), vp.sf(0.5),
                 theme.lerp_color(theme.FIELD, theme.MIST, 0.07), None, 0, seed=5, keep_clear=keep_clear)
        return bg

    def _build_rush(self) -> pygame.Surface:
        vp = self.vp
        bg = self._wall(theme.DEEP)  # Market Rush: the field goes deep, with coral accents
        field = vp.cm_rect(4, self.hud_h + 4, self.W - 4, self.footer_y - 3)
        dot_grid(bg, self.sprites, field, vp.sf(12.5), vp.sf(0.55), theme.lerp_color(theme.DEEP, theme.CORAL, 0.18))
        self._frame_marks(bg, None)
        return bg

    def _build_candles(self) -> pygame.Surface:
        """A strip of faint candlesticks, as wide as the field, that scrolls and repeats."""
        vp = self.vp
        w, h = self.rush_rect.size
        strip = pygame.Surface((w, h)).convert()
        key = (1, 2, 3)
        strip.fill(key)
        strip.set_colorkey(key)
        up = theme.lerp_color(theme.DEEP, theme.TURQ, 0.24)
        down = theme.lerp_color(theme.DEEP, theme.CORAL, 0.26)
        rng = random.Random(23)
        n = max(1, int(self.W / 11))
        step = w / n
        y_of = lambda p: int(h * (0.14 + 0.72 * (1.0 - min(max(p, 0.0), 1.0))))  # noqa: E731
        price = 0.5
        for i in range(n):
            close = price + rng.gauss(0.0, 0.11) + (0.5 - price) * 0.2
            lo, hi = min(price, close) - rng.uniform(0.01, 0.06), max(price, close) + rng.uniform(0.01, 0.06)
            x = int((i + 0.5) * step)
            color = up if close >= price else down
            strip.fill(color, pygame.Rect(x - vp.s(0.4), y_of(hi), max(1, vp.s(0.8)), max(1, y_of(lo) - y_of(hi))))
            top, bottom = y_of(max(price, close)), y_of(min(price, close))
            strip.fill(color, pygame.Rect(x - vp.s(2.8), top, vp.s(5.6), max(vp.s(1), bottom - top)))
            price = close
        return strip

    def _draw_rush_bg(self, surf: pygame.Surface, t: float) -> None:
        vp = self.vp
        surf.blit(self.bg_rush, (0, 0))
        rect = self.rush_rect
        clip = surf.get_clip()
        surf.set_clip(rect)
        x = -int(vp.sf(t * 11.0) % rect.w)
        surf.blit(self.candles, (rect.x + x, rect.y))
        surf.blit(self.candles, (rect.x + x + rect.w, rect.y))
        mid = rect.centery
        for amp_cm, speed, width_cm, color in ((52.0, 1.0, 0.9, theme.lerp_color(theme.DEEP, theme.CORAL, 0.55)),
                                               (34.0, 1.45, 0.5, theme.lerp_color(theme.DEEP, theme.WHITE, 0.2))):
            amp = vp.sf(amp_cm)
            pts = []
            for i in range(97):
                u = i / 96
                y = (0.55 * math.sin(8.0 * u + t * 1.5 * speed) + 0.3 * math.sin(21.0 * u - t * 2.3 * speed + 1.0)
                     + 0.15 * math.sin(53.0 * u + t * 3.7 * speed))
                pts.append((rect.x + int(u * rect.w), mid + int(amp * y)))
            pygame.draw.lines(surf, color, False, pts, max(1, vp.s(width_cm)))
        surf.set_clip(clip)

    def _badge_spot(self, o: Outcome, flow: F.Flow, now: float) -> tuple[float, float]:
        """Where the hit badge goes: where the ball hit, or the nearest place close by where its
        inner ring covers none of the other boxes (now and half a second on, as they move).
        Always inside the field."""
        r = BADGE_R + 3.0
        lo_x, hi_x = r, self.W - r
        lo_y, hi_y = self.hud_h + r + 4.0, self.footer_y - r
        clamp = lambda x, y: (min(max(x, lo_x), hi_x), min(max(y, lo_y), hi_y))  # noqa: E731
        others = []
        rnd = flow.round
        for tg in (rnd.targets if rnd is not None else []):
            for t in (now, now + 0.5):
                if tg.gone(t):
                    continue
                cx, cy = tg.center_at(t)
                if abs(cx - o.target_x) < 0.5 and abs(cy - o.target_y) < 0.5:
                    continue  # the box that was hit: it is going
                side = tg.side_at(t)
                others.append((cx, cy, side / 2.0, side * tg.aspect / 2.0))
        reach = BADGE_R + 2.0

        def covered(x: float, y: float) -> float:  # how far the ring reaches into the other boxes
            total = 0.0
            for cx, cy, hw, hh in others:
                d = math.hypot(max(abs(x - cx) - hw, 0.0), max(abs(y - cy) - hh, 0.0))
                if d < reach:
                    total += reach - d
            return total

        best, best_cost = clamp(o.x, o.y), None
        for dist in (0.0, 10.0, 20.0, 30.0, 40.0):
            for i in range(1 if dist == 0 else 12):
                a = math.radians(30.0 * i - 90.0)
                x, y = clamp(o.x + math.cos(a) * dist, o.y + math.sin(a) * dist)
                cost = covered(x, y) * 4.0 + math.hypot(x - o.x, y - o.y)
                if best_cost is None or cost < best_cost:
                    best, best_cost = (x, y), cost
        return best

    # -- events from the game -----------------------------------------------------------
    def on_event(self, name: str, data: dict, flow: F.Flow, now: float) -> None:
        fx = self.fx
        if name == "hit":
            o: Outcome = data["outcome"]
            storm = flow.round is not None and flow.round.rush is not None
            color = theme.CORAL if storm else theme.TURQ
            half = o.target_side / 2
            rings = self.cfg.ui.get("hit_style", "rings") != "shatter"
            if rings:  # MOFID_UI v11, 05 GAMEPLAY: rings open around the box, the product and its points
                if o.target_side:
                    fx.dot_ring(o.target_x, o.target_y, half * 0.8, half * 2.4, color, 0.6)
                bx, by = self._badge_spot(o, flow, now)
                tile = (o.target_x, o.target_y, o.target_side, o.target_side * o.target_aspect) if o.target_side else None
                fx.badge(bx, by, self.product_name(o.product), f"+{o.points}",
                         f"×{o.multiplier:g}" if o.multiplier > 1 else "", color, big=storm or o.tier == "tiny",
                         tile=tile, home=(self.W - 34.0, 37.0))  # then it flies into the HUD's score
            # or: the hit box breaks into four pieces that fly apart, and only its points float up
            self._shatters = self._shatters[-5:]
            if not rings:
                self._shatters.append({
                    "t0": now, "product": o.product, "x": o.target_x if o.target_side else o.x,
                    "y": o.target_y if o.target_side else o.y, "side": o.target_side or 20.0,
                    "points": f"+{o.points}", "mult": f"×{o.multiplier:g}" if o.multiplier > 1 else "",
                    "name": self.product_name(o.product), "color": color, "big": storm or o.tier == "tiny"})
            if o.tier == "tiny" and self.cfg.rush.enabled:  # the brand's symbols fly out: this one starts Market Rush
                fx.burst(o.x, o.y, color, count=10, speed=(50.0, 110.0), life=0.9, start_r=half)
            if not rings:
                self.score_pop_t = now  # the score counts it at once
            if o.level_up:
                self.badge_pop_t = now
                if self.cfg.ui.combo_banner and flow.round is not None:
                    fx.banner(self.T.combo_up.format(m=f"×{flow.round.next_multiplier:g}"), theme.TURQ, 1.3)
        elif name == "miss":
            o = data["outcome"]
            if o.x >= 0:
                fx.ripple(o.x, o.y, 2, 17, 1.0, theme.MIST, 0.45)
                fx.ripple(o.x, o.y, 1, 7, 1.4, theme.BLUE_SOFT, 0.3)
            if o.combo_broken:
                self.badge_shake_t = now
        elif name == "rush_start":
            self.rush_title_t = now
            self.badge_pop_t = now
            fx.banner(self.T.rush_start.format(m=f"×{data['multiplier']:g}"), theme.CORAL, 2.2)
        elif name == "rush_again":
            self.badge_pop_t = now
            fx.banner(self.T.rush_start.format(m=f"×{data['multiplier']:g}"), theme.CORAL, 1.4)
        elif name == "rush_end":
            if data["reason"] == "time":
                fx.banner(self.T.rush_end, theme.TURQ, 1.6)
        elif name == "start_hit":
            sb = self.cfg.layout.start_button
            fx.ripple(sb.x_cm + layout_left(self.cfg), sb.y_cm + layout_top(self.cfg), sb.size_cm / 2, sb.size_cm / 2 + 40, 2.5, theme.TURQ, 0.6)
        elif name == "end":
            self.end_t0 = now
        elif name == "go" and flow.round is not None:
            self._leaving.clear()
            self._shatters.clear()
        elif name == "results":
            self.results_t0 = now
        elif name == "state":
            if data["state"] in (F.ATTRACT, F.RESULTS):
                fx.clear()
                self._shatters.clear()
            # Screens cross-fade; the steps of a round (countdown to play to end) share one screen.
            if data["state"] not in (F.PLAYING, F.ENDING):
                self._fade_pending = True

    # -- frame ----------------------------------------------------------------------------
    def draw(self, surf: pygame.Surface, flow: F.Flow, now: float, overlays: dict) -> None:
        if self.boot_t0 is not None and now - self.boot_t0 >= self.cfg.flow.intro_s:
            self.boot_t0 = None  # the logo has gone: cross-fade to the screen under it
            self._fade_pending = True
        if self._fade_pending:  # the display still holds the last frame of the previous screen
            self.fade_src.blit(surf, (0, 0))
            self._fade_pending = False
            self._fade_t0 = now
        st = flow.state
        if self.boot_t0 is not None:
            self._intro_overlay(surf, now - self.boot_t0, self.cfg.flow.intro_s)
        elif st == F.ATTRACT:
            with self.menu():
                self.draw_attract(surf, flow, now, overlays)
        elif st == F.INSTRUCTIONS:
            with self.menu():
                self.draw_instructions(surf, flow, now)
        elif st == F.INTRO:
            self.draw_intro(surf, flow, now)
        elif st == F.REGISTER:
            with self.menu():
                self.draw_register(surf, flow, now)
        elif st in (F.COUNTDOWN, F.PLAYING, F.ENDING):
            self.draw_play(surf, flow, now)
        elif st == F.RESULTS:
            with self.menu():
                self.draw_results(surf, flow, now)
        p = (now - self._fade_t0) / 0.35
        if 0.0 <= p < 1.0:
            self.fade_src.set_alpha(int(255 * (1.0 - p)))
            surf.blit(self.fade_src, (0, 0))
        if overlays.get("paused"):
            self._center_overlay(surf, self.T.paused, None)
        self._badges(surf, overlays)

    # -- gameplay ----------------------------------------------------------------------------
    def draw_play(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        vp = self.vp
        rnd = flow.round
        # When the round ends, everything on the field stops at that moment.
        t = min(now, rnd.finished_at) if rnd is not None and rnd.finished_at is not None else now
        storm = rnd.rush_intensity(t) if rnd is not None else 0.0
        if storm <= 0.0:
            surf.blit(self.bg_field, (0, 0))
        elif storm >= 1.0:
            self._draw_rush_bg(surf, t)
        else:
            surf.blit(self.bg_field, (0, 0))
            self._draw_rush_bg(self.rush_layer, t)
            self.rush_layer.set_alpha(int(255 * storm))
            surf.blit(self.rush_layer, (0, 0))
        bg = theme.lerp_color(theme.FIELD, theme.DEEP, storm)
        st = flow.state
        if rnd is not None and rnd.started_at is not None:
            fade = 1.0 - _ease_in_out((now - self.end_t0) / 0.4) if st == F.ENDING else 1.0
            self.fx.draw_under(surf, vp, bg)
            for tg in rnd.targets:
                if fade > 0.0:
                    self._draw_target(surf, tg, t, bg, storm, fade)
            self._shatters = [h for h in self._shatters if now - h["t0"] < self.HIT_FX_S]
            for h in self._shatters:
                self.draw_hit_fx(surf, h, now - h["t0"], bg)
        field_rect = vp.cm_rect(0, self.hud_h, self.W, self.footer_y)
        self.fx.draw(surf, vp, field_rect, bg)
        self._hud(surf, flow, t)
        self.fx.draw_banner(surf, vp, self.hud_h + 9)
        if st == F.PLAYING and now - self.rush_title_t < 1.4:
            p = (now - self.rush_title_t) / 1.4
            self.text.blit(surf, self.T.rush_title, "title", vp.s(30), theme.CORAL, vp.px(self.W / 2, 205),
                           alpha=int(235 * (1.0 - p * p)))
        if st == F.COUNTDOWN:
            self._countdown(surf, flow, now)
        elif st == F.PLAYING and flow.elapsed(now) < 0.9:
            p = flow.elapsed(now) / 0.9
            img = self.text.render(self.T.go, "title", vp.s(34), theme.TURQ)
            k = 0.75 + 0.35 * _ease_out(p * 2.5)
            img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
            img.set_alpha(int(255 * (1 - p**3)))
            surf.blit(img, img.get_rect(center=vp.px(self.W / 2, 205)))
            w = 40 * _ease_out(p * 2)  # the turquoise line under it (MOFID_UI v4.1, 05 GO)
            surf.fill(theme.TURQ, vp.cm_rect(self.W / 2 - w, 228, self.W / 2 + w, 228.6))
        elif st == F.ENDING:
            self._ending(surf, rnd, now - self.end_t0)

    def _draw_target(self, surf: pygame.Surface, tg, t: float, bg, storm: float, alpha: float = 1.0) -> None:
        """One product tile with its thin ring, its points under it, and corner ticks while it
        appears (and always around a tiny box: the one that starts Market Rush)."""
        vp = self.vp
        age = t - tg.born
        if age < 0.0:
            return  # a new box that appears a moment after the hit (layout.spawn_delay_s)
        side = tg.side_at(t)
        if side < 0.5:
            return
        cx, cy = tg.center_at(t)  # while it drops in, a little above its place (the hit test too)
        c = vp.px(cx, cy)
        landed = max(0.05, tg.spawn_dur)
        a = alpha * min(1.0, age / 0.1)  # it fades in at the top of its drop, in full colour and size
        if a >= 1.0 and age >= landed:
            self._rest_halo(surf, tg, side, c, age, bg)
        spr = self.sprites.product_turned(tg.product, vp.sf(side), vp.s(tg.size), self._turn(tg, age), ring=True)
        if a < 1.0:
            spr.set_alpha(int(255 * a))
        surf.blit(spr, spr.get_rect(center=c))
        spr.set_alpha(255)
        h = side * tg.aspect
        grow = min(1.0, max(0.0, (age - landed) / 0.25))
        if grow > 0.0 and alpha >= 1.0:  # its points under it, in mist, once it has landed
            pts = points_of(self.cfg.raw(), tg.product, tg.tier)
            self.text.blit(surf, f"+{pts}", "numbers", vp.s(4.6), theme.MIST, vp.px(cx, cy + h / 2 + 6.5),
                           alpha=int(255 * grow))
        # A new box: four white corner marks close in on the place where it lands, then fade
        # (they stay faint on tiny boxes, the ones that start Market Rush)
        spawn = 1.0 - min(1.0, max(0.0, (age - landed) / self.TICKS_FADE_S))
        if spawn > 0.0 or tg.tier == "tiny":
            close = _ease_out(min(1.0, age / landed))
            inset = 3.0 + 14.0 * (1.0 - close)
            strength = max(spawn, 0.55 if tg.tier == "tiny" else 0.0) * alpha * min(1.0, age / 0.08)
            col = theme.lerp_color(bg, theme.CORAL if storm > 0.5 else theme.WHITE, strength)
            lx, ly = cx, cy + tg._drop_offset(t)  # its place, not where it is in the drop
            self._ticks(surf, lx, ly, tg.size / 2 + inset, tg.size * tg.aspect / 2 + inset,
                        min(7.0, tg.size * 0.2), col, 0.75)

    SHOW_S = 0.9  # s, after this a resting tile's halo may open now and then
    TICKS_FADE_S = 0.5  # s, a new box's corner marks fade after it has landed
    SHATTER_S = 0.45  # s, a hit box breaking into four pieces that fly apart
    HIT_FX_S = 1.0  # s, the hit's points floating up (the shatter is the first part)
    # A resting tile, besides its slow float (motion): it turns a little now and then, as under the
    # clip's slowly moving camera, and a thin halo opens around it and fades every so often.
    TURN_DEPTH = 0.07  # the narrowest it gets: 93 % of its width
    TURN_S = 6.5  # s, one slow turn and back (each tile a little different)
    HALO_EVERY = 5.5  # s, between two halos of one tile (each tile at its own moment)
    HALO_S = 1.0  # s, one halo opening and fading

    def _turn(self, tg, age: float) -> float:
        """How much of its width a resting tile shows: 1 facing us, down to 1 - TURN_DEPTH."""
        period = self.TURN_S * (1.0 + 0.11 * ((tg.id * 7) % 5 - 2))
        k = 0.5 - 0.5 * math.cos(math.tau * (age / period + 0.37 * tg.id))
        return 1.0 - self.TURN_DEPTH * k

    def _rest_halo(self, surf, tg, side: float, c: tuple[int, int], age: float, bg) -> None:
        """Now and then a thin ring opens around a resting tile and fades (the clip's icon halo)."""
        ph = (age - self.SHOW_S + 1.9 * tg.id) % self.HALO_EVERY
        if age < self.SHOW_S + 1.0 or ph >= self.HALO_S:
            return
        q = ph / self.HALO_S
        r = side * 0.62 + 7.0 * _ease_out(q)
        col = theme.lerp_color(bg, theme.MIST, 0.55 * (1.0 - q) ** 1.3)
        pygame.draw.aacircle(surf, col, c, int(self.vp.sf(r)), max(1, self.vp.s(0.3)))

    def draw_hit_fx(self, surf, h: dict, age: float, bg) -> None:
        """A box that was hit, ``age`` s ago: it breaks into four pieces that fly apart, spin and
        fade, and its points float up where it was. h: product, x, y (cm), side (cm), points,
        mult, name, color, big. Also the attract screen's demonstration."""
        vp = self.vp
        if age < 0.0:
            return
        if age < self.SHATTER_S:
            q = age / self.SHATTER_S
            e = _ease_out(q)
            spr = self.sprites.product(h["product"], vp.s(h["side"]))
            w, hh = spr.get_size()
            cx, cy = vp.px(h["x"], h["y"])
            reach = vp.sf(h["side"]) * 0.75 * e  # how far the pieces have flown
            fall = vp.sf(h["side"]) * 0.5 * q * q  # and dropped
            alpha = int(255 * (1.0 - q) ** 1.2)
            for k, (sx, sy) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1))):
                piece = spr.subsurface(pygame.Rect(0 if sx < 0 else w // 2, 0 if sy < 0 else hh // 2,
                                                   w // 2 if sx < 0 else w - w // 2,
                                                   hh // 2 if sy < 0 else hh - hh // 2))
                img = pygame.transform.rotozoom(piece, sx * sy * -(35 + 12 * k) * e, 1.0 - 0.35 * q)
                img.set_alpha(alpha)
                px = cx + sx * (w / 4 + reach)
                py = cy + sy * (hh / 4 + reach * 0.8) + fall
                surf.blit(img, img.get_rect(center=(int(px), int(py))))
            if q < 0.35:  # a short white flash on the cracks as it breaks
                f = 1.0 - q / 0.35
                col = theme.lerp_color(bg, theme.WHITE, f)
                lw = max(1, vp.s(0.8))
                pygame.draw.line(surf, col, (cx - w // 2 - reach, cy), (cx + w // 2 + reach, cy), lw)
                pygame.draw.line(surf, col, (cx, cy - hh // 2 - reach), (cx, cy + hh // 2 + reach), lw)
        # the points (and the multiplier) float up where the box was, the product's name under them
        p = age / self.HIT_FX_S
        if p < 1.0:
            rise = 16.0 * _ease_out(p)
            fade = 1.0 if p < 0.6 else 1.0 - (p - 0.6) / 0.4
            pop = 0.7 + 0.3 * _ease_back(min(1.0, age / 0.2))
            x, y = h["x"], h["y"] - rise
            big = 15.0 if h.get("big") else 12.0
            img = self.text.render(h["points"], "numbers", vp.s(big * pop), h["color"])
            img.set_alpha(int(255 * fade))
            surf.blit(img, img.get_rect(center=vp.px(x, y)))
            img.set_alpha(255)
            if h.get("mult"):
                self.text.blit(surf, h["mult"], "numbers", vp.s(7 * pop), theme.WHITE,
                               vp.px(x + big * 0.9 + 4, y - 2), "midleft", alpha=int(255 * fade))
            if h.get("name"):
                self.text.blit(surf, h["name"], "heading", vp.s(4.6), theme.MIST, vp.px(x, y + big * 0.62 + 2),
                               alpha=int(230 * fade))

    def _draw_showing(self, surf, tg, side: float, c: tuple[int, int], p: float) -> None:
        """A new tile appearing, p 0..1 (the reference clip's icon): born small as a thin sliver
        that turns to face us as a circle, it morphs into the tile's rounded square while it keeps
        growing, then its artwork fades in. side: its full width, cm."""
        vp = self.vp
        d = side * (0.06 + 0.94 * (1.0 - (1.0 - p) ** 3))  # small at its birth, bigger over time
        turn = 0.15 + 0.85 * _ease_out(min(1.0, p / 0.4))  # the sliver turning to face us
        m = _ease_in_out(min(1.0, max(0.0, (p - 0.35) / 0.3)))  # circle -> the tile's shape
        w, h = d * turn, d * (1.0 + (tg.aspect - 1.0) * m)
        radius = min(w, h) / 2.0 * (1.0 - m) + d * PRODUCT_RADIUS * m
        rect = pygame.Rect(0, 0, max(2, vp.s(w)), max(2, vp.s(h)))
        rect.center = c
        art = _ease_in_out(min(1.0, max(0.0, (p - 0.5) / 0.35)))  # the artwork fades in last
        if art < 1.0:
            color = self.sprites.product_color(tg.product)
            r_px = max(1, int(vp.sf(radius)))
            pygame.draw.rect(surf, color, rect, border_radius=r_px)
            pygame.draw.rect(surf, theme.lerp_color(color, theme.WHITE, 0.55), rect, width=max(1, vp.s(0.5)), border_radius=r_px)
        if art > 0.0:
            spr = self.sprites.product_scaled(tg.product, vp.sf(d), vp.s(tg.size), ring=True)
            spr.set_alpha(int(255 * art))
            surf.blit(spr, spr.get_rect(center=c))
            spr.set_alpha(255)

    def _ticks(self, surf, cx: float, cy: float, hw: float, hh: float, arm: float, col, width_cm: float = 0.55) -> None:
        """The design's corner ticks: an L at each corner of a box (cm)."""
        vp = self.vp
        w = max(1, vp.s(width_cm))
        for sx in (-1, 1):
            for sy in (-1, 1):
                x, y = cx + sx * hw, cy + sy * hh
                pygame.draw.lines(surf, col, False, [vp.px(x - sx * arm, y), vp.px(x, y), vp.px(x, y - sy * arm)], w)

    PILE = 13  # at most this many tiles fall into place when the round ends

    def _ending(self, surf: pygame.Surface, rnd, t: float) -> None:
        """MOFID_UI v11, 06 ENDING: «پایان» big, a thin line, and every product falls into place
        in two neat rows under it."""
        vp, W = self.vp, self.W
        p = min(1.0, t / 0.35)
        self._dim_field(surf, int(170 * p))
        k = 0.85 + 0.15 * _ease_back(min(1.0, t / 0.35))
        img = self.text.render(self.T.end_title, "title", vp.s(48), theme.WHITE)
        img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
        surf.blit(img, img.get_rect(center=vp.px(W / 2, 172)))
        half = 90 * _ease_out(min(1.0, t / 0.5))
        surf.fill(theme.lerp_color(theme.FIELD, theme.TURQ, 0.6), vp.cm_rect(W / 2 - half, 221, W / 2 + half, 221.5))
        products = self.cfg.raw()["products"][: self.PILE]
        n = len(products)
        per_row = (n + 1) // 2
        tile_w, step = 38.0, 48.0
        for i, prod in enumerate(products):
            row, col = divmod(i, per_row)
            in_row = per_row if row == 0 else n - per_row
            x = W / 2 + ((in_row - 1) / 2 - col) * step  # from the right, as Persian is read
            y_end = 293 + row * 48
            delay = 0.05 * i
            q = min(1.0, max(0.0, (t - delay) / 0.55))
            if q <= 0.0:
                continue
            fall = q * q  # gravity, then a small bounce as it lands
            y = -30 + (y_end + 30) * fall - (math.sin(min(1.0, (t - delay - 0.55) / 0.25) * math.pi) * 3
                                             if t - delay > 0.55 else 0)
            tile = self.sprites.product(prod["key"], vp.s(tile_w))
            surf.blit(tile, tile.get_rect(center=vp.px(x, y)))

    def _roll_score(self, score: int, now: float) -> int:
        """The HUD's score, rolling up to ``score``: most of the way in about 0.3 s, always at
        least one point a frame, and at once when the score goes down (a new round)."""
        dt = 0.0 if self._score_t is None else min(0.1, max(0.0, now - self._score_t))
        self._score_t = now
        if score <= self._score_shown or dt == 0.0 and self._score_shown == 0.0 and score == 0:
            self._score_shown = float(score)
        else:
            gap = score - self._score_shown
            self._score_shown += max(min(gap, 1.0), gap * (1.0 - math.exp(-dt / 0.11)))
        return int(round(self._score_shown))

    def _player_name(self, flow: F.Flow) -> str:
        name = _short((flow.round_player or {}).get("name") or "", self.cfg.flow.name_max_chars)
        return name if self.cfg.results.show_names else ""

    def _dim_field(self, surf, alpha: int) -> None:
        vp = self.vp
        area = vp.cm_rect(0, self.hud_h, self.W, self.footer_y)
        self.dim.set_alpha(alpha)
        surf.blit(self.dim, area.topleft, pygame.Rect(0, 0, area.w, area.h))

    def _hud(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        vp = self.vp
        rnd = flow.round
        if rnd is None:
            return
        W = self.W
        mid = 37.0
        # MOFID_UI v4.1 HUD: type only, no band or pills. Score on the right (read first),
        # seconds on the left, balls and the multiplier in the middle, the timeline under them.
        self.text.blit(surf, self.T.score, "body", vp.s(5.6), theme.MIST, vp.px(W - 16, 9), "topright")
        rush = rnd.rush_at(now)
        # the score rolls up to its new value (soft, never behind by more than a moment) and
        # swells gently as the points arrive
        # points still flying in from a hit are counted when they land
        flying = sum(b.value for b in self.fx.badges if b.home is not None)
        if flying < self._flying:
            self.score_pop_t = now  # one has just landed
        self._flying = flying
        shown = self._roll_score(max(0, rnd.score - flying), now)
        p = (now - self.score_pop_t) / 0.5
        w = math.sin(math.pi * p) if 0.0 < p < 1.0 else 0.0  # 0 -> 1 -> 0, smooth both ways
        k = 1.0 + (0.14 if rush else 0.09) * w
        col = theme.lerp_color(theme.WHITE, theme.CORAL if rush else theme.TURQ, 0.75 * w)
        img = self.text.render(str(shown), "numbers", vp.s(29), col, cache=shown == rnd.score and w == 0.0)
        if k > 1.001:
            img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
        surf.blit(img, img.get_rect(midright=vp.px(W - 16, mid)))
        # time (left); coral in the last seconds
        if rnd.timed:
            left = rnd.time_left(now)
            secs = math.ceil(left) if rnd.started_at is not None else int(rnd.duration)
            warn = rnd.started_at is not None and 0 < secs <= self.cfg.flow.time_warning_s and not rnd.finished
            label, big, frac, ticks = self.T.time, secs, left / rnd.duration, max(1, int(round(rnd.duration / 10)))
        else:  # no time limit (round.time_limit off): the balls left take the corner and the timeline
            big = rnd.throws_left
            warn = 0 < big <= 3 and not rnd.finished
            label, frac, ticks = self.T.balls, big / max(1, rnd.balls), min(rnd.balls, 30)
        self.text.blit(surf, label, "body", vp.s(5.6), theme.MIST, vp.px(16, 9), "topleft")
        self.text.blit(surf, str(big), "numbers", vp.s(29), theme.CORAL if warn else theme.WHITE, vp.px(16, mid), "midleft")
        # balls (only with a ball limit and a time limit): filled dots still to throw, rings for the used ones
        n = rnd.balls if rnd.timed else 0
        if n:
            step = min(7.6, 120.0 / max(1, n))
            d = min(4.4, step * 0.66)
            x0 = W / 2 + (n - 1) * step / 2
            used = rnd.throws
            for i in range(n):
                cx, cy = vp.px(x0 - i * step, 12)
                if i < n - used:
                    dot = self.sprites.dot(vp.sf(d / 2), theme.WHITE)
                    surf.blit(dot, dot.get_rect(center=(cx, cy)))
                else:
                    pygame.draw.aacircle(surf, theme.MIST, (cx, cy), int(vp.sf(d / 2)), max(1, vp.s(0.35)))
        # the multiplier: mist at ×1, turquoise above (coral while Market Rush runs)
        pop = max(0.0, 1.0 - (now - self.badge_pop_t) / 0.35)
        if rnd.levels or rush is not None:
            m = rnd.next_multiplier
            if rush is not None and self.cfg.rush.stack_with_combo:
                m *= float(self.cfg.rush.score_multiplier)
            elif rush is not None:
                m = max(m, float(self.cfg.rush.score_multiplier))
            shake = max(0.0, 1.0 - (now - self.badge_shake_t) / 0.4)
            x = W / 2 + 3 * shake * math.sin((now - self.badge_shake_t) * 50)
            self.text.blit(surf, self.T.multiplier, "body", vp.s(4.6), theme.MIST, vp.px(W / 2, 22))
            col = theme.CORAL if rush is not None else (theme.TURQ if m > 1 else theme.MIST)
            self.text.blit(surf, f"×{m:g}", "numbers", vp.s(13 * (1 + 0.18 * pop)), col, vp.px(x, 38))
        # the timeline: shrinks from the left; a tick every ten seconds
        y = self.hud_h - 6
        x0, x1 = 16.0, W - 16
        track = theme.lerp_color(theme.FIELD, theme.MIST, 0.3)
        pygame.draw.line(surf, track, vp.px(x0, y), vp.px(x1, y), max(1, vp.s(0.45)))
        if frac > 0:
            pygame.draw.line(surf, theme.CORAL if warn else theme.TURQ, vp.px(x1 - (x1 - x0) * frac, y), vp.px(x1, y),
                             max(1, vp.s(0.7)))
        for i in range(ticks + 1):
            tx = x0 + (x1 - x0) * i / ticks
            pygame.draw.line(surf, track, vp.px(tx, y - 1.4), vp.px(tx, y + 1.4), max(1, vp.s(0.35)))
        if rush is not None:  # Market Rush: what is left of it, and its seconds
            w = max(0.0, (rush.ends_at - now) / max(0.1, float(self.cfg.rush.duration_s)))
            pygame.draw.line(surf, theme.CORAL, vp.px(x1 - (x1 - x0) * min(1.0, w), y + 2.6), vp.px(x1, y + 2.6),
                             max(1, vp.s(0.6)))
            self.text.blit(surf, self.T.rush_hud.format(seconds=math.ceil(rush.ends_at - now)), "heading",
                           vp.s(6 * (1 + 0.12 * pop)), theme.CORAL, vp.px(W / 2, self.hud_h + 19))

    def _countdown(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        """MOFID_UI v4.1, 04 COUNTDOWN: "get ready", the number in a ring whose arc runs down,
        and the products waiting along the bottom."""
        vp, W = self.vp, self.W
        n = flow.countdown_value(now)
        total = max(0.1, float(self.cfg.flow.countdown_s))
        frac = 1.0 - ((total - flow.elapsed(now)) % 1.0)
        center = vp.px(W / 2, 212)
        r = vp.sf(68)
        pygame.draw.aacircle(surf, theme.lerp_color(theme.FIELD, theme.MIST, 0.18), center, int(r), max(1, vp.s(0.9)))
        left = max(0.0, 1.0 - flow.elapsed(now) / total)
        if left > 0:  # the arc of the time still to wait, from the top, clockwise
            rect = pygame.Rect(0, 0, int(2 * r), int(2 * r))
            rect.center = center
            pygame.draw.arc(surf, theme.TURQ, rect, math.pi / 2, math.pi / 2 + math.tau * left, max(1, vp.s(1.1)))
        self.text.blit(surf, self.T.get_ready, "title", vp.s(12), theme.TURQ, vp.px(W / 2, 112))
        img = self.text.render(str(n), "numbers", vp.s(80), theme.WHITE)
        k = 1.2 - 0.2 * _ease_out(frac * 3)
        img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
        surf.blit(img, img.get_rect(center=center))
        self._tile_strip(surf, 338, 14.0, now)
        self._micro(surf, self.T.loading_market, vp.px(W / 2, 354), "center", theme.MIST, 2.8)

    def _tile_strip(self, surf, y: float, w_cm: float, now: float, gap: float = 3.0) -> None:
        """All the products in a row across the screen at y (cm), each a little afloat."""
        vp = self.vp
        products = self.cfg.raw()["products"]
        n = len(products)
        w_cm = min(w_cm, (self.W - 24) / n - gap)
        x = self.W / 2 + (n - 1) * (w_cm + gap) / 2
        for i, p in enumerate(products):
            tile = self.sprites.product(p["key"], vp.s(w_cm))
            surf.blit(tile, tile.get_rect(center=vp.px(x - i * (w_cm + gap), y + 1.2 * math.sin(now * 2.0 + i * 0.7))))

    # -- Mofid logo intro -------------------------------------------------------------------
    # Before every countdown (flow state INTRO) and once as the game opens. The logo, in its
    # brand colours, settles in and holds while the brand symbols gather around it; then it zooms
    # in towards the players and fades, and the symbols fly out with it. The screen cross-fade
    # then uncovers the countdown (or the attract screen). Flat shapes and eased movement only,
    # no flashing.
    INTRO_LOGO_CM = 150  # the logo's width while it holds: half the wall
    INTRO_ZOOM = 3.2  # how much larger it is at the end, long after it has faded
    INTRO_MARKS = (  # (symbol, angle in degrees, distance in cm, colour); clear of the logo
        ("plus", -150, 120, "TURQ"), ("dot", -110, 91, "BLUE_SOFT"), ("times", -62, 96, "MIST"),
        ("slash", -24, 125, "TURQ"), ("minus", 16, 122, "BLUE_SOFT"), ("plus", 58, 99, "MIST"),
        ("dot", 102, 88, "TURQ"), ("times", 142, 109, "BLUE_SOFT"), ("minus", 178, 127, "MIST"),
        ("slash", -84, 135, "TURQ"),
    )

    def _build_intro(self) -> None:
        vp = self.vp
        self.bg_intro = self._wall(theme.FIELD)  # the same field as every screen (MOFID_UI v4.1)
        dot_grid(self.bg_intro, self.sprites, vp.rect.inflate(-vp.s(8), -vp.s(8)), vp.sf(12.5), vp.sf(0.5),
                 theme.lerp_color(theme.FIELD, theme.MIST, 0.07))
        # The artwork at a few sizes, each drawn from the SVG: every frame scales the nearest one
        # by less than a quarter, so the logo stays sharp and the zoom costs little.
        self.intro_logos: list[tuple[float, pygame.Surface]] = []
        k = 1.06
        while True:
            img = self._logo("fa_s_white", width_cm=self.INTRO_LOGO_CM * k)
            if img is None:
                break
            self.intro_logos.append((k, img))
            if k >= self.INTRO_ZOOM:
                break
            k = min(self.INTRO_ZOOM, k * 1.25)
        for kind, _, _, color in self.INTRO_MARKS:  # ready before the first frame
            self.sprites.symbol(kind, vp.s(9), getattr(theme, color))

    def _intro_timing(self, d: float) -> tuple[float, float]:
        """(end of the entrance, start of the exit) for an intro of d seconds."""
        appear = min(0.6, d * 0.25)
        return appear, max(appear, d - 1.1)

    def start_boot(self, now: float) -> None:
        """Play the intro over the attract screen as the game opens; the logo is already
        on screen from draw_loading, so it starts where the entrance ends."""
        self.boot_t0 = now - self._intro_timing(self.cfg.flow.intro_s)[0]

    def draw_loading(self, surf: pygame.Surface) -> None:
        """The logo at rest, shown while the game is still loading (camera models, inputs)."""
        d = self.cfg.flow.intro_s
        self._intro_overlay(surf, self._intro_timing(d)[0], d)

    def draw_intro(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        self._intro_overlay(surf, flow.elapsed(now), self.cfg.flow.intro_s)

    def _intro_overlay(self, surf: pygame.Surface, t: float, d: float) -> None:
        vp, W = self.vp, self.W
        appear, leave = self._intro_timing(d)
        go = _phase(t, leave, d - leave)  # 0..1 while the logo leaves
        surf.blit(self.bg_intro, (0, 0))
        cy = self.H / 2 - 8
        # the symbols gather around the logo, drift out a little while it holds, then fly away
        hold = _phase(t, appear, leave - appear) if leave > appear else 1.0
        for i, (kind, ang, dist, color) in enumerate(self.INTRO_MARKS):
            show = _ease_out(_phase(t, appear * (0.1 + 0.05 * i), appear * 0.4))
            alpha = show * (1.0 - _ease_in_out(go / 0.6))
            if alpha <= 0.0:
                continue
            r = dist * (0.82 + 0.18 * show + 0.05 * hold + 1.2 * go * go)
            a = math.radians(ang)
            sym = self.sprites.symbol(kind, vp.s(9), getattr(theme, color))
            sym.set_alpha(int(255 * alpha))
            surf.blit(sym, sym.get_rect(center=vp.px(W / 2 + math.cos(a) * r, cy + math.sin(a) * r)))
            sym.set_alpha(255)
        come = _ease_out(_phase(t, 0.0, appear))
        if go <= 0.0:  # settles in, then pushes in slowly while it holds
            k, alpha = 0.86 + 0.14 * come + 0.04 * hold, come
        else:  # zooms in towards the players, faster and faster, and fades away
            k = 1.04 + (self.INTRO_ZOOM - 1.04) * go * go
            alpha = 1.0 - _ease_in_out((go - 0.15) / 0.75)
        if alpha <= 0.0:
            return
        center = vp.px(W / 2, cy)
        if not self.intro_logos:  # no logo artwork: the brand name, the same way
            self.text.blit(surf, BRAND_NAME, "heading", vp.s(18 * k), theme.WHITE, center,
                           alpha=int(255 * alpha))
            return
        base, src = next(((b, img) for b, img in self.intro_logos if b >= k), self.intro_logos[-1])
        f = k / base
        w, h = src.get_width() * f, src.get_height() * f
        x0, y0 = center[0] - w / 2, center[1] - h / 2
        # only the part that lands on the screen is scaled, so a logo larger than the wall costs no more
        view = surf.get_rect().clip(pygame.Rect(int(x0), int(y0), int(w) + 1, int(h) + 1))
        part = pygame.Rect(int((view.x - x0) / f), int((view.y - y0) / f), int(view.w / f) + 1,
                           int(view.h / f) + 1).clip(src.get_rect())
        if view.w < 1 or view.h < 1 or part.w < 1 or part.h < 1:
            return
        # smooth while it is still; while it moves fast a plain scale cannot be told apart, at a third of the cost
        img = (pygame.transform.smoothscale if go <= 0.0 else pygame.transform.scale)(src.subsurface(part), view.size)
        img.set_alpha(int(255 * alpha))
        surf.blit(img, view.topleft)

    # -- attract ----------------------------------------------------------------------------
    def draw_attract(self, surf: pygame.Surface, flow: F.Flow, now: float, overlays: dict) -> None:
        """The idle loop (ui/attract.py): the circle of tiles, a round that plays itself, today's best."""
        self.attract.draw(surf, flow, now, overlays)

    def _steady_progress(self, surf: pygame.Surface, c1, c2) -> None:
        """Letter-spaced brand line from the pattern graphic (page 22)."""
        vp = self.vp
        a = self.text.render(_letterspaced("STEADY"), "latin", vp.s(4.2), c1, localize=False)
        b = self.text.render(_letterspaced("PROGRESS"), "latin", vp.s(4.2), c2, localize=False)
        gap = vp.s(3)
        total = a.get_width() + gap + b.get_width()
        x = vp.px(self.W / 2, 0)[0] - total // 2
        y = vp.px(0, self.H - 13)[1]
        surf.blit(a, a.get_rect(midleft=(x, y)))
        surf.blit(b, b.get_rect(midleft=(x + a.get_width() + gap, y)))

    # -- registration --------------------------------------------------------------------------
    def draw_register(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        vp, W, T = self.vp, self.W, self.T
        surf.blit(self.bg_dark, (0, 0))
        self._blit_logo(surf, "fa_s_white", vp.px(W / 2, 44), theme.WHITE)
        self.text.blit(surf, T.title, "title", vp.s(30), theme.WHITE, vp.px(W / 2, 90))

        card = vp.cm_rect(22, 112, 278, 226)
        surf.blit(self._art("panel.attract_card", card.w, card.h, lambda: self.sprites.rounded(card.w, card.h, vp.s(5), theme.WHITE)),
                  card.topleft)
        self.text.blit(surf, T.register_title, "heading", vp.s(12), theme.DARK_BLUE, vp.px(W / 2, 128))
        self.text.blit(surf, T.register_prompt, "body", vp.s(7), theme.TEXT_GREY, vp.px(W / 2, 145))

        err_at = flow.register_error_at
        shake = 0.0
        if err_at is not None:
            age = now - err_at
            shake = 3.0 * max(0.0, 1.0 - age / 0.4) * math.sin(age * 50)
        fw, fh = vp.s(220), vp.s(34)
        fc = vp.px(W / 2 + shake, 174)
        edge = theme.RED if err_at is not None else theme.TURQUOISE
        fill = self.sprites.rounded(fw, fh, vp.s(5), theme.TURQUOISE_PALE)
        surf.blit(fill, fill.get_rect(center=fc))
        outline = self.sprites.rounded(fw, fh, vp.s(5), edge, border=max(1, vp.s(0.7)))
        surf.blit(outline, outline.get_rect(center=fc))

        name = flow.name_input
        blink = int(now * 2.0) % 2 == 0
        if name:
            size = 14.0
            while size > 7.0 and self.text.width(name, "heading", vp.s(size)) > fw - vp.s(14):
                size -= 1.0
            rect = self.text.blit(surf, name, "heading", vp.s(size), theme.DARK_BLUE, fc)
            rtl = split_runs(self.text.localize(name))[-1][1]
            caret_x = rect.left - vp.s(1.5) if rtl else rect.right + vp.s(1.5)
        else:
            self.text.blit(surf, T.register_placeholder, "body", vp.s(10), theme.GREY, fc)
            caret_x = fc[0] + vp.s(22)
        if blink:
            surf.fill(theme.DARK_BLUE, pygame.Rect(caret_x, fc[1] - vp.s(8), max(1, vp.s(0.7)), vp.s(16)))
        if err_at is not None:
            self._pill(surf, T.register_empty, theme.RED, theme.WHITE, (W / 2, 209), 6.5)

        sb = self.cfg.layout.start_button
        ready = F.name_is_valid(name)
        px = vp.s(sb.size_cm)
        if ready:
            box = self._art("button.start", px, px, lambda: self.sprites.rounded(px, px, vp.s(sb.size_cm * 0.18), theme.TURQUOISE))
        else:
            box = self.sprites.rounded(px, px, vp.s(sb.size_cm * 0.18), theme.BLUE_MID)
        surf.blit(box, box.get_rect(center=vp.px(sb.x_cm, sb.y_cm)))
        self.text.blit(surf, T.register_start, "title", vp.s(14), theme.WHITE if ready else theme.BLUE_PALE, vp.px(sb.x_cm, sb.y_cm))
        self.text.blit(surf, T.register_hint, "body", vp.s(6.5), theme.WHITE, vp.px(W / 2, sb.y_cm + sb.size_cm / 2 + 14))
        self._steady_progress(surf, theme.TURQUOISE, theme.WHITE)
        self.fx.draw(surf, vp, vp.rect, theme.DARK_BLUE)

    # -- instructions --------------------------------------------------------------------------
    def draw_instructions(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        """MOFID_UI v11, 02 INSTRUCTIONS: the logo, «راهنمای بازی», the seconds and the balls big,
        the three sizes with their points, and the two rules."""
        vp, W, T, cfg = self.vp, self.W, self.T, self.cfg
        el = flow.elapsed(now)
        surf.blit(self.bg_dark, (0, 0))
        self._blit_logo(surf, "fa_s_white_small", vp.px(W / 2, 26), theme.WHITE)
        self.text.blit(surf, T.how_to_title, "title", vp.s(22), theme.WHITE, vp.px(W - 16, 66), "midright")
        self.text.blit(surf, T.how_to_sub, "heading", vp.s(7.5), theme.TURQ, vp.px(W - 16, 86), "midright")
        rd = cfg.round
        balls = int(rd.balls) if rd.get("ball_limit", False) else 0
        timed = rd.get("time_limit", True) or not balls
        secs = int(rd.duration_s)
        # right: the balls (∞ without a limit); left: the seconds
        cols = [(str(balls) if balls else "∞", T.balls if balls else T.balls_unlimited)]
        if timed:
            cols.append((str(secs), T.time))
        for i, (num, label) in enumerate(cols):
            x = (224 if i == 0 else 80) if len(cols) == 2 else W / 2
            role = "latin" if num == "∞" else "numbers"
            self.text.blit(surf, num, role, vp.s(40 if num != "∞" else 46), theme.WHITE, vp.px(x, 128),
                           localize=num != "∞")
            self.text.blit(surf, label, "title", vp.s(9), theme.TURQ, vp.px(x, 170))
        line = theme.lerp_color(theme.FIELD, theme.MIST, 0.25)
        lw = max(1, vp.s(0.25))
        if len(cols) == 2:
            pygame.draw.line(surf, line, vp.px(W / 2, 106), vp.px(W / 2, 176), lw)
        sentence = T.rule_time.format(seconds=secs) if timed and not balls else T.rule_balls.format(balls=balls, seconds=secs)
        self.text.blit(surf, sentence, "body", vp.s(5.4), theme.MIST, vp.px(W / 2, 189))
        pygame.draw.line(surf, line, vp.px(16, 204), vp.px(W - 16, 204), lw)
        self.text.blit(surf, T.rule_size, "heading", vp.s(6.2), theme.WHITE, vp.px(W - 16, 216), "midright")
        raw = cfg.raw()
        for i, (tier, x, key) in enumerate((("medium", 232, product_for(raw, "medium")), ("small", 149, product_for(raw, "small")),
                                           ("tiny", 67, product_for(raw, "tiny")))):
            appear = _ease_back((el - 0.15 * i) / 0.4)
            if appear <= 0:
                continue
            size = cfg.targets[tier].size_cm  # true size on the wall
            spr = self.sprites.product(key, max(4, vp.s(size * appear)), ring=True)
            surf.blit(spr, spr.get_rect(center=vp.px(x, 254)))
            self.text.blit(surf, f"+{points_of(raw, key, tier)}", "numbers", vp.s(13), theme.WHITE, vp.px(x, 297))
            self.text.blit(surf, T[f"tier_{tier}"], "heading", vp.s(5.6), theme.MIST, vp.px(x, 315))
            if tier == "tiny" and cfg.rush.enabled:
                self.text.blit(surf, T.tiny_rush, "heading", vp.s(5), theme.CORAL, vp.px(x, 325))
        pygame.draw.line(surf, line, vp.px(16, 336), vp.px(W - 16, 336), lw)
        lines = [("slash", T.rule_move)]
        if cfg.combo.enabled and cfg.combo.levels:
            top = max(lv["multiplier"] for lv in cfg.combo.levels)
            lines.append(("times", T.rule_combo.format(max=f"×{top:g}")))
        lines.append(("plus", T.rule_positive))
        if cfg.rush.enabled and cfg.rush.max_per_round > 0:
            lines.append(("dot", T.rule_rush.format(seconds=int(cfg.rush.duration_s), m=f"×{float(cfg.rush.score_multiplier):g}")))
        y = 345
        for mark, text in lines:
            draw_symbol(surf, mark, vp.px(W - 18, y + 3.2), vp.s(2.6), theme.CORAL if mark == "dot" else theme.TURQ)
            y = self.text.blit_lines(surf, text, "body", vp.s(5.4), theme.WHITE, vp.px(W - 24, y), vp.s(250), 1.3,
                                     anchor_x="right")
            y = int((y - vp.rect.y) / vp.ppc + 2.5)

    # -- player replay ---------------------------------------------------------------------------
    # With camera.enabled the results screen is a profile card made to be photographed and
    # shared: a tall picture on the left in a thick white frame with a turquoise outline frame
    # tilted behind it, and the name, score, play style and hits in a column on the right.
    # The picture is the replay of one of the player's scoring hits, ending on their face.
    PHOTO_RECT = (16, 40, 148, 304)  # wall cm, 1:2
    PHOTO_BORDER_CM = 3.0
    OUTLINE_CM = 1.4  # the tilted turquoise frame behind the photo
    OUTLINE_TILT = -3.0  # degrees
    COL_X, COL_W = 222, 128  # the text column: centre and width in cm

    def set_replay(self, round_id: str | None, replay) -> None:
        """The camera's replay (replay.Replay) for the results of round_id; None: the placeholder.
        The still it ends on gets a random look on some rounds (camera.effect_chance)."""
        if self.replay is not None and self.replay is not replay:
            self.replay.close()  # its frames and thread go with the round
        self.replay_round = round_id
        self.replay = replay if round_id is not None else None
        self.photo_img = None
        cam = self.cfg.camera
        looks = list(cam.photo_effects)
        self.photo_effect = "natural"
        if self.replay is not None and looks and self.fx_rng.random() < cam.effect_chance:
            self.photo_effect = self.fx_rng.choice(looks)

    # The card's entrance, in seconds after the results appear. Everything is a function of
    # that time (no state), so a frame is the same however it is reached. Brand book: flat
    # shapes, eased movement, the + - x / o symbols, no flashing.
    #   0.00 the white frame opens from a turquoise line     0.30 the replay rises into it
    #   0.95 the outline frame turns out from behind          1.10 brand symbols
    #   0.45 name, then score count, play style, sentence line by line, hit cards, details
    # The replay plays in the frame (replay.Timeline) and ends on a still of the face; one soft
    # light sweep crosses it as it lands.
    T_FRAME, T_PHOTO, T_OUTLINE, T_BURST = 0.0, 0.3, 0.95, 1.1

    def _photo(self, rec: dict) -> tuple[dict, object]:
        replay = self.replay if rec.get("round_id") == self.replay_round else None  # only that round's
        r = self.vp.cm_rect(*self.PHOTO_RECT)
        key = (replay is not None, rec.get("round_id"), r.size, self.photo_effect)
        if self.photo_img is None or self.photo_img[0] != key:
            self.photo_img = (key, self._build_photo(replay is None, r.w, r.h, str(rec.get("round_id"))))
            if replay is not None:
                replay.start(r.size, self.photo_effect)
        return self.photo_img[1], replay

    def _build_photo(self, placeholder: bool, w: int, h: int, seed: str) -> dict:
        """The layers around the picture, built once per round."""
        vp = self.vp
        radius = vp.s(3)
        pic = None
        if placeholder:  # no replay this round: a head and shoulders
            pic = pygame.Surface((w, h), pygame.SRCALPHA)
            pic.fill(theme.BLUE_MID)
            pygame.draw.circle(pic, theme.BLUE_SOFT, (w * 0.5, h * 0.3), w * 0.22)
            pygame.draw.ellipse(pic, theme.BLUE_SOFT, pygame.Rect(w * 0.1, h * 0.5, w * 0.8, h * 0.62))
            mask = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
            pic.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            pic = pic.convert_alpha() if pygame.display.get_surface() is not None else pic
        # the video's rounded corners: four small pieces of the white frame laid over it
        corner = pygame.Surface((radius, radius), pygame.SRCALPHA)
        corner.fill(theme.WHITE)
        pygame.draw.circle(corner, (0, 0, 0, 0), (radius, radius), radius)
        corners = [(corner, (0, 0)), (pygame.transform.flip(corner, True, False), (w - radius, 0)),
                   (pygame.transform.flip(corner, False, True), (0, h - radius)),
                   (pygame.transform.flip(corner, True, True), (w - radius, h - radius))]

        b = vp.s(self.PHOTO_BORDER_CM)
        shadow = pygame.Surface((w + 2 * b, h + 2 * b), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (*theme.BLUE_DEEP, 150), shadow.get_rect(), border_radius=radius + b)
        # the light sweep: a slanted soft band
        bw = int(w * 0.45)
        shine = pygame.Surface((bw + h // 3, h), pygame.SRCALPHA)
        for i, a in enumerate((18, 26, 34)):
            inset = int(bw * 0.18 * i)
            pygame.draw.polygon(shine, (255, 255, 255, a), [(h // 3 + inset, 0), (h // 3 + bw - inset, 0),
                                                              (bw - inset, h), (inset, h)])
        rng = random.Random(seed)  # the same symbols for the same round, every frame
        burst = []
        for i in range(12):
            side = i % 4
            u = rng.uniform(0.1, 0.9)
            start = [(u, 0.0), (1.0, u), (u, 1.0), (0.0, u)][side]
            out = [(0, -1), (1, 0), (0, 1), (-1, 0)][side]
            burst.append((start, out, rng.uniform(8, 20), rng.choice(theme.SYMBOLS),
                          rng.choice((theme.TURQUOISE, theme.WHITE, theme.TURQUOISE_RAMP[5])),
                          rng.uniform(2.4, 4.2), rng.uniform(0.0, 0.25)))
        sparks = [(rng.uniform(-26, 26), rng.uniform(0.0, 0.3), rng.uniform(2.2, 3.4)) for _ in range(5)]
        return {"pic": pic, "corners": corners, "shadow": shadow, "shine": shine, "burst": burst, "sparks": sparks}

    def _draw_photo(self, surf: pygame.Surface, layers: dict, t: float, replay=None) -> None:
        vp = self.vp
        inner = vp.cm_rect(*self.PHOTO_RECT)
        b = vp.s(self.PHOTO_BORDER_CM)
        frame = inner.inflate(2 * b, 2 * b)
        radius = vp.s(3) + b

        # the tilted outline: a frame-sized border that turns out from behind the photo, then breathes
        p = _ease_out(_phase(t, self.T_OUTLINE, 0.7))
        if p > 0:
            line = max(2, vp.s(self.OUTLINE_CM))
            tilt = self.OUTLINE_TILT * p + 0.35 * math.sin(t * 2 * math.pi / 7.0) * p
            off = vp.s(2.5) * p
            hw, hh = frame.w / 2 + vp.s(2) + line / 2, frame.h / 2 + vp.s(2) + line / 2
            a = math.radians(tilt)
            cx, cy = frame.centerx + off, frame.centery + off
            pts = [(cx + x * math.cos(a) + y * math.sin(a), cy - x * math.sin(a) + y * math.cos(a))
                   for x, y in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
            pygame.draw.lines(surf, theme.TURQUOISE, True, pts, line)
            for pt in pts:
                pygame.draw.circle(surf, theme.TURQUOISE, pt, line / 2)

        # the white frame opens up from a turquoise line across its middle
        p = _ease_out(_phase(t, self.T_FRAME, 0.45))
        if p <= 0:
            return
        h = max(vp.s(1.2), int(frame.h * p))
        r = pygame.Rect(frame.x, frame.centery - h // 2, frame.w, h)
        if p >= 1:
            surf.blit(layers["shadow"], frame.move(vp.s(2.4), vp.s(2.4)))
        pygame.draw.rect(surf, theme.lerp_color(theme.TURQUOISE, theme.WHITE, p * 1.6), r,
                         border_radius=min(radius, h // 2))
        if p < 1:
            return
        pygame.draw.rect(surf, theme.BLUE_DEEP, inner, border_radius=vp.s(3))

        # the replay rises into the frame behind a turquoise line, like a price going up
        tau = t - self.T_PHOTO
        pic = layers["pic"] if replay is None else replay.surface(tau)
        p = _ease_in_out(_phase(t, self.T_PHOTO, 0.75))
        shown = int(inner.h * p)
        if pic is not None and shown > 0:
            surf.blit(pic, (inner.x, inner.bottom - shown), pygame.Rect(0, inner.h - shown, inner.w, shown))
        if replay is not None:
            self._replay_overlay(surf, inner, replay, tau, layers)
            for img, (x, y) in layers["corners"]:
                surf.blit(img, (inner.x + x, inner.y + y))
        if 0 < p < 1:
            surf.fill(theme.TURQUOISE, pygame.Rect(inner.x, inner.bottom - shown - vp.s(0.6), inner.w, vp.s(1.2)))

        # one soft light sweep across the picture: as the replay's still lands, or once it is in
        sweep = self.T_BURST + 0.1 if replay is None else self.T_PHOTO + replay.length + 0.5
        p = _ease_in_out(_phase(t, sweep, 0.8))
        if 0 < p < 1:
            shine = layers["shine"]
            clip = surf.get_clip()
            surf.set_clip(inner)
            surf.blit(shine, (inner.x - shine.get_width() + int((inner.w + shine.get_width()) * p), inner.y))
            surf.set_clip(clip)

        # brand symbols leave the frame's edges and fade
        for (u, v), (dx, dy), dist, kind, color, size, delay in layers["burst"]:
            q = _phase(t, self.T_BURST + delay, 1.4)
            if not 0 < q < 1:
                continue
            d = vp.s(dist) * _ease_out(q)
            sym = self.sprites.symbol(kind, vp.s(size), color)
            sym.set_alpha(int(255 * min(1.0, (1 - q) / 0.5)))
            surf.blit(sym, sym.get_rect(center=(frame.x + u * frame.w + dx * d, frame.y + v * frame.h + dy * d)))
            sym.set_alpha(255)

    REPLAY_POINTS_S = 1.9  # how long the points of the replayed hit stay up

    def _replay_overlay(self, surf: pygame.Surface, inner: pygame.Rect, replay, tau: float, layers: dict) -> None:
        """Over the video, like a sports replay: film bars and a label in slow motion, the hit's
        points rising at the hit, a thin progress line. All of it leaves as the still comes in."""
        vp, T = self.vp, self.T
        m = replay.info(tau)
        fade = 1.0 - m.poster
        if fade <= 0.0 or tau < 0:
            return
        clip = surf.get_clip()
        surf.set_clip(inner)
        bar = int(inner.h * 0.06 * _ease_in_out(m.slow) * fade)
        if bar > 0:
            surf.fill(theme.BLUE_DEEP, pygame.Rect(inner.x, inner.y, inner.w, bar))
            surf.fill(theme.BLUE_DEEP, pygame.Rect(inner.x, inner.bottom - bar, inner.w, bar))

        # the label, top right where Persian starts: "replay", then "slow motion"
        show = fade * _ease_out(_phase(tau, 0.5, 0.4))
        if show > 0:
            px = vp.s(4.6)
            y = inner.y + bar + vp.s(6)
            for text, share in ((T.replay_label, 1.0 - m.slow), (T.replay_slow, m.slow)):
                a = int(255 * show * share)
                if a < 8:
                    continue
                img = self.text.render(text, "bold", px, theme.WHITE)
                pw, ph = img.get_width() + vp.s(9), int(img.get_height() * 1.35)
                right = inner.right - vp.s(4)
                pill = self.sprites.rounded(pw, ph, ph // 2, theme.BLUE_DEEP)
                pill.set_alpha(int(a * 0.8))
                surf.blit(pill, (right - pw, y))
                pill.set_alpha(255)
                dot = self.sprites.symbol("dot", vp.s(2.2), theme.TURQUOISE)
                dot.set_alpha(a)
                surf.blit(dot, dot.get_rect(center=(right - vp.s(3.6), y + ph // 2)))
                dot.set_alpha(255)
                img.set_alpha(a)
                surf.blit(img, img.get_rect(midright=(right - vp.s(6.2), y + ph // 2)))

        # the hit's points rise over the player, with + symbols
        if m.since_hit is not None and replay.points > 0 and m.since_hit < self.REPLAY_POINTS_S:
            q = m.since_hit / self.REPLAY_POINTS_S
            rise = _ease_out(min(1.0, q / 0.3))
            a = int(255 * fade * min(1.0, (1.0 - q) / 0.3) * min(1.0, q / 0.1))
            cx, y = inner.centerx, inner.bottom - bar - vp.s(34) - vp.s(12) * rise
            label = f"+{replay.points}"
            self.text.blit(surf, label, "numbers", vp.s(22), theme.BLUE_DEEP, (cx + vp.s(0.9), int(y) + vp.s(0.9)), alpha=a)
            self.text.blit(surf, label, "numbers", vp.s(22), theme.WHITE, (cx, int(y)), alpha=a)
            half = int(vp.s(16) * rise)
            if half > 0 and a > 128:
                surf.fill(theme.TURQUOISE, pygame.Rect(cx - half, int(y) + vp.s(13), 2 * half, vp.s(1.2)))
            for dx, delay, size in layers["sparks"]:
                k = _phase(m.since_hit, 0.15 + delay, 1.1)
                if 0 < k < 1:
                    sym = self.sprites.symbol("plus", vp.s(size), theme.TURQUOISE_RAMP[5])
                    sym.set_alpha(int(255 * fade * (1 - k)))
                    surf.blit(sym, sym.get_rect(center=(cx + vp.s(dx), int(y - vp.s(10) - vp.s(18) * _ease_out(k)))))
                    sym.set_alpha(255)

        # a thin progress line along the bottom
        if fade > 0.5:
            w = int(inner.w * m.progress)
            if w > 0:
                surf.fill(theme.TURQUOISE, pygame.Rect(inner.x, inner.bottom - bar - vp.s(0.9), w, vp.s(0.9)))
        surf.set_clip(clip)

    def _wipe(self, surf: pygame.Surface, img: pygame.Surface, center: tuple[int, int], p: float, caret=None) -> None:
        """Text revealed from the right, the way Persian is read, rising a little into place."""
        if p <= 0:
            return
        w, h = img.get_size()
        rect = img.get_rect(center=center)
        rect.y += int(self.vp.s(2.5) * (1 - p))
        vis = max(1, int(w * p))
        surf.blit(img, (rect.x + w - vis, rect.y), pygame.Rect(w - vis, 0, vis, h))
        if caret is not None and p < 1:
            surf.fill(caret, pygame.Rect(rect.x + w - vis - self.vp.s(0.9), rect.y + int(h * 0.12), self.vp.s(0.9), int(h * 0.76)))

    def _fit(self, text: str, role: str, size_cm: float, width_cm: float) -> int:
        """The font size in px for text that must fit width_cm (smaller, never wider)."""
        px = self.vp.s(size_cm)
        w = self.text.width(text, role, px)
        return int(px * self.vp.s(width_cm) / w) if w > self.vp.s(width_cm) else px

    def _results_card(self, surf: pygame.Surface, flow: F.Flow, rec: dict, t: float) -> None:
        """The profile card: the part people take a picture of."""
        vp, T, cfg = self.vp, self.T, self.cfg
        X, CW = self.COL_X, self.COL_W
        self._blit_logo(surf, "fa_h_white_small", vp.px(self.W / 2, 17), theme.WHITE)
        layers, replay = self._photo(rec)
        self._draw_photo(surf, layers, t, replay)

        name = _short(rec.get("player_name") or "", cfg.flow.name_max_chars)
        if name and cfg.results.show_names:
            img = self.text.render(name, "title", self._fit(name, "title", 15, CW), theme.WHITE)
            self._wipe(surf, img, vp.px(X, 54), _ease_out(_phase(t, 0.45, 0.5)), theme.TURQUOISE)
        a = int(255 * _phase(t, 0.75, 0.3))
        if a:
            self.text.blit(surf, T.results_title, "body", vp.s(6), theme.BLUE_PALE, vp.px(X, 72), alpha=a)

        # the score counts up, then gives one small beat with "+" sparks rising from it
        final = rec.get("score", 0)
        count_end = 2.0
        score = int(round(final * _ease_out(_phase(t, 0.85, count_end - 0.85))))
        px = self._fit(str(final), "numbers", 46, CW)
        if t >= 0.85:
            img = self.text.render(str(score), "numbers", px, theme.WHITE, cache=score == final)
            beat = math.sin(math.pi * _phase(t, count_end, 0.35))
            if beat > 0:
                img = pygame.transform.smoothscale(img, (int(img.get_width() * (1 + 0.1 * beat)),
                                                         int(img.get_height() * (1 + 0.1 * beat))))
            surf.blit(img, img.get_rect(center=vp.px(X, 99)))
            for dx, delay, size in layers["sparks"]:
                q = _phase(t, count_end + delay, 0.9)
                if 0 < q < 1:
                    sym = self.sprites.symbol("plus", vp.s(size), theme.TURQUOISE_RAMP[5])
                    sym.set_alpha(int(255 * (1 - q)))
                    surf.blit(sym, sym.get_rect(center=vp.px(X + dx, 84 - 14 * _ease_out(q))))
                    sym.set_alpha(255)
        half = 20 * _ease_out(_phase(t, 1.0, 0.45))
        if half > 0:
            surf.fill(theme.TURQUOISE, vp.cm_rect(X - half, 124, X + half, 125.6))

        profile = rec.get("profile")
        lines = self.text.wrap(self.text.localize(profile["statement"]), "body", vp.s(6.6), vp.s(CW - 4)) if profile else []
        t_cards = 1.9 + 0.2 * len(lines) + 0.3
        if profile:
            a = int(255 * _phase(t, 1.3, 0.3))
            if a:
                self.text.blit(surf, T.profile_title, "body", vp.s(5.2), theme.BLUE_PALE, vp.px(X, 136), alpha=a)
            img = self.text.render(profile["label"], "title", self._fit(profile["label"], "title", 12, CW), theme.TURQUOISE_RAMP[5])
            self._wipe(surf, img, vp.px(X, 150), _ease_out(_phase(t, 1.4, 0.5)), theme.WHITE)
            px = vp.s(6.6)
            y = vp.px(X, 166)[1]
            for i, line in enumerate(lines):  # one line after another, each read in from the right
                img = self.text.render(line, "body", px, theme.WHITE, localize=False)
                rect = img.get_rect(midtop=(vp.px(X, 0)[0], y))
                self._wipe(surf, img, rect.center, _ease_out(_phase(t, 1.9 + 0.2 * i, 0.45)))
                y += int(px * 1.35)

        y = 166 + len(lines) * 6.6 * 1.35 + 30  # the portfolio follows the sentence, however long it is
        a = int(255 * _phase(t, t_cards - 0.1, 0.3))
        if a:
            self.text.blit(surf, T.portfolio, "heading", vp.s(5.2), theme.BLUE_PALE, vp.px(X, y - 17), alpha=a)
        self._portfolio(surf, rec, X, y, CW, 15.0, t, t_cards)

        t_more = t_cards + 0.6
        img = self.text.render(T.results_hits.format(hits=rec.get("total_hits", 0), throws=rec.get("throws", 0)),
                               "heading", vp.s(6), theme.WHITE)
        self._wipe(surf, img, vp.px(X, y + 36), _ease_out(_phase(t, t_more, 0.4)))
        y += 52
        pills = []
        if cfg.combo.enabled and cfg.combo.levels:
            pills.append((f"{T.results_best} ×{rec.get('best_multiplier', 1):g}", theme.PURPLE))
        rank = flow.rank
        if rank is not None and rank <= max(1, cfg.results.leaderboard_size):
            pills.append((T.results_rank.format(rank=rank), theme.TURQUOISE))
        for i, (label, color) in enumerate(pills):  # slide in from the right
            p = _ease_back(_phase(t, t_more + 0.25 + 0.15 * i, 0.4))
            if p > 0:
                self._pill(surf, label, color, theme.WHITE, (X + 14 * (1 - p), y), 5.4)
            y += 16
        rush = rec.get("rush") or {}
        if rush.get("count"):
            text = T.results_rush.format(hits=rush.get("hits", 0), points=rush.get("points", 0))
            a = int(255 * _phase(t, t_more + 0.6, 0.4))
            if a:
                self.text.blit(surf, text, "body", self._fit(text, "body", 5.4, CW), theme.BLUE_PALE, vp.px(X, y), alpha=a)
        self.card_done_at = t_more + 1.0  # the note below waits for this

    BARS_BASE = 62.0  # cm below the portfolio title: the chart's base line
    BARS_MAX = 30.0  # cm, the tallest bar (the product that earned the most points)

    def _portfolio_bars(self, surf, rec: dict, y: float, t: float, start: float) -> None:
        """The portfolio as a rising bar chart (after the reference clip): a bar for each product
        hit, as tall as the points it earned, lowest on the left. Each bar rises, its tile pops
        up on a thin stem above it with a halo that opens and fades, and a line of moving dots
        joins the tiles. y: the portfolio title's line, cm. Older records without
        product_points get bars by hits."""
        vp, W = self.vp, self.W
        known = {p["key"] for p in self.cfg.raw()["products"]}
        hits = rec.get("products") or {}
        pts = rec.get("product_points") or {}
        items = [(k, int(pts.get(k, 0)) if pts else int(n), int(n)) for k, n in hits.items() if k in known and n > 0]
        if not items:
            return
        items.sort(key=lambda it: it[1])  # rising, as the clip's chart
        n = len(items)
        slot = min(30.0, (W - 32) / n)
        tile_cm = min(18.0, slot - 4.0)
        bar_w = tile_cm * 0.62
        base_y = y + self.BARS_BASE
        top = max(v for _, v, _ in items) or 1
        x0 = W / 2 - (n - 1) * slot / 2
        line = theme.lerp_color(theme.FIELD, theme.MIST, 0.35)
        body = theme.lerp_color(theme.FIELD, theme.MIST, 0.3)
        thin = max(1, vp.s(0.3))
        p = _ease_out(_phase(t, start - 0.1, 0.4))  # the base line draws out first
        if p > 0:
            half = (W - 32) / 2 * p
            surf.fill(line, vp.cm_rect(W / 2 - half, base_y, W / 2 + half, base_y + 0.35))
        heads = []
        for i, (key, value, count) in enumerate(items):
            x = x0 + i * slot
            grow = _ease_out(_phase(t, start + 0.07 * i, 0.45))
            if grow <= 0:
                continue
            h = max(3.0, self.BARS_MAX * value / top) * grow
            radius = max(1, vp.s(0.9))
            pygame.draw.rect(surf, body, vp.cm_rect(x - bar_w / 2, base_y - h, x + bar_w / 2, base_y),
                             border_top_left_radius=radius, border_top_right_radius=radius)
            pygame.draw.rect(surf, theme.TURQ, vp.cm_rect(x - bar_w / 2, base_y - h, x + bar_w / 2, base_y - h + min(h, 1.0)),
                             border_top_left_radius=radius, border_top_right_radius=radius)
            label = f"+{value}" if pts else f"×{count}"
            self.text.blit(surf, label, "numbers", vp.s(3.8), theme.MIST, vp.px(x, base_y + 4.2), alpha=int(255 * grow))
            q = _phase(t, start + 0.35 + 0.1 * i, 0.35)  # the stem grows, then the tile pops on it
            if q <= 0:
                continue
            stem = 4.0 * min(1.0, q * 2.0)
            pygame.draw.line(surf, line, vp.px(x, base_y - h), vp.px(x, base_y - h - stem), thin)
            s = _ease_back(max(0.0, q * 2.0 - 1.0))
            tile_h = tile_cm * self.sprites.product_aspect.get(key, 0.92)
            cy = base_y - h - 4.0 - tile_h / 2
            if s > 0:
                base = vp.s(tile_cm)
                img = self.sprites.product_scaled(key, base * s, base, ring=False)
                surf.blit(img, img.get_rect(center=vp.px(x, cy)))
            halo = _phase(t, start + 0.6 + 0.1 * i, 0.6)  # then a thin halo opens around it and fades
            if 0.0 < halo < 1.0:
                col = theme.lerp_color(theme.MIST, theme.FIELD, halo ** 0.8)
                pygame.draw.aacircle(surf, col, vp.px(x, cy), int(vp.sf(tile_cm * 0.62 + 4.0 * _ease_out(halo))), thin)
            if s >= 1.0:
                heads.append((x, cy - tile_h / 2 - 1.5))
        d = _phase(t, start + 0.55 + 0.1 * n, 0.5)  # moving dots join the tiles, left to right
        if d > 0 and len(heads) > 1:
            flow = (t * 0.9) % 1.0
            dot = max(1.0, vp.sf(0.45))
            for (ax, ay), (bx, by) in zip(heads, heads[1:]):
                ax, bx = ax + tile_cm * 0.25, bx - tile_cm * 0.25
                for j in range(7):
                    u = (j + flow) / 7
                    if u > d:
                        continue
                    lift = 5.0 * 4 * u * (1 - u)
                    px, py = vp.px(ax + (bx - ax) * u, ay + (by - ay) * u - lift)
                    pygame.draw.circle(surf, theme.lerp_color(theme.FIELD, theme.TURQ, min(1.0, 0.35 + u)), (px, py), dot)

    def _portfolio(self, surf, rec: dict, cx: float, y: float, width: float, tile_cm: float, t: float, start: float) -> None:
        """The products the player hit, most-hit first, in a row centred at cx (MOFID_UI v4.1:
        "your portfolio"), each popping in, with ×n when it was hit more than once."""
        vp = self.vp
        hits = rec.get("products") or {}
        known = {p["key"] for p in self.cfg.raw()["products"]}
        items = sorted(((k, n) for k, n in hits.items() if k in known and n > 0), key=lambda kv: -kv[1])
        if not items:
            return
        gap = 2.5
        n = min(len(items), max(1, int((width + gap) // (tile_cm + gap))))
        items = items[:n]
        x = cx + (n - 1) * (tile_cm + gap) / 2
        for i, (key, count) in enumerate(items):  # right to left, as Persian is read
            s = _ease_back(_phase(t, start + 0.08 * i, 0.4))
            if s > 0:
                base = vp.s(tile_cm)
                img = self.sprites.product_scaled(key, base * s, base, ring=False)
                surf.blit(img, img.get_rect(center=vp.px(x, y)))
                if count > 1 and s >= 1.0:
                    self.text.blit(surf, f"×{count}", "numbers", vp.s(3.8), theme.TURQ, vp.px(x, y + tile_cm * 0.46 + 4))
            x -= tile_cm + gap

    # -- results -------------------------------------------------------------------------------
    def draw_results(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        vp, W, T, cfg = self.vp, self.W, self.T, self.cfg
        rec = flow.summary or {}
        t = now - self.results_t0
        profile = rec.get("profile")
        if self.photo_slot:
            surf.blit(self.bg_card, (0, 0))
            self._results_card(surf, flow, rec, t)
            a = int(255 * _phase(t, self.card_done_at, 0.5))
            if profile and a:
                self.text.blit(surf, T.profile_note, "body", vp.s(4.6), theme.BLUE_PALE, vp.px(W / 2, 336), alpha=a)
            self._results_footer(surf, flow, now)
            return
        # MOFID_UI v11, 07 RESULTS: the logo, «امتیاز شما» and the score huge over its line, the
        # lottery points, the hits and today's rank. Type only: no cards, icons or play style.
        surf.blit(self.bg_dark, (0, 0))
        self._blit_logo(surf, "fa_s_white_small", vp.px(W / 2, 30), theme.WHITE)
        line = theme.lerp_color(theme.FIELD, theme.MIST, 0.22)
        lw = max(1, vp.s(0.25))
        img = self.text.render(T.results_title, "title", vp.s(14), theme.TURQ)
        self._wipe(surf, img, vp.px(W / 2, 74), _ease_out(_phase(t, 0.0, 0.45)))
        final = rec.get("score", 0)
        COUNT = (0.25, 1.4)
        p_count = _ease_out(_phase(t, *COUNT))
        score = int(round(final * p_count))
        img = self.text.render(str(score), "numbers", vp.s(62), theme.WHITE, cache=score == final)
        beat = math.sin(math.pi * _phase(t, sum(COUNT), 0.3))  # one small beat as it lands
        if beat > 0:
            img = pygame.transform.smoothscale(img, (int(img.get_width() * (1 + 0.07 * beat)), int(img.get_height() * (1 + 0.07 * beat))))
        surf.blit(img, img.get_rect(center=vp.px(W / 2, 128)))
        self._score_line(surf, rec.get("score_path") or [], _phase(t, *COUNT), (20, 178, W - 20, 200))
        pygame.draw.line(surf, line, vp.px(16, 214), vp.px(W - 16, 214), lw)

        # 1.4 the lottery points: the title and its rule on the right, the number on the left
        on = bool(cfg.results.get("lottery", True))
        y = 214
        if on:
            q = _phase(t, 1.4, 0.4)
            if q > 0:
                a, dy = int(255 * q), 4 * (1 - _ease_out(q))
                lottery = rec.get("lottery_points", lottery_points(cfg.raw(), final))
                self.text.blit(surf, T.results_lottery, "title", vp.s(8.5), theme.WHITE, vp.px(W - 20, 228 + dy),
                               "midright", alpha=a)
                # the three levels, right to left, each its own piece (numbers in Persian text keep their place)
                two, three = int(cfg.results.lottery_two_from), int(cfg.results.lottery_three_from)
                x = vp.px(W - 20, 0)[0]
                for i, key in enumerate(("lottery_tier1", "lottery_tier2", "lottery_tier3")):
                    piece = self.text.render(T[key].format(a=two, b=three), "body", vp.s(4.4),
                                             theme.WHITE if i + 1 == lottery else theme.MIST)
                    piece.set_alpha(a)
                    r = surf.blit(piece, piece.get_rect(midright=(x, vp.px(0, 241 + dy)[1])))
                    piece.set_alpha(255)
                    x = r.left - vp.s(5)
                self.text.blit(surf, str(lottery), "numbers", vp.s(26), theme.TURQ, vp.px(44, 234 + dy), alpha=a)
            y = 254
            pygame.draw.line(surf, line, vp.px(16, y), vp.px(W - 16, y), lw)

        # 1.6 the hits (right) and today's rank (left)
        cols = [(str(rec.get("total_hits", 0)), T.results_hits_ok)]
        if flow.rank is not None:
            cols.append((str(flow.rank), T.results_rank.format(rank=flow.rank)))
        xs = (216, 84) if len(cols) == 2 else (W / 2,)
        for i, ((big, label), x) in enumerate(zip(cols, xs)):
            q = _phase(t, 1.6 + 0.15 * i, 0.4)
            if q <= 0:
                continue
            a, dy = int(255 * q), 5 * (1 - _ease_out(q))  # each rises into place
            self.text.blit(surf, big, "numbers", vp.s(26), theme.TURQ, vp.px(x, y + 22 + dy), alpha=a)
            self.text.blit(surf, label, "heading", vp.s(5.6), theme.WHITE, vp.px(x, y + 44 + dy), alpha=a)
        if len(cols) == 2 and t > 1.6:
            pygame.draw.line(surf, line, vp.px(W / 2, y + 8), vp.px(W / 2, y + 52), lw)
        self._results_footer(surf, flow, now)

    def _score_line(self, surf, path: list, p: float, box: tuple) -> None:
        """MOFID_UI v4.1, 08 RESULTS: the score after each throw as a thin rising line, drawn
        left to right up to share p, with a dot at its tip."""
        if p <= 0:
            return
        vp = self.vp
        x0, y0, x1, y1 = box
        vals = [0, *path] if path else [0, 0]
        top = max(max(vals), 1)
        pts = [vp.px(x0 + (x1 - x0) * i / (len(vals) - 1), y1 - (y1 - y0) * v / top) for i, v in enumerate(vals)]
        # the part drawn so far, ending part-way along a segment
        f = p * (len(pts) - 1)
        k = int(f)
        shown = pts[:k + 1]
        if k < len(pts) - 1:
            (ax, ay), (bx, by) = pts[k], pts[k + 1]
            shown.append((ax + (bx - ax) * (f - k), ay + (by - ay) * (f - k)))
        col = theme.lerp_color(theme.FIELD, theme.TURQ, 0.8)
        if len(shown) > 1:
            w = max(1, vp.s(0.35))
            pygame.draw.lines(surf, col, False, shown, w) if w > 1 else pygame.draw.aalines(surf, col, False, shown)
        dot = self.sprites.dot(vp.sf(1.1), theme.TURQ)
        surf.blit(dot, dot.get_rect(center=(int(shown[-1][0]), int(shown[-1][1]))))

    def _results_footer(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        vp, W, cfg = self.vp, self.W, self.cfg
        p = min(1.0, (now - flow.since) / max(0.1, cfg.flow.results_s))
        self.text.blit(surf, self.T.results_next, "heading", vp.s(6.5), theme.MIST, vp.px(W / 2, 352))
        bar = vp.cm_rect(60, 364, W - 60, 364.9)
        surf.fill(theme.lerp_color(theme.FIELD, theme.MIST, 0.25), bar)
        w = int(bar.w * (1 - p))
        if w:
            surf.fill(theme.TURQ, pygame.Rect(bar.right - w, bar.y, w, bar.h))
        if self.photo_slot:  # the card has no frame of its own
            self._steady_progress(surf, theme.TURQUOISE, theme.WHITE)

    def _pill(self, surf, label: str, bg, fg, center_cm, size_cm: float) -> None:
        vp = self.vp
        img = self.text.render(label, "heading", vp.s(size_cm), fg)
        w = img.get_width() + vp.s(size_cm * 2.2)
        h = int(img.get_height() * 1.25)
        pill = self.sprites.rounded(w, h, h // 2, bg)
        c = vp.px(*center_cm)
        surf.blit(pill, pill.get_rect(center=c))
        surf.blit(img, img.get_rect(center=c))

    # -- shared overlays ----------------------------------------------------------------------
    def _center_overlay(self, surf, title: str, sub: str | None) -> None:
        vp = self.vp
        self.dim.set_alpha(210)
        surf.blit(self.dim, vp.rect.topleft)
        self.text.blit(surf, title, "title", vp.s(30), theme.WHITE, vp.px(self.W / 2, self.H / 2))
        if sub:
            self.text.blit(surf, sub, "body", vp.s(8), theme.MIST, vp.px(self.W / 2, self.H / 2 + 30))

    def _badges(self, surf, overlays: dict) -> None:
        """Operator-facing status pills along the bottom edge."""
        vp = self.vp
        y = self.H - 4.5
        # no pill for simulation mode (mouse clicks as throws): the wall shows only the game
        if overlays.get("sensor_offline"):
            img = self.text.render(self.T.sensor_offline, "bold", vp.s(3.4), theme.WHITE)
            pill = self.sprites.rounded(img.get_width() + vp.s(6), int(img.get_height() * 1.2), vp.s(2.5), theme.RED)
            r = pill.get_rect(bottomright=vp.px(self.W - 3, self.H - 2))
            surf.blit(pill, r)
            surf.blit(img, img.get_rect(center=r.center))
        msg = overlays.get("notice")
        if msg:
            self.text.blit(surf, msg, "latin", vp.s(3.2), theme.RED, vp.px(self.W / 2, y), localize=False)
