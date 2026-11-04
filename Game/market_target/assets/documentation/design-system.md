# Market Pulse: visual design system

The rules every graphic in the game follows, taken from the existing game and the Mofid brand book.
Use it to extend or restyle the game without breaking its look. For the list of every asset, see
`asset-inventory.md`. To see every file at once, see `asset-sheet.png` (or `asset-sheet.html` in a
browser).

## 1. Sources and assumptions

| Source | Used for |
|---|---|
| Mofid Brand Book v0.4 (`Brnadbook-V04.pdf`), pp. 4–5 | Logos, copied path for path into SVG (no tracing, no redrawing) |
| p. 7 | White logo allowed on turquoise, dark-blue and grey backgrounds |
| pp. 8–9 | Primary and secondary colours, the tint ramps printed under them, and colour usage rules |
| p. 22 | Dot pattern and the "MOFID SECURITIES / STEADY PROGRESS" lettering |
| p. 23 | The five graphic elements and their proportions (measured from the PDF vectors) |
| p. 24 | The pattern in use (letterhead, grey panel), which is where the neutrals come from |
| The game itself (before this work) | Sizes, layout, radii, line weights, motion |

Assumptions, stated so they can be checked:

* The neutrals grey `#B1BAC0`, light grey `#CED8DE`, text grey `#546670` and pattern blue `#005379` are
  read from the brand book layouts (pp. 22–24). They are not named swatches.
* The logo artwork is printed in turquoise `#00AAA5` (step 4 of the turquoise ramp), not in the
  primary turquoise `#00A796`. Both are kept exactly as the book has them.
* No guidance was found for shadows, motion or a standalone mark. Those are the game's own choices
  and are flagged in `asset-inventory.md` §8.

## 2. Units

* **1 unit = 1 cm on the wall** for every interface SVG with a fixed size. The wall is 300 × 400 cm in
  portrait, so a file's viewBox shows its true proportions on the wall.
* **Targets and the start box** use a 100-unit viewBox (percent of the side), because their size comes
  from the settings (`targets.*.size_cm`, `layout.start_button.size_cm`).
* **Graphic elements** use a 48-unit viewBox, which is one module (see §6).
* On screen, the wall is letterboxed to keep 3:4. Nothing is ever stretched.

Layout grid (wall cm): HUD band 0–64, field 64–370, footer 370–400. The side margin is 12, the gap
between boxes 12, and every box moves at least 35 after a hit.

## 3. Colour

All colours are tokens in `assets/design-tokens.json`, with the same names as in
`market_pulse/ui/theme.py`. Changing a token recolours the shapes drawn in code and every "themed"
SVG. The logos are never recoloured.

| Token | Value | Role in the game | Brand rule (pp. 8–9) |
|---|---|---|---|
| `turquoise` | `#00A796` | Green box (growth), start box, rank 1, "go", positive accents | Mofid colour; growth, profit; CTA; titles |
| `dark_blue` | `#004C6A` | Blue box (diversification), HUD band, dark screens, body text on white | CTA, backgrounds, titles |
| `red` | `#F3716D` | Red box (volatility), time warning, spawn flash, sensor warning | Volatility / decline. **Not text, not background** |
| `purple` | `#832C73` | Active multiplier, best-multiplier pill, simulation badge | Highlight / index. **Not text, not CTA, under 30 % of a background** |
| `white` | `#FFFFFF` | Wall, cards, text on dark | — |
| `turquoise_ramp` | `#00817D` … `#DEF1EE` | [3] logo turquoise, [5] light accents and names, [6] points pill | Tints printed on p. 8 |
| `blue_ramp` | `#00243D` … `#B5D5E5` | [0] letterbox and shadow, [2] results bar track, [5] used balls and outlines, [6] pale labels and rank badges | Tints printed on p. 8 |
| `grey` | `#B1BAC0` | Miss ripple | Layout neutral |
| `grey_light` | `#CED8DE` | Light dot pattern, time-bar track, footer divider | Layout neutral |
| `text_grey` | `#546670` | Secondary text | Layout neutral |
| `pattern_blue` | `#005379` | Dots on dark blue | Pattern colour (p. 22) |

The only gradient is dark blue → turquoise, used on the time and instructions bars. The brand book
allows gradients for charts and icons (p. 9).

**Usage to review with the brand team** (it already existed before this work and was not changed):
red is used for text in the time warning, the word "نوسان" (the red box's word, coloured like its box
on the instructions, attract card and hit pop-up) and operator notices. Purple is used for text in the
"×N" pop-up and the "ضریب ×N!" banner. Both conflict with the "not text" rules above. Possible fixes
are dark-blue text with a red or purple mark next to it. Since this changes how the colour coding
reads, it is left for a decision.

## 4. Typography

The text is live, not artwork: Persian shaping and right-to-left order need it, and every string is
editable in `config/settings.json`. Sizes are the font size in wall cm.

| Role | Font (first one found) | Used for, size in cm |
|---|---|---|
| title | Dana Black / Bold → IRANSansX Black | Game title 38, "شروع!" 34, end / pause 30, start label 21, hit word 15, instruction words 12 |
| heading | IRANSansX ExtraBold / Bold | Band title 14, "آماده باشید" 12, rule title and "آفرین" 10, card words 8.5, leaderboard and results lines 8, multiplier and banner 7, pills 6.5 |
| body | IRANSansX Medium / Regular | Reason line and results title 9, subtitle 8, CTA 7.5, footer lines 6.5, rules 6.3, HUD labels 6, meanings 5.8, small words 5.2–5.5 |
| numbers | IRANSansX Black / ExtraBold | Countdown 80, results score 72, HUD score and time 31, results counts 15, "+N" 9, "×N" and leaderboard 7, rank digit 5.5 |
| bold | IRANSansX Bold | Operator badges 3.4 |
| latin | Helvetica → Myriad → Arial → IRANSansX | "STEADY PROGRESS" 4.2 (letter-spaced), operator and calibration text 2.4–4 |

Digits are Persian by default (`ui.digits`). Dana is the brand title font, but it was not supplied.

## 5. Shape language

* **Flat colour, no outlines.** The only outlines are the inactive states: the ×1 multiplier and a used
  ball (0.5 cm).
* **Rounded squares** for anything you throw at (boxes, start box): corner radius 18 % of the side.
* **Pills** for labels: the radius is half the height (multiplier 46 × 13, points 50 × 15, banners and
  results pills follow the text). Operator badges use a smaller radius (2.5 cm), so they read as
  "system" rather than "game".
* **Cards** are white with a 4–5 cm radius: attract card 256 × 80 (r 5), results card 80 × 58 (r 4).
* **Circles** for countable things (balls, rank badges, impact markers) and for every ring.
* **Brand elements** keep square ends and sharp corners. They are never rounded.

## 6. Graphic elements (Brand Book p. 23)

Measured from the PDF: module M = 38.98 pt; every bar is M/3 = 12.99 pt thick.

| Element | Geometry | Inside a box | Colours in use |
|---|---|---|---|
| + plus | two bars M × M/3, crossed | green box | white in boxes; any token for particles and accents |
| − minus | one bar M × M/3 | — | accents, particles |
| × times | the plus turned 45° (bounding box 0.943 M) | red box | as above |
| / slash | one bar M × M/3, rising to the right at 45° | blue box | as above |
| • dot | circle, diameter M | — | accents, particles |

* Inside a box, the element's module is **42 % of the box side**, centred.
* Background accents use a 2.2 cm module (light screens) or 2.6 cm (dark screens). Particles are
  3–5.5 cm and shrink by 40 % as they fade.
* The game paints the element files in whatever colour a use needs, so the files' own colours (dark
  blue / turquoise as on p. 23) are only for designers. Keep the shapes one flat colour.

## 7. Pattern (pp. 22–24)

An even grid of small dots, with a few grid points replaced by brand elements:

| Surface | Spacing | Dot Ø | Dot colour | Accents |
|---|---|---|---|---|
| Light wall (gameplay) | 12.5 cm | 1.1 cm | `grey_light` | 6 (+ / − × •, light turquoise and soft blue, 2.2 cm) |
| Light wall (instructions) | 12.5 cm | 1.1 cm | `grey_light` | none, to keep the text calm |
| HUD band | 6 cm | 0.9 cm | `pattern_blue` on `dark_blue` | none |
| Dark screens | 12.5 cm | 1.2 cm | `pattern_blue` on `dark_blue` | 8 (turquoise and white, 2.6 cm) |

On p. 22 the dot is 1/5 of the spacing. The wall uses a sparser grid (about 1/11) so the boxes stay
the strongest shapes from a distance.

## 8. Line weights (cm on the wall)

| Line | Weight |
|---|---|
| Hit ripple (outer / inner) | 2.2 / 0.9, thinning to 40 % as it grows |
| Start-box ripple | 2.5 |
| Countdown ring | 1.6 |
| Start-box pulse ring | 1.2 |
| Miss ripple (blue / grey) | 1.0 / 1.4 |
| Spawn ring | 0.9 |
| Inactive outlines (×1 multiplier, used ball, rejected impact) | 0.5 |
| Footer divider | 0.25 |
| Calibration: crosses / field / grid | 0.6 / 0.4 / 0.2 |

## 9. Depth

Only the boxes have a shadow, which keeps them the one "physical" thing on the wall: the box outline
in `blue_ramp[0]` at 27 % opacity, 5 % of the side lower, blurred by 6 % of the side. It can be turned
off with `ui.target_shadow`. There are no glows, bevels or inner shadows. Emphasis comes from scale
and motion instead (§11).

## 10. States

| Component | States |
|---|---|
| Box | normal; pulse (size breathes per colour); pop-in when placed (with a spawn ring); hit (ripples, particles, word); moving between layouts |
| Start box | idle pulse ±3 % with a ring every 1.6 s; hit (ripple) |
| Multiplier badge | inactive ×1 (outline, pale text) → active ×2+ (purple fill, white text); level-up pop +15 %; shake when the combo breaks |
| Ball indicator | remaining (white dot) → used (light-blue ring) |
| Time | normal → warning in the last `flow.time_warning_s` seconds (digits pulse to red) |
| Rank badge | first place (turquoise, white digit) / others (pale blue, dark-blue digit) |
| Attract card | how to play ↔ leaderboard, cross-fading |
| Impact marker (operator) | accepted (turquoise dot) / rejected (red ring), fading over 20 s |
| Disabled / selected | Not used: nothing on the wall is selectable or disabled |

## 11. Motion (for reference; unchanged)

Ease-out cubic everywhere. Ripples 0.3–0.7 s, particles about 0.75 s, pop-ups 0.95–1.15 s
(grow to 115 %, settle, rise and fade), banners 1.3–1.5 s, spawn 0.32 s, layout transition 0.35 s,
countdown ring once per second, score pop 0.3 s, results count-up 1.2 s.

## 12. File conventions (for designers)

* **One asset per file**, kebab-case names: `<thing>-<variant>.svg` (for example `target-red.svg`,
  `multiplier-inactive.svg`, `logo-fa-stacked-white.svg`).
* **Keep the viewBox.** The game places files by their viewBox, so it is the box's hit area or the
  card's exact size. Artwork fills it edge to edge, with no padding, except the graphic elements,
  which sit centred on their 48-unit module.
* **Flat fills from the token palette**, written as `#RRGGBB`. This is how "themed" files are
  recoloured when a token changes, so do not use named colours or `rgb()`.
* **No text, fonts, images, clipping paths, masks, filters or `<use>`.** The game's renderer (nanosvg)
  ignores filters and `<use>`, and text must stay live. `tests/test_assets.py` checks all of this.
* **Named groups** (`tile`, `symbol`, `mark`, `wordmark`, `tagline`, …) so the layers make sense in
  Illustrator and Figma.
* White variants are separate files (`*-white.svg`), as the brand book treats them.

## 13. Changing artwork

1. Edit or replace the SVG under `assets/`, keeping the file name and viewBox.
2. Run `python tools/render_asset_sheet.py` to refresh the asset sheet and check the result.
3. Run `python -m pytest -q tests/test_assets.py` to confirm the file is clean and renders.
4. Run `python main.py --gallery out/` to see every screen with the new artwork.

To change colours only, edit `assets/design-tokens.json`. To swap a file for a different one, point
its entry in `assets/manifest.json` at the new path. To fall back to the shape drawn in code, remove
the entry.

`tools/build_svg_assets.py` and `tools/extract_brand_assets.py` created the starting files. They never
overwrite an existing file unless they are given `--force`.
