"""Write the short guide (how to run the game, what the rules are) into design/guide/.

    python tools/export_quick_guide.py

One PDF in Persian and one in English, two pages each. The numbers come from
config/settings.json. Needs Chrome or Edge, like tools/export_design_kit.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_design_kit import LRM, OUT, H, Ink, box_icon, config, doc, fa_digits, font_css, inline, layered, print_pdf, theme, wall_layers  # noqa: E402

COPY = {
    "fa": {
        "dir": "rtl", "file": "MarketPulse_Quick_Guide_FA.pdf", "title": "نبض بازار", "sub": "راهنمای ساده‌ی بازی",
        "run_h": "اجرای بازی",
        "steps": (("نصب پایتون (فقط یک بار)", "پایتون نسخه‌ی 3.10 یا بالاتر را از python.org نصب کنید. هنگام نصب، گزینه‌ی Add python.exe to PATH را تیک بزنید.", ()),
                  ("آماده‌کردن بازی (فقط یک بار)", "پوشه‌ی market_pulse را باز کنید. در نوار آدرس بالای پنجره بنویسید cmd و Enter بزنید. سپس این دو دستور را یکی‌یکی اجرا کنید:",
                   (r"python -m venv .venv", r".venv\Scripts\python -m pip install -r requirements.txt")),
                  ("امتحان با موس", "بدون دیوار و حسگر. هر کلیک موس جای یک توپ را می‌گیرد:", (r".venv\Scripts\python main.py --windowed --simulate",)),
                  ("اجرا روی دیوار", "بازی تمام‌صفحه باز می‌شود و برخورد توپ‌ها را از حسگر می‌خواند. یا روی فایل scripts\\run_kiosk.bat دوبار کلیک کنید.", (r".venv\Scripts\python main.py",))),
        "keys_h": "کلیدهای اپراتور",
        "keys": (("Space", "شروع دور یا ردکردن راهنما"), ("Enter", "تأیید نام و شروع بازی"), ("P", "توقف و ادامه"), ("R", "لغو دور و بازگشت به صفحه‌ی شروع"), ("F4", "لغو ثبت‌نام"), ("Esc Esc", "خروج (دو بار پشت‌سرهم)")),
        "note": "توپ‌ها روی دیوار ثبت نمی‌شوند؟ کلید F3 را بزنید تا وضعیت حسگر را ببینید.",
        "rules_h": "قوانین بازی", "goal": "با توپ به باکس‌های رنگی بزنید و تا می‌توانید امتیاز جمع کنید.",
        "stats": (("۱۰", "توپ"), ("۶۰", "ثانیه"), ("۴", "ثانیه طوفان بازار")),
        "start": "توپ را به مربع «شروع» بزنید، نام خود را با صفحه‌کلید بنویسید و «شروع بازی» را بزنید.",
        "boxes_h": "سه باکس", "pts": "امتیاز",
        "boxes": (("green", "رشد", "ریسک کم، بازده کم"), ("blue", "تنوع سبد", "ریسک و بازده متوسط"), ("red", "نوسان", "ریسک و بازده احتمالی زیاد")),
        "rules": (("بعد از هر برخورد", "همه‌ی باکس‌ها به جای تازه‌ای می‌روند."),
                  ("باکس قرمز", "هر بار سه باکس تازه اضافه می‌کند و «طوفان بازار» را شروع می‌کند."),
                  ("طوفان بازار", "۴ ثانیه با سرعت و باکس بیشتر و امتیاز ×۲. برخورد دوباره به قرمز، ۴ ثانیه‌ی تازه می‌دهد."),
                  ("برخورد پشت‌سرهم", "بعد از ۲ برخورد ضریب ×۲، بعد از ۴ برخورد ×۳ و بعد از ۶ برخورد ×۴ می‌شود."),
                  ("پرتاب خطا", "امتیاز کم نمی‌شود، ولی یک توپ می‌رود و ضریب به ×۱ برمی‌گردد."),
                  ("پایان بازی", "با توپ دهم یا بعد از ۶۰ ثانیه، هر کدام زودتر برسد."),),
        "foot": "کارگزاری مفید", "page": "صفحه‌ی {n} از ۲",
    },
    "en": {
        "dir": "ltr", "file": "MarketPulse_Quick_Guide_EN.pdf", "title": "Market Pulse", "sub": "A simple guide to the game",
        "run_h": "How to run the game",
        "steps": (("Install Python (once)", "Install Python 3.10 or newer from python.org. During setup, tick Add python.exe to PATH.", ()),
                  ("Prepare the game (once)", "Open the market_pulse folder. Type cmd in the address bar at the top of the window and press Enter. Then run these two commands, one at a time:",
                   (r"python -m venv .venv", r".venv\Scripts\python -m pip install -r requirements.txt")),
                  ("Try it with the mouse", "No wall or sensor needed. Each mouse click counts as one ball:", (r".venv\Scripts\python main.py --windowed --simulate",)),
                  ("Run it on the wall", "The game opens full screen and reads ball hits from the sensor. Or double-click scripts\\run_kiosk.bat.", (r".venv\Scripts\python main.py",))),
        "keys_h": "Operator keys",
        "keys": (("Space", "Start a round or skip the instructions"), ("Enter", "Confirm the name and start the game"), ("P", "Pause and resume"), ("R", "Cancel the round and go back to the start screen"), ("F4", "Cancel registration"), ("Esc Esc", "Quit (press twice)")),
        "note": "Balls not registering on the wall? Press F3 to see the sensor status.",
        "rules_h": "The rules", "goal": "Throw balls at the coloured boxes and score as many points as you can.",
        "stats": (("10", "touches"), ("60", "seconds"), ("4", "seconds of Market Rush")),
        "start": "Throw a ball at the «شروع» (Start) square, type your name on the keyboard, then press Start Game.",
        "boxes_h": "The three boxes", "pts": "points",
        "boxes": (("green", "رشد · Growth", "Low risk, low return"), ("blue", "تنوع سبد · Balanced portfolio", "Medium risk and return"), ("red", "نوسان · Volatility", "High risk, high possible return")),
        "rules": (("After every hit", "All the boxes move to new places."),
                  ("Red box", "Every time: adds three new boxes and starts Market Rush."),
                  ("Market Rush", "4 seconds with faster movement, more boxes and ×2 points. Another red hit gives a fresh 4 seconds."),
                  ("Hits in a row", "The multiplier becomes ×2 after 2 hits, ×3 after 4 hits and ×4 after 6 hits."),
                  ("A miss", "You lose no points, but it uses a touch and the multiplier goes back to ×1."),
                  ("End of the game", "On the 10th touch or after 60 seconds, whichever comes first."),),
        "foot": "Mofid Securities", "page": "Page {n} of 2",
    },
}


def build(lang: str, cfg) -> None:
    t = COPY[lang]
    c = {k: H(v) for k, v in (("teal", theme.TURQUOISE), ("blue", theme.DARK_BLUE), ("red", theme.RED), ("grey", theme.TEXT_GREY),
                              ("line", theme.GREY_LIGHT), ("pale", theme.TURQUOISE_PALE))}
    box_col = {"green": c["teal"], "blue": c["blue"], "red": c["red"]}
    num = fa_digits if lang == "fa" else str
    wall = doc("", 300, 400, layered([x for x in wall_layers(Ink(True), cfg) if x[0] != "Feedback_sample"]))

    def head(title: str, big: bool) -> str:
        return f'<header class="{"big" if big else ""}"><h1>{t["title"]}</h1><p>{t["sub"]}</p></header><h2>{title}</h2>'

    def foot(n: int) -> str:
        return f'<div class="foot"><span>{t["title"]} · {t["foot"]}</span><span>{t["page"].format(n=num(str(n)))}</span></div>'

    steps = ""
    for i, (title, text, cmds) in enumerate(t["steps"]):
        code = "".join(f'<code>{x}</code>' for x in cmds)
        steps += f'<div class="step"><i>{num(str(i + 1))}</i><div><h3>{title}</h3><p>{text}</p>{code}</div></div>'
    keys = "".join(f'<div class="key"><kbd>{k}</kbd><span>{d}</span></div>' for k, d in t["keys"])
    stats = "".join(f'<div class="stat"><b>{a}</b><span>{b}</span></div>' for a, b in t["stats"])
    boxes = ""
    for color, word, size in t["boxes"]:
        boxes += (f'<div class="box">{box_icon(color, "58px")}<h3 style="color:{box_col[color]}">{word}</h3><small>{size}</small>'
                  f'<b>{num(str(cfg.targets[color].points))}</b><small>{t["pts"]}</small></div>')
    rules = "".join(f'<div class="rule"><h3>{a}</h3><p>{b}</p></div>' for a, b in t["rules"])
    css = f"""{font_css()}
@page{{size:A4;margin:0}}*{{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
html,body{{margin:0;padding:0;font-family:IRANSansX;color:{c['blue']};font-size:14px;line-height:1.6;direction:{t['dir']}}}
.page{{width:210mm;height:297mm;position:relative;overflow:hidden;break-after:page;padding:0 15mm}}.page:last-child{{break-after:auto}}
header{{margin:0 -15mm;padding:7mm 15mm 6mm;background:linear-gradient(90deg,{c['blue']},{c['teal']});color:#fff}}
h1{{margin:0;font-size:32px;font-weight:900;line-height:1.25}}header p{{margin:0;font-size:14px;font-weight:500}}
h2{{margin:7mm 0 4mm;font-size:23px;font-weight:900;line-height:1.3}}h3{{margin:0;font-size:15.5px;font-weight:800;line-height:1.4}}p{{margin:0}}
.step{{display:flex;gap:11px;margin-bottom:5mm}}.step>div{{flex:1;min-width:0}}.step i{{flex:none;font-style:normal;width:31px;height:31px;border-radius:50%;background:{c['teal']};color:#fff;font-weight:800;font-size:15px;display:flex;align-items:center;justify-content:center}}
.step p{{color:{c['grey']}}}code{{display:block;direction:ltr;text-align:left;font-family:Consolas,monospace;font-size:13px;background:{c['blue']};color:#fff;border-radius:7px;padding:6px 11px;margin-top:5px}}
.keys{{display:grid;grid-template-columns:1fr 1fr;gap:7px 12px}}.key{{display:flex;align-items:center;gap:9px;border:1px solid {c['line']};border-radius:9px;padding:7px 10px}}
kbd{{flex:none;direction:ltr;font-family:IRANSansX;font-weight:800;font-size:12.5px;background:{c['pale']};border-radius:6px;padding:3px 9px;min-width:62px;text-align:center}}.key span{{font-size:13px;line-height:1.4}}
.note{{margin-top:5mm;background:{c['pale']};border-radius:9px;padding:8px 13px;font-weight:500}}
.top{{display:flex;gap:7mm;align-items:stretch}}.top>div:first-child{{flex:1;display:flex;flex-direction:column;gap:4mm;justify-content:center}}.wall{{flex:none;border:1px solid {c['line']};border-radius:6px;overflow:hidden}}
.goal{{font-size:17px;font-weight:700;line-height:1.5}}.stats{{display:flex;gap:9px}}.stat{{flex:1;background:{c['blue']};color:#fff;border-radius:10px;text-align:center;padding:8px 0}}.stat b{{display:block;font-size:34px;font-weight:900;line-height:1.2}}
.start{{background:{c['pale']};border-radius:9px;padding:8px 12px;font-weight:700}}
.boxes{{display:flex;gap:10px}}.box{{flex:1;border:1px solid {c['line']};border-radius:11px;padding:10px;display:flex;flex-direction:column;align-items:center;text-align:center}}.box h3{{margin-top:5px}}.box small{{color:{c['grey']};font-size:12px}}.box b{{font-size:32px;font-weight:900;line-height:1.25;margin-top:3px}}
.rule{{display:flex;gap:10px;padding:7px 0;border-bottom:1px solid {c['line']};align-items:baseline}}.rule h3{{flex:none;width:36mm;font-size:14.5px}}.rule p{{color:{c['grey']}}}.rule:last-child{{border:none}}
.foot{{position:absolute;left:15mm;right:15mm;bottom:7mm;display:flex;justify-content:space-between;color:{c['grey']};font-size:11px;border-top:1px solid {c['line']};padding-top:4px}}"""
    html = f"""<!doctype html><html lang="{lang}" dir="{t['dir']}"><meta charset="utf-8"><title>{t['title']} · {t['sub']}</title><style>{css}</style><body>
<section class="page">{head(t['run_h'], True)}{steps}<h2>{t['keys_h']}</h2><div class="keys">{keys}</div><div class="note">{t['note']}</div>{foot(1)}</section>
<section class="page">{head(t['rules_h'], False)}<div class="top"><div><div class="goal">{t['goal']}</div><div class="stats">{stats}</div><div class="start">{t['start']}</div></div><div class="wall">{inline(wall, "52mm")}</div></div>
<h2>{t['boxes_h']}</h2><div class="boxes">{boxes}</div><div style="margin-top:4mm">{rules}</div>{foot(2)}</section></body></html>"""
    print_pdf(html.replace("×", LRM + "×"), OUT / "guide" / t["file"])  # keeps ×2 the way the wall shows it


def main() -> int:
    cfg, _ = config.load(None)
    for lang in COPY:
        build(lang, cfg)
    print(f"written to {OUT / 'guide'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
