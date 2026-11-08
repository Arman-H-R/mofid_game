"""Anti-aliased brand shapes, cached sprites and background patterns.

Shapes come from the SVG files in assets/ (see assets.py) when they are
available, rasterised once per size; otherwise they are drawn in code,
supersampled and scaled down. Either way they are cached. Everything that
changes size every frame (pulsing targets) is quantised so the caches stay
small and bounded.
"""
from __future__ import annotations

import math
import random
from collections import OrderedDict

import numpy as np
import pygame

from . import icons, theme
from .assets import AssetLibrary

SS = 3  # supersampling factor for sprites


class LRU(OrderedDict):
    """Least-recently-used surface cache, bounded by entry count and by pixel memory."""

    def __init__(self, size: int, max_bytes: int = 64 << 20):
        super().__init__()
        self.size = size
        self.max_bytes = max_bytes
        self.bytes = 0

    @staticmethod
    def _cost(value) -> int:
        return value.get_width() * value.get_height() * 4 if isinstance(value, pygame.Surface) else 0

    def put(self, key, value):
        old = self.pop(key, None)
        if old is not None:
            self.bytes -= self._cost(old)
        self[key] = value
        self.bytes += self._cost(value)
        while len(self) > 1 and (len(self) > self.size or self.bytes > self.max_bytes):
            _, evicted = self.popitem(last=False)
            self.bytes -= self._cost(evicted)
        return value

    def clear(self) -> None:
        super().clear()
        self.bytes = 0

    def hit(self, key):
        value = self.get(key)
        if value is not None:
            self.move_to_end(key)
        return value


def _convert(surf: pygame.Surface) -> pygame.Surface:
    return surf.convert_alpha() if pygame.display.get_surface() is not None else surf


def _bar(cx: float, cy: float, length: float, thick: float, angle_deg: float) -> list[tuple[float, float]]:
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    hl, ht = length / 2, thick / 2
    pts = [(-hl, -ht), (hl, -ht), (hl, ht), (-hl, ht)]
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in pts]


def draw_symbol(surf: pygame.Surface, kind: str, center: tuple[float, float], size: float, color) -> None:
    """Brand graphic elements (page 23) on a module of `size`: plus, minus, times,
    slash, dot. Bars are size / 3 thick with square ends; the times sign is the
    plus turned 45 degrees. Draws directly (aliased); Sprites.symbol() is smooth.
    Fallback for assets/brand/elements/*.svg."""
    cx, cy = center
    t = size / 3
    if kind == "plus":
        pygame.draw.polygon(surf, color, _bar(cx, cy, size, t, 0))
        pygame.draw.polygon(surf, color, _bar(cx, cy, size, t, 90))
    elif kind == "minus":
        pygame.draw.polygon(surf, color, _bar(cx, cy, size, t, 0))
    elif kind == "times":
        pygame.draw.polygon(surf, color, _bar(cx, cy, size, t, 45))
        pygame.draw.polygon(surf, color, _bar(cx, cy, size, t, -45))
    elif kind == "slash":
        pygame.draw.polygon(surf, color, _bar(cx, cy, size, t, -45))
    elif kind == "dot":
        pygame.draw.circle(surf, color, (cx, cy), size * 0.5)


def draw_icon(surf: pygame.Surface, kind: str, side: float, color) -> None:
    """A target icon on a tile of `side` pixels at the surface's corner. Fallback
    for assets/gameplay/target-*.svg, from the same shapes (icons.py)."""
    k = side / 100.0
    for poly in icons.polygons(kind):
        pygame.draw.polygon(surf, color, [(x * k, y * k) for x, y in poly])


PRODUCT_RADIUS = 0.1665  # the tiles' corner radius, as a share of their width (MOFID_UI v4.1)
RING_GAP = 0.05  # the thin white ring around a tile on the field: this far out, as a share of the width
RING_ALPHA = 72  # 28 % white


class Sprites:
    def __init__(self, assets: AssetLibrary | None = None):
        self.assets = assets or AssetLibrary()
        self._targets = LRU(220)
        self._symbols = LRU(160)
        self._rounded = LRU(160)
        self._dots = LRU(16)
        self._images = LRU(160)
        self._products = LRU(400)
        self._product_src: dict[str, pygame.Surface | None] = {}
        self.product_dir = None  # assets/products, set by set_products
        self.product_files: dict[str, str] = {}
        self.product_aspect: dict[str, float] = {}

    def clear(self) -> None:
        for c in (self._targets, self._symbols, self._rounded, self._dots, self._images, self._products):
            c.clear()

    def set_budget(self, canvas_bytes: int) -> None:
        """Scale the memory caps with the canvas (a 4K wall needs bigger sprites)."""
        self._targets.max_bytes = 6 * canvas_bytes
        self._symbols.max_bytes = canvas_bytes
        self._rounded.max_bytes = 6 * canvas_bytes
        self._dots.max_bytes = canvas_bytes
        self._images.max_bytes = 6 * canvas_bytes
        self._products.max_bytes = 8 * canvas_bytes

    # -- the 13 product tiles ------------------------------------------------------------
    def set_products(self, folder, products: list[dict]) -> None:
        """The product tiles: assets/products and cfg.products ({key, file, aspect})."""
        self.product_dir = folder
        self.product_files = {p["key"]: p["file"] for p in products}
        self.product_aspect = {p["key"]: float(p.get("aspect", 1.0)) for p in products}
        self._product_src.clear()
        self._products.clear()

    def _product_source(self, key: str) -> pygame.Surface | None:
        if key not in self._product_src:
            src = None
            name = self.product_files.get(key)
            if self.product_dir is not None and name:
                try:
                    src = pygame.image.load(str(self.product_dir / name))
                    src = _convert(src)
                except (pygame.error, FileNotFoundError, OSError):
                    src = None
            self._product_src[key] = src
        return self._product_src[key]

    @staticmethod
    def ring_margin(w: int) -> int:
        """Room around a tile for its ring, in pixels, on each side."""
        return max(2, int(math.ceil(w * (RING_GAP + 0.02))))

    def product(self, key: str, w: int, ring: bool = False) -> pygame.Surface:
        """The product's tile, w pixels wide, never recoloured or redrawn. With ``ring``, the
        thin 28 % white ring around it (and ring_margin(w) of room on each side)."""
        w = max(4, int(w))
        cache_key = (key, w, ring)
        surf = self._products.hit(cache_key)
        if surf is not None:
            return surf
        aspect = self.product_aspect.get(key, 0.92)
        h = max(3, int(round(w * aspect)))
        src = self._product_source(key)
        if src is not None:
            tile = pygame.transform.smoothscale(src, (w, h))
        else:  # no artwork: a plain tile in the field's turquoise, the same shape
            tile = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(tile, theme.TURQUOISE, tile.get_rect(), border_radius=int(w * PRODUCT_RADIUS))
        if not ring:
            return self._products.put(cache_key, _convert(tile))
        m = self.ring_margin(w)
        surf = pygame.Surface((w + 2 * m, h + 2 * m), pygame.SRCALPHA)
        gap = w * RING_GAP
        big = pygame.Surface(((w + 2 * m) * 2, (h + 2 * m) * 2), pygame.SRCALPHA)
        r = pygame.Rect(0, 0, int((w + 2 * gap) * 2), int((h + 2 * gap) * 2))
        r.center = big.get_rect().center
        pygame.draw.rect(big, (255, 255, 255, RING_ALPHA), r, width=max(2, int(w * 0.016 * 2)),
                         border_radius=int((w * PRODUCT_RADIUS + gap) * 2))
        surf.blit(pygame.transform.smoothscale(big, surf.get_size()), (0, 0))
        surf.blit(tile, (m, m))
        return self._products.put(cache_key, _convert(surf))

    def product_scaled(self, key: str, w_px: float, base_px: int, ring: bool = True) -> pygame.Surface:
        """A tile drawn at w_px: near its base size the width is quantised (the gentle pulse),
        transient sizes (pop-in, leaving) are scaled from the base sprite."""
        f = w_px / max(1, base_px)
        q = round(f / 0.02) * 0.02
        if 0.9 <= q <= 1.1:
            return self.product(key, int(round(base_px * q)), ring)
        base = self.product(key, base_px, ring)
        return pygame.transform.smoothscale(base, (max(2, int(round(base.get_width() * f))),
                                                   max(2, int(round(base.get_height() * f)))))

    # -- SVG artwork ------------------------------------------------------------------
    def image(self, name: str, w: int, h: int | None = None) -> pygame.Surface | None:
        """The asset `name` at w x h pixels (cached), or None so the caller draws it in code."""
        key = (name, int(w), h)
        surf = self._images.hit(key)
        if surf is not None:
            return surf
        surf = self.assets.render(name, w, h)
        return None if surf is None else self._images.put(key, surf)

    # -- targets ------------------------------------------------------------------
    @staticmethod
    def target_margin(side: int) -> int:
        return max(2, int(side * 0.14))

    def target(self, color_key: str, side: int, shadow: bool = True) -> pygame.Surface:
        """Rounded square in the target colour with its white icon.
        The sprite includes a margin (target_margin) for the soft shadow."""
        side = max(4, side)
        key = (color_key, side, shadow)
        surf = self._targets.hit(key)
        if surf is not None:
            return surf
        m = self.target_margin(side)
        full = side + 2 * m
        surf = pygame.Surface((full, full), pygame.SRCALPHA)
        tile = self.assets.render(f"target.{color_key}", side, side)
        if tile is None:
            big = pygame.Surface((side * SS, side * SS), pygame.SRCALPHA)
            pygame.draw.rect(big, theme.TARGET_COLORS[color_key], big.get_rect(), border_radius=int(side * 0.18 * SS))
            draw_icon(big, theme.TARGET_SYMBOLS[color_key], side * SS, theme.WHITE)
            tile = pygame.transform.smoothscale(big, (side, side))
        if shadow:  # the tile silhouette in deep blue at 27 %, a little lower, blurred
            sh = pygame.Surface((full, full), pygame.SRCALPHA)
            alpha = pygame.surfarray.array_alpha(tile).astype(np.uint16)
            y0 = m + int(side * 0.05)
            rgb = pygame.surfarray.pixels3d(sh)
            rgb[m:m + side, y0:y0 + side][alpha > 0] = theme.BLUE_DEEP
            del rgb
            a = pygame.surfarray.pixels_alpha(sh)
            a[m:m + side, y0:y0 + side] = alpha * 70 // 255
            del a
            sh = pygame.transform.gaussian_blur(sh, max(1, int(side * 0.06)))
            surf.blit(sh, (0, 0))
        surf.blit(tile, (m, m))
        return self._targets.put(key, _convert(surf))

    def target_scaled(self, color_key: str, side_px: float, base_px: int, shadow: bool = True) -> pygame.Surface:
        """Sprite for a target drawn at side_px. Sizes near the base size are
        quantised (pulse); transient sizes (pop-in) are scaled from the base sprite."""
        f = side_px / max(1, base_px)
        q = round(f / 0.02) * 0.02
        if 0.7 <= q <= 1.3:
            return self.target(color_key, int(round(base_px * q)), shadow)
        base = self.target(color_key, base_px, shadow)
        size = max(2, int(round(base.get_width() * f)))
        return pygame.transform.smoothscale(base, (size, size))

    # -- symbols / shapes -----------------------------------------------------------
    def symbol(self, kind: str, size: int, color) -> pygame.Surface:
        size = max(3, size)
        key = (kind, size, tuple(color))
        surf = self._symbols.hit(key)
        if surf is not None:
            return surf
        pad = int(size * 0.3)
        shape = self.assets.render(f"symbol.{kind}", size, size)
        if shape is not None:  # the SVG gives the shape; paint it in the colour asked for
            pygame.surfarray.pixels3d(shape)[...] = tuple(color)[:3]
            surf = pygame.Surface((size + 2 * pad,) * 2, pygame.SRCALPHA)
            surf.blit(shape, (pad, pad), special_flags=pygame.BLEND_RGBA_MAX)  # a copy, no blending
        else:
            big = pygame.Surface(((size + 2 * pad) * SS,) * 2, pygame.SRCALPHA)
            c = big.get_width() / 2
            draw_symbol(big, kind, (c, c), size * SS, color)
            surf = pygame.transform.smoothscale(big, (size + 2 * pad,) * 2)
        return self._symbols.put(key, _convert(surf))

    def rounded(self, w: int, h: int, radius: int, color, border: int = 0) -> pygame.Surface:
        key = (w, h, radius, tuple(color), border)
        surf = self._rounded.hit(key)
        if surf is not None:
            return surf
        ss = 2
        big = pygame.Surface((max(1, w) * ss, max(1, h) * ss), pygame.SRCALPHA)
        pygame.draw.rect(big, color, big.get_rect(), width=border * ss, border_radius=radius * ss)
        surf = pygame.transform.smoothscale(big, (max(1, w), max(1, h)))
        return self._rounded.put(key, _convert(surf))

    def dot(self, radius: float, color) -> pygame.Surface:
        key = (round(radius, 1), tuple(color))
        surf = self._dots.hit(key)
        if surf is not None:
            return surf
        d = max(2, int(math.ceil(radius * 2)) + 2)
        big = pygame.Surface((d * SS, d * SS), pygame.SRCALPHA)
        pygame.draw.circle(big, color, (d * SS / 2, d * SS / 2), radius * SS)
        surf = pygame.transform.smoothscale(big, (d, d))
        return self._dots.put(key, _convert(surf))


def dot_grid(
    surf: pygame.Surface,
    sprites: Sprites,
    rect: pygame.Rect,
    spacing: float,
    radius: float,
    color,
    accents: list | None = None,
    accent_size: int = 0,
    seed: int = 7,
    keep_clear: list[pygame.Rect] | None = None,
) -> None:
    """The brand's dot pattern (pages 22 and 24): an even grid of small dots,
    with a few grid points replaced by brand symbols in an accent colour.
    Symbols that would fall inside a keep_clear rect move to the next free point."""
    dot = sprites.dot(radius, color)
    dw = dot.get_width() / 2
    cols = max(1, int(rect.w // spacing))
    rows = max(1, int(rect.h // spacing))
    ox = rect.x + (rect.w - (cols - 1) * spacing) / 2
    oy = rect.y + (rect.h - (rows - 1) * spacing) / 2
    rng = random.Random(seed)
    accent_at: dict[tuple[int, int], tuple[str, tuple]] = {}
    if accents:
        for kind, col in accents:
            for _ in range(200):
                c, r = rng.randrange(cols), rng.randrange(rows)
                if not any(k.collidepoint(ox + c * spacing, oy + r * spacing) for k in keep_clear or ()):
                    break
            accent_at[(c, r)] = (kind, col)
    for r in range(rows):
        y = oy + r * spacing
        for c in range(cols):
            x = ox + c * spacing
            acc = accent_at.get((c, r))
            if acc:
                sym = sprites.symbol(acc[0], accent_size, acc[1])
                surf.blit(sym, sym.get_rect(center=(int(x), int(y))))
            else:
                surf.blit(dot, (int(x - dw), int(y - dw)))


def gradient_bar(w: int, h: int, left, right) -> pygame.Surface:
    """Horizontal brand gradient (page 9: allowed for charts and icons)."""
    surf = pygame.Surface((max(1, w), max(1, h)))
    for x in range(max(1, w)):
        surf.fill(theme.lerp_color(left, right, x / max(1, w - 1)), pygame.Rect(x, 0, 1, h))
    return surf.convert() if pygame.display.get_surface() is not None else surf
