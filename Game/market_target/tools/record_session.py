"""Record a throwing session for tuning the camera: every camera frame AND the LiDAR's hits, on one clock.

    .venv\\Scripts\\python tools\\record_session.py [--seconds 75]

Close the game first (it uses the camera and the LiDAR). The LiDAR first learns the empty wall
(keep the wall clear until READY is printed), then throw balls until the time is up.

Writes data/camtest/rec_fuse_<time>/: the frames (000123.jpg + times.csv, the same layout as
balltool.py --record, so balltool.py and camtool.py replay can play it) and lidar_hits.csv (t, wall
x, y cm): the LiDAR's hits are the reference for tools/tune_camera.py, which replays the frames with
many camera settings and counts which throws each one finds.
"""
from __future__ import annotations

import argparse
import csv
import logging
import queue
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from market_pulse import config  # noqa: E402
from market_pulse.input.lidar.source import LidarSource  # noqa: E402
from market_pulse.input.wallcam.stream import open_stream  # noqa: E402


def wall_size(cfg) -> tuple[float, float]:
    """The wall as the game uses it: display.fill_screen makes it the screen's shape (App.fit_wall)."""
    w, h = float(cfg.wall.width_cm), float(cfg.wall.height_cm)
    res = cfg.display.resolution
    if cfg.display.fill_screen and isinstance(res, list) and len(res) == 2 and 0 not in res:
        shape = res[0] / res[1]
        if shape > w / h * 1.003:
            w = h * shape
        elif shape < w / h / 1.003:
            h = w / shape
    return w, h


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=75.0, help="throwing time after the LiDAR is ready")
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "settings.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg, warnings = config.load(args.config)
    for w in warnings:
        print("config:", w)
    W, H = wall_size(cfg)
    out_dir = ROOT / "data" / "camtest" / f"rec_fuse_{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[record] wall {W:.0f} x {H:.0f} cm -> {out_dir}", flush=True)

    q: queue.SimpleQueue = queue.SimpleQueue()
    lcfg = cfg.input.lidar
    lidar = LidarSource(q, lcfg, ROOT / lcfg.settings_file, W, H)
    lidar.start()
    cam = open_stream(cfg.input.camera)

    times = open(out_dir / "times.csv", "w", newline="")
    tw = csv.writer(times)
    tw.writerow(["frame", "t"])
    hits_f = open(out_dir / "lidar_hits.csv", "w", newline="")
    hw = csv.writer(hits_f)
    hw.writerow(["t", "x_cm", "y_cm", "trusted"])
    frames = hits = 0
    start = None
    announced = time.monotonic()
    try:
        while True:
            fr = cam.get(0.05)
            if fr is not None:
                cv2.imwrite(str(out_dir / f"{fr.id:06d}.jpg"), fr.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
                tw.writerow([fr.id, f"{fr.t:.6f}"])
                frames += 1
            while True:
                try:
                    ev = q.get_nowait()
                except queue.Empty:
                    break
                if hasattr(ev, "x"):
                    hits += 1
                    hw.writerow([f"{ev.t:.6f}", f"{ev.x * W:.1f}", f"{ev.y * H:.1f}", int(ev.trusted)])
                    hits_f.flush()
                    print(f"[record] LiDAR hit {hits} at ({ev.x * W:.0f}, {ev.y * H:.0f}) cm", flush=True)
            if start is None and lidar.ready:
                start = time.monotonic()
                print(f"[record] READY - throw balls now for {args.seconds:.0f} s", flush=True)
            if start is None and time.monotonic() - announced > 3:
                announced = time.monotonic()
                print(f"[record] waiting: LiDAR {lidar.state} {lidar.detail} {lidar.error}; camera {cam.status} "
                      f"{frames} frames", flush=True)
                if lidar.state == "error":
                    return 1
            if start is not None and time.monotonic() - start > args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        cam.stop()
        lidar.stop()
        times.close()
        hits_f.close()
    print(f"[record] DONE: {frames} frames, {hits} LiDAR hits in {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
