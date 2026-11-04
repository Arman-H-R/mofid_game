"""Build dist/MarketPulse.exe: the whole game in one file, no Python needed to run it.

    python tools/build_exe.py
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "scripts" / "market_pulse.ico"
SIZES = (256, 64, 48, 32, 16)


def make_icon() -> None:
    """The Mofid mark in white on a turquoise tile, as an .ico holding PNG images."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import io

    import pygame

    sys.path.insert(0, str(ROOT))
    from market_pulse.ui import theme

    pygame.init()
    mark = pygame.image.load(str(ROOT / "assets" / "brand" / "logo-mark.svg"))
    white = mark.copy()
    white.fill((255, 255, 255, 255), special_flags=pygame.BLEND_RGB_MAX)
    images = []
    for size in SIZES:
        tile = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.rect(tile, theme.TURQUOISE, tile.get_rect(), border_radius=int(size * 0.18))
        k = size * 0.74 / max(white.get_size())
        small = pygame.transform.smoothscale(white, (int(white.get_width() * k), int(white.get_height() * k)))
        tile.blit(small, small.get_rect(center=tile.get_rect().center))
        data = io.BytesIO()
        pygame.image.save(tile, data, "icon.png")
        images.append(data.getvalue())
    offset = 6 + 16 * len(images)
    head = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    for size, png in zip(SIZES, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    ICON.write_bytes(head + entries + b"".join(images))


def main() -> int:
    if not ICON.is_file():
        make_icon()
    code = subprocess.call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN", str(ROOT / "MarketPulse.spec")], cwd=ROOT)
    if code == 0:
        exe = ROOT / "dist" / "MarketPulse.exe"
        print(f"{exe} ({exe.stat().st_size / 1e6:.0f} MB)")
    return code


if __name__ == "__main__":
    sys.exit(main())
