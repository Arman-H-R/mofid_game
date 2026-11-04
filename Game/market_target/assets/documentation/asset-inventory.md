# Market Pulse: graphic asset inventory

Every visual element the game renders, where it was drawn before, where its vector file is now,
and whether the game loads that file or still draws the shape in code.

* **Loaded:** the game reads the SVG through `assets/manifest.json` and rasterises it at the exact pixel
  size. If the file is missing or broken, the game logs a warning and draws the same shape in code.
* **Reference:** the game draws the shape in code, because its size follows the text or an animation
  frame by frame. The SVG documents the shape, colours and line weights at a default size, so a
  designer can restyle it. A redesign then needs a matching code change (listed in the last column).
* **Text:** live text rendered with the game fonts (Persian shaping and right-to-left order need it).
  There is no SVG for text. All words are editable in `config/settings.json` → `text`.
* **Tint:** a flat colour or a translucent fill over an area. These have no shape, so they are colour
  tokens only.

Code locations are `file → function`. "Before" is the state of the code at the start of this work;
the full set of before/after screens is in `comparison/`.

Every SVG in this inventory is pure vector. No file embeds a PNG or JPEG, uses text or fonts, or
refers to another file. Nothing needed to be traced or redrawn from a raster image, so there are no
illustrations that could not be vectorised.

---

## 1. Brand

| Asset | Before (code) | SVG file | Appears in | Purpose | Variants | Use | Designer work |
|---|---|---|---|---|---|---|---|
| Persian logo, horizontal | PNG `images/logo_fa_horizontal.png` via `screens.py → Renderer._logo` | `brand/logo-fa-horizontal.svg` | Footer of the gameplay and instructions screens (58 cm wide) | Brand sign-off | white | Loaded (`logo.fa_horizontal`) | None: path-for-path copy of the brand book (p.4) |
| Persian logo, horizontal, white | PNG `logo_fa_horizontal_white.png` | `brand/logo-fa-horizontal-white.svg` | Results screen header (100 cm) | Brand on dark blue | colour | Loaded (`logo.fa_horizontal_white`) | None (white version allowed on dark blue, p.7) |
| Persian logo, stacked, white | PNG `logo_fa_stacked_white.png` | `brand/logo-fa-stacked-white.svg` | Attract screen, top (84 cm) | Brand on dark blue | colour | Loaded (`logo.fa_stacked_white`) | None |
| Persian logo, stacked | PNG `logo_fa_stacked.png` (only cropped for the window icon) | `brand/logo-fa-stacked.svg` | Not on the wall | Complete set; source of the mark | white | File only | None |
| English logo, horizontal and stacked | PNGs `logo_en_*.png` (in the project, never displayed) | `brand/logo-en-horizontal(-white).svg`, `brand/logo-en-stacked(-white).svg` | Not on the wall | Complete set (p.5) | colour, white | File only | None |
| Logo mark (the bull) | `app.py → App._set_icon`: the top half of `logo_fa_stacked.png`, cropped | `brand/logo-mark.svg` | Window and taskbar icon (64 × 64 px) | App icon | none | Loaded (`logo.mark`) | **Confirm with the brand team.** The brand book pages used here show the mark only inside the full logo |
| Logo text fallback "کارگزاری مفید" | `screens.py → Renderer._blit_logo` | none | Only if a logo file and its PNG fallback are both missing | Keeps the brand visible | none | Text | None |
| Graphic element + (plus) | `draw.py → draw_symbol` | `brand/elements/plus.svg` | Particles, background accents | Brand texture | white file, any colour at runtime | Loaded (`symbol.plus`, shape only) | Approve the proportion change (section 8) |
| Graphic element − (minus) | `draw_symbol` | `brand/elements/minus.svg` | Particles, background accents | Brand texture | as above | Loaded (`symbol.minus`) | As above |
| Graphic element × (times) | `draw_symbol` | `brand/elements/times.svg` | Particles, accents | Brand texture | as above | Loaded (`symbol.times`) | As above |
| Graphic element / (slash) | `draw_symbol` | `brand/elements/slash.svg` | Particles, accents | Brand texture | as above | Loaded (`symbol.slash`) | As above |
| Graphic element • (dot) | `draw_symbol` | `brand/elements/dot.svg` | Particles, accents | Brand texture | as above | Loaded (`symbol.dot`) | None (unchanged) |
| "STEADY PROGRESS" line | `screens.py → Renderer._steady_progress` | none | Attract and results screens, bottom (4.2 cm, letter-spaced, turquoise + white) | Brand tagline from the pattern page (p.22) | none | Text | Optional: the brand font (Helvetica / Myriad) is used only if installed, otherwise Arial. Supplying the tagline as lettering would fix that |

## 2. Gameplay

| Asset | Before (code) | SVG file | Appears in | Purpose | Variants / states | Use | Designer work |
|---|---|---|---|---|---|---|---|
| Green box (target) | `draw.py → Sprites.target` (rounded square, radius 18 % of the side, white rising bars, `ui/icons.py`) | `gameplay/target-green.svg` | Field (60 cm), instructions (true size), attract card (82 %, bobbing), results cards (14 cm) | Growth and stability, 10-point target | Normal, pulse (size breathes per `motion.*.pulse`), pop-in, move. All motion is in code | Loaded (`target.green`) | Approve the symbol proportions |
| Blue box | as above, white allocation ring 50/30/20 | `gameplay/target-blue.svg` | as above (45 cm) | Balanced portfolio, 15-point target | as above | Loaded (`target.blue`) | as above |
| Red box | as above, white irregular price line | `gameplay/target-red.svg` | as above (25 cm) | High volatility, 30 points, spawns three new boxes, starts Market Rush | as above | Loaded (`target.red`) | as above |
| Box shadow | `Sprites.target` | none | Under every box on the field and the instructions screen | Lifts the boxes off the wall | On/off: `ui.target_shadow` in settings | Code: the box outline in deep blue (27 % opacity), 5 % of the side lower, blurred by 6 % of the side | None. It is drawn in code because an SVG blur filter is slow, renders unreliably, and is not editable in Illustrator or Figma. The brand book gives no shadow guidance, so the style is a design decision to confirm |
| Spawn ring | `screens.py → Renderer._spawn_rings` → `effects.Effects.ripple` | `feedback/spawn-ring.svg` | Around every new box at "go" and after a red hit | Draws the eye to new boxes | colour = box colour | Reference (radius and alpha animate) | None |

## 3. Interface

| Asset | Before (code) | SVG file | Appears in | Purpose | Variants / states | Use | Designer work |
|---|---|---|---|---|---|---|---|
| Start box | `screens.py → draw_attract`, `Sprites.rounded` | `interface/buttons/start-button.svg` | Attract screen (84 cm, pulses ±3 %) | The only "button": the player throws a ball at it | Label "شروع" is live text | Loaded (`button.start`) | None |
| Attract card | `draw_attract`, `Sprites.rounded` (256 × 80 cm, radius 5) | `interface/panels/attract-card.svg` | Attract screen | Holds "how to play" (three boxes and their words) or the leaderboard, alternating with a fade | Fades to 25 % when it switches | Loaded (`panel.attract_card`) | None |
| Results card | `draw_results` (80 × 58 cm, radius 4) | `interface/panels/results-card.svg` | Results screen, three cards | Hits per colour | Slides up and appears in turn | Loaded (`panel.results_card`) | None |
| Banner pill | `effects.py → Effects.draw_banner` | `interface/panels/banner-pill.svg` | Below the HUD: "بازار شلوغ‌تر شد!" (dark blue text), "ضریب ×N!" (purple text) | Event message | Fades in and out | Reference (width = text + 12 cm) | None |
| Best-multiplier pill | `screens.py → Renderer._pill` | `interface/panels/pill-best-multiplier.svg` | Results screen | Best combo of the round | Purple, white text | Reference (width follows the text) | None |
| Rank pill | `Renderer._pill` | `interface/panels/pill-rank.svg` | Results screen, when in today's top N | Today's rank | Turquoise, white text | Reference (width follows the text) | None |
| Ball still to throw | `screens.py → _hud`, `Sprites.dot` | `interface/indicators/ball-remaining.svg` | HUD, centre row (6 cm) | Balls left | Filled white dot | Loaded (`indicator.ball_remaining`) | None |
| Ball already thrown | `_hud`, `pygame.draw.aacircle` | `interface/indicators/ball-used.svg` | HUD | Balls used | Light-blue ring, 0.5 cm | Loaded (`indicator.ball_used`) | None |
| Multiplier badge, active | `_hud`, `Sprites.rounded` (46 × 13 cm) | `interface/indicators/multiplier-active.svg` | HUD (×2 and up) | Current combo multiplier | Purple fill, white text; pops +15 % on level-up, shakes when the combo breaks | Loaded (`indicator.multiplier_active`) | None |
| Multiplier badge, inactive | `_hud`, outlined `Sprites.rounded` | `interface/indicators/multiplier-inactive.svg` | HUD (×1) | Multiplier at rest | Light-blue outline 0.5 cm, pale text | Loaded (`indicator.multiplier_inactive`) | None |
| Points pill | `draw_instructions` (50 × 15 cm) | `interface/indicators/points-pill.svg` | Instructions, one per colour | "N امتیاز" | Pale turquoise | Loaded (`indicator.points`) | None |
| Rank badge, first place | `screens.py → _panel_board` (9 cm circle) | `interface/indicators/rank-badge-first.svg` | Leaderboard on the attract card | Rank 1 | Turquoise, white number | Loaded (`indicator.rank_first`) | None |
| Rank badge, places 2–5 | `_panel_board` | `interface/indicators/rank-badge.svg` | Leaderboard | Ranks 2–5 | Pale blue, dark-blue number | Loaded (`indicator.rank`) | None |
| Time bar | `_hud` + `draw.py → gradient_bar` | `interface/progress/time-bar.svg` | Under the HUD band (300 × 2.2 cm) | Round time, drains to the right | Gradient dark blue → turquoise on a light-grey track | Reference (width changes every frame) | None |
| Instructions progress bar | `draw_instructions` (same bar) | `interface/progress/time-bar.svg` | Instructions screen | Time until the round starts, fills to the left | as above | Reference | None |
| Results auto-return bar | `draw_results` (180 × 1.6 cm) | `interface/progress/results-countdown-bar.svg` | Results screen bottom | Time until the next player | Turquoise on dark blue | Reference | None |
| Gameplay background | `screens.py → _build_light`, `_band`, `_footer`, `draw.py → dot_grid` | `interface/backgrounds/background-play.svg` | Countdown, play, ending | White wall, dot grid (12.5 cm, Ø 1.1 cm) with six symbol accents, dark-blue HUD band with its own dots, footer divider and logo | none | Reference: drawn once per screen size in code, from the same seed | Confirm the dot density (section 7 of the design system) |
| Instructions background | `_build_light(accents=False)` | `interface/backgrounds/background-instructions.svg` | Instructions | Same, without accents behind the text. The band title "راهنمای بازی" is live text | none | Reference | as above |
| Dark background | `_build_dark` | `interface/backgrounds/background-dark.svg` | Attract, results | Dark blue with dot grid and eight symbol accents | none | Reference | as above |
| Letterbox | `_wall` | none | Around the wall on screens that are not 3:4 | Neutral fill | none | Tint: `blue_ramp[0]` | None |
| Simulation badge | `screens.py → _badges` | `interface/operator/status-simulation.svg` | Bottom-left, simulation mode only | Tells the operator the mouse is the "ball" | Purple, white text | Reference (width follows the text) | None |
| Sensor-offline badge | `_badges` | `interface/operator/status-sensor-offline.svg` | Bottom-right, when the sensor is silent | Hardware warning | Red, white text | Reference | None |
| Operator notice | `_badges` | none | Bottom centre (red Latin text) | Short operator messages ("config warnings…") | none | Text | None |
| Debug panel (F3) | `overlays.py → draw_debug` | `interface/operator/debug-panel.svg` | Top-left, operator only | Diagnostics | Size follows the lines of text | Reference | None (operator tool) |
| Calibration overlay (F2) | `overlays.py → draw_calibration` | `interface/operator/calibration-overlay.svg` | Whole wall, operator only | 50 cm grid, field outline, start box outline, five aiming crosses | Labels are live text | Reference | None (operator tool) |
| Impact marker, accepted | `draw_calibration` | `interface/operator/impact-accepted.svg` | Calibration overlay | Where a sensor impact landed | Fades over 20 s | Reference | None |
| Impact marker, rejected | `draw_calibration` | `interface/operator/impact-rejected.svg` | Calibration overlay | Impact outside the wall | Red ring | Reference | None |
| Mouse cursor | system cursor (`App._update_cursor`) | none | Simulation, calibration, windowed mode | Operator pointer | Hidden in fullscreen kiosk | Operating system | None |

## 4. Feedback and transitions

| Asset | Before (code) | SVG file | Appears in | Purpose | Variants | Use | Designer work |
|---|---|---|---|---|---|---|---|
| Hit ripple (two rings) | `screens.py → on_event("hit")` → `Effects.ripple` | `feedback/ripple-hit.svg` | Around a hit box | Confirms the hit | colour = box colour | Reference: radius eases out, width shrinks to 40 %, colour fades into the background | None |
| Hit particles | `Effects.burst` → `Sprites.symbol` | brand elements (`brand/elements/*.svg`) | 12 (16 for red) symbols flying out of a hit box | Celebration in brand shapes | box colour ×3, dark blue, turquoise | Loaded (`symbol.*`, painted at runtime) | None |
| Word, points and multiplier pop-ups | `on_event("hit")` → `Effects.popup` | none | Over the hit box: "رشد / تنوع سبد / نوسان" (15 cm, box colour), "+N" (9 cm, dark blue), "×N" (7 cm, purple) | Score feedback | none | Text | None |
| Red flash | `on_event("hit")` → `Effects.flash` | none | Whole field, 0.35 s, after a red hit spawns boxes | "The market got busier" | none | Tint: red at 7 % fading out | None |
| Miss ripple | `on_event("miss")` | `feedback/ripple-miss.svg` | Where the ball hit the wall | A miss is acknowledged, not punished | light blue + grey | Reference | None |
| Start-box ripple | `on_event("start_hit")` | `feedback/ripple-start.svg` | Around the start box | Start confirmed | turquoise | Reference | None |
| Start-box pulse ring | `draw_attract` | `feedback/start-pulse-ring.svg` | Attract screen | Invites a throw | turquoise → dark blue each beat | Reference | None |
| Countdown ring | `screens.py → _countdown` | `feedback/countdown-ring.svg` | Countdown, around the digit | Rhythm of 3-2-1 | turquoise → white each second | Reference | None |
| Countdown digit, "آماده باشید" | `_countdown` | none | Countdown | — | digit scales 1.25 → 1 | Text | None |
| "شروع!" (go) | `draw_play` | none | First 0.9 s of play | Start signal | scales up, fades | Text | None |
| "پایان دور!" + reason | `draw_play` (ending) | none | End of the round | End signal | none | Text over a white tint | None |
| "توقف" (pause) | `_center_overlay` | none | When the operator pauses | — | none | Text over a white tint | None |
| Screen dims | `_dim_field`, `_center_overlay` | none | Countdown (67 %), ending (89 %), pause (82 %) | Focus on the message | none | Tint: white | None |
| Score pop, count-up | `_hud`, `draw_results` | none | HUD score (+16 % for 0.3 s), results score | Emphasis | none | Text animation | None |
| Time warning | `_hud` | none | Last 10 s: the seconds pulse to red | Urgency | none | Text colour | None |

## 5. Elements the game does not have

None of these were found in the code, so no assets were made for them. They are not part of
this game:

* Play, pause, restart, settings, back and exit buttons. The player only throws balls. The operator
  uses keyboard keys (README §2), which have no on-screen controls.
* Menus or settings screens. Settings are in `config/settings.json`.
* Success, failure and warning icons. Feedback is ripples, symbol particles and words; operator
  warnings are text pills.
* Loading screens or spinners. Start-up is fast and sprites are prewarmed between frames.
* Screen transitions other than the fades, pop-ins and slides listed above.

## 6. What stays procedural, and why

| Element | Why it is drawn in code |
|---|---|
| Rings and ripples | Radius, width and colour change every frame. A rasterised SVG per frame would be slower and look the same |
| Time and progress bars | Width changes every frame. The gradient is pre-rendered once per screen size |
| Banners, results pills, operator badges, debug panel | Width follows translatable text |
| Dot pattern and accents | Hundreds of dots placed to fit any screen size, pre-rendered once. The SVG backgrounds are generated with the same placement and seed |
| Box shadow | A blur. SVG filters are avoided (slow, inconsistent, not editable in design tools) |
| Tints and dims, letterbox | A colour, not a shape |
| All text | Persian shaping and right-to-left order, and the words are configurable |

## 7. Files that only document

* `documentation/original/`: the symbol and box geometry the game drew before this work (bars 0.27 of
  the size, × 5 % and / 15 % longer), kept as SVG for comparison.
* `images/*.png`: the original raster logos. The game still uses them as a fallback if an SVG logo
  fails to load, and they can be regenerated with `tools/extract_brand_assets.py --png`.
* `documentation/comparison/*.png`: every screen before (shapes in code) and after (SVG assets).
* `documentation/asset-sheet.png` and `asset-sheet.html`: every SVG on one page, plus original vs proposed.

## 8. Needs design or brand decisions

1. **Symbol proportions.** The marks inside the boxes and the particles now follow Brand Book p.23
   exactly: bars are one third of the module, and × and / are one module long. Before, the bars were
   0.27 and × and / were 5 % and 15 % longer. This is the only visible change. To go back, point the
   five `symbol.*` and three `target.*` entries in `manifest.json` at copies of the files in
   `documentation/original/`.
2. **Logo mark as the window icon.** This was already the behaviour (PNG crop). Confirm the brand allows
   the mark on its own.
3. **Dana (title font)** was not supplied. Titles use IRANSansX Black until Dana files are added to
   `assets/fonts/`, where they are picked up automatically.
4. **Latin brand font** for "STEADY PROGRESS" and operator text: Helvetica or Myriad is used if
   installed, otherwise Arial.
5. **Pattern density.** On page 22 the dot is 1/5 of the spacing. On the wall, the light field uses
   1.1 cm dots every 12.5 cm and the dark screens 1.2 cm dots every 12.5 cm. The density was chosen for
   a 3 × 4 m wall seen from a distance, so confirm it.
6. **Box shadow style.** Not specified by the brand book.
7. **Reference-only shapes** (section 6): if they are redesigned, the matching numbers are in the
   functions listed above. Colours alone can be changed through `design-tokens.json`, with no code
   change.
