"""Short-lived feedback effects: ripples, symbol particles, floating words,
banners and flashes. Counts are capped so a burst of events can never grow
memory or frame time without bound. Positions are in wall centimetres."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import pygame

from . import theme
from .draw import Sprites
from .text import Text
from .theme import Viewport

MAX_PARTICLES = 240
MAX_RIPPLES = 24
MAX_POPUPS = 16


def _ease_out(p: float) -> float:
    return 1.0 - (1.0 - min(max(p, 0.0), 1.0)) ** 3


@dataclass
class Ripple:
    x: float
    y: float
    r0: float
    r1: float
    width: float
    color: tuple
    dur: float
    delay: float = 0.0
    age: float = 0.0


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    kind: str
    size: float
    color: tuple
    life: float
    age: float = 0.0


@dataclass
class Popup:
    x: float
    y: float
    text: str
    role: str
    size: float  # cm
    color: tuple
    dur: float
    rise: float = 12.0
    delay: float = 0.0
    age: float = 0.0


@dataclass
class Banner:
    text: str
    color: tuple
    dur: float
    age: float = 0.0


class Effects:
    def __init__(self, sprites: Sprites, text: Text):
        self.sprites = sprites
        self.text = text
        self.ripples: list[Ripple] = []
        self.particles: list[Particle] = []
        self.popups: list[Popup] = []
        self.banners: list[Banner] = []
        self.flash_color: tuple | None = None
        self.flash_age = 0.0
        self.flash_dur = 0.0
        self.flash_alpha = 0
        self._flash_surf: pygame.Surface | None = None
        self.rng = random.Random()

    def clear(self) -> None:
        self.ripples.clear()
        self.particles.clear()
        self.popups.clear()
        self.banners.clear()
        self.flash_color = None

    # -- spawning -------------------------------------------------------------------
    def ripple(self, x, y, r0, r1, width, color, dur=0.5, delay=0.0) -> None:
        if len(self.ripples) >= MAX_RIPPLES:
            self.ripples.pop(0)
        self.ripples.append(Ripple(x, y, r0, r1, width, color, dur, delay))

    def burst(self, x, y, color, count=12, speed=(70.0, 150.0), size=(3.0, 5.5), life=0.75, start_r=0.0) -> None:
        """Brand symbols flying out from a ring of radius start_r (cm) around (x, y)."""
        palette = [color, color, color, theme.DARK_BLUE, theme.TURQUOISE]
        for i in range(count):
            if len(self.particles) >= MAX_PARTICLES:
                self.particles.pop(0)
            a = (i / count) * math.tau + self.rng.uniform(-0.3, 0.3)
            v = self.rng.uniform(*speed)
            self.particles.append(
                Particle(
                    x + math.cos(a) * start_r,
                    y + math.sin(a) * start_r,
                    math.cos(a) * v,
                    math.sin(a) * v,
                    self.rng.choice(theme.SYMBOLS),
                    self.rng.uniform(*size),
                    self.rng.choice(palette),
                    life * self.rng.uniform(0.8, 1.15),
                )
            )

    def popup(self, x, y, text, role, size, color, dur=1.1, rise=12.0, delay=0.0) -> None:
        if len(self.popups) >= MAX_POPUPS:
            self.popups.pop(0)
        self.popups.append(Popup(x, y, text, role, size, color, dur, rise, delay))

    def banner(self, text, color, dur=1.4) -> None:
        """Queue a short message; banners are shown one after another, never stacked."""
        if any(b.text == text for b in self.banners):
            return
        self.banners = self.banners[-2:]
        self.banners.append(Banner(text, color, dur))

    def flash(self, color, alpha=40, dur=0.3) -> None:
        self.flash_color, self.flash_alpha, self.flash_dur, self.flash_age = color, alpha, dur, 0.0

    # -- update / draw ----------------------------------------------------------------
    def update(self, dt: float) -> None:
        for r in self.ripples:
            r.age += dt
        self.ripples = [r for r in self.ripples if r.age < r.delay + r.dur]
        drag = math.exp(-3.2 * dt)
        for p in self.particles:
            p.age += dt
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.vx *= drag
            p.vy = p.vy * drag + 40.0 * dt  # a little gravity
        self.particles = [p for p in self.particles if p.age < p.life]
        for p in self.popups:
            p.age += dt
        self.popups = [p for p in self.popups if p.age < p.delay + p.dur]
        if self.banners:
            self.banners[0].age += dt
            if self.banners[0].age >= self.banners[0].dur:
                self.banners.pop(0)
        if self.flash_color is not None:
            self.flash_age += dt
            if self.flash_age >= self.flash_dur:
                self.flash_color = None

    def draw(self, surf: pygame.Surface, vp: Viewport, field_rect: pygame.Rect, bg) -> None:
        """Flash, ripples, particles and floating words (everything under the HUD)."""
        if self.flash_color is not None:
            if self._flash_surf is None or self._flash_surf.get_size() != field_rect.size:
                self._flash_surf = pygame.Surface(field_rect.size)
            self._flash_surf.fill(self.flash_color)
            self._flash_surf.set_alpha(int(self.flash_alpha * (1.0 - self.flash_age / self.flash_dur)))
            surf.blit(self._flash_surf, field_rect.topleft)

        for r in self.ripples:
            if r.age < r.delay:
                continue
            p = (r.age - r.delay) / r.dur
            rad = vp.sf(r.r0 + (r.r1 - r.r0) * _ease_out(p))
            color = theme.lerp_color(r.color, bg, p**1.5)  # fade towards the background
            w = max(1, int(vp.sf(r.width) * (1.0 - 0.6 * p)))
            if rad > w:
                pygame.draw.aacircle(surf, color, vp.px(r.x, r.y), int(rad), w)

        for pt in self.particles:
            p = pt.age / pt.life
            spr = self.sprites.symbol(pt.kind, vp.s(pt.size * (1.0 - 0.4 * p)), pt.color)
            spr.set_alpha(int(255 * (1.0 - p**2)))
            surf.blit(spr, spr.get_rect(center=vp.px(pt.x, pt.y)))
            spr.set_alpha(255)

        for pu in self.popups:
            if pu.age < pu.delay:
                continue
            p = (pu.age - pu.delay) / pu.dur
            scale = 0.6 + 0.55 * _ease_out(p * 4.0) - 0.15 * _ease_out(max(0.0, p * 4.0 - 1.0))
            alpha = 255 if p < 0.6 else int(255 * (1.0 - (p - 0.6) / 0.4))
            base = self.text.render(pu.text, pu.role, vp.s(pu.size), pu.color)
            w, h = base.get_size()
            img = pygame.transform.smoothscale(base, (max(1, int(w * scale)), max(1, int(h * scale))))
            img.set_alpha(alpha)
            surf.blit(img, img.get_rect(center=vp.px(pu.x, pu.y - pu.rise * _ease_out(p))))


    def draw_banner(self, surf: pygame.Surface, vp: Viewport, y_cm: float) -> None:
        """The current banner as a white pill centred at y_cm (drawn over the HUD)."""
        if not self.banners:
            return
        b = self.banners[0]
        p = b.age / b.dur
        alpha = max(0, int(255 * min(1.0, p * 6.0, (1.0 - p) * 4.0)))
        img = self.text.render(b.text, "heading", vp.s(7), b.color)
        h = int(img.get_height() * 1.2)
        pill = self.sprites.rounded(img.get_width() + vp.s(12), h, h // 2, theme.WHITE)
        c = vp.px(vp.wall_w / 2, y_cm)
        pill.set_alpha(alpha)
        img.set_alpha(alpha)
        surf.blit(pill, pill.get_rect(center=c))
        surf.blit(img, img.get_rect(center=c))
        pill.set_alpha(255)
        img.set_alpha(255)
