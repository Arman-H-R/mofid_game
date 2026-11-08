"""Settings: built-in defaults deep-merged with ``config/settings.json``.

Every value the game reads lives in ``DEFAULTS``. The JSON file only needs the
keys you want to change; anything missing falls back to the default, and a
value of the wrong type is reported and replaced by the default, so a typo in
the file never stops the installation from starting.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

# The three box sizes. Every one of the 13 products can appear at any size; the size alone
# sets the points (MOFID_UI v4.1: L +10, M +15, S +30). A hit on a tiny box starts Market Rush.
TIERS = ("medium", "small", "tiny")
COLORS = TIERS  # the old name (green, blue, red boxes), still imported by the design-kit tools

# The 13 official product tiles (assets/products, cut from MOFID_UI_v4.1 page 1 by
# tools/extract_product_tiles.py). "name" is the label shown with the points after a hit.
# "aspect" is the tile's height / width: the hit area is the tile itself.
PRODUCTS: list[dict[str, Any]] = [
    {"key": "gold", "name": "طلای گرمی", "file": "gold.png", "aspect": 0.92},
    {"key": "atiyeh", "name": "آتیه", "file": "atiyeh.png", "aspect": 0.92},
    {"key": "hamnag", "name": "همنگ", "file": "hamnag.png", "aspect": 0.92},
    {"key": "tavan", "name": "توان", "file": "tavan.png", "aspect": 0.92},
    {"key": "hami", "name": "حامی", "file": "hami.png", "aspect": 0.92},
    {"key": "ayar", "name": "عیار", "file": "ayar.png", "aspect": 0.92},
    {"key": "amoozesh", "name": "آموزش مفید", "file": "amoozesh.png", "aspect": 0.92},
    {"key": "bimeh", "name": "بیمه زندگی مفید", "file": "bimeh.png", "aspect": 0.92},
    {"key": "boursview", "name": "بورس ویو", "file": "boursview.png", "aspect": 0.92},
    {"key": "boursview2", "name": "بورس ویو ۲.۰", "file": "boursview2.png", "aspect": 0.92},
    {"key": "jet", "name": "مفید کارت جت", "file": "jet.png", "aspect": 0.92},
    {"key": "mofidapp", "name": "مفید اپ", "file": "mofidapp.png", "aspect": 0.92},
    {"key": "easytrader", "name": "ایزی تریدر", "file": "easytrader.png", "aspect": 0.92},
]

DEFAULTS: dict[str, Any] = {
    "display": {
        "fullscreen": True,
        "display_index": 0,
        # The canvas the game is drawn on. [0, 0] (or "auto") = from the display; [w, h] = that
        # size, GPU-scaled to the display; one of them 0 = that one follows the wall's shape.
        "resolution": [0, 0],
        # The game is never stretched (one scale for x and y). On: the wall takes the display's
        # shape at the start (wider than 3:4 on a 16:9 screen), so play fills the whole screen;
        # the menus keep their 3:4 layout in the middle. Off: the wall stays 3:4, with bars.
        "fill_screen": False,
        # The game's picture at this part of the screen, in the middle (1.0 = all of it): smaller
        # for a projector that spills over the wall. The lidar is calibrated again after a change.
        "scale": 1.0,
        "windowed_height_fraction": 0.88,
        "fps": 60,
        "vsync": True,
        "hide_cursor": True,
    },
    # Physical size of the LED wall. Everything in the game is laid out in
    # centimetres on this wall, so target sizes match the scenario at any resolution.
    "wall": {"width_cm": 300, "height_cm": 400},
    "layout": {
        "hud_height_cm": 64,
        "footer_height_cm": 30,
        "side_margin_cm": 12,
        "gap_cm": 12,
        "min_move_cm": 35,
        "transition_s": 0.35,
        "spawn_s": 0.32,
        # below the circle of tiles on the attract screen (MOFID_UI v4.1 page 1)
        "start_button": {"x_cm": 150, "y_cm": 318, "size_cm": 70},
    },
    "round": {
        # Two limits, each with its own switch (F5, tab «دور بازی»). By default only the time:
        # the round lasts duration_s and players throw as many balls as they can.
        # ball_limit on: the round also ends on ball number "balls" (0 = no limit after all).
        # At least one is always on (with both off, the time limit is used).
        "time_limit": True,
        "duration_s": 60,
        "ball_limit": False,
        "balls": 10,
        "end_delay_s": 1.8,
    },
    # The product boxes. "active" is how many are on the wall at once, outside Market Rush:
    # every hit box is replaced at once, so the number stays the same (the one place it is set).
    # Sizes are the tile widths on the wall (MOFID_UI v4.1: 196 / 150 / 104 px of 1080 on 3 m);
    # the smaller the box, the more points. "weight" is how often each size is drawn for a new
    # box; min_tiny keeps at least that many tiny boxes up (with 3 or more boxes), so Market
    # Rush can always be reached.
    "targets": {
        "active": 4,
        "medium": {"size_cm": 54, "points": 10, "weight": 3},
        "small": {"size_cm": 42, "points": 15, "weight": 3},
        "tiny": {"size_cm": 29, "points": 30, "weight": 2},
        "min_tiny": 1,
        "min_size_cm": 20,
    },
    "products": PRODUCTS,
    # Gentle movement so the boxes feel alive but stay easy to read. Patterns: still,
    # sway_x, sway_y, orbit, figure8, volatile. "pulse" is the size change as a fraction of
    # the side. "variation": every box gets its own speed and reach, up to this much
    # faster or slower, wider or narrower.
    "motion": {
        "enabled": True,
        "speed": 1.0,
        "variation": 0.18,
        "medium": {"pattern": "sway_x", "amplitude_cm": 10, "period_s": 5.4, "pulse": 0.0, "pulse_period_s": 2.0},
        "small": {"pattern": "figure8", "amplitude_cm": 9, "period_s": 5.0, "pulse": 0.0, "pulse_period_s": 2.6},
        "tiny": {"pattern": "volatile", "amplitude_cm": 8, "period_s": 4.2, "pulse": 0.04, "pulse_period_s": 1.6},
    },
    # Add-on 1.2: after N consecutive hits, the following hits use this multiplier.
    # A miss resets it to x1. Nothing is ever subtracted.
    "combo": {
        "enabled": True,
        "levels": [
            {"after_hits": 2, "multiplier": 2},
            {"after_hits": 4, "multiplier": 3},
            {"after_hits": 6, "multiplier": 4},
        ],
    },
    # Market Rush: one high-intensity phase, started by hitting tiny boxes.
    # Tiny boxes that are merely on the wall never start or extend it.
    "rush": {
        "enabled": True,
        "duration_s": 4,
        "trigger_tiny_hits": 1,  # tiny-box hits needed to start it
        "restart_on_tiny_hit": True,  # a tiny hit during it gives a fresh duration_s from that hit
        "max_per_round": 100,
        "cooldown_s": 0,  # after a rush, before red hits count again
        "score_multiplier": 2,
        "stack_with_combo": True,  # True: combo x rush. False: the larger of the two.
        "speed": 1.6,  # movement speed during the phase
        "speed_ramp_s": 0.4,
        "max_targets": 8,  # boxes on the wall during it, with the extra ones (never fewer than targets.active)
        "spawn_interval_s": 1.0,
        "spawn_lifetime_s": 4.0,  # extra boxes leave after this long, and always when the phase ends
        "spawn_tiers": ["tiny", "small", "medium", "small"],
        "transition_s": 0.4,  # background fade in and out
    },
    # The end-screen statement describes how the round was played, from the share of
    # hits per box size and the hits during Market Rush. The score only picks the
    # wording within a style, so a high score alone never means a high risk appetite.
    "profiles": {
        "min_hits": 3,  # below this there is too little to go on: "watchful"
        "aggressive_tiny_share": 0.5,
        "risk_tiny_share": 0.3,
        "rush_min_hits": 4,  # also risk-tolerant: at least this many hits during Market Rush...
        "rush_share": 0.6,  # ...and at least this share of all hits
        "growth_medium_share": 0.5,
        "cautious_tiny_share": 0.1,  # medium-led with tiny at or below this is "cautious", above it "growth"
        # statements[0] below the first, [1] up to the second, [2] above. Set from simulated
        # players (about a third of scores in each); check against real rounds.
        "score_tiers": [90, 180],
        "styles": {
            "watchful": {
                "label": "ناظر صبور",
                "statements": [
                    "شما بیشتر بازار را زیر نظر گرفتید و با صبر حرکت کردید؛ دفعه‌ی بعد فرصت‌های بیشتری را امتحان کنید.",
                ],
            },
            "cautious": {
                "label": "سرمایه‌گذار محتاط",
                "statements": [
                    "شما با احتیاط جلو رفتید و از نوسان‌های تند فاصله گرفتید؛ قدم‌های سنجیده شروع خوبی است.",
                    "شما سرمایه‌گذاری محتاط بودید و در تصمیم‌هایتان ثبات را به هیجان ترجیح دادید.",
                    "با تکیه بر انتخاب‌های کم‌ریسک بازی خوبی کردید و نشان دادید صبر هم می‌تواند راهگشا باشد.",
                ],
            },
            "growth": {
                "label": "سرمایه‌گذار رشدگرا",
                "statements": [
                    "شما بیشتر به دنبال رشد پایدار بودید و گاهی هم سراغ فرصت‌های پرنوسان رفتید.",
                    "شما به رشد سرمایه علاقه نشان دادید و بیشتر روی فرصت‌های پایدار تمرکز کردید.",
                    "تمرکز شما روی رشد پایدار بود و هر جا لازم بود، فرصت‌های پرنوسان را هم امتحان کردید.",
                ],
            },
            "balanced": {
                "label": "سرمایه‌گذار متعادل",
                "statements": [
                    "شما میان گزینه‌های مختلف تعادل برقرار کردید و انتخاب‌هایتان را متنوع نگه داشتید.",
                    "شما به دنبال تعادل میان ریسک و بازده بودید و فرصت‌ها را با دقت انتخاب کردید.",
                    "با تنوع‌بخشی و مدیریت ریسک بازی کردید و این تعادل را تا پایان بازی حفظ کردید.",
                ],
            },
            "risk_tolerant": {
                "label": "معامله‌گر ریسک‌پذیر",
                "statements": [
                    "شما از نوسان نترسیدید و با وجود سختی هدف‌ها، سراغ فرصت‌های پرریسک رفتید.",
                    "شما در مواجهه با نوسان‌های بازار جسورتر عمل کردید و به دنبال فرصت‌های بیشتر بودید.",
                    "شما در نوسان‌های بازار راحت تصمیم گرفتید و در لحظه‌های پرهیجان هم تمرکزتان را حفظ کردید.",
                ],
            },
            "aggressive": {
                "label": "معامله‌گر تهاجمی",
                "statements": [
                    "شما بیشتر از همه سراغ باکس‌های پرنوسان رفتید و ریسک را پذیرفتید، حتی وقتی هدف سخت بود.",
                    "شما فعالانه به دنبال فرصت‌های پرنوسان بودید و آماده‌ی پذیرش ریسک بیشتر بودید.",
                    "شما با جسارت بازار پرنوسان را دنبال کردید و در بازی، ریسک زیاد را با تمرکز مدیریت کردید.",
                ],
            },
        },
    },
    "flow": {
        "start_by_throw": True,
        "show_instructions": False,  # the how-to page; off: players go straight to the logo intro
        # Ask for the player's name before the countdown. A name sent by a registration
        # system over the network skips the screen.
        "registration": True,
        "registration_timeout_s": 120,  # back to the attract screen if nobody types
        "name_max_chars": 20,
        "instructions_s": 9.0,
        "intro_s": 2.6,  # the Mofid logo zooms in and fades before the countdown (0 = no intro)
        "countdown_s": 3.0,
        "results_s": 25.0,  # long enough for players to take a picture of the results
        # The idle screen loops: the 13 tiles around the title, a short demonstration of a
        # round (a hit, a new box, the sizes and points, Market Rush), then today's best.
        "attract_panel_s": 8.0,  # the title and the leaderboard, each
        "attract_demo": True,
        "attract_demo_s": 14.0,
        "time_warning_s": 10,
    },
    "input": {
        # Any of: "udp_json", "tuio", "lidar". Simulation (mouse) is added with --simulate.
        "sources": ["udp_json"],
        "simulation": False,
        "latency_ms": 0,
        "ball_radius_cm": 4,
        "merge_window_ms": 250,
        "merge_radius_cm": 35,
        "rebound_window_ms": 600,
        "rebound_dx_cm": 40,
        "heartbeat_timeout_s": 0,
        "calibration": {
            "swap_xy": False,
            "flip_x": False,
            "flip_y": False,
            "scale_x": 1.0,
            "scale_y": 1.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
        },
        "udp_json": {"host": "0.0.0.0", "port": 9100, "units": "normalized"},
        "tuio": {"host": "0.0.0.0", "port": 3333},
        # The STL27L lidar on a USB serial port, read inside the game (market_pulse/input/lidar).
        "lidar": {
            "port": "COM14",
            "baud": 921600,
            # Tuning made with the lidar tool (LIdar/use_lib.py --settings this file): area,
            # background gap, ball mode, delay after a hit. Relative to the game folder.
            "settings_file": "config/lidar.json",
            "max_range_mm": 8000,
            "wall_margin_cm": 15,  # hits this far outside the wall still count (at the edge)
            "round_only": True,  # hits count only while a round is played; the start comes from the phone page
            # Written by F2 then C: [{"wall_cm": [x, y], "lidar_mm": [x, y]}, ...]
            "calibration_points": [],
        },
    },
    "operator": {
        "start_keys": ["space", "return", "enter", "page down", "right"],
        "double_press_to_quit": True,
    },
    "audio": {"enabled": True, "volume": 0.8},
    "results": {
        "log_file": "data/results.jsonl",
        "webhook_url": "",
        "webhook_timeout_s": 4.0,
        "leaderboard_size": 5,
        "show_names": True,
        # False (MOFID_UI v4.1, 08 RESULTS): the results are type only, no photo or replay.
        # True: with camera.enabled, the profile card with the player's replay.
        "show_photo": False,
    },
    # The operator's phone page: name, Play, saved rounds and the CSV file.
    "web": {
        "enabled": True,
        "host": "0.0.0.0",  # reachable from phones on the same network
        "port": 8080,
        "pin": "",  # set this if other people use the same Wi-Fi: it protects the page and the names
        "title": "بازار هدف",
    },
    # The player's score replay on the results screen, from the IP camera facing the
    # players: a few seconds around one of their scoring hits, in slow motion, zooming onto
    # the face, ending on a still that is the card's photo. Needs OpenCV
    # (opencv-python-headless) and the files in models/; without them, or without the
    # camera, the screen shows a placeholder. The video stays in memory only.
    "camera": {
        "enabled": True,
        # Main stream (1920x1080) for a sharp face; the sub stream .../media/live/1/2
        # (640x360) needs less CPU but gives a small, soft picture.
        "url": "rtsp://192.168.70.66:554/media/live/1/1",
        "model_dir": "models",  # next to the game; the copy inside the .exe if missing
        # One scoring hit per round is replayed, drawn at random (more points, more likely):
        "clip_before_s": 10.0,  # seconds of the replay before the hit
        "clip_after_s": 2.0,  # and after it: the reaction
        "clip_fps": 25,  # frames recorded per second (the camera sends about 25-30)
        "slow_motion": 0.3,  # speed of the throw and the hit in the replay (1 = no slow motion)
        "delay_s": 0.5,  # how far the camera's picture is behind the wall's sensor
        "samples_per_s": 2,  # frames looked at per second to follow the player
        # Where the player's feet are in the picture: [left, top, right, bottom] as
        # fractions of the frame. People outside it count for a third.
        "play_zone": [0.0, 0.0, 1.0, 1.0],
        "min_person_height": 0.3,  # of the frame height; smaller people are too far away
        "min_sharpness": 60,  # of the face (variance of the Laplacian); the replay ends on the sharpest
        "person_confidence": 0.4,
        # A random look for the still at the end of the replay on some rounds, so the cards
        # people share differ:
        # natural, duotone (brand blue and turquoise), mono, warm, pop.
        "effect_chance": 0.6,  # 0 = always the plain picture
        "photo_effects": ["duotone", "mono", "warm", "pop"],
    },
    "ui": {
        "digits": "fa",
        "target_shadow": True,
        "combo_banner": True,
    },
    "fonts": {
        # First file found in assets/fonts wins. The brand book names Dana for
        # main titles and IRANSans for subtitles, body text and numbers.
        "title": ["Dana-Black.ttf", "Dana-Bold.ttf", "Dana-DemiBold.ttf", "IRANSansX-Black.ttf"],
        "heading": ["IRANSansX-ExtraBold.ttf", "IRANSansX-Bold.ttf"],
        "bold": ["IRANSansX-Bold.ttf"],
        "body": ["IRANSansX-Medium.ttf", "IRANSansX-Regular.ttf"],
        "numbers": ["IRANSansX-Black.ttf", "IRANSansX-ExtraBold.ttf"],
        # English (operator badges, "STEADY PROGRESS"): Helvetica / Myriad Pro per
        # the brand book; Arial is the closest font shipped with Windows.
        "latin": ["Helvetica.ttf", "HelveticaNeue.ttf", "MyriadPro-Regular.otf", "sys:helvetica", "sys:arial",
                  "IRANSansX-Medium.ttf"],
    },
    "text": {
        "title": "نبض بازار",
        "subtitle": "بازی تعاملی گروه مالی مفید",
        "start": "شروع",
        "cta_throw": "برای شروع، توپ را به این مربع بزنید",
        "cta_operator": "برای شروع آماده شوید",
        "next_player": "نوبت",
        "how_to_title": "راهنمای بازی",
        "rule_balls": "{balls} توپ و {seconds} ثانیه فرصت دارید",
        "rule_time": "{seconds} ثانیه فرصت دارید",
        "rule_rush": "هر برخورد به باکس کوچک، «طوفان بازار» را شروع می‌کند: {seconds} ثانیه با سرعت و باکس بیشتر و امتیاز {m}",
        "register_title": "ثبت‌نام بازیکن",
        "register_prompt": "نام خود را وارد کنید",
        "register_placeholder": "نام شما",
        "register_empty": "لطفاً نام خود را وارد کنید",
        "register_start": "شروع بازی",
        "register_hint": "بعد از نوشتن نام، Enter را بزنید یا توپ را به این مربع بزنید",
        "rule_positive": "همه‌ی باکس‌ها امتیاز مثبت دارند؛ امتیاز منفی وجود ندارد",
        "rule_move": "بعد از هر برخورد، محصول تازه‌ای جای باکس را می‌گیرد و همه‌ی باکس‌ها جابه‌جا می‌شوند",
        "rule_combo": "برخوردهای پشت‌سرهم، ضریب امتیاز را تا {max} بالا می‌برند",
        "rule_size": "امتیاز هر محصول را اندازه‌ی باکس تعیین می‌کند",
        "points_unit": "امتیاز",
        # the three sizes as the design names them (MOFID_UI v4.1): big, medium, small box
        "tier_medium": "باکس بزرگ",
        "tier_small": "باکس متوسط",
        "tier_tiny": "باکس کوچک",
        "tiny_rush": "+ طوفان بازار",
        "attract_tag": "13 PRODUCTS · ONE MARKET",
        "demo_title": "چطور بازی کنیم؟",
        "demo_hit": "توپ را به محصول‌ها بزنید",
        "demo_new": "محصول تازه جای آن را می‌گیرد",
        "demo_sizes": "باکس کوچک‌تر، امتیاز بیشتر",
        "demo_rush": "باکس کوچک، «طوفان بازار» را شروع می‌کند",
        "loading_market": "LOADING THE MARKET · 13 PRODUCTS",
        "portfolio": "سبد محصولات شما",
        "rush_title": "طوفان بازار",
        "rush_start": "طوفان بازار! امتیاز {m}",
        "rush_end": "بازار آرام شد",
        "rush_hud": "طوفان بازار: {seconds} ثانیه",
        "rush_pill": "طوفان {m}",
        "get_ready": "آماده باشید",
        "go": "شروع!",
        "score": "امتیاز",
        "time": "ثانیه",
        "balls": "توپ",
        "multiplier": "ضریب",
        "combo_up": "ضریب {m}!",
        "market_busier": "بازار شلوغ‌تر شد!",
        "end_title": "پایان دور!",
        "end_time": "زمان تمام شد",
        "end_balls": "توپ‌ها تمام شد",
        "results_title": "امتیاز شما",
        "results_hits": "{hits} برخورد از {throws} پرتاب",
        "results_best": "بیشترین ضریب",
        "results_rush": "در طوفان بازار: {hits} برخورد و {points} امتیاز",
        "profile_title": "سبک بازی شما",
        "replay_label": "تکرار صحنه",
        "replay_slow": "حرکت آهسته",
        "profile_note": "این نتیجه فقط سبک بازی شما را توصیف می‌کند و ارزیابی مالی یا توصیه‌ی سرمایه‌گذاری نیست.",
        "results_rank": "رتبه‌ی {rank} امروز",
        "results_next": "نفر بعدی، آماده شوید",
        "well_done": "آفرین {name}!",
        "leaderboard": "برترین‌های امروز",
        "leaderboard_empty": "اولین امتیاز امروز را شما ثبت کنید",
        "guest": "مهمان",
        "slogan": "پیشرفت مداوم",
        "paused": "توقف",
        "simulation": "حالت شبیه‌سازی",
        "sensor_offline": "حسگر متصل نیست",
    },
    "debug": {"overlay": False, "log_level": "INFO"},
}

# (section path, key) -> (min, max) for numeric values that must stay in range.
_RANGES: dict[tuple[str, ...], tuple[float, float]] = {
    ("display", "fps"): (10, 240),
    ("display", "windowed_height_fraction"): (0.2, 1.0),
    ("display", "scale"): (0.3, 1.0),
    ("wall", "width_cm"): (50, 5000),
    ("wall", "height_cm"): (50, 5000),
    ("round", "balls"): (0, 100),
    ("round", "duration_s"): (5, 3600),
    ("targets", "active"): (1, 13),
    ("targets", "min_tiny"): (0, 13),
    ("targets", "min_size_cm"): (1, 500),
    ("motion", "variation"): (0, 0.5),
    ("profiles", "min_hits"): (1, 100),
    ("flow", "attract_panel_s"): (2, 120),
    ("flow", "attract_demo_s"): (8, 120),
    ("motion", "speed"): (0, 5),
    ("rush", "duration_s"): (1, 600),
    ("rush", "trigger_tiny_hits"): (1, 100),
    ("rush", "max_per_round"): (0, 100),
    ("rush", "cooldown_s"): (0, 600),
    ("rush", "score_multiplier"): (1, 20),
    ("rush", "speed"): (0.1, 5),
    ("rush", "speed_ramp_s"): (0, 5),
    ("rush", "max_targets"): (1, 13),
    ("rush", "spawn_interval_s"): (0.2, 60),
    ("rush", "spawn_lifetime_s"): (0.5, 600),
    ("rush", "transition_s"): (0, 5),
    ("flow", "registration_timeout_s"): (5, 3600),
    ("flow", "name_max_chars"): (1, 40),
    ("flow", "intro_s"): (0, 10),
    ("audio", "volume"): (0, 1),
    ("results", "leaderboard_size"): (0, 20),
    ("web", "port"): (1, 65535),
    ("camera", "samples_per_s"): (0.2, 10),
    ("camera", "clip_before_s"): (2.0, 20.0),
    ("camera", "clip_after_s"): (0.5, 5.0),
    ("camera", "clip_fps"): (5, 30),
    ("camera", "slow_motion"): (0.1, 1.0),
    ("camera", "delay_s"): (0.0, 3.0),
    ("camera", "min_person_height"): (0.0, 1.0),
    ("camera", "min_sharpness"): (0, 100000),
    ("camera", "person_confidence"): (0.05, 0.95),
    ("camera", "effect_chance"): (0.0, 1.0),
}


class Section:
    """Read-only attribute access over a nested dict: ``cfg.round.balls``."""

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        try:
            value = self._data[name]
        except KeyError:
            raise AttributeError(name) from None
        return Section(value) if isinstance(value, dict) else value

    def __getitem__(self, name: str) -> Any:
        return self.__getattr__(name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("settings are read-only; edit config/settings.json")

    def get(self, name: str, default: Any = None) -> Any:
        try:
            return self.__getattr__(name)
        except AttributeError:
            return default

    def raw(self) -> dict[str, Any]:
        return self._data


def layout_left(cfg: Section) -> float:
    """Where the 3:4 layout of the menus starts on the wall (cm): 0, or the margin on each side
    when display.fill_screen has made the wall wider (App.fit_wall)."""
    wall = cfg.wall
    return (float(wall.width_cm) - float(wall.get("layout_width_cm", wall.width_cm))) / 2


def layout_top(cfg: Section) -> float:
    """The same from the top: the margin above and below when the wall was made taller (a portrait screen)."""
    wall = cfg.wall
    return (float(wall.height_cm) - float(wall.get("layout_height_cm", wall.height_cm))) / 2


def _type_ok(default: Any, value: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str) or (default == "auto" and isinstance(value, list))
    if isinstance(default, list):
        return isinstance(value, list) or value == "auto"  # display.resolution
    return True


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _merge(base: dict, override: dict, path: tuple[str, ...], warnings: list[str]) -> None:
    for key, value in override.items():
        where = ".".join(path + (key,))
        if key.startswith("_"):
            continue  # "_comment" keys are allowed in the JSON
        if key not in base:
            warnings.append(f"unknown setting '{where}' (ignored)")
            continue
        default = base[key]
        if isinstance(default, dict):
            if isinstance(value, dict):
                _merge(default, value, path + (key,), warnings)
            else:
                warnings.append(f"'{where}' must be an object; using default")
        elif _type_ok(default, value):
            base[key] = value
        else:
            warnings.append(f"'{where}' has the wrong type ({type(value).__name__}); using default {default!r}")


def _validate(cfg: dict, warnings: list[str]) -> None:
    for (section, key), (lo, hi) in _RANGES.items():
        value = cfg[section][key]
        if not lo <= value <= hi:
            clamped = min(max(value, lo), hi)
            warnings.append(f"'{section}.{key}'={value} is outside {lo}..{hi}; using {clamped}")
            cfg[section][key] = clamped

    rd = cfg["round"]
    if not rd["time_limit"] and not (rd["ball_limit"] and rd["balls"] > 0):
        warnings.append("'round': no time limit and no ball limit, so a round would never end; using the time limit")
        rd["time_limit"] = True

    res = cfg["display"]["resolution"]
    if res == "auto":
        cfg["display"]["resolution"] = [0, 0]
    elif not (isinstance(res, list) and len(res) == 2
              and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in res)):
        warnings.append(f"'display.resolution' must be \"auto\" or [width, height]; got {res!r}, using auto")
        cfg["display"]["resolution"] = [0, 0]

    for tier in TIERS:
        t = cfg["targets"][tier]
        if t["size_cm"] <= 0:
            warnings.append(f"'targets.{tier}.size_cm' must be positive; using default")
            t["size_cm"] = DEFAULTS["targets"][tier]["size_cm"]
        if t["points"] < 0:
            warnings.append(f"'targets.{tier}.points' cannot be negative (the scenario has no penalties); using 0")
            t["points"] = 0
        if t["weight"] < 0:
            warnings.append(f"'targets.{tier}.weight' cannot be negative; using 0")
            t["weight"] = 0
        m = cfg["motion"][tier]
        if m["pattern"] not in ("still", "sway_x", "sway_y", "orbit", "figure8", "volatile"):
            warnings.append(f"'motion.{tier}.pattern' {m['pattern']!r} is unknown; using 'still'")
            m["pattern"] = "still"
        if m["period_s"] <= 0 or m["pulse_period_s"] <= 0:
            warnings.append(f"'motion.{tier}' periods must be positive; using defaults")
            m["period_s"] = DEFAULTS["motion"][tier]["period_s"]
            m["pulse_period_s"] = DEFAULTS["motion"][tier]["pulse_period_s"]
        m["pulse"] = min(max(m["pulse"], 0.0), 0.5)
    if not any(cfg["targets"][tier]["weight"] > 0 for tier in TIERS):
        warnings.append("'targets.*.weight' are all 0; using the defaults")
        for tier in TIERS:
            cfg["targets"][tier]["weight"] = DEFAULTS["targets"][tier]["weight"]

    products, seen = [], set()
    for p in cfg["products"] if isinstance(cfg["products"], list) else []:
        if (isinstance(p, dict) and isinstance(p.get("key"), str) and p["key"] and p["key"] not in seen
                and isinstance(p.get("name"), str) and isinstance(p.get("file"), str)):
            aspect = p.get("aspect", 1.0)
            aspect = float(aspect) if _number(aspect) and 0.3 <= aspect <= 3.0 else 1.0
            products.append({"key": p["key"], "name": p["name"], "file": p["file"], "aspect": aspect})
            seen.add(p["key"])
        else:
            warnings.append(f"ignored invalid product {p!r} (needs key, name and file)")
    if not products:
        warnings.append("'products' has no valid product; using the 13 Mofid products")
        products = copy.deepcopy(PRODUCTS)
    cfg["products"] = products
    if cfg["targets"]["active"] > len(products):
        warnings.append(f"'targets.active'={cfg['targets']['active']} but there are {len(products)} products; "
                        f"using {len(products)}")
        cfg["targets"]["active"] = len(products)
    cfg["targets"]["active"] = int(cfg["targets"]["active"])

    rush = cfg["rush"]
    tiers = [c for c in rush["spawn_tiers"] if c in TIERS]
    if not tiers:
        warnings.append("'rush.spawn_tiers' has no valid sizes (medium, small, tiny); using the default")
        tiers = list(DEFAULTS["rush"]["spawn_tiers"])
    rush["spawn_tiers"] = tiers

    prof = cfg["profiles"]
    tiers = prof["score_tiers"]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in tiers) or tiers != sorted(tiers):
        warnings.append("'profiles.score_tiers' must be ascending numbers; using the default")
        prof["score_tiers"] = list(DEFAULTS["profiles"]["score_tiers"])
    for key, default in DEFAULTS["profiles"]["styles"].items():
        style = prof["styles"][key]
        said = style["statements"]
        if not said or not all(isinstance(s, str) and s.strip() for s in said):
            warnings.append(f"'profiles.styles.{key}.statements' needs at least one sentence; using the default")
            style["statements"] = list(default["statements"])

    levels = []
    for lv in cfg["combo"]["levels"]:
        if (
            isinstance(lv, dict)
            and isinstance(lv.get("after_hits"), int)
            and isinstance(lv.get("multiplier"), (int, float))
            and lv["after_hits"] >= 1
            and lv["multiplier"] >= 1
        ):
            levels.append({"after_hits": lv["after_hits"], "multiplier": lv["multiplier"]})
        else:
            warnings.append(f"ignored invalid combo level {lv!r}")
    cfg["combo"]["levels"] = sorted(levels, key=lambda lv: lv["after_hits"])

    for name in cfg["input"]["sources"]:
        if name not in ("udp_json", "tuio", "lidar"):
            warnings.append(f"unknown input source {name!r} (known: udp_json, tuio, lidar)")
    if cfg["input"]["udp_json"]["units"] not in ("normalized", "cm", "m"):
        warnings.append("'input.udp_json.units' must be normalized, cm or m; using normalized")
        cfg["input"]["udp_json"]["units"] = "normalized"
    cam = cfg["camera"]
    zone = cam["play_zone"]
    if not (len(zone) == 4 and all(_number(v) and 0 <= v <= 1 for v in zone) and zone[0] < zone[2] and zone[1] < zone[3]):
        warnings.append("'camera.play_zone' must be [left, top, right, bottom] fractions of the picture; using the whole picture")
        cam["play_zone"] = list(DEFAULTS["camera"]["play_zone"])
    known = ("natural", "duotone", "mono", "warm", "pop")
    bad = [e for e in cam["photo_effects"] if e not in known]
    if bad:
        warnings.append(f"'camera.photo_effects' has unknown looks {bad} (known: {', '.join(known)}); they are ignored")
        cam["photo_effects"] = [e for e in cam["photo_effects"] if e in known]
    if cfg["ui"]["digits"] not in ("fa", "latin"):
        cfg["ui"]["digits"] = "fa"


def load(path: Path | None) -> tuple[Section, list[str]]:
    """Return the merged settings and a list of human-readable problems."""
    cfg = copy.deepcopy(DEFAULTS)
    warnings: list[str] = []
    if path is not None:
        if path.is_file():
            try:
                user = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                warnings.append(f"could not read {path.name}: {exc}; using built-in defaults")
                user = {}
            if isinstance(user, dict):
                _merge(cfg, user, (), warnings)
            else:
                warnings.append(f"{path.name} must contain a JSON object; using built-in defaults")
        else:
            warnings.append(f"{path} not found; using built-in defaults")
    _validate(cfg, warnings)
    return Section(cfg), warnings  # main.py logs the warnings once logging is set up


def write_defaults(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
