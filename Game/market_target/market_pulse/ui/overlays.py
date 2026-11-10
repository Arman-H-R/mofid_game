"""Operator tools drawn over the game: F3 diagnostics and F2 sensor calibration."""
from __future__ import annotations

import time

import pygame

from ..config import layout_left, layout_top
from . import theme
from .text import Text
from .theme import Viewport

MONO_PX_CM = 3.0


def draw_debug(surf: pygame.Surface, vp: Viewport, text: Text, info: dict) -> None:
    lines = [
        f"FPS {info['fps']:.0f}   frame {info['frame_ms']:.1f} ms   {vp.win_w}x{vp.win_h}   wall {vp.rect.w}x{vp.rect.h}",
        f"state {info['state']}   paused {info['paused']}   simulation {info['simulation']}",
    ]
    rnd = info.get("round")
    if rnd is not None:
        lines.append(
            f"round: score {rnd.score}  throws {rnd.throws}/{rnd.balls}  streak {rnd.streak}  "
            f"targets {len(rnd.targets)}  motion x{rnd.last_motion_scale:.2f}  grid-fallback {rnd.last_layout_fallback}"
        )
    for s in info["inputs"]:
        age = s["last_packet_age"]
        age_s = "never" if age is None else f"{age:.1f}s ago"
        lines.append(
            f"input {s['name']}: {s['state']}  packets {s['packets']}  bad {s['bad']}  events {s['events']}  last {age_s}"
            + (f"  {s['detail']}" if s.get("detail") else "")
            + (f"  ERROR {s['error']}" if s["error"] else "")
        )
    flt = info["filter"]
    lines.append(
        f"impacts accepted {info['accepted']}  merged {flt.merged}  rebounds {flt.rebounds}  out-of-range {info['out_of_range']}"
    )
    fusion = info.get("fusion")
    if fusion is not None:
        st = fusion.stats.as_dict()
        lines.append(
            f"fusion {'on' if fusion.enabled else 'off'} {fusion.window_s * 1000:.0f} ms: both {st['fused']}  "
            f"lidar {st['lidar_only']}  camera {st['camera_only']}  duplicates {st['duplicates']} (late {st['late']})  "
            f"repeats {st['repeats']}  refused {st['refused']}  rejected {st['rejected']}  waiting {fusion.pending}  "
            f"wait avg {st['wait_avg_ms']} max {st['wait_max_ms']} ms"
        )
    if info.get("webhook"):
        wh = info["webhook"]
        lines.append(f"webhook sent {wh.sent}  failed {wh.failed}  {wh.last_error[:60]}")
    if info.get("panel"):
        lines.append(f"phone page {info['panel'].url or info['panel'].error[:60]}")
    if info.get("camera"):
        lines.append(info["camera"].status())
    if info.get("errors"):
        lines.append(f"recovered frame errors: {info['errors']}")
    for w in info.get("warnings", [])[:6]:
        lines.append(f"config: {w}")
    px = vp.s(MONO_PX_CM)
    imgs = [text.render(line, "latin", px, theme.WHITE, localize=False) for line in lines]
    w = max(i.get_width() for i in imgs) + vp.s(4)
    h = sum(int(i.get_height() * 1.1) for i in imgs) + vp.s(3)
    box = pygame.Surface((w, h), pygame.SRCALPHA)
    box.fill((*theme.BLUE_DEEP, 225))
    y = vp.s(1.5)
    for img in imgs:
        box.blit(img, (vp.s(2), y))
        y += int(img.get_height() * 1.1)
    surf.blit(box, (vp.rect.x + vp.s(2), vp.rect.y + vp.s(2)))


def draw_calibration(surf: pygame.Surface, vp: Viewport, text: Text, cfg, recent, capture=None, lidar=None,
                     board=None, camera=None, fusion=None) -> None:
    """A centimetre grid over the whole wall plus the last sensor impacts.

    Throw at the corner crosses and adjust input.calibration until the green
    dots land on them. Impacts are not counted while this overlay is open.
    capture: the lidar's calibration points being taken (C), one cross after the other.
    board: the camera's calibration markers (K), drawn over the whole wall until the camera has found
    them. camera: the wall camera, for its state and a small picture. fusion: its counts.
    """
    W, H = cfg.wall.width_cm, cfg.wall.height_cm
    if board is not None:
        _draw_board(surf, vp, text, board, camera, W, H)
        return
    shade = pygame.Surface(vp.rect.size, pygame.SRCALPHA)
    shade.fill((*theme.WHITE, 150))
    surf.blit(shade, vp.rect.topleft)
    thin = max(1, vp.s(0.2))
    for x in range(0, int(W) + 1, 50):
        pygame.draw.line(surf, theme.BLUE_SOFT, vp.px(x, 0), vp.px(x, H), thin)
        text.blit(surf, f"{x}", "latin", vp.s(2.6), theme.DARK_BLUE, vp.px(x + 1, 1), "topleft", localize=False)
    for y in range(0, int(H) + 1, 50):
        pygame.draw.line(surf, theme.BLUE_SOFT, vp.px(0, y), vp.px(W, y), thin)
        text.blit(surf, f"{y}", "latin", vp.s(2.6), theme.DARK_BLUE, vp.px(1, y + 1), "topleft", localize=False)
    lay = cfg.layout
    field = vp.cm_rect(lay.side_margin_cm, lay.hud_height_cm, W - lay.side_margin_cm, H - lay.footer_height_cm)
    pygame.draw.rect(surf, theme.PURPLE, field, max(1, vp.s(0.4)))
    sb = lay.start_button
    sx = sb.x_cm + layout_left(cfg)  # in the menus' 3:4 layout, centred on a wider or taller wall
    sy = sb.y_cm + layout_top(cfg)
    pygame.draw.rect(surf, theme.TURQUOISE, vp.cm_rect(sx - sb.size_cm / 2, sy - sb.size_cm / 2,
                                                       sx + sb.size_cm / 2, sy + sb.size_cm / 2), thin)
    for cx, cy in ((25, 25), (W - 25, 25), (25, H - 25), (W - 25, H - 25), (W / 2, H / 2)):
        c = vp.px(cx, cy)
        r = vp.s(8)
        pygame.draw.line(surf, theme.RED, (c[0] - r, c[1]), (c[0] + r, c[1]), max(1, vp.s(0.6)))
        pygame.draw.line(surf, theme.RED, (c[0], c[1] - r), (c[0], c[1] + r), max(1, vp.s(0.6)))
    now = time.perf_counter()
    for rx, ry, x, y, ok, source, t in recent:
        fade = max(0.25, 1.0 - (now - t) / 20.0)
        col = theme.lerp_color(theme.TURQUOISE if ok else theme.RED, theme.WHITE, 1 - fade)
        c = vp.px(x, y)
        pygame.draw.aacircle(surf, col, c, vp.s(3), 0 if ok else max(1, vp.s(0.5)))
        if source == "lidar":
            raw = f"lidar({rx:.0f},{ry:.0f})mm"
        elif source == "camera":
            raw = f"camera px({rx:.0f},{ry:.0f})"
        else:
            raw = f"{source} raw({rx:.3f},{ry:.3f})"
        text.blit(surf, f"{raw} wall({x:.0f},{y:.0f})cm", "latin", vp.s(2.4),
                  theme.DARK_BLUE, (c[0] + vp.s(4), c[1]), "midleft", localize=False)
    text.blit(surf, "CALIBRATION (F2) - impacts are not counted", "latin", vp.s(4), theme.PURPLE,
              vp.px(W / 2, H / 2 + 14), localize=False)
    if lidar is not None:
        st = lidar.status()
        line = f"LiDAR {st['state']}  {st['detail'] or st['error']}".rstrip()
        if capture is None:
            line += "   C: calibrate the LiDAR   F6: scan the background again"
        text.blit(surf, line, "latin", vp.s(3), theme.DARK_BLUE, vp.px(W / 2, H / 2 + 24), localize=False)
    if camera is not None:
        st = camera.status()
        line = f"Camera {st['state']} {st['fps']:.0f} fps  {st['detail'] or st['error']}".rstrip()
        if capture is None:
            line += "   K: calibrate the camera with markers   F7: learn the empty wall again"
        text.blit(surf, line, "latin", vp.s(3), theme.DARK_BLUE, vp.px(W / 2, H / 2 + 31), localize=False)
        _draw_preview(surf, vp, text, camera, W, H)
    if fusion is not None and lidar is not None and camera is not None:
        st = fusion.stats.as_dict()
        line = (f"Fusion: both {st['fused']}  LiDAR alone {st['lidar_only']}  camera alone {st['camera_only']}  "
                f"duplicates {st['duplicates']}  (dots: every sensor's own detections)")
        text.blit(surf, line, "latin", vp.s(3), theme.DARK_BLUE, vp.px(W / 2, H / 2 + 38), localize=False)
    if capture is not None:
        _draw_capture(surf, vp, text, capture, W, H, now)


def _draw_preview(surf: pygame.Surface, vp: Viewport, text: Text, camera, W: float, H: float) -> None:
    """What the camera sees (the wall's outline in green, the last hits in red), in a corner."""
    rgb = camera.preview
    if rgb is None:
        return
    h, w = rgb.shape[:2]
    img = pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB")
    tw = min(vp.rect.w // 3, vp.s(W * 0.4))
    th = max(1, int(round(h * tw / w)))
    img = pygame.transform.smoothscale(img, (tw, th))
    pos = (vp.rect.right - tw - vp.s(3), vp.rect.bottom - th - vp.s(3))
    pygame.draw.rect(surf, theme.DARK_BLUE, pygame.Rect(pos[0] - 2, pos[1] - 2, tw + 4, th + 4))
    surf.blit(img, pos)
    text.blit(surf, "camera", "latin", vp.s(2.6), theme.DARK_BLUE, (pos[0], pos[1] - vp.s(1)), "bottomleft",
              localize=False)


_BOARD: dict = {}  # (size, wall, grid) -> the markers as a surface (made once)


def _draw_board(surf: pygame.Surface, vp: Viewport, text: Text, board, camera, W: float, H: float) -> None:
    """The camera's calibration markers over the whole wall, black on white, at their places in wall cm."""
    from ..input.wallcam import markers  # OpenCV: only when the camera is calibrated
    key = (vp.rect.size, W, H, tuple(board))
    img = _BOARD.get(key)
    if img is None:
        _BOARD.clear()
        gray = markers.render(W, H, board, vp.rect.w, vp.rect.h)
        rgb = gray[:, :, None].repeat(3, axis=2)
        img = pygame.image.frombuffer(rgb.tobytes(), (vp.rect.w, vp.rect.h), "RGB").copy()
        _BOARD[key] = img
    surf.blit(img, vp.rect.topleft)
    rows = len({p.cy for p in board})
    y = H / max(rows, 1)  # between the first two rows of markers: never over one
    seen = camera.markers_seen if camera is not None else 0
    text.blit(surf, f"Camera calibration: the camera sees {seen} of {len(board)} markers   K / Esc: cancel",
              "latin", vp.s(3.2), theme.PURPLE, vp.px(W / 2, y), localize=False)


def _draw_capture(surf: pygame.Surface, vp: Viewport, text: Text, capture, W: float, H: float, now: float) -> None:
    for i, (cx, cy) in enumerate(capture.targets):
        c = vp.px(cx, cy)
        if i < capture.index:  # done
            pygame.draw.aacircle(surf, theme.TURQUOISE, c, vp.s(5), max(1, vp.s(0.8)))
        elif i == capture.index:  # the one to hit now: a pulsing ring
            r = vp.s(10 + 3 * abs(((now * 1.6) % 2) - 1))
            pygame.draw.aacircle(surf, theme.PURPLE, c, r, max(2, vp.s(1.2)))
            text.blit(surf, str(i + 1), "latin", vp.s(5), theme.PURPLE, (c[0], c[1] + vp.s(16)), localize=False)
    target = capture.target()
    if target is not None:
        side = "top" if target[1] < H / 2 else "bottom"
        side += "-left" if target[0] < W / 2 else "-right"
        text.blit(surf, f"LiDAR calibration: hit cross {capture.index + 1} of {len(capture.targets)} ({side})",
                  "latin", vp.s(4.4), theme.PURPLE, vp.px(W / 2, H / 2 - 16), localize=False)
        text.blit(surf, "throw the ball or touch it   Backspace: redo the last   C / Esc: cancel",
                  "latin", vp.s(3), theme.DARK_BLUE, vp.px(W / 2, H / 2 - 6), localize=False)
