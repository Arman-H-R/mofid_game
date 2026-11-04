"""Extract the Mofid logos and graphic elements from the brand book PDF.

Development tool only (needs PyMuPDF: ``pip install -r requirements-dev.txt``).
The game reads the files this script writes; it never opens the PDF.

    python tools/extract_brand_assets.py "path/to/Brnadbook-V04.pdf"          # SVGs
    python tools/extract_brand_assets.py "path/to/Brnadbook-V04.pdf" --png    # also the PNG fallbacks

The logos are vector artwork in the PDF (filled Bezier paths, no live text and
no embedded images), so they are copied path by path into SVG. Nothing is
redrawn or traced:

- ``assets/brand/logo-*.svg``: the four logos (pages 4-5) in their brand colours,
  grouped as mark / wordmark / tagline, plus the white versions the brand book
  allows on turquoise, dark-blue and grey backgrounds (page 7).
- ``assets/brand/logo-mark.svg``: the mark of the Persian stacked logo on its own
  (used for the window icon, as the PNG crop did before).
- ``assets/brand/elements/*.svg``: the five graphic elements of page 23, drawn on
  their module (bar thickness = 1/3 of the module, square ends). The script
  checks the PDF geometry against these proportions before writing.

Existing SVGs are not overwritten unless ``--force`` is given, so designer edits
are safe from an accidental re-run.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pymupdf

from svgkit import MODULE, element_shape, svg_document, write
from svgkit import num as _num

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
IMAGES = ROOT / "assets" / "images"

DARK_BLUE = "#004C6A"
TURQUOISE_LIGHT = "#00AAA5"  # the turquoise the logo artwork is printed in
WHITE = "#FFFFFF"

# (name, title, page, viewBox in PDF points, tagline top). The viewBox is the
# bounding box of the logo paths plus a small margin; it is the same rectangle the
# PNG logos were cut with, so the game places the SVG exactly where the PNG was.
# Paths whose top edge is at or below "tagline top" are the tagline; the largest
# path is the mark; everything else is the wordmark.
LOGOS = [
    ("logo-fa-horizontal", "Mofid Securities logo, Persian, horizontal", 4, (134, 277, 365, 318), 304.0),
    ("logo-fa-stacked", "Mofid Securities logo, Persian, stacked", 4, (521, 249, 666, 346), 330.0),
    ("logo-en-horizontal", "Mofid Securities logo, English, horizontal", 5, (139, 280, 371, 315), 304.0),
    ("logo-en-stacked", "Mofid Securities logo, English, stacked", 5, (520, 261, 665, 334), 322.0),
]
PNG_NAMES = {
    "logo-fa-horizontal": "logo_fa_horizontal",
    "logo-fa-stacked": "logo_fa_stacked",
    "logo-en-horizontal": "logo_en_horizontal",
    "logo-en-stacked": "logo_en_stacked",
}
MARK_FROM = "logo-fa-stacked"
MARK_MARGIN = 1.5  # pt, same margin as the logo files
PNG_WIDTH_PX = 2000

# Page 23: one element of each kind, left to right, with its brand colour.
ELEMENTS_PAGE = 23
ELEMENT_COLORS = {"slash": DARK_BLUE, "times": TURQUOISE_LIGHT, "plus": DARK_BLUE, "minus": TURQUOISE_LIGHT,
                  "dot": DARK_BLUE}


# -- PDF paths -> SVG path data -----------------------------------------------------------------
def _pt(p, ox: float, oy: float) -> str:
    return f"{_num(p.x - ox)} {_num(p.y - oy)}"


def path_data(drawing: dict, ox: float, oy: float) -> str:
    """One PyMuPDF drawing (lines, curves, rectangles) as SVG path data, origin moved to (ox, oy)."""
    out: list[str] = []
    cur = None
    for item in drawing["items"]:
        op = item[0]
        if op == "re":
            r = item[1]
            out.append(f"M{_num(r.x0 - ox)} {_num(r.y0 - oy)}H{_num(r.x1 - ox)}V{_num(r.y1 - oy)}H{_num(r.x0 - ox)}Z")
            cur = None
            continue
        if op == "qu":
            q = item[1]
            out.append(f"M{_pt(q.ul, ox, oy)}L{_pt(q.ur, ox, oy)}L{_pt(q.lr, ox, oy)}L{_pt(q.ll, ox, oy)}Z")
            cur = None
            continue
        start = item[1]
        if cur is None or abs(start.x - cur.x) > 1e-3 or abs(start.y - cur.y) > 1e-3:
            out.append(f"M{_pt(start, ox, oy)}")
        if op == "l":
            out.append(f"L{_pt(item[2], ox, oy)}")
            cur = item[2]
        elif op == "c":
            out.append(f"C{_pt(item[2], ox, oy)} {_pt(item[3], ox, oy)} {_pt(item[4], ox, oy)}")
            cur = item[4]
        else:
            raise ValueError(f"unsupported path item {op!r}")
    if drawing.get("closePath"):
        out.append("Z")
    return "".join(out)


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(round(c * 255)) for c in rgb)


def logo_paths(page: pymupdf.Page, clip: tuple[float, float, float, float]) -> list[dict]:
    rect = pymupdf.Rect(*clip)
    found = [d for d in page.get_drawings() if d.get("fill") and rect.contains(d["rect"])]
    for d in found:
        if d.get("color") is not None or d.get("fill_opacity", 1.0) != 1.0:
            raise ValueError("logo artwork changed: stroked or transparent paths found")
    return found


def logo_svg(name: str, title: str, drawings: list[dict], clip, tagline_top: float, white: bool) -> str:
    ox, oy = clip[0], clip[1]
    mark = max(drawings, key=lambda d: d["rect"].width * d["rect"].height)
    groups = {"mark": [mark], "wordmark": [], "tagline": []}
    for d in drawings:
        if d is mark:
            continue
        groups["tagline" if d["rect"].y0 >= tagline_top else "wordmark"].append(d)
    body = []
    for gid, items in groups.items():
        if not items:
            continue
        body.append(f'  <g id="{gid}">')
        for d in items:
            fill = WHITE if white else _hex(d["fill"])
            rule = ' fill-rule="evenodd"' if d.get("even_odd") else ""
            body.append(f'    <path fill="{fill}"{rule} d="{path_data(d, ox, oy)}"/>')
        body.append("  </g>")
    suffix = ", white version" if white else ""
    return svg_document(title + suffix, clip[2] - clip[0], clip[3] - clip[1], body)


def mark_svg(drawings: list[dict]) -> str:
    mark = max(drawings, key=lambda d: d["rect"].width * d["rect"].height)
    r = mark["rect"]
    ox, oy = r.x0 - MARK_MARGIN, r.y0 - MARK_MARGIN
    body = [f'  <path id="mark" fill="{_hex(mark["fill"])}" d="{path_data(mark, ox, oy)}"/>']
    return svg_document("Mofid Securities mark (from the Persian stacked logo)",
                        r.width + 2 * MARK_MARGIN, r.height + 2 * MARK_MARGIN, body)


# -- page 23 graphic elements -----------------------------------------------------------------
def check_elements(page: pymupdf.Page) -> dict[str, str]:
    """Measure the five elements on page 23 and confirm the proportions used here."""
    shapes = [d for d in page.get_drawings() if d.get("fill") and 270 < d["rect"].y0 < 300 and d["rect"].width > 30]
    shapes.sort(key=lambda d: d["rect"].x0)
    if len(shapes) != 5:
        raise ValueError(f"expected 5 graphic elements on page {ELEMENTS_PAGE}, found {len(shapes)}")
    kinds = ["slash", "times", "plus", "minus", "dot"]
    colors = {}
    plus = shapes[2]["rect"]
    module = plus.width
    for kind, d in zip(kinds, shapes):
        r = d["rect"]
        colors[kind] = _hex(d["fill"])
        expect_w = {"plus": module, "minus": module, "dot": module,
                    "times": (module + module / 3) / math.sqrt(2), "slash": (module + module / 3) / math.sqrt(2)}[kind]
        expect_h = module / 3 if kind == "minus" else expect_w
        if abs(r.width - expect_w) > 0.02 * module or abs(r.height - expect_h) > 0.02 * module:
            raise ValueError(f"{kind}: {r.width:.2f}x{r.height:.2f} pt does not match the module proportions")
        print(f"  {kind:5s} {r.width:6.2f} x {r.height:6.2f} pt (expected {expect_w:6.2f} x {expect_h:6.2f}) {colors[kind]}")
    print(f"  module {module:.2f} pt, bar {module / 3:.2f} pt")
    return colors


def element_svg(kind: str, color: str, white: bool) -> str:
    fill = WHITE if white else color
    title = f"Mofid graphic element: {kind}" + (", white" if white else "")
    return svg_document(title, MODULE, MODULE, [f'  <g id="{kind}" fill="{fill}">', f"    {element_shape(kind)}", "  </g>"])


# -- PNG fallbacks (the original raster logos) ---------------------------------------------------
def write_pngs(doc: pymupdf.Document) -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    for name, _title, page_no, clip, _t in LOGOS:
        rect = pymupdf.Rect(*clip)
        zoom = PNG_WIDTH_PX / rect.width
        pix = doc[page_no - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect, alpha=True)
        pix.save(IMAGES / f"{PNG_NAMES[name]}.png")
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4).copy()
        arr[..., :3] = 255
        white = pymupdf.Pixmap(pymupdf.csRGB, pix.width, pix.height, arr.tobytes(), True)
        white.save(IMAGES / f"{PNG_NAMES[name]}_white.png")
        print(f"  {PNG_NAMES[name]}.png {pix.width}x{pix.height}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--force", action="store_true", help="replace SVGs that already exist")
    ap.add_argument("--png", action="store_true", help="also write the raster fallback logos to assets/images")
    args = ap.parse_args()
    if not args.pdf.is_file():
        print(f"brand book not found: {args.pdf}", file=sys.stderr)
        return 1
    doc = pymupdf.open(args.pdf)
    print("logos")
    mark_source = None
    for name, title, page_no, clip, tagline_top in LOGOS:
        drawings = logo_paths(doc[page_no - 1], clip)
        write(BRAND / f"{name}.svg", logo_svg(name, title, drawings, clip, tagline_top, white=False), args.force, ROOT)
        write(BRAND / f"{name}-white.svg", logo_svg(name, title, drawings, clip, tagline_top, white=True), args.force, ROOT)
        if name == MARK_FROM:
            mark_source = drawings
    write(BRAND / "logo-mark.svg", mark_svg(mark_source), args.force, ROOT)
    print(f"graphic elements (page {ELEMENTS_PAGE})")
    colors = check_elements(doc[ELEMENTS_PAGE - 1])
    for kind, color in colors.items():
        write(BRAND / "elements" / f"{kind}.svg", element_svg(kind, color, white=False), args.force, ROOT)
        write(BRAND / "elements" / f"{kind}-white.svg", element_svg(kind, color, white=True), args.force, ROOT)
    if args.png:
        print("png fallbacks")
        write_pngs(doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
