"""The idle (attract) screen of MOFID_UI v4.1: a loop that draws people in and shows them how
to play, with nobody to explain it.

    title   the 10 product tiles circle the title slowly (1 turn in 40 s), 01 ATTRACT
    demo    a round played by itself on a small field: products appear, a ball hits one, its
            name and points open in rings, a new product takes its place and the boxes move;
            then the three sizes and their points (the smaller, the more), and a tiny box
            hit that sets off Market Rush
    board   today's best three, 02 LEADERBOARD (only once somebody has played today)

The start box and its line stay at the bottom the whole time. Everything is a function of the
clock only: nothing here touches the game, so the loop can never start, change or block a
round, and it stops the moment the flow leaves the attract state.
"""
from __future__ import annotations

import math

import pygame

from . import theme
from .effects import Badge

TITLE_TURN_S = 40.0  # one turn of the circle of tiles
DEMO_TILE = {"medium": 40.0, "small": 31.0, "tiny": 22.0}  # tile widths on the demo field, cm


def _ease(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1.0 - (1.0 - p) ** 3


def _smooth(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def _back(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    c = 1.4
    return 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2 if p > 0 else 0.0


def _phase(t: float, start: float, dur: float) -> float:
    return min(max((t - start) / dur, 0.0), 1.0)


class DemoTile:
    """A tile of the demonstration: where it is, when it comes and goes."""

    def __init__(self, key: str, tier: str, path: list[tuple[float, float, float]], born: float, gone: float = 1e9,
                 hit: bool = False, extra: bool = False):
        self.key, self.tier = key, tier
        self.path = path  # (time, x, y): it glides from one point to the next
        self.born, self.gone, self.hit, self.extra = born, gone, hit, extra
        self.seed = (sum(map(ord, key)) % 97) / 97 * math.tau  # the same wobble on every run

    def pos(self, t: float, speed: float) -> tuple[float, float]:
        x, y = self.path[0][1], self.path[0][2]
        for (t0, x0, y0), (t1, x1, y1) in zip(self.path, self.path[1:]):
            if t >= t1:
                x, y = x1, y1
            elif t > t0:
                p = _ease((t - t0) / max(0.01, t1 - t0))
                x, y = x0 + (x1 - x0) * p, y0 + (y1 - y0) * p
                break
        a = t * speed
        return x + 4.0 * math.sin(a * 1.3 + self.seed), y + 2.6 * math.sin(a * 1.7 + self.seed * 1.9)

    def scale(self, t: float) -> float:
        if t < self.born:
            return 0.0
        if t >= self.gone:
            return max(0.0, 1.0 - (t - self.gone) / 0.22)
        return _back((t - self.born) / 0.35)


class Attract:
    def __init__(self, renderer):
        self.r = renderer
        self._scene: str | None = None
        self._deep: pygame.Surface | None = None
        self._script()

    # -- the loop ----------------------------------------------------------------------------
    def scenes(self, has_board: bool) -> list[tuple[str, float]]:
        flow = self.r.cfg.flow
        out = [("title", float(flow.attract_panel_s))]
        if flow.attract_demo:
            out.append(("demo", float(flow.attract_demo_s)))
        if has_board:
            out.append(("board", float(flow.attract_panel_s)))
        return out

    def scene_at(self, now: float, has_board: bool) -> tuple[str, float, float]:
        """(scene, seconds into it, its length) at game time now."""
        scenes = self.scenes(has_board)
        total = sum(d for _, d in scenes)
        t = now % total
        for name, dur in scenes:
            if t < dur:
                return name, t, dur
            t -= dur
        return scenes[-1][0], 0.0, scenes[-1][1]

    def start_of(self, scene: str, now: float, has_board: bool) -> float:
        """The game time of the next start of a scene at or after now (for the gallery)."""
        scenes = self.scenes(has_board)
        total = sum(d for _, d in scenes)
        offset = 0.0
        for name, dur in scenes:
            if name == scene:
                break
            offset += dur
        k = math.floor((now - offset) / total) + 1
        return k * total + offset

    def draw(self, surf: pygame.Surface, flow, now: float, overlays: dict) -> None:
        r = self.r
        board = [rec for rec in (overlays.get("leaderboard") or [])]
        show_board = bool(board) and r.cfg.results.leaderboard_size > 0
        scene, t, dur = self.scene_at(now, show_board)
        if scene != self._scene:
            if self._scene is not None:  # cross-fade: the screen still holds the old scene's last frame
                r.fade_src.blit(surf, (0, 0))
                r._fade_t0 = now
            self._scene = scene
        surf.blit(r.bg_dark, (0, 0))
        number = {"title": "01 — ATTRACT", "demo": "02 — HOW TO PLAY",
                  "board": ("03" if r.cfg.flow.attract_demo else "02") + " — LEADERBOARD"}[scene]
        if scene == "title":
            self._title(surf, t, dur, now)
        elif scene == "demo":
            self._demo(surf, t, dur)
        else:
            self._board(surf, board, t, now)
        r._frame_marks(surf, number)
        self._start_box(surf, now)
        player = flow.player
        if player and (player.get("name") or player.get("id")):
            from .screens import _short
            label = f"{r.T.next_player}: {_short(player.get('name') or player.get('id'), 24)}"
            sb = r.cfg.layout.start_button
            r.text.blit(surf, label, "heading", r.vp.s(6.5), theme.TURQ, r.vp.px(r.W / 2, sb.y_cm - sb.size_cm / 2 - 16))
        r.fx.draw(surf, r.vp, r.vp.rect, theme.FIELD)

    # -- shared --------------------------------------------------------------------------------
    def _start_box(self, surf: pygame.Surface, now: float) -> None:
        """The turquoise start square with its corner ticks breathing around it (06 START TARGET)."""
        r, vp = self.r, self.r.vp
        sb = r.cfg.layout.start_button
        if not r.cfg.flow.start_by_throw:
            r.text.blit(surf, r.T.cta_operator, "heading", vp.s(10), theme.WHITE, vp.px(r.W / 2, sb.y_cm))
            return
        beat = 0.5 - 0.5 * math.cos(now * math.tau / 1.6)
        half = sb.size_cm / 2
        surf.fill(theme.TURQ, vp.cm_rect(sb.x_cm - half, sb.y_cm - half, sb.x_cm + half, sb.y_cm + half))
        r.text.blit(surf, r.T.start, "title", vp.s(sb.size_cm * 0.26), theme.DEEP, vp.px(sb.x_cm, sb.y_cm))
        inner = half + 4 + 2.5 * beat
        r._ticks(surf, sb.x_cm, sb.y_cm, inner, inner, 9, theme.lerp_color(theme.FIELD, theme.WHITE, 0.7), 0.6)
        outer = half + 11 + 2.5 * beat
        r._ticks(surf, sb.x_cm, sb.y_cm, outer, outer, 9, theme.lerp_color(theme.FIELD, theme.WHITE, 0.3), 0.5)
        r.text.blit(surf, r.T.cta_throw, "heading", vp.s(6.6), theme.WHITE, vp.px(r.W / 2, sb.y_cm + half + 13))

    def _tile(self, surf, key: str, w_cm: float, x: float, y: float, scale: float = 1.0, ring: bool = True,
              alpha: int = 255) -> None:
        if scale <= 0.02:
            return
        vp = self.r.vp
        base = vp.s(w_cm)
        img = self.r.sprites.product_scaled(key, base * scale, base, ring)
        if alpha < 255:
            img.set_alpha(alpha)
        surf.blit(img, img.get_rect(center=vp.px(x, y)))
        img.set_alpha(255)

    def _pulse_line(self, surf, cx: float, y: float, half: float, t: float) -> None:
        """The Market Pulse line: flat, one beat in the middle, a light running along it."""
        vp = self.r.vp
        pts = [(-1.0, 0.0), (-0.18, 0.0), (-0.1, -0.12), (-0.04, 0.55), (0.03, -0.9), (0.1, 0.2), (0.16, 0.0), (1.0, 0.0)]
        amp = 14.0
        line = [vp.px(cx + u * half, y + v * amp) for u, v in pts]
        pygame.draw.lines(surf, theme.lerp_color(theme.FIELD, theme.TURQ, 0.85), False, line, max(1, vp.s(0.55)))
        u = ((t * 0.45) % 1.0) * 2 - 1  # the light
        k = next(i for i in range(len(pts) - 1) if pts[i][0] <= u <= pts[i + 1][0])
        (u0, v0), (u1, v1) = pts[k], pts[k + 1]
        v = v0 + (v1 - v0) * (u - u0) / max(1e-6, u1 - u0)
        dot = self.r.sprites.dot(vp.sf(1.3), theme.WHITE)
        surf.blit(dot, dot.get_rect(center=vp.px(cx + u * half, y + v * amp)))

    # -- 01 attract: the circle of tiles ----------------------------------------------------------
    def _title(self, surf, t: float, dur: float, now: float) -> None:
        r, vp, W = self.r, self.r.vp, self.r.W
        products = r.cfg.raw()["products"]
        n = len(products)
        cx, cy, rad = W / 2, 152.0, 100.0
        turn = now / TITLE_TURN_S * math.tau
        for i, p in enumerate(products):
            a = -math.pi / 2 + i / n * math.tau + turn
            come = _back(_phase(t, 0.05 * i, 0.4))
            x = cx + math.cos(a) * rad * (0.85 + 0.15 * come)
            y = cy + math.sin(a) * rad * (0.85 + 0.15 * come) + 1.5 * math.sin(now * 1.4 + i)
            self._tile(surf, p["key"], 27.0, x, y, come)
        a = int(255 * _phase(t, 0.2, 0.5))
        r._micro(surf, r.T.attract_tag, vp.px(W / 2, 112), "center", theme.TURQ, 2.8)
        img = r.text.render(r.T.title, "title", vp.s(36), theme.WHITE)
        k = 0.9 + 0.1 * _ease(_phase(t, 0.0, 0.5))
        img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
        img.set_alpha(a)
        surf.blit(img, img.get_rect(center=vp.px(W / 2, 146)))
        img.set_alpha(255)
        r.text.blit(surf, r.T.subtitle, "body", vp.s(7), theme.MIST, vp.px(W / 2, 172), alpha=a)
        self._pulse_line(surf, W / 2, 192, 56, now)

    # -- 02 how to play: a round that plays itself ----------------------------------------------------
    FIELD = (24.0, 96.0, 276.0, 268.0)  # the demo field, cm (x0, y0, x1, y1)
    HIT1, NEW, SIZES, HIT2, CALM = 2.1, 2.5, 5.2, 9.0, 12.6  # the script's moments, s

    def _script(self) -> None:
        """Who is where in the demonstration. The products are taken in turn from the list."""
        keys = [p["key"] for p in self.r.cfg.raw()["products"]] or ["atiyeh"]
        k = lambda i: keys[i % len(keys)]  # noqa: E731
        N, H1, H2 = self.NEW, self.HIT1, self.HIT2
        self.hit1 = DemoTile(k(12), "medium", [(0, 78, 148)], 0.2, H1, hit=True)
        self.tiny = DemoTile(k(3), "tiny", [(0, 214, 226), (N, 222, 206)], 0.5, H2, hit=True)
        self.tiles = [
            self.hit1,
            DemoTile(k(11), "small", [(0, 210, 140), (N, 196, 128)], 0.35),
            self.tiny,
            DemoTile(k(0), "medium", [(0, 92, 232), (N, 108, 226)], 0.65),
            DemoTile(k(4), "small", [(N, 132, 172)], N + 0.1),  # takes the hit one's place
            DemoTile(k(8), "tiny", [(H2, 64, 132)], H2 + 0.5, extra=True),  # Market Rush brings more
            DemoTile(k(9), "small", [(H2, 160, 112)], H2 + 1.1, extra=True),
            DemoTile(k(7), "tiny", [(H2 + 1.2, 150, 236)], H2 + 1.6, extra=True),
        ]

    def _demo(self, surf, t: float, dur: float) -> None:
        r, vp, W = self.r, self.r.vp, self.r.W
        T = r.T
        x0, y0, x1, y1 = self.FIELD
        storm = _smooth(_phase(t, self.HIT2 + 0.05, 0.4)) * (1.0 - _smooth(_phase(t, self.CALM, 0.6)))
        if storm > 0.0:
            self._storm_bg(surf, storm, t)
        r.text.blit(surf, T.demo_title, "title", vp.s(13), theme.WHITE, vp.px(W / 2, 50))
        step = T.demo_hit if t < self.NEW else T.demo_new if t < self.SIZES else T.demo_sizes if t < self.HIT2 - 0.6 \
            else T.demo_rush
        start = 0.0 if t < self.NEW else self.NEW if t < self.SIZES else self.SIZES if t < self.HIT2 - 0.6 else self.HIT2 - 0.6
        p = _phase(t, start, 0.35)
        r.text.blit(surf, step, "heading", vp.s(7), theme.CORAL if storm > 0.5 else theme.TURQ,
                    vp.px(W / 2 + 10 * (1 - _ease(p)), 76), alpha=int(255 * p))
        # the field's frame: corner ticks, like the targets' own
        r._ticks(surf, (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2, 8,
                 theme.lerp_color(theme.FIELD, theme.MIST, 0.35), 0.45)
        sizes = _smooth(_phase(t, self.SIZES, 0.4)) * (1.0 - _smooth(_phase(t, self.HIT2 - 0.8, 0.4)))
        speed = 1.0 + 1.2 * storm
        fade_out = 1.0 - _smooth(_phase(t, dur - 0.7, 0.6))
        for tile in self.tiles:
            s = tile.scale(t)
            if s <= 0:
                continue
            if tile.extra:
                s *= 1.0 - _smooth(_phase(t, self.CALM, 0.4))
            x, y = tile.pos(t, speed)
            w = DEMO_TILE[tile.tier]
            alpha = int(255 * fade_out * (1.0 - 0.92 * sizes))
            self._tile(surf, tile.key, w, x, y, s, alpha=alpha)
            if tile.tier == "tiny" and s > 0.5:  # the tiny box keeps its ticks
                col = theme.lerp_color(theme.FIELD, theme.CORAL if storm > 0.5 else theme.WHITE, 0.6 * alpha / 255)
                r._ticks(surf, x, y, w / 2 + 3, w * 0.46 + 3, 5, col, 0.45)
            if s > 0.8 and alpha > 120:
                pts = int(r.cfg.targets[tile.tier].points)
                r.text.blit(surf, f"+{pts}", "numbers", vp.s(4.2), theme.MIST, vp.px(x, y + w * 0.46 + 6),
                            alpha=alpha)
        for target, when in ((self.hit1, self.HIT1), (self.tiny, self.HIT2)):
            self._throw(surf, target, when, t, speed)
        if sizes > 0.0:
            self._sizes(surf, sizes)
        if storm > 0.0:
            p = _phase(t, self.HIT2 + 0.1, 1.6)
            if p < 1.0:
                r.text.blit(surf, T.rush_title, "title", vp.s(26), theme.CORAL, vp.px(W / 2, 180),
                            alpha=int(235 * (1 - p * p)))
            m = f"×{float(r.cfg.rush.score_multiplier):g}"
            r.text.blit(surf, T.rush_pill.format(m=m), "heading", vp.s(6.5), theme.CORAL, vp.px(W / 2, 88),
                        alpha=int(255 * storm))

    def _storm_bg(self, surf, storm: float, t: float) -> None:
        """Market Rush on the demo field: the screen goes deep, faint candles run across."""
        r, vp = self.r, self.r.vp
        if self._deep is None or self._deep.get_size() != surf.get_size():
            self._deep = pygame.Surface(surf.get_size()).convert()
            self._deep.fill(theme.DEEP)
        full = storm >= 0.999  # fully on: plain copies, no blending (a full-screen blend costs ~10 ms)
        if full:
            surf.fill(theme.DEEP)
        else:
            self._deep.set_alpha(int(255 * storm))
            surf.blit(self._deep, (0, 0))
        x0, y0, x1, y1 = self.FIELD
        rect = vp.cm_rect(x0, y0, x1, y1)
        candles = r.candles
        clip = surf.get_clip()
        surf.set_clip(rect)
        candles.set_alpha(None if full else int(255 * storm))
        x = -int(vp.sf(t * 14.0) % max(1, candles.get_width()))
        surf.blit(candles, (rect.x + x, rect.y))
        surf.blit(candles, (rect.x + x + candles.get_width(), rect.y))
        candles.set_alpha(None)
        surf.set_clip(clip)

    def _throw(self, surf, target: DemoTile, when: float, t: float, speed: float) -> None:
        """A ball flies up from the player to the tile; at the hit, the rings with its name and points."""
        r, vp = self.r, self.r.vp
        fly = 0.45
        tx, ty = target.pos(when, speed)
        if when - fly <= t < when:
            p = (t - (when - fly)) / fly
            sx, sy = r.W / 2 + 40, 300.0
            x = sx + (tx - sx) * p
            y = sy + (ty - sy) * p - 60 * math.sin(p * math.pi) * 0.6
            for k in range(4):  # a short trail
                q = max(0.0, p - 0.06 * (k + 1))
                px = sx + (tx - sx) * q
                py = sy + (ty - sy) * q - 60 * math.sin(q * math.pi) * 0.6
                dot = r.sprites.dot(vp.sf(3.2 * (1 - 0.3 * q) * (1 - 0.18 * (k + 1))), theme.lerp_color(theme.FIELD, theme.WHITE, 0.5 - 0.1 * k))
                surf.blit(dot, dot.get_rect(center=vp.px(px, py)))
            ball = r.sprites.dot(vp.sf(4.2 * (1 - 0.3 * p)), theme.WHITE)
            surf.blit(ball, ball.get_rect(center=vp.px(x, y)))
        age = t - when
        if 0.0 <= age < 1.35:
            storm = target is self.tiny
            color = theme.CORAL if storm else theme.TURQ
            q = age / 0.5
            if q < 1.0:
                rad = vp.sf(DEMO_TILE[target.tier] / 2 + 18 * _ease(q))
                pygame.draw.aacircle(surf, theme.lerp_color(color, theme.FIELD, q), vp.px(tx, ty), int(rad), max(1, vp.s(1.0)))
            pts = int(r.cfg.targets[target.tier].points)
            if storm:
                pts = int(round(pts))  # the hit that starts the rush is not multiplied
            badge = Badge(tx, ty, r.product_name(target.key), f"+{pts}", "", color, storm, age=age)
            r.fx._draw_badge(surf, vp, badge, theme.DEEP if storm else theme.FIELD)

    def _sizes(self, surf, a: float) -> None:
        """The three sizes side by side with their points: the smaller, the more (03 SCORING)."""
        r, vp, W = self.r, self.r.vp, self.r.W
        T = r.T
        keys = [p["key"] for p in r.cfg.raw()["products"]]
        slots = (("medium", 222, keys[12 % len(keys)]), ("small", 150, keys[8 % len(keys)]), ("tiny", 78, keys[10 % len(keys)]))
        alpha = int(255 * a)
        for i, (tier, x, key) in enumerate(slots):
            w = DEMO_TILE[tier] * 1.25
            pop = _back(min(1.0, a * 1.3 - 0.1 * i))
            self._tile(surf, key, w, x, 168 - (w * 0.46), pop, alpha=alpha)
            pts = int(r.cfg.targets[tier].points)
            r.text.blit(surf, f"+{pts}", "numbers", vp.s(13), theme.WHITE, vp.px(x, 196), alpha=alpha)
            r.text.blit(surf, T[f"tier_{tier}"], "heading", vp.s(5.6), theme.MIST, vp.px(x, 214), alpha=alpha)
            if tier == "tiny":
                r.text.blit(surf, T.tiny_rush, "heading", vp.s(4.8), theme.CORAL, vp.px(x, 225), alpha=alpha)
        r.text.blit(surf, T.rule_size, "body", vp.s(5.2), theme.MIST, vp.px(W / 2, 246), alpha=alpha)

    # -- 02/03 leaderboard ------------------------------------------------------------------------
    def _board(self, surf, board: list[dict], t: float, now: float) -> None:
        r, vp, W = self.r, self.r.vp, self.r.W
        from .screens import _short
        r.text.blit(surf, r.T.leaderboard, "title", vp.s(20), theme.WHITE, vp.px(W - 16, 62), "midright")
        r._micro(surf, "TODAY · TOP 3", vp.px(16, 62), "midleft", theme.MIST, 2.8)
        show_names = r.cfg.results.show_names
        line = theme.lerp_color(theme.FIELD, theme.MIST, 0.22)
        for i, rec in enumerate(board[:3]):
            y = 112 + i * 54
            p = _ease(_phase(t, 0.15 + 0.15 * i, 0.45))
            if p <= 0:
                continue
            dx = 14 * (1 - p)
            a = int(255 * p)
            col = theme.TURQ if i == 0 else theme.lerp_color(theme.MIST, theme.FIELD, 0.25 * i)
            r.text.blit(surf, str(i + 1), "numbers", vp.s(34), col, vp.px(W - 16 + dx, y), "midright", alpha=a)
            name = _short(rec.get("player_name") or "", 16) if show_names else ""
            r.text.blit(surf, name or r.T.guest, "title", vp.s(11), theme.WHITE, vp.px(W - 70 + dx, y), "midright", alpha=a)
            r.text.blit(surf, str(rec.get("score", 0)), "numbers", vp.s(24), theme.WHITE, vp.px(16 - dx, y), "midleft", alpha=a)
            pygame.draw.line(surf, line, vp.px(16, y + 27), vp.px(W - 16, y + 27), max(1, vp.s(0.25)))
        # the products drift past along the bottom
        products = r.cfg.raw()["products"]
        n = len(products)
        w, gap = 18.0, 6.0
        span = n * (w + gap)
        shift = (now * 9.0) % span
        for i, p in enumerate(products):
            x = W + w - ((i * (w + gap) + shift) % span)
            if -w < x < W + w:
                self._tile(surf, p["key"], w, x, 258, ring=False)
