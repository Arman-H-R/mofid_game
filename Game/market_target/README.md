# Market Pulse · نبض بازار

An interactive ball-throwing game for a Mofid Securities (کارگزاری مفید) LED wall. Players
throw soft balls at moving coloured boxes on a 3 × 4 m wall. Python with pygame-ce, built
from *Mofid-Market-Pulse-Game-Scenario.pdf* (plus add-ons 1.1–1.4) and *Brandbook V04*.

```
python main.py --windowed --simulate     # try it with the mouse (SIMULATION, not hardware)
python main.py                           # installation mode: fullscreen, sensor input
```

---

## 0. Redesign: MOFID_UI v4.1 (2026-11-06)

The game now follows *MOFID_UI_v4.1_1.pdf*. **Where this file below still talks about green,
blue and red boxes, read the three sizes instead** (green → medium, blue → small,
red → tiny). Those sections describe the game before the redesign.

**Targets: 9 Mofid products, each with its own size and points.** The tiles are made from the
square product icons (their own colour, with the game's rounded corners) by
`tools/import_product_icons.py "<icons folder>" assets/products`, which finds each icon by the
product's Persian name. جت کارت has no icon yet, so it gets a plain tile with its name until its
icon is added and the tool is run again. The products are listed in `config.PRODUCTS` with their
size (`tier`) and points; the size stays with the points, so the smaller box is worth more:

| Points | Products | Box (`tier`) | Width |
|---|---|---|---|
| 10 | ایزی تریدر، مفید اپ، بورس ویو ۲.۰ | باکس بزرگ (`medium`) | 54 cm |
| 15 | بیمه زندگی مفید، آموزش مفید، جت کارت | باکس متوسط (`small`) | 42 cm |
| 30, and starts Market Rush | آتیه، عیار، جت پی | باکس کوچک (`tiny`) | 29 cm |

Each product's points can be changed on the settings page, tab **امتیاز محصولات** (one slider per
product); its box size stays. حامی، توان، طلای گرمی، همنگ and the older بورس ویو / مفید کارت جت tiles
are out of the game (`config.RETIRED`). A `settings.json` with the older product list (without
sizes and points) gets the 9 products, with a warning. A tile is square (aspect 1.0), and the
hit area is the tile itself.

- **`targets.active` is how many boxes are on the wall at once** (default 4, from 1 to 10).
  It is the only place that number is set. A hit box is replaced at once, so the count never
  changes outside Market Rush, and all boxes then move. The old growth to 12 boxes on a red
  hit is gone.
- **Which product and which size** comes from `game/products.py`. The size is drawn first,
  then a product of that size from a shuffled bag: none already on the wall, and not the one
  just hit. Sizes are drawn
  from a bag weighted by `targets.<size>.weight` (3 : 3 : 2). `targets.min_tiny` (1) keeps a
  tiny box up whenever there are 3 or more boxes, so Market Rush can always be reached.
- **Movement:** each size has a gentle pattern (`motion.*`), and every box gets its own speed
  and reach (`motion.variation`, ±18 %).
- **After a hit** the PDF's "rings + popup" opens where the ball hit: the **product's name**,
  `+points` and the multiplier (`Effects.badge`). Each tile shows its points under it, and
  tiny tiles keep corner ticks.
- **Market Rush** is started by a tiny-box hit (`rush.trigger_tiny_hits`,
  `rush.restart_on_tiny_hit`). It uses the same mechanics as before: 4 s, ×2, faster
  movement, extra boxes up to `rush.max_targets`. It now shows on a deep field with coral
  accents. Medium and small hits never start it.
- **Every screen is on the dark field** (#004C6A), with the PDF's palette (`theme.FIELD`,
  `MIST`, `TURQ`, `CORAL`, `DEEP`), HUD, countdown, GO, ending (the products fall into a pile)
  and results (the portfolio of products hit). Fonts stay IRANSansX: the PDF's Pinar is not in
  the project.
- **The attract screen (`ui/attract.py`) is a loop driven only by the clock.** It never
  touches the game. It plays three scenes:
  - **01:** the 10 tiles circle the title.
  - **02:** a demonstration round: a hit with its name and points, a new product, the three
    sizes and their points, and a tiny hit that starts Market Rush.
  - **03:** today's best three.

  `flow.attract_panel_s`, `flow.attract_demo` and `flow.attract_demo_s` control it.
- **Settings page (F5):** the Boxes tab starts with **تعداد باکس‌های فعال روی دیوار** (1–20; above 10
  boxes some products come up twice, each about as often), with a live row of all the products lit for the active ones. Below it are each size's width;
  the points are on the **امتیاز محصولات** tab, one slider per product. Save writes `targets.active` into `config/settings.json`, as with every other
  row.
- **More boxes as the round runs (`targets.grow_to`, default 6):** the round starts with
  `targets.active` boxes and gets one more at a time, evenly over the round, up to `grow_to`.
  4 → 6 in 60 s: a 5th box at 20 s and a 6th at 40 s. Up to 20 (`MAX_BOXES` in config.py), as is `rush.max_targets`.
  The hit margin row is on the «دور بازی» tab and the Start square's size on «نمایش». Without a time limit it goes by the balls
  thrown. A new box takes a free spot and the others stay where they are. 0 turns it off
  (F5, tab «باکس‌ها», row «باکس‌های بیشتر با گذشت زمان، تا»).
- **Results records** now hold `hits` and `points_by_tier` per size, plus `products` (hits
  per product) and `product_points` (points per product). The CSV gets the matching columns.
- **Motion from the reference clip** (rings of dots, as on a floor):
  - A new tile is born small as a thin sliver and turns to face you as a circle. It then
    morphs into the tile's rounded square while it keeps growing, and its artwork fades in
    (0.9 s, `SHOW_S`), as rings of dots close in. Then a thin halo opens.
  - Tiles float slowly a few cm in place (`motion`: `sway_y`, 2–3 cm, 6–8 s; `motion.enabled`
    false keeps them still). A resting tile also turns a little now and then (down to 93 % of
    its width, `TURN_S` 6.5 s), and a thin halo opens around it and fades every 5.5 s
    (`HALO_EVERY`), as in the clip. After a hit the other tiles stay where they are, and the new
    one takes a free spot. `layout.move_on_hit` true brings back the glide to new spots.
  - A hit tile sends rings of dots out under its badge.
  - A leaving tile (Market Rush extras) shrinks while rings of dots spread out.
  - On the results screen, «سبد محصولات شما» is a rising bar chart: one bar per product hit,
    as tall as its points, lowest on the left. Each tile pops up on a thin stem with a halo,
    and moving dots join the tiles. The photo card (`results.show_photo`) keeps its row of tiles.
- **Not updated:** the design-kit and guide generators in `tools/` (`export_design_kit.py`,
  `export_quick_guide.py`, `export_player_guide.py`, `build_svg_assets.py`) still draw the old
  coloured boxes, and so does `design/`.

## 1. The game as implemented

| Rule (scenario) | Implementation |
|---|---|
| 10 touches or 60 seconds | `round.balls`, `round.duration_s`. The game ends on the 10th touch (a hit or a miss) or when the 60 seconds run out, whichever comes first. `round.balls: 0` removes the touch limit. |
| Start with 3 boxes: green, red, blue | `round.initial` |
| Green box 55–65 cm, blue 40–50 cm, red 20–30 cm | 60 / 45 / 25 cm (the middle of each range), `targets.*.size_cm` |
| Hit → points, the box is replaced by a new one of the same colour, **all** boxes move to new random places (no fixed grid) | `rules.py` + `layout.py` (random, non-overlapping, every box moves at least `layout.min_move_cm`) |
| Red hit adds 3 boxes (one green, one blue, one red): 3 → 6 → 9 → 12 | `targets.red.spawn_on_hit`, capped at `round.max_targets` = 12. At the cap a red hit still scores. |
| Miss: no penalty, the ball is used up | Nothing is subtracted and the field does not change. |
| Hits after time runs out do not count | The hit test uses the moment the ball touched the wall (minus sensor latency), not the moment the event arrived. |
| Add-on 1.1: predictable, repeating motion | Green sways steadily left/right, blue moves in a balanced figure of eight, red moves irregularly and pulses. Configurable in `motion.*`. Dense fields shrink the motion so boxes never overlap. A pulse never makes a box smaller than `targets.min_size_cm` (20 cm). |
| Add-on 1.2: combo / multiplier | After 2 consecutive hits ×2, after 4 ×3, after 6 ×4 (`combo.levels`). A miss resets to ×1. Shown in the HUD, with a banner when the level rises. |
| Add-ons 1.3 / 1.4: registration platform, landing page | A phone page for names, Play, history and CSV is built in (section 6.1). A public landing page is web work outside this game; the hooks are a player identity in, every result out (webhook + JSONL file). See section 6.2. |

**Points are defaults to confirm.** The scenario does not fix them, only that red is
worth the most: green 10, blue 15, red 30 (`targets.*.points`). Combo thresholds are also
defaults (the add-on leaves them to the implementation).

**Screens:** the Mofid logo as the game opens → attract (logo, title, how-to or today's leaderboard, start box) →
registration (the player's name, see 1.1) → the Mofid logo intro (2.6 s, `flow.intro_s`) → 3-2-1 countdown → play
(HUD: score, timer with a 10-second warning, the player's name, touches left, multiplier, time bar) →
"round over" → results (name, score count-up, play style, hits per colour, best multiplier,
today's rank, 18 s) → attract. Feedback on every hit: brand-symbol burst, the colour's word
(رشد / تنوع سبد / نوسان), points, multiplier, a sound. A miss shows a ring where the ball
touched.

The how-to page (instructions, 9 s) is off as the game ships (`flow.show_instructions: false`); it is still in
the code and comes back between the attract screen and the registration when switched on.

**Logo intro.** Before every countdown, whichever way the round started (start box, key, phone page), the Persian
stacked logo in its brand colours (brand book page 4, `assets/brand/logo-fa-stacked.svg`) settles in on a white
dot-grid screen, half the wall wide, with the brand symbols + − × / • gathering around it. It holds for about a
second, then zooms in towards the players, faster and faster, and fades; the symbols fly out with it, and the
screen cross-fades to the countdown. The same logo is on screen while the game loads (the box sprites are built
then, so nothing stutters later) and plays its zoom when the window opens. `flow.intro_s: 0` turns both off. No
flashing; at 1536 × 2048 a frame costs about 4 ms (95 % under 12 ms).

### 1.1 Registration

A ball cannot type, so the name comes from the operator's phone (section 6.1), a keyboard at
the wall, or a registration platform (section 6.2). The phone and the platform skip this screen.

- Type the name and press Enter, click the Start button, or throw a ball at it.
- An empty name, or one with no letter or digit, cannot start: the field turns red with a
  message. Spaces and the Persian half-space are kept; symbols and emoji are not accepted.
- While this screen is open, keys type into the name instead of running operator commands.
  F4 cancels. After `flow.registration_timeout_s` with no typing it returns to the attract screen.
- `flow.registration: false` removes the screen. `results.show_names: false` hides names on the wall.

### 1.2 Items, icons and scoring

| Colour | Category | Icon (`assets/gameplay/`) | Size | Points | Movement | Extra |
|---|---|---|---|---|---|---|
| Green (turquoise) | Growth and stability | Rising bars, `target-green.svg` | 60 cm | 10 | Steady sway | |
| Blue | Balanced portfolio, risk management | Allocation ring, 50/30/20, `target-blue.svg` | 45 cm | 15 | Figure of eight | |
| Red | High volatility | Irregular price line, `target-red.svg` | 25 cm | 30 | Irregular, pulsing | Adds 3 boxes, starts Market Rush |

The table of icon, category and meaning is in `market_pulse/ui/icons.py`; points and sizes are
in `targets.*`, movement in `motion.*`. To replace an icon, edit its SVG; the game reads it directly.

### 1.3 Market Rush

| | |
|---|---|
| **Starts** | On every red hit (`rush.trigger_red_hits`: 1). Red boxes merely on the wall do nothing. The touch that ends the game starts nothing. |
| **Lasts** | `rush.duration_s` (4 s), or until the round ends if that comes first. |
| **Red hit during it** | A fresh 4 s from that hit, in the same phase, so the picture does not flash (`rush.restart_on_red_hit`). With `false`, it is ignored; `rush.max_per_round` and `rush.cooldown_s` limit how often one can start. |
| **Speed** | All boxes move at `rush.speed` (×1.6), ramping over 0.4 s and back to exactly normal when it ends. The hit test follows the same clock. |
| **Boxes** | One more every `rush.spawn_interval_s` (1 s), up to `rush.max_targets` (12). They never move or cover existing boxes. All extras leave when it ends. |
| **Score** | `rush.score_multiplier` (×2) on every hit whose *impact time* falls inside the phase, so the hit that started it is not multiplied. It multiplies with the combo (`rush.stack_with_combo`): up to ×8. Points are worked out in one place, once per ball. |
| **Picture** | Field fades to deep blue with a moving candlestick chart and price line, HUD band to purple, white edge on boxes. Fades over `rush.transition_s`; no flashing. |

### 1.4 Play style on the results screen

It describes how the round was played; it is not a financial assessment, and the screen says
so. The style comes from behaviour: the share of hits per colour and the hits during Market
Rush. The score only picks which of three sentences is shown, so a high score alone never
means a high risk appetite. In order, first match wins (`profiles.*`):

| Condition | Style |
|---|---|
| Fewer than 3 hits | ناظر صبور (watchful) |
| Red ≥ 50 % of hits | معامله‌گر تهاجمی (aggressive) |
| Red ≥ 30 %, or at least 4 hits in Market Rush making up ≥ 60 % of all hits | معامله‌گر ریسک‌پذیر (risk-tolerant) |
| Green ≥ 50 % and red ≤ 10 % | سرمایه‌گذار محتاط (cautious) |
| Green ≥ 50 % | سرمایه‌گذار رشدگرا (growth) |
| Anything else | سرمایه‌گذار متعادل (balanced) |

Sentence by score: below 90, 90 to 179, 180 and up (`profiles.score_tiers`). These ranges
come from simulated players, not real rounds. The game sees hits, not intentions: someone who
aims at red and keeps missing counts as playing what they hit.

**Starting a round:** throw a ball at the big "شروع" box on the attract screen
(`flow.start_by_throw`), or press the operator start key (Space / Enter / Page Down / → so a
presentation clicker works), or send `{"type":"start"}` over UDP.

---

## 2. Install and run

Requires Python 3.10+ (tested with 3.10.11 on Windows 11).

```bash
python -m venv .venv
```
```bash
.venv\Scripts\python -m pip install -r requirements.txt
```
(Linux: `.venv/bin/python -m pip install -r requirements.txt`.)

Use **pygame-ce**, not `pygame`: only pygame-ce shapes Persian text. If `pygame` is already
installed in the environment, `pip uninstall pygame` first.

Easiest on Windows: `run.bat` does all of this. It uses this folder's `.venv`, and if `.venv` is
missing or does not work on this PC it makes it again and installs the requirements first:

```bash
.\run.bat --windowed --simulate
```

A `.venv` copied from another PC does not work here: it fails with
`No Python at 'C:\Users\<other user>\...\python.exe'` (it points at that PC's Python). Never copy
or commit `.venv`; delete it (or let `run.bat` replace it). If the prompt still shows the old
venv's name, run `deactivate` or open a new terminal.

| Command | What it does |
|---|---|
| `python main.py` | Installation mode: fullscreen on display 0, sensor input from `config/settings.json`. |
| `python main.py --windowed` | In a window (scaled to fit the desktop, resizable). |
| `python main.py --simulate` | **SIMULATION:** mouse clicks stand in for ball impacts. A purple "SIMULATION" badge is always shown. Sensor inputs keep working at the same time. |
| `python main.py --display 1 --resolution 1536x2048` | Choose the display and the canvas size. |
| `python main.py --debug` | Diagnostics overlay on (also F3). |
| `python main.py --settings` | Only the settings page, full screen, without the game (`--windowed` for a window). Also `MarketPulse.exe --settings`. |
| `python main.py --gallery out/` | Render every screen to PNG without a window (design review, CI). |
| `python main.py --exit-after 30` | Quit after 30 s (smoke tests). |
| `python main.py --write-config` | Write a settings file with every default (will not overwrite). |
| `scripts\run_kiosk.bat` / `scripts/run_kiosk.sh` | Kiosk launcher: restarts the game if it ever exits with an error. Esc twice stops it. |

Logs: `logs/market_pulse.log` (rotating, 5 × 1 MB). Results: `data/results.jsonl`.
Screenshots (F12): `logs/screenshots/`.

### Operator keys

| Key | Action |
|---|---|
| Space, Enter, Page Down, → | Start a round / skip the instructions / resume after pause |
| Letters, Backspace, Enter | On the registration screen only: type the name, correct it, confirm |
| F4 | On the registration screen: cancel, back to the attract screen |
| P | Pause / resume (game clock and timer stop) |
| M | A ball that never reached the wall (counts as a throw, no points) |
| R | Reset: abort the round, back to the attract screen |
| F2 | Calibration view (impacts are not counted while it is open); there, **C** calibrates the LiDAR (4.3) |
| F3 | Diagnostics: FPS, state, input status, packet counts, filters, config warnings |
| F6 | LiDAR: scan the background again (keep the wall clear for 10 s) |
| F5 | Settings page: sliders and switches for boxes, points, multipliers, the round, sound, the replay, the screen and the LiDAR (section 5.1) |
| F11 | Full screen or a window, at once (F5 can save it) |
| F12 | Screenshot |
| Esc, Esc (within 1.5 s) | Quit |

---

### MarketPulse.exe (no Python needed)

The whole game in one file, for Windows 10/11 (64-bit). To build it (needs `requirements-dev.txt`):

```bash
.venv\Scripts\python tools/build_exe.py
```

The result is `dist/MarketPulse.exe`. Results and logs are written to `data/` and `logs/` next to it, and
a `config/settings.json` next to it replaces the built-in settings.

* Double-click: plays with the mouse in a window (simulation mode).
* On the wall, with the sensor: `MarketPulse.exe --fullscreen`. All the options above work the same way.

---

## 3. Display configuration

Everything is laid out in **centimetres on the wall** (`wall.width_cm` × `wall.height_cm`,
default 300 × 400), then mapped to pixels. Box sizes therefore match the scenario on any
wall resolution, and text is sized in cm too: labels use a font size of 5.5–9 cm, words and
points on hits 7–15 cm, the score and timer 31 cm, the countdown 80 cm.

* `display.resolution: "auto"` (or `[0, 0]`) renders at the display's native resolution. `[w, h]`
  renders at that size and the GPU scales it to the display (`pygame.SCALED`). With one of the
  two 0, that one follows the wall's shape (`[1200, 0]` is 1200×1600 on a 3:4 wall).
* **Nothing is ever stretched.** The wall keeps its shape with one scale for x and y
  (`Viewport`), and `pygame.SCALED` scales the canvas evenly into the screen or window. Where
  the screen's shape still differs, there are bars: the brand's deep blue (#00243D) in full screen at
  the native resolution, black around a scaled canvas (a maximized window, a `[w, h]` canvas).
  Circles stay round, and photos, the replay and text keep their shape.
* Full screen (`--fullscreen`, F11, or the F5 switch) draws at the display's own pixels, 1:1.
  A window opens at 88 % of the display's height (and never wider than the display); maximized,
  it is scaled evenly to the largest size that fits.
* `display.fill_screen: true` (set in `config/settings.json`): **the game fills the whole
  screen, still without stretching.** The wall takes the picture's shape (display.resolution,
  or the display's own): on a landscape 16:9 screen it is 711 × 400 cm instead of 300 × 400,
  on a portrait 800 × 1280 one 300 × 480 cm, on 1080 × 1920 300 × 533 cm, so the boxes spread
  over the whole screen and keep their shape. The menus (start, instructions, name, results, F5)
  keep their 3:4 layout, centred, with their background over the whole screen; the start
  square is where it is drawn (`config.layout_left`, `layout_top`). The LiDAR's crosses (F2, C)
  are then at the screen's corners: a calibration made for another shape is not used (the log
  and a notice say so), calibrate again; going back to the shape it was made for uses it again.
  Changing the size or this switch on F5 applies when the page closes: a round being played
  ends, the next one uses the new shape.
* `display.fill_screen: false`: the wall stays 3:4, with bars at the sides (or above and below
  on a portrait screen).
* Checked on the real 1920×1080 display by measuring desktop screenshots with a test circle
  and a frame on the wall's edge: full screen (fill on and off), a window, a maximized window
  (fill on and off), F11 window → full screen → window, and a 1280×1024 and a 1366×768 canvas
  in full screen. The circle was round in all (width / height 0.997–1.003), and the wall 3:4
  (0.749–0.750). With the wall in the screen's shape: full screen 1920×1080 filled edge to edge,
  F11 both ways, a maximized window 1760×991 (thin bars only because the window is not 16:9);
  the circle round in all. Other shapes (1366×768, 1280×1024, 1080×1920 and 1536×2048 displays) are
  checked in `tests/test_display.py` without the display.
* The LiDAR (4.2a) maps to wall centimetres, and the crosses of its calibration are drawn at
  wall centimetres, so it is not changed by the display mode or the canvas size; only by the
  wall's size (`fill_screen`), which asks for a new calibration.
* If the physical wall is not 3 × 4 m, set `wall.width_cm` / `wall.height_cm` to its real
  size. The layout bands (`layout.hud_height_cm` 64, `footer_height_cm` 30,
  `side_margin_cm` 12) keep boxes inside a safe area away from the edges and under the HUD.
* `display.display_index` picks the monitor or LED controller output; `display.vsync`
  falls back to no-vsync if the driver refuses it; `display.fps` caps the frame rate (60).
* Windows high-DPI scaling is disabled for the game window, so pixels map 1:1.

---

## 4. Hardware integration

**No sensor model was specified**, so the game does not assume one. It accepts the two
protocols that LED-wall ball-game sensors are normally integrated with. Each source runs on
its own thread and only queues events, so a silent, slow or broken sensor cannot block or
crash the game. A socket that fails (for example a busy port) is reported in the F3 overlay
and reopened with back-off.

### 4.1 UDP JSON (default, `input.sources: ["udp_json"]`, port 9100)

One JSON object per datagram (several separated by newlines also work). Coordinates are
normalised by default: (0, 0) is the top-left of the wall as the players see it, (1, 1) the
bottom-right. Set `input.udp_json.units` to `"cm"` or `"m"` to send wall distances instead.

```json
{"type": "impact", "x": 0.42, "y": 0.61}
{"type": "impacts", "points": [{"x": 0.42, "y": 0.61}, {"x": 0.80, "y": 0.20}]}
{"type": "throw_missed"}
{"type": "start"}
{"type": "start", "player": {"id": "1042", "name": "سارا"}}
{"type": "player", "id": "1042", "name": "سارا"}
{"type": "reset"}
{"type": "heartbeat"}
```

`"hit"` is accepted as a synonym of `"impact"`. Malformed messages are counted and logged,
never fatal. `tools/send_test_impacts.py` is a **test sender** for this protocol (not a
sensor driver) and the reference for whoever writes the sensor bridge:

```bash
python tools/send_test_impacts.py demo
```

### 4.2 TUIO 1.1 over OSC (`input.sources: ["tuio"]`, port 3333)

The standard output of most LiDAR / IR touch-wall software. `/tuio/2Dcur`, `/tuio/2Dblb` and
`/tuio/2Dobj` are read; each **new session id** (a ball crossing the sensor plane) is one
impact at its first position. Needs `python-osc`. Both sources can be enabled together.

### 4.2a STL27L LiDAR on USB (`input.sources: ["udp_json", "lidar"]`, COM14)

The LiDAR is read inside the game, with no bridge program and no network. The detection is the
one of the LiDAR tool (`LIdar/use_lib.py`), copied into `market_pulse/input/lidar/`:

1. **Background.** When the game starts (and on **F6**) the empty wall is scanned for
   `calibration_time` seconds (10). For each 0.25° slice the closest stable distance is kept.
   Keep the wall clear meanwhile; F3 shows `lidar background ... s`.
2. **New points.** A beam that ends clearly in front of the background (`bg_margin` 40 mm plus
   `bg_margin_ratio` 2 % of the distance) and inside the area is a new point.
3. **Blobs.** New points next to each other (`cluster_distance`) are one object; objects
   thinner than `min_width` or wider than `max_width` are ignored.
4. **Hit.** Ball mode: the first rotation that sees a new object is the hit, at that position,
   moved back by half of `ball_size` to the ball's centre. New hits are ignored for
   `hit_delay` seconds (the ball bouncing back or rolling).
5. **Wall.** The LiDAR's millimetres become wall centimetres through the calibration below,
   then the game's usual filters apply (merge, rebound, 4.3).

The tuning values are in `config/lidar.json`, the same format as `LIdar/settings.json` (it
started as a copy). With the game closed, tune them with the LiDAR tool:

```powershell
cd ..\..\LIdar
python use_lib.py --settings ..\Game\market_target\config\lidar.json --no-osc
```

`input.lidar` in `config/settings.json`:

| Key | Default | |
|---|---|---|
| `port`, `baud` | `COM14`, 921600 | The serial port (Device Manager, «Silicon Labs CP210x») |
| `settings_file` | `config/lidar.json` | The tuning above |
| `max_range_mm` | 8000 | Farther points are dropped |
| `wall_margin_cm` | 15 | Hits this far outside the wall still count |
| `round_only` | true | Hits count only while a round is played. The name and Start come from the phone page (6.1) |
| `calibration_points` | [] | Written by F2 then C |

**Calibration (once, and again if the LiDAR is moved):** start the game, wait for the background,
press **F2**, then **C**. A purple ring marks cross 1 (top-left); throw the ball at it or touch it.
The ring moves to top-right, bottom-right, bottom-left. **Backspace** takes the last cross again,
**C** or **Esc** cancels. After the fourth cross the game fits the LiDAR's plane to the wall
(least squares: turn, scale, shift and mirror, so the LiDAR can sit anywhere along the wall),
says how many centimetres the four hits were off on average and at most, saves the points and
uses them at once. From then on the area of the touch is the wall itself (plus `wall_margin_cm`). While the
crosses are taken the LiDAR looks in a bigger box: the tool's area made as wide as the wall's
shape and 20 % bigger all round, so the corners of a wide wall (`display.fill_screen`) are seen too.
A hit within 20 cm of a cross already taken is left out (the same spot again). From cross 2 on, a hit
must lie along the wall's edge from the cross before (right of cross 1, below cross 2, left of cross 3,
give or take 35°, with the way the wall lies taken from a calibration made before, else from the tool's
map), so someone at the side of the wall is not taken as two crosses one above the other; after 3 such
misses in a row the game says Backspace takes the last cross back. A fit that is off by more than 8 cm at a cross (a hit about 30 cm or more from its cross) is refused, not saved, and starts again from cross 1. While calibrating, hits count in the LiDAR tool's area and on the wall as calibrated before. Without calibration the area of the LiDAR
tool stands for the wall, with the top of its map at the top of the wall.

Needs `pyserial`. Without the LiDAR plugged in the game runs as usual; F3 shows
`lidar connecting  waiting for the lidar on COM14` and it connects as soon as it is plugged in.

### 4.3 Calibration, latency, de-duplication

1. Press **F2**. A centimetre grid, the play field, the start box and five red crosses
   (corners and centre) are drawn; the last 40 impacts are shown with raw and wall coordinates.
2. Throw at (or touch) the crosses and adjust `input.calibration` until the dots land on them:
   `swap_xy`, `flip_x`, `flip_y` for orientation, then `scale_x/y` and `offset_x/y`
   (wall = raw × scale + offset, normalised units). Restart the game to apply.
3. `input.latency_ms`: the sensor's delay. The hit test looks up where the boxes were at the
   moment of contact, so moving boxes are judged fairly.
4. `input.ball_radius_cm` (4; 10 in `config/settings.json`): an impact this close to a box edge still
   counts, so the hit box is the box plus this all round. F5, tab «باکس‌ها», row «حاشیه‌ی برخورد دور هر
   باکس» (0-50 cm) changes it at once; the boxes there show it as a line around each box.
5. One ball = one throw: reports within `merge_window_ms` (250) and `merge_radius_cm` (35) are
   the same contact; an impact lower on the wall within `rebound_window_ms` (600) and
   `rebound_dx_cm` (40) is the ball dropping after a hit, not a new throw.
6. `input.heartbeat_timeout_s` (0 = off): show "sensor not connected" if no packet arrives in
   time. Send `heartbeat` messages if the sensor is otherwise silent between throws.

**A wall sensor cannot see a ball that never reaches the wall.** Such a ball is counted only
if the operator presses **M** or the sensor bridge sends `throw_missed` (for example from a
throw-line sensor). Otherwise that round ends on time instead of after the 10th touch.

### 4.4 What we need from the hardware supplier

Sensor model and output (TUIO, JSON over UDP, or something else), coordinate orientation,
latency, the PC's IP and port, how a ball is reported (one event or a stream per contact),
and the LED wall's controller resolution and physical size.

---

## 5. Configuration (`config/settings.json`)

The file only needs the keys you change; missing keys use the built-in defaults
(`market_pulse/config.py`). A wrong type, an unknown key or an out-of-range value is
reported (log + F3 + a notice on screen) and replaced by the default, so a typo never stops
the installation. Keys starting with `_` are ignored (use them for comments).

| Section | Main keys |
|---|---|
| `display` | `fullscreen`, `display_index`, `resolution`, `fill_screen`, `fps`, `vsync`, `hide_cursor` |
| `wall` | `width_cm`, `height_cm` |
| `layout` | `hud_height_cm`, `footer_height_cm`, `side_margin_cm`, `gap_cm` (between boxes), `min_move_cm`, `transition_s`, `spawn_s`, `start_button` |
| `round` | `balls` (touches; 0 = no limit), `duration_s`, `initial`, `max_targets`, `end_delay_s` |
| `rush` | Market Rush: see section 1.3 |
| `profiles` | Play-style thresholds, score ranges and sentences: see section 1.4 |
| `targets` | per colour: `size_cm`, `points`, `spawn_on_hit`; `min_size_cm` |
| `motion` | `enabled`, `speed`; per colour: `pattern` (`still`, `sway_x`, `sway_y`, `orbit`, `figure8`), `amplitude_cm`, `period_s`, `pulse`, `pulse_period_s` |
| `combo` | `enabled`, `levels` (`after_hits`, `multiplier`) |
| `flow` | `start_by_throw`, `show_instructions`, `instructions_s`, `intro_s`, `countdown_s`, `results_s`, `attract_panel_s`, `time_warning_s` |
| `input` | `sources`, `simulation`, `latency_ms`, `ball_radius_cm`, merge / rebound filters, `heartbeat_timeout_s`, `calibration`, `udp_json` (`host`, `port`, `units`), `tuio` (`host`, `port`), `lidar` (4.2a) |
| `operator` | `start_keys`, `double_press_to_quit` |
| `audio` | `enabled`, `volume` |
| `results` | `log_file`, `webhook_url`, `webhook_timeout_s`, `leaderboard_size`, `show_names` |
| `web` | the phone page: `enabled`, `host`, `port`, `pin`, `title` (section 6.1) |
| `ui` | `digits` (`fa` / `latin`), `target_shadow`, `combo_banner` |
| `fonts` | font file per role, first one found wins |
| `text` | every on-screen Persian string |
| `debug` | `overlay`, `log_level` |

### 5.1 Settings page (F5, or `--settings` from the terminal)

A settings page for changing the game without editing the file. Open it on its own from a terminal,
without the game:

```powershell
python main.py --settings              # full screen; add --windowed for a window
dist\MarketPulse.exe --settings
```

There, **Save** (Ctrl+S) writes the file and the page stays open; **Esc** or the button
«خروج بدون ذخیره» ends it. With unsaved changes the first Esc only warns, and a second Esc leaves
without saving. The next start of the game uses the saved values.

Or press **F5** while the game runs, for the same page in **a window of its own** (on a second display when there is one, else in the middle of the game's display; F5 on the game brings it to the front, its close button closes it). The game keeps its own window. `operator.settings_window: false` puts the page over the game instead. The game stands still while it is open: the clock stops, throws do not
count and keys do not reach the name field. The mouse pointer shows. Seven tabs:

| Tab | What it changes |
|---|---|
| باکس‌ها (boxes) | Size (15-100 cm) and points (0-200) of the green, blue and red box; the start square's size. The three boxes are shown above the sliders at their real size on the wall (scaled down together if they do not fit). |
| ضریب‌ها (multipliers) | Hits-in-a-row multiplier on/off; for each of the three levels, after how many hits in a row and the multiplier; the Market Rush multiplier; whether the two multiply. A card shows the most one hit can score, worked out as the game does. |
| طوفان بازار (Market Rush) | On/off, length, red hits that start it, the boxes' speed, the most boxes, how often a new box comes, the rest between two rushes |
| دور بازی (round) | Round time, balls (0 = no limit), the most boxes, box movement on/off and speed, the name screen on/off, countdown, results time, the last-seconds warning |
| صدا و ویدیو (sound and video) | Volume, the boxes' shadow, the multiplier message, player names on screen; the replay's seconds before and after the score, slow-motion speed, camera delay and the chance of a colour look |
| نمایش (screen) | Full screen on/off, the game filling the whole screen on/off (never stretched, at the next start, section 3), the picture size with one click (خودکار = the display's own, landscape افقی 1920×1080 or 1280×800, portrait عمودی 1080×1920 or 800×1280), the game's width and height typed in two boxes for any other size (click the box or press Enter on its row, type the number, Enter sets it, Esc leaves it; 0 = automatic, at least 320), and the picture's scale (مقیاس تصویر, 30-100 %: the game drawn smaller in the middle of the screen with a dark border, for a projector that spills over the wall; calibrate the LiDAR again after changing it). These apply when the page closes (without the game: on Save); the tab shows the size now |
| LiDAR | The lidar tool's settings (`LIdar/settings.py`), on four pages in a second row of tabs: **ناحیه** (the area's shape, centre, radius / side / width and height, the nearest distance seen), **زاویه و آینه** (turning the map, the angle range, mirror left-right and up-down), **تشخیص** (gap from the background, extra gap far away, smallest and biggest object, join distance, one hand's radius, background scan time), **توپ و لمس** (ball or touch mode, the delay after a hit and its radius, the ball's size, and the touch mode's values). They apply in the running game at once and Save writes them into `config/lidar.json` (input.lidar.settings_file; the .exe: next to itself), not into settings.json. Only the lidar tool's own window and OSC values are left out. After changing the turn or a mirror, calibrate again (F2 then C); a new scan time applies at the next background scan (F6). There is no LiDAR tab when that file does not exist and the lidar is not an input |

Every row is a slider with **−** and **+** buttons (or a switch). Under each slider, two short lines say what its low end (کم, by −) and high end (زیاد, by +) do. Drag the slider, click −/+,
or use the mouse wheel; with the keyboard, ↑/↓ choose a row, ←/→ change it (Shift: five steps),
Enter or Space flips a switch or picks the next choice, Tab / Page Up / Page Down / 1-9 change the page. A small grey line
on each slider marks the built-in value, and a purple dot marks a value that is not saved yet.

A change takes effect in the game at once. What a round reads when it starts (box sizes, balls,
multipliers, Market Rush) applies from the next round. The buttons:

* **ذخیره (save)**, or Ctrl+S: writes the changed values into `config/settings.json` and closes.
  Only those values are written; everything else in the file stays as it is, and values set on
  the command line (such as `--windowed`) are never written. The .exe saves to
  `config/settings.json` next to itself, creating it from its built-in settings if there is none.
* **امتحان بدون ذخیره (try without saving)**, or F5 / Esc: closes and keeps the changes until the
  game is closed. A notice on the wall says how many are not saved.
* **برگرداندن تغییرها (revert)**: puts back the saved values and closes.
* **پیش‌فرض این صفحه (defaults for this page)**: the built-in values for this tab's rows (save to keep them).

Values the page does not have (the wall's size, the sensor, colours, texts) are still set in the file.

---

## 6. Phone page, registration platform and landing page hooks (add-ons 1.3 / 1.4)

### 6.1 Phone page for the operator

The game runs a small web server on the local network; nothing needs installing. When the game
starts, the wall shows `Phone page: http://<address>:8080` for 30 seconds (F3 shows it at any time,
and it is in `logs/market_pulse.log`). On a phone on the same Wi-Fi, open that address:

* **Name + "شروع بازی":** the round goes straight to the countdown on the wall, with that name.
  While a round runs Play is off, and a second player cannot replace the first. It works again on
  the results screen. The old way (a ball on the Start box, then the keyboard) still works; set
  `flow.start_by_throw: false` to start only from the phone.
* **Live status:** score, seconds and touches left, then the result. "لغو این دور" cancels a round
  (it is not saved).
* **History and CSV:** the last 30 rounds, and "دانلود فایل CSV" with every round ever saved.
  `data/results.jsonl` stays the source of truth, so the history survives restarts. The CSV (UTF-8
  with BOM, for Excel) has one row per round and one column per value: `round_id`, `date`, `time`,
  `player_id`, `player_name`, `score`, `rank_of_day`, `throws`, `total_hits`, `misses`, `hits_*`,
  `points_by_color_*`, `rush_*`, `best_streak`, `best_multiplier`, `end_reason`, `duration_s`,
  `targets_at_end`, `profile_*`, `balls`, `round_duration_s`, `timestamp`.

Settings are under `web`: `enabled`, `host`, `port`, `title` (the page's heading) and `pin`.

**The first time, Windows Firewall asks about the game: allow it**, or phones cannot connect. The
page has no password by default, so anyone on the same Wi-Fi can open it, start rounds and download
the names. On a shared network set `web.pin`: the page then asks for it once per phone. It uses plain
HTTP, so it is for a private network and not the internet.

### 6.2 Other systems

* **Player in:** before a round, the platform sends `{"type":"player","id":…,"name":…}` (or puts
  `player` inside `start`) over UDP. The name is shown on the attract and results screens and
  stored with the result, and the registration screen is skipped. Without it the player types a
  name on that screen. With `flow.registration: false` and no name the player is "مهمان" (guest).
* **Result out:** every finished round is appended to `data/results.jsonl` and, if
  `results.webhook_url` is set, POSTed there as JSON from a background thread (3 attempts; the
  local file is always written first). Aborted rounds are not recorded.

```json
{"round_id": "20261026-153012-4", "timestamp": 1792932612.4, "player_id": "1042", "player_name": "سارا",
 "score": 260, "throws": 10, "hits": {"green": 3, "blue": 2, "red": 3},
 "points_by_color": {"green": 50, "blue": 45, "red": 165}, "total_hits": 8, "best_streak": 6,
 "best_multiplier": 3, "end_reason": "balls", "duration_s": 41.2, "targets_at_end": 6,
 "rush": {"count": 2, "throws": 4, "hits": 3, "points": 140},
 "profile": {"key": "risk_tolerant", "label": "معامله‌گر ریسک‌پذیر", "statement": "…"},
 "balls": 10, "round_duration_s": 60.0}
```

The attract screen alternates between how-to-play and **today's** top 5 (from the JSONL file,
so it survives restarts). The landing page itself (intro, gameplay video, score lists, account
linking) is a separate web project; the webhook payload above is its data source.

### 6.3 Player replay on the results screen

With the camera on, the results screen is a profile card made for players to photograph and share.
On the left is a tall picture (1.32 × 2.64 m on the wall) in a thick white frame, with a turquoise
outline frame tilted behind it. The picture is a **replay of one of the player's scoring hits**,
recorded by the game during the round, and it ends on a still of their face. On the right, in one
column: the name, the score in large digits, the play style and its sentence, the hits per colour,
the best multiplier, today's rank and the Market Rush line. Under them are the note and the
next-player bar.

The card arrives in about 4 seconds, always the same way, so the order reads clearly on video:
the white frame opens from a turquoise line; the replay rises into it behind a turquoise line, like a
price going up; the turquoise outline turns out from behind the frame (and then sways very slowly
while the card is up); brand symbols (+ − × / o) leave the frame's edges. On the right, the name is
read in from the right (the way Persian is read) behind a turquoise caret; the score counts up and
gives one small beat with "+" sparks; the divider grows from the centre; the play style and then each
line of its sentence are read in; the hit cards pop in with their counts ticking up; the hits line,
pills and Market Rush line follow. Nothing flashes. Every frame is a function of the time since the
results appeared (`ui/screens.py`, `_results_card`), and the gallery has a shot half way
(`results_reveal`).

**The replay** (`market_pulse/replay.py`, timeline in `Timeline`), about 15 to 18 s:

1. The 10 s before the hit (`camera.clip_before_s`) at normal speed, with a slow push in. A small
   «تکرار صحنه» label sits at the top right.
2. About 2 s before the hit it slows to 0.3× (`camera.slow_motion`). Neighbouring frames are blended so
   the slow motion stays smooth, the edges darken, thin dark bars close in at the top and bottom
   like a film, and the label becomes «حرکت آهسته».
3. At the hit, the hit's points (for example «+۳۰») rise over the player with "+" symbols, and the
   picture zooms onto the face.
4. The 2 s after the hit (`camera.clip_after_s`), the reaction, at half speed. A thin turquoise
   line along the bottom shows how far the replay has got.
5. It holds on a still of the clearest face of the replay. The bars, label and line leave, one soft
   light sweeps over it, and it stays for the rest of the 25 s. This is the "photo" people take a
   picture of.

Some rounds give the still a random look, so the cards people post differ: `duotone` (deep blue
to turquoise, the brand colours), `mono` (black and white, strong contrast), `warm` (golden film
look) or `pop` (strong colour, dark vignette). The video itself keeps its natural colours.
`camera.effect_chance` (0.6) is the share of rounds that get a look; `camera.photo_effects` lists
the looks to choose from. Set the chance to 0 for the plain still every time. The screen stays up
25 s (`flow.results_s`) so there is time to take a picture. Nobody presses a button: the game
records and chooses by itself, from the IP camera that faces the players.

* **Camera:** the camera's RTSP main stream (`camera.url`, default `rtsp://192.168.70.66:554/media/live/1/1`,
  1920×1080). It is read the way `camera/camera_viewer.py` reads it: OpenCV, one decoder thread,
  only the newest frame, reconnecting by itself. The game holds one connection for as long as it
  runs. Frames are decoded all the time, but turned into pictures only while a round is played.
* **Recording:** from "go" until the results appear, 25 frames a second (`camera.clip_fps`). Each
  frame is cut to a tall 1:2 strip around the player, at the camera's full resolution (540×1080),
  and kept as JPEG. The strip glides after the player, about a third of a second behind, like a
  camera operator would, so it never jumps. Only the last 13 or so seconds are kept, plus the chosen
  hit's clip once it is complete. That is up to about 40 MB in memory, and nothing is written to disk.
* **Which person is the player:** people are found with YOLOv4-tiny, the same model the camera
  viewer uses (`models/`), 2 times a second (`camera.samples_per_s`). The player is the person nearest
  the wall, which for a camera facing the players is the tallest person in the picture. People whose
  feet are outside `camera.play_zone` count for a third, so spectators further back or at the side
  are not chosen. The choice is kept from frame to frame by overlap, so the player can move without
  the strip jumping to someone else. There is no LiDAR or body tracking in this installation (the
  sensor only reports ball impacts), so the camera picture is all there is to go on.
* **Which hit:** every scoring hit may become the replay. One is drawn at random, with bigger hits
  more likely (a 90-point hit beats a 10-point hit about 9 times in 10), so the replay differs from
  round to round but is usually a good one. Misses and hits worth 0 or less are not replayed. A hit at
  the very end still gets its replay, just with a shorter reaction.
* **Timing:** the camera's picture arrives a little after the sensor reports the hit. `camera.delay_s`
  (0.5 s) moves the hit later in the video to match. If the points appear before the ball has
  left the player's hand, raise it; if they appear well after, lower it.
* **The face:** OpenCV's face finder (front or side) looks at the replay around and after the hit,
  5 times a second of video. The zoom follows the face it finds. The still is the frame where the face
  is largest and sharpest (`camera.min_sharpness`). Without a face it zooms to where the head is
  in the strip.
* **If there is nothing to show:** a round without a score shows its last seconds in the same way,
  without points. With no person, no camera or no OpenCV, the screen shows a placeholder (a grey head
  and shoulders) and nothing else changes. A camera fault never stops a round or its scoring.
* **Cost:** the frames are decoded and composed in a background thread just ahead of the screen, so
  the game loop only puts the newest one on the wall (about 6 ms per frame for the whole card at
  1536×2048). Recording takes about half of one CPU core during a round.
* **Privacy:** the replay belongs to one round. It is shown only with that round's results and is
  dropped when the next round starts or the round is aborted. It is **never written to disk**, sent
  to the phone page or put in the CSV.

To turn it off, set `"camera": {"enabled": false}`. The results screen then looks as before (big
score at the top, no picture). Tell players about the camera (a sign at the wall), as for any filming
at an event.

---

## 7. Brand implementation (Brandbook V04)

| Element | Used as |
|---|---|
| Turquoise #00A796 (primary) | Green boxes (growth, رشد), start box, positive accents |
| Dark blue #004C6A (primary) | Blue boxes (balanced portfolio, تنوع سبد), HUD band, attract and results backgrounds; the Market Rush field uses its tints |
| Red #F3716D (secondary, "volatility") | Red boxes (volatility, نوسان), time warning; never text or background, as the book requires |
| Purple #832C73 (secondary, highlight) | Multiplier pill and combo banner, the simulation badge, the HUD band during Market Rush; small areas only |
| Tint ramps (page 8), #00243D deep blue | Gradients, letterbox, pale fills |
| Dot pattern #005379 / #CED8DE (page 22) | Backgrounds |
| Graphic elements + − × / • (page 23) | The hit particles and the pattern accents, in the book's exact proportions (bars one third of the module) |
| Box icons (rising bars, allocation ring, price line) | **Not in the brand book.** Made for this game from the book's colours and white-on-colour rule, so they need Mofid's approval (`market_pulse/ui/icons.py`) |
| Logos (pages 4–5) | Copied path for path from the PDF into SVG (`tools/extract_brand_assets.py`), uniform scaling only, white versions only on turquoise / dark blue; the colour Persian stacked logo on white for the logo intro |
| Slogan "پیشرفت مداوم / STEADY PROGRESS" | Under the logo, footer of the attract screen |
| Fonts | Titles: Dana per the book. **Dana was not supplied**, so IRANSansX Black is used until Dana files are put in `assets/fonts/` (picked up automatically). Body and numbers: IRANSans (IRANSansX). Latin: Helvetica / Myriad if installed, else Arial. Persian digits throughout. |

### 7.1 Graphic assets (SVG)

Every piece of artwork is a separate, editable SVG under `assets/`, and the game rasterises it at
the exact size the wall needs:

```
assets/
  manifest.json          the name the code asks for -> SVG file (what the game loads)
  design-tokens.json     the colour palette (recolours the code and the "themed" SVGs)
  brand/                 logos (+ white versions, the mark) and elements/ (the five graphic elements)
  gameplay/              the three boxes
  interface/             buttons/, panels/, indicators/, progress/, backgrounds/, operator/
  feedback/              ripples and rings (reference frames)
  documentation/         asset-inventory.md, design-system.md, asset-sheet.png/.html,
                         comparison/ (every screen before/after), original/ (previous geometry)
  images/                the original PNG logos (fallback only), fonts/, sounds/
```

Files listed in `manifest.json` are loaded by the game. If one is missing or broken, the game logs it
and draws the same shape in code. The other SVGs are references for shapes the game still draws in
code, because their size follows text or an animation. `assets/documentation/asset-inventory.md`
lists every visual element, and `design-system.md` has the rules (colours, units, radii, line
weights, states, file conventions). To refresh the asset sheet after editing a file, run:

```bash
.venv\Scripts\python tools/render_asset_sheet.py
```

### 7.2 Designer's kit

`design/` is a package for Adobe Illustrator: the wall with named layers, every screen, target, icon and
element, the palette (with an `.ase` swatch file), fonts, all Persian messages (live and outlined), the
reference screenshots, a vector PDF of everything and a two-page player guide. Its `README.md` (in
Persian) explains how a design goes back into the game. The game never reads this folder; rebuild it
after changing settings or artwork (Chrome or Edge needed for the PDFs). The registration screen,
Market Rush and the new results layout are only in the reference screenshots, not yet as layered
vector screens:

```bash
.venv\Scripts\python tools/export_design_kit.py
```

The short guide for users (how to run the game, the rules; Persian and English) is in `design/guide/`:

```bash
.venv\Scripts\python tools/export_quick_guide.py
```

The how-to-play sheet for players (Persian, two pages, nothing about installing) is next to it:

```bash
.venv\Scripts\python tools/export_player_guide.py
```

Sounds are not in the brand book; soft tones are synthesised. Drop designed WAV/OGG files
named `hit_green`, `hit_blue`, `hit_red`, `spawn`, `miss`, `combo`, `count`, `go`, `warn`,
`end`, `start`, `results` into `assets/sounds/` to replace them.

---

## 8. Testing

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
```
```bash
.venv\Scripts\python -m pytest -q
```

On a machine with a system proxy, set `NO_PROXY=127.0.0.1,localhost` first, or the webhook test
fails with a 502. The two timing tests (frame budget, layout speed) depend on how busy the machine is.

371 automated tests. Most rules tests run in the classic mode (no registration, no Market Rush,
`conftest.make_cfg`); `conftest.game_cfg` is the game as it ships.

* **Rules:** start with 3 boxes; same-colour replacement; every box moves ≥ 35 cm; red grows
  3 → 6 → 9 → 12 and caps; misses change nothing but the ball count; combo ×1 ×1 ×2 ×2 ×3 ×3
  ×4 ×4 and reset on a miss; end after 10 balls (when set) and at 60 s; late impacts rejected; impacts
  judged at contact time on moving boxes; ball-radius tolerance; minimum box size under pulse;
  40 random full rounds sampled after every move: **no overlaps, all boxes inside the field.**
* **Market Rush (`test_market_rush.py`):** starts on red hits only, 4 s each, a red hit during it
  gives a fresh 4 s without a flash, the 10th touch ends it and starts none, speed ramps and returns to normal, more
  boxes without overlap, multiplier applied once and reset, the 60 s end cuts it short, impact time
  decides the multiplier.
* **Registration and play style (`test_registration_profile.py`):** empty and invalid names, typing,
  timeout, start by button, the classification rules and score ranges, and a complete game as it
  ships played through `App.frame()`, ending on the 10th touch.
* **Layout:** 300 random layouts each for 3 / 6 / 9 / 12 boxes, minimum move distance, grid
  fallback for impossible configurations, speed (< 8 ms per layout).
* **Input:** JSON protocol incl. 11 kinds of malformed messages, calibration, merge and rebound
  filters, edge noise, a real UDP socket end to end, busy-port handling, a real TUIO/OSC
  listener.
* **Flow:** every screen transition, start by throw, operator skip, reset/abort, player identity,
  results log, leaderboard ranking and restart persistence, corrupt log lines, unwritable log,
  webhook delivery and failure.
* **Text:** Persian / number run splitting (13 cases), numbers drawn on the correct side, digit
  order, caching, memory-bounded surface caches, wrapping, long player names.
* **Config:** broken / missing / partial files, 10 kinds of bad values, read-only settings.
* **Assets:** every SVG is pure vector (no images, text, fonts, filters, clipping or external
  references), has a title and viewBox, and renders at 16, 64 and 512 px. Every name the code uses
  is in the manifest, colours come from the palette, tokens recolour the themed files but never the
  logos, bad tokens fall back to the brand values, and a missing file falls back to the code shape.
  The SVG boxes and symbols cover the same pixels as their code fallbacks.
* **Whole app (headless):** a full session at 4 display sizes started by a sensor "throw", letterbox
  colour, pause, missed-ball key and UDP message, calibration and debug overlays, reset, simulation
  click, Esc double press, recovery from frame errors and exit code 2 after repeated errors,
  frame time with 12 moving boxes at 1536×2048.

Soak and real-window results are in section 9.

---

## 9. Verified results, known limitations and open questions

**Measured on the development PC** (Windows 11, 16 threads, 1920×1080 monitor):

| Check | Result |
|---|---|
| Fullscreen, native 1920×1080, wall letterboxed, round played over UDP | 59.6 FPS average over 20 s; 7 dropped frames, all in the first second after launch or the first time the instructions appear; none during play or results |
| Windowed with the F3 overlay, 45 s, full round over UDP with a player name | 60.0 FPS average, result written and ranked |
| Headless, 1536×2048, 12 moving boxes with effects | p95 frame time 3.7 ms (budget at 60 FPS: 16.7 ms) |
| Player replay, live from the camera (1920×1080, 27 fps), 16-core PC | Connected in 1.3 s; 25 frames/s recorded; ~6 % of one CPU core between rounds (decoding), ~57 % during a round (recording and following the player, background threads); a 12 s clip is 17 MB in memory; the card with the replay at 1536×2048 draws in 5.8 ms (median), the background thread composes a frame in 4 ms (median) |
| Soak: 30 back-to-back rounds (41 min of game time, 74,409 frames), ended by balls and by time, **before the Market Rush upgrade** | 0 errors; memory levels off at ~240 MB (process private bytes) |

Use `python main.py --debug` on the real wall: the F3 overlay shows the live FPS, and every
10 minutes the log gets a `health:` line (FPS, dropped frames, rounds, recovered errors).

**Not verified (no access):**

* No real sensor was available. Both protocols were tested with loopback senders only.
  Calibration values, latency and the merge / rebound filters must be tuned on site.
* Not run on the actual LED wall or its controller. Run `python main.py --debug` there once and
  check the FPS line, the canvas size and that text is readable from the throwing line.
* No real players. The speed of Market Rush, the number of extra boxes, the ×2 multiplier and the
  play-style thresholds were set with simulated players; check them with real rounds.
* Typing a name on a physical keyboard, including the Persian layout, has not been tried by hand.
* The player replay was tested with the camera on a desk (people sitting and walking past), not
  mounted at the wall with someone throwing. Check `camera.play_zone`, `camera.delay_s` (do the points
  appear as the ball lands?) and the face zoom with real players.

**Limitations:**

* Balls that never reach the wall need the operator's **M** key or a `throw_missed` message.
* A ball cannot type: registration needs a keyboard at the wall or the platform's `player` message.
* A miss does not show which box was aimed at, so the play style is based on hits alone.
* Dana is not included (licence); IRANSansX is used for titles. The IRANSansX files must be
  covered by Mofid's font licence for this installation.
* The registration platform and landing page are not part of this program (hooks only).
* The JSONL file grows by about 400 bytes per round; archive it occasionally.
* The player replay needs OpenCV (about 70 MB more in the .exe) and the 24 MB model in `models/`.
  The "nearest person" rule assumes the camera faces the players from the wall; a camera at the
  side needs `camera.play_zone` set to the throwing area.

**Questions for Mofid / the organiser:**

1. Final point values per colour and the combo thresholds.
2. Sensor model and protocol, and who writes the bridge if it speaks neither TUIO nor JSON.
3. Exact LED wall size and controller resolution.
4. Dana font files for this installation.
5. The registration platform's webhook URL and how players identify themselves (QR, tablet…).
6. Designed sounds, if any.
7. Brand and compliance approval of the box icons, the name «طوفان بازار» and the play-style sentences.

---

## 10. Architecture

```
main.py                     command line, logging, start-up
config/settings.json        installation settings (every value has a built-in default)
market_pulse/
  config.py                 defaults, deep merge, validation (bad values never crash)
  app.py                    window, 60 FPS loop, operator keys, error recovery, wiring
  audio.py                  synthesised sounds, optional WAV/OGG overrides
  game/                     pure logic, no pygame (unit-tested)
    rules.py                one round: scoring, combo, Market Rush, spawning, end conditions
    targets.py              box motion and size as functions of time (exact hit tests), TimeWarp
    profile.py              the play style shown on the results screen
    layout.py               random non-overlapping placement (numpy)
    flow.py                 screen state machine, pausable clock, result records
    results.py              JSONL log, today's leaderboard, CSV file, webhook thread
  input/                    hardware, threaded, never blocks the frame
    sources.py              UDP JSON and TUIO listeners with reconnect
    events.py               impact / control events, calibration, merge + rebound filter
    manager.py              queue → calibration → filter → wall cm + game time
    lidar/                  the STL27L LiDAR (4.2a): driver.py serial → scans, detect.py background → hits,
                            tuning.py its settings, wallmap.py LiDAR mm → wall cm, capture.py F2+C, source.py
  ui/
    theme.py                brand colours (+ design tokens), wall-cm → pixel viewport (letterboxing)
    assets.py               SVG artwork from assets/manifest.json, rasterised at exact sizes
    text.py                 Persian shaping (HarfBuzz) + bidi runs, render cache
    draw.py                 box sprites, brand symbols (SVG or code fallback), dot pattern
    effects.py              ripples, particles, floating words, banners
    icons.py                the three box icons: meaning and shapes (no pygame)
    screens.py              attract, instructions, registration, countdown, HUD, Market Rush, results
    overlays.py             F2 calibration and F3 diagnostics
    settings.py             F5 settings page: tabs, sliders, switches, the LiDAR tab, saving changed values (5.1)
  web.py                    the operator's phone page: local web server, threaded (section 6.1)
  camera.py                 player replay: RTSP camera thread, YOLO player tracker, face finder (6.3)
  replay.py                 recording the strip, choosing the hit, the replay's timeline and frames (6.3)
models/                     YOLOv4-tiny and OpenCV face finders for the player replay
assets/                     SVG artwork, manifest, design tokens, docs (section 7.1); fonts, sounds; web/panel.html
tools/                      extract_brand_assets.py (logos + elements from the PDF), build_svg_assets.py,
                            render_asset_sheet.py, export_design_kit.py, svgkit.py,
                            send_test_impacts.py (test sender)
design/                     generated designer's kit for Illustrator (section 7.2)
scripts/                    run_kiosk.bat / run_kiosk.sh
tests/                      pytest suite
```

**Threads:** the main thread renders and runs the game. Each input source has a daemon thread
that only writes to a queue; the webhook has its own thread, and the phone page has its own server
threads, which also only write to a queue. The game reads the queues once per frame. The camera
has two threads (the stream, which also records, and the player tracker), and each replay has two more
on the results screen (frames and face); the main loop only tells them when a round starts, scores and
ends, and blits the newest replay frame.

**Time:** one monotonic game clock that stops while paused. Box positions are analytic functions
of that clock, and every impact carries its contact time, so hit tests do not depend on frame
rate or event delay. Market Rush speed is not a separate timer: a TimeWarp turns game time into
motion time, so speed changes stay smooth and past impacts can still be checked exactly.

**Reliability:** an exception inside a frame is logged and the game returns to the attract
screen. After 5 errors within a minute it exits with code 2, and `run_kiosk` restarts it. Missing
fonts, logos, sound devices or a broken settings file degrade gracefully with a logged warning.

**Why these libraries:** pygame-ce (SDL2 rendering, GPU scaling, HarfBuzz text shaping for
Persian), numpy (vectorised layout and sound synthesis), python-osc (TUIO, optional). No OpenCV:
the game receives positions from the sensor's own software rather than processing camera images.
