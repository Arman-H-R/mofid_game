"""Persian text rendering.

pygame-ce shapes Arabic script with HarfBuzz (letters join, ZWNJ works), but a
single right-to-left shaping run also reverses numbers ("۱۰" would show as
"۰۱"). So each string is split into right-to-left runs (Persian letters) and
left-to-right runs (digits, Latin, and signs glued to digits such as "+۲۰",
"×۲", "۵۰٪"), each run is shaped on its own, and the runs are laid out right to
left. That is a small subset of the Unicode bidi algorithm, which is all the
game's texts need.

Rendered strings are cached (LRU bounded by count and by pixel memory), so
steady-state frames do no text rendering at all.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pygame

from .draw import LRU

log = logging.getLogger(__name__)

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_DIGITS = set("0123456789۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩")
_NUMBER_GLUE = set("+-−×%٪.,٫٬:/")
_NUMBER_SIGNS = set("٪٫٬")  # Arabic-block characters that are number punctuation, not letters
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


def fa_digits(text: str) -> str:
    return text.translate(_FA_DIGITS)


def _char_class(ch: str) -> str:
    if ch in _DIGITS:
        return "L"
    if ch in _NUMBER_SIGNS:
        return "N"
    o = ord(ch)
    if ch == "‌" or any(lo <= o <= hi for lo, hi in _ARABIC_RANGES):
        return "R"
    if ch.isalpha():
        return "L"
    return "N"


def split_runs(text: str) -> list[tuple[str, bool]]:
    """Split ``text`` into (run, is_rtl) in logical order.

    A string with no Persian letters is a single left-to-right run.
    """
    if not text:
        return []
    cls = [_char_class(c) for c in text]
    if "R" not in cls:
        return [(text, False)]
    n = len(text)
    for i, c in enumerate(text):  # signs glued to a digit travel with the number
        if cls[i] == "N" and c in _NUMBER_GLUE:
            if (i + 1 < n and text[i + 1] in _DIGITS) or (i > 0 and text[i - 1] in _DIGITS):
                cls[i] = "L"
    strong = [c if c != "N" else None for c in cls]
    prev_strong: list[str | None] = [None] * n
    last = None
    for i in range(n):
        prev_strong[i] = last
        if strong[i]:
            last = strong[i]
    nxt = None
    for i in range(n - 1, -1, -1):
        if cls[i] == "N":
            cls[i] = "L" if (prev_strong[i] == "L" and nxt == "L") else "R"
        else:
            nxt = cls[i]
    runs: list[tuple[str, bool]] = []
    start = 0
    for i in range(1, n + 1):
        if i == n or cls[i] != cls[start]:
            runs.append((text[start:i], cls[start] == "R"))
            start = i
    return runs


class FontBook:
    """Loads fonts by role (title, heading, bold, body, numbers, latin)."""

    def __init__(self, fonts_dir: Path, roles: dict[str, list[str]]):
        self.paths: dict[str, str | None] = {}
        for role, candidates in roles.items():
            self.paths[role] = self._find(fonts_dir, candidates, role)
        self.paths.setdefault("latin", self.paths.get("body"))
        self._fonts: dict[tuple[str, int], pygame.font.Font] = {}
        self.shaping = hasattr(pygame.font.Font, "set_script") and hasattr(pygame.font.Font, "set_direction")
        if not self.shaping:
            log.error("this pygame build cannot shape Persian text; install pygame-ce (see README)")

    @staticmethod
    def _find(fonts_dir: Path, candidates: list[str], role: str) -> str | None:
        for name in candidates:
            if name.startswith("sys:"):
                found = pygame.font.match_font(name[4:])
                if found:
                    return found
                continue
            p = fonts_dir / name
            if p.is_file():
                return str(p)
        fallback = pygame.font.match_font("tahoma,segoeui,arial")
        log.warning("no font found for role %r (tried %s); falling back to %s", role, candidates, fallback)
        return fallback

    def get(self, role: str, px: int) -> pygame.font.Font:
        key = (role, max(4, px))
        font = self._fonts.get(key)
        if font is None:
            path = self.paths.get(role) or self.paths.get("body")
            try:
                font = pygame.font.Font(path, key[1])
            except (OSError, FileNotFoundError, pygame.error) as exc:
                log.error("cannot load font %s: %s", path, exc)
                font = pygame.font.Font(None, key[1])
            self._fonts[key] = font
        return font


class Text:
    CACHE_SIZE = 700

    def __init__(self, fonts: FontBook, persian_digits: bool = True):
        self.fonts = fonts
        self.persian_digits = persian_digits
        self._cache = LRU(self.CACHE_SIZE)

    def clear(self) -> None:
        self._cache.clear()

    def set_budget(self, canvas_bytes: int) -> None:
        self._cache.max_bytes = 4 * canvas_bytes

    def localize(self, text: str) -> str:
        return fa_digits(text) if self.persian_digits else text

    def _render_run(self, font: pygame.font.Font, run: str, rtl: bool, color) -> pygame.Surface:
        if self.fonts.shaping:
            font.set_script("Arab" if rtl else "Latn")
            font.set_direction(pygame.DIRECTION_RTL if rtl else pygame.DIRECTION_LTR)
        return font.render(run, True, color)

    def render(self, text: str, role: str, px: int, color, localize: bool = True, cache: bool = True) -> pygame.Surface:
        """``cache=False`` for strings shown for a frame or two (count-ups), so
        they do not push the long-lived texts out of the cache."""
        if localize:
            text = self.localize(text)
        key = (text, role, px, tuple(color))
        surf = self._cache.hit(key)
        if surf is not None:
            return surf
        font = self.fonts.get(role, px)
        runs = split_runs(text) or [(" ", False)]
        if len(runs) == 1:
            surf = self._render_run(font, runs[0][0], runs[0][1], color)
        else:
            parts = [self._render_run(font, r, rtl, color) for r, rtl in runs]
            w = sum(p.get_width() for p in parts)
            h = max(p.get_height() for p in parts)
            surf = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
            x = w
            for p in parts:  # logical order is laid out from the right edge
                x -= p.get_width()
                surf.blit(p, (x, (h - p.get_height()) // 2))
        surf = surf.convert_alpha() if pygame.display.get_surface() is not None else surf
        return self._cache.put(key, surf) if cache else surf

    def width(self, text: str, role: str, px: int) -> int:
        return self.render(text, role, px, (0, 0, 0)).get_width()

    def wrap(self, text: str, role: str, px: int, max_w: int) -> list[str]:
        lines: list[str] = []
        for para in text.split("\n"):
            words = para.split(" ")
            line = ""
            for word in words:
                cand = f"{line} {word}" if line else word
                if line and self.width(cand, role, px) > max_w:
                    lines.append(line)
                    line = word
                else:
                    line = cand
            lines.append(line)
        return lines

    def blit(
        self,
        target: pygame.Surface,
        text: str,
        role: str,
        px: int,
        color,
        pos: tuple[int, int],
        anchor: str = "center",
        alpha: int = 255,
        localize: bool = True,
        cache: bool = True,
    ) -> pygame.Rect:
        surf = self.render(text, role, px, color, localize, cache)
        rect = surf.get_rect(**{anchor: pos})
        if alpha < 255:
            surf.set_alpha(alpha)
            target.blit(surf, rect)
            surf.set_alpha(255)
        else:
            target.blit(surf, rect)
        return rect

    def blit_lines(
        self,
        target: pygame.Surface,
        text: str,
        role: str,
        px: int,
        color,
        pos: tuple[int, int],
        max_w: int,
        line_gap: float = 1.25,
        anchor_x: str = "center",
    ) -> int:
        """Wrapped paragraph; ``pos`` is the top anchor point. Returns the bottom y."""
        x, y = pos
        step = int(px * line_gap)
        for line in self.wrap(self.localize(text), role, px, max_w):
            surf = self.render(line, role, px, color, localize=False)
            rect = surf.get_rect()
            setattr(rect, {"center": "midtop", "right": "topright", "left": "topleft"}[anchor_x], (x, y))
            target.blit(surf, rect)
            y += step
        return y
