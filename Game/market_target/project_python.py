"""Start the tools with the project's own Python (.venv), whatever Python they were started with.

`python sensortest.py` with the PC's own Python finds plain pygame there, which cannot shape Persian
(the letters come out unjoined and left to right), and may lack OpenCV / PyAV / pyserial. The
project's .venv has pygame-ce and everything else. Call relaunch() before importing pygame.
Set MARKET_PULSE_ANY_PYTHON=1 to stay in the Python you started with.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def relaunch() -> None:
    """If this is not the project's .venv Python and pygame here is not pygame-ce, run the same
    command again with .venv's Python and exit with its exit code."""
    if getattr(sys, "frozen", False) or os.environ.get("MARKET_PULSE_ANY_PYTHON"):
        return
    venv = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        if not venv.is_file() or Path(sys.prefix).resolve() == venv.parents[1].resolve():
            return
    except OSError:
        return
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    try:
        import pygame
        if getattr(pygame, "IS_CE", False):
            return  # another Python, but with the right pygame
    except ImportError:
        pass
    script = str(Path(sys.argv[0]).resolve())
    print(f"{sys.executable} has no pygame-ce (Persian text): starting with {venv}", file=sys.stderr)
    sys.exit(subprocess.call([str(venv), script, *sys.argv[1:]]))
