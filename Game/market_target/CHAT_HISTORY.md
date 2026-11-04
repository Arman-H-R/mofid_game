# Market Pulse (نبض بازار): chat history and handoff

Paste this file into a new chat, and give it `market_pulse_full_project.zip` (the project), unzipped.
Written 2026-10-30, updated after the "Market Target" upgrade. Everything below describes the project inside that zip.

## What the project is

An interactive ball-throwing game for a Mofid Securities (کارگزاری مفید) LED wall, 3 × 4 m portrait.
Python 3.10 with pygame-ce. Players throw soft balls at moving coloured boxes; a sensor reports each hit.
Built from the scenario PDF `Mofid-Market-Pulse-Game-Scenario.pdf` (with Persian add-ons 1.1–1.4) and the
brand book `C:/Users/Arman/Downloads/Brnadbook-V04.pdf`.

## Where things are

- **Latest work:** `market_pulse_full_project.zip`. It was built in a temporary folder the app made for the old chat.
- **Original project on the Desktop:** `C:/Users/Arman/Desktop/P.project/Wenods/mofid_games/market_pulse`.
  It is **older**: none of the work from step 6 onwards is in it. Nothing has been copied there, and the user
  has not answered whether to copy it.
- **Reference kit:** `C:/Users/Arman/Desktop/P.project/Wenods/mofid_games/design.zip` (Market Hockey). The Market Pulse kit copies its structure.

## What was done, in order

1. Read and analysed the scenario PDF and add-ons.
2. Built the production game from the brand book and a long technical spec.
3. Told the user where the files are, sent a zip, ran the game. The user played 2 rounds.
4. Spec "Game Graphic Asset Extraction & Vector Design System": all graphics moved into editable SVGs under `assets/`,
   with a manifest, design tokens, inventory, design system doc, asset sheet and comparison. Gameplay unchanged.
5. Delivered `market_pulse_svg_assets.zip`.
6. Design kit like `design.zip`: `design/`, built by `tools/export_design_kit.py`. Includes the wall with named layers,
   screens, targets, icons, elements, brand logos, palette (with `.ase`), fonts, all Persian messages live and outlined,
   13 screenshots, a 10-page vector PDF, a 2-page player guide and a Persian README.
7. Every asset as its own SVG: `design/svg_separate/`, 438 SVG files in 14 folders.
8. Simple guide, Persian and English, 2 pages each (how to run, the rules): `design/guide/MarketPulse_Quick_Guide_{FA,EN}.pdf`,
   built by `tools/export_quick_guide.py`. Players' how-to-play sheet, Persian, 2 pages: `design/guide/MarketPulse_HowToPlay_FA.pdf`,
   built by `tools/export_player_guide.py`.
9. Discussed better target icons related to the stock market.
10. Spec "Improve and Upgrade the Existing Market Target Interactive Game". No project of that name exists on this PC,
    so it was applied to Market Pulse. Built: name registration, exactly 60 s, three categories with new icons and
    motion, Market Rush, results screen with a play style. See `README.md` sections 1.1 to 1.4.
11. The user moved the project to `C:/Users/Arman/Desktop/P.project/Wenods/mofid_games/market_target/Game/market_target`
    (no `.venv` came with it) and set the final rules: 10 touches or 60 seconds, 4 seconds of Market Rush for every red touch.
12. Phone page for an operator (name, Play, history, CSV of every round): `market_pulse/web.py`, `assets/web/panel.html`,
    `README.md` section 6.1. The user also said the game's name is «بازار هدف»: the page and the player guide use it,
    the wall still shows `text.title` «نبض بازار».
13. Player photo on the results screen (2026-11-02): `market_pulse/camera.py`, `models/`, settings `camera`,
    `README.md` section 6.3, `tests/test_camera.py`. It uses the Witness IP camera at 192.168.70.66 over RTSP, read the
    way `../../camera/camera_viewer.py` reads it, and the same YOLOv4-tiny model. The nearest (tallest) person in
    `camera.play_zone` is the player. The photo is taken at a random moment 5-20 s after go, or the best frame is used
    if the round ends first, or a placeholder is shown. It is kept in memory only. The game venv got OpenCV 4.13 by copying
    `cv2` from the system Python 3.10, because pip could not reach files.pythonhosted.org. The exe is now 86 MB.
14. The user wanted the photo bigger and bolder, as a shareable profile, in a square or rectangular frame. The results screen
    became a profile card: a 4:5 photo (120 × 150 cm) in a white frame, the score on a turquoise badge over its corner, the
    name, and the play style with its sentence. Photos got a contrast and colour boost, and `flow.results_s` went from 18 to 25 s.
15. From the user's sketch: a tall photo on the left (1:2, 132 × 264 cm) with a white frame and a tilted turquoise outline frame
    behind it, and the text in a column on the right. Random photo looks per round are in `market_pulse/ui/photo_fx.py`
    (duotone, mono, warm, pop), controlled by `camera.effect_chance` and `camera.photo_effects`.
16. Entrance animation for the card (`_results_card` / `_draw_photo` in `ui/screens.py`; README 6.3). The frame opens, the photo
    rises in behind a turquoise "price" line, the outline turns out, symbols burst and a light sweeps across. The text is read in
    right to left, the score counts up and beats, and the hit cards pop. It is stateless (a function of time). The phone page's
    Play/Cancel commands can no longer start a round after the page has already reported "no answer" (`web.Command.claim/expire`).
17. No how-to page in the game (2026-11-03): `flow.show_instructions` is now false (the screen stays in the code). Instead the
    Mofid logo (colour Persian stacked logo from the brand book) zooms in and fades before every countdown: flow state
    `INTRO`, `flow.intro_s` 2.6 s, `_intro_overlay` in `ui/screens.py`, tests in `tests/test_intro.py`. The same logo shows
    while the game loads (the box sprites are built behind it) and zooms away when the window opens. The phone page shows the
    intro as "countdown" and can cancel it. The test scenario `CLASSIC` in `tests/conftest.py` keeps the how-to page and no intro.
18. Video instead of a photo (2026-11-03): the user asked for a replay of the player scoring, with a zoom on the face and
    cinematic slow motion, about 10 s before the score. `market_pulse/replay.py` (new): `ClipRecorder` keeps a 1:2 strip that
    follows the player, 25 frames/s as JPEG in memory, and picks one scoring hit at random, weighted by points. `Timeline`
    plays the lead-up at normal speed, then 0.3× around the throw with blended frames, film bars and a vignette, the points
    rising at the hit and a zoom onto the face. `Replay` composes the frames in a background thread and ends on a still of the
    clearest face, in the round's random look. `camera.py` now has `PlayerCam` (recording and tracking) instead of `PlayerPhoto`.
    The settings `camera.clip_before_s` (10), `clip_after_s` (2), `clip_fps` (25), `slow_motion` (0.3) and `delay_s` (0.5)
    replace `capture_window_s`. Tests are in `tests/test_replay.py` and `tests/test_camera.py`. It was checked with a real clip
    from the camera on the desk.
19. Settings page in the game (2026-11-03): the user asked for settings with sliders and buttons to change the useful
    things, such as the boxes' size, the multipliers ("ratio", ضریب) and the points, with simple graphics.
    `market_pulse/ui/settings.py` (new) draws a page opened with F5. It has five tabs (boxes, multipliers, Market Rush,
    round, sound and video), each row a slider with − and + buttons or a switch, and it works with the mouse and the
    keyboard. The boxes are previewed at their real size, and a card shows the most one hit can score. Changes apply at
    once (from the next round for what a round reads at its start). Save writes only the changed keys into
    `config/settings.json`; there are also try without saving, revert, and defaults for this page. The game's clock stops
    while the page is open. `Renderer.refresh_settings`, `Audio.set_volume` and `ClipRecorder._read_settings` pick up the
    values those parts keep. Tests are in `tests/test_settings.py`.
    The user then asked to open it from the terminal, without the game: `python main.py --settings` (or
    `MarketPulse.exe --settings`) opens only the page, full screen (`--windowed` for a window); `App.run_settings`. Save keeps
    it open there, Esc quits, and with unsaved changes the first Esc only warns.
20. LiDAR in the game (2026-11-03): the user gave the LiDAR folder (`LIdar/`: an STL27L driver, a background /
    blob / ball-mode detector with a settings window, OSC out) and asked to merge it into the game: hits from the LiDAR
    score during the round only, while the name and Start come from the platform (the phone page). The driver and the
    detector are copied into `market_pulse/input/lidar/` with the algorithm unchanged, so `config/lidar.json` (a copy of
    `LIdar/settings.json`) can still be tuned with the LiDAR tool. New: `LidarSource` (its own thread: connect, scan the
    background, hits as impacts), `WallMap` (a least-squares fit from LiDAR mm to wall cm through four crosses taken with
    F2 then C, saved in `input.lidar.calibration_points`; the touch area is then the wall), F6 scans the background
    again, `input.lidar.round_only`. `pyserial` added to requirements. Checked with the real sensor on COM14: connected,
    background from 97 scans, 9.6 scans a second, no false hit on the empty wall. Tests are in `tests/test_lidar.py`.
21. Full screen and the screen settings (2026-11-03): the user asked for full screen, the screen size on the settings
    page, and the game filling the screen. First `display.fill_screen` stretched the game; the user then asked never
    to stretch (bars where needed), and then, with two drawings, for the game itself to fill the screen like the
    second drawing. Now: nothing is ever stretched (one scale; `pygame.SCALED` scales evenly). With `fill_screen` the
    wall takes the display's shape at the start (`fit_wall`: 711 x 400 cm on 16:9), so play covers the whole screen;
    the menus keep their 3:4 layout centred (`Renderer.vp_menu`, `wall.layout_width_cm`, `config.layout_left` for the
    start square), with their backgrounds over the whole screen. A lidar calibration for another wall size is not
    used. `display.resolution` is `[w, h]` with 0 = automatic. F5 has a sixth tab, نمایش. F11 switches full screen
    (a new window, because set_mode from a window to full screen kept showing the window's last frame). Checked on
    the real 1920x1080 display from desktop screenshots (README 3). Tests are in `tests/test_display.py`.

## Decisions in the upgrade that the user may want to change

- **A touch is any ball on the wall**, hit or miss. The user's rule: the game ends on the 10th touch or at 60 s.
- **Names need a keyboard at the wall** or the UDP `player` message, which skips the screen. A ball or a mouse can only press Start.
- **Market Rush** starts on every red hit and lasts 4 s. A red hit during it gives a fresh 4 s from that hit, in the same phase (my reading; `rush.restart_on_red_hit: false` ignores those hits instead). The 10th touch starts nothing.
- **Multipliers multiply** (combo × rush, up to ×8). `rush.stack_with_combo: false` uses the larger one instead.
- **Play style comes from hit shares**, and the score only picks the sentence. `score_tiers` [90, 180] came from simulated players.

## Target icons

Boxes in the game: green rising bars, blue allocation ring 50/30/20, red irregular price line (`market_pulse/ui/icons.py`).

The user's chosen meanings, in their own words:

```
1) رشد یا سود آوری   -    ریسک کم سود اوری کم  (سبز)
2) تنوع سبد    -    یا مدیریت ریسک    -    ریسک متوسط سود اوری متوسط  (ابی)
3) نوسان هیجان -  ریسک زیاد سود زیاد ( قرمز)
```

Icon ideas I sketched for those meanings (the first of each was built):

| Box | Size | Recommended | Alternatives |
|---|---|---|---|
| Green | 60 cm | Three rising bars | Rising arrow, sprout |
| Blue | 45 cm | Ring split into three parts | Pie chart, shield |
| Red | 25 cm | Zigzag | Lightning bolt (use it if the zigzag is hard to read on the wall), flame |

Reasons: each icon has a different outline, so boxes can be told apart by shape while moving. The game's rules already
act out risk and return: small red is hardest to hit and worth most, and a miss resets the multiplier.
Rejected: down arrow on red (rewards a falling price), money symbols (suggest promised profit), the Mofid bull
(the logo must not be altered), candlesticks (look alike at small size), buy/hold/sell, bull/bear, fund-type names.

### Notes for changing icons

- Artwork: `assets/gameplay/target-{green,blue,red}.svg`. Must stay pure vector for nanosvg: no text, `<use>`, filters, clipping or masks.
- Code fallback shapes: `market_pulse/ui/draw.py` (`draw_symbol`), and `theme.TARGET_SYMBOLS` in `market_pulse/ui/theme.py`.
  `tests/test_assets.py` checks that the SVG boxes and symbols cover the same pixels as the code fallback, so update both together.
- Generators that draw the same symbols: `tools/build_svg_assets.py` and `tools/export_design_kit.py` (`target`, `element_shape`).
- The brand symbols + − × / • should stay in the background pattern and hit bursts.
- Text: `config/settings.json` → `text` → `word_*`, `meaning_*`. The meaning lines are drawn at 5.8 cm on the instructions screen; check they fit.
- Hard-coded meanings or symbols also appear in `tools/export_quick_guide.py` and `guide_pdf()` in `tools/export_design_kit.py`.
- Afterwards regenerate the kit and guides, compare before and after, run the tests.

## Game rules as implemented

- 10 touches or 60 seconds, whichever comes first. Start by throwing at the start box (or the operator's start key), then type a name.
- Green 60 cm, 10 points. Blue 45 cm, 15 points. Red 25 cm, 30 points, and adds 3 boxes (one of each colour), up to 12.
- All boxes move after each hit. Motion: green sways left and right, blue moves in a figure of eight, red moves irregularly and pulses.
- Market Rush: 4 s from every red hit, speed ×1.6, up to 12 boxes, points ×2, deep blue background.
- Multiplier for each hit in a row: ×1 ×1 ×2 ×2 ×3 ×3 ×4 ×4. A miss resets it to ×1 and uses a touch. No negative points.
- Hits after the end do not count.
- Digits are Persian by default (`ui.digits`). All text is in `config/settings.json` → `text`.

## Brand facts

- Colours in `assets/design-tokens.json`: turquoise #00A796, dark blue #004C6A, purple #832C73, red #F3716D, plus the book's tints and neutrals.
- Fonts in the project: IRANSansX (Black, ExtraBold, Bold, DemiBold, Medium, Regular). Dana was not supplied; if `Dana-*.ttf` is added to `assets/fonts/`, titles use it automatically.
- Wall layout in cm: HUD 0–64, field 64–370, footer 370–400. Tile radius 18 %.

## Commands (run inside the project folder)

```bash
python -m venv .venv
```
```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
```
```bash
.venv\Scripts\python main.py --windowed --simulate
```
```bash
.venv\Scripts\python main.py
```
```bash
.venv\Scripts\python -m pytest -q
```
```bash
.venv\Scripts\python main.py --gallery out/
```
```bash
.venv\Scripts\python tools/export_design_kit.py
```
```bash
.venv\Scripts\python tools/export_quick_guide.py
.venv\Scripts\python tools/export_player_guide.py
```

Re-running the kit overwrites `design/`. The PDFs need Chrome or Edge.

## Known working notes

- `market_pulse/app.py`, `game/rules.py`, `game/targets.py`, `ui/draw.py` and `ui/screens.py` use CRLF line endings; other project files use LF. Keep them that way.
- Tests: `conftest.make_cfg` is the classic mode (10 balls, no registration, no rush), `conftest.game_cfg` is the game as it ships.
- After changing `config.py` defaults, regenerate `config/settings.json` from them, or the new defaults are overridden.
- In a very deep folder, compiled `fontTools` can fail with "filename too long". `pip install --force-reinstall --no-binary=fonttools fonttools` fixes it.
- `tests/test_assets.py` scans everything under `assets/` and forbids live text there, so anything with live text belongs in `design/`.
- Don't print Persian to the Windows console without `PYTHONIOENCODING=utf-8`.

## Open items

Decisions for the user:
- Copy the latest work to the Desktop project (asked twice, not answered).

To confirm with Mofid:
- The points 10, 15 and 30 are my defaults; the scenario only says red is worth the most.
- New target icons are outside brand book page 23, so the brand team should approve them. The same goes for the name «طوفان بازار» and the play-style sentences.
- «ریسک زیاد، سود زیاد» can read as a promise of profit. «سود احتمالی زیاد» is safer; compliance should approve the wording.
- The game uses red and purple as text colours (the word «نوسان», the floating multiplier), which the brand book does not allow.

Not verified:
- The player replay was tested with the camera on a desk (people sitting), not mounted at the wall with throwing players;
  `camera.delay_s` (picture behind the sensor) is a guess until it is timed with a real throw.
- Nothing has been opened in Adobe Illustrator (live text, `.ase` swatches).
- Never run on the real LED wall. The game reads the STL27L LiDAR on COM14 (background and an empty wall checked), but
  hits from real throws and the F2 + C calibration at the wall are not tested yet. It also accepts UDP JSON (port 9100) and TUIO.
- The zigzag's readability on the 25 cm box needs testing at the real throwing distance.

Test status after the upgrade (2026-10-30): 360 of 362 pass with the system proxy bypassed. The two failures are the
timing tests below. At that moment this PC ran about 12× slower than earlier the same day: the suite took 444 s instead of
38 s, and the pre-upgrade code failed the same two tests by the same margin (layout 51.5 ms against 52.1 ms, frame 18.3 ms
against 19.2 ms). The cause of the slowdown was not found. Rerun them when the PC is back to normal.

- This PC has a Windows system proxy on (`http://127.0.0.1:12334`, no bypass list). With it on,
  `tests/test_flow.py::test_webhook_posts_results` fails with HTTP 502, because Python sends even the test's local request through
  the proxy. The test command below bypasses it. The game's score webhook uses the same system proxy setting,
  so it could fail the same way on a PC with a proxy on. Failed scores stay in the local log.
- 2026-11-03: it failed again (9.3 ms) on battery at 22 %; everything else passed (454).
- `tests/test_layout.py::test_layout_is_fast_enough_for_a_frame` has an 8 ms limit. It failed at about 10 ms on battery at 15 %
  and passes plugged in, so a failure on battery is probably not a code problem.

```powershell
$env:NO_PROXY = "127.0.0.1,localhost"; .venv\Scripts\python -m pytest -q
```
