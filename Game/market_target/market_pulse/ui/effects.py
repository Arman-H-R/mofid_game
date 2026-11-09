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
MAX_DOT_RINGS = 16
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
class DotRing:
    """Rings of small dots (the reference clip's motion): they close in where a box appears
    (r1 < r0) and spread out where one is hit or leaves (r1 > r0)."""
    x: float
    y: float
    r0: float  # cm, the outer ring at the start
    r1: float  # cm, the outer ring at the end
    color: tuple
    dur: float
    delay: float = 0.0
    age: float = 0.0


def dot_rings(surf: pygame.Surface, vp: Viewport, x: float, y: float, r: float, color, bg, spin: float = 0.0,
              rings: int = 3, gap: float = 0.24, spacing: float = 3.4, dot: float = 0.55) -> None:
    """Concentric rings of small dots around (x, y) cm, the outer one of radius r cm. Inner
    rings are fainter; neighbouring rings turn opposite ways by spin (radians)."""
    cx, cy = vp.px(x, y)
    d = max(1.0, vp.sf(dot))
    for k in range(rings):
        rr = r * (1.0 - gap * k)
        if rr <= dot * 2:
            break
        n = max(6, int(math.tau * rr / spacing))
        a0 = spin if k % 2 == 0 else -spin
        rp = vp.sf(rr)
        col = theme.lerp_color(color, bg, 0.3 * k)
        for i in range(n):
            a = a0 + math.tau * i / n
            pygame.draw.circle(surf, col, (cx + rp * math.cos(a), cy + rp * math.sin(a)), d)


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


@dataclass
class Badge:
    """The hit feedback of MOFID_UI v4.1 ("HIT · RINGS + POPUP"): rings open around the spot,
    and inside them the product's name, the points and the multiplier."""
    x: float
    y: float
    name: str
    points: str
    mult: str
    color: tuple
    big: bool  # Market Rush or a tiny box: a touch larger
    dur: float = 1.05
    age: float = 0.0
    out: float | None = None  # the age at which a newer hit sent it away
    # the box that was hit (centre, width, height in cm): the rings morph out of its shape;
    # None: they start as a small rounded square at the badge's own centre
    tile: tuple | None = None
    # where it goes at the end (cm): the score in the HUD; None: it shrinks where it is
    home: tuple | None = None

    @property
    def value(self) -> int:
        """Its points as a number ("+60" -> 60)."""
        return int(self.points[1:]) if self.points[1:].isdigit() else 0

    def leaving(self) -> float:
        """0 while it is up, 1 when it has gone: a short exit at the end, or a quicker one once
        another hit has taken over."""
        e = (self.age - (self.dur - BADGE_EXIT)) / BADGE_EXIT
        if self.out is not None:
            e = max(e, (self.age - self.out) / BADGE_HANDOVER)
        return min(1.0, max(0.0, e + 1e-9))  # 1 exactly at the end, whatever the rounding


MAX_BADGES = 4
# MOFID_UI v4.1 page 6, measured on the 300 cm wall (810 pt = 300 cm): the inner ring and
# two quieter rings around it, eight short rays further out.
BADGE_R = 25.6  # cm, the inner ring (solid, 0.83 cm)
BADGE_R2 = 34.4  # cm, the second ring (50 %)
BADGE_R3 = 43.3  # cm, the third ring (22 %)
BADGE_RAYS = (49.0, 53.8)  # cm, from and to, at 22.5° + 45°·k
BADGE_EXIT = 0.42  # s, the end: it morphs back into a small box and flies into the score
BADGE_HANDOVER = 0.26  # s, the same when the next hit comes first
BADGE_MORPH = 0.3  # s, a ring's morph from the box's rounded square to its circle
BADGE_HOME_R = 4.5  # cm, the size of the little box that lands in the score
SQUIRCLE_N = 5.0  # the box's shape as a superellipse |x|^n + |y|^n = 1 (2 is a circle)
SUPERSAMPLE = 2  # morphing outlines are drawn this many times larger, then smoothed down


def _superellipse(cx: float, cy: float, a: float, b: float, n: float, steps: int = 96) -> list:
    """Points round |x/a|^n + |y/b|^n = 1, clockwise from the top."""
    pts = []
    e = 2.0 / n
    for i in range(steps):
        t = math.tau * i / steps
        s, c = math.sin(t), math.cos(t)
        pts.append((cx + a * math.copysign(abs(s) ** e, s), cy - b * math.copysign(abs(c) ** e, c)))
    return pts


def _ease_out5(p: float) -> float:
    """A long, soft landing."""
    return 1.0 - (1.0 - min(max(p, 0.0), 1.0)) ** 5


def _ease_in_out(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3.0 - 2.0 * p)


class Effects:
    def __init__(self, sprites: Sprites, text: Text):
        self.sprites = sprites
        self.text = text
        self.ripples: list[Ripple] = []
        self.dot_rings: list[DotRing] = []
        self.particles: list[Particle] = []
        self.popups: list[Popup] = []
        self.banners: list[Banner] = []
        self.badges: list[Badge] = []
        self.flash_color: tuple | None = None
        self.flash_age = 0.0
        self.flash_dur = 0.0
        self.flash_alpha = 0
        self._flash_surf: pygame.Surface | None = None
        self.rng = random.Random()

    def clear(self) -> None:
        self.ripples.clear()
        self.dot_rings.clear()
        self.particles.clear()
        self.popups.clear()
        self.banners.clear()
        self.badges.clear()
        self.flash_color = None

    # -- spawning -------------------------------------------------------------------
    def ripple(self, x, y, r0, r1, width, color, dur=0.5, delay=0.0) -> None:
        if len(self.ripples) >= MAX_RIPPLES:
            self.ripples.pop(0)
        self.ripples.append(Ripple(x, y, r0, r1, width, color, dur, delay))

    def dot_ring(self, x, y, r0, r1, color, dur=0.5, delay=0.0) -> None:
        if len(self.dot_rings) >= MAX_DOT_RINGS:
            self.dot_rings.pop(0)
        self.dot_rings.append(DotRing(x, y, r0, r1, color, dur, delay))

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

    def badge(self, x, y, name, points, mult, color, big=False, tile=None, home=None) -> None:
        """The product's name and its points in rings at (x, y) cm. Every badge still up leaves
        quickly: only the newest hit is read."""
        for b in self.badges:
            if b.out is None:
                b.out = b.age
        self.badges = self.badges[-(MAX_BADGES - 1):]
        self.badges.append(Badge(x, y, name, points, mult, color, big, tile=tile, home=home))

    def flash(self, color, alpha=40, dur=0.3) -> None:
        self.flash_color, self.flash_alpha, self.flash_dur, self.flash_age = color, alpha, dur, 0.0

    # -- update / draw ----------------------------------------------------------------
    def update(self, dt: float) -> None:
        for r in self.ripples:
            r.age += dt
        self.ripples = [r for r in self.ripples if r.age < r.delay + r.dur]
        for d in self.dot_rings:
            d.age += dt
        self.dot_rings = [d for d in self.dot_rings if d.age < d.delay + d.dur]
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
        for b in self.badges:
            b.age += dt
        self.badges = [b for b in self.badges if b.leaving() < 1.0]
        if self.banners:
            self.banners[0].age += dt
            if self.banners[0].age >= self.banners[0].dur:
                self.banners.pop(0)
        if self.flash_color is not None:
            self.flash_age += dt
            if self.flash_age >= self.flash_dur:
                self.flash_color = None

    def draw_under(self, surf: pygame.Surface, vp: Viewport, bg) -> None:
        """The rings of dots: on the field under the boxes, as the clip's dots lie on the floor."""
        for d in self.dot_rings:
            if d.age < d.delay:
                continue
            p = (d.age - d.delay) / d.dur
            r = d.r0 + (d.r1 - d.r0) * _ease_out(p)
            dot_rings(surf, vp, d.x, d.y, r, theme.lerp_color(d.color, bg, p**1.5), bg, spin=0.5 * p)

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

        for b in self.badges:
            self._draw_badge(surf, vp, b, bg)

    def _outlines(self, surf: pygame.Surface, vp: Viewport, shapes: list, bg) -> None:
        """Anti-aliased thick outlines of superellipses: shapes are (cx, cy, a, b, n, width_cm,
        color) in cm, drawn SUPERSAMPLE times larger on one transparent layer, then smoothed down."""
        if not shapes:
            return
        pad = 2.0
        x0 = min(s[0] - s[2] for s in shapes) - pad
        x1 = max(s[0] + s[2] for s in shapes) + pad
        y0 = min(s[1] - s[3] for s in shapes) - pad
        y1 = max(s[1] + s[3] for s in shapes) + pad
        area = vp.cm_rect(x0, y0, x1, y1).clip(surf.get_rect())
        if area.w <= 0 or area.h <= 0:
            return
        k, ppc = SUPERSAMPLE, vp.ppc
        ox, oy = vp.px(0, 0)
        layer = pygame.Surface((area.w * k, area.h * k), pygame.SRCALPHA)
        layer.fill((*bg[:3], 0))  # smoothing blends the edges towards the field, not towards black
        for cx, cy, a, b, n, width, color in shapes:
            pts = [((ox + x * ppc - area.x) * k, (oy + y * ppc - area.y) * k) for x, y in _superellipse(cx, cy, a, b, n)]
            pygame.draw.lines(layer, color, True, pts, max(1, int(round(width * ppc * k))))
        surf.blit(pygame.transform.smoothscale(layer, area.size), area)

    def _draw_badge(self, surf: pygame.Surface, vp: Viewport, b: Badge, bg) -> None:
        """MOFID_UI v4.1 page 6, "HIT · RINGS + POPUP", as a morph both ways:
        in    0.00 the points are there at once and count up in 0.2 s; the name and the multiplier
              follow at 0.05 and 0.10. 0.00-0.30 the box that was hit rounds off into the inner
              ring (two more outlines follow it at 0.04 and 0.08 into the quieter rings);
              0.12-0.42 eight rays slide out into place
        held  the outer rings breathe a little
        out   the rays draw in, the outer rings fold into the inner one, and it morphs back into
              a small rounded box that flies along a curve into the score in the HUD (b.home),
              taking the words with it, shrinking: 0.42 s, or 0.26 s when the next hit comes first."""
        age = b.age
        k = 1.04 if b.big else 1.0
        e = _ease_in_out(b.leaving())
        stroke = lambda cm: max(1, vp.s(cm))  # noqa: E731
        tx, ty, tw, th = b.tile if b.tile else (b.x, b.y, 24.0, 22.0)
        breathe = 1.0 + 0.012 * math.sin(max(0.0, age - 0.6) * 5.0)

        # where it is: at the badge's centre, or on a curve to the score as it leaves
        cx, cy = b.x, b.y
        if e > 0.0:
            hx, hy = b.home if b.home else (b.x, b.y)
            ctrl = (b.x + (hx - b.x) * 0.25, b.y + (hy - b.y) * 0.8)  # rises first, then across
            u = e
            cx = (1 - u) ** 2 * b.x + 2 * (1 - u) * u * ctrl[0] + u * u * hx
            cy = (1 - u) ** 2 * b.y + 2 * (1 - u) * u * ctrl[1] + u * u * hy
        shrink = BADGE_HOME_R / BADGE_R
        size = 1.0 - (1.0 - shrink) * e  # the inner ring's size, 1 -> the little box
        back = SQUIRCLE_N ** e * 2.0 ** (1.0 - e)  # circle -> rounded box again
        fade = 1.0 - max(0.0, (e - 0.6) / 0.4)  # it stays clear until it is nearly home
        c = vp.px(cx, cy)

        # behind the words the field's own colour, solid inside the inner ring and half way out
        # to the second: invisible on the empty field (as in the design), but a box beside it or
        # gliding under it is covered, so it never shows through the words
        m = _ease_in_out(age / BADGE_MORPH)
        show = min(1.0, age / 0.08) * (1.0 - max(0.0, (e - 0.5) / 0.4))
        if show > 0.0:
            grow = 0.75 + 0.25 * m
            for radius, alpha in ((BADGE_R2, 0.55), (BADGE_R, 1.0)):
                disc = self.sprites.dot(vp.sf(radius * k * size * grow), bg)
                disc.set_alpha(int(255 * alpha * show))
                surf.blit(disc, disc.get_rect(center=c))
                disc.set_alpha(255)

        # the three outlines: out of the box into circles, and at the end folded back together
        shapes = []
        for radius, opacity, width, delay in ((BADGE_R, 1.0, 0.83, 0.0), (BADGE_R2, 0.5, 0.69, 0.04),
                                              (BADGE_R3, 0.22, 0.55, 0.08)):
            p = (age - delay) / BADGE_MORPH
            if p <= 0.0:
                continue
            inner = radius == BADGE_R
            a_in = min(1.0, _ease_out(p) * 2.5)
            a_out = fade if inner else (1.0 - min(1.0, e * 1.8)) ** 2  # the outer ones fold in first
            col = theme.lerp_color(bg, b.color, opacity * a_in * a_out)
            if a_out <= 0.0:
                continue
            R = radius * k * (1.0 if inner else breathe)
            if e > 0.0:  # folding into the inner ring as it shrinks
                fold = 1.0 if inner else min(1.0, e * 1.8)
                R = (R + (BADGE_R * k - R) * fold) * size
                shapes.append((cx, cy, R, R, back, width, col))
            elif p >= 1.0:
                pygame.draw.aacircle(surf, col, c, int(vp.sf(R)), stroke(width))
            else:
                s = _ease_in_out(p)  # how far the shape has come: box -> circle
                margin = (radius - BADGE_R) * 0.3  # the outer ones start just outside the box
                a0, b0 = tw / 2 + margin, th / 2 + margin
                shapes.append((tx + (b.x - tx) * s, ty + (b.y - ty) * s, a0 + (R - a0) * s, b0 + (R - b0) * s,
                               SQUIRCLE_N ** (1.0 - s) * 2.0 ** s, width, col))
        self._outlines(surf, vp, shapes, bg)

        # eight short rays, at 22.5° + 45°·k: sliding out into place, drawn in first as it leaves
        q = _ease_out((age - 0.12) / 0.3) * (1.0 - min(1.0, e / 0.3))
        if q > 0.0:
            ray = theme.lerp_color(bg, b.color, q)
            r0 = (BADGE_RAYS[0] - 7.0 * (1.0 - q)) * k
            r1 = r0 + (BADGE_RAYS[1] - BADGE_RAYS[0]) * q
            for i in range(8):
                a = math.radians(22.5 + 45.0 * i)
                ca, sa = math.cos(a), math.sin(a)
                pygame.draw.line(surf, ray, vp.px(b.x + ca * r0, b.y + sa * r0), vp.px(b.x + ca * r1, b.y + sa * r1),
                                 stroke(0.83))

        # the words: the points at once, counting up; the name and the multiplier just after
        pts = b.points
        q_pts = age / 0.2
        if pts[1:].isdigit() and q_pts < 1.0:
            pts = f"{pts[0]}{int(round(int(pts[1:]) * _ease_out(q_pts)))}"
        for text, role, size_cm, color, dy, delay, dur, rise, grow in (
                (b.name, "heading", 8.2, theme.WHITE, -13.2, 0.05, 0.22, 2.5, 0.0),
                (pts, "numbers", 20.5, theme.WHITE, 3.4, 0.0, 0.16, 0.0, 0.18),
                (b.mult, "numbers", 8.6, theme.TURQ, 18.4, 0.1, 0.22, -2.0, 0.2)):
            if not text:
                continue
            q = _ease_out((age - delay) / dur)
            if q <= 0.0:
                continue
            fit = min(1.0, BADGE_R * 1.6 / max(0.1, self.text.width(text, role, vp.s(size_cm)) / vp.ppc))
            img = self.text.render(text, role, vp.s(size_cm * k * fit), color, cache=text is not pts or pts == b.points)
            s = (1.0 - grow * (1.0 - q)) * size  # settling to its size; shrinking with the box as it goes
            if abs(s - 1.0) > 0.002:
                img = pygame.transform.smoothscale(img, (max(1, int(img.get_width() * s)), max(1, int(img.get_height() * s))))
            img.set_alpha(int(255 * q * (1.0 - min(1.0, e * 1.6))))  # the words go before the box lands
            surf.blit(img, img.get_rect(center=vp.px(cx, cy + (dy + rise * (1.0 - q)) * k * size)))
            img.set_alpha(255)

    def draw_banner(self, surf: pygame.Surface, vp: Viewport, y_cm: float) -> None:
        """The current banner at y_cm, as type only (MOFID_UI v4.1: no pills around type)."""
        if not self.banners:
            return
        b = self.banners[0]
        p = b.age / b.dur
        alpha = max(0, int(255 * min(1.0, p * 6.0, (1.0 - p) * 4.0)))
        img = self.text.render(b.text, "title", vp.s(8), b.color)
        k = 0.85 + 0.15 * _ease_out(p * 5.0)
        if k < 0.999:
            img = pygame.transform.smoothscale(img, (max(1, int(img.get_width() * k)), max(1, int(img.get_height() * k))))
        img.set_alpha(alpha)
        surf.blit(img, img.get_rect(center=vp.px(vp.wall_w / 2, y_cm)))
        img.set_alpha(255)
