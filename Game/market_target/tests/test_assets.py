"""SVG assets and colour tokens: every file is a clean vector, renders at any
size, matches the palette, follows token changes, and the game still draws
everything when a file is missing."""
import json
import re
import xml.etree.ElementTree as ET

import pygame
import pytest

from market_pulse.ui import theme
from market_pulse.ui.assets import AssetLibrary, load_tokens
from market_pulse.ui.draw import Sprites
from conftest import ROOT

ASSETS = ROOT / "assets"
ALL_SVGS = sorted(ASSETS.rglob("*.svg"))
MANIFEST = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
NAMED = {name: rel for section in ("brand", "themed") for name, rel in MANIFEST[section].items()
         if not name.startswith("_")}
SVG_NS = "{http://www.w3.org/2000/svg}"
# Every name the code asks the library for.
USED = {"logo.fa_horizontal", "logo.fa_horizontal_white", "logo.fa_stacked_white", "logo.mark",
        *(f"symbol.{k}" for k in theme.SYMBOLS), *(f"target.{c}" for c in theme.TARGET_COLORS),
        "button.start", "panel.attract_card", "panel.results_card", "indicator.ball_remaining",
        "indicator.ball_used", "indicator.multiplier_active", "indicator.multiplier_inactive",
        "indicator.points", "indicator.rank_first", "indicator.rank"}


@pytest.fixture(autouse=True)
def brand_palette():
    pygame.init()
    theme.apply_tokens({})
    yield
    theme.apply_tokens({})


def test_there_is_a_full_asset_set():
    assert len(ALL_SVGS) >= 50
    assert USED <= set(NAMED), USED - set(NAMED)
    for name, rel in NAMED.items():
        assert (ASSETS / rel).is_file(), f"{name} -> {rel} is missing"


@pytest.mark.parametrize("path", ALL_SVGS, ids=lambda p: p.relative_to(ASSETS).as_posix())
def test_svg_is_clean_vector(path):
    text = path.read_text(encoding="utf-8")
    root = ET.fromstring(text)
    assert root.tag == SVG_NS + "svg"
    vb = [float(v) for v in root.get("viewBox").split()]
    assert vb[2] > 0 and vb[3] > 0
    tags = {el.tag.replace(SVG_NS, "") for el in root.iter()}
    assert not tags & {"image", "text", "foreignObject", "style", "script", "filter", "clipPath", "mask", "use"}, tags
    assert root.find(SVG_NS + "title") is not None, "every file says what it is"
    assert not re.search(r"(href|url\()\s*=?\s*[\"']?(https?:|file:|data:|\.\.?/)", text), "no external or embedded files"
    assert "font" not in text.lower()
    shapes = tags & {"path", "rect", "circle", "polygon", "line"}
    assert shapes, "has vector shapes"


@pytest.mark.parametrize("path", ALL_SVGS, ids=lambda p: p.relative_to(ASSETS).as_posix())
def test_svg_renders_at_several_sizes(path):
    for side in (16, 64, 512):
        img = pygame.image.load_sized_svg(str(path), (side, side))
        assert max(img.get_size()) == side
        assert pygame.mask.from_surface(img, 0).count() > 0, f"nothing drawn at {side}px"


def test_library_renders_exact_sizes():
    lib, problems = AssetLibrary.load(ASSETS)
    assert problems == []
    for name in NAMED:
        assert lib.has(name), name
        img = lib.render(name, 97)
        assert img.get_width() == 97
        assert img.get_height() == max(1, round(97 / lib.aspect(name)))
    pill = lib.render("indicator.multiplier_active", 139, 39)  # a pixel off the 46:13 viewBox
    assert pill.get_size() == (139, 39)


def test_svg_colours_match_the_palette():
    """The themed files use the token colours, so recolouring can find them."""
    palette = {theme.to_hex(c) for name in theme.COLOR_TOKENS for c in [theme.DEFAULTS[name]]}
    palette |= {theme.to_hex(c) for name in theme.RAMP_TOKENS for c in theme.DEFAULTS[name]}
    for name, rel in NAMED.items():
        used = set(re.findall(r"#[0-9A-F]{6}", (ASSETS / rel).read_text(encoding="utf-8").upper()))
        assert used <= palette, f"{rel}: {used - palette} not in the brand palette"
    for color, rgb in theme.TARGET_COLORS.items():
        text = (ASSETS / NAMED[f"target.{color}"]).read_text(encoding="utf-8").upper()
        assert theme.to_hex(rgb) in text


def test_tokens_recolour_themed_assets_but_never_the_logo():
    problems = theme.apply_tokens({"turquoise": "#112233"})
    assert problems == [] and theme.TURQUOISE == (0x11, 0x22, 0x33)
    assert theme.TARGET_COLORS["green"] == (0x11, 0x22, 0x33)
    lib, _ = AssetLibrary.load(ASSETS)
    tile = lib.render("target.green", 50, 50)
    assert tuple(tile.get_at((25, 3)))[:3] == (0x11, 0x22, 0x33)
    assert b"#00A796" not in lib._src["target.green"]
    # the logo artwork keeps its colours (#00AAA5 is the turquoise it is printed in)
    theme.apply_tokens({"turquoise_ramp": ["#000000"] * 7})
    lib, _ = AssetLibrary.load(ASSETS)
    assert b"#00AAA5" in lib._src["logo.fa_horizontal"]


def test_bad_tokens_keep_the_brand_value(tmp_path):
    (tmp_path / "design-tokens.json").write_text(json.dumps({"color": {
        "red": "crimson", "blue_ramp": ["#000000"], "sparkle": "#FFFFFF", "_note": "ignored"}}), encoding="utf-8")
    problems = load_tokens(tmp_path)
    assert len(problems) == 3
    assert theme.RED == theme.DEFAULTS["RED"] and list(theme.BLUE_RAMP) == list(theme.DEFAULTS["BLUE_RAMP"])
    (tmp_path / "design-tokens.json").write_text("{not json", encoding="utf-8")
    assert len(load_tokens(tmp_path)) == 1
    assert load_tokens(ASSETS) == []


def test_shipped_tokens_are_the_brand_defaults():
    data = json.loads((ASSETS / "design-tokens.json").read_text(encoding="utf-8"))["color"]
    for key, value in data.items():
        default = theme.DEFAULTS[key.upper()]
        expected = [theme.to_hex(c) for c in default] if key.upper() in theme.RAMP_TOKENS else theme.to_hex(default)
        assert value == expected, key


def test_missing_files_fall_back_to_code(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"brand": {}, "themed": {"target.red": "nope.svg"}}),
                                            encoding="utf-8")
    lib, problems = AssetLibrary.load(tmp_path)
    assert len(problems) == 1 and not lib.has("target.red")
    sprites = Sprites(lib)
    assert sprites.target("red", 80).get_width() > 80
    assert sprites.symbol("times", 20, theme.RED).get_width() > 20
    assert sprites.image("panel.attract_card", 100, 30) is None
    broken = AssetLibrary({"target.red": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"})
    assert not broken.has("target.red")


def test_svg_and_code_shapes_agree():
    """The SVG target and its code fallback cover the same pixels (same shape, same size)."""
    with_svg = Sprites(AssetLibrary.load(ASSETS)[0])
    in_code = Sprites(AssetLibrary())
    for color in theme.TARGET_COLORS:
        a = pygame.mask.from_surface(with_svg.target(color, 120, shadow=False), 128)
        b = pygame.mask.from_surface(in_code.target(color, 120, shadow=False), 128)
        assert a.get_size() == b.get_size()
        # pygame draws its rounded corners a little tighter than the 18 % radius
        assert abs(a.count() - b.count()) / b.count() < 0.03
        assert a.overlap_area(b, (0, 0)) / a.count() > 0.99
        # ...and so does the white icon inside it
        a_white = pygame.mask.from_threshold(with_svg.target(color, 200, shadow=False), (255, 255, 255), (40, 40, 40, 255))
        b_white = pygame.mask.from_threshold(in_code.target(color, 200, shadow=False), (255, 255, 255), (40, 40, 40, 255))
        assert a_white.count() > 0 and a_white.overlap_area(b_white, (0, 0)) / max(a_white.count(), b_white.count()) > 0.93, color
    for kind in theme.SYMBOLS:
        a = pygame.mask.from_surface(with_svg.symbol(kind, 60, theme.DARK_BLUE), 128)
        b = pygame.mask.from_surface(in_code.symbol(kind, 60, theme.DARK_BLUE), 128)
        assert a.overlap_area(b, (0, 0)) / max(a.count(), b.count()) > 0.95, kind
    sym = with_svg.symbol("plus", 40, theme.RED)
    c = sym.get_at((sym.get_width() // 2, sym.get_height() // 2))
    assert tuple(c) == (*theme.RED, 255)
