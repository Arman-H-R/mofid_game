"""One A4 page for players: how to play «پرتاب هدفمند» and how the points work (MOFID_UI v11).

    .venv\\Scripts\\python tools\\export_player_guide_v11.py      -> design/guide/PartabHadafmand_Player_Guide_FA.pdf

The numbers (seconds, the points of each size, the lottery scores) come from
config/settings.json, and the pictures are the game's own screens, played headless.
Needs Chrome or Edge (it prints the page).
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "design"
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402

from market_pulse.ui import theme  # noqa: E402

from market_pulse import config  # noqa: E402
from market_pulse.app import App, Paths  # noqa: E402
from market_pulse.game import flow as F  # noqa: E402
from market_pulse.input.events import Impact  # noqa: E402

FILE = "PartabHadafmand_Player_Guide_FA.pdf"
SHOT_W, SHOT_H = 600, 800


def H(rgb) -> str:
    return "#%02X%02X%02X" % tuple(rgb[:3])


def fa_digits(s: str) -> str:
    return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def font_css() -> str:
    out = []
    for name, weight in (("Regular", 400), ("Medium", 500), ("DemiBold", 600), ("Bold", 700), ("ExtraBold", 800), ("Black", 900)):
        data = base64.b64encode((ROOT / "assets" / "fonts" / f"IRANSansX-{name}.ttf").read_bytes()).decode("ascii")
        out.append(f"@font-face{{font-family:IRANSansX;font-weight:{weight};src:url(data:font/ttf;base64,{data}) format('truetype')}}")
    return "".join(out)


def find_browser() -> str | None:
    for p in (Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
              Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
              Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe"):
        if p.is_file():
            return str(p)
    return shutil.which("chrome") or shutil.which("msedge")


def print_pdf(html: str, out: Path) -> None:
    """The page printed to PDF by Chrome or Edge, headless."""
    browser = find_browser()
    if browser is None:
        raise RuntimeError("the PDF needs Chrome or Edge")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="guide_pdf_") as tmp:
        src = Path(tmp) / "page.html"
        src.write_text(html, encoding="utf-8")
        target = Path(tmp) / "out.pdf"
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
                        f"--user-data-dir={tmp}/profile", f"--print-to-pdf={target}", src.as_uri()],
                       check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.copyfile(target, out)


def png_uri(surf: pygame.Surface) -> str:
    buf = io.BytesIO()
    pygame.image.save(surf, buf, "png")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def screens() -> tuple[dict[str, str], str, config.Section]:
    """The game's own screens from one played session, and its logo."""
    tmp = Path(tempfile.mkdtemp(prefix="guide_"))
    raw = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    raw.setdefault("display", {}).update({"resolution": [SHOT_W, SHOT_H], "fullscreen": False, "fill_screen": False, "scale": 1.0})
    raw.setdefault("input", {}).update({"sources": [], "simulation": False})
    raw.setdefault("camera", {})["enabled"] = False
    raw.setdefault("web", {})["enabled"] = False
    raw.setdefault("results", {}).update({"log_file": str(tmp / "r.jsonl"), "webhook_url": ""})
    (tmp / "settings.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cfg, _ = config.load(tmp / "settings.json")

    class Clock:
        t = 1000.0

        def __call__(self):
            return self.t

    clock = Clock()
    app = App(cfg, Paths(ROOT), clock_source=clock, start_inputs=False)
    W, Hh = float(cfg.wall.width_cm), float(cfg.wall.height_cm)

    def run(s):
        for _ in range(max(1, int(round(s * 30)))):
            clock.t += 1 / 30
            app.frame()

    def ball(x, y):
        app.input.queue.put(Impact(x / W, y / Hh, clock.t, "udp_json", (0.0, 0.0)))

    shots = {}
    now = app.clock.now()
    clock.t += app.renderer.attract.start_of("title", now, False) - now + 2.0
    run(0.5)
    shots["start"] = app.screen.copy()
    if not app.flow.start_for("سارا", app.clock.now()):  # as the operator's phone page does
        raise RuntimeError(f"the round did not start (state {app.flow.state})")
    for _ in range(200):
        if app.flow.state == F.COUNTDOWN:
            break
        run(0.2)
    run(0.6)
    shots["ready"] = app.screen.copy()
    for _ in range(200):
        if app.flow.state == F.PLAYING:
            break
        run(0.2)
    run(4.0)
    rnd = app.flow.round
    for k in range(9):
        now = app.clock.now()
        tg = next(t for t in rnd.targets if t.shown(now))
        ball(*tg.center_at(now))
        run(0.3 if k == 8 else 1.6)
    shots["play"] = app.screen.copy()
    for _ in range(400):  # never forever
        if app.flow.state == F.RESULTS:
            break
        run(0.5)
    run(5.0)
    shots["result"] = app.screen.copy()
    logo = app.renderer._logo("fa_s_white", 84)
    logo_uri = png_uri(logo) if logo is not None else ""
    app.shutdown()
    return {k: png_uri(v) for k, v in shots.items()}, logo_uri, cfg


def tile_uri(key: str) -> str:
    p = ROOT / "assets" / "products" / f"{key}.png"
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def build(cfg, pics: dict[str, str], logo: str) -> Path:
    n = lambda v: fa_digits(str(int(v)))  # noqa: E731
    field, turq, deep, mist = H(theme.FIELD), H(theme.TURQ), H(theme.DEEP), H(theme.MIST)
    navy, grey, line, pale = H(theme.DARK_BLUE), H(theme.TEXT_GREY), H(theme.GREY_LIGHT), H(theme.TURQUOISE_PALE)
    raw = cfg.raw()
    t = raw["targets"]
    secs = n(cfg.round.duration_s)
    two, three = n(cfg.results.lottery_two_from), n(cfg.results.lottery_three_from)
    keys = [p["key"] for p in raw["products"]]
    sizes = (("medium", "باکس بزرگ", 64, keys[0]), ("small", "باکس متوسط", 50, keys[4 % len(keys)]),
             ("tiny", "باکس کوچک", 36, keys[6 % len(keys)]))
    size_html = "".join(
        f'<div class="size"><div class="tile"><img src="{tile_uri(k)}" style="width:{px}px;height:{px}px"></div>'
        f'<b>+{n(t[tier]["points"])}</b><span>{label}</span></div>' for tier, label, px, k in sizes)
    steps = (("start", "شروع", "نام خود را به اپراتور بگویید؛ او بازی را برای شما شروع می‌کند."),
             ("ready", "آماده", "روبه‌روی دیوار بایستید؛ بعد از شمارش معکوس بازی شروع می‌شود."),
             ("play", "بازی", f"بعد از شمارش معکوس، {secs} ثانیه فرصت دارید. به باکس‌ها بزنید."),
             ("result", "نتیجه", "امتیاز، امتیاز قرعه‌کشی و رتبه‌ی امروز خود را ببینید."))
    step_html = "".join(f'<div class="step">' + (f'<img src="{pics[k]}">' if k in pics else '<div class="noimg"></div>')
                        + f'<h3><i>{n(i + 1)}</i>{title}</h3><p>{text}</p></div>' for i, (k, title, text) in enumerate(steps))
    lottery = ""
    if cfg.results.get("lottery", True):
        lottery = f"""<h2>امتیاز قرعه‌کشی</h2><p class="sub">در پایان بازی، بر اساس امتیازتان ۱، ۲ یا ۳ امتیاز قرعه‌کشی می‌گیرید.</p>
<div class="lot"><div><b>۱</b><span>امتیاز بازی کمتر از {two}</span></div>
<div><b>۲</b><span>از {two} تا کمتر از {three}</span></div>
<div class="top"><b>۳</b><span>{three} و بیشتر</span></div></div>"""
    css = f"""{font_css()}
@page{{size:A4;margin:0}}*{{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
html,body{{margin:0;padding:0;font-family:IRANSansX;color:{navy};font-size:13.5px;line-height:1.6;direction:rtl}}
.page{{width:210mm;height:297mm;position:relative;overflow:hidden;padding:0 13mm}}
header{{margin:0 -13mm;padding:7mm 13mm 6mm;background:{field};color:#fff;display:flex;align-items:center;justify-content:space-between}}
header .t small{{display:block;color:{turq};font-size:17px;font-weight:700;line-height:1.3}}
header h1{{margin:0;font-size:40px;font-weight:900;line-height:1.15}}header img{{height:22mm}}
h2{{margin:6mm 0 2.5mm;font-size:19px;font-weight:900;line-height:1.3}}h3{{margin:0;font-size:14.5px;font-weight:800}}p{{margin:0}}
.sub{{color:{grey};margin-top:-1mm;margin-bottom:2.5mm}}
.goal{{margin-top:5mm;display:flex;gap:4mm;align-items:stretch}}
.goal .say{{flex:1;background:{pale};border-radius:10px;padding:8px 14px;font-size:16px;font-weight:800;display:flex;align-items:center}}
.stat{{width:31mm;background:{field};color:#fff;border-radius:10px;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:4px}}
.stat b{{font-size:30px;font-weight:900;line-height:1.1}}.stat span{{font-size:12.5px;color:{mist};font-weight:600}}
.steps{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}}
.step img,.noimg{{display:block;width:100%;aspect-ratio:3/4;border-radius:7px;border:1px solid {line}}}
.step h3{{margin-top:5px;display:flex;align-items:center;gap:6px}}
.step i{{font-style:normal;width:22px;height:22px;border-radius:50%;background:{turq};color:#fff;font-size:12.5px;font-weight:800;display:flex;align-items:center;justify-content:center}}
.step p{{color:{grey};font-size:12px;line-height:1.5;margin-top:2px}}
.sizes{{display:flex;gap:8px}}.size{{flex:1;border:1px solid {line};border-radius:10px;padding:8px;display:flex;flex-direction:column;align-items:center}}
.tile{{height:66px;display:flex;align-items:center}}.tile img{{border-radius:8px}}
.size b{{font-size:26px;font-weight:900;line-height:1.25;direction:ltr}}.size span{{color:{grey};font-weight:600}}
.rules{{margin-top:3mm}}.rule{{display:flex;gap:8px;padding:4px 0;font-weight:600}}.rule i{{font-style:normal;color:{turq};font-weight:900}}
.lot{{display:flex;gap:8px}}.lot div{{flex:1;border-radius:10px;background:{pale};padding:7px 10px;display:flex;align-items:center;gap:10px}}
.lot div.top{{background:{field};color:#fff}}.lot b{{font-size:30px;font-weight:900;line-height:1;color:{turq}}}.lot span{{font-weight:700;font-size:13px}}
.foot{{position:absolute;left:13mm;right:13mm;bottom:7mm;display:flex;justify-content:space-between;color:{grey};font-size:11px;border-top:1px solid {line};padding-top:4px}}"""
    html = f"""<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>پرتاب هدفمند | راهنمای بازیکن</title><style>{css}</style><body>
<section class="page"><header><div class="t"><small>چالش تعاملی</small><h1>پرتاب هدفمند</h1></div>{f'<img src="{logo}">' if logo else ''}</header>
<div class="goal"><div class="say">با توپ به باکس‌های محصولات مفید بزنید و تا می‌توانید امتیاز جمع کنید.</div>
<div class="stat"><b>{secs}</b><span>ثانیه</span></div><div class="stat"><b>∞</b><span>توپ نامحدود</span></div></div>
<h2>چطور بازی کنم؟</h2><div class="steps">{step_html}</div>
<h2>امتیاز هر باکس</h2><p class="sub">باکس‌های کوچکتر، امتیاز بیشتر.</p><div class="sizes">{size_html}</div>
<div class="rules"><div class="rule"><i>+</i>همه‌ی باکس‌ها امتیاز مثبت دارند؛ امتیاز منفی وجود ندارد. توپی که به باکس نخورد امتیازی کم نمی‌کند.</div>
<div class="rule"><i>/</i>بعد از هر برخورد، همه‌ی باکس‌ها جابه‌جا می‌شوند؛ باکس تازه را پیدا کنید.</div></div>
{lottery}
<div class="foot"><span>پرتاب هدفمند | گروه مالی مفید</span><span>راهنمای بازیکن</span></div></section></body></html>"""
    out = OUT / "guide" / FILE
    print_pdf(html, out)
    return out


def main() -> int:
    pics, logo, cfg = screens()
    print(build(cfg, pics, logo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
