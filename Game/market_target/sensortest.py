"""Sensor test: see where the camera, the LiDAR, or both together find a touch or a ball hit.
A separate program from the game (close the game first: both use the camera and the LiDAR).

    .venv\\Scripts\\python sensortest.py            (or double-click sensortest.bat)
    .venv\\Scripts\\python sensortest.py --shots out  render the three tabs to PNG and quit (no sensors)

Three tabs (click them, or keys 1 2 3):
  1  camera alone   every hit / touch the camera finds, on the wall, with its cm; the camera's picture
  2  LiDAR alone    every hit / touch the LiDAR finds
  3  mixed          both sensors and the fusion: each sensor's own dot, and the one hit the game would
                    count (a ring), with which sensor's point it took and how long it waited

It runs the game's own input (market_pulse/input/manager.py) with config/settings.json, so it shows
exactly what the game will get. The crosses on the wall map are 25 cm in from the corners and the
centre: touch one and see where its dot lands.

Two parts (the buttons at the top left, or F5):
  1  sensor tests   the three tests below
  2  game settings  the game's own settings page, the same as F5 in the game (boxes, points, rush, round,
                    sound, display, camera, fusion, LiDAR ...), with its Save; and the phone page's address
                    (type the name, start the round)

At the top, "سنسور بازی": which sensor the GAME finds the hits with - LiDAR, camera or both
(input.sources in config/settings.json, saved at once; the game uses it from its next start).
Every tab has its own settings on the right (hover the mouse over one: what it does, in Persian).
Click / drag to change, the mouse wheel moves one step. They act at once; "Save" writes them to
config/settings.json (camera, fusion) and config/lidar.json (LiDAR).
Camera tab, "QR calibration" (or K): the markers (QR-like squares) fill the game's display, where the
game draws its picture, at the game's own size and place; the camera finds them and the calibration
is saved to config/camera_calibration.json, as F2 then K in the game. K or Esc cancels.
Keys: F5 part 1 / 2   1 2 3 tabs   K camera calibration   L learn the empty wall again   Space clear the dots   Esc quit
"""
from __future__ import annotations

import argparse
import copy
import logging
import os
import sys
import time
from collections import ChainMap, deque
from dataclasses import dataclass, field
from pathlib import Path

import project_python  # noqa: E402

project_python.relaunch()  # the .venv Python: pygame-ce shapes Persian

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import pygame  # noqa: E402

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from market_pulse import config  # noqa: E402
from market_pulse.input.lidar.tuning import FIELDS as LIDAR_FIELDS  # noqa: E402
from market_pulse.input.manager import InputManager, WallImpact  # noqa: E402
from market_pulse.app import canvas_size  # noqa: E402
from market_pulse.ui import overlays  # noqa: E402
from market_pulse.ui.settings import LidarValues, SettingsPage, write_changes  # noqa: E402
from market_pulse.ui.theme import Viewport  # noqa: E402
from market_pulse.ui.text import FontBook, Text  # noqa: E402
from market_pulse.web import phone_address  # noqa: E402
from balltool_panel import HELP as CAMERA_HELP  # noqa: E402
from record_session import wall_size  # noqa: E402

log = logging.getLogger("sensortest")

SIZE = (1500, 900)
TOP_H = 64  # the bar at the top: the two parts, the game's sensor, the test tabs
PARTS = (("sensors", "۱  تست سنسورها"), ("game", "۲  تنظیمات بازی (F5)"))
PAGE_HELP = "F5: part 1 / part 2     Up / Down: choose     Left / Right or the mouse: change     Tab: next page     Ctrl+S: save"
GAME_GUIDE = (
    "بخش دوم همه‌ی تنظیمات بازی است، همان صفحه‌ی F5 در بازی",
    "باکس‌ها، امتیاز، ضریب، طوفان، دور بازی، صدا، نمایش،",
    "دوربین، حساسیت، ترکیب و LiDAR.",
    "",
    "«ذخیره» (یا Ctrl+S) همه را در config/settings.json",
    "و تنظیمات LiDAR را در config/lidar.json می‌نویسد.",
    "بازی از اجرای بعدی با این مقدارها کار می‌کند.",
    "",
    "دوربین، حساسیت، ترکیب و LiDAR همین الان روی",
    "تست سنسور هم اثر دارند.",
    "",
    "کلید F5 بین بخش ۱ و بخش ۲ جابه‌جا می‌کند.",
)
BG, PANEL, LINE, TEXT, GREY = (16, 24, 34), (28, 38, 52), (60, 74, 92), (230, 236, 242), (140, 152, 168)
CAM_C, LID_C, BOTH_C, ACCENT, WARN = (255, 150, 40), (40, 210, 255), (90, 240, 120), (0, 200, 190), (255, 90, 90)
SENSOR_COLOR = {"camera": CAM_C, "lidar": LID_C}
FADE_S = 20.0

TESTS = [
    ("camera", "تست دوربین", ["camera"]),
    ("lidar", "تست LiDAR", ["lidar"]),
    ("mixed", "تست ترکیبی", ["lidar", "camera"]),
]


@dataclass
class Row:
    """One setting on the right: where it lives, its range and its help."""

    key: tuple  # ("input", "camera", "threshold") in settings.json, or ("lidar", "bg_margin") in lidar.json
    label: str
    lo: float = 0
    hi: float = 1
    step: float = 1
    options: tuple = ()  # ((value, label), ...) for a choice
    switch: bool = False
    help: tuple = ()
    unit: str = ""
    entry: bool = False  # a box to type the number in (Enter: use it and save it)
    zero: str = ""  # shown instead of 0


def cam_row(name, label, lo=0, hi=1, step=1, unit="", **kw) -> Row:
    title, lines = CAMERA_HELP.get(name, ("", []))
    return Row(("input", "camera", name), label, lo, hi, step, help=tuple(kw.pop("help", lines)), unit=unit, **kw)


def lidar_row(name, label, help_lines, **kw) -> Row:
    f = LIDAR_FIELDS[name]
    if f.kind == "choice":
        return Row(("lidar", name), label, options=tuple((o, o) for o in f.options), help=tuple(help_lines))
    if f.kind == "bool":
        return Row(("lidar", name), label, switch=True, help=tuple(help_lines))
    return Row(("lidar", name), label, f.low, f.high, f.step, help=tuple(help_lines), unit=f.unit)


FU = ("input", "fusion")
SCREEN = [  # the game's picture on the wall, measured with a tape: the camera's ball size and speed are real cm
    Row(("input", "camera", "screen_width_cm"), "عرض تصویر بازی روی دیوار (بنویسید)", 0, 2000, 1, unit="cm",
        entry=True, zero="همان دیوار",
        help=("عرض واقعی تصویر بازی روی دیوار را با متر اندازه بگیرید و بنویسید، بعد Enter.",
              "همان لحظه اعمال و در تنظیمات بازی ذخیره می‌شود.",
              "0: همان اندازه‌ی دیوار در تنظیمات.",
              "اندازه‌ی تصویر عوض شد؟ دوربین را دوباره کالیبره کنید (QR).")),
    Row(("input", "camera", "screen_height_cm"), "ارتفاع تصویر بازی روی دیوار (بنویسید)", 0, 2000, 1, unit="cm",
        entry=True, zero="به نسبت عرض",
        help=("ارتفاع واقعی تصویر بازی روی دیوار، با متر؛ بعد Enter (ذخیره می‌شود).",
              "0: از عرض و شکل تصویر حساب می‌شود.",
              "LiDAR به این نیازی ندارد: با ضربدرهای خود بازی کالیبره می‌شود.")),
]
ROWS = {
    "camera": [
        cam_row("mode", "حالت", options=(("ball", "پرتاب توپ"), ("touch", "لمس با دست")),
                help=["پرتاب توپ: برخورد توپ پرتاب‌شده به دیوار.",
                      "لمس با دست: دستی که چند لحظه روی دیوار ثابت بماند (برای آزمایش جای نقطه‌ها)."]),
        *SCREEN,
        cam_row("threshold", "حساسیت به تغییر روشنایی", 4, 60, 1),
        cam_row("ball_cm", "قطر توپ", 4, 40, 1, "cm"),
        cam_row("ball_tone", "رنگ توپ نسبت به دیوار", options=(("any", "هر رنگ"), ("light", "روشن‌تر"), ("dark", "تیره‌تر"))),
        cam_row("color", "تشخیص با رنگ", switch=True),
        cam_row("min_speed", "کمترین سرعت پرتاب", 20, 600, 10, "cm/s"),
        cam_row("turn_deg", "زاویه‌ی برگشت از دیوار", 10, 120, 5, "°"),
        cam_row("size_min", "کوچک‌ترین اندازه‌ی توپ", 0.1, 1.0, 0.05),
        cam_row("size_max", "بزرگ‌ترین اندازه‌ی توپ", 1.0, 3.0, 0.1),
        cam_row("margin_cm", "حاشیه‌ی بیرون دیوار", 0, 100, 5, "cm"),
    ],
    "lidar": [
        lidar_row("mode", "حالت", ["ball: یک برخورد برای هر پرتاب توپ (برای بازی).",
                                   "touch: هر لمس دست یک نقطه؛ برای آزمایش جای لمس‌ها و کالیبراسیون."]),
        lidar_row("bg_margin", "فاصله‌ی لازم از پس‌زمینه", ["چیزی چقدر باید جلوتر از دیوار خالی باشد تا دیده شود (میلی‌متر).",
                                                           "کمتر: حساس‌تر، ولی نویز هم برخورد می‌شود. بیشتر: نویز کمتر."]),
        lidar_row("min_width", "کوچک‌ترین جسم", ["جسم کوچک‌تر از این (گرد و غبار، نویز) دیده نمی‌شود.",
                                                 "توپ دیده نمی‌شود؟ کمش کنید. نقطه‌های الکی؟ زیادش کنید."]),
        lidar_row("max_width", "بزرگ‌ترین جسم", ["جسم بزرگ‌تر از این (بدن، صندلی) برخورد حساب نمی‌شود.",
                                                 "وقتی کسی کنار دیوار می‌ایستد برخورد ثبت می‌شود؟ کمش کنید."]),
        lidar_row("hit_delay", "مکث بعد از هر برخورد", ["بعد از هر برخورد، این مدت برخورد تازه‌ای ثبت نمی‌شود",
                                                        "(توپی که برمی‌گردد یا می‌افتد). کمتر: پرتاب‌های پشت‌سرهم سریع‌تر."]),
        lidar_row("ball_size", "قطر توپ", ["LiDAR جلوی توپ را می‌بیند؛ نقطه نصف قطر عقب‌تر، به مرکز توپ برده می‌شود.",
                                          "0: خاموش."]),
        lidar_row("confirm_scans", "لمس: تأیید بعد از", ["حالت لمس: چند دور اسکن لازم است تا لمس قطعی شود.",
                                                         "کمتر: سریع‌تر ولی نویز بیشتر."]),
        lidar_row("release_time", "لمس: رها شدن بعد از", ["حالت لمس: دستی که این مدت دیده نشود، رها شده است."]),
    ],
    "mixed": [
        *SCREEN,
        Row((*FU, "enabled"), "ترکیب دوربین و LiDAR", switch=True,
            help=("روشن: از هر پرتاب یک برخورد (مختصات LiDAR اول). خاموش: هر سنسور جدا حساب می‌شود.",)),
        Row((*FU, "window_ms"), "تأخیر: انتظار دوربین برای LiDAR", 0, 1000, 10, unit="ms",
            help=("برخوردی که اول دوربین دیده، این مدت منتظر LiDAR می‌ماند.",
                  "اگر LiDAR آمد: مختصات LiDAR. نیامد: مختصات دوربین. کمتر: سریع‌تر.")),
        Row((*FU, "match_time_ms"), "بیشترین اختلاف زمان دو سنسور", 50, 1000, 10, unit="ms",
            help=("دو تشخیص با این اختلاف زمانی می‌توانند یک پرتاب باشند.",
                  "LiDAR برخورد را 0.07 تا 0.2 ثانیه دیرتر از دوربین ثبت می‌کند؛ کمتر از 250 نگذارید.")),
        Row((*FU, "match_radius_cm"), "بیشترین فاصله‌ی دو سنسور", 10, 200, 5, unit="cm",
            help=("دو نقطه‌ی یک پرتاب از دو سنسور حداکثر این‌قدر از هم فاصله دارند.",
                  "روی نقشه، نقطه‌های نارنجی (دوربین) و آبی (LiDAR) هر پرتاب را ببینید.")),
        Row((*FU, "camera_alone"), "برخوردی که فقط دوربین دیده",
            options=(("always", "همیشه"), ("lidar_offline", "فقط بی LiDAR"), ("never", "هرگز")),
            help=("همیشه: حساب می‌شود.  فقط بی LiDAR: فقط وقتی LiDAR کار نمی‌کند",
                  "(برای دوربینی که توپ الکی می‌بیند).  هرگز: فقط با LiDAR.")),
    ],
}
GUIDE = ("موس را روی هر تنظیم ببرید تا توضیحش را ببینید.",
         "کلیک یا کشیدن: تغییر مقدار   چرخ موس: یک گام   همان لحظه اعمال می‌شود.",
         "کالیبره با QR (یا K): نشانه‌ها روی صفحه‌ی بازی می‌آیند",
         "و دوربین آن‌ها را پیدا می‌کند. K یا Esc: لغو.")


def get(raw, key):
    node = raw
    for k in key:
        node = node[k]
    return node


def put(raw, key, value):
    node = raw
    for k in key[:-1]:
        node = node[k]
    node[key[-1]] = value


@dataclass
class Dot:
    x: float
    y: float
    at: float  # time.monotonic()
    sensor: str
    final: bool = False  # a hit the game counts (a ring)
    label: str = ""


@dataclass
class Session:
    """One running test: its input manager and what it has seen."""

    test: str
    manager: InputManager | None
    raw: list = field(default_factory=list)  # (sensor, x, y) the sensors reported (before the fusion)
    dots: deque = field(default_factory=lambda: deque(maxlen=200))
    log: deque = field(default_factory=lambda: deque(maxlen=12))
    counts: dict = field(default_factory=dict)


class SensorTest:
    def __init__(self, start_sensors: bool = True):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.settings_file = ROOT / "config" / "settings.json"
        self.cfg, warnings = config.load(self.settings_file)
        for w in warnings:
            log.warning("config: %s", w)
        self.raw = self.cfg.raw()
        wall = self.raw["wall"]  # the settings page's 3:4 frame is the wall in the file, as in the game
        wall.setdefault("layout_width_cm", wall["width_cm"])
        wall.setdefault("layout_height_cm", wall["height_cm"])
        self.W, self.H = wall_size(self.cfg)
        self.raw["wall"]["width_cm"], self.raw["wall"]["height_cm"] = self.W, self.H  # as the game's fill_screen
        self.start_sensors = start_sensors
        pygame.init()
        self.screen = pygame.display.set_mode(SIZE)
        pygame.display.set_caption("Market Pulse - sensor test")
        book = FontBook(ROOT / "assets" / "fonts", {"bold": ["IRANSansX-Bold.ttf"], "body": ["IRANSansX-Medium.ttf"],
                                                    "latin": ["IRANSansX-Medium.ttf"]})
        self.text = Text(book, persian_digits=False)
        self.saved_raw = copy.deepcopy(self.raw)
        self.game_sources = list(self.raw["input"]["sources"])  # what the game uses (the tests change raw's)
        self.session: Session | None = None
        self.hover: int | None = None
        self.drag: int | None = None
        self.notice = ("", 0.0)
        self._hits: list = []  # (rect, action, arg) for the mouse
        self.lidar_tuning = None  # the LiDAR's live settings (lidar.json), kept across tabs
        self.board = None  # the camera's calibration markers on the game's display (K)
        self.edit: int | None = None  # the row whose number is being typed
        self.buf = ""
        self.board_vp: Viewport | None = None
        self.part = "sensors"  # "sensors": the three tests, "game": the game's own settings page (F5)
        self.page: SettingsPage | None = None  # made the first time part 2 opens
        self.switch("camera")

    # -- tests ------------------------------------------------------------------------------------
    def switch(self, test: str) -> None:
        self.edit = None
        if self.board is not None:
            self.stop_markers()
        self.stop()
        raw = self.raw
        raw["input"]["sources"] = list(next(s for t, _, s in TESTS if t == test))
        raw["input"]["simulation"] = False
        m = InputManager(self.cfg, to_game_time=lambda t: t, roots=(ROOT,))
        s = Session(test, m)
        feed = m.fusion.feed

        def watch(d, now):  # every detection of a sensor, before the fusion decides
            s.raw.append(d)
            return feed(d, now)

        m.fusion.feed = watch
        if m.lidar is not None:
            if self.lidar_tuning is not None:  # the values changed on the LiDAR tab stay
                for k in LIDAR_FIELDS:
                    m.lidar.tuning.set(k, getattr(self.lidar_tuning, k))
            self.lidar_tuning = m.lidar.tuning
        elif self.lidar_tuning is None:
            from market_pulse.input.lidar.wallmap import GameTuning
            lc = self.cfg.input.lidar
            t = GameTuning(str(ROOT / lc.settings_file), self.W, self.H, float(lc.wall_margin_cm), float(lc.max_range_mm))
            t.load()
            self.lidar_tuning = t
        self._bind_lidar()
        if m.camera is not None:
            m.camera.want_preview = True
            m.camera.preview_every_s = 0.0  # every frame, not 5 a second as in the game's F2
            m.camera.preview_w = 480
        if self.start_sensors:
            m.start()
        self.session = s
        self.hover = self.drag = None
        log.info("test %s: sources %s", test, raw["input"]["sources"])

    def stop(self) -> None:
        if self.session is None or self.session.manager is None:
            return
        m = self.session.manager
        m.stop()
        for src in m.sources:  # the serial port / the camera must be free for the next test
            if src._thread is not None:
                src._thread.join(timeout=4)
        self.session = None

    # -- settings ------------------------------------------------------------------------------------------
    def rows(self) -> list[Row]:
        return ROWS[self.session.test]

    def value(self, row: Row):
        if row.key[0] == "lidar":
            return getattr(self.lidar_tuning, row.key[1])
        return get(self.raw, row.key)

    def set_value(self, row: Row, value) -> None:
        if row.options:
            pass
        elif row.switch:
            value = bool(value)
        else:
            value = min(max(value, row.lo), row.hi)
            value = round(round((value - row.lo) / row.step) * row.step + row.lo, 4)
            old = self.value(row)
            if isinstance(old, int) and not isinstance(old, bool) and float(row.step).is_integer():
                value = int(round(value))
        if value == self.value(row):
            return
        m = self.session.manager
        if row.key[0] == "lidar":
            self.lidar_tuning.set(row.key[1], value)
            if m.lidar is not None:
                m.lidar.tuning_changed()
        else:
            put(self.raw, row.key, value)
            if m.camera is not None and row.key[:2] == ("input", "camera"):
                m.camera.settings_changed()
            m.settings_changed()

    def step_row(self, i: int, steps: int) -> None:
        row = self.rows()[i]
        v = self.value(row)
        if row.options:
            vals = [o for o, _ in row.options]
            self.set_value(row, vals[(vals.index(v) + steps) % len(vals)] if v in vals else vals[0])
        elif row.switch:
            self.set_value(row, not v)
        else:
            self.set_value(row, v + steps * row.step)

    def save(self) -> None:
        keys = [r.key for rows in ROWS.values() for r in rows
                if r.key[0] != "lidar" and get(self.raw, r.key) != get(self.saved_raw, r.key)]
        try:
            if keys:
                write_changes(self.settings_file, None, self.raw, keys)
                for k in keys:
                    put(self.saved_raw, k, copy.deepcopy(get(self.raw, k)))
                    if self.page is not None and k in self.page.saved:  # part 2 sees them saved too
                        self.page.saved[k] = copy.deepcopy(get(self.raw, k))
            if self.lidar_tuning is not None:
                self.lidar_tuning.save()
            self.notice = (f"Saved: {len(keys)} game setting(s) -> settings.json, LiDAR -> lidar.json", time.monotonic() + 5)
            log.info("saved %s and the LiDAR settings", [".".join(k) for k in keys])
        except (OSError, ValueError) as exc:
            self.notice = (f"NOT saved: {exc}", time.monotonic() + 8)

    def commit(self) -> None:
        """The typed number: used at once and saved into config/settings.json (the game reads it)."""
        i, self.edit = self.edit, None
        if i is None or i >= len(self.rows()):
            return
        row = self.rows()[i]
        try:
            value = float(self.buf) if self.buf.strip(".") else 0.0
        except ValueError:
            self.notice = (f"Not a number: {self.buf}", time.monotonic() + 5)
            return
        value = int(round(min(max(value, row.lo), row.hi)))
        self.set_value(row, value)
        self.save()
        self.notice = (f"{row.key[-1]} = {value} cm, saved to settings.json (the game uses it)", time.monotonic() + 6)

    def game_sensor(self) -> str:
        has_l, has_c = "lidar" in self.game_sources, "camera" in self.game_sources
        return "both" if has_l and has_c else "lidar" if has_l else "camera" if has_c else "none"

    def set_game_sensor(self, key: str) -> None:
        """The game finds the hits with this sensor (input.sources), saved into settings.json at once."""
        if key == self.game_sensor():
            return
        chosen = {"lidar": ["lidar"], "camera": ["camera"], "both": ["lidar", "camera"]}[key]
        sources = [s for s in self.game_sources if s not in ("lidar", "camera")] + chosen  # udp_json etc. stay
        try:
            write_changes(self.settings_file, None, {"input": {"sources": sources}}, [("input", "sources")])
        except (OSError, ValueError) as exc:
            self.notice = (f"NOT saved: {exc}", time.monotonic() + 8)
            return
        self.game_sources = sources
        names = {"lidar": "LiDAR", "camera": "camera", "both": "LiDAR + camera (fusion)"}
        self.notice = (f"The game now uses {names[key]}: input.sources = {sources} (from its next start)",
                       time.monotonic() + 8)
        log.info("game sensors: %s", sources)

    # -- part 2: the game's settings page (the same as F5 in the game) ---------------------------------------
    def game_page(self) -> SettingsPage:
        if self.page is None:
            from market_pulse.ui.assets import AssetLibrary
            from market_pulse.ui.draw import Sprites
            assets, _ = AssetLibrary.load(ROOT / "assets")
            text = Text(FontBook(ROOT / "assets" / "fonts", self.cfg.fonts.raw()), self.cfg.ui.digits == "fa")
            page = SettingsPage(self.cfg, text, Sprites(assets), self.settings_file, None, self._game_changed,
                                self.lidar_tuning)
            page.standalone = True  # its own Save; "leave without saving" goes back to part 1
            page.help_text = PAGE_HELP
            url = phone_address(self.cfg.web)
            page.phone = (url, "later" if url else "off")
            d = self.cfg.display
            sizes = pygame.display.get_desktop_sizes() or [SIZE]
            idx = min(max(int(d.display_index), 0), len(sizes) - 1)
            page.game_size = canvas_size(d.resolution, self.W / self.H) or sizes[idx]
            self.page = page
        return self.page

    def _bind_lidar(self) -> None:
        """Part 2's LiDAR pages change the LiDAR the test runs now (a new test makes a new one)."""
        p = self.page
        if p is not None and self.lidar_tuning is not None and p.lidar is not self.lidar_tuning:
            p.lidar = self.lidar_tuning
            p.root = ChainMap({"lidar": LidarValues(self.lidar_tuning)}, p.raw)

    def _game_changed(self) -> None:
        """A value changed in part 2: the running test uses it at once, as the game does."""
        m = self.session.manager if self.session else None
        if m is None:
            return
        if m.lidar is not None:
            m.lidar.tuning_changed()
        if m.camera is not None:
            m.camera.settings_changed()
        m.settings_changed()

    def set_part(self, part: str) -> None:
        if part == self.part:
            return
        self.edit = self.drag = self.hover = None
        if part == "game":
            self.game_page().show()
        elif self.page is not None:
            self.page.hide()
        self.part = part

    def _page_done(self, done) -> None:
        """What part 2's page answered: "saved" stays, "close" (Esc, leave without saving) goes to part 1."""
        page = self.page
        if done == "saved":
            page.message = ("ذخیره شد در config/settings.json؛ بازی از اجرای بعدی با این مقدارها کار می‌کند", "ok")
            for rows in ROWS.values():  # part 1 sees them saved too
                for r in rows:
                    if r.key[0] != "lidar" and r.key in page.saved:  # the LiDAR's are in lidar.json
                        put(self.saved_raw, r.key, copy.deepcopy(page.saved[r.key]))
            self.notice = ("Game settings saved to config/settings.json (the game uses them from its next start)",
                           time.monotonic() + 8)
        elif done is not None:
            unsaved = len(page.changed())
            self.set_part("sensors")
            if unsaved:
                self.notice = (f"{unsaved} game setting(s) changed for now but not saved", time.monotonic() + 8)

    def learn_wall(self) -> None:
        m = self.session.manager
        if m.lidar is not None:
            m.lidar.rescan()
        if m.camera is not None:
            m.camera.rescan()
        self.notice = ("Learning the empty wall again: keep it clear", time.monotonic() + 5)

    # -- camera calibration with markers (K) -------------------------------------------------------------------
    def start_markers(self) -> None:
        cam = self.session.manager.camera
        if cam is None:
            self.notice = ("The camera tab: calibration needs the camera (tab 1 or 3)", time.monotonic() + 5)
            return
        d = self.cfg.display
        sizes = pygame.display.get_desktop_sizes() or [SIZE]
        idx = min(max(int(d.display_index), 0), len(sizes) - 1)
        size = canvas_size(d.resolution, self.W / self.H) or sizes[idx]
        try:  # the game's display, the game's canvas: the markers land where the game draws its picture
            self.screen = pygame.display.set_mode(size, pygame.FULLSCREEN | pygame.SCALED, display=idx)
        except pygame.error as exc:
            log.warning("fullscreen failed (%s): the markers in this window", exc)
            self.screen = pygame.display.set_mode(size)
        self.board_vp = Viewport(*self.screen.get_size(), self.W, self.H, float(d.scale))
        self.board = cam.start_markers()
        log.info("camera calibration: %d markers on display %d (%dx%d)", len(self.board), idx, *size)

    def stop_markers(self, message: str = "") -> None:
        cam = self.session.manager.camera if self.session else None
        if cam is not None:
            cam.stop_markers()
        self.board = self.board_vp = None
        self.screen = pygame.display.set_mode(SIZE)
        if message:
            log.info("camera calibration: %s", message)
            self.notice = (message, time.monotonic() + 10)

    def draw_board(self) -> None:
        cam = self.session.manager.camera
        self.screen.fill((0, 0, 0))
        overlays._draw_board(self.screen, self.board_vp, self.text, self.board, cam, self.W, self.H)
        if cam is not None and cam.marker_result is not None:
            ok, msg = cam.marker_result
            cam.marker_result = None
            self.stop_markers(msg if ok else f"Camera calibration failed: {msg}")

    # -- per frame ------------------------------------------------------------------------------------------
    def poll(self) -> None:
        s = self.session
        now = time.monotonic()
        for ev in s.manager.poll():
            if not isinstance(ev, WallImpact):
                continue
            sensors = ev.sensors or (ev.source,)
            if s.test == "mixed":
                kind = "both" if len(sensors) == 2 else ev.source
                s.counts[kind] = s.counts.get(kind, 0) + 1
                label = {"both": "both", "lidar": "LiDAR", "camera": "camera"}.get(kind, kind)
                s.dots.append(Dot(ev.x, ev.y, now, ev.source, True, label))
                waited = s.manager.fusion.stats.wait_max  # shown in the log line below
                s.log.appendleft(f"{time.strftime('%H:%M:%S')}  {label:6s} -> {ev.source:6s} ({ev.x:4.0f}, {ev.y:4.0f}) cm")
            else:
                s.counts[ev.source] = s.counts.get(ev.source, 0) + 1
                s.dots.append(Dot(ev.x, ev.y, now, ev.source, True))
                s.log.appendleft(f"{time.strftime('%H:%M:%S')}  {ev.source:6s} ({ev.x:4.0f}, {ev.y:4.0f}) cm")
        if s.test == "mixed":
            for d in s.raw:
                s.dots.append(Dot(d.x, d.y, now, d.sensor))
        s.raw.clear()

    # -- drawing ---------------------------------------------------------------------------------------------
    def blit_fa(self, s: str, px: int, color, right: int, y: int, role="body") -> int:
        img = self.text.render(s, role, px, color, localize=False)
        self.screen.blit(img, (right - img.get_width(), y))
        return img.get_height()

    def blit_en(self, s: str, px: int, color, x: int, y: int) -> int:
        img = self.text.render(s, "latin", px, color, localize=False)
        self.screen.blit(img, (x, y))
        return img.get_height()

    def draw(self) -> None:
        sc = self.screen
        sc.fill(BG)
        self._hits = []
        if self.part == "game":
            self._draw_game_part()
            self._draw_tabs()
            return
        self._draw_tabs()
        wall = self._draw_wall(pygame.Rect(20, 70, 560, 790))
        self._draw_status(pygame.Rect(600, 70, 420, 810))
        self._draw_settings(pygame.Rect(1040, 70, 440, 810))
        msg, until = self.notice
        if msg and time.monotonic() < until:
            self.blit_en(msg[:90], 14, ACCENT, 600, 882)
        del wall

    def _draw_game_part(self) -> None:
        """Part 2: the game's settings page in the middle, a guide and the phone page on the left."""
        page = self.game_page()
        area = pygame.Rect(0, TOP_H, SIZE[0], SIZE[1] - TOP_H)
        vp = Viewport(area.w, area.h, page.W, page.H)
        page.draw(self.screen.subsurface(area), vp)
        right = vp.rect.left - 24
        y = TOP_H + 24
        y += self.blit_fa("تنظیمات بازی", 22, ACCENT, right, y, "bold") + 12
        for line in GAME_GUIDE:
            y += (self.blit_fa(line, 15, TEXT, right, y) if line else 10) + 4
        url, state = page.phone
        y += 24
        y += self.blit_fa("صفحه‌ی گوشی: نوشتن نام و شروع بازی", 17, ACCENT, right, y, "bold") + 6
        if url:
            img = self.text.render(url, "latin", 22, (255, 255, 255), localize=False)
            self.screen.blit(img, (right - img.get_width(), y))
            y += img.get_height() + 6
            y += self.blit_fa("با گوشیِ وصل به همین شبکه (Wi-Fi) باز کنید.", 14, GREY, right, y) + 2
            y += self.blit_fa("این نشانی وقتی بازی باز است کار می‌کند.", 14, GREY, right, y) + 2
        else:
            y += self.blit_fa("خاموش است (web.enabled در settings.json)", 15, WARN, right, y) + 2
        msg, until = self.notice
        if msg and time.monotonic() < until:
            self.blit_en(msg[:60], 13, ACCENT, 16, SIZE[1] - 28)

    def _draw_tabs(self) -> None:
        pygame.draw.rect(self.screen, BG, (0, 0, SIZE[0], TOP_H))
        px = 20  # the two parts, on the left
        for key, label in PARTS:
            img = self.text.render(label, "bold", 17, TEXT, localize=False)
            r = pygame.Rect(px, 12, img.get_width() + 36, 40)
            on = self.part == key
            pygame.draw.rect(self.screen, (225, 165, 40) if on else PANEL, r, border_radius=10)
            if not on:
                pygame.draw.rect(self.screen, LINE, r, 2, border_radius=10)
            self.screen.blit(img, img.get_rect(center=r.center))
            self._hits.append((r, "part", key))
            px = r.right + 10
        x = SIZE[0] - 20
        for i, (key, title, _) in enumerate(TESTS if self.part == "sensors" else ()):
            img = self.text.render(f"{title}  {i + 1}", "bold", 18, TEXT, localize=False)
            r = pygame.Rect(0, 0, img.get_width() + 40, 40)
            r.topright = (x, 14)
            active = self.session.test == key
            pygame.draw.rect(self.screen, ACCENT if active else PANEL, r, border_radius=20)
            self.screen.blit(img, img.get_rect(center=r.center))
            self._hits.append((r, "tab", key))
            x = r.left - 12
        # which sensor the game itself uses (input.sources), a switch of three
        now = self.game_sensor()
        bx = x - 30
        for key, label in (("both", "هر دو"), ("camera", "دوربین"), ("lidar", "LiDAR")):
            img = self.text.render(label, "bold", 15, TEXT, localize=False)
            b = pygame.Rect(0, 0, img.get_width() + 36, 32)
            b.topright = (bx, 18)
            on = now == key
            pygame.draw.rect(self.screen, BOTH_C if on else PANEL, b, border_radius=16)
            if on:
                pygame.draw.circle(self.screen, BG, (b.x + 11, b.centery), 4)
            self.screen.blit(img, img.get_rect(center=(b.centerx + (6 if on else 0), b.centery)))
            self._hits.append((b, "gamesrc", key))
            bx = b.left - 6
        self.blit_fa("سنسور بازی:", 15, GREY, bx - 4, 23, "bold")

    def _wall_rect(self, area: pygame.Rect) -> tuple[pygame.Rect, float]:
        scale = min(area.w / self.W, area.h / self.H)
        r = pygame.Rect(0, 0, int(self.W * scale), int(self.H * scale))
        r.center = area.center
        return r, scale

    def _draw_wall(self, area: pygame.Rect) -> pygame.Rect:
        sc = self.screen
        r, k = self._wall_rect(area)
        pygame.draw.rect(sc, PANEL, r)
        px = lambda x, y: (int(r.x + x * k), int(r.y + y * k))  # noqa: E731
        for x in range(0, int(self.W) + 1, 50):
            pygame.draw.line(sc, LINE, px(x, 0), px(x, self.H))
            self.blit_en(str(x), 11, GREY, px(x, 0)[0] + 2, r.y + 2)
        for y in range(0, int(self.H) + 1, 50):
            pygame.draw.line(sc, LINE, px(0, y), px(self.W, y))
            self.blit_en(str(y), 11, GREY, r.x + 2, px(0, y)[1] + 2)
        for cx, cy in ((25, 25), (self.W - 25, 25), (25, self.H - 25), (self.W - 25, self.H - 25), (self.W / 2, self.H / 2)):
            c = px(cx, cy)
            pygame.draw.line(sc, WARN, (c[0] - 12, c[1]), (c[0] + 12, c[1]), 2)
            pygame.draw.line(sc, WARN, (c[0], c[1] - 12), (c[0], c[1] + 12), 2)
        pygame.draw.rect(sc, GREY, r, 2)
        now = time.monotonic()
        dots = list(self.session.dots)
        last_final = None
        for d in dots:
            age = now - d.at
            if age > FADE_S:
                continue
            fade = max(0.25, 1 - age / FADE_S)
            base = BOTH_C if d.label == "both" else SENSOR_COLOR.get(d.sensor, TEXT)
            col = tuple(int(c * fade + 30 * (1 - fade)) for c in base)
            c = px(d.x, d.y)
            if d.final and self.session.test == "mixed":
                pygame.draw.circle(sc, col, c, 13, 3)
            else:
                pygame.draw.circle(sc, col, c, 7)
            if d.final:
                last_final = d
        if last_final is not None:
            c = px(last_final.x, last_final.y)
            pygame.draw.circle(sc, (255, 255, 255), c, 18, 2)
            tag = f"({last_final.x:.0f}, {last_final.y:.0f}) cm" + (f"  {last_final.label}" if last_final.label else "")
            self.blit_en(tag, 15, (255, 255, 255), min(c[0] + 22, r.right - 150), c[1] - 10)
        self.blit_en(f"wall {self.W:.0f} x {self.H:.0f} cm   crosses: 25 cm from the corners, and the centre",
                     13, GREY, r.x, r.bottom + 6)
        return r

    def _draw_status(self, area: pygame.Rect) -> None:
        sc = self.screen
        pygame.draw.rect(sc, PANEL, area, border_radius=10)
        x, y = area.x + 14, area.y + 12
        m, s = self.session.manager, self.session
        for st in m.status():
            if st["name"] not in ("camera", "lidar"):
                continue
            col = SENSOR_COLOR[st["name"]]
            age = st["last_packet_age"]
            alive = st.get("alive", False)
            pygame.draw.circle(sc, BOTH_C if alive else WARN, (x + 6, y + 11), 6)
            head = f"{st['name'].upper()}  {st['state']}" + (f"  {st['fps']:.0f} fps" if "fps" in st else "")
            y += self.blit_en(head, 17, col, x + 18, y) + 2
            detail = st.get("detail") or st.get("error") or ""
            if detail:
                y += self.blit_en(detail[:60], 13, GREY, x + 18, y)
            y += self.blit_en(f"data {'never' if age is None else f'{age:.1f} s ago'}   hits {st['events']}", 13, GREY, x + 18, y) + 10
        if not self.start_sensors:
            y += self.blit_en("(sensors not started)", 13, GREY, x, y) + 6
        if s.test == "mixed":
            st = m.fusion.stats.as_dict()
            y += self.blit_en("FUSION (the hits the game counts)", 16, BOTH_C, x, y) + 2
            for line in (f"both {st['fused']}   LiDAR only {st['lidar_only']}   camera only {st['camera_only']}",
                         f"duplicates absorbed {st['duplicates']} (late {st['late']})   refused {st['refused']}",
                         f"wait avg {st['wait_avg_ms']} ms   max {st['wait_max_ms']} ms   waiting now {m.fusion.pending}",
                         "dots: orange camera, blue LiDAR   rings: counted (green = both)"):
                y += self.blit_en(line, 13, TEXT, x, y)
            y += 10
        else:
            n = sum(s.counts.values())
            y += self.blit_en(f"hits / touches: {n}", 16, TEXT, x, y) + 8
        cam = m.camera
        if cam is not None and cam.preview is not None:
            rgb = cam.preview
            img = pygame.image.frombuffer(rgb.tobytes(), (rgb.shape[1], rgb.shape[0]), "RGB")
            w = area.w - 28
            img = pygame.transform.smoothscale(img, (w, int(rgb.shape[0] * w / rgb.shape[1])))
            sc.blit(img, (x, y))
            y += img.get_height() + 2
            y += self.blit_en("camera picture: green = the calibrated wall, red = its last hits", 12, GREY, x, y) + 8
        y += self.blit_en("last hits", 15, TEXT, x, y) + 2
        for line in s.log:
            if y > area.bottom - 22:
                break
            y += self.blit_en(line, 13, GREY, x, y)

    def _draw_settings(self, area: pygame.Rect) -> None:
        sc = self.screen
        pygame.draw.rect(sc, PANEL, area, border_radius=10)
        right = area.right - 14
        y = area.y + 10
        y += self.blit_fa("تنظیمات این تست", 18, ACCENT, right, y, "bold") + 8
        rows = self.rows()
        for i, row in enumerate(rows):
            rr = pygame.Rect(area.x + 6, y - 2, area.w - 12, 46)
            if i == self.hover:
                pygame.draw.rect(sc, LINE, rr, border_radius=6)
            self.blit_fa(row.label, 15, TEXT, right, y)
            v = self.value(row)
            ty = y + 28
            if row.entry:
                editing = self.edit == i
                shown = (self.buf + ("|" if int(time.monotonic() * 2) % 2 else " ")) if editing else                     (row.zero if not v else f"{v:g} {row.unit}")
                img = self.text.render(shown, "body", 15, (255, 220, 90), localize=False)
                b = pygame.Rect(area.x + 14, ty - 14, 170, 26)  # left, under nothing: the label is on the right
                pygame.draw.rect(sc, BG, b, border_radius=6)
                pygame.draw.rect(sc, ACCENT if editing else LINE, b, 2, border_radius=6)
                sc.blit(img, img.get_rect(center=b.center))
                if editing:
                    self.blit_fa("Enter: ذخیره   Esc: لغو", 12, ACCENT, right, ty)
                self._hits.append((b, "entry", i))
            elif row.options or row.switch:
                choices = row.options or ((False, "خاموش"), (True, "روشن"))
                bx = right
                for val, lab in choices:
                    img = self.text.render(lab, "body", 13, TEXT, localize=False)
                    b = pygame.Rect(0, 0, img.get_width() + 18, 22)
                    b.topright = (bx, ty - 10)
                    pygame.draw.rect(sc, ACCENT if v == val else BG, b, border_radius=11)
                    sc.blit(img, img.get_rect(center=b.center))
                    self._hits.append((b, "choice", (i, val)))
                    bx = b.left - 6
            else:
                x0, x1 = area.x + 90, right
                pygame.draw.line(sc, BG, (x0, ty), (x1, ty), 6)
                frac = (v - row.lo) / (row.hi - row.lo) if row.hi > row.lo else 0
                kx = int(x0 + max(0, min(1, frac)) * (x1 - x0))
                pygame.draw.line(sc, ACCENT, (x0, ty), (kx, ty), 6)
                pygame.draw.circle(sc, (255, 255, 255), (kx, ty), 8)
                shown = f"{v:g}" + (f" {row.unit}" if row.unit else "")
                self.blit_en(shown, 14, (255, 220, 90), area.x + 14, ty - 10)
                self._hits.append((pygame.Rect(x0 - 8, ty - 12, x1 - x0 + 16, 24), "track", (i, x0, x1)))
            self._hits.append((rr, "row", i))
            y += 50
        # buttons
        y += 4
        bx = right
        buttons = [("save", "ذخیره"), ("learn", "یادگیری دیوار خالی"), ("clear", "پاک کردن نقطه‌ها")]
        if self.session.manager.camera is not None:
            buttons.insert(1, ("markers", "کالیبره با QR"))
        for action, label in buttons:
            img = self.text.render(label, "bold", 14, TEXT, localize=False)
            b = pygame.Rect(0, 0, img.get_width() + 24, 32)
            b.topright = (bx, y)
            pygame.draw.rect(sc, ACCENT if action == "save" else LINE, b, border_radius=16)
            sc.blit(img, img.get_rect(center=b.center))
            self._hits.append((b, action, None))
            bx = b.left - 8
        # help for the setting under the mouse
        box = pygame.Rect(area.x + 6, area.bottom - 120, area.w - 12, 112)
        pygame.draw.rect(sc, BG, box, border_radius=8)
        hy = box.y + 8
        if self.hover is not None and self.hover < len(rows):
            row = rows[self.hover]
            hy += self.blit_fa(f"{row.label}   ({row.key[-1]})", 15, ACCENT, box.right - 10, hy, "bold") + 4
            lines = row.help
        else:
            lines = GUIDE
        for line in lines:
            hy += self.blit_fa(line, 13, TEXT, box.right - 10, hy) + 2

    # -- input --------------------------------------------------------------------------------------------
    def _at(self, pos, kinds):
        for rect, action, arg in reversed(self._hits):
            if action in kinds and rect.collidepoint(pos):
                return action, arg
        return None, None

    def _slide(self, arg, mx) -> None:
        i, x0, x1 = arg
        row = self.rows()[i]
        frac = min(max((mx - x0) / (x1 - x0), 0.0), 1.0)
        self.set_value(row, row.lo + frac * (row.hi - row.lo))

    def handle(self, event) -> bool:
        if event.type == pygame.QUIT:
            return False
        if self.board is None and event.type == pygame.KEYDOWN and event.key == pygame.K_F5:
            if not getattr(event, "repeat", False):
                if self.edit is not None:
                    self.commit()
                self.set_part("game" if self.part == "sensors" else "sensors")
            return True
        if self.board is None and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and event.pos[1] < TOP_H:
            action, arg = self._at(event.pos, ("part", "gamesrc", "tab"))
            if action == "part":
                if self.edit is not None:
                    self.commit()
                self.set_part(arg)
                return True
            if self.part == "game":
                if action == "gamesrc":
                    self.set_game_sensor(arg)
                return True
        if self.part == "game":  # part 2: the game's settings page has the keys and the mouse
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION,
                              pygame.MOUSEWHEEL):
                if hasattr(event, "pos"):
                    x, y = event.pos
                    event = pygame.event.Event(event.type, {**event.dict, "pos": (x, y - TOP_H)})
                self._page_done(self.page.handle(event))
            return True
        if self.board is not None:  # only K / Esc (cancel) while the markers are up
            if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_k) and not getattr(event, "repeat", False):
                self.stop_markers("Camera calibration cancelled")
            return True
        if self.edit is not None and event.type == pygame.KEYDOWN:  # typing a number
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.commit()
            elif event.key == pygame.K_ESCAPE:
                self.edit = None
            elif event.key == pygame.K_BACKSPACE:
                self.buf = self.buf[:-1]
            elif event.unicode and event.unicode in "0123456789.۰۱۲۳۴۵۶۷۸۹" and len(self.buf) < 6:
                self.buf += str("0123456789"["۰۱۲۳۴۵۶۷۸۹".index(event.unicode)]) if event.unicode in "۰۱۲۳۴۵۶۷۸۹" else event.unicode
            return True
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                return False
            if event.key == pygame.K_k and not getattr(event, "repeat", False):
                self.start_markers()
                return True
            if event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_KP1, pygame.K_KP2, pygame.K_KP3):
                n = {pygame.K_1: 0, pygame.K_2: 1, pygame.K_3: 2, pygame.K_KP1: 0, pygame.K_KP2: 1, pygame.K_KP3: 2}[event.key]
                if TESTS[n][0] != self.session.test:
                    self.switch(TESTS[n][0])
            elif event.key == pygame.K_l:
                self.learn_wall()
            elif event.key == pygame.K_SPACE:
                self.session.dots.clear()
            elif event.key == pygame.K_s and event.mod & pygame.KMOD_CTRL:
                self.save()
        elif event.type == pygame.MOUSEMOTION:
            action, arg = self._at(event.pos, ("row",))
            self.hover = arg
            if self.drag is not None and event.buttons[0]:
                self._slide(self.drag, event.pos[0])
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            action, arg = self._at(event.pos, ("tab", "choice", "track", "save", "learn", "clear", "markers", "entry",
                                                 "gamesrc"))
            if self.edit is not None and not (action == "entry" and arg == self.edit):
                self.commit()  # a click elsewhere keeps what was typed
            if action == "gamesrc":
                self.set_game_sensor(arg)
            elif action == "entry":
                if self.edit != arg:
                    self.edit = arg
                    v = self.value(self.rows()[arg])
                    self.buf = f"{v:g}" if v else ""
            elif action == "tab" and arg != self.session.test:
                self.switch(arg)
            elif action == "choice":
                i, val = arg
                self.set_value(self.rows()[i], val)
            elif action == "track":
                self.drag = arg
                self._slide(arg, event.pos[0])
            elif action == "save":
                self.save()
            elif action == "markers":
                self.start_markers()
            elif action == "learn":
                self.learn_wall()
            elif action == "clear":
                self.session.dots.clear()
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.drag = None
        elif event.type == pygame.MOUSEWHEEL and self.hover is not None:
            self.step_row(self.hover, 1 if event.y > 0 else -1)
        return True

    def run(self, exit_after: float | None = None) -> int:
        clock = pygame.time.Clock()
        running = True
        end = None if exit_after is None else time.monotonic() + exit_after
        try:
            while running:
                for event in pygame.event.get():
                    running = self.handle(event) and running
                self.poll()
                if self.board is not None:
                    self.draw_board()
                else:
                    self.draw()
                pygame.display.flip()
                clock.tick(60)
                if end is not None and time.monotonic() > end:
                    break
        finally:
            self.stop()
            pygame.quit()
        return 0

    def shots(self, folder: Path) -> list[Path]:
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        for test, _, _ in TESTS:
            self.switch(test)
            now = time.monotonic()
            demo = [("lidar", 25, 25), ("camera", 32, 40), ("lidar", 150, 240), ("camera", 160, 228)]
            for sensor, x, y in demo:
                if test == "mixed" or test == sensor:
                    self.session.dots.append(Dot(x, y, now, sensor, test != "mixed"))
            if test == "mixed":
                self.session.dots.append(Dot(25, 25, now, "lidar", True, "both"))
            self.hover = 1
            self.draw()
            p = folder / f"sensortest_{test}.png"
            pygame.image.save(self.screen, str(p))
            out.append(p)
        self.set_part("game")  # part 2: the game's settings page, a few of its tabs
        for tab in (0, 4, 9, len(self.page.pages) - 1):
            self.page.select_tab(tab)
            self.draw()
            p = folder / f"sensortest_game_{tab}.png"
            pygame.image.save(self.screen, str(p))
            out.append(p)
        self.stop()
        pygame.quit()
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shots", type=Path, help="render the three tabs to PNG in this folder and quit (no sensors)")
    ap.add_argument("--exit-after", type=float, help="quit after this many seconds (a smoke test)")
    ap.add_argument("--test", choices=[t for t, _, _ in TESTS], default="camera", help="the tab to open first")
    args = ap.parse_args()
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
    if args.shots:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        app = SensorTest(start_sensors=False)
        for p in app.shots(args.shots):
            print(p)
        return 0
    app = SensorTest()
    if args.test != "camera":
        app.switch(args.test)
    return app.run(args.exit_after)


if __name__ == "__main__":
    sys.exit(main())
