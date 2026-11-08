"""Cut the product tiles out of MOFID_UI_v4.1_1.pdf page 1 (attract) as RGBA PNGs.

The PDF has 13 tiles; طلای گرمی (gold), همنگ (hamnag) and توان (tavan) are not in the game,
so they are not cut (see config.RETIRED).

Each tile is a filled rounded rectangle; it is rendered at high zoom and given an
anti-aliased rounded-corner alpha mask with the tile's own corner radius, so only the
tile itself (not the white ring or the field behind it) is kept.
"""
import json, sys, pathlib
import numpy as np
import pymupdf
from PIL import Image

pdf, out = sys.argv[1], pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
page = pymupdf.open(pdf)[0]

# tile top-left (pt) -> key, Persian label (from the PDF's components page)
TILES = [
    ((160.2, 189.6), "atiyeh", "آتیه"),
    ((111.1, 584.5), "hami", "حامی"),
    ((218.4, 680.0), "ayar", "عیار"),
    ((357.7, 714.7), "amoozesh", "آموزش مفید"),
    ((497.2, 680.7), "bimeh", "بیمه زندگی مفید"),
    ((604.9, 585.8), "boursview", "بورس ویو"),
    ((656.2, 451.7), "boursview2", "بورس ویو ۲.۰"),
    ((639.3, 309.1), "jet", "مفید کارت جت"),
    ((558.0, 190.7), "mofidapp", "مفید اپ"),
    ((431.1, 123.6), "easytrader", "ایزی تریدر"),
]

drawings = [d for d in page.get_drawings() if d.get("fill") and "".join(i[0] for i in d["items"]) == "lclclclc"]


def find(x, y):
    return min(drawings, key=lambda d: abs(d["rect"].x0 - x) + abs(d["rect"].y0 - y))


meta = []
WIDTH_PX = 640
for (x, y), key, label in TILES:
    d = find(x, y)
    r = d["rect"]
    c = next(i for i in d["items"] if i[0] == "c")  # first corner curve: start and end points
    radius = 15.48  # the tiles' corner radius (pt); a few paths start mid-corner and measure less
    zoom = WIDTH_PX / r.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=r, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    h, w = img.shape[:2]
    # supersampled rounded-rectangle coverage
    ss = 4
    rad = radius * zoom * ss
    yy, xx = np.mgrid[0:h * ss, 0:w * ss] + 0.5
    W, H = w * ss, h * ss
    dx = np.maximum(np.maximum(rad - xx, xx - (W - rad)), 0)
    dy = np.maximum(np.maximum(rad - yy, yy - (H - rad)), 0)
    inside = (dx * dx + dy * dy) <= rad * rad
    alpha = inside.reshape(h, ss, w, ss).mean(axis=(1, 3))
    rgba = np.dstack([img, (alpha * 255).round().astype(np.uint8)])
    Image.fromarray(rgba, "RGBA").save(out / f"{key}.png", optimize=True)
    fill = tuple(round(v * 255) for v in d["fill"])
    meta.append({"key": key, "name": label, "file": f"{key}.png", "aspect": round(r.height / r.width, 4),
                 "radius": round(radius / r.width, 4), "fill": "#%02X%02X%02X" % fill})
    print(key, w, h, "radius", round(radius, 2), "pt", fill)
(out / "products.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
