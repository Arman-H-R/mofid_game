"""Persian text: bidi run splitting and the order runs are drawn in."""
import numpy as np
import pygame
import pytest

from market_pulse.ui.text import FontBook, Text, fa_digits, split_runs
from conftest import ROOT


def test_fa_digits():
    assert fa_digits("10 x 2 = 20") == "۱۰ x ۲ = ۲۰"


@pytest.mark.parametrize(
    "text, runs",
    [
        ("", []),
        ("SIMULATION", [("SIMULATION", False)]),
        ("۱۲۳", [("۱۲۳", False)]),
        ("امتیاز", [("امتیاز", True)]),
        ("۳۰ امتیاز", [("۳۰", False), (" امتیاز", True)]),
        ("ضریب ×۲", [("ضریب ", True), ("×۲", False)]),
        ("ضریب ×۲!", [("ضریب ", True), ("×۲", False), ("!", True)]),
        ("+۲۰ امتیاز", [("+۲۰", False), (" امتیاز", True)]),
        ("۵۰٪ رشد", [("۵۰٪", False), (" رشد", True)]),
        ("+ سه باکس تازه", [("+ سه باکس تازه", True)]),
        ("رتبه‌ی ۱ امروز", [("رتبه‌ی ", True), ("۱", False), (" امروز", True)]),
        ("نوبت Ali", [("نوبت ", True), ("Ali", False)]),
        ("۰۰:۴۵ ثانیه", [("۰۰:۴۵", False), (" ثانیه", True)]),
    ],
)
def test_split_runs(text, runs):
    assert split_runs(text) == runs


@pytest.fixture(scope="module")
def text():
    pygame.init()
    pygame.display.set_mode((64, 64))
    fb = FontBook(ROOT / "assets" / "fonts", {"bold": ["IRANSansX-Bold.ttf"], "body": ["IRANSansX-Medium.ttf"]})
    assert fb.shaping, "pygame-ce with HarfBuzz is required"
    yield Text(fb, persian_digits=True)
    pygame.quit()


def _alpha(surf):
    return pygame.surfarray.array_alpha(surf).astype(int)


def test_number_is_drawn_on_the_right_of_an_rtl_line(text):
    full = text.render("30 امتیاز", "bold", 60, (0, 0, 0))
    num = text.render("30", "bold", 60, (0, 0, 0))
    a, b = _alpha(full), _alpha(num)
    w, h = b.shape
    right = a[-w:, :h]
    left = a[:w, :h]
    assert np.abs(right - b).sum() == 0
    assert np.abs(left - b).sum() > 0


def test_digits_inside_a_number_keep_their_order(text):
    """'۱۰' must not come out as '۰۱' (HarfBuzz RTL alone would reverse it)."""
    ten = _alpha(text.render("10", "bold", 60, (0, 0, 0)))
    one = _alpha(text.render("1", "bold", 60, (0, 0, 0)))
    w, h = one.shape
    assert np.abs(ten[:w, :h] - one).sum() == 0  # '۱' is the leftmost glyph


def test_render_is_cached(text):
    a = text.render("نبض بازار", "bold", 40, (1, 2, 3))
    assert text.render("نبض بازار", "bold", 40, (1, 2, 3)) is a


def test_wrap_respects_width(text):
    lines = text.wrap("بعد از هر برخورد، همه‌ی باکس‌ها جابه‌جا می‌شوند", "body", 30, 260)
    assert len(lines) > 1
    assert all(text.width(line, "body", 30) <= 260 or " " not in line for line in lines)


def test_transient_render_is_not_cached(text):
    n = len(text._cache)
    text.render("98765", "bold", 50, (0, 0, 0), cache=False)
    assert len(text._cache) == n


def test_surface_cache_is_bounded_by_memory():
    from market_pulse.ui.draw import LRU

    cache = LRU(100, max_bytes=3 * 100 * 100 * 4)
    for i in range(10):
        cache.put(i, pygame.Surface((100, 100)))
    assert list(cache) == [7, 8, 9] and cache.bytes == 3 * 100 * 100 * 4
    cache.hit(7)  # recently used: 8 is now the oldest
    cache.put(10, pygame.Surface((100, 100)))
    assert list(cache) == [9, 7, 10]
    cache.clear()
    assert cache.bytes == 0


def test_every_game_text_can_be_drawn_with_the_fonts():
    """A letter the font lacks shows as an empty box on the wall."""
    import pygame

    from market_pulse.config import DEFAULTS

    pygame.init()
    fonts = FontBook(ROOT / "assets" / "fonts", DEFAULTS["fonts"])
    texts = [v for v in DEFAULTS["text"].values() if isinstance(v, str)]
    for style in DEFAULTS["profiles"]["styles"].values():
        texts += [style["label"], *style["statements"]]
    used = {c for text in texts for c in fa_digits(text.replace("{", "").replace("}", ""))} - {"\u200c"}
    for role in ("title", "heading", "bold", "body", "numbers"):
        font = fonts.get(role, 40)
        missing = [c for c, m in zip(sorted(used), font.metrics("".join(sorted(used)))) if m is None]
        assert not missing, (role, missing)


def test_long_player_names_are_shortened():
    from market_pulse.ui.screens import _short

    assert _short("  سارا   احمدی ", 18) == "سارا احمدی"
    long = _short("Bartholomew Alexander Fitzgerald-Smythe", 18)
    assert len(long) == 18 and long.endswith("…")
