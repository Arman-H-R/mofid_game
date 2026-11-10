"""Camera hit detection, step by step. Run from this folder:  .venv\\Scripts\\python camtool.py <step>

  view                                   1. the camera works: live picture, fps, delay.  G grid, U lens fix, S snapshot
  calibrate --width-cm 200 --height-cm 150
                                         2a. a plain wall: click the 4 corners of a measured rectangle on the wall
                                             (top-left, top-right, bottom-right, bottom-left). Enter saves.
                                             --grid 3: click 3 x 3 points (rows, left to right) - also fits the lens
  calibrate --markers [--size-cm 60 34]  2b. a screen (monitor, projector, LED wall): markers fill the screen and the
                                             camera finds them by itself. (In the game: F2.)
  touch                                  3. hold a hand (or a stick) on the wall: the dot and the cm show where it is
  hits [--record]                        4. throw balls: every hit is drawn and printed (cm, delay)
  replay data/camtest/rec_YYYYmmdd-HHMMSS [--show]
                                         run the detector again on a recording (for tuning)

Settings come from config/settings.json, input.camera (camera address, ball size, thresholds). The
calibration is saved to input.camera.calibration_file and used by the game.
Keys in every window: Q or Esc quits, R relearns the background.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from market_pulse import config as game_config  # noqa: E402
from market_pulse.input.wallcam import markers  # noqa: E402
from market_pulse.input.wallcam.detect import HitDetector, Settings, TouchDetector  # noqa: E402
from market_pulse.input.wallcam.geometry import DEFAULT_LENS, Lens, WallMap  # noqa: E402
from market_pulse.input.wallcam.stream import open_stream  # noqa: E402

WIN = "Wall camera"
OUT = HERE / "data" / "camtest"
GREEN, YELLOW, RED, CYAN, WHITE = (80, 220, 80), (0, 220, 255), (60, 60, 255), (255, 220, 0), (255, 255, 255)


# -- helpers -------------------------------------------------------------------------------------
def load_cfg():
    cfg, warnings = game_config.load(HERE / "config" / "settings.json")
    for w in warnings:
        print("[settings]", w)
    return cfg


def calib_path(cfg) -> Path:
    p = Path(cfg.input.camera.calibration_file)
    return p if p.is_absolute() else HERE / p


def load_map(cfg, required=True) -> WallMap | None:
    m = WallMap.load(calib_path(cfg))
    if m is None and required:
        sys.exit(f"No calibration yet ({calib_path(cfg)}). Run:  camtool.py calibrate ...")
    return m


def text(img, s, org, color=WHITE, scale=0.6, thick=1):
    x, y = org
    (w, h), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    cv2.rectangle(img, (x - 3, y - h - 4), (x + w + 3, y + 5), (0, 0, 0), -1)
    cv2.putText(img, s, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def view_scale(size) -> float:
    return 1280.0 / size[0]


def upscale(img, scale):
    if abs(scale - 1.0) < 1e-3:
        return img.copy()
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)


def draw_wall(disp, wmap: WallMap, size, scale, grid=True, color=GREEN):
    if wmap is None:
        return
    if grid:
        for line in wmap.grid_lines(size, 50.0):
            cv2.polylines(disp, [np.round(line * scale).astype(np.int32)], False, (60, 140, 60), 1, cv2.LINE_AA)
    cv2.polylines(disp, [np.round(wmap.outline(size) * scale).astype(np.int32)], True, color, 2, cv2.LINE_AA)


def open_window():
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 1280, 720)


def window_open() -> bool:
    try:
        return cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) >= 1
    except cv2.error:
        return False


def keypress() -> int:
    k = cv2.pollKey()  # waitKey(1) blocks ~15 ms on Windows
    return -1 if k < 0 else k & 0xFF


def start_camera(cfg):
    cc = cfg.input.camera
    print(f"[camera] {cc.source} {cc.host if cc.source == 'ws' else cc.url} ({cc.stream} stream) ...")
    return open_stream(cc)


def wait_first(cam, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        fr = cam.wait_latest(0, 0.2)
        if fr is not None:
            return fr
    sys.exit(f"[camera] no picture after {timeout:.0f} s ({cam.status} {cam.error})")


def wall_panel(wmap: WallMap, hits, height=720, width=300):
    """A plan of the wall (cm grid) with the recent hits."""
    w, h = wmap.wall_size
    s = min((width - 40) / w, (height - 80) / h)
    panel = np.full((height, width, 3), 24, np.uint8)
    ox, oy = int((width - w * s) / 2), 50
    cv2.rectangle(panel, (ox, oy), (int(ox + w * s), int(oy + h * s)), GREEN, 2)
    for gx in np.arange(50, w, 50):
        cv2.line(panel, (int(ox + gx * s), oy), (int(ox + gx * s), int(oy + h * s)), (50, 90, 50), 1)
    for gy in np.arange(50, h, 50):
        cv2.line(panel, (ox, int(oy + gy * s)), (int(ox + w * s), int(oy + gy * s)), (50, 90, 50), 1)
    text(panel, f"wall {w:.0f} x {h:.0f} cm", (10, 25), WHITE, 0.55)
    for i, hit in enumerate(hits):
        c = YELLOW if i == len(hits) - 1 else (0, 140, 200)
        p = (int(ox + hit.x * s), int(oy + hit.y * s))
        cv2.circle(panel, p, 7 if i == len(hits) - 1 else 5, c, -1, cv2.LINE_AA)
    if hits:
        last = hits[-1]
        text(panel, f"x {last.x:.0f}  y {last.y:.0f} cm", (10, height - 15), YELLOW, 0.7, 2)
    return panel


# -- 1. view -------------------------------------------------------------------------------------
def cmd_view(args, cfg):
    cam = start_camera(cfg)
    fr = wait_first(cam)
    size = (fr.image.shape[1], fr.image.shape[0])
    wmap = load_map(cfg, required=False)
    lens = wmap.lens if wmap else Lens.from_dict(DEFAULT_LENS)
    K = lens.matrix(size)
    maps = cv2.initUndistortRectifyMap(K, lens.coeffs(), None, K, size, cv2.CV_16SC2)
    show_grid, fix_lens, last, ages = True, False, 0, []
    open_window()
    print("[view] G grid  U lens fix  S snapshot  Q quit")
    while window_open():
        fr = cam.wait_latest(last, 0.05)
        if fr is not None and fr.id != last:
            last = fr.id
            img = cv2.remap(fr.image, *maps, cv2.INTER_LINEAR) if fix_lens else fr.image
            disp = upscale(img, view_scale(size))
            if show_grid and not fix_lens:
                draw_wall(disp, wmap, size, view_scale(size))
            ages.append(time.perf_counter() - fr.t)
            ages = ages[-50:]
            text(disp, f"{cam.status}  {size[0]}x{size[1]}  {cam.fps:4.1f} fps  picture is {np.median(ages) * 1000:3.0f} ms old"
                       f"{'  [lens fixed]' if fix_lens else ''}{'  no calibration yet' if wmap is None else ''}",
                 (10, 28))
            cv2.imshow(WIN, disp)
        k = keypress()
        if k in (ord("q"), 27):
            break
        if k == ord("g"):
            show_grid = not show_grid
        if k == ord("u"):
            fix_lens = not fix_lens
        if k == ord("s") and fr is not None:
            OUT.mkdir(parents=True, exist_ok=True)
            p = OUT / f"snap_{time.strftime('%Y%m%d-%H%M%S')}.jpg"
            cv2.imwrite(str(p), fr.image)
            print("[view] saved", p)
    cam.stop()


# -- 2a. calibrate by clicking -----------------------------------------------------------------
def cmd_calibrate_click(args, cfg):
    if not (args.width_cm and args.height_cm):
        sys.exit("calibrate needs --width-cm and --height-cm (the rectangle you will click), or --markers")
    W, H = float(args.width_cm), float(args.height_cm)
    n = args.grid
    wall_pts = [(c / (n - 1) * W, r / (n - 1) * H) for r in range(n) for c in range(n)]
    if n == 2:
        wall_pts = [(0, 0), (W, 0), (W, H), (0, H)]
        names = ["top-left", "top-right", "bottom-right", "bottom-left"]
    else:
        names = [f"row {r + 1} point {c + 1} ({c / (n - 1) * W:.0f}, {r / (n - 1) * H:.0f} cm)"
                 for r in range(n) for c in range(n)]
    cam = start_camera(cfg)
    fr = wait_first(cam)
    size = (fr.image.shape[1], fr.image.shape[0])
    scale = view_scale(size)
    clicks: list[tuple[float, float]] = []
    state = {"map": None, "mouse": None}

    def on_mouse(event, x, y, flags, _):
        state["mouse"] = (x, y)
        if event == cv2.EVENT_LBUTTONDOWN and len(clicks) < len(wall_pts):
            clicks.append((x / scale, y / scale))
            state["map"] = None
            print(f"[calibrate] {names[len(clicks) - 1]}: picture ({x / scale:.1f}, {y / scale:.1f})")
        elif event == cv2.EVENT_RBUTTONDOWN and clicks:
            clicks.pop()
            state["map"] = None

    open_window()
    cv2.setMouseCallback(WIN, on_mouse)
    print(f"[calibrate] click {len(wall_pts)} points: {', '.join(names[:4])}{' ...' if n > 2 else ''}")
    print("[calibrate] right click / Backspace: undo   Enter: check, then Enter again: save   Q: quit")
    frozen = None
    last = 0
    while window_open():
        fr2 = cam.wait_latest(last, 0.05)
        if fr2 is not None and fr2.id != last:
            last = fr2.id
            if frozen is None or len(clicks) == 0:
                frozen = fr2.image
        disp = upscale(frozen, scale)
        for i, (cx, cy) in enumerate(clicks):
            p = (int(cx * scale), int(cy * scale))
            cv2.drawMarker(disp, p, YELLOW, cv2.MARKER_CROSS, 24, 2)
            text(disp, str(i + 1), (p[0] + 8, p[1] - 8), YELLOW)
        if state["mouse"] is not None and len(clicks) < len(wall_pts):  # a magnifier for exact clicks
            mx, my = state["mouse"]
            r = 24
            x0, y0 = max(0, mx - r), max(0, my - r)
            crop = disp[y0:y0 + 2 * r, x0:x0 + 2 * r]
            if crop.size:
                zoom = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
                zh, zw = zoom.shape[:2]
                cv2.drawMarker(zoom, (zw // 2, zh // 2), RED, cv2.MARKER_CROSS, 30, 1)
                disp[-zh - 10:-10, -zw - 10:-10] = zoom
        if state["map"] is not None:
            draw_wall(disp, state["map"], size, scale)
            m = state["map"]
            text(disp, f"fit: {m.points} points, error {m.rms_cm:.1f} cm (worst {m.max_cm:.1f})  lens k1 {m.lens.k1:+.3f}"
                       f"   Enter = save, right click = undo", (10, 60), GREEN)
        msg = (f"click {names[len(clicks)]}" if len(clicks) < len(wall_pts) else "all points: press Enter")
        text(disp, f"CALIBRATE {W:.0f} x {H:.0f} cm - {msg}", (10, 28), YELLOW)
        cv2.imshow(WIN, disp)
        k = keypress()
        if k in (ord("q"), 27):
            print("[calibrate] cancelled, nothing saved")
            break
        if k == 8 and clicks:
            clicks.pop()
            state["map"] = None
        if k in (13, 10) and len(clicks) == len(wall_pts):
            if state["map"] is None:
                old = load_map(cfg, required=False)
                lens = old.lens if (old is not None and old.method == "markers") else Lens.from_dict(DEFAULT_LENS)
                try:
                    state["map"] = WallMap.fit(clicks, wall_pts, size, (W, H), lens, method="click")
                except ValueError as exc:
                    print("[calibrate] cannot fit:", exc)
            else:
                state["map"].save(calib_path(cfg))
                print(f"[calibrate] saved {calib_path(cfg)} (error {state['map'].rms_cm:.1f} cm)")
                time.sleep(0.5)
                break
    cam.stop()


# -- 2b. calibrate with markers on a screen ------------------------------------------------------
def screen_size():
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    except (AttributeError, OSError):
        return 1920, 1080


def cmd_calibrate_markers(args, cfg):
    sw, sh = screen_size()
    W, H = (float(args.size_cm[0]), float(args.size_cm[1])) if args.size_cm else (sw / 10.0, sh / 10.0)
    cols, rows = cfg.input.camera.markers
    placed = markers.grid(W, H, cols, rows)
    board = cv2.cvtColor(markers.render(W, H, placed, sw, sh), cv2.COLOR_GRAY2BGR)
    cam = start_camera(cfg)
    fr = wait_first(cam)
    size = (fr.image.shape[1], fr.image.shape[0])
    name = "Markers"
    cv2.namedWindow(name, cv2.WINDOW_NORMAL)
    cv2.moveWindow(name, 0, 0)
    cv2.setWindowProperty(name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    col = markers.MarkerCollector(placed)
    t0, last, result = time.time(), 0, None
    print(f"[markers] showing {cols} x {rows} markers on the {sw}x{sh} screen ({W:.0f} x {H:.0f} cm). Point the camera at it.")
    while True:
        fr = cam.wait_latest(last, 0.05)
        if fr is not None and fr.id != last and result is None:
            last = fr.id
            col.add(cv2.cvtColor(fr.image, cv2.COLOR_BGR2GRAY))
            seen = col.averaged(min_count=5)
            if len(seen) >= max(4, int(0.7 * len(placed))) or (time.time() - t0 > 15 and len(seen) >= 4):
                img, wall = markers.pairs(seen, placed)
                try:
                    result = WallMap.fit(img, wall, size, (W, H), Lens.from_dict(DEFAULT_LENS), method="markers")
                    result.save(calib_path(cfg))
                    print(f"[markers] saved {calib_path(cfg)}: {len(seen)} markers, error {result.rms_cm:.2f} cm "
                          f"(worst {result.max_cm:.2f}), lens k1 {result.lens.k1:+.3f}")
                    t_done = time.time()
                except ValueError as exc:
                    print("[markers] fit failed:", exc)
                    col = markers.MarkerCollector(placed)
        disp = board.copy()
        msg = (f"camera sees {col.last_seen} of {len(placed)} markers ({time.time() - t0:.0f} s)" if result is None else
               f"SAVED - error {result.rms_cm:.1f} cm")
        text(disp, msg + "   (Q quits)", (20, sh - 20), (0, 120, 0) if result else (0, 0, 200), 0.8, 2)
        cv2.imshow(name, disp)
        k = keypress()
        if k in (ord("q"), 27) or (result is not None and time.time() - t_done > 2.5):
            break
        if result is None and time.time() - t0 > 60:
            print("[markers] the camera did not see enough markers in 60 s")
            break
    cam.stop()
    cv2.destroyAllWindows()


# -- 3. touch -------------------------------------------------------------------------------------
def cmd_touch(args, cfg):
    wmap = load_map(cfg)
    cam = start_camera(cfg)
    fr = wait_first(cam)
    size = (fr.image.shape[1], fr.image.shape[0])
    wmap_s = wmap
    det = TouchDetector(wmap_s, Settings.from_dict(cfg.input.camera))
    scale = view_scale(size)
    touches = []
    rec = Recorder() if args.record else None
    open_window()
    print("[touch] keep the wall clear for a second, then hold a hand still on it. R relearns, Q quits.")
    while window_open():
        fr = cam.get(0.05)
        if fr is None:
            if keypress() in (ord("q"), 27):
                break
            continue
        if rec:
            rec.add(fr)
        for h in det.process(fr.image, fr.t):
            touches.append(h)
            print(f"[touch] x {h.x:6.1f} cm   y {h.y:6.1f} cm")
        if cam.frames.qsize() > 2:
            continue  # behind: detect on every frame, draw only the newest
        disp = upscale(fr.image, scale)
        if det.mask is not None:
            tint = np.zeros_like(disp)
            tint[..., 2] = upscale(det.mask, scale)
            disp = cv2.addWeighted(disp, 1.0, tint, 0.35, 0)
        draw_wall(disp, wmap, size, scale)
        if det.tip is not None:
            px, py, x, y = det.tip
            cv2.circle(disp, (int(px * scale), int(py * scale)), 9, CYAN, 2, cv2.LINE_AA)
            text(disp, f"{x:.0f}, {y:.0f} cm", (int(px * scale) + 12, int(py * scale) - 12), CYAN)
        for i, h in enumerate(touches[-6:]):
            cv2.circle(disp, (int(h.px * scale), int(h.py * scale)), 12, YELLOW, 3, cv2.LINE_AA)
        text(disp, f"TOUCH  {cam.fps:4.1f} fps  touches {len(touches)}" +
             (f"   last: x {touches[-1].x:.0f}  y {touches[-1].y:.0f} cm" if touches else ""), (10, 28), YELLOW)
        cv2.imshow(WIN, np.hstack([disp, wall_panel(wmap, touches[-10:])]))
        k = keypress()
        if k in (ord("q"), 27):
            break
        if k == ord("r"):
            det.reset()
    cam.stop()
    if rec:
        rec.close()


# -- 4. hits --------------------------------------------------------------------------------------
class Recorder:
    def __init__(self):
        self.dir = OUT / f"rec_{time.strftime('%Y%m%d-%H%M%S')}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.f = open(self.dir / "times.csv", "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(["frame", "t"])
        self.n = 0

    def add(self, fr):
        cv2.imwrite(str(self.dir / f"{fr.id:06d}.jpg"), fr.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        self.w.writerow([fr.id, f"{fr.t:.6f}"])
        self.n += 1

    def close(self):
        self.f.close()
        print(f"[record] {self.n} frames in {self.dir}")


def draw_hits(disp, det: HitDetector, scale, hits, now):
    for b in det.blobs:
        x, y, w, h = b.box
        cv2.rectangle(disp, (int(x * scale), int(y * scale)), (int((x + w) * scale), int((y + h) * scale)), RED, 1)
    for tr in det.tracks + list(det.ended)[-3:]:
        pts = np.array([[p.px * scale, p.py * scale] for p in tr.pts[-15:]], np.int32)
        if len(pts) >= 2:
            cv2.polylines(disp, [pts], False, CYAN if tr in det.tracks else (120, 120, 120), 2, cv2.LINE_AA)
    for h in hits[-8:]:
        age = now - h.t
        if age < 3.0:
            p = (int(h.px * scale), int(h.py * scale))
            cv2.circle(disp, p, 16, YELLOW, 3, cv2.LINE_AA)
            text(disp, f"{h.x:.0f},{h.y:.0f}", (p[0] + 18, p[1] - 10), YELLOW)


def cmd_hits(args, cfg):
    wmap = load_map(cfg)
    cam = start_camera(cfg)
    fr = wait_first(cam)
    size = (fr.image.shape[1], fr.image.shape[0])
    settings = Settings.from_dict(cfg.input.camera)
    for kv in args.set or []:
        settings.set_text(*kv.split("=", 1))
    det = HitDetector(wmap, settings)
    scale = view_scale(size)
    rec = Recorder() if args.record else None
    OUT.mkdir(parents=True, exist_ok=True)
    log = open(OUT / "hits.csv", "a", newline="")
    logw = csv.writer(log)
    hits = []
    open_window()
    print(f"[hits] ball {settings.ball_cm:.0f} cm, wall {wmap.wall_size[0]:.0f} x {wmap.wall_size[1]:.0f} cm. "
          "Keep the wall clear for a second, then throw. R relearns, Q quits.")
    while window_open():
        fr = cam.get(0.05)
        if fr is None:
            if keypress() in (ord("q"), 27):
                break
            continue
        if rec:
            rec.add(fr)
        if cam.gap:
            cam.gap = False
            det.tracks.clear()
        new = det.process(fr.image, fr.t)
        now = time.perf_counter()
        for h in new:
            hits.append(h)
            late = (now - h.t) * 1000
            print(f"[hit {len(hits):3d}] x {h.x:6.1f} cm  y {h.y:6.1f} cm   {h.kind:5s} {h.speed:5.0f} cm/s  "
                  f"found {late:4.0f} ms after the hit")
            logw.writerow([time.strftime("%H:%M:%S"), len(hits), f"{h.x:.1f}", f"{h.y:.1f}", h.kind, f"{h.speed:.0f}",
                           f"{late:.0f}", rec.dir.name if rec else "", fr.id])
            log.flush()
        if cam.frames.qsize() > 2 and not new:
            continue
        disp = upscale(fr.image, scale)
        draw_wall(disp, wmap, size, scale, grid=False)
        draw_hits(disp, det, scale, hits, now)
        text(disp, f"HITS {len(hits)}   {cam.fps:4.1f} fps   tracks {len(det.tracks)}"
                   f"{'   REC' if rec else ''}{'   (light change)' if det.skipped and det.mask is not None and not det.blobs and not det.tracks else ''}",
             (10, 28), YELLOW)
        if new:
            cv2.imwrite(str(OUT / f"hit_{len(hits):03d}.jpg"), disp)
        cv2.imshow(WIN, np.hstack([disp, wall_panel(wmap, hits[-12:])]))
        k = keypress()
        if k in (ord("q"), 27):
            break
        if k == ord("r"):
            det.reset()
    cam.stop()
    log.close()
    if rec:
        rec.close()


# -- replay a recording ---------------------------------------------------------------------------
def cmd_replay(args, cfg):
    folder = Path(args.folder)
    wmap = load_map(cfg)
    settings = Settings.from_dict(cfg.input.camera)
    for kv in args.set or []:
        settings.set_text(*kv.split("=", 1))
    det = HitDetector(wmap, settings)
    with open(folder / "times.csv") as f:
        rows = list(csv.DictReader(f))
    hits = []
    if args.show:
        open_window()
    for row in rows:
        img = cv2.imread(str(folder / f"{int(row['frame']):06d}.jpg"))
        t = float(row["t"])
        for h in det.process(img, t):
            hits.append(h)
            print(f"[hit {len(hits):3d}] frame {row['frame']}  x {h.x:6.1f}  y {h.y:6.1f} cm  {h.kind} {h.speed:.0f} cm/s "
                  f"angle {h.angle:.0f}")
        if args.show:
            size = (img.shape[1], img.shape[0])
            disp = upscale(img, view_scale(size))
            draw_wall(disp, wmap, size, view_scale(size), grid=False)
            draw_hits(disp, det, view_scale(size), hits, t)
            text(disp, f"frame {row['frame']}", (10, 28))
            cv2.imshow(WIN, disp)
            k = cv2.waitKey(0 if args.step else 30) & 0xFF
            if k in (ord("q"), 27):
                break
    print(f"[replay] {len(rows)} frames, {len(hits)} hits")


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("view")
    c = sub.add_parser("calibrate")
    c.add_argument("--width-cm", type=float)
    c.add_argument("--height-cm", type=float)
    c.add_argument("--grid", type=int, default=2, choices=[2, 3, 4])
    c.add_argument("--markers", action="store_true")
    c.add_argument("--size-cm", type=float, nargs=2, metavar=("W", "H"), help="the screen's size, for --markers")
    t = sub.add_parser("touch")
    t.add_argument("--record", action="store_true")
    h = sub.add_parser("hits")
    h.add_argument("--record", action="store_true")
    h.add_argument("--set", action="append", metavar="KEY=VALUE", help="detector setting, e.g. threshold=25")
    r = sub.add_parser("replay")
    r.add_argument("folder")
    r.add_argument("--show", action="store_true")
    r.add_argument("--step", action="store_true")
    r.add_argument("--set", action="append", metavar="KEY=VALUE")
    args = p.parse_args()
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
    cfg = load_cfg()
    if args.cmd == "view":
        cmd_view(args, cfg)
    elif args.cmd == "calibrate":
        (cmd_calibrate_markers if args.markers else cmd_calibrate_click)(args, cfg)
    elif args.cmd == "touch":
        cmd_touch(args, cfg)
    elif args.cmd == "hits":
        cmd_hits(args, cfg)
    elif args.cmd == "replay":
        cmd_replay(args, cfg)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
