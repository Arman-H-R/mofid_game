"""SVG artwork named in assets/manifest.json and the colour tokens in
assets/design-tokens.json.

The game asks for an image by its logical name ("target.green") and a pixel
size; the SVG is rasterised at exactly that size, so it stays sharp on any wall.
A missing or broken file is logged once and the caller falls back to drawing the
shape in code, so a bad edit can never stop the game.

Files under "brand" in the manifest are the logos and are used exactly as they
are. Files under "themed" are drawn in the default palette and follow the colour
tokens (see theme.recolor_map).
"""
from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path

import pygame

from . import theme

log = logging.getLogger(__name__)

MANIFEST = "manifest.json"
TOKENS = "design-tokens.json"
_VIEWBOX = re.compile(rb'viewBox\s*=\s*"\s*([-\d.eE]+)[\s,]+([-\d.eE]+)[\s,]+([\d.eE]+)[\s,]+([\d.eE]+)\s*"')
_HEX = re.compile(rb"#[0-9A-Fa-f]{6}\b")


def load_tokens(assets_dir: Path) -> list[str]:
    """Apply assets/design-tokens.json to the theme; returns problems to log."""
    path = assets_dir / TOKENS
    if not path.is_file():
        theme.apply_tokens({})
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        colors = data.get("color", {}) if isinstance(data, dict) else None
        if not isinstance(colors, dict):
            raise ValueError('needs a "color" object')
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        theme.apply_tokens({})
        return [f"{TOKENS}: {exc}; using the brand palette"]
    return [f"{TOKENS}: {p}" for p in theme.apply_tokens(colors)]


class AssetLibrary:
    def __init__(self, sources: dict[str, bytes] | None = None):
        self._src: dict[str, bytes] = sources or {}
        self._size: dict[str, tuple[float, float]] = {}
        self._failed: set[str] = set()
        self.enabled = hasattr(pygame.image, "load_sized_svg")  # pygame-ce 2.4+
        for name, data in self._src.items():
            m = _VIEWBOX.search(data)
            if m and float(m.group(3)) > 0 and float(m.group(4)) > 0:
                self._size[name] = (float(m.group(3)), float(m.group(4)))
            else:
                log.warning("asset %s has no usable viewBox; it is drawn in code instead", name)
                self._failed.add(name)

    @classmethod
    def load(cls, assets_dir: Path) -> tuple[AssetLibrary, list[str]]:
        """Read every file named in the manifest; returns the library and problems to log."""
        problems: list[str] = []
        try:
            manifest = json.loads((assets_dir / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return cls(), [f"{MANIFEST}: {exc}; every shape is drawn in code"]
        recolor = {k.encode(): v.encode() for k, v in theme.recolor_map().items()}
        sources: dict[str, bytes] = {}
        for section, themed in (("brand", False), ("themed", True)):
            for name, rel in (manifest.get(section) or {}).items():
                if name.startswith("_"):
                    continue
                path = assets_dir / rel
                try:
                    data = path.read_bytes()
                except OSError as exc:
                    problems.append(f"{MANIFEST}: {name} -> {rel}: {exc.strerror or exc}; drawn in code instead")
                    continue
                if themed and recolor:
                    data = _HEX.sub(lambda m: recolor.get(m.group(0).upper(), m.group(0)), data)
                sources[name] = data
        return cls(sources), problems

    def has(self, name: str) -> bool:
        return self.enabled and name in self._src and name not in self._failed

    def aspect(self, name: str) -> float:
        """Width / height of the artwork's viewBox."""
        w, h = self._size[name]
        return w / h

    def render(self, name: str, w: int, h: int | None = None) -> pygame.Surface | None:
        """The artwork at exactly w x h pixels (h from the viewBox when omitted);
        None if it is missing or cannot be drawn."""
        if not self.has(name):
            return None
        w = max(1, int(w))
        h = max(1, int(round(w / self.aspect(name)))) if h is None else max(1, int(h))
        try:
            img = pygame.image.load_sized_svg(io.BytesIO(self._src[name]), (w, h))
        except (pygame.error, ValueError) as exc:
            log.warning("asset %s could not be drawn (%s); it is drawn in code instead", name, exc)
            self._failed.add(name)
            return None
        if img.get_size() != (w, h):  # a pixel of rounding between the viewBox and the box
            img = pygame.transform.smoothscale(img, (w, h))
        return img.convert_alpha() if pygame.display.get_surface() is not None else img
