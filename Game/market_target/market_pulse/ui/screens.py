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
import time
from contextlib import contextmanager
from pathlib import Path

import pygame

from ..config import COLORS, Section, layout_left, layout_top
from ..game import flow as F
from ..game.rules import Outcome
from . import theme
from .draw import Sprites, dot_grid, gradient_bar
from .effects import Effects
from .text import Text, split_runs
from .theme import Viewport

log = logging.getLogger(__name__)

LOGOS = {  # key: (SVG asset in assets/manifest.json, raster fallback in assets/images)
    "fa_h": ("logo.fa_horizontal", "logo_fa_horizontal.png"),
    "fa_h_white": ("logo.fa_horizontal_white", "logo_fa_horizontal_white.png"),
    "fa_h_white_small": ("logo.fa_horizontal_white", "logo_fa_horizontal_white.png"),
    "fa_s_white": ("logo.fa_stacked_white", "logo_fa_stacked_white.png"),
    "fa_s": ("logo.fa_stacked", "logo_fa_stacked.png"),
}


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
        self.badge_pop_t = -10.0
        self.badge_shake_t = -10.0
        self.results_t0 = 0.0
        self.rush_title_t = -10.0
        self._fade_pending = False
        self._fade_t0 = -10.0
        self._prewarm: list[tuple[str, int]] = []
        self.dim: pygame.Surface | None = None
        # The player's replay on the results screen (camera.enabled), or a placeholder.
        self.photo_slot = bool(cfg.camera.enabled)
        self.replay_round: str | None = None
        self.replay = None
        self.photo_img: tuple | None = None
        self.photo_effect = "natural"
        self.fx_rng = random.Random()
        self.card_done_at = 0.0
        self.boot_t0: float | None = None  # the logo intro as the game opens (start_boot)

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
        }
        self.bg_field = self._build_light(band_title=None)
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
        self.dim.fill(theme.WHITE)
        self.time_grad = gradient_bar(vp.rect.w, vp.s(2.2), theme.DARK_BLUE, theme.TURQUOISE)
        self.panel = pygame.Surface(vp.cm_rect(22, 142, 278, 222).size, pygame.SRCALPHA).convert_alpha()
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
        base = {c: self.vp.s(self.cfg.targets[c].size_cm) for c in COLORS}
        self._prewarm = []
        for c in COLORS:
            for i in range(-8, 9):
                self._prewarm.append((c, int(round(base[c] * (1 + i * 0.02)))))

    def prewarm(self, budget_s: float = 0.003) -> None:
        """Build pulse-size target sprites a few at a time, so no frame stalls."""
        t0 = time.perf_counter()
        while self._prewarm and time.perf_counter() - t0 < budget_s:
            c, side = self._prewarm.pop()
            self.sprites.target(c, side, self.shadow)
            self._box_edge_sprite(side)

    def _logo(self, key: str, width_cm: float) -> pygame.Surface | None:
        w = self.vp.s(width_cm)
        img = self.sprites.assets.render(LOGOS[key][0], w)  # height from the artwork: uniform scale only
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

    def _art(self, name: str, w: int, h: int, fallback) -> pygame.Surface:
        """The SVG asset `name` at w x h pixels; fallback() draws the same shape in code."""
        img = self.sprites.image(name, w, h)
        return img if img is not None else fallback()

    def _blit_logo(self, surf, key: str, center: tuple[int, int], fallback_color) -> None:
        img = self.logos.get(key)
        if img is not None:
            surf.blit(img, img.get_rect(center=center))
        else:
            self.text.blit(surf, "کارگزاری مفید", "heading", self.vp.s(8), fallback_color, center)

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

    def _build_dark(self, keep_clear: list | None = None) -> pygame.Surface:
        vp = self.vp
        bg = self._wall(theme.DARK_BLUE)
        accents = [("plus", theme.TURQUOISE), ("times", theme.WHITE), ("slash", theme.TURQUOISE), ("minus", theme.WHITE),
                   ("dot", theme.TURQUOISE), ("plus", theme.WHITE), ("dot", theme.WHITE), ("slash", theme.WHITE)]
        dot_grid(bg, self.sprites, vp.rect.inflate(-vp.s(8), -vp.s(8)), vp.sf(12.5), vp.sf(0.6),
                 theme.PATTERN_BLUE, accents, vp.s(2.6), seed=5, keep_clear=keep_clear)
        return bg

    def _build_rush(self) -> pygame.Surface:
        vp = self.vp
        bg = self._wall(theme.BLUE_DEEP)
        field = vp.cm_rect(4, self.hud_h + 4, self.W - 4, self.footer_y - 3)
        dot_grid(bg, self.sprites, field, vp.sf(12.5), vp.sf(0.55), theme.BLUE_RAMP[1])
        band = vp.cm_rect(0, 0, self.W, self.hud_h)
        bg.fill(theme.PURPLE, band)
        dot_grid(bg, self.sprites, band.inflate(-vp.s(6), -vp.s(4)), vp.sf(6.0), vp.sf(0.45),
                 theme.lerp_color(theme.PURPLE, theme.WHITE, 0.16))
        y = self.footer_y
        pygame.draw.line(bg, theme.BLUE_RAMP[2], vp.px(12, y), vp.px(self.W - 12, y), max(1, vp.s(0.25)))
        self._blit_logo(bg, "fa_h_white_small", vp.px(self.W / 2, (y + self.H) / 2), theme.WHITE)
        return bg

    def _build_candles(self) -> pygame.Surface:
        """A strip of faint candlesticks, as wide as the field, that scrolls and repeats."""
        vp = self.vp
        w, h = self.rush_rect.size
        strip = pygame.Surface((w, h)).convert()
        key = (1, 2, 3)
        strip.fill(key)
        strip.set_colorkey(key)
        up = theme.lerp_color(theme.BLUE_DEEP, theme.TURQUOISE, 0.28)
        down = theme.lerp_color(theme.BLUE_DEEP, theme.RED, 0.28)
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
        for amp_cm, speed, width_cm, color in ((52.0, 1.0, 0.9, theme.lerp_color(theme.BLUE_DEEP, theme.TURQUOISE, 0.62)),
                                               (34.0, 1.45, 0.5, theme.lerp_color(theme.BLUE_DEEP, theme.WHITE, 0.2))):
            amp = vp.sf(amp_cm)
            pts = []
            for i in range(97):
                u = i / 96
                y = (0.55 * math.sin(8.0 * u + t * 1.5 * speed) + 0.3 * math.sin(21.0 * u - t * 2.3 * speed + 1.0)
                     + 0.15 * math.sin(53.0 * u + t * 3.7 * speed))
                pts.append((rect.x + int(u * rect.w), mid + int(amp * y)))
            pygame.draw.lines(surf, color, False, pts, max(1, vp.s(width_cm)))
        surf.set_clip(clip)

    # -- events from the game -----------------------------------------------------------
    def on_event(self, name: str, data: dict, flow: F.Flow, now: float) -> None:
        fx = self.fx
        if name == "hit":
            o: Outcome = data["outcome"]
            storm = flow.round is not None and flow.round.rush is not None
            color = self._on_field(theme.TARGET_COLORS[o.color], storm)
            half = o.target_side / 2
            fx.ripple(o.x, o.y, half, half + 20, 2.2, color, 0.55)
            fx.ripple(o.x, o.y, half * 0.6, half + 34, 0.9, color, 0.7, delay=0.08)
            fx.burst(o.x, o.y, color, count=16 if o.color == "red" else 12, start_r=half)
            fx.popup(o.x, o.y - 10, self.T[f"word_{o.color}"], "title", 15, color, dur=1.15, rise=16)
            fx.popup(o.x, o.y + 12, f"+{o.points}", "numbers", 11 if o.rush_multiplier > 1 else 9,
                     theme.WHITE if storm else theme.DARK_BLUE, dur=1.0, rise=12, delay=0.05)
            if o.multiplier > 1:
                fx.popup(o.x, o.y + 24, f"×{o.multiplier:g}", "numbers", 7, theme.WHITE if storm else theme.PURPLE, dur=0.95,
                         rise=10, delay=0.1)
            self.score_pop_t = now
            if flow.round is not None:
                self._spawn_rings(flow, now, set(o.spawned))
            if len(o.spawned) > 1:
                fx.flash(theme.RED, alpha=18, dur=0.35)
                if self.T.market_busier and not storm:
                    fx.banner(self.T.market_busier, theme.DARK_BLUE, 1.5)
            if o.level_up:
                self.badge_pop_t = now
                if self.cfg.ui.combo_banner and flow.round is not None:
                    fx.banner(self.T.combo_up.format(m=f"×{flow.round.next_multiplier:g}"), theme.PURPLE, 1.3)
        elif name == "miss":
            o = data["outcome"]
            if o.x >= 0:
                fx.ripple(o.x, o.y, 2, 17, 1.0, theme.BLUE_SOFT, 0.45)
                fx.ripple(o.x, o.y, 1, 7, 1.4, theme.GREY, 0.3)
            if o.combo_broken:
                self.badge_shake_t = now
        elif name == "rush_start":
            self.rush_title_t = now
            self.badge_pop_t = now
            fx.banner(self.T.rush_start.format(m=f"×{data['multiplier']:g}"), theme.PURPLE, 2.2)
        elif name == "rush_again":
            self.badge_pop_t = now
            fx.banner(self.T.rush_start.format(m=f"×{data['multiplier']:g}"), theme.PURPLE, 1.4)
        elif name == "rush_end":
            if data["reason"] == "time":
                fx.banner(self.T.rush_end, theme.DARK_BLUE, 1.6)
        elif name == "rush_spawn" and flow.round is not None:
            self._spawn_rings(flow, now, set(data["ids"]))
        elif name == "start_hit":
            sb = self.cfg.layout.start_button
            fx.ripple(sb.x_cm + layout_left(self.cfg), sb.y_cm + layout_top(self.cfg), sb.size_cm / 2, sb.size_cm / 2 + 40, 2.5, theme.TURQUOISE, 0.6)
        elif name == "go" and flow.round is not None:
            self._spawn_rings(flow, now, {tg.id for tg in flow.round.targets})
        elif name == "results":
            self.results_t0 = now
        elif name == "state":
            if data["state"] in (F.ATTRACT, F.RESULTS):
                fx.clear()
            # Screens cross-fade; the steps of a round (countdown to play to end) share one screen.
            if data["state"] not in (F.PLAYING, F.ENDING):
                self._fade_pending = True

    @staticmethod
    def _on_field(color, storm: bool):
        """An effect colour that stays visible on the dark Market Rush field."""
        return theme.BLUE_SOFT if storm and color == theme.DARK_BLUE else color

    def _spawn_rings(self, flow: F.Flow, now: float, ids: set[int]) -> None:
        """A thin ring that opens around each newly placed box."""
        storm = flow.round.rush is not None
        for tg in flow.round.targets:
            if tg.id in ids:
                cx, cy = tg.center_at(now + tg.spawn_dur)
                self.fx.ripple(cx, cy, tg.size * 0.6, tg.size * 1.0, 0.9, self._on_field(theme.TARGET_COLORS[tg.color], storm),
                               0.45, 0.1)

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
        if rnd is not None and rnd.started_at is not None:
            for tg in rnd.targets:
                side = tg.side_at(t)
                if side < 0.5:
                    continue
                cx, cy = tg.center_at(t)
                spr = self.sprites.target_scaled(tg.color, vp.sf(side), vp.s(tg.size), self.shadow)
                if storm > 0.0:  # the same size step as the box, so the edge stays even as it pulses
                    m = self.sprites.target_margin
                    side_px = spr.get_width() / (1.0 + 2 * m(100) / 100.0)
                    edge = self._box_edge_sprite(int(round(side_px)))
                    edge.set_alpha(int(255 * min(1.0, storm)))
                    surf.blit(edge, edge.get_rect(center=vp.px(cx, cy)))
                    edge.set_alpha(255)
                surf.blit(spr, spr.get_rect(center=vp.px(cx, cy)))
        field_rect = vp.cm_rect(0, self.hud_h, self.W, self.footer_y)
        self.fx.draw(surf, vp, field_rect, theme.lerp_color(theme.WHITE, theme.BLUE_DEEP, storm))
        self._hud(surf, flow, t)
        self.fx.draw_banner(surf, vp, self.hud_h + 1)
        st = flow.state
        if st == F.PLAYING and now - self.rush_title_t < 1.4:
            p = (now - self.rush_title_t) / 1.4
            self.text.blit(surf, self.T.rush_title, "title", vp.s(30), theme.WHITE, vp.px(self.W / 2, 205),
                           alpha=int(235 * (1.0 - p * p)))
        if st == F.COUNTDOWN:
            self._countdown(surf, flow, now)
        elif st == F.PLAYING and flow.elapsed(now) < 0.9:
            p = flow.elapsed(now) / 0.9
            img = self.text.render(self.T.go, "title", vp.s(34), theme.TURQUOISE)
            k = 0.75 + 0.35 * _ease_out(p * 2.5)
            img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
            img.set_alpha(int(255 * (1 - p**3)))
            surf.blit(img, img.get_rect(center=vp.px(self.W / 2, 205)))
        elif st == F.ENDING:
            p = min(1.0, flow.elapsed(now) / 0.35)
            self._dim_field(surf, int(228 * p))
            reason = self.T.end_time if rnd.end_reason == "time" else self.T.end_balls
            self.text.blit(surf, self.T.end_title, "title", vp.s(30), theme.DARK_BLUE, vp.px(self.W / 2, 190))
            self.text.blit(surf, reason, "body", vp.s(9), theme.TEXT_GREY, vp.px(self.W / 2, 228))

    def _box_edge_sprite(self, side_px: int) -> pygame.Surface:
        """The white edge behind a box on the dark Market Rush field."""
        w = side_px + 2 * self.vp.s(1.3)
        return self.sprites.rounded(w, w, int(w * 0.18), theme.WHITE)

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
        mid = self.hud_h / 2 + 6
        # score (right: first thing a right-to-left reader sees)
        self.text.blit(surf, self.T.score, "body", vp.s(6), theme.BLUE_PALE, vp.px(W - 14, 7), "topright")
        rush = rnd.rush_at(now)
        k = 1.0 + (0.28 if rush else 0.16) * max(0.0, 1.0 - (now - self.score_pop_t) / 0.3)
        img = self.text.render(str(rnd.score), "numbers", vp.s(31), theme.WHITE)
        if k > 1.001:
            img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
        surf.blit(img, img.get_rect(midright=vp.px(W - 14, mid)))
        # time (left)
        left = rnd.time_left(now)
        secs = math.ceil(left) if rnd.started_at is not None else int(rnd.duration)
        warn = rnd.started_at is not None and 0 < secs <= self.cfg.flow.time_warning_s and not rnd.finished
        self.text.blit(surf, self.T.time, "body", vp.s(6), theme.BLUE_PALE, vp.px(14, 7), "topleft")
        self.text.blit(surf, str(secs), "numbers", vp.s(31), theme.WHITE, vp.px(14, mid), "midleft")
        if warn and not rush:  # pulse to red: a red copy faded in on top (two cached surfaces, not one per shade)
            red = 0.5 - 0.5 * math.cos(left * math.tau)
            self.text.blit(surf, str(secs), "numbers", vp.s(31), theme.RED, vp.px(14, mid), "midleft",
                           alpha=int(255 * red))
        # the player's name: always there, but smaller than score and time
        name = self._player_name(flow)
        if name:
            self.text.blit(surf, name, "heading", vp.s(7), theme.WHITE, vp.px(W / 2, 11))
        # balls (only with a ball limit): the brand dot motif, filled = still to throw
        n = rnd.balls
        step = min(8.4, 150.0 / max(1, n))
        d = min(6.0, step * 0.72)
        x0 = W / 2 + (n - 1) * step / 2
        used = rnd.throws
        dpx = max(2, int(round(vp.sf(d))))
        for i in range(n):
            cx, cy = vp.px(x0 - i * step, 21)
            if i < n - used:
                dot = self._art("indicator.ball_remaining", dpx, dpx, lambda: self.sprites.dot(vp.sf(d / 2), theme.WHITE))
                surf.blit(dot, dot.get_rect(center=(cx, cy)))
            else:
                ring = self.sprites.image("indicator.ball_used", dpx, dpx)
                if ring is not None:
                    surf.blit(ring, ring.get_rect(center=(cx, cy)))
                else:
                    pygame.draw.aacircle(surf, theme.BLUE_PALE if rush else theme.BLUE_SOFT, (cx, cy), int(vp.sf(d / 2)), max(1, vp.s(0.5)))
        # Market Rush: seconds left, and its multiplier in a pill of its own
        pop = max(0.0, 1.0 - (now - self.badge_pop_t) / 0.35)
        if rush is not None:
            self.text.blit(surf, self.T.rush_hud.format(seconds=math.ceil(rush.ends_at - now)), "heading", vp.s(6.5),
                           theme.WHITE, vp.px(W / 2, 30))
            rw, rh = vp.s(46 * (1 + 0.15 * pop)), vp.s(13 * (1 + 0.15 * pop))
            rc = vp.px(W / 2 - 25, 46)
            pill = self.sprites.rounded(rw, rh, rh // 2, theme.WHITE)
            surf.blit(pill, pill.get_rect(center=rc))
            self.text.blit(surf, self.T.rush_pill.format(m=f"×{float(self.cfg.rush.score_multiplier):g}"), "heading",
                           vp.s(7 * (1 + 0.15 * pop)), theme.DARK_BLUE, rc)
        # multiplier badge
        if rnd.levels:
            m = rnd.next_multiplier
            shake = max(0.0, 1.0 - (now - self.badge_shake_t) / 0.4)
            bw, bh = vp.s(46 * (1 + 0.15 * pop)), vp.s(13 * (1 + 0.15 * pop))
            cx, cy = vp.px(W / 2 + (25 if rush else 0) + 3 * shake * math.sin((now - self.badge_shake_t) * 50), 46 if rush else 44)
            label = f"{self.T.multiplier} ×{m:g}"
            if m > 1 and rush is not None:  # the band is purple now
                pill = self.sprites.rounded(bw, bh, bh // 2, theme.BLUE_DEEP)
                tcol = theme.WHITE
            elif m > 1:
                pill = self._art("indicator.multiplier_active", bw, bh,
                                 lambda: self.sprites.rounded(bw, bh, bh // 2, theme.PURPLE))
                tcol = theme.WHITE
            else:
                pill = self._art("indicator.multiplier_inactive", bw, bh,
                                 lambda: self.sprites.rounded(bw, bh, bh // 2, theme.BLUE_SOFT, border=max(1, vp.s(0.5))))
                tcol = theme.BLUE_PALE
            surf.blit(pill, pill.get_rect(center=(cx, cy)))
            self.text.blit(surf, label, "heading", vp.s(7 * (1 + 0.15 * pop)), tcol, (cx, cy))
        elif rnd.balls:
            self.text.blit(surf, f"{rnd.throws_left} {self.T.balls}", "heading", vp.s(7), theme.BLUE_PALE, vp.px(W / 2, 44))
        # time bar under the band, draining towards the right (right-to-left reading)
        frac = left / rnd.duration if rnd.duration else 0.0
        bar = vp.cm_rect(0, self.hud_h, W, self.hud_h + 2.2)
        surf.fill(theme.GREY_LIGHT, bar)
        w = int(bar.w * frac)
        if w > 0:
            surf.blit(self.time_grad, (bar.right - w, bar.y), pygame.Rect(bar.w - w, 0, w, bar.h))
        if rush is not None:  # what is left of Market Rush, under the time bar
            track = vp.cm_rect(0, self.hud_h + 2.2, W, self.hud_h + 3.8)
            surf.fill(theme.BLUE_RAMP[1], track)
            w = int(track.w * max(0.0, (rush.ends_at - now) / max(0.1, float(self.cfg.rush.duration_s))))
            if w > 0:
                surf.fill(theme.RED, pygame.Rect(track.right - w, track.y, w, track.h))

    def _countdown(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        vp = self.vp
        self._dim_field(surf, 170)
        n = flow.countdown_value(now)
        frac = 1.0 - ((self.cfg.flow.countdown_s - flow.elapsed(now)) % 1.0)
        center = vp.px(self.W / 2, 215)
        ring = vp.sf(42 + 10 * _ease_out(frac))
        col = theme.lerp_color(theme.TURQUOISE, theme.WHITE, frac)
        pygame.draw.aacircle(surf, col, center, int(ring), max(1, vp.s(1.6)))
        self.text.blit(surf, self.T.get_ready, "heading", vp.s(12), theme.DARK_BLUE, vp.px(self.W / 2, 140))
        img = self.text.render(str(n), "numbers", vp.s(80), theme.DARK_BLUE)
        k = 1.25 - 0.25 * _ease_out(frac * 3)
        img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
        surf.blit(img, img.get_rect(center=center))

    # -- Mofid logo intro -------------------------------------------------------------------
    # Before every countdown (flow state INTRO) and once as the game opens. The logo, in its
    # brand colours, settles in and holds while the brand symbols gather around it; then it zooms
    # in towards the players and fades, and the symbols fly out with it. The screen cross-fade
    # then uncovers the countdown (or the attract screen). Flat shapes and eased movement only,
    # no flashing.
    INTRO_LOGO_CM = 150  # the logo's width while it holds: half the wall
    INTRO_ZOOM = 3.2  # how much larger it is at the end, long after it has faded
    INTRO_MARKS = (  # (symbol, angle in degrees, distance in cm, colour); clear of the logo
        ("plus", -150, 120, "TURQUOISE"), ("dot", -110, 91, "BLUE_SOFT"), ("times", -62, 96, "DARK_BLUE"),
        ("slash", -24, 125, "TURQUOISE"), ("minus", 16, 122, "BLUE_SOFT"), ("plus", 58, 99, "DARK_BLUE"),
        ("dot", 102, 88, "TURQUOISE"), ("times", 142, 109, "BLUE_SOFT"), ("minus", 178, 127, "DARK_BLUE"),
        ("slash", -84, 135, "TURQUOISE"),
    )

    def _build_intro(self) -> None:
        vp = self.vp
        self.bg_intro = self._wall(theme.WHITE)
        dot_grid(self.bg_intro, self.sprites, vp.rect.inflate(-vp.s(8), -vp.s(8)), vp.sf(12.5), vp.sf(0.55),
                 theme.GREY_LIGHT)
        # The artwork at a few sizes, each drawn from the SVG: every frame scales the nearest one
        # by less than a quarter, so the logo stays sharp and the zoom costs little.
        self.intro_logos: list[tuple[float, pygame.Surface]] = []
        k = 1.06
        while True:
            img = self._logo("fa_s", width_cm=self.INTRO_LOGO_CM * k)
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
            self.text.blit(surf, "کارگزاری مفید", "heading", vp.s(18 * k), theme.DARK_BLUE, center,
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
        vp, W, T = self.vp, self.W, self.T
        surf.blit(self.bg_dark, (0, 0))
        self._blit_logo(surf, "fa_s_white", vp.px(W / 2, 44), theme.WHITE)
        self.text.blit(surf, T.title, "title", vp.s(38), theme.WHITE, vp.px(W / 2, 94))
        self.text.blit(surf, T.subtitle, "body", vp.s(8), theme.BLUE_PALE, vp.px(W / 2, 129))
        sb = self.cfg.layout.start_button
        if self.cfg.flow.start_by_throw:  # pulse ring first, so the card and the box sit on top of it
            beat = (now % 1.6) / 1.6
            ring_r = vp.sf(sb.size_cm * 0.56 + 16 * _ease_out(beat))
            pygame.draw.aacircle(surf, theme.lerp_color(theme.TURQUOISE, theme.DARK_BLUE, beat),
                                 vp.px(sb.x_cm, sb.y_cm), int(ring_r), max(1, vp.s(1.2)))

        board = overlays.get("leaderboard") or []
        period = self.cfg.flow.attract_panel_s
        show_board = bool(board) and self.cfg.results.leaderboard_size > 0 and int(now / period) % 2 == 1
        phase = (now % period) / period
        fade = min(1.0, phase * period / 0.5, (1.0 - phase) * period / 0.5)
        self.panel.fill((0, 0, 0, 0))
        prect = self.panel.get_rect()
        self.panel.blit(self._art("panel.attract_card", prect.w, prect.h,
                                  lambda: self.sprites.rounded(prect.w, prect.h, vp.s(5), theme.WHITE)), (0, 0))
        if show_board:
            self._panel_board(self.panel, board)
        else:
            self._panel_how(self.panel, now)
        if bool(board) and fade < 1.0:
            self.panel.set_alpha(int(255 * (0.25 + 0.75 * fade)))
        else:
            self.panel.set_alpha(255)
        surf.blit(self.panel, vp.px(22, 142))

        if self.cfg.flow.start_by_throw:
            side = sb.size_cm * (1 + 0.03 * math.sin(now * math.tau / 1.6))
            px = vp.s(side)
            box = self._art("button.start", px, px, lambda: self.sprites.rounded(px, px, vp.s(side * 0.18), theme.TURQUOISE))
            surf.blit(box, box.get_rect(center=vp.px(sb.x_cm, sb.y_cm)))
            self.text.blit(surf, T.start, "title", vp.s(21), theme.WHITE, vp.px(sb.x_cm, sb.y_cm))
            self.text.blit(surf, T.cta_throw, "body", vp.s(7.5), theme.WHITE, vp.px(W / 2, sb.y_cm + sb.size_cm / 2 + 14))
        else:
            self.text.blit(surf, T.cta_operator, "heading", vp.s(11), theme.WHITE, vp.px(W / 2, sb.y_cm))
        player = flow.player
        if player and (player.get("name") or player.get("id")):
            label = f"{T.next_player}: {_short(player.get('name') or player.get('id'), 24)}"
            self.text.blit(surf, label, "heading", vp.s(7.5), theme.TURQUOISE_RAMP[5], vp.px(W / 2, 362))
        self._steady_progress(surf, theme.TURQUOISE, theme.WHITE)
        self.fx.draw(surf, vp, vp.rect, theme.DARK_BLUE)

    def _panel_how(self, panel: pygame.Surface, now: float) -> None:
        vp = self.vp
        # three boxes at their real relative sizes, gently moving, with their words
        slots = (("green", 205), ("blue", 128), ("red", 60))  # x in panel cm, right to left
        for color, x in slots:
            size = self.cfg.targets[color].size_cm * 0.82
            bob = 2.2 * math.sin(now * 1.7 + x)
            spr = self.sprites.target(color, vp.s(size), False)
            panel.blit(spr, spr.get_rect(center=(vp.s(x), vp.s(32 + bob))))
            self.text.blit(panel, self.T[f"word_{color}"], "heading", vp.s(8.5), theme.TARGET_COLORS[color],
                           (vp.s(x), vp.s(68)))

    def _panel_board(self, panel: pygame.Surface, board: list[dict]) -> None:
        vp = self.vp
        pw = panel.get_width()
        self.text.blit(panel, self.T.leaderboard, "heading", vp.s(8), theme.DARK_BLUE, (pw // 2, vp.s(11)))
        show_names = self.cfg.results.show_names
        for i, rec in enumerate(board[:5]):
            y = vp.s(26 + i * 12.5)
            badge = self._art("indicator.rank_first" if i == 0 else "indicator.rank", vp.s(9), vp.s(9),
                              lambda: self.sprites.rounded(vp.s(9), vp.s(9), vp.s(4.5),
                                                           theme.TURQUOISE if i == 0 else theme.BLUE_PALE))
            panel.blit(badge, badge.get_rect(center=(pw - vp.s(14), y)))
            self.text.blit(panel, str(i + 1), "numbers", vp.s(5.5), theme.WHITE if i == 0 else theme.DARK_BLUE,
                           (pw - vp.s(14), y))
            name = _short(rec.get("player_name") or "", 18) if show_names else ""
            self.text.blit(panel, name or self.T.guest, "body", vp.s(6.5), theme.DARK_BLUE, (pw - vp.s(24), y), "midright")
            self.text.blit(panel, str(rec.get("score", 0)), "numbers", vp.s(7), theme.DARK_BLUE, (vp.s(12), y), "midleft")

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
        vp, W, T, cfg = self.vp, self.W, self.T, self.cfg
        surf.blit(self.bg_light, (0, 0))
        p = min(1.0, flow.elapsed(now) / max(0.1, cfg.flow.instructions_s))
        bar = vp.cm_rect(0, self.hud_h, W, self.hud_h + 2.2)
        surf.fill(theme.GREY_LIGHT, bar)
        w = int(bar.w * p)
        if w:
            surf.blit(self.time_grad, (bar.right - w, bar.y), pygame.Rect(bar.w - w, 0, w, bar.h))
        rule = T.rule_balls if cfg.round.balls else T.rule_time
        self.text.blit(surf, rule.format(balls=cfg.round.balls, seconds=int(cfg.round.duration_s)),
                       "heading", vp.s(10), theme.DARK_BLUE, vp.px(W / 2, 86))
        for i, color in enumerate(("green", "blue", "red")):
            y = 128 + i * 57
            appear = _ease_out((flow.elapsed(now) - 0.15 * i) / 0.4)
            if appear <= 0:
                continue
            size = cfg.targets[color].size_cm  # true size on the wall
            spr = self.sprites.target(color, vp.s(size * appear), self.shadow)
            surf.blit(spr, spr.get_rect(center=vp.px(242, y)))
            self.text.blit(surf, T[f"word_{color}"], "title", vp.s(12), theme.TARGET_COLORS[color], vp.px(200, y - 8), "midright")
            self.text.blit(surf, T[f"meaning_{color}"], "body", vp.s(5.8), theme.TEXT_GREY, vp.px(200, y + 9), "midright")
            pill = self._art("indicator.points", vp.s(50), vp.s(15),
                             lambda: self.sprites.rounded(vp.s(50), vp.s(15), vp.s(7.5), theme.TURQUOISE_PALE))
            surf.blit(pill, pill.get_rect(center=vp.px(42, y - (5 if color == "red" else 0))))
            self.text.blit(surf, f"{cfg.targets[color].points} {T.points_unit}", "heading", vp.s(6.5), theme.DARK_BLUE,
                           vp.px(42, y - (5 if color == "red" else 0)))
            if color == "red" and cfg.targets.red.spawn_on_hit:
                self.text.blit(surf, T.red_extra, "body", vp.s(5.2), theme.DARK_BLUE, vp.px(42, y + 10))
        lines = [T.rule_move]
        if cfg.rush.enabled and cfg.rush.max_per_round > 0:
            lines.append(T.rule_rush.format(seconds=int(cfg.rush.duration_s), m=f"×{float(cfg.rush.score_multiplier):g}"))
        if cfg.combo.enabled and cfg.combo.levels:
            top = max(lv["multiplier"] for lv in cfg.combo.levels)
            lines.append(T.rule_combo.format(max=f"×{top:g}"))
        lines.append(T.rule_positive)
        y = 291
        for line in lines:
            y = self.text.blit_lines(surf, line, "body", vp.s(6.3), theme.DARK_BLUE, vp.px(W / 2, y), vp.s(272), 1.3)
            y = int((y - vp.rect.y) / vp.ppc + 4)

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

        y = 166 + len(lines) * 6.6 * 1.35 + 30  # the hit cards follow the sentence, however long it is
        hits = rec.get("hits", {})
        for i, (color, dx) in enumerate((("green", 43), ("blue", 0), ("red", -43))):
            start = t_cards + 0.12 * i
            s = _ease_back(_phase(t, start, 0.45))
            if s <= 0:
                continue
            cx = X + dx
            side = vp.s(38 * s)
            card = pygame.Rect(0, 0, side, side)
            card.center = vp.px(cx, y)
            pygame.draw.rect(surf, theme.WHITE, card, border_radius=max(1, int(vp.s(4) * s)))
            a = int(255 * _phase(t, start + 0.2, 0.25))
            if a <= 0:
                continue
            spr = self.sprites.target(color, vp.s(11), False)
            spr.set_alpha(a)
            surf.blit(spr, spr.get_rect(center=vp.px(cx, y - 8)))
            spr.set_alpha(255)
            n = int(round(hits.get(color, 0) * _ease_out(_phase(t, start + 0.2, 0.5))))
            self.text.blit(surf, str(n), "numbers", vp.s(8.5), theme.DARK_BLUE, vp.px(cx, y + 4), alpha=a)
            self.text.blit(surf, T[f"word_{color}"], "body", vp.s(3.8), theme.TEXT_GREY, vp.px(cx, y + 13), alpha=a)

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
        surf.blit(self.bg_dark, (0, 0))
        self._blit_logo(surf, "fa_h_white", vp.px(W / 2, 26), theme.WHITE)
        name = _short(rec.get("player_name") or "", cfg.flow.name_max_chars)
        if name and cfg.results.show_names:
            self.text.blit(surf, name, "title", vp.s(18), theme.WHITE, vp.px(W / 2, 57))
        self.text.blit(surf, T.results_title, "body", vp.s(7.5), theme.BLUE_PALE, vp.px(W / 2, 77))
        final = rec.get("score", 0)
        score = int(round(final * _ease_out(t / 1.2)))
        self.text.blit(surf, str(score), "numbers", vp.s(56), theme.WHITE, vp.px(W / 2, 109), cache=score == final)

        # the play style, the screen's message, once the score has counted up
        appear = _ease_out((t - 0.7) / 0.5)
        if profile and appear > 0:
            shift = 7 * (1 - appear)
            card = vp.cm_rect(16, 142 + shift, W - 16, 226 + shift)
            surf.blit(self.sprites.rounded(card.w, card.h, vp.s(5), theme.WHITE), card.topleft)
            self.text.blit(surf, T.profile_title, "body", vp.s(6), theme.TEXT_GREY, vp.px(W / 2, 153 + shift))
            self.text.blit(surf, profile["label"], "title", vp.s(15), theme.TURQUOISE_RAMP[0], vp.px(W / 2, 170 + shift))
            self.text.blit_lines(surf, profile["statement"], "body", vp.s(7.5), theme.DARK_BLUE, vp.px(W / 2, 184 + shift),
                                 vp.s(246), 1.35)

        hits = rec.get("hits", {})
        for i, (color, x) in enumerate((("green", 245), ("blue", 150), ("red", 55))):
            appear = _ease_out((t - 1.1 - 0.12 * i) / 0.4)
            if appear <= 0:
                continue
            cy = 256 + 8 * (1 - appear)
            card = self._art("panel.results_card", vp.s(80), vp.s(46),
                             lambda: self.sprites.rounded(vp.s(80), vp.s(46), vp.s(4), theme.WHITE))
            surf.blit(card, card.get_rect(center=vp.px(x, cy)))
            spr = self.sprites.target(color, vp.s(13), False)
            surf.blit(spr, spr.get_rect(center=vp.px(x, cy - 12)))
            self.text.blit(surf, str(hits.get(color, 0)), "numbers", vp.s(11), theme.DARK_BLUE, vp.px(x, cy + 3))
            self.text.blit(surf, T[f"word_{color}"], "body", vp.s(5), theme.TEXT_GREY, vp.px(x, cy + 15))

        if t > 1.5:
            self.text.blit(surf, T.results_hits.format(hits=rec.get("total_hits", 0), throws=rec.get("throws", 0)),
                           "heading", vp.s(7), theme.WHITE, vp.px(W / 2, 288))
            rush = rec.get("rush") or {}
            if rush.get("count"):
                self.text.blit(surf, T.results_rush.format(hits=rush.get("hits", 0), points=rush.get("points", 0)), "body", vp.s(6),
                               theme.BLUE_PALE, vp.px(W / 2, 299))
            pills = []
            if cfg.combo.enabled and cfg.combo.levels:
                pills.append((f"{T.results_best} ×{rec.get('best_multiplier', 1):g}", theme.PURPLE))
            rank = flow.rank
            if rank is not None and rank <= max(1, cfg.results.leaderboard_size):
                pills.append((T.results_rank.format(rank=rank), theme.TURQUOISE))
            for i, (label, color) in enumerate(pills):  # side by side when there are two
                x = W / 2 + (58 - 116 * i if len(pills) == 2 else 0)
                self._pill(surf, label, color, theme.WHITE, (x, 314), 6.5)
        if profile:
            self.text.blit(surf, T.profile_note, "body", vp.s(4.6), theme.BLUE_PALE, vp.px(W / 2, 334))
        self._results_footer(surf, flow, now)

    def _results_footer(self, surf: pygame.Surface, flow: F.Flow, now: float) -> None:
        vp, W, cfg = self.vp, self.W, self.cfg
        p = min(1.0, (now - flow.since) / max(0.1, cfg.flow.results_s))
        self.text.blit(surf, self.T.results_next, "body", vp.s(6.5), theme.BLUE_PALE, vp.px(W / 2, 350))
        bar = vp.cm_rect(60, 361, W - 60, 362.6)
        surf.fill(theme.BLUE_RAMP[2], bar)
        w = int(bar.w * (1 - p))
        if w:
            surf.fill(theme.TURQUOISE, pygame.Rect(bar.right - w, bar.y, w, bar.h))
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
        self.text.blit(surf, title, "title", vp.s(30), theme.DARK_BLUE, vp.px(self.W / 2, self.H / 2))
        if sub:
            self.text.blit(surf, sub, "body", vp.s(8), theme.TEXT_GREY, vp.px(self.W / 2, self.H / 2 + 30))

    def _badges(self, surf, overlays: dict) -> None:
        """Operator-facing status pills along the bottom edge."""
        vp = self.vp
        y = self.H - 4.5
        if overlays.get("simulation"):
            img = self.text.render(f"{self.T.simulation} - SIMULATION", "bold", vp.s(3.4), theme.WHITE)
            pill = self.sprites.rounded(img.get_width() + vp.s(6), int(img.get_height() * 1.2), vp.s(2.5), theme.PURPLE)
            r = pill.get_rect(bottomleft=vp.px(3, self.H - 2))
            surf.blit(pill, r)
            surf.blit(img, img.get_rect(center=r.center))
        if overlays.get("sensor_offline"):
            img = self.text.render(self.T.sensor_offline, "bold", vp.s(3.4), theme.WHITE)
            pill = self.sprites.rounded(img.get_width() + vp.s(6), int(img.get_height() * 1.2), vp.s(2.5), theme.RED)
            r = pill.get_rect(bottomright=vp.px(self.W - 3, self.H - 2))
            surf.blit(pill, r)
            surf.blit(img, img.get_rect(center=r.center))
        msg = overlays.get("notice")
        if msg:
            self.text.blit(surf, msg, "latin", vp.s(3.2), theme.RED, vp.px(self.W / 2, y), localize=False)
