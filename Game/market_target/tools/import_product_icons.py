"""Make the product tiles (assets/products) from the square product icons, named in Persian.

    python tools/import_product_icons.py "<folder with the icons>" assets/products

Each icon (a full square in the product's own colour) is scaled to 640 x 640 and given the
game's rounded corners (draw.PRODUCT_RADIUS) as an anti-aliased alpha mask. A product with
no icon in the folder gets a plain tile with its name, until its icon is added.
Writes products.json next to the tiles: key, name, file, aspect, radius, fill.
"""
import json
import os
import pathlib
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pygame  # noqa: E402

from market_pulse.config import PRODUCTS  # noqa: E402
from market_pulse.ui.draw import PRODUCT_RADIUS  # noqa: E402
from market_pulse.ui.text import FontBook, Text  # noqa: E402

# key -> the icon's file name in the folder (the game's own Persian name, unless the file differs)
ICON_FILES = {"boursview": "بورس وبو.png"}
SIZE = 640
NAME_TILE_BG = (234, 234, 234)  # like the light icons (ایزی تریدر, آموزش مفید)
NAME_TILE_TEXT = (2, 68, 94)  # the icons' navy

src_dir, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
pygame.init()
pygame.display.set_mode((1, 1))
fonts = FontBook(pathlib.Path(__file__).resolve().parents[1] / "assets" / "fonts", {"bold": ["IRANSansX-Black.ttf"]})
text = Text(fonts)


def round_corners(img: pygame.Surface) -> pygame.Surface:
    """The tile with rounded corners: supersampled coverage as alpha."""
    w, h = img.get_size()
    ss = 4
    rad = PRODUCT_RADIUS * w * ss
    yy, xx = np.mgrid[0:h * ss, 0:w * ss] + 0.5
    W, H = w * ss, h * ss
    dx = np.maximum(np.maximum(rad - xx, xx - (W - rad)), 0)
    dy = np.maximum(np.maximum(rad - yy, yy - (H - rad)), 0)
    alpha = ((dx * dx + dy * dy) <= rad * rad).reshape(h, ss, w, ss).mean(axis=(1, 3))
    tile = img.convert_alpha()
    pygame.surfarray.pixels_alpha(tile)[:, :] = (alpha.T * 255).round().astype(np.uint8)
    return tile


def name_tile(name: str) -> pygame.Surface:
    img = pygame.Surface((SIZE, SIZE))
    img.fill(NAME_TILE_BG)
    label = text.render(name, "bold", 150, NAME_TILE_TEXT, localize=False)
    if label.get_width() > SIZE * 0.8:
        k = SIZE * 0.8 / label.get_width()
        label = pygame.transform.smoothscale(label, (int(label.get_width() * k), int(label.get_height() * k)))
    img.blit(label, label.get_rect(center=(SIZE // 2, SIZE // 2)))
    return img


meta = []
for p in PRODUCTS:
    key, name = p["key"], p["name"]
    icon = src_dir / ICON_FILES.get(key, f"{name}.png")
    if icon.is_file():
        img = pygame.transform.smoothscale(pygame.image.load(str(icon)).convert(), (SIZE, SIZE))
        how = icon.name
    else:
        img = name_tile(name)
        how = "no icon: a tile with its name"
    pygame.image.save(round_corners(img), str(out / p["file"]))
    fill = tuple(pygame.transform.average_color(img, pygame.Rect(0, 0, SIZE, SIZE // 20)))[:3]
    meta.append({"key": key, "name": name, "file": p["file"], "aspect": 1.0, "radius": PRODUCT_RADIUS,
                 "fill": "#%02X%02X%02X" % fill})
    print(key, "<-", how)
(out / "products.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
