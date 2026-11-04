"""Write the Persian how-to-play sheet for players into design/guide/.

    python tools/export_player_guide.py

Two pages, nothing about installing or running the game. The numbers come from
config/settings.json and the pictures are the game's own screens, rendered with
main.py --gallery. Needs Chrome or Edge, like tools/export_design_kit.py.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_design_kit import LRM, OUT, ROOT, H, box_icon, config, fa_digits, font_css, print_pdf, theme  # noqa: E402

FILE = "MarketPulse_HowToPlay_FA.pdf"
NAME = "بازار هدف"  # also replaces the game's own title in the pictures, whatever config/settings.json says
SCREENS = {"start": "01_attract", "name": "05_register_name", "play": "14_after_rush", "rush": "10_combo_x2", "end": "17_results"}
PICTURE_W, PICTURE_H = 720, 960


def pictures() -> dict[str, str]:
    """The game's screens as data URIs, small enough to keep the PDF light."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame

    with tempfile.TemporaryDirectory(prefix="guide_") as tmp:
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        settings["text"]["title"] = NAME
        named = Path(tmp) / "settings.json"
        named.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "main.py"), "--config", str(named), "--gallery", tmp], cwd=ROOT, check=True, capture_output=True)
        out = {}
        for key, name in SCREENS.items():
            small = Path(tmp) / f"{key}.png"
            pygame.image.save(pygame.transform.smoothscale(pygame.image.load(Path(tmp) / f"{name}.png"), (PICTURE_W, PICTURE_H)), small)
            out[key] = "data:image/png;base64," + base64.b64encode(small.read_bytes()).decode("ascii")
    return out


def build(cfg, pics: dict[str, str]) -> None:
    c = {k: H(v) for k, v in (("teal", theme.TURQUOISE), ("blue", theme.DARK_BLUE), ("red", theme.RED), ("purple", theme.PURPLE),
                              ("grey", theme.TEXT_GREY), ("line", theme.GREY_LIGHT), ("pale", theme.TURQUOISE_PALE))}
    n = lambda v: fa_digits(str(v))  # noqa: E731
    balls, seconds, rush_s = n(cfg.round.balls), n(cfg.round.duration_s), n(cfg.rush.duration_s)
    rush_m = n(cfg.rush.score_multiplier)
    levels = [(n(lv["after_hits"]), n(lv["multiplier"])) for lv in cfg.combo.levels]
    blue, combo_2 = cfg.targets["blue"].points, cfg.combo.levels[0]["multiplier"]
    sum_ltr = f'<span class="sum">{n(blue)} × {n(combo_2)} × {rush_m} = {n(blue * combo_2 * cfg.rush.score_multiplier)}</span>'
    example = f"در طوفان، ضریب برخورد پشت‌سرهم و ضریب طوفان در هم ضرب می‌شوند.<br>مثال: باکس «تنوع سبد» با ضریب ×{n(combo_2)} در طوفان: {sum_ltr} امتیاز"

    steps = (("start", "شروع کنید", "توپ را به مربع «شروع» بزنید. بعد از آن، راهنمای کوتاه بازی را می‌بینید."),
             ("name", "نام خود را بنویسید", "نام را با صفحه‌کلید بنویسید و Enter را بزنید، یا توپ را به مربع «شروع بازی» بزنید."),
             ("play", "به باکس‌ها بزنید", "بعد از شمارش معکوس، بازی شروع می‌شود. توپ را به هر باکسی که می‌خواهید بزنید."),
             ("end", "نتیجه را ببینید", "در پایان، امتیاز، سبک بازی و رتبه‌ی امروز خود را می‌بینید."))
    boxes = (("green", "رشد", "بزرگ و آرام"), ("blue", "تنوع سبد", "اندازه‌ی متوسط"), ("red", "نوسان", "کوچک و پرتحرک"))
    box_col = {"green": c["teal"], "blue": c["blue"], "red": c["red"]}
    rules = (("بعد از هر برخورد", "همه‌ی باکس‌ها به جای تازه‌ای می‌روند."),
             ("برخورد پشت‌سرهم", "بعد از " + "، بعد از ".join(f"{a} برخورد ضریب ×{m}" for a, m in levels) + " می‌شود."),
             ("پرتاب خطا", "امتیازی کم نمی‌شود، ولی یک توپ از دست می‌رود و ضریب به ×۱ برمی‌گردد."),
             ("پایان بازی", f"با آخرین توپ یا بعد از {seconds} ثانیه، هر کدام زودتر برسد."))

    step_html = "".join(f'<div class="step"><img src="{pics[key]}" alt=""><h3><i>{n(i + 1)}</i>{title}</h3><p>{text}</p></div>'
                        for i, (key, title, text) in enumerate(steps))
    box_html = "".join(f'<div class="box">{box_icon(color, "54px")}<h3 style="color:{box_col[color]}">{word}</h3><small>{look}</small>'
                       f'<b>{n(cfg.targets[color].points)}</b><small>امتیاز</small></div>' for color, word, look in boxes)
    rule_html = "".join(f'<div class="rule"><h3>{a}</h3><p>{b}</p></div>' for a, b in rules)

    def foot(page: int) -> str:
        return f'<div class="foot"><span>{NAME} | کارگزاری مفید</span><span>صفحه‌ی {n(page)} از ۲</span></div>'

    css = f"""{font_css()}
@page{{size:A4;margin:0}}*{{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
html,body{{margin:0;padding:0;font-family:IRANSansX;color:{c['blue']};font-size:14.5px;line-height:1.65;direction:rtl}}
.page{{width:210mm;height:297mm;position:relative;overflow:hidden;break-after:page;padding:0 15mm}}.page:last-child{{break-after:auto}}
header{{margin:0 -15mm;padding:7mm 15mm 6mm;background:linear-gradient(90deg,{c['blue']},{c['teal']});color:#fff}}
h1{{margin:0;font-size:34px;font-weight:900;line-height:1.25}}header p{{margin:0;font-size:15px;font-weight:500}}
h2{{margin:8mm 0 4mm;font-size:22px;font-weight:900;line-height:1.3}}h3{{margin:0;font-size:15.5px;font-weight:800;line-height:1.4}}p{{margin:0}}
.goal{{margin-top:6mm;font-size:19px;font-weight:800;line-height:1.5}}
.stats{{display:flex;gap:9px;margin-top:4mm}}.stat{{flex:1;background:{c['blue']};color:#fff;border-radius:11px;padding:7px 14px;display:flex;align-items:center;justify-content:center;gap:10px}}.stat b{{font-size:34px;font-weight:900;line-height:1.2}}.stat span{{font-size:15px;font-weight:600}}
.steps{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.step img{{display:block;width:100%;border:1px solid {c['line']};border-radius:8px}}.step p{{color:{c['grey']};font-size:13px;line-height:1.55;margin-top:3px}}
.step h3{{margin-top:8px;display:flex;align-items:center;gap:6px;font-size:14.5px}}.step i{{flex:none;font-style:normal;width:25px;height:25px;border-radius:50%;background:{c['teal']};color:#fff;font-weight:800;font-size:13.5px;display:flex;align-items:center;justify-content:center}}
.tip{{margin-top:6mm;background:{c['pale']};border-radius:11px;padding:10px 15px;font-weight:700;font-size:15.5px}}.cap{{margin-top:3mm;color:{c['grey']};font-weight:500}}
.sum{{direction:ltr;unicode-bidi:isolate;display:inline-block}}
.boxes{{display:flex;gap:10px}}.box{{flex:1;border:1px solid {c['line']};border-radius:11px;padding:9px;display:flex;flex-direction:column;align-items:center;text-align:center}}.box h3{{margin-top:5px}}.box small{{color:{c['grey']};font-size:12.5px}}.box b{{font-size:32px;font-weight:900;line-height:1.25}}
.rush{{display:flex;gap:7mm;align-items:center}}.rush img{{flex:none;width:58mm;display:block;border-radius:8px}}.rush ul{{margin:0;padding:0;list-style:none;flex:1}}
.rush li{{padding:9px 0;border-bottom:1px solid {c['line']};font-weight:500}}.rush li:last-child{{border:none}}.rush li b{{color:{c['purple']};font-weight:800}}
.rule{{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid {c['line']};align-items:baseline}}.rule h3{{flex:none;width:37mm}}.rule p{{color:{c['grey']}}}.rule:last-child{{border:none}}
.example{{margin-top:2mm;background:{c['pale']};border-radius:9px;padding:8px 13px;font-weight:700}}
.pc{{margin-top:7mm;border:1px solid {c['line']};border-radius:11px;padding:9px 14px}}.pc p{{color:{c['grey']}}}
kbd{{direction:ltr;display:inline-block;font-family:IRANSansX;font-weight:800;font-size:12.5px;background:{c['pale']};color:{c['blue']};border-radius:6px;padding:0 8px}}
.note{{position:absolute;left:15mm;right:15mm;bottom:17mm;color:{c['grey']};font-size:11.5px;line-height:1.5}}
.foot{{position:absolute;left:15mm;right:15mm;bottom:7mm;display:flex;justify-content:space-between;color:{c['grey']};font-size:11px;border-top:1px solid {c['line']};padding-top:4px}}"""
    html = f"""<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>{NAME} | راهنمای بازیکن</title><style>{css}</style><body>
<section class="page"><header><h1>{NAME}</h1><p>چطور بازی کنم؟</p></header>
<div class="goal">با توپ به باکس‌های رنگی بزنید و تا می‌توانید امتیاز جمع کنید.</div>
<div class="stats"><div class="stat"><b>{balls}</b><span>توپ</span></div><div class="stat"><b>{seconds}</b><span>ثانیه</span></div></div>
<div class="cap">هر پرتاب یک توپ کم می‌کند، چه به باکس بخورد چه نخورد. هر کدام زودتر تمام شود، بازی تمام است.</div>
<h2>مرحله‌های بازی</h2><div class="steps">{step_html}</div>
<h2>سه باکس</h2><div class="boxes">{box_html}</div>
<div class="tip">باکس قرمز کوچک‌ترین است، ولی بیشترین امتیاز را دارد و طوفان بازار را شروع می‌کند.</div>{foot(1)}</section>
<section class="page"><header><h1>طوفان بازار</h1><p>لحظه‌ی پرهیجان بازی</p></header>
<h2>باکس قرمز را بزنید</h2><div class="rush"><ul>
<li>هر بار که به باکس قرمز بزنید، <b>پس‌زمینه عوض می‌شود</b> و طوفان بازار شروع می‌شود.</li>
<li>هر طوفان <b>{rush_s} ثانیه</b> طول می‌کشد.</li>
<li>در طوفان، باکس‌ها بیشتر و تندتر می‌شوند و <b>امتیاز ×{rush_m}</b> می‌شود.</li>
<li>اگر در طوفان دوباره به قرمز بزنید، <b>{rush_s} ثانیه‌ی تازه</b> از همان لحظه شروع می‌شود.</li>
</ul><img src="{pics['rush']}" alt=""></div>
<h2>چند نکته‌ی ساده</h2>{rule_html}
<div class="example">{example}</div>
<div class="pc"><h3>بازی روی کامپیوتر</h3><p>روی MarketPulse.exe دوبار کلیک کنید. هر کلیک موس جای یک توپ را می‌گیرد.<br>برای خروج، دو بار <kbd>Esc</kbd> را بزنید.</p></div>
<div class="note">سبک بازی فقط شیوه‌ی بازی شما را توصیف می‌کند و ارزیابی مالی یا توصیه‌ی سرمایه‌گذاری نیست.</div>{foot(2)}</section></body></html>"""
    print_pdf(html.replace("×", LRM + "×"), OUT / "guide" / FILE)  # keeps ×2 the way the wall shows it


def main() -> int:
    cfg, _ = config.load(None)
    build(cfg, pictures())
    print(OUT / "guide" / FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
