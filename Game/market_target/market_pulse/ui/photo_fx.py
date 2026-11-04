"""Looks for the player's photo on the results card. numpy only, so they work without OpenCV.

One is picked at random per round (camera.effect_chance, camera.photo_effects), so the
cards people post are not all the same. Every look keeps the face natural enough to
recognise; the colours come from the brand palette.

    natural   the camera photo as it is
    duotone   shadows in deep blue, light in turquoise to white (the brand's colours)
    mono      black and white with strong contrast
    warm      golden light, lifted shadows (a film look)
    pop       stronger colour and a dark vignette around the player
"""
from __future__ import annotations

import numpy as np

from . import theme

EFFECTS = ("natural", "duotone", "mono", "warm", "pop")

_LUMA = np.array([0.299, 0.587, 0.114], np.float32)


def _luma(img: np.ndarray) -> np.ndarray:
    return img @ _LUMA


def _vignette(h: int, w: int, strength: float) -> np.ndarray:
    y = np.linspace(-1.0, 1.0, h, dtype=np.float32)[:, None]
    x = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
    r = np.sqrt(x * x * 1.3 + (y + 0.25) ** 2 * 0.7)  # centred a little above the middle, where the face is
    return 1.0 - strength * np.clip(r - 0.45, 0.0, 1.0) ** 1.6


def apply(rgb: np.ndarray, name: str) -> np.ndarray:
    """The photo (RGB uint8) with the named look; an unknown name returns it unchanged."""
    if name not in EFFECTS or name == "natural":
        return rgb
    img = rgb.astype(np.float32) / 255.0
    h, w = img.shape[:2]
    if name == "duotone":
        lum = _luma(img)
        lum = np.clip((lum - 0.06) / 0.88, 0.0, 1.0)[..., None]
        dark = np.array(theme.BLUE_DEEP, np.float32) / 255
        mid = np.array(theme.TURQUOISE, np.float32) / 255
        light = np.array(theme.TURQUOISE_PALE, np.float32) / 255
        lo = dark + (mid - dark) * np.clip(lum / 0.55, 0.0, 1.0)
        out = np.where(lum < 0.55, lo, mid + (light - mid) * np.clip((lum - 0.55) / 0.45, 0.0, 1.0))
    elif name == "mono":
        lum = _luma(img)
        lum = np.clip((lum - 0.5) * 1.3 + 0.52, 0.0, 1.0)
        lum = lum * lum * (3 - 2 * lum)  # S-curve
        out = np.repeat(lum[..., None], 3, axis=2) * _vignette(h, w, 0.35)[..., None]
    elif name == "warm":
        out = img * np.array([1.07, 1.0, 0.86], np.float32) + np.array([0.03, 0.015, 0.0], np.float32)
        out = 0.05 + out * 0.93  # lifted blacks
    else:  # pop
        lum = _luma(img)[..., None]
        out = lum + (img - lum) * 1.4
        out = (out - 0.5) * 1.1 + 0.5
        out = out * _vignette(h, w, 0.55)[..., None]
    return (np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
