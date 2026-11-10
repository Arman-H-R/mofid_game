"""Find the camera detector settings that best match the LiDAR, on a session recorded with
tools/record_session.py (camera frames + the LiDAR's hits on one clock).

    .venv\\Scripts\\python tools\\tune_camera.py data/camtest/rec_fuse_<time>          (the settings now)
    .venv\\Scripts\\python tools\\tune_camera.py data/camtest/rec_fuse_<time> --search (look for better ones)
    ... --save   writes the best found into config/settings.json (input.camera)

Every LiDAR hit is a real throw. A camera hit within --match-ms and --match-cm of one finds it; a
camera hit that matches none is a false hit. Score = 2 x found - false (finding throws counts most,
a false hit costs half a throw). The camera's hits are mapped to the game's wall exactly as the game
does (input/wallcam/source.py): a share of the calibrated picture times the wall.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from market_pulse import config  # noqa: E402
from market_pulse.input.wallcam.detect import HitDetector, Settings  # noqa: E402
from market_pulse.input.wallcam.geometry import WallMap  # noqa: E402

# what the search tries, setting by setting (coordinate descent, best value kept each time)
SEARCH = [
    ("ball_cm", [8, 10, 12, 15, 20]),
    ("ball_tone", ["any", "light", "dark"]),
    ("threshold", [10, 13, 16, 18, 21, 25, 30]),
    ("min_speed", [30, 50, 80, 120, 160]),
    ("size_min", [0.2, 0.3, 0.4, 0.5]),
    ("size_max", [1.6, 2.0, 2.6]),
    ("turn_deg", [30, 40, 50, 60, 75]),
    ("bend_deg", [22, 28, 32, 40, 90]),
    ("depth_rise", [0.05, 0.08, 0.12, 0.18]),
    ("max_region", [8, 12, 20, 30]),
    ("min_track", [3, 4, 5]),
    ("wait_frames", [0, 1, 2, 3]),
    ("color", [False, True]),
    ("end_is_hit", [False, True]),
]

_FRAMES: list = []
_MAP = None
_SCALE = (1.0, 1.0)


def _init(frames, wmap, scale):
    global _FRAMES, _MAP, _SCALE
    _FRAMES, _MAP, _SCALE = frames, wmap, scale


def run_detector(values: dict) -> list[tuple]:
    """Camera hits (t, x, y game cm, kind) on the recording with these settings."""
    s = Settings()
    for k, v in values.items():
        if hasattr(s, k):
            setattr(s, k, type(getattr(s, k))(v))
    det = HitDetector(_MAP, s)
    out = []
    for t, data in _FRAMES:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        for h in det.process(img, t):
            mw, mh = _MAP.wall_size
            x = min(max(h.x / mw, 0.0), 1.0) * _SCALE[0]
            y = min(max(h.y / mh, 0.0), 1.0) * _SCALE[1]
            out.append((h.t, x, y, h.kind))
    return out


def score(cam: list[tuple], lidar: list[tuple], match_s: float, match_cm: float) -> dict:
    used = set()
    found, dists, dts = 0, [], []
    for lt, lx, ly in lidar:
        best, bd = None, math.inf
        for i, (ct, cx, cy, _) in enumerate(cam):
            if i in used or abs(ct - lt) > match_s:
                continue
            d = math.hypot(cx - lx, cy - ly)
            if d <= match_cm and d < bd:
                best, bd = i, d
        if best is not None:
            used.add(best)
            found += 1
            dists.append(bd)
            dts.append(lidar_dt := lt - cam[best][0])
    false = len(cam) - len(used)
    return {"found": found, "throws": len(lidar), "false": false, "camera_hits": len(cam),
            "score": 2 * found - false, "dist_cm": float(np.mean(dists)) if dists else 0.0,
            "lidar_later_ms": float(np.mean(dts) * 1000) if dts else 0.0}


def line(values: dict, r: dict) -> str:
    return (f"found {r['found']:2d}/{r['throws']}  false {r['false']:2d}  score {r['score']:3d}  "
            f"off {r['dist_cm']:4.0f} cm  LiDAR later {r['lidar_later_ms']:4.0f} ms")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--search", action="store_true")
    ap.add_argument("--save", action="store_true", help="write the best settings into config/settings.json")
    ap.add_argument("--match-ms", type=float, default=300)
    ap.add_argument("--match-cm", type=float, default=90)
    ap.add_argument("--passes", type=int, default=2)
    args = ap.parse_args()

    cfg, _ = config.load(ROOT / "config" / "settings.json")
    cc = cfg.input.camera
    wmap = WallMap.load(ROOT / cc.calibration_file)
    if wmap is None:
        print("no camera calibration")
        return 1
    sw, sh = float(cc.get("screen_width_cm", 0) or 0), float(cc.get("screen_height_cm", 0) or 0)
    if wmap.method == "markers" and (sw > 0 or sh > 0):  # as CameraSource.real_screen_size
        w, h = wmap.wall_size
        real = (sw, sh) if sw > 0 and sh > 0 else (sw, h * sw / w) if sw > 0 else (w * sh / h, sh)
        game = wmap.wall_size
        wmap = wmap.scaled_to(real)
    else:
        game = wmap.wall_size
    sys.path.insert(0, str(ROOT / "tools"))
    from record_session import wall_size
    W, H = wall_size(cfg)

    with open(args.folder / "times.csv") as f:
        rows = [(int(r["frame"]), float(r["t"])) for r in csv.DictReader(f)]
    frames = [(t, (args.folder / f"{fid:06d}.jpg").read_bytes()) for fid, t in rows]
    with open(args.folder / "lidar_hits.csv") as f:
        lidar = [(float(r["t"]), float(r["x_cm"]), float(r["y_cm"])) for r in csv.DictReader(f)]
    print(f"{len(frames)} frames, {len(lidar)} LiDAR hits; camera picture {wmap.wall_size[0]:.0f} x "
          f"{wmap.wall_size[1]:.0f} cm -> game wall {W:.0f} x {H:.0f} cm", flush=True)

    base = {k: v for k, v in cc.raw().items() if hasattr(Settings(), k)}
    with ProcessPoolExecutor(max_workers=14, initializer=_init, initargs=(frames, wmap, (W, H))) as pool:
        def evaluate(cands: list[dict]) -> list[dict]:
            results = list(pool.map(run_detector, cands))
            return [score(c, lidar, args.match_ms / 1000, args.match_cm) for c in results]

        t0 = time.time()
        (r0,) = evaluate([base])
        cam0 = list(pool.map(run_detector, [base]))[0]
        print(f"NOW     {line(base, r0)}   ({time.time() - t0:.0f} s per run)", flush=True)
        for lt, lx, ly in lidar:  # which throws the camera finds now
            near = [(abs(ct - lt), ct, cx, cy, k) for ct, cx, cy, k in cam0 if abs(ct - lt) < 0.6]
            if near:
                _, ct, cx, cy, k = min(near)
                print(f"   LiDAR ({lx:4.0f},{ly:4.0f})  camera ({cx:4.0f},{cy:4.0f}) {k:7s} {1000 * (lt - ct):+5.0f} ms")
            else:
                print(f"   LiDAR ({lx:4.0f},{ly:4.0f})  camera: nothing")
        best, best_r = dict(base), r0
        if args.search:
            for p in range(args.passes):
                for name, values in SEARCH:
                    cands = [dict(best, **{name: v}) for v in values]
                    rs = evaluate(cands)
                    i = max(range(len(rs)), key=lambda j: (rs[j]["score"], -rs[j]["false"], -rs[j]["dist_cm"]))
                    if (rs[i]["score"], -rs[i]["false"]) > (best_r["score"], -best_r["false"]):
                        best, best_r = cands[i], rs[i]
                    print(f"pass {p + 1} {name:11s} -> {best.get(name, getattr(Settings(), name))!s:6s}  {line(best, best_r)}", flush=True)
            changed = {k: v for k, v in best.items() if base.get(k) != v}
            print(f"BEST    {line(best, best_r)}")
            print(f"changes: {json.dumps(changed)}")
            if args.save and changed:
                path = ROOT / "config" / "settings.json"
                data = json.loads(path.read_text(encoding="utf-8"))
                known = config.DEFAULTS["input"]["camera"]
                data["input"]["camera"].update({k: v for k, v in changed.items() if k in known})
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"saved into {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
