"""Market Pulse (نبض بازار) - interactive ball-throwing game for Mofid Securities.

    python main.py                  run with config/settings.json (fullscreen by default)
    python main.py --windowed       run in a window
    python main.py --simulate       mouse clicks stand in for ball impacts (testing only)
    python main.py --gallery out/   render every screen to PNG and exit (no window needed)
    python main.py --exit-after 30  quit after 30 seconds (unattended smoke test)
    python main.py --write-config   write a full settings file with every default value
    python main.py --settings       only the settings page (sliders), no game; add --windowed for a window

MarketPulse.exe (tools/build_exe.py): a double-click plays with the mouse in a window. For the wall:
    MarketPulse.exe --fullscreen
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# numpy only does small vector maths here. Without this, OpenBLAS reserves
# buffers for every CPU thread (about 500 MB of committed memory on 16 threads).
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

FROZEN = getattr(sys, "frozen", False)  # running as MarketPulse.exe
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
BUNDLE = Path(sys._MEIPASS) if FROZEN else ROOT  # what is packed inside the .exe


def setup_logging(level: str) -> None:
    logs = ROOT / "logs"
    handlers: list[logging.Handler] = [logging.StreamHandler()] if sys.stderr else []  # the .exe has no console
    try:
        logs.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(logs / "market_pulse.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"))
    except OSError as exc:
        print(f"cannot write logs to {logs}: {exc}", file=sys.stderr)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Market Pulse - Mofid interactive wall game")
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "settings.json", help="settings JSON file")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--windowed", action="store_true", help="run in a window")
    mode.add_argument("--fullscreen", action="store_true", help="run fullscreen")
    ap.add_argument("--display", type=int, help="display index for fullscreen")
    ap.add_argument("--resolution", help="canvas size, e.g. 1536x2048 (default: native)")
    ap.add_argument("--simulate", action="store_true", help="SIMULATION: mouse clicks are ball impacts")
    ap.add_argument("--debug", action="store_true", help="show the diagnostics overlay (F3)")
    ap.add_argument("--gallery", type=Path, metavar="DIR", help="render all screens to PNG files and exit")
    ap.add_argument("--exit-after", type=float, metavar="SECONDS", help="quit after this many seconds (smoke tests)")
    ap.add_argument("--settings", action="store_true", help="open only the settings page, without the game")
    ap.add_argument("--write-config", action="store_true", help="write every default setting to --config and exit")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if FROZEN and len(sys.argv) == 1:  # a double-click: no wall or sensor at hand
        args.windowed = args.simulate = True
    settings_file = args.config  # the settings page (F5) saves here, next to the .exe even if there is none yet
    if FROZEN and not args.write_config and not args.config.exists():
        args.config = BUNDLE / "config" / "settings.json"  # a settings file next to the .exe wins
    sys.path.insert(0, str(ROOT))
    from market_pulse import config

    if args.write_config:
        if args.config.exists():
            print(f"{args.config} already exists; not overwriting", file=sys.stderr)
            return 1
        config.write_defaults(args.config)
        print(f"wrote {args.config}")
        return 0

    cfg, warnings = config.load(args.config)
    setup_logging(cfg.debug.log_level)
    log = logging.getLogger("market_pulse")
    for w in warnings:
        log.warning("config: %s", w)

    raw = cfg.raw()
    if args.windowed:
        raw["display"]["fullscreen"] = False
    if args.fullscreen:
        raw["display"]["fullscreen"] = True
    if args.display is not None:
        raw["display"]["display_index"] = max(0, args.display)
    if args.resolution:
        try:
            w, h = (int(v) for v in args.resolution.lower().split("x"))
            raw["display"]["resolution"] = [w, h]
        except ValueError:
            log.error("--resolution must look like 1536x2048; ignored")
    if args.simulate:
        raw["input"]["simulation"] = True
    if args.debug:
        raw["debug"]["overlay"] = True

    # Real pixels on Windows high-DPI displays (no OS bitmap scaling of the wall).
    os.environ.setdefault("SDL_WINDOWS_DPI_AWARENESS", "permonitorv2")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    # The full-screen game stays on screen while the settings window (F5) has the keyboard.
    os.environ.setdefault("SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS", "0")

    from market_pulse.app import App, Paths, render_gallery

    paths = Paths(ROOT, BUNDLE)
    if args.gallery:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        raw["display"]["fullscreen"] = False
        if 0 in raw["display"]["resolution"]:
            raw["display"]["resolution"] = [1536, 2048]
        raw["input"]["sources"] = []
        files = render_gallery(cfg, paths, args.gallery)
        print("\n".join(str(f) for f in files))
        return 0

    if raw["input"]["simulation"]:
        log.warning("SIMULATION MODE: mouse clicks are treated as ball impacts")
    log.info("starting Market Pulse from %s", ROOT)
    try:
        app = App(cfg, paths, warnings, start_inputs=not args.settings, settings_file=settings_file,
                  settings_source=args.config)
    except Exception:
        log.exception("could not start the game")
        return 1
    app.exit_after = args.exit_after
    if args.settings:
        return app.run_settings()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
