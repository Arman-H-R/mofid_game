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

# The three box sizes. Each product has its own size ("tier") and its own points: the 10-point
# products come in the big box, the 15-point ones in the medium box, the 30-point ones in the
# small box. A hit on a small (tiny) box starts Market Rush.
TIERS = ("medium", "small", "tiny")
COLORS = TIERS  # the old name (green, blue, red boxes), still imported by the design-kit tools

# The 9 product tiles in the game (assets/products, made from the product icons by
# tools/import_product_icons.py). RETIRED lists the products taken out.
# "name" is the label shown with the points after a hit; "tier" the box size; "points" a hit's
# base points (set per product on the settings page).
# "aspect" is the tile's height / width: the hit area is the tile itself.
PRODUCTS: list[dict[str, Any]] = [
    # 10 points, the big box: the main Mofid products
    {"key": "easytrader", "name": "ایزی تریدر", "file": "easytrader.png", "aspect": 1.0, "tier": "medium", "points": 10},
    {"key": "mofidapp", "name": "مفید اپ", "file": "mofidapp.png", "aspect": 1.0, "tier": "medium", "points": 10},
    {"key": "boursview", "name": "بورس ویو ۲.۰", "file": "boursview.png", "aspect": 1.0, "tier": "medium", "points": 10},
    # 15 points, the medium box: the second layer
    {"key": "bimeh", "name": "بیمه زندگی مفید", "file": "bimeh.png", "aspect": 1.0, "tier": "small", "points": 15},
    {"key": "amoozesh", "name": "آموزش مفید", "file": "amoozesh.png", "aspect": 1.0, "tier": "small", "points": 15},
    {"key": "jetcard", "name": "جت کارت", "file": "jetcard.png", "aspect": 1.0, "tier": "small", "points": 15},
    # 30 points, the small box: the smart achievements (a hit starts Market Rush)
    {"key": "atiyeh", "name": "آتیه", "file": "atiyeh.png", "aspect": 1.0, "tier": "tiny", "points": 30},
    {"key": "ayar", "name": "عیار", "file": "ayar.png", "aspect": 1.0, "tier": "tiny", "points": 30},
    {"key": "jetpay", "name": "جت پی", "file": "jetpay.png", "aspect": 1.0, "tier": "tiny", "points": 30},
]


# A product's own points by its size, when it has none (targets.points_by_size off; MOFID_UI v11
# sets the points by size instead: targets.<size>.points, +100 / +150 / +300)
PRODUCT_POINTS = {"medium": 10, "small": 15, "tiny": 30}


def products_of(raw: dict, tier: str) -> list[str]:
    """The products shown in boxes of this size (every product, with points_by_size)."""
    if by_size(raw):
        return [p["key"] for p in raw["products"]]
    return [p["key"] for p in raw["products"] if p.get("tier", "medium") == tier]


def points_of(raw: dict, key: str | None, tier: str) -> int:
    """A hit's base points: the size's (points_by_size), else the product's own, else the size's."""
    if by_size(raw):
        return int(raw["targets"][tier]["points"])
    for p in raw["products"]:
        if p["key"] == key and "points" in p:
            return int(p["points"])
    return int(raw["targets"][tier]["points"])


def by_size(raw: dict) -> bool:
    """MOFID_UI v11: the box's size sets the score (+100 / +150 / +300) and any product may show
    at any size (targets.points_by_size). Off: every product its own size and points."""
    return bool(raw.get("targets", {}).get("points_by_size", False))


def tier_points(raw: dict, tier: str) -> int:
    """The points shown for a size (how-to-play, the settings preview): its products' highest."""
    if by_size(raw):
        return int(raw["targets"][tier]["points"])
    pts = [int(p["points"]) for p in raw["products"] if p.get("tier", "medium") == tier and "points" in p]
    return max(pts) if pts else int(raw["targets"][tier]["points"])


def product_for(raw: dict, tier: str, i: int = 0) -> str:
    """The i-th product of a size (in turn), for the demos; any product if the size has none."""
    keys = products_of(raw, tier) or [p["key"] for p in raw["products"]] or ["atiyeh"]
    if by_size(raw):  # every size shows every product: each size starts at its own one
        i += 3 * TIERS.index(tier) if tier in TIERS else 0
    return keys[i % len(keys)]

# Products taken out of the game (2026-11-09): left out even when a settings.json lists them.
# The most boxes on the wall at once (targets.active, targets.grow_to, rush.max_targets). Above
# the number of products, a product comes up twice.
MAX_BOXES = 20
RETIRED = frozenset({"gold", "hamnag", "tavan", "hami", "boursview2", "jet"})  # طلای گرمی، همنگ، توان، حامی، ...

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
        # After a hit: false, the other boxes stay where they are and the new one takes a free
        # spot (as in the reference clip); true, every box glides to a new spot (min_move_cm).
        "move_on_hit": True,  # MOFID_UI v11: after every hit all boxes move
        "spawn_s": 0.32,  # s, a new box dropping in (or popping up, with spawn_drop_cm 0)
        # A new box drops in from this far above its place, with a small bounce (0: it pops up
        # from small). Unlike a hit box, which shatters: the two never look alike.
        "spawn_drop_cm": 0,  # MOFID_UI v11: a new box scales 0 -> 100 % inside its corner ticks
        # After a hit the new box appears this much later, when the hit box has shattered: no
        # new box shows while the old one breaks, so nobody takes one for the other.
        "spawn_delay_s": 0.4,
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
    # the smaller the box, the more points ("points" here only for a product without its own).
    # "weight" is how often each size is drawn for a new
    # box; min_tiny keeps at least that many tiny boxes up (with 3 or more boxes), so Market
    # Rush can always be reached.
    "targets": {
        "active": 6,
        # More boxes as the round runs: it starts with "active" boxes and gets one more at a time,
        # evenly over the round (the time, or the balls without a time limit), up to grow_to.
        # 6 -> 9 in 60 s: a 7th box at 15 s, an 8th at 30 s, a 9th at 45 s (every product once).
        # 0 (or not above active): no growth.
        "grow_to": 9,
        # MOFID_UI v11: the size sets the score, any product at any size (false: each product
        # keeps its own size and points from "products")
        "points_by_size": True,
        "medium": {"size_cm": 54, "points": 100, "weight": 3},
        "small": {"size_cm": 42, "points": 150, "weight": 3},
        "tiny": {"size_cm": 29, "points": 300, "weight": 2},
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
        # Slow and small, as in the reference clip: each box floats a few cm in place.
        # enabled false keeps them still.
        "medium": {"pattern": "sway_y", "amplitude_cm": 3, "period_s": 8.0, "pulse": 0.0, "pulse_period_s": 2.0},
        "small": {"pattern": "sway_y", "amplitude_cm": 2.5, "period_s": 7.2, "pulse": 0.0, "pulse_period_s": 2.6},
        "tiny": {"pattern": "sway_y", "amplitude_cm": 2, "period_s": 6.4, "pulse": 0.02, "pulse_period_s": 3.2},
    },
    # Add-on 1.2: after N consecutive hits, the following hits use this multiplier.
    # A miss resets it to x1. Nothing is ever subtracted.
    # MOFID_UI v11: no multiplier, every hit scores only its size's points (off).
    "combo": {
        "enabled": False,
        "levels": [
            {"after_hits": 2, "multiplier": 2},
            {"after_hits": 4, "multiplier": 3},
            {"after_hits": 6, "multiplier": 4},
        ],
    },
    # Market Rush: one high-intensity phase, started by hitting tiny boxes.
    # Tiny boxes that are merely on the wall never start or extend it.
    # MOFID_UI v11: no Market Rush, a simple game of aiming at the boxes (off).
    "rush": {
        "enabled": False,
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
        # The idle screen loops: the 10 tiles around the title, a short demonstration of a
        # round (a hit, a new box, the sizes and points, Market Rush), then today's best.
        "attract_panel_s": 8.0,  # the title and the leaderboard, each
        "attract_demo": True,
        "attract_demo_s": 14.0,
        "time_warning_s": 10,
    },
    "input": {
        # Any of: "udp_json", "tuio", "lidar", "camera". Simulation (mouse) is added with --simulate.
        # "lidar" and "camera" together: both find the hits and input.fusion makes one hit of each throw.
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
        # The IP camera facing the wall finds the ball hits (market_pulse/input/wallcam). camtool.py
        # tests it step by step (view, calibrate, touch, hits); F2 then K in the game calibrates it with markers.
        "camera": {
            "source": "ws",  # ws: the camera's own web stream (lowest delay); rtsp: any camera, at "url"
            "host": "192.168.70.66",
            # main 1920x1080 at a steady 30 fps (the Witness camera's sub stream, 640x360, drops to ~25 fps
            # with gaps); process_width makes every frame that wide as it arrives, so it costs no more
            "stream": "main",
            "process_width": 640,  # 0: the frames as the camera sends them
            "url": "",
            "delay_ms": 60,  # camera -> PC for its fastest frames; taken off every frame's time
            "calibration_file": "config/camera_calibration.json",  # written by F2 then K, or camtool.py calibrate
            "mode": "ball",  # ball: thrown balls hit; touch: a hand held still on the wall (for testing)
            "ball_cm": 8,  # ball diameter
            "ball_tone": "any",  # any | light | dark: the ball against the wall (light: dark sleeves never count)
            "threshold": 18,  # grey-level change that counts as new (higher: less noise, misses dark balls)
            "min_speed": 120,  # cm/s: slower things are not throws
            "turn_deg": 50,  # a bend this sharp in the ball's path is the hit
            "bend_deg": 32,  # ...or this much between the straight lines before and after (a ball thrown high)
            # a softer turn or bend (under strong_deg) waits up to wait_frames frames for a sharper one
            "strong_deg": 70,
            "wait_frames": 2,
            # the ball's width at the hit, x a ball's width on the wall there (a dark ball is measured small)
            "size_min": 0.4,
            "size_max": 1.6,
            "depth_rise": 0.12,
            "margin_cm": 25,  # hits this far outside the wall still count (at the edge)
            "floor_margin_cm": 0,  # ...but below the picture's bottom edge only this far (the floor)
            "color": False,  # what is new: colour and brightness (on), or brightness alone (off)
            "end_is_hit": False,  # a fast ball that vanishes on the wall counts at its last place
            "round_only": True,  # hits count only while a round is played; the start comes from the phone page
            "markers": [5, 4],  # columns, rows of the F2 calibration markers
            # The real size of the game's picture on the wall, measured with a tape (cm), when it is not
            # the wall's size above (a projector). 0: the same as the wall.
            "screen_width_cm": 0,
            "screen_height_cm": 0,
            "ignore_screen": True,  # with markers the camera sees the game: what the game draws is no ball
        },
        # Camera + LiDAR together (market_pulse/input/fusion.py): each sensor reports every ball it
        # sees; one hit per throw. A LiDAR hit counts at once with its coordinates; a camera hit waits
        # up to window_ms for a matching LiDAR hit (then the LiDAR's coordinates) and otherwise counts
        # with its own. A sensor that is missing, disconnected or not ready is never waited for.
        "fusion": {
            "enabled": True,  # False: every sensor's hits count on their own (no matching)
            "window_ms": 150,  # how long a camera hit waits for the LiDAR before it counts with its own point
            # the two sensors' contact times of one throw are at most this far apart (the LiDAR stamps a
            # hit 0.07-0.2 s after the camera: keep this above that, whatever the wait above)
            "match_time_ms": 300,
            "match_radius_cm": 60,  # the two sensors' points of one throw are at most this far apart
            "keep_ms": 1000,  # after a hit, a late detection of the same throw is absorbed this long
            # camera hits nobody else saw: always count | lidar_offline: only when the LiDAR is not
            # working (a camera that sees false balls) | never
            "camera_alone": "always",
            "offline_after_ms": 1000,  # a sensor with no data for this long is not waited for
        },
    },
    "operator": {
        "start_keys": ["space", "return", "enter", "page down", "right"],
        "double_press_to_quit": True,
        # True: F5 opens the settings page in a window of its own (on another display when there
        # is one) and the game keeps showing in its window. False: the page covers the game.
        "settings_window": True,
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
        # MOFID_UI v11, 07 RESULTS: lottery points, this many for every full 1000 points (0: not shown)
        "lottery_per_1000": 2,
    },
    # The operator's phone page: name, Play, saved rounds and the CSV file.
    "web": {
        "enabled": True,
        "host": "0.0.0.0",  # reachable from phones on the same network
        "port": 8080,
        "pin": "",  # set this if other people use the same Wi-Fi: it protects the page and the names
        "title": "پرتاب هدفمند",
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
        # a hit: "rings" (MOFID_UI v11: rings open with the product and its points) or "shatter"
        # (the box breaks into four pieces and only its points float up)
        "hit_style": "rings",
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
        "title": "پرتاب هدفمند",  # MOFID_UI v11: «چالش تعاملی / پرتاب هدفمند»
        "subtitle": "چالش تعاملی",
        "how_to_sub": "باکس‌ها را هدف بگیرید",
        "balls_unlimited": "توپ نامحدود",
        "results_lottery": "امتیاز شما در قرعه‌کشی",
        "results_lottery_rule": "هر ۱۰۰۰ امتیاز معادل {n} امتیاز قرعه‌کشی",
        "results_hits_ok": "برخورد موفق",
        "demo_tiny": "باکس کوچک: بیشترین امتیاز",
        "start": "شروع",
        "cta_throw": "برای شروع، توپ را به این مربع بزنید",
        "cta_operator": "برای شروع آماده شوید",
        "next_player": "نوبت",
        "how_to_title": "راهنمای بازی",
        "rule_balls": "{balls} توپ و {seconds} ثانیه فرصت دارید",
        "rule_time": "{seconds} ثانیه فرصت دارید؛ تعداد توپ نامحدود است",
        "rule_rush": "هر برخورد به باکس کوچک، «طوفان بازار» را شروع می‌کند: {seconds} ثانیه با سرعت و باکس بیشتر و امتیاز {m}",
        "register_title": "ثبت‌نام بازیکن",
        "register_prompt": "نام خود را وارد کنید",
        "register_placeholder": "نام شما",
        "register_empty": "لطفاً نام خود را وارد کنید",
        "register_start": "شروع بازی",
        "register_hint": "بعد از نوشتن نام، Enter را بزنید یا توپ را به این مربع بزنید",
        "rule_positive": "همه‌ی باکس‌ها امتیاز مثبت دارند؛ امتیاز منفی وجود ندارد",
        "rule_move": "بعد از هر برخورد، همه‌ی باکس‌ها جابه‌جا می‌شوند",
        "rule_combo": "برخوردهای پشت‌سرهم، ضریب امتیاز را تا {max} بالا می‌برند",
        "rule_size": "باکس‌های کوچکتر، امتیاز بیشتر",
        "points_unit": "امتیاز",
        # the three sizes as the design names them (MOFID_UI v4.1): big, medium, small box
        "tier_medium": "باکس بزرگ",
        "tier_small": "باکس متوسط",
        "tier_tiny": "باکس کوچک",
        "tiny_rush": "+ طوفان بازار",
        "attract_tag": "GROUP MOFID · INTERACTIVE CHALLENGE",
        "demo_title": "چطور بازی کنیم؟",
        "demo_hit": "توپ را به محصول‌ها بزنید",
        "demo_new": "محصول تازه جای آن را می‌گیرد",
        "demo_sizes": "باکس‌های کوچکتر، امتیاز بیشتر",
        "demo_rush": "باکس کوچک، «طوفان بازار» را شروع می‌کند",
        "loading_market": "GET READY · 9 PRODUCTS",
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
        "end_title": "پایان",
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
    ("targets", "active"): (1, MAX_BOXES),
    ("targets", "min_tiny"): (0, len(PRODUCTS)),
    ("targets", "grow_to"): (0, MAX_BOXES),
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
    ("rush", "max_targets"): (1, MAX_BOXES),
    ("rush", "spawn_interval_s"): (0.2, 60),
    ("rush", "spawn_lifetime_s"): (0.5, 600),
    ("rush", "transition_s"): (0, 5),
    ("layout", "spawn_delay_s"): (0, 3),
    ("layout", "spawn_drop_cm"): (0, 60),
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
    listed = cfg["products"] if isinstance(cfg["products"], list) else []
    if listed and not any(isinstance(p, dict) and "tier" in p for p in listed):
        # a list from before the products had their own size and points: the 9 products now
        warnings.append(f"'products' is an older list; using the {len(PRODUCTS)} Mofid products")
        listed = copy.deepcopy(PRODUCTS)
    for p in listed:
        if isinstance(p, dict) and p.get("key") in RETIRED:  # an older settings.json still lists it
            warnings.append(f"product {p['key']!r} is no longer in the game; left out")
            continue
        if (isinstance(p, dict) and isinstance(p.get("key"), str) and p["key"] and p["key"] not in seen
                and isinstance(p.get("name"), str) and isinstance(p.get("file"), str)):
            aspect = p.get("aspect", 1.0)
            aspect = float(aspect) if _number(aspect) and 0.3 <= aspect <= 3.0 else 1.0
            tier = p.get("tier", "medium")
            if tier not in TIERS:
                warnings.append(f"product {p['key']!r}: 'tier' must be one of {', '.join(TIERS)}; using medium")
                tier = "medium"
            # a product's own points (used with targets.points_by_size off): its size's classic value
            points = p.get("points", PRODUCT_POINTS[tier])
            if not _number(points) or points < 0:
                warnings.append(f"product {p['key']!r}: 'points' must be 0 or more; using the {tier} box's")
                points = PRODUCT_POINTS[tier]
            products.append({"key": p["key"], "name": p["name"], "file": p["file"], "aspect": aspect,
                             "tier": tier, "points": int(points)})
            seen.add(p["key"])
        else:
            warnings.append(f"ignored invalid product {p!r} (needs key, name and file)")
    if not products:
        warnings.append(f"'products' has no valid product; using the {len(PRODUCTS)} Mofid products")
        products = copy.deepcopy(PRODUCTS)
    cfg["products"] = products
    cfg["targets"]["active"] = int(cfg["targets"]["active"])
    cfg["targets"]["grow_to"] = int(cfg["targets"].get("grow_to", 0))

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
        if name not in ("udp_json", "tuio", "lidar", "camera"):
            warnings.append(f"unknown input source {name!r} (known: udp_json, tuio, lidar, camera)")
    cc = cfg["input"]["camera"]
    for key, allowed in (("source", ("ws", "rtsp")), ("mode", ("ball", "touch")), ("ball_tone", ("any", "light", "dark")),
                         ("stream", ("sub", "main"))):
        if cc[key] not in allowed:
            warnings.append(f"'input.camera.{key}' must be {' or '.join(allowed)}; using {allowed[0]}")
            cc[key] = allowed[0]
    if not (cc["process_width"] == 0 or 160 <= cc["process_width"] <= 3840):
        warnings.append("'input.camera.process_width' must be 0 or 160..3840; using 640")
        cc["process_width"] = 640
    m = cc["markers"]
    if not (len(m) == 2 and all(isinstance(v, int) and not isinstance(v, bool) and 2 <= v <= 10 for v in m)):
        warnings.append("'input.camera.markers' must be [columns, rows], 2..10 each; using [5, 4]")
        cc["markers"] = [5, 4]
    fu = cfg["input"]["fusion"]
    for key, (lo, hi) in (("window_ms", (0, 3000)), ("match_time_ms", (0, 3000)), ("match_radius_cm", (1, 500)),
                          ("keep_ms", (0, 10000)),
                          ("offline_after_ms", (100, 60000))):
        if not lo <= fu[key] <= hi:
            clamped = min(max(fu[key], lo), hi)
            warnings.append(f"'input.fusion.{key}'={fu[key]} is outside {lo}..{hi}; using {clamped}")
            fu[key] = clamped
    if fu["camera_alone"] not in ("always", "lidar_offline", "never"):
        warnings.append("'input.fusion.camera_alone' must be always, lidar_offline or never; using always")
        fu["camera_alone"] = "always"
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
