"""The operator's settings page (F5): sliders, - and + buttons and switches for the values
worth changing on site. The boxes' sizes and points, the multipliers, Market Rush, the round,
sound and the replay, the screen, and the LiDAR (the lidar tool's settings, config/lidar.json:
its own tab with four pages, saved into that file).

The page also opens on its own, without the game (main.py --settings, App.run_settings): then
closing it ends the program, and leaving with unsaved changes needs a second Esc.

A change takes effect at once in the running game. A round reads some values when it starts
(the boxes' sizes, the balls, the multipliers), so those apply from the next round, and the
screen's values when the page closes (or on Save, without the game). Save writes
only the values changed on this page into config/settings.json, and leaves the rest of the file
as it is. Values set on the command line, such as --windowed, are not written to the file.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
from collections import ChainMap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pygame

from .. import config as C
from ..input.lidar.tuning import FIELDS as LIDAR_FIELDS
from . import theme
from .draw import Sprites, draw_symbol
from .text import Text
from .theme import Viewport

log = logging.getLogger(__name__)

Key = tuple  # where a value is: ("targets", "tiny", "points"), ("combo", "levels", 0, "multiplier")

UNITS = {"cm": "سانتی‌متر", "s": "ثانیه", "pts": "امتیاز", "hits": "برخورد", "%": "درصد", "px": "پیکسل",
         "mm": "میلی‌متر", "deg": "درجه", "pct": "درصد", "Hz": "هرتز", "scans": "اسکن", "boxes": "باکس", "": ""}
TIER_NAMES = (("medium", "بزرگ"), ("small", "متوسط"), ("tiny", "کوچک"))  # as the design names the three sizes
PREVIEW_PRODUCTS = ("easytrader", "boursview", "jet")  # the tiles the Boxes tab shows at the three sizes
LIDAR_UNITS = {"mm": "mm", "°": "deg", "s": "s", "%": "pct", "Hz": "Hz", "scans": "scans", "": ""}
LIDAR_CHOICES = {"circle": "دایره", "square": "مربع", "rectangle": "مستطیل", "touch": "لمس", "ball": "توپ"}
# the game's picture size in pixels: landscape (افقی) and portrait (عمودی) screens; [0, 0] = the display's own
RESOLUTIONS = (([0, 0], "خودکار"), ([1920, 1080], "افقی 1920×1080"), ([1080, 1920], "عمودی 1080×1920"),
               ([1280, 800], "افقی 1280×800"), ([800, 1280], "عمودی 800×1280"))
MIN_PIXELS = 320  # a typed width or height below this (but not 0, automatic) is raised to it
DIGIT_KEYS = {**{getattr(pygame, f"K_{d}"): str(d) for d in range(10)},
              **{getattr(pygame, f"K_KP{d}"): str(d) for d in range(10)}}
MAX_LEVELS = 3  # multiplier levels with rows on the page; more can be set in the file

# Page geometry in wall centimetres (laid out for the 300 x 400 cm wall, like the game).
TOP = 62.0  # below the title band and the tabs
LIDAR_TOP = 76.0  # the LiDAR pages have a second row of tabs
SLIDER_H = 30.0
SWITCH_H = 22.0
HELP_ALONE = "Esc: quit     Up / Down: choose     Left / Right or the mouse: change     Tab: next page     Ctrl+S: save"
HELP = "F5 / Esc: close     Up / Down: choose     Left / Right or the mouse: change     Tab: next page     Ctrl+S: save"
BUTTONS = (("save", "ذخیره"), ("try", "امتحان بدون ذخیره"), ("revert", "برگرداندن تغییرها"),
           ("defaults", "پیش‌فرض این صفحه"))


@dataclass(frozen=True)
class Item:
    key: Key
    label: str
    lo: float = 0.0
    hi: float = 1.0
    step: float = 1.0
    unit: str = ""  # cm, s, pts, hits, x (a multiplier), % (a fraction shown as a percentage)
    zero: str = ""  # shown instead of 0, for example "no limit"
    switch: bool = False
    options: tuple = ()  # ((value, label), ...): one of these, chosen with buttons
    shape: str | None = None  # a LiDAR row shown only for this area shape
    entry: bool = False  # a box to type the number in, instead of a slider
    needs: Key | None = None  # a switch that must be on for this value to count (shown faded when off)

    @property
    def height(self) -> float:
        return SWITCH_H if self.switch or self.options or self.entry else SLIDER_H


def pages(raw: dict) -> list[tuple[str, list[Item], str]]:
    """The tabs: title, rows, and what is drawn with the rows ("boxes": the boxes at their
    size, "best": the most one hit can score, "screen": the canvas now and when changes apply)."""
    boxes = [Item(("targets", "active"), "تعداد باکس‌های فعال روی دیوار", 1, C.MAX_BOXES, 1, "boxes"),
             Item(("targets", "grow_to"), "باکس‌های بیشتر با گذشت زمان، تا", 0, C.MAX_BOXES, 1, "boxes", zero="خاموش")]
    for tier, name in TIER_NAMES:
        boxes += [Item(("targets", tier, "size_cm"), f"اندازه‌ی باکس {name}", 15, 100, 1, "cm"),
                  Item(("targets", tier, "points"), f"امتیاز باکس {name}", 0, 200, 5, "pts")]

    combo = [Item(("combo", "enabled"), "ضریب برخوردهای پشت‌سرهم", switch=True)]
    for i in range(min(MAX_LEVELS, len(raw["combo"]["levels"]))):
        combo += [Item(("combo", "levels", i, "after_hits"), f"ضریب {i + 1} بعد از چند برخورد پشت‌سرهم", 1, 30, 1, "hits"),
                  Item(("combo", "levels", i, "multiplier"), f"مقدار ضریب {i + 1}", 1, 10, 0.5, "x")]
    combo += [Item(("rush", "score_multiplier"), "ضریب طوفان بازار", 1, 10, 0.5, "x"),
              Item(("rush", "stack_with_combo"), "ضریب طوفان در ضریب پشت‌سرهم ضرب شود", switch=True)]

    rush = [
        Item(("rush", "enabled"), "طوفان بازار", switch=True),
        Item(("rush", "duration_s"), "مدت طوفان", 1, 15, 0.5, "s"),
        Item(("rush", "trigger_tiny_hits"), "چند برخورد به باکس کوچک طوفان را شروع کند", 1, 10, 1, "hits"),
        Item(("targets", "min_tiny"), "کمترین تعداد باکس کوچک روی دیوار", 0, 4, 1, "boxes"),
        Item(("rush", "speed"), "سرعت باکس‌ها در طوفان", 0.5, 3, 0.1, "x"),
        Item(("rush", "max_targets"), "بیشترین تعداد باکس در طوفان", 1, C.MAX_BOXES, 1, "boxes"),
        Item(("rush", "spawn_interval_s"), "هر چند ثانیه یک باکس تازه", 0.3, 5, 0.1, "s"),
        Item(("rush", "cooldown_s"), "استراحت بین دو طوفان", 0, 30, 1, "s"),
    ]
    play = [
        Item(("round", "time_limit"), "محدودیت زمان", switch=True),
        Item(("round", "duration_s"), "زمان هر دور (بنویسید)", 5, 3600, 1, "s", entry=True, needs=("round", "time_limit")),
        Item(("round", "ball_limit"), "محدودیت تعداد توپ", switch=True),
        Item(("round", "balls"), "تعداد توپ (بنویسید)", 1, 100, 1, entry=True, needs=("round", "ball_limit")),
        Item(("motion", "enabled"), "حرکت باکس‌ها", switch=True),
        Item(("motion", "speed"), "سرعت حرکت باکس‌ها", 0.2, 3, 0.1, "x"),
        Item(("flow", "registration"), "صفحه‌ی نوشتن نام", switch=True),
        Item(("input", "ball_radius_cm"), "حاشیه‌ی برخورد دور هر باکس", 0, 50, 1, "cm"),
        Item(("flow", "countdown_s"), "شمارش معکوس", 1, 10, 1, "s"),
        Item(("flow", "results_s"), "زمان صفحه‌ی نتیجه", 8, 60, 1, "s"),
        Item(("flow", "time_warning_s"), "هشدار ثانیه‌های آخر", 0, 20, 1, "s"),
    ]
    media = [
        Item(("audio", "volume"), "بلندی صدا", 0, 1, 0.05, "%"),
        Item(("ui", "target_shadow"), "سایه‌ی باکس‌ها", switch=True),
        Item(("ui", "combo_banner"), "پیام بالا رفتن ضریب", switch=True),
        Item(("results", "show_names"), "نمایش نام بازیکن", switch=True),
        Item(("camera", "clip_before_s"), "ویدیو: ثانیه‌های پیش از امتیاز", 2, 20, 1, "s"),
        Item(("camera", "clip_after_s"), "ویدیو: ثانیه‌های پس از امتیاز", 0.5, 5, 0.5, "s"),
        Item(("camera", "slow_motion"), "ویدیو: سرعت حرکت آهسته", 0.1, 1, 0.05, "x"),
        Item(("camera", "delay_s"), "ویدیو: تأخیر تصویر دوربین", 0, 3, 0.1, "s"),
        Item(("camera", "effect_chance"), "ویدیو: احتمال جلوه‌ی رنگی عکس پایانی", 0, 1, 0.1, "%"),
    ]
    screen = [
        Item(("display", "fullscreen"), "تمام‌صفحه", switch=True),
        Item(("display", "fill_screen"), "بازی همه‌ی صفحه را پر کند، بدون کشیدن", switch=True),
        Item(("display", "resolution"), "اندازه‌ی تصویر", options=RESOLUTIONS),
        Item(("display", "resolution", 0), "عرض تصویر بازی (بنویسید)", 0, 7680, 1, "px", zero="خودکار", entry=True),
        Item(("display", "resolution", 1), "ارتفاع تصویر بازی (بنویسید)", 0, 7680, 1, "px", zero="خودکار", entry=True),
        Item(("display", "scale"), "مقیاس تصویر", 0.3, 1, 0.01, "%"),
        Item(("layout", "start_button", "size_cm"), "اندازه‌ی مربع شروع", 40, 140, 2, "cm"),
    ]
    return [("باکس‌ها", boxes, "boxes"), ("ضریب‌ها", combo, "best"), ("طوفان بازار", rush, ""),
            ("دور بازی", play, ""), ("صدا و ویدیو", media, ""), ("نمایش", screen, "screen")]


def lidar_item(name: str, label: str, zero: str = "") -> Item:
    """A row for a setting of the lidar tool, with its range and steps."""
    f = LIDAR_FIELDS[name]
    key = ("lidar", name)
    if f.kind == "bool":
        return Item(key, label, switch=True)
    if f.kind == "choice":
        return Item(key, label, options=tuple((o, LIDAR_CHOICES[o]) for o in f.options))
    return Item(key, label, f.low, f.high, f.step, LIDAR_UNITS[f.unit], zero, shape=f.shape)


def lidar_pages() -> list[tuple[str, list[Item], str]]:
    """The LiDAR tab's pages: the lidar tool's settings (LIdar/settings.py) without the ones only
    its own window and OSC output use."""
    L = lidar_item
    area = [L("shape", "شکل ناحیه‌ی لمس"), L("area_x", "مرکز ناحیه: چپ و راست"), L("area_y", "مرکز ناحیه: پایین و بالا"),
            L("radius", "شعاع دایره"), L("size", "ضلع مربع"), L("width", "عرض مستطیل"), L("height", "ارتفاع مستطیل"),
            L("min_distance", "نزدیک‌تر از این به LiDAR دیده نشود")]
    angle = [L("rotation", "چرخش نقشه"), L("angle_from", "از زاویه"), L("angle_to", "تا زاویه"),
             L("flip_x", "آینه‌ی چپ و راست"), L("flip_y", "آینه‌ی پایین و بالا")]
    detect = [L("bg_margin", "فاصله‌ی لازم از پس‌زمینه"), L("bg_margin_ratio", "فاصله‌ی اضافه برای جاهای دور"),
              L("min_width", "کوچک‌ترین جسم"), L("max_width", "بزرگ‌ترین جسم"),
              L("cluster_distance", "نقطه‌های نزدیک‌تر از این یک جسم‌اند"), L("touch_spacing", "شعاع یک دست"),
              L("calibration_time", "زمان اسکن پس‌زمینه")]
    ball = [L("mode", "حالت"), L("hit_delay", "مکث بعد از هر برخورد"), L("hit_radius", "شعاع مکث", "همه‌ی ناحیه"),
            L("ball_size", "قطر توپ", "خاموش"), L("confirm_scans", "لمس: تأیید بعد از"),
            L("release_time", "لمس: رها شدن بعد از"), L("match_distance", "لمس: بیشترین پرش"),
            L("smooth_min_cutoff", "لمس: نرمی وقتی ثابت است"), L("smooth_beta", "لمس: دنبال کردن حرکت تند")]
    return [("ناحیه", area, "lidar"), ("زاویه و آینه", angle, "lidar"), ("تشخیص", detect, "lidar"),
            ("توپ و لمس", ball, "lidar")]


# Under each slider: what the low end does (left, by −) and what the high end does (right, by +).
# A key with "*" stands for every colour or multiplier level.
HINTS: dict[Key, tuple[str, str]] = {
    ("targets", "active"): ("باکس کمتر روی دیوار: بازی سخت‌تر", "باکس بیشتر (تا 20؛ بالای 10 بعضی محصول‌ها دو بار): آسان‌تر"),
    ("targets", "grow_to"): ("خاموش یا کمتر از تعداد بالا: تعداد باکس‌ها ثابت می‌ماند",
                            "بیشتر: با گذشت زمان یکی‌یکی باکس اضافه می‌شود"),
    ("targets", "min_tiny"): ("0: باکس کوچک فقط به شانس می‌آید", "باکس کوچک بیشتر: طوفان زودتر"),
    ("targets", "*", "size_cm"): ("باکس کوچک‌تر: زدنش سخت‌تر", "باکس بزرگ‌تر: زدنش آسان‌تر"),
    ("targets", "*", "points"): ("امتیاز کمتر؛ 0: بی‌امتیاز", "امتیاز بیشتر برای هر برخورد"),
    ("input", "ball_radius_cm"): ("0: فقط خود باکس امتیاز دارد", "برخورد دورتر از باکس هم امتیاز دارد"),
    ("combo", "levels", "*", "after_hits"): ("ضریب زودتر می‌رسد", "برخورد پشت‌سرهم بیشتری لازم است"),
    ("combo", "levels", "*", "multiplier"): ("×1: بی‌اثر", "امتیازها چند برابر می‌شوند"),
    ("rush", "score_multiplier"): ("×1: طوفان امتیاز اضافه ندارد", "امتیاز در طوفان چند برابر می‌شود"),
    ("rush", "duration_s"): ("طوفان کوتاه‌تر", "طوفان طولانی‌تر"),
    ("rush", "trigger_tiny_hits"): ("طوفان زودتر شروع می‌شود", "برای طوفان باید باکس کوچک بیشتری زد"),
    ("rush", "speed"): ("باکس‌ها در طوفان آهسته‌تر", "تندتر: زدنشان سخت‌تر"),
    ("rush", "max_targets"): ("باکس کمتر در طوفان", "دیوار در طوفان شلوغ‌تر"),
    ("rush", "spawn_interval_s"): ("باکس تازه زودبه‌زود می‌آید", "باکس تازه دیرتر می‌آید"),
    ("rush", "cooldown_s"): ("0: طوفان بعدی بی‌درنگ ممکن است", "فاصله‌ی بیشتر بین دو طوفان"),
    ("round", "duration_s"): ("ثانیه؛ دور با تمام شدن زمان تمام می‌شود", "عدد بیشتر: دور طولانی‌تر"),
    ("round", "balls"): ("دور با آخرین توپ تمام می‌شود", "عدد بیشتر: توپ بیشتر برای هر بازیکن"),
    ("motion", "speed"): ("باکس‌ها آهسته حرکت می‌کنند", "تندتر: زدنشان سخت‌تر"),
    ("layout", "start_button", "size_cm"): ("مربع شروع کوچک‌تر", "بزرگ‌تر: زدنش آسان‌تر"),
    ("flow", "countdown_s"): ("بازی زودتر شروع می‌شود", "شمارش معکوس طولانی‌تر"),
    ("flow", "results_s"): ("نتیجه زودتر بسته می‌شود", "نتیجه بیشتر روی دیوار می‌ماند"),
    ("flow", "time_warning_s"): ("0: بدون هشدار پایان زمان", "هشدار پایان زمان زودتر شروع می‌شود"),
    ("audio", "volume"): ("0: بی‌صدا", "بلندترین صدا"),
    ("camera", "clip_before_s"): ("ویدیو از کمی پیش از امتیاز", "ویدیو از مدت بیشتری پیش از امتیاز"),
    ("camera", "clip_after_s"): ("ویدیو زود بعد از امتیاز تمام می‌شود", "ویدیو بیشتر بعد از امتیاز ادامه دارد"),
    ("camera", "slow_motion"): ("حرکت خیلی آهسته", "×1: سرعت عادی"),
    ("camera", "delay_s"): ("0: تصویر دوربین هم‌زمان با سنسور", "برای دوربینی که دیرتر از سنسور است"),
    ("camera", "effect_chance"): ("0: همیشه عکس ساده", "100: همیشه با جلوه‌ی رنگی"),
    ("display", "resolution", 0): ("0: خودکار، به اندازه‌ی صفحه", "پیکسل بیشتر: تیزتر، کار بیشتر برای کامپیوتر"),
    ("display", "resolution", 1): ("0: خودکار، به اندازه‌ی صفحه", "پیکسل بیشتر: تیزتر، کار بیشتر برای کامپیوتر"),
    ("display", "scale"): ("تصویر کوچک‌تر، با حاشیه‌ی تیره دورش", "100: تصویر همه‌ی صفحه را می‌گیرد"),
    ("lidar", "area_x"): ("ناحیه به چپ می‌رود", "ناحیه به راست می‌رود"),
    ("lidar", "area_y"): ("ناحیه پایین می‌رود", "ناحیه بالا می‌رود"),
    ("lidar", "radius"): ("کوچک‌تر: برخوردهای دور دیده نمی‌شوند", "بزرگ‌تر: بیرون دیوار هم دیده می‌شود"),
    ("lidar", "size"): ("کوچک‌تر: برخوردهای دور دیده نمی‌شوند", "بزرگ‌تر: بیرون دیوار هم دیده می‌شود"),
    ("lidar", "width"): ("ناحیه باریک‌تر", "ناحیه پهن‌تر"),
    ("lidar", "height"): ("ناحیه کوتاه‌تر", "ناحیه بلندتر"),
    ("lidar", "min_distance"): ("0: نزدیک LiDAR هم دیده می‌شود", "قاب و کابل نزدیک LiDAR دیده نمی‌شوند"),
    ("lidar", "rotation"): ("نقشه ساعت‌گرد می‌چرخد", "نقشه پادساعت‌گرد می‌چرخد"),
    ("lidar", "angle_from"): ("بازه از زاویه‌ی کمتر شروع می‌شود (0: راست)", "بازه از زاویه‌ی بیشتر شروع می‌شود"),
    ("lidar", "angle_to"): ("بازه زودتر تمام می‌شود", "360: تا دور کامل"),
    ("lidar", "bg_margin"): ("حساس‌تر، ولی نویز هم برخورد می‌شود", "کم‌حساس‌تر، نویز کمتر"),
    ("lidar", "bg_margin_ratio"): ("0: در همه‌جا یکسان", "جاهای دور کم‌حساس‌تر، نویز دور کمتر"),
    ("lidar", "min_width"): ("جسم‌های ریز (گرد و غبار) هم دیده می‌شوند", "فقط جسم‌های بزرگ‌تر، نویز کمتر"),
    ("lidar", "max_width"): ("جسم بزرگ (بدن، صندلی) دیده نمی‌شود", "جسم‌های بزرگ هم برخورد می‌شوند"),
    ("lidar", "cluster_distance"): ("یک جسم چند تکه دیده می‌شود", "دو جسم نزدیک یکی می‌شوند"),
    ("lidar", "touch_spacing"): ("دو جسم نزدیک: دو برخورد جدا", "جسم‌های نزدیک: یک برخورد"),
    ("lidar", "calibration_time"): ("اسکن پس‌زمینه سریع‌تر، کم‌دقت‌تر", "طولانی‌تر و دقیق‌تر"),
    ("lidar", "hit_delay"): ("0: برخوردهای پشت‌سرهم بی‌درنگ", "مدت بیشتری بعد از برخورد چیزی ثبت نمی‌شود"),
    ("lidar", "hit_radius"): ("0: مکث همه‌ی ناحیه را می‌بندد", "مکث فقط نزدیک برخورد قبلی (چند بازیکن)"),
    ("lidar", "ball_size"): ("0: برخورد جلوی توپ ثبت می‌شود", "برخورد به مرکز توپ برده می‌شود"),
    ("lidar", "confirm_scans"): ("لمس سریع‌تر، نویز بیشتر", "لمس دیرتر ولی مطمئن‌تر"),
    ("lidar", "release_time"): ("لمس زود رها می‌شود", "لمس پایدارتر، رها شدن دیرتر"),
    ("lidar", "match_distance"): ("حرکت تند دست لمس تازه می‌شود", "حرکت تند دنبال می‌شود؛ دست‌ها قاطی می‌شوند"),
    ("lidar", "smooth_min_cutoff"): ("دست ثابت آرام‌تر، با تأخیر", "تأخیر کمتر، لرزش بیشتر"),
    ("lidar", "smooth_beta"): ("حرکت تند با تأخیر دنبال می‌شود", "بی‌تأخیر، لرزش بیشتر"),
}


def hint(key: Key) -> tuple[str, str] | None:
    """What the low end and the high end of a slider do."""
    if key in HINTS:
        return HINTS[key]
    return HINTS.get(tuple("*" if isinstance(p, int) or p in C.TIERS else p for p in key))


class LidarValues:
    """The lidar tool's settings as a dict, for get() and put(): ("lidar", "bg_margin")."""

    def __init__(self, tuning):
        self.tuning = tuning

    def __getitem__(self, name: str) -> Any:
        return getattr(self.tuning, name)

    def __setitem__(self, name: str, value: Any) -> None:
        self.tuning.set(name, value)


# -- values -----------------------------------------------------------------------------------
def get(raw: Any, key: Key) -> Any:
    for part in key:
        raw = raw[part]
    return raw


def put(raw: Any, key: Key, value: Any) -> None:
    get(raw, key[:-1])[key[-1]] = value


def default(key: Key) -> Any:
    """The built-in value, or None (a multiplier level the defaults do not have)."""
    if key[0] == "lidar":
        return LIDAR_FIELDS[key[1]].default
    try:
        return get(C.DEFAULTS, key)
    except (KeyError, IndexError):
        return None


def bounds(item: Item, raw: dict) -> tuple[float, float]:
    """The multiplier levels stay in order: each needs more hits in a row than the one before."""
    lo, hi = item.lo, item.hi
    if item.key[:2] == ("combo", "levels") and item.key[3] == "after_hits":
        levels, i = raw["combo"]["levels"], item.key[2]
        if i > 0:
            lo = max(lo, levels[i - 1]["after_hits"] + 1)
        if i + 1 < len(levels):
            hi = min(hi, levels[i + 1]["after_hits"] - 1)
    return lo, hi


def tidy(item: Item, v: float) -> int | float:
    """Whole numbers stay whole in the file (the combo levels must be), the rest is rounded."""
    if float(item.step).is_integer() or (float(v).is_integer() and not isinstance(default(item.key), float)):
        return int(round(v))
    return round(v, 3)


def snap(item: Item, raw: dict, v: float) -> int | float:
    """v moved onto the item's steps and inside its range."""
    lo, hi = bounds(item, raw)
    if lo > hi:  # no room between its neighbours: it stays
        return get(raw, item.key)
    v = item.lo + round((v - item.lo) / item.step) * item.step
    return tidy(item, min(max(v, lo), hi))


def nudge(item: Item, raw: dict, steps: int) -> Any:
    """The next value up (steps > 0) or down, also from a value that is between two steps."""
    v = get(raw, item.key)
    if item.switch:
        return not v
    if item.options:
        values = [o for o, _ in item.options]
        return values[(values.index(v) + steps) % len(values)] if v in values else values[0]
    k = (v - item.lo) / item.step
    k = math.floor(k + 1e-9) + steps if steps > 0 else math.ceil(k - 1e-9) + steps
    return snap(item, raw, item.lo + k * item.step)


def shown(item: Item, v: Any) -> str:
    if item.switch:
        return "روشن" if v else "خاموش"
    if item.options:
        return next((label for o, label in item.options if o == v), str(v))
    if v == 0 and item.zero:
        return item.zero
    if item.unit == "%":
        v = round(v * 100)
    num = f"{v:g}"
    if item.unit == "x":
        return f"×{num}"
    return f"{num} {UNITS[item.unit]}".strip()


def write_changes(file: Path, source: Path | None, raw: dict, keys: list[Key]) -> None:
    """Write the values at ``keys`` into the settings file, leaving the rest of it as it is.
    A file that does not exist yet starts as a copy of ``source`` (the settings inside the .exe).
    A value inside a list (a multiplier level) writes the whole list."""
    start = file if file.is_file() else source
    doc: dict = {}
    if start is not None and start.is_file():
        doc = json.loads(start.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError(f"{start.name} must contain a JSON object")
    for key in keys:
        cut = next((i for i, part in enumerate(key) if isinstance(part, int)), len(key))
        key = key[:cut]
        node = doc
        for part in key[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[key[-1]] = copy.deepcopy(get(raw, key))
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, file)  # a crash while writing never leaves half a file


# -- the page ----------------------------------------------------------------------------------
class SettingsPage:
    def __init__(self, cfg: C.Section, text: Text, sprites: Sprites, file: Path | None,
                 source: Path | None = None, on_change: Callable[[], None] | None = None, lidar=None):
        """``lidar``: the lidar tool's settings (input.lidar's GameTuning, or a lidar Settings read
        from its file); its save() writes config/lidar.json. None: no LiDAR tab."""
        self.raw = cfg.raw()  # the live settings: a change here is a change in the game
        self.lidar = lidar
        # get() and put() reach the lidar values as ("lidar", name)
        self.root = ChainMap({"lidar": LidarValues(lidar)}, self.raw) if lidar is not None else self.raw
        self.W = float(cfg.wall.get("layout_width_cm", cfg.wall.width_cm))  # the 3:4 layout (Renderer.vp_menu)
        self.H = float(cfg.wall.get("layout_height_cm", cfg.wall.height_cm))
        self.text = text
        self.sprites = sprites
        self.file = file
        self.source = source or file
        self.on_change = on_change or (lambda: None)
        self.pages = pages(self.raw) + (lidar_pages() if lidar is not None else [])
        self.open = False
        self.tab = 0
        self.row = 0
        self.saved = self._values()  # as the game started, or as last saved
        self.message = ("", "")  # text, "ok" | "error" | ""
        self._hits: list[tuple[pygame.Rect, str, Any]] = []  # from the last draw, for the mouse
        self._tracks: dict[int, pygame.Rect] = {}
        self._drag: int | None = None
        self._mouse: tuple[int, int] | None = None  # the last position, for the wheel
        self.standalone = False  # opened without the game: closing ends the program
        self._leaving = False  # Esc once with unsaved changes (standalone)
        self._edit: int | None = None  # the row whose number is being typed
        self._typed = ""
        self.game_size: tuple[int, int] | None = None  # the game's picture, when the page has a window of its own

    @property
    def buttons(self) -> list[tuple[str, str]]:
        if not self.standalone:
            return list(BUTTONS)
        return [(name, "خروج بدون ذخیره" if name == "revert" else label) for name, label in BUTTONS if name != "try"]

    @property
    def items(self) -> list[Item]:
        """The rows of this page (a LiDAR area row only for the area's shape)."""
        items = self.pages[self.tab][1]
        if self.lidar is None:
            return items
        return [it for it in items if it.shape is None or it.shape == self.lidar.shape]

    def _values(self) -> dict[Key, Any]:
        return {it.key: copy.deepcopy(get(self.root, it.key)) for _, items, _ in self.pages for it in items}

    def changed(self) -> list[Key]:
        """Values that differ from the file (unsaved). A row inside another changed one
        (the picture's width inside its size) is not counted again."""
        keys = [key for key, v in self.saved.items() if get(self.root, key) != v]
        return [key for key in keys if not any(len(k) < len(key) and key[:len(k)] == k for k in keys)]

    def show(self) -> None:
        self.open = True
        self.message = ("", "")

    def hide(self) -> None:
        self._commit()
        self.open = False
        self._drag = None

    def select_tab(self, i: int) -> None:
        self._commit()
        self.tab = i % len(self.pages)
        self.row = 0
        self._drag = None

    def _set(self, item: Item, value: Any) -> None:
        if value != get(self.root, item.key):
            put(self.root, item.key, copy.deepcopy(value))  # never a preset's own list
            self.message = ("", "")
            self._leaving = False
            self._one_limit(item.key)
            self.on_change()

    LIMITS = (("round", "time_limit"), ("round", "ball_limit"))

    def _one_limit(self, key: Key) -> None:
        """A round needs a time limit or a ball limit: turning off the last one turns the other on."""
        if key in self.LIMITS and not any(get(self.root, k) for k in self.LIMITS):
            other = next(k for k in self.LIMITS if k != key)
            put(self.root, other, True)
            self.message = ("دور بازی باید محدودیت زمان یا توپ داشته باشد؛ دیگری روشن شد", "ok")

    def _nudge(self, i: int, steps: int) -> None:
        self.row = i
        item = self.items[i]
        self._set(item, nudge(item, self.root, steps))

    def _slide(self, i: int, x: int) -> None:
        item, track = self.items[i], self._tracks.get(i)
        if track is None:
            return
        frac = min(max((x - track.x) / max(1, track.w), 0.0), 1.0)
        self._set(item, snap(item, self.root, item.lo + frac * (item.hi - item.lo)))

    # -- buttons ----------------------------------------------------------------------------
    def press(self, name: str) -> str | None:
        """A button. Returns "saved" or "close" when the page should close."""
        if name == "save":
            return "saved" if self.save() else None
        if name == "try":
            return "close"
        if name == "revert":
            for key, v in self.saved.items():
                put(self.root, key, copy.deepcopy(v))
            self.on_change()
            return "close"
        if name == "defaults":
            for item in self.items:
                d = default(item.key)
                if d is not None:
                    put(self.root, item.key, copy.deepcopy(d))
            self.on_change()
            self.message = ("مقدارهای پیش‌فرض این صفحه گذاشته شد؛ برای ماندن، ذخیره کنید", "ok")
        return None

    def save(self) -> bool:
        """The game's values into the settings file, the LiDAR's into the lidar tool's file."""
        changed = self.changed()
        if not changed:
            return True
        game = [key for key in changed if key[0] != "lidar"]
        lidar = [key for key in changed if key[0] == "lidar"]
        if game and self.file is None:
            self.message = ("فایل تنظیمات مشخص نیست؛ ذخیره نشد", "error")
            return False
        for keys, where in ((game, self.file), (lidar, self.lidar.path if lidar else None)):
            if not keys:
                continue
            try:
                if keys is game:
                    write_changes(self.file, self.source, self.raw, game)
                else:
                    self.lidar.save()
            except (OSError, ValueError) as exc:
                log.error("settings: could not save %s: %s", where, exc)
                self.message = (f"ذخیره نشد: {exc}", "error")
                return False
            log.info("settings: saved %d change(s) to %s: %s", len(keys), where,
                     ", ".join(".".join(str(p) for p in key) + f"={get(self.root, key)}" for key in keys))
            for key in keys:
                self.saved[key] = copy.deepcopy(get(self.root, key))
        return True

    # -- operator input ---------------------------------------------------------------------
    def handle(self, event: pygame.event.Event) -> str | None:
        """A key or mouse event while the page is open. Returns "close" or "saved" when it closes."""
        if event.type == pygame.KEYDOWN:
            return self._key(event.key, event.mod)
        if hasattr(event, "pos"):
            self._mouse = event.pos  # canvas pixels, also when the game is stretched over the screen
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            return self._click(event.pos)
        if event.type == pygame.MOUSEMOTION and self._drag is not None:
            self._slide(self._drag, event.pos[0])
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._drag = None
        elif event.type == pygame.MOUSEWHEEL:
            i = self._row_at(self._mouse or pygame.mouse.get_pos())
            if i is not None and event.y:
                self._nudge(i, 1 if event.y > 0 else -1)
        return None

    def _start_edit(self, i: int) -> None:
        self.row = self._edit = i
        self._typed = ""

    def _commit(self) -> None:
        """The typed number into the setting (nothing typed: it stays as it was)."""
        i, self._edit = self._edit, None
        if i is None or not self._typed or i >= len(self.items):
            return
        item = self.items[i]
        v = float(self._typed)
        if item.unit == "px" and 0 < v < MIN_PIXELS:
            v = MIN_PIXELS
        self._set(item, snap(item, self.root, v))

    def _type(self, key: int) -> str | None:
        """A key while a number is typed: digits, Backspace, Enter (or Tab) sets it, Esc leaves it."""
        if key in DIGIT_KEYS:
            if len(self._typed) < 5:
                self._typed += DIGIT_KEYS[key]
        elif key == pygame.K_BACKSPACE:
            self._typed = self._typed[:-1]
        elif key == pygame.K_ESCAPE:
            self._edit = None
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB, pygame.K_UP, pygame.K_DOWN):
            self._commit()
        return None

    def _key(self, key: int, mod: int) -> str | None:
        if self._edit is not None:
            if key != pygame.K_F5 and not (key == pygame.K_s and mod & pygame.KMOD_CTRL):
                return self._type(key)
            self._commit()  # closing or saving keeps what was typed
        n = len(self.items)
        self.row = min(self.row, n - 1)  # a LiDAR shape with fewer rows
        if key in (pygame.K_ESCAPE, pygame.K_F5):
            if self.standalone and self.changed() and not self._leaving:  # leaving now would lose them
                self._leaving = True
                self.message = ("تغییرها ذخیره نشده‌اند: Ctrl+S برای ذخیره، یا دوباره Esc برای خروج بدون ذخیره", "error")
                return None
            return "close"
        if key == pygame.K_s and mod & pygame.KMOD_CTRL:
            return self.press("save")
        if key == pygame.K_TAB:
            self.select_tab(self.tab + (-1 if mod & pygame.KMOD_SHIFT else 1))
        elif key in (pygame.K_PAGEDOWN, pygame.K_PAGEUP):
            self.select_tab(self.tab + (1 if key == pygame.K_PAGEDOWN else -1))
        elif pygame.K_1 <= key < pygame.K_1 + min(9, len(self.pages)):
            self.select_tab(key - pygame.K_1)
        elif key in (pygame.K_DOWN, pygame.K_UP):
            self.row = (self.row + (1 if key == pygame.K_DOWN else -1)) % n
        elif key in (pygame.K_RIGHT, pygame.K_LEFT):
            steps = 1 if key == pygame.K_RIGHT else -1
            self._nudge(self.row, steps * (5 if mod & pygame.KMOD_SHIFT else 1))
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.items[self.row].entry:
            self._start_edit(self.row)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE) and (self.items[self.row].switch
                                                                              or self.items[self.row].options):
            self._nudge(self.row, 1)
        return None

    def _click(self, pos: tuple[int, int]) -> str | None:
        self._commit()  # a click anywhere ends the typing
        for rect, action, arg in reversed(self._hits):  # the small controls lie over their row
            if not rect.collidepoint(pos):
                continue
            if action == "entry":
                self._start_edit(arg)
                return None
            if action == "tab":
                self.select_tab(arg)
            elif action == "button":
                return self.press(arg)
            elif action == "track":
                self.row = self._drag = arg
                self._slide(arg, pos[0])
            elif action in ("minus", "plus"):
                self._nudge(arg, -1 if action == "minus" else 1)
            elif action == "option":
                self.row = arg[0]
                self._set(self.items[arg[0]], arg[1])
            elif action == "row":
                self.row = arg
                if self.items[arg].entry:
                    self._start_edit(arg)
                elif self.items[arg].switch:
                    self._nudge(arg, 1)
            return None
        return None

    def _row_at(self, pos: tuple[int, int]) -> int | None:
        return next((arg for rect, action, arg in self._hits if action == "row" and rect.collidepoint(pos)), None)

    # -- drawing ----------------------------------------------------------------------------
    def draw(self, surf: pygame.Surface, vp: Viewport, wall: pygame.Rect | None = None) -> None:
        """``wall``: the whole wall, when the page's 3:4 frame (vp) is only its middle: the page's
        white and its title band reach the wall's edges."""
        self._hits = []
        self._tracks = {}
        W = self.W
        wall = wall or vp.rect
        surf.fill(theme.BLUE_DEEP)  # the letterbox outside the wall (display.scale, fill_screen off)
        surf.fill(theme.WHITE, wall)
        band = vp.cm_rect(0, 0, W, 33)
        surf.fill(theme.DARK_BLUE, pygame.Rect(wall.x, wall.y, wall.w, band.bottom - wall.y))
        self.text.blit(surf, "تنظیمات بازی", "title", vp.s(11), theme.WHITE, vp.px(W / 2, 14))
        self.text.blit(surf, HELP_ALONE if self.standalone else HELP, "latin", vp.s(2.7), theme.BLUE_PALE, vp.px(W / 2, 27.5), localize=False)
        changed = set(self.changed())
        self._tabs(surf, vp, changed)
        title, _, extra = self.pages[self.tab]
        items = self.items
        self.row = min(self.row, len(items) - 1)
        y = TOP
        if extra == "lidar":
            self._lidar_tabs(surf, vp, changed)
            y = LIDAR_TOP
        if extra == "boxes":
            y = self._boxes(surf, vp, y)
        for i, item in enumerate(items):
            self._row(surf, vp, i, item, y, item.key in changed)
            y += item.height
        if extra == "best":
            self._best_hit(surf, vp, y + 4)
        if extra == "screen":
            self._screen_notes(surf, vp, y + 6)
        if extra == "lidar":
            self._lidar_notes(surf, vp, y + 4)
        self._buttons(surf, vp, len(changed))

    def _screen_notes(self, surf: pygame.Surface, vp: Viewport, y: float) -> None:
        when = "با ذخیره" if self.standalone else "با بستن این صفحه"
        gw, gh = self.game_size or (vp.win_w, vp.win_h)
        notes = (f"تصویر بازی الان {gw} × {gh} پیکسل است.",
                 f"تغییرهای این صفحه {when} اعمال می‌شوند.",
                 "خودکار: به اندازه‌ی صفحه‌نمایش. اگر فقط یکی را بدهید، دیگری از شکل دیوار می‌آید.",
                 "تصویر هیچ‌وقت کشیده نمی‌شود. پر کردن صفحه: بازی به شکل صفحه درمی‌آید و منوها در وسط می‌مانند.",
                 "پر کردن صفحه: بازی به شکل صفحه (افقی یا عمودی) درمی‌آید؛ بعد LiDAR را دوباره کالیبره کنید (F2 و C).",
                 "عرض و ارتفاع: روی جعبه بزنید، عدد را بنویسید و Enter بزنید (کمترین: 320).",
                 "بعد از تغییر مقیاس تصویر هم LiDAR را دوباره کالیبره کنید (F2 و C).")
        for i, note in enumerate(notes):
            self.text.blit(surf, note, "body", vp.s(3.6), theme.TEXT_GREY, vp.px(self.W - 14, y + i * 8), "topright")

    def _tabs(self, surf: pygame.Surface, vp: Viewport, changed: set) -> None:
        """One tab per page of the game, and one "LiDAR" for all the LiDAR pages."""
        tabs = [(title, [i]) for i, (title, _, extra) in enumerate(self.pages) if extra != "lidar"]
        lidar = [i for i, (_, _, extra) in enumerate(self.pages) if extra == "lidar"]
        if lidar:
            tabs.append(("LiDAR", lidar))
        n, gap = len(tabs), 3.0
        w = (self.W - 20 - gap * (n - 1)) / n
        for k, (title, group) in enumerate(tabs):
            right = self.W - 10 - k * (w + gap)  # the first tab on the right, as Persian is read
            rect = vp.cm_rect(right - w, 39, right, 54)
            active = self.tab in group
            surf.blit(self.sprites.rounded(rect.w, rect.h, rect.h // 2, theme.DARK_BLUE if active else theme.BLUE_PALE), rect)
            self.text.blit(surf, title, "bold", vp.s(4.2), theme.WHITE if active else theme.DARK_BLUE, rect.center)
            if any(it.key in changed for i in group for it in self.pages[i][1]):  # unsaved changes there
                dot = self.sprites.dot(vp.sf(1.3), theme.PURPLE)
                surf.blit(dot, dot.get_rect(center=(rect.right - vp.s(4), rect.y + vp.s(3))))
            self._hits.append((rect, "tab", self.tab if active else group[0]))

    def _lidar_tabs(self, surf: pygame.Surface, vp: Viewport, changed: set) -> None:
        """The second row of tabs: the LiDAR's pages."""
        tabs = [i for i, (_, _, extra) in enumerate(self.pages) if extra == "lidar"]
        n, gap = len(tabs), 3.0
        w = (self.W - 40 - gap * (n - 1)) / n
        for k, i in enumerate(tabs):
            right = self.W - 20 - k * (w + gap)
            rect = vp.cm_rect(right - w, 58, right, 70)
            active = i == self.tab
            surf.blit(self.sprites.rounded(rect.w, rect.h, rect.h // 2, theme.TURQUOISE if active else theme.GREY_LIGHT), rect)
            self.text.blit(surf, self.pages[i][0], "bold", vp.s(3.8), theme.WHITE if active else theme.DARK_BLUE, rect.center)
            if any(it.key in changed for it in self.pages[i][1]):
                dot = self.sprites.dot(vp.sf(1.1), theme.PURPLE)
                surf.blit(dot, dot.get_rect(center=(rect.right - vp.s(3.5), rect.y + vp.s(2.5))))
            self._hits.append((rect, "tab", i))

    def _lidar_notes(self, surf: pygame.Surface, vp: Viewport, y: float) -> None:
        notes = {
            "ناحیه": ("فقط برخوردهای داخل این ناحیه دیده می‌شوند (میلی‌متر، روی نقشه‌ی LiDAR).",
                      "وقتی LiDAR کالیبره است، ناحیه خود دیوار است؛ این ناحیه فقط بدون کالیبره",
                      "و هنگام کالیبره کردن (F2 و C) به کار می‌رود."),
            "زاویه و آینه": ("چرخش و آینه نقشه‌ی LiDAR را عوض می‌کنند:",
                             "بعد از تغییرشان LiDAR را دوباره کالیبره کنید (F2 و C).",
                             "از 0 تا 360 درجه: همه‌ی زاویه‌ها."),
            "تشخیص": ("زمان اسکن از اسکن بعدی پس‌زمینه اعمال می‌شود: F6 (دیوار خالی باشد).",),
        }.get(self.pages[self.tab][0], ())
        notes += (f"در بازی همان لحظه اعمال می‌شوند؛ ذخیره در {Path(self.lidar.path).name}",)
        for i, note in enumerate(notes):
            self.text.blit(surf, note, "body", vp.s(3.4), theme.TEXT_GREY, vp.px(self.W - 14, y + i * 7), "topright")

    def _boxes(self, surf: pygame.Surface, vp: Viewport, y: float) -> float:
        """The three sizes side by side, standing on one line: at their real size on the wall
        when they fit, otherwise all scaled down the same. A line around each shows where a hit
        still counts (input.ball_radius_cm). Under them, all the products, one lit for every
        box that is on the wall at once (targets.active)."""
        raw = self.raw["targets"]
        pad = max(0.0, float(self.raw["input"]["ball_radius_cm"]))
        aspect = 0.92
        sizes = [float(raw[c]["size_cm"]) for c, _ in TIER_NAMES]
        room_h, gap = 26.0, 16.0 + 2 * pad
        k = min(1.0, (self.W - 60 - 2 * gap) / sum(sizes), room_h / (max(sizes) * aspect + 2 * pad))
        base = y + 4 + room_h
        x = (self.W + sum(sizes) * k + 2 * gap) / 2  # from the right
        for (tier, _), size, key in zip(TIER_NAMES, sizes, PREVIEW_PRODUCTS):
            w = size * k
            h = w * self.sprites.product_aspect.get(key, aspect)
            cx, cy = x - w / 2, base - h / 2
            tile = self.sprites.product(key, vp.s(w))
            surf.blit(tile, tile.get_rect(center=vp.px(cx, cy)))
            if pad > 0:
                rx, ry = w / 2 + pad * k, h / 2 + pad * k
                pygame.draw.rect(surf, theme.TURQUOISE, vp.cm_rect(cx - rx, cy - ry, cx + rx, cy + ry),
                                 max(1, vp.s(0.5)), border_radius=vp.s(1.5))
            self.text.blit(surf, f"+{raw[tier]['points']}", "numbers", vp.s(4.2), theme.DARK_BLUE,
                           vp.px(cx, base + 4.5 + pad * k))
            x -= w + gap
        note = "اندازه‌ی واقعی روی دیوار" if k >= 1.0 else f"کوچک‌شده تا {round(k * 100)} درصد"
        self.text.blit(surf, note, "body", vp.s(3.2), theme.TEXT_GREY, vp.px(12, y + 4), "topleft")
        # the active boxes: every product, lit for each box on the wall at once
        products = self.raw.get("products") or C.PRODUCTS
        n, active = len(products), int(raw["active"])
        tw = min(11.0, (self.W - 70) / n - 1.5)
        row_y = base + 12 + pad * k
        x = self.W - 14 - tw / 2
        for i, p in enumerate(products):
            tile = self.sprites.product(p["key"], vp.s(tw))
            tile.set_alpha(255 if i < active else 55)
            surf.blit(tile, tile.get_rect(center=vp.px(x, row_y)))
            tile.set_alpha(255)
            x -= tw + 1.5
        label = f"{active} از {n} محصول هم‌زمان" if active <= n else f"{active} باکس، بعضی محصول‌ها دو بار"
        self.text.blit(surf, label, "bold", vp.s(3.6), theme.TURQUOISE_RAMP[0],
                       vp.px(12, row_y), "midleft")
        return row_y + tw * aspect / 2 + 3

    def _row(self, surf: pygame.Surface, vp: Viewport, i: int, item: Item, y: float, unsaved: bool) -> None:
        W, t = self.W, self.text
        row = vp.cm_rect(8, y + 1, W - 8, y + item.height - 1)
        if i == self.row:
            surf.blit(self.sprites.rounded(row.w, row.h, vp.s(3), theme.TURQUOISE_PALE), row)
        self._hits.append((row, "row", i))
        v = get(self.root, item.key)
        mid = y + (item.height / 2 if item.switch or item.options or item.entry else 7.5)
        off = item.needs is not None and not get(self.root, item.needs)  # its switch is off: not used now
        t.blit(surf, item.label, "body", vp.s(4.4), theme.TEXT_GREY if off else theme.DARK_BLUE, vp.px(W - 14, mid), "midright")
        if unsaved:
            dot = self.sprites.dot(vp.sf(1.1), theme.PURPLE)
            surf.blit(dot, dot.get_rect(center=vp.px(W - 10.5, mid)))
        if item.entry:
            box = vp.cm_rect(14, mid - 5, 58, mid + 5)
            typing = self._edit == i
            surf.blit(self.sprites.rounded(box.w, box.h, vp.s(2), theme.WHITE), box)
            pygame.draw.rect(surf, theme.TURQUOISE if typing else theme.GREY_LIGHT, box, max(1, vp.s(0.5)), border_radius=vp.s(2))
            if typing:
                cursor = "|" if int(pygame.time.get_ticks() / 500) % 2 == 0 else " "
                text = (self._typed or "") + cursor
                t.blit(surf, text, "latin", vp.s(4.6), theme.DARK_BLUE, (box.right - vp.s(3), box.centery), "midright",
                       localize=False, cache=False)
                t.blit(surf, "Enter: ثبت     Esc: لغو", "body", vp.s(3.4), theme.TURQUOISE_RAMP[0], vp.px(62, mid), "midleft")
            else:
                t.blit(surf, shown(item, v), "heading", vp.s(4.4), theme.GREY if off else theme.TURQUOISE_RAMP[0], box.center)
                tips = ("خاموش: به کار نمی‌رود", "") if off else hint(item.key)
                if tips is not None:
                    t.blit(surf, tips[0], "body", vp.s(3.4), theme.TEXT_GREY, vp.px(62, mid), "midleft")
            self._hits.append((box, "entry", i))
            return
        if item.options:
            pw = 23.0 if max(len(label) for _, label in item.options) <= 8 else 37.0  # a wider button for a long name
            for k, (value, label) in enumerate(item.options):
                rect = vp.cm_rect(14 + k * (pw + 2), mid - 4.5, 14 + pw + k * (pw + 2), mid + 4.5)
                on = value == v
                surf.blit(self.sprites.rounded(rect.w, rect.h, rect.h // 2, theme.TURQUOISE if on else theme.GREY_LIGHT), rect)
                t.blit(surf, label, "bold", vp.s(3.8), theme.WHITE if on else theme.DARK_BLUE, rect.center)
                self._hits.append((rect, "option", (i, value)))
            return
        if item.switch:
            sw = vp.cm_rect(14, mid - 4.5, 32, mid + 4.5)
            surf.blit(self.sprites.rounded(sw.w, sw.h, sw.h // 2, theme.TURQUOISE if v else theme.GREY_LIGHT), sw)
            knob = self.sprites.dot(sw.h * 0.36, theme.WHITE)
            surf.blit(knob, knob.get_rect(center=(sw.right - sw.h // 2 if v else sw.x + sw.h // 2, sw.centery)))
            t.blit(surf, shown(item, v), "bold", vp.s(4), theme.TEXT_GREY, vp.px(35, mid), "midleft")
            return
        t.blit(surf, shown(item, v), "heading", vp.s(4.8), theme.TURQUOISE_RAMP[0], vp.px(14, mid), "midleft")
        ly = y + 17
        tips = hint(item.key)
        if tips is not None:  # the low end by −, the high end by +
            t.blit(surf, "کم: " + tips[0], "body", vp.s(3.4), theme.TEXT_GREY, vp.px(14, y + 25.5), "midleft")
            t.blit(surf, "زیاد: " + tips[1], "body", vp.s(3.4), theme.TEXT_GREY, vp.px(W - 14, y + 25.5), "midright")
        for action, cx in (("minus", 18.0), ("plus", W - 18)):
            c = vp.px(cx, ly)
            r = vp.s(4.6)
            disc = self.sprites.dot(r, theme.BLUE_PALE)
            surf.blit(disc, disc.get_rect(center=c))
            draw_symbol(surf, action, c, vp.s(4.4), theme.DARK_BLUE)
            self._hits.append((pygame.Rect(c[0] - r, c[1] - r, 2 * r, 2 * r), action, i))
        x0, x1 = 28.0, W - 28
        track = vp.cm_rect(x0, ly - 0.8, x1, ly + 0.8)
        pygame.draw.rect(surf, theme.GREY_LIGHT, track, border_radius=track.h // 2)
        frac = (min(max(v, item.lo), item.hi) - item.lo) / (item.hi - item.lo)
        if frac > 0:
            pygame.draw.rect(surf, theme.TURQUOISE, pygame.Rect(track.x, track.y, max(track.h, int(track.w * frac)), track.h),
                             border_radius=track.h // 2)
        d = default(item.key)
        if d is not None and item.lo <= d <= item.hi:  # where the built-in value is
            dx = track.x + int(track.w * (d - item.lo) / (item.hi - item.lo))
            surf.fill(theme.GREY, pygame.Rect(dx - max(1, vp.s(0.25)), track.y - vp.s(2), max(2, vp.s(0.5)), track.h + vp.s(4)))
        knob = (track.x + int(track.w * frac), track.centery)
        ring = self.sprites.dot(vp.sf(3.6), theme.TURQUOISE)
        surf.blit(ring, ring.get_rect(center=knob))
        inner = self.sprites.dot(vp.sf(2.5), theme.WHITE)
        surf.blit(inner, inner.get_rect(center=knob))
        self._tracks[i] = track
        self._hits.append((vp.cm_rect(x0 - 3, ly - 6, x1 + 3, ly + 6), "track", i))

    def _best_hit(self, surf: pygame.Surface, vp: Viewport, y: float) -> None:
        """What one hit can score at most, worked out the way the game does."""
        raw = self.raw
        pts = max(raw["targets"][c]["points"] for c in C.TIERS)
        combo = max([1] + [lv["multiplier"] for lv in raw["combo"]["levels"]]) if raw["combo"]["enabled"] else 1
        rush = raw["rush"]["score_multiplier"] if raw["rush"]["enabled"] else 1
        if raw["rush"]["stack_with_combo"]:
            formula, best = f"{pts} × {combo:g} × {rush:g}", pts * combo * rush
        else:
            formula, best = f"{pts} × {max(combo, rush):g}", pts * max(combo, rush)
        card = vp.cm_rect(14, y, self.W - 14, y + 32)
        surf.blit(self.sprites.rounded(card.w, card.h, vp.s(4), theme.BLUE_PALE), card)
        self.text.blit(surf, "بیشترین امتیاز یک برخورد", "bold", vp.s(4.2), theme.DARK_BLUE, vp.px(self.W / 2, y + 9))
        self.text.blit(surf, f"{formula} = {int(round(best))}", "numbers", vp.s(7), theme.DARK_BLUE, vp.px(self.W / 2, y + 22))

    def _buttons(self, surf: pygame.Surface, vp: Viewport, unsaved: int) -> None:
        buttons = self.buttons
        n, gap = len(buttons), 4.0
        w = (self.W - 20 - gap * (n - 1)) / n
        styles = {"save": (theme.TURQUOISE, theme.WHITE), "try": (theme.DARK_BLUE, theme.WHITE)}
        for i, (name, label) in enumerate(buttons):
            right = self.W - 10 - i * (w + gap)
            rect = vp.cm_rect(right - w, self.H - 40, right, self.H - 24)
            bg, fg = styles.get(name, (theme.GREY_LIGHT, theme.DARK_BLUE))
            surf.blit(self.sprites.rounded(rect.w, rect.h, rect.h // 2, bg), rect)
            self.text.blit(surf, label, "bold", vp.s(4), fg, rect.center)
            self._hits.append((rect, "button", name))
        msg, kind = self.message
        if not msg:
            msg = f"{unsaved} تغییر ذخیره نشده" if unsaved else "همه‌ی تغییرها ذخیره شده است"
        color = {"error": theme.PURPLE, "ok": theme.TURQUOISE_RAMP[0]}.get(kind, theme.TEXT_GREY)
        self.text.blit(surf, msg, "body", vp.s(3.6), color, vp.px(self.W / 2, self.H - 12))
