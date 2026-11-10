"""balltool.py's settings window: one slider per detector setting, drawn by hand so that hovering the
mouse over a slider shows what it does, in Persian (OpenCV's own trackbars can do neither).

Mouse: click or drag on a slider to set it, the wheel moves it one step, hovering shows its help.
The Persian text is shaped by the game's own text engine (pygame-ce + HarfBuzz, assets/fonts).
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

from market_pulse.ui.text import FontBook, Text  # noqa: E402

TONES = ["any", "light", "dark"]
# (setting, low, high, steps per unit, needs the empty wall learnt again)
SLIDERS = [
    ("threshold", 4, 60, 1, False),       # grey change that counts as new
    ("color", 0, 1, 1, True),             # colour + brightness
    ("ball_tone", 0, 2, 1, False),        # 0 any, 1 light, 2 dark
    ("ball_cm", 5, 40, 1, True),          # ball diameter
    ("size_min", 0.1, 1.0, 100, False),   # x a ball's width there
    ("size_max", 1.0, 3.0, 100, False),
    ("min_speed", 20, 600, 1, False),     # cm/s
    ("turn_deg", 10, 120, 1, False),
    ("bend_deg", 10, 90, 1, False),       # 90: off
    ("strong_deg", 30, 180, 1, False),    # a softer turn/bend waits for a sharper one...
    ("wait_frames", 0, 6, 1, False),      # ...this many frames (0: never)
    ("slow_ratio", 0, 1, 100, False),
    ("depth_rise", 0, 0.5, 100, False),
    ("min_track", 3, 8, 1, False),
    ("margin_cm", 0, 100, 1, True),
    ("floor_margin_cm", 0, 100, 1, True),
    ("max_region", 2, 40, 1, False),      # x a ball: bigger new regions are a person
    ("edge_reject", 0, 1, 1, False),
    ("max_fg_share", 0.05, 0.8, 100, False),
    ("learn", 0.01, 0.3, 100, False),
]

# Shown while the mouse is over a slider: its Persian name, then what it does and which way to move it.
HELP: dict[str, tuple[str, list[str]]] = {
    "threshold": ("حساسیت به تغییر روشنایی", [
        "یک پیکسل چقدر باید با دیوار خالی فرق کند تا «تازه» حساب شود.",
        "کمتر: توپ کم‌رنگ هم دیده می‌شود، ولی نویز و سایه هم.",
        "بیشتر: نویز کمتر، ولی توپ کم‌رنگ گم می‌شود. اول این را تنظیم کنید."]),
    "color": ("تشخیص با رنگ (0 خاموش، 1 روشن)", [
        "روشن: رنگ و روشنایی با هم سنجیده می‌شوند؛ توپی که هم‌روشنایی دیوار",
        "است ولی رنگش فرق دارد هم دیده می‌شود. کمی پردازش بیشتر می‌خواهد.",
        "با تغییر آن، دیوار خالی دوباره یاد گرفته می‌شود."]),
    "ball_tone": ("رنگ توپ نسبت به دیوار", [
        "0: هر رنگ   1: روشن‌تر از دیوار   2: تیره‌تر از دیوار.",
        "توپ روشن است؟ 1 را بگذارید تا آستین و لباس تیره توپ حساب نشود."]),
    "ball_cm": ("قطر توپ (سانتی‌متر)", [
        "اندازه‌ی واقعی توپ را بدهید؛ اندازه‌ی مورد انتظار توپ در تصویر از آن",
        "حساب می‌شود. اگر اشتباه باشد، توپ رد می‌شود (جعبه‌ی نارنجی یا قرمز)."]),
    "size_min": ("کوچک‌ترین اندازه‌ی توپ هنگام برخورد", [
        "نسبت به اندازه‌ی واقعی (1.00). توپ تیره یا تار کوچک‌تر دیده می‌شود.",
        "اگر توپ جعبه‌ی نارنجی با اندازه‌ی کم دارد، این را پایین بیاورید."]),
    "size_max": ("بزرگ‌ترین اندازه‌ی توپ هنگام برخورد", [
        "اگر دست یا سر به‌جای توپ برخورد حساب می‌شود، کمش کنید.",
        "اگر توپ نزدیک دوربین رد می‌شود (big)، زیادش کنید."]),
    "min_speed": ("کمترین سرعت پرتاب (سانتی‌متر بر ثانیه)", [
        "حرکت آرام‌تر از این (دست، بدن، راه رفتن) پرتاب نیست.",
        "پرتاب‌های آرام دیده نمی‌شوند؟ کمش کنید. حرکت آدم‌ها برخورد می‌شود؟ زیادش کنید."]),
    "turn_deg": ("زاویه‌ی برگشت از دیوار", [
        "وقتی مسیر توپ این‌قدر ناگهانی می‌چرخد (خوردن و برگشتن)، برخورد است.",
        "کمتر: برخوردهای بیشتری دیده می‌شوند، خطا هم بیشتر. بیشتر: فقط برگشت تند."]),
    "bend_deg": ("خم مسیر (پرتاب از بالا)", [
        "توپی که از بالا به دیوار می‌خورد مسیرش فقط کمی خم می‌شود.",
        "خط‌های قبل و بعد که با این زاویه به هم برسند، برخوردند. 90 یعنی خاموش."]),
    "strong_deg": ("برگشت قوی", [
        "چرخش تندتر از این، همان لحظه برخورد است.",
        "چرخش نرم‌تر چند فریم صبر می‌کند تا شاید چرخش تندتری (خود دیوار) بیاید."]),
    "wait_frames": ("صبر برای چرخش تندتر (فریم)", [
        "برای چرخش نرم، چند فریم صبر شود. 0: صبر نکن؛ سریع‌تر،",
        "ولی توپی که در هوا کمی خم می‌شود ممکن است زودتر از دیوار برخورد حساب شود."]),
    "slow_ratio": ("کند شدن ناگهانی", [
        "اگر سرعت توپ یک‌باره به این نسبت سرعت قبلی برسد (مثلاً 0.35)،",
        "نشانه‌ی خوردن به دیوار است. 0: خاموش."]),
    "depth_rise": ("نشانه‌ی عمق", [
        "توپ هنگام برخورد از دوربین دورترین است و در تصویر کوچک‌ترین.",
        "این مقدار: چقدر باید دور شده باشد. کمتر: پرتاب بیشتر، خطا بیشتر."]),
    "min_track": ("کمترین نقطه‌های مسیر", [
        "چند نقطه از مسیر توپ لازم است تا برخورد اعلام شود.",
        "کمتر: پرتاب خیلی تند (با فریم کم) هم دیده می‌شود. بیشتر: مطمئن‌تر."]),
    "margin_cm": ("حاشیه‌ی بیرون دیوار (سانتی‌متر)", [
        "برخوردی که کمی بیرون تصویر بازی است هم (روی لبه) حساب می‌شود.",
        "0: فقط روی خود تصویر."]),
    "floor_margin_cm": ("حاشیه‌ی پایین، سمت کف (سانتی‌متر)", [
        "زیر لبه‌ی پایین تصویر فقط تا این فاصله برخورد حساب می‌شود.",
        "توپی که روی زمین می‌جهد و به سمت دوربین می‌آید برخورد نیست."]),
    "max_region": ("بزرگ‌ترین ناحیه‌ی تغییر دور توپ", [
        "چند برابر یک توپ. ناحیه‌ی بزرگ‌تر یک آدم است و رد می‌شود.",
        "توپ کنار بدن رد می‌شود («part of big»)؟ زیادش کنید."]),
    "edge_reject": ("رد چیزهای واردشده از لبه (0 یا 1)", [
        "1: چیزی که از لبه‌ی تصویر وارد می‌شود (آدم، دست، توپ در دست) توپ نیست.",
        "خاموش کردنش خطا را زیاد می‌کند."]),
    "max_fg_share": ("بیشترین سهم تغییر تصویر", [
        "اگر بیش از این سهم تصویر یک‌جا عوض شود (چراغ، نور، تغییر صفحه)،",
        "آن فریم نادیده گرفته می‌شود («LIGHT CHANGE»)."]),
    "learn": ("سرعت یاد گرفتن دیوار خالی", [
        "هر فریم چقدر از تصویر به پس‌زمینه اضافه شود.",
        "بیشتر: تغییر نور زود جذب می‌شود، ولی توپ کند هم ممکن است محو شود."]),
}
GUIDE = ("راهنما", [
    "موس را روی هر اسلایدر ببرید تا توضیحش را ببینید.",
    "کلیک یا کشیدن: تغییر مقدار   چرخ موس: یک گام",
    "W: ذخیره در فایل جدا   G: ذخیره در تنظیمات بازی (config/settings.json)"])

SWIN = "balltool settings"
ROW = 30  # px per slider
TOP = 6
LABEL_W = 170
VALUE_W = 70
WIDTH = 620
TRACK_X0, TRACK_X1 = LABEL_W, WIDTH - VALUE_W - 10
HELP_H = 128
BG, TRACK, FILL, HOVER, TEXT = (40, 34, 30), (90, 90, 90), (170, 160, 0), (70, 58, 48), (235, 235, 235)


def slider_value(det_s, name):
    v = getattr(det_s, name)
    if name == "ball_tone":
        return TONES.index(v) if v in TONES else 0
    return float(v)


def _fonts(root: Path) -> Text:
    pygame.font.init()
    fonts = root / "assets" / "fonts"
    book = FontBook(fonts, {"bold": ["IRANSansX-Bold.ttf"], "body": ["IRANSansX-Medium.ttf", "IRANSansX-Regular.ttf"],
                            "latin": ["IRANSansX-Medium.ttf"]})
    return Text(book, persian_digits=False)


class Sliders:
    """The settings window; changes go into the detector's settings (apply)."""

    def __init__(self, det, root: Path):
        self.text = _fonts(root)
        self.changed: dict[str, int] = {}
        self.pos = {}
        for name, lo, hi, k, _ in SLIDERS:
            self.pos[name] = int(round((min(max(slider_value(det.s, name), lo), hi) - lo) * k))
        self.hover: int | None = None
        self.drag: int | None = None
        self.dirty = True
        self._help_cache: dict = {}
        cv2.namedWindow(SWIN, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(SWIN, self._mouse)
        self.draw()

    # -- values --------------------------------------------------------------------------------------
    @staticmethod
    def _spec(name):
        return next(s for s in SLIDERS if s[0] == name)

    def _set_pos(self, name, pos):
        _, lo, hi, k, _ = self._spec(name)
        pos = int(min(max(pos, 0), round((hi - lo) * k)))
        if pos != self.pos[name]:
            self.pos[name] = pos
            self.changed[name] = pos
            self.dirty = True

    def set(self, name, value):
        _, lo, hi, k, _ = self._spec(name)
        self._set_pos(name, round((min(max(value, lo), hi) - lo) * k))

    def apply(self, det) -> list:
        """Puts the moved sliders into det.s; returns (name, value) for each."""
        if not self.changed:
            return []
        out, reset = [], False
        for name, pos in list(self.changed.items()):
            _, lo, _, k, needs = self._spec(name)
            v = lo + pos / k
            old = getattr(det.s, name)
            if name == "ball_tone":
                val = TONES[int(round(v))]
            elif isinstance(old, bool):
                val = bool(int(round(v)))
            elif isinstance(old, int):
                val = int(round(v))
            else:
                val = round(v, 3)
            setattr(det.s, name, val)
            reset |= needs
            out.append((name, val))
        self.changed.clear()
        if reset:
            det.reset()
        return out

    def _shown(self, name) -> str:
        _, lo, _, k, _ = self._spec(name)
        v = lo + self.pos[name] / k
        if name == "ball_tone":
            return TONES[int(round(v))]
        if name in ("color", "edge_reject"):
            return "on" if round(v) else "off"
        return f"{v:.2f}" if k > 1 else f"{v:.0f}"

    # -- the mouse -------------------------------------------------------------------------------------
    def _row_at(self, y) -> int | None:
        i = (y - TOP) // ROW
        return int(i) if 0 <= y - TOP and i < len(SLIDERS) else None

    def _pos_at(self, row, x) -> int:
        name, lo, hi, k, _ = SLIDERS[row]
        frac = (x - TRACK_X0) / (TRACK_X1 - TRACK_X0)
        return round(min(max(frac, 0.0), 1.0) * (hi - lo) * k)

    def _mouse(self, event, x, y, flags, _param):
        row = self._row_at(y)
        if row != self.hover:
            self.hover = row
            self.dirty = True
        if event == cv2.EVENT_LBUTTONDOWN and row is not None:
            self.drag = row
            self._set_pos(SLIDERS[row][0], self._pos_at(row, x))
        elif event == cv2.EVENT_MOUSEMOVE and self.drag is not None and flags & cv2.EVENT_FLAG_LBUTTON:
            self._set_pos(SLIDERS[self.drag][0], self._pos_at(self.drag, x))
        elif event == cv2.EVENT_LBUTTONUP:
            self.drag = None
        elif event == cv2.EVENT_MOUSEWHEEL and row is not None:
            name = SLIDERS[row][0]
            self._set_pos(name, self.pos[name] + (1 if cv2.getMouseWheelDelta(flags) > 0 else -1))

    # -- drawing ---------------------------------------------------------------------------------------------
    def _help(self, key: str | None) -> np.ndarray:
        """The help box under the sliders, in Persian (right to left), as a BGR picture."""
        if key in self._help_cache:
            return self._help_cache[key]
        title, lines = HELP.get(key, GUIDE) if key else GUIDE
        surf = pygame.Surface((WIDTH, HELP_H))
        surf.fill(BG[::-1])
        pygame.draw.line(surf, (0, 160, 170), (0, 0), (WIDTH, 0), 2)
        y = 8
        head = self.text.render(f"{title}" + (f"   ({key})" if key else ""), "bold", 17, (0, 220, 240), localize=False)
        surf.blit(head, (WIDTH - 10 - head.get_width(), y))
        y += head.get_height() + 4
        for line in lines:
            img = self.text.render(line, "body", 14, TEXT, localize=False)
            surf.blit(img, (WIDTH - 10 - img.get_width(), y))
            y += img.get_height() + 2
        rgb = pygame.surfarray.array3d(surf).swapaxes(0, 1)
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        self._help_cache[key] = bgr
        return bgr

    def draw(self) -> None:
        if not self.dirty:
            return
        self.dirty = False
        h = TOP * 2 + ROW * len(SLIDERS)
        img = np.full((h, WIDTH, 3), BG, np.uint8)
        for i, (name, lo, hi, k, _) in enumerate(SLIDERS):
            y0 = TOP + i * ROW
            if i == self.hover:
                cv2.rectangle(img, (0, y0), (WIDTH - 1, y0 + ROW - 1), HOVER, -1)
            cy = y0 + ROW // 2
            cv2.putText(img, name, (8, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.48, TEXT, 1, cv2.LINE_AA)
            cv2.line(img, (TRACK_X0, cy), (TRACK_X1, cy), TRACK, 4, cv2.LINE_AA)
            frac = self.pos[name] / max(1, round((hi - lo) * k))
            kx = int(TRACK_X0 + frac * (TRACK_X1 - TRACK_X0))
            cv2.line(img, (TRACK_X0, cy), (kx, cy), FILL, 4, cv2.LINE_AA)
            cv2.circle(img, (kx, cy), 7, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.putText(img, self._shown(name), (TRACK_X1 + 12, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 240, 255), 1, cv2.LINE_AA)
        key = SLIDERS[self.hover][0] if self.hover is not None else None
        cv2.imshow(SWIN, np.vstack([img, self._help(key)]))
