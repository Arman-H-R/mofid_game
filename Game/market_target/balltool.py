"""See the ball detection, frame by frame: what the camera sees, what changed, every blob and why it was
or was not taken as the ball. Run from this folder (close the game first: it uses the camera and the CPU):

  python balltool.py                       live camera (settings and calibration from config/settings.json)
  python balltool.py --record              live, and save the frames to data/camtest/rec_ball_... (send it to me)
  python balltool.py data/camtest/rec_...  a recording   [--calib data/camtest/calib_200044.json]

Left: the camera picture.
  GREEN box   = the ball (taken). Text: size (1.00 = a ball's size there; it must be size_min-size_max) and speed.
  ORANGE box  = taken, but its size does not fit a ball there: it cannot become a hit.
  RED box     = thrown away, with the reason:
                  small / big     too few / too many changed pixels for a ball
                  edge            touches the edge of the area (a person, an arm, a ball in the hand)
                  line / ragged   not ball-shaped
                  part of big     part of a bigger changed region (a person)
                  not light NN%   only NN% of it is lighter than the wall (needs 18%)
  CYAN line   = the ball's path (track).   YELLOW ring = a HIT.
  Green frame = the wall's picture; the hit area is inside it (no hits below its bottom edge).
Right: what changed.  WHITE = changed from the empty wall AND from the last frame (this is what is used),
  BLUE = only from the empty wall, RED = only from the last frame.
Bottom: one mark per frame: green = ball seen, red = only thrown-away blobs, grey = nothing, yellow = hit.

Sliders (window "balltool settings"): every detector setting, live. Hover the mouse over one to read
what it does (in Persian); click or drag to set it, the mouse wheel moves it one step. They start from config/settings.json
and are NOT saved there (W writes them to data/camtest/balltool_settings_<time>.json). With --record every
change is written next to the frames too (settings.csv), so a recording can be replayed with them.

Keys: SPACE pause / play    A or <-  back    D or ->  forward (while paused)
      C colour mode on/off   [ ]  threshold down/up   R learn the empty wall again
      H a recording: start again from its beginning (with the sliders as they are now)
      W save the sliders     G save them into the game's settings    S save this picture    Q or Esc quit
Every frame is also written to data/camtest/balltool_<time>.csv (frame, time, what was seen, why).
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import json
import math
import sys
import time
from collections import deque
from pathlib import Path

import project_python  # noqa: E402

project_python.relaunch()  # the .venv Python: pygame-ce shapes Persian

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from market_pulse import config as game_config  # noqa: E402
from market_pulse.input.wallcam.detect import HitDetector, Settings  # noqa: E402
from market_pulse.input.wallcam.geometry import WallMap  # noqa: E402
from market_pulse.input.wallcam.stream import open_stream  # noqa: E402
from balltool_panel import SLIDERS, Sliders  # noqa: E402

OUT = HERE / "data" / "camtest"
WIN = "ball detection - balltool"
VIEW_W = 960  # each panel this wide
HISTORY = 300  # frames kept for going back
GREEN, ORANGE, RED, CYAN, YELLOW, WHITE, GREY = (60, 220, 60), (0, 160, 255), (60, 60, 255), (255, 220, 0), (0, 240, 255), (255, 255, 255), (110, 110, 110)
KEY_LEFT, KEY_RIGHT = 2424832, 2555904  # cv2.waitKeyEx on Windows
def settings_dict(det_s) -> dict:
    return {n: getattr(det_s, n) for n, *_ in SLIDERS}


def save_to_game(det_s) -> None:
    """G: the sliders the game has a setting for go into config/settings.json, input.camera (the game
    uses them on its next start, or at once from F5). The others stay in balltool only (W saves them)."""
    path = HERE / "config" / "settings.json"
    known = game_config.DEFAULTS["input"]["camera"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cam = data.setdefault("input", {}).setdefault("camera", {})
        saved = []
        for name, value in settings_dict(det_s).items():
            if name in known:
                cam[name] = value
                saved.append(f"{name}={value}")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
        print(f"[balltool] saved to {path}: {', '.join(saved)}")
        skipped = [n for n, *_ in SLIDERS if n not in known]
        print(f"[balltool] not game settings (balltool only, W saves them): {', '.join(skipped)}")
    except (OSError, ValueError) as exc:
        print(f"[balltool] could not save to {path}: {exc}")


def text(img, s, org, color=WHITE, scale=0.5, thick=1):
    x, y = int(org[0]), int(org[1])
    (w, h), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    x = max(2, min(x, img.shape[1] - w - 4))
    y = max(h + 4, min(y, img.shape[0] - 6))
    cv2.rectangle(img, (x - 2, y - h - 3), (x + w + 2, y + 4), (0, 0, 0), -1)
    cv2.putText(img, s, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def load_map(cfg, calib: Path | None) -> WallMap:
    cc = cfg.input.camera
    if calib is None:
        calib = Path(cc.calibration_file)
        calib = calib if calib.is_absolute() else HERE / calib
    m = WallMap.load(calib)
    if m is None:
        sys.exit(f"No calibration in {calib}: press F2 then C in the game first.")
    if m.method == "markers":  # the game's cm -> the picture's real cm (Screen Width / Height), as the game does
        sw = float(cc.get("screen_width_cm", 0) or 0)
        sh = float(cc.get("screen_height_cm", 0) or 0)
        w, h = m.wall_size
        real = (sw, sh) if sw > 0 and sh > 0 else (sw, h * sw / w) if sw > 0 else (w * sh / h, sh) if sh > 0 else None
        if real:
            m = m.scaled_to(real)
    print(f"[balltool] calibration {calib.name}: picture {m.wall_size[0]:.0f} x {m.wall_size[1]:.0f} cm")
    return m


class Frames:
    """Live camera or a recording folder: next() -> (frame id, image, t) or None at the end."""

    def __init__(self, cfg, folder: Path | None, record: bool):
        self.folder, self.cam, self.rec = folder, None, None
        if folder is not None:
            with open(folder / "times.csv") as f:
                self.rows = [(int(r["frame"]), float(r["t"])) for r in csv.DictReader(f)]
            self.i = 0
            print(f"[balltool] {len(self.rows)} frames in {folder}")
        else:
            cc = cfg.input.camera
            print(f"[balltool] camera {cc.host} ({cc.stream}) ...")
            self.cam = open_stream(cc)
            if record:
                self.dir = OUT / f"rec_ball_{time.strftime('%Y%m%d-%H%M%S')}"
                self.dir.mkdir(parents=True, exist_ok=True)
                self.rec = open(self.dir / "times.csv", "w", newline="")
                self.recw = csv.writer(self.rec)
                self.recw.writerow(["frame", "t"])
                self.setf = open(self.dir / "settings.csv", "w", newline="", encoding="utf-8")
                self.setw = csv.writer(self.setf)
                self.setw.writerow(["frame", "setting", "value"])
                self.last_id = 0
                print(f"[balltool] recording to {self.dir}")

    def log_settings(self, items):
        """With --record: the settings from this frame on (name, value)."""
        if self.rec:
            for name, value in items:
                self.setw.writerow([self.last_id, name, value])
            self.setf.flush()

    def restart(self) -> bool:
        if self.cam is None:
            self.i = 0
            return True
        return False

    @property
    def fps(self) -> float:
        return self.cam.fps if self.cam else 0.0

    @property
    def gap(self) -> bool:
        if self.cam and self.cam.gap:
            self.cam.gap = False
            return True
        return False

    def next(self, wait: float = 0.05):
        if self.cam is None:
            while self.i < len(self.rows):
                fid, t = self.rows[self.i]
                self.i += 1
                img = cv2.imread(str(self.folder / f"{fid:06d}.jpg"))
                if img is not None:
                    return fid, img, t
            return None
        fr = self.cam.get(wait)
        if fr is None:
            return False  # nothing yet
        if self.rec:
            cv2.imwrite(str(self.dir / f"{fr.id:06d}.jpg"), fr.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
            self.recw.writerow([fr.id, f"{fr.t:.6f}"])
            self.last_id = fr.id
        return fr.id, fr.image, fr.t

    def close(self):
        if self.cam:
            self.cam.stop()
        if self.rec:
            self.rec.close()
            self.setf.close()
            print(f"[balltool] saved the recording to {self.dir}")


def make_detector(wmap, cfg, color=None, threshold=None) -> HitDetector:
    s = Settings.from_dict(cfg.input.camera)
    if color is not None:
        s.color = color
    if threshold is not None:
        s.threshold = threshold
    det = HitDetector(wmap, s)
    det.debug = True
    print(f"[balltool] ball {s.ball_cm:.0f} cm ({s.ball_tone}), threshold {s.threshold}, colour {'on' if s.color else 'off'}")
    return det


def look(det: HitDetector, t: float):
    """The taken blobs with their size ratio, speed and whether they could be a hit."""
    out = []
    if not det.blobs:
        return out
    wall = det.map.to_wall([(b.px, b.py) for b in det.blobs], det.size)
    for b, (wx, wy) in zip(det.blobs, wall):
        ratio = det._size_ratio(float(wx), float(wy), b.diam)
        speed = None
        for tr in det.tracks:
            p = tr.pts[-1]
            if abs(p.t - t) < 1e-6 and abs(p.px - b.px) < 1e-3 and abs(p.py - b.py) < 1e-3 and len(tr.pts) >= 2:
                speed = tr.velocity()[2]
        out.append((b, float(wx), float(wy), ratio, det._size_ok(ratio), det._inside(float(wx), float(wy)), speed))
    return out


def draw(img, det: HitDetector, seen, hits_now, recent_hits, t, scale):
    disp = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    size = det.size
    cv2.polylines(disp, [np.round(det.map.outline(size) * scale).astype(np.int32)], True, GREEN, 1, cv2.LINE_AA)
    if det.roi is not None:
        cs, _ = cv2.findContours(det.roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.polylines(disp, [np.round(c * scale).astype(np.int32) for c in cs], True, GREY, 1, cv2.LINE_AA)

    def box(b, color, label):
        x, y, w, h = b
        p1, p2 = (int(x * scale) - 2, int(y * scale) - 2), (int((x + w) * scale) + 2, int((y + h) * scale) + 2)
        cv2.rectangle(disp, p1, p2, color, 2)
        if label:
            text(disp, label, (p1[0], p1[1] - 4), color, 0.45)

    for b, reason in det.rejects:
        box(b, RED, reason)
    for tr in list(det.ended)[-3:] + det.tracks:
        pts = np.array([[p.px * scale, p.py * scale] for p in tr.pts[-20:]], np.int32)
        if len(pts) >= 2:
            cv2.polylines(disp, [pts], False, CYAN if tr in det.tracks else GREY, 2, cv2.LINE_AA)
            for q in pts:
                cv2.circle(disp, tuple(int(v) for v in q), 2, CYAN, -1)
    for b, wx, wy, ratio, ok, inside, speed in seen:
        label = f"size {ratio:.2f}"
        if speed is not None:
            label += f"  {speed / 100:.1f} m/s"
        if not inside:
            label += "  outside"
        box(b.box, GREEN if ok and inside else ORANGE, label)
    for h in recent_hits:
        if 0 <= t - h.t < 1.5:
            p = (int(h.px * scale), int(h.py * scale))
            cv2.circle(disp, p, 22, YELLOW, 3, cv2.LINE_AA)
            text(disp, f"HIT {h.kind} {h.x:.0f},{h.y:.0f} cm  size {h.size:.2f}", (p[0] + 25, p[1] + 5), YELLOW, 0.55)
    return disp


def mask_view(det: HitDetector, scale):
    h, w = det.size[1], det.size[0]
    m = np.zeros((h, w, 3), np.uint8)
    new, moved = getattr(det, "dbg_new", None), getattr(det, "dbg_moved", None)
    if new is not None and new.shape == (h, w):
        roi = det.roi > 0
        m[new & ~moved & roi] = (160, 60, 0)
        m[moved & ~new & roi] = (0, 0, 160)
        m[new & moved & roi] = WHITE
    m = cv2.resize(m, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    cv2.polylines(m, [np.round(det.map.outline(det.size) * scale).astype(np.int32)], True, GREEN, 1, cv2.LINE_AA)
    return m


def timeline(states, width, height=26):
    bar = np.full((height, width, 3), 20, np.uint8)
    step = max(2, width // HISTORY)
    for i, st in enumerate(list(states)[-(width // step):]):
        color = {0: (50, 50, 50), 1: RED, 2: GREEN, 3: YELLOW}[st]
        cv2.rectangle(bar, (i * step, 4 if st == 3 else 8), (i * step + step - 1, height - 4), color, -1)
    return bar


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", help="a recording (data/camtest/rec_...); none = the live camera")
    ap.add_argument("--calib", type=Path, help="calibration for a recording (default: the game's)")
    ap.add_argument("--record", action="store_true", help="live: save the frames too")
    ap.add_argument("--color", choices=["on", "off"], help="colour mode (default: as in the settings)")
    args = ap.parse_args()
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
    cfg, warnings = game_config.load(HERE / "config" / "settings.json")
    for w in warnings:
        print("[settings]", w)
    wmap = load_map(cfg, args.calib)
    src = Frames(cfg, Path(args.folder) if args.folder else None, args.record)
    color = None if args.color is None else args.color == "on"
    det = make_detector(wmap, cfg, color)
    sliders = Sliders(det, HERE)
    src.log_settings(settings_dict(det.s).items())
    OUT.mkdir(parents=True, exist_ok=True)
    logf = open(OUT / f"balltool_{time.strftime('%Y%m%d-%H%M%S')}.csv", "w", newline="", encoding="utf-8")
    log = csv.writer(logf)
    log.writerow(["frame", "t", "balls", "ball_sizes", "speeds_m_s", "thrown_away", "hit"])
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 1600, 620)
    history: deque = deque(maxlen=HISTORY)  # rendered frames, for going back
    states: deque = deque(maxlen=HISTORY)
    hits, n_hits = deque(maxlen=10), 0
    paused, back = False, 0  # back: frames back in the history while paused
    ended = False
    step = False  # one frame forward while paused
    shown = None
    while True:
        try:
            if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break
        sliders.draw()
        moved = sliders.apply(det)
        for name, val in moved:
            print(f"[balltool] {name} = {val}")
        src.log_settings(moved)
        if (not paused or step) and not ended:
            step = False
            got = src.next()
            if got is None:
                ended, paused = True, True
                print("[balltool] end of the recording (A / <- to look back, Q to quit)")
            elif got is not False:
                fid, img, t = got
                if src.gap:
                    det.tracks.clear()
                t0 = time.perf_counter()
                new_hits = det.process(img, t)
                ms = (time.perf_counter() - t0) * 1000
                hits.extend(new_hits)
                n_hits += len(new_hits)
                seen = look(det, t)
                st = 3 if new_hits else 2 if seen else 1 if det.rejects else 0
                states.append(st)
                for h in new_hits:
                    print(f"[hit {n_hits:3d}] frame {fid}  {h.kind:7s} x {h.x:5.0f} y {h.y:5.0f} cm  size {h.size:.2f}  "
                          f"{h.speed / 100:.1f} m/s")
                log.writerow([fid, f"{t:.3f}", len(seen), " ".join(f"{r:.2f}" for _, _, _, r, *_ in seen),
                              " ".join(f"{sp / 100:.1f}" for *_, sp in seen if sp is not None),
                              "; ".join(r for _, r in det.rejects), " ".join(h.kind for h in new_hits)])
                scale = VIEW_W / img.shape[1]
                left = draw(img, det, seen, new_hits, hits, t, scale)
                right = mask_view(det, scale)
                s = det.s
                info = (f"frame {fid}   {src.fps:4.1f} fps   {ms:4.1f} ms   HITS {n_hits}   balls {len(seen)}   "
                        f"thrown away {len(det.rejects)}   threshold {s.threshold}   colour {'ON' if s.color else 'off'}"
                        f"   ball {s.ball_cm:.0f} cm = {s.ball_cm * det.ppc:.0f} px"
                        f"{'   LIGHT CHANGE' if det.mask is not None and not det.blobs and np.count_nonzero(det.mask) > s.max_fg_share * det.roi_count else ''}")
                top = np.hstack([left, right])
                bar = np.zeros((34, top.shape[1], 3), np.uint8)
                text(bar, info, (8, 23), YELLOW, 0.55)
                history.append(np.vstack([bar, top]))
                back = 0
        if history:
            frame = history[-1 - back]
            pic = np.vstack([frame, timeline(list(states)[:len(states) - back], frame.shape[1])])
            if paused:
                text(pic, "PAUSED" + (f"  ({back} frames back)" if back else "") + "   SPACE play  A/D step",
                     (pic.shape[1] - 430, 23), WHITE, 0.55)
            if pic is not shown:
                cv2.imshow(WIN, pic)
                shown = pic
        k = cv2.waitKeyEx(30 if paused else 1)
        if k < 0:
            continue
        ch = k & 0xFF if k < 256 else -1
        if ch in (ord("q"), 27):
            break
        if ch == ord(" "):
            paused, back = (not paused) if not ended else True, 0
        elif ch == ord("a") or k == KEY_LEFT:
            paused, back = True, min(back + 1, len(history) - 1)
        elif ch == ord("d") or k == KEY_RIGHT:
            if back > 0:
                back -= 1
            elif not ended:  # one new frame
                paused, step = True, True
        elif ch == ord("c"):
            sliders.set("color", 0 if det.s.color else 1)
        elif ch in (ord("["), ord("]")):
            sliders.set("threshold", det.s.threshold + (2 if ch == ord("]") else -2))
        elif ch == ord("h") and src.restart():
            det = HitDetector(wmap, det.s)
            det.debug = True
            history.clear()
            states.clear()
            hits.clear()
            n_hits, ended, paused, back = 0, False, False, 0
            print("[balltool] from the beginning again")
        elif ch == ord("w"):
            p = OUT / f"balltool_settings_{time.strftime('%H%M%S')}.json"
            p.write_text(json.dumps(settings_dict(det.s), indent=2), encoding="utf-8")
            print(f"[balltool] saved the sliders to {p}")
        elif ch == ord("g"):
            save_to_game(det.s)
        elif ch == ord("r"):
            det.reset()
            print("[balltool] learning the empty wall again")
        elif ch == ord("s") and history:
            p = OUT / f"balltool_{time.strftime('%H%M%S')}.jpg"
            cv2.imwrite(str(p), history[-1 - back])
            print(f"[balltool] saved {p}")
    src.close()
    logf.close()
    cv2.destroyAllWindows()
    print(f"[balltool] {n_hits} hits")


if __name__ == "__main__":
    main()
