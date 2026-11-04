"""Render the visual asset sheet: every SVG in assets/, plus original vs proposed.

    python tools/render_asset_sheet.py

Writes assets/documentation/asset-sheet.png (rasterised with the same SVG
renderer the game uses) and assets/documentation/asset-sheet.html (the SVG files
themselves, for a browser). Run it again after editing any SVG.
"""
from __future__ import annotations

import html
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
DOCS = ASSETS / "documentation"
FONT = ASSETS / "fonts" / "IRANSansX-Regular.ttf"
FONT_BOLD = ASSETS / "fonts" / "IRANSansX-Bold.ttf"

SECTIONS = [
    ("Brand: logos (Brand Book p.4-5, p.7)", "brand", False),
    ("Brand: graphic elements (Brand Book p.23)", "brand/elements", False),
    ("Gameplay: targets", "gameplay", False),
    ("Interface: buttons", "interface/buttons", False),
    ("Interface: panels and pills", "interface/panels", False),
    ("Interface: HUD and screen indicators", "interface/indicators", False),
    ("Interface: progress bars", "interface/progress", False),
    ("Interface: backgrounds", "interface/backgrounds", False),
    ("Interface: operator only (simulation, sensor, debug, calibration)", "interface/operator", False),
    ("Feedback (reference frames of animated rings)", "feedback", False),
]
COMPARE = [(f"documentation/original/symbol-{k}.svg", f"brand/elements/{k}.svg", k)
           for k in ("plus", "minus", "times", "slash", "dot")]
COMPARE += [(f"documentation/original/target-{c}.svg", f"gameplay/target-{c}.svg", f"target {c}")
            for c in ("green", "blue", "red")]

PAGE_W = 1680
COLS = 6
TILE_W = (PAGE_W - 40) // COLS
TILE_H = 210
ART = (TILE_W - 30, 140)
INK = (0, 76, 106)
GREY = (84, 102, 112)
LIGHT = (242, 245, 247)
DARK = (0, 76, 106)


def viewbox(path: Path) -> tuple[float, float]:
    m = re.search(r'viewBox="([^"]+)"', path.read_text(encoding="utf-8"))
    v = [float(x) for x in m.group(1).split()]
    return v[2], v[3]


def render(path: Path, box: tuple[int, int]) -> pygame.Surface:
    w, h = viewbox(path)
    k = min(box[0] / w, box[1] / h)
    size = (max(1, round(w * k)), max(1, round(h * k)))
    return pygame.image.load_sized_svg(str(path), size)


def is_light(img: pygame.Surface) -> bool:
    """True for mostly-white artwork, which is shown on dark blue."""
    total = lum = 0
    w, h = img.get_size()
    for y in range(0, h, max(1, h // 24)):
        for x in range(0, w, max(1, w // 24)):
            c = img.get_at((x, y))
            if c.a > 128:
                total += 1
                lum += (c.r + c.g + c.b) / 765
    return total > 0 and lum / total > 0.85


def tile(path: Path, label: str, fonts) -> pygame.Surface:
    img = render(path, ART)
    t = pygame.Surface((TILE_W - 10, TILE_H - 10))
    t.fill((255, 255, 255))
    art = pygame.Rect(5, 5, TILE_W - 20, ART[1] + 20)
    t.fill(DARK if is_light(img) else LIGHT, art)
    t.blit(img, img.get_rect(center=art.center))
    small = fonts["small"]
    y = art.bottom + 6
    for line in wrap(label, small, TILE_W - 24):
        t.blit(small.render(line, True, GREY), (8, y))
        y += small.get_linesize()
    pygame.draw.rect(t, (206, 216, 222), t.get_rect(), 1)
    return t


def wrap(text: str, font, width: int) -> list[str]:
    parts = re.split(r"(?<=/)", text)
    lines, cur = [], ""
    for p in parts:
        if font.size(cur + p)[0] > width and cur:
            lines.append(cur)
            cur = p
        else:
            cur += p
    return lines + [cur] if cur else lines


def section_files(folder: str) -> list[Path]:
    d = ASSETS / folder
    return sorted(p for p in d.glob("*.svg"))


def build_png(fonts) -> pygame.Surface:
    blocks: list[pygame.Surface] = []
    head = fonts["head"]

    def heading(text: str) -> pygame.Surface:
        s = pygame.Surface((PAGE_W, 46))
        s.fill((255, 255, 255))
        s.blit(head.render(text, True, INK), (20, 12))
        return s

    def grid(tiles: list[pygame.Surface]) -> pygame.Surface:
        rows = (len(tiles) + COLS - 1) // COLS
        s = pygame.Surface((PAGE_W, rows * TILE_H + 10))
        s.fill((255, 255, 255))
        for i, t in enumerate(tiles):
            s.blit(t, (20 + (i % COLS) * TILE_W, (i // COLS) * TILE_H))
        return s

    title = pygame.Surface((PAGE_W, 90))
    title.fill((255, 255, 255))
    title.blit(fonts["title"].render("Market Pulse - SVG asset sheet", True, INK), (20, 14))
    title.blit(fonts["small"].render("Rendered from the SVG files with the game's own SVG renderer (pygame-ce / nanosvg). "
                                     "White artwork is shown on dark blue.", True, GREY), (20, 58))
    blocks.append(title)
    for name, folder, _ in SECTIONS:
        files = section_files(folder)
        if files:
            blocks.append(heading(name))
            blocks.append(grid([tile(p, p.relative_to(ASSETS).as_posix(), fonts) for p in files]))
    blocks.append(heading("Original (left) vs proposed (right): symbol proportions aligned to Brand Book p.23"))
    pairs = []
    for old, new, label in COMPARE:
        pairs.append(tile(ASSETS / old, f"ORIGINAL {label}", fonts))
        pairs.append(tile(ASSETS / new, f"PROPOSED {label}", fonts))
    blocks.append(grid(pairs))
    sheet = pygame.Surface((PAGE_W, sum(b.get_height() for b in blocks)))
    y = 0
    for b in blocks:
        sheet.blit(b, (0, y))
        y += b.get_height()
    return sheet


def build_html() -> str:
    def card(rel: str, label: str) -> str:
        return (f'<figure><div class="art"><img src="../{html.escape(rel)}" alt="{html.escape(label)}"></div>'
                f"<figcaption>{html.escape(label)}</figcaption></figure>")

    parts = []
    for name, folder, _ in SECTIONS:
        files = section_files(folder)
        if files:
            cards = "".join(card(p.relative_to(ASSETS).as_posix(), p.relative_to(ASSETS).as_posix()) for p in files)
            parts.append(f"<h2>{html.escape(name)}</h2><div class='grid'>{cards}</div>")
    cmp_cards = "".join(card(o, f"original {lab}") + card(n, f"proposed {lab}") for o, n, lab in COMPARE)
    parts.append(f"<h2>Original vs proposed</h2><div class='grid'>{cmp_cards}</div>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Market Pulse asset sheet</title>
<style>
:root {{ --ink:#004C6A; --muted:#546670; --tile:#F2F5F7; --line:#CED8DE; --bg:#FFFFFF; }}
body {{ margin:0; padding:24px 16px; font-family: system-ui, sans-serif; color:var(--ink); background:var(--bg); }}
h1 {{ font-size:24px; margin:0 0 4px; }} p {{ color:var(--muted); margin:0 0 16px; }}
h2 {{ font-size:17px; margin:28px 0 10px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:10px; }}
figure {{ margin:0; border:1px solid var(--line); border-radius:6px; overflow:hidden; }}
.art {{ height:130px; display:flex; align-items:center; justify-content:center; padding:10px;
  background: repeating-conic-gradient(#E3EAEE 0% 25%, #F7F9FA 0% 50%) 50% / 16px 16px; }}
.art img {{ max-width:100%; max-height:100%; }}
figcaption {{ font-size:12px; color:var(--muted); padding:6px 8px; word-break:break-all; }}
</style></head><body>
<h1>Market Pulse - SVG asset sheet</h1>
<p>The SVG files as a browser draws them, on a checkerboard so white artwork stays visible. Open this file from
assets/documentation/ so the relative paths resolve.</p>
{''.join(parts)}
</body></html>
"""


def main() -> int:
    pygame.init()
    fonts = {
        "title": pygame.font.Font(str(FONT_BOLD), 28),
        "head": pygame.font.Font(str(FONT_BOLD), 18),
        "small": pygame.font.Font(str(FONT), 12),
    }
    DOCS.mkdir(parents=True, exist_ok=True)
    sheet = build_png(fonts)
    pygame.image.save(sheet, str(DOCS / "asset-sheet.png"))
    (DOCS / "asset-sheet.html").write_text(build_html(), encoding="utf-8", newline="\n")
    print(f"asset-sheet.png {sheet.get_width()}x{sheet.get_height()}, asset-sheet.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
