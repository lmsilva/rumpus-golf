# Design tokens — Rumpus Golf v2 "late night lounge"

## Color
| Token | Value | Use |
|---|---|---|
| ground | #15171c | screen background |
| panel | #1e2128 | cards, menu rows (opaque) |
| panel-2 | #272b34 | nested surfaces |
| glass | rgba(21,23,28,.70) + backdrop-filter: blur(20px) + 1px border rgba(242,239,232,.12) | any panel over the camera feed or a photo |
| glass-strong | rgba(21,23,28,.80–.85), blur(24–30px) | pause menu, settings cards |
| text | #f2efe8 | primary text |
| text-muted | #a3a8b4 | secondary text, kickers on dark |
| text-soft | #d8d5cd | secondary text over photos |
| line | rgba(242,239,232,.10) | dividers; .15 for stronger |
| fill-quiet | rgba(242,239,232,.06–.08) | secondary buttons, segmented tracks |
| mint (go) | #8be9c3 | primary actions, hole zone, positive states, kickers. Text on mint: #0f1a15 |
| mint-deep (light theme) | #0f7a5a | primary on light ground, text #f2efe8 |
| coral (warn) | #ff6b57 | out of bounds, rejected ball, over-par, "fixed" tag. Text on coral: #15171c |
| player orange | #ff8a3d | text on it: #15171c |
| player pink | #ff5fa8 | text on it: #15171c |
| player blue | #5b8cff | text on it: #15171c |
| player yellow | #ffd84d | text on it: #15171c |
| reserved (5th/6th players) | pick from ball hue at setup; never green (cup) | |
| Xbox A | #3aa655 / #fff | glyph fill / letter |
| Xbox B | #e04343 / #fff | |
| Xbox X | #3d7dff / #fff | |
| Xbox Y | #f2c230 / #15171c | |
| Xbox LB RB Menu stick | #3a3f4a / #fff | |
| keycap (no controller) | rgba(242,239,232,.14) fill, #f2efe8 text, radius 8 | |

Light theme: ground #f4f1ea, panel #ffffff with border rgba(21,23,28,.08), text #15171c, muted #5c6068, primary mint-deep #0f7a5a. Player colors unchanged. Contrast ≥ 4.5:1 for all body text in both themes.

Feed placeholder (when camera feed hidden): linear-gradient(180deg,#34373d,#1f2126) + repeating-linear-gradient(90deg, rgba(255,255,255,.04) 0 2px, transparent 2px 150px).

## Typography (Google Fonts, OFL)
- Display/UI: **Outfit** 500/600/700/800
- Body: **Manrope** 400/600/700
| Role | Spec |
|---|---|
| Hero name (S12/S18) | Outfit 800, 190–230px, line-height 1.15, letter-spacing −.05em, nowrap |
| Display H1 (S01 title) | Outfit 800 200px/1, −.05em |
| Screen H2 | Outfit 800 56–88px/1, −.03 to −.04em |
| HUD active name / stroke | Outfit 800 76px/1.15, −.035em |
| Card title | Outfit 800 40px/1, −.02em |
| Button (primary) | Outfit 700 26–34px |
| Button (secondary) | Outfit 700 20–28px |
| Menu row | Outfit 700 26px |
| Chip name | Outfit 700 24px/1.25 |
| Kicker | Manrope 600 13–15px, letter-spacing .16–.18em, uppercase |
| Body | Manrope 400 17–22px, line-height 1.45 |
| Hint label | Manrope 600 15–16px |
| Glyph letter | Outfit 800 13–15px |
Rule: any nowrap text container uses line-height ≥ 1.15 so descenders are never clipped.

## Radius
frame 28 · large panel/card 24 · card 20 · button-lg 18–20 · button 14–16 · row 14–16 · input 12 · segmented track 14–16 / option 10–12 · chip/pill 999 · glyph circle 50% · glyph rect 8

## Spacing
Screen safe inset 40 px (HUD) / 56 px (menus). Top bar height 96. Panel padding 44–48 (large), 24–28 (cards), 36 (feed side panel). Grid gaps 12–32. Menu row gap 8. Button padding 20–26 × 28–32.

## Shadow
frame: 0 30px 80px rgba(0,0,0,.5) · floating card over feed: 0 20px 50px rgba(0,0,0,.35) · slider knob: 0 4px 12px rgba(0,0,0,.4)

## Component recipes
- **Primary button**: flex, gap 16, mint fill, text #0f1a15, padding 22×28–30, radius 18, Outfit 700 28; trailing hint glyph (32 px) right-aligned via margin-left:auto.
- **Secondary button**: fill-quiet + 1px line, text #f2efe8; same geometry.
- **Glass button**: glass recipe; used on feed screens.
- **Hint pill**: inline-flex, gap 8, glass, padding 8–10 × 14–16 (left 8–10), radius 999, Manrope 600 15–16; glyph 28–30 px.
- **Segmented control**: track fill-quiet, padding 6, radius 14–16; selected option #f2efe8 text #15171c (or mint for on/off), radius 10–12, Outfit 700 18.
- **Toggle**: 64×36, radius 999, mint when on with 28 px #0f1a15 knob right; fill-quiet when off with #f2efe8 knob left.
- **Slider**: 10 px track fill-quiet, mint progress, 28 px #f2efe8 knob.
- **Step pill (calibration rail)**: pill with 24 px numbered circle; current = #f2efe8 fill / #15171c text with #15171c circle; done = mint circle + ✓; upcoming = muted.
- **Player card (HUD active)**: player color fill, text #15171c, radius 24, padding 26×40, sections divided by 2px rgba(21,23,28,.25).
- **Other-player chip**: glass, radius 20, padding 20×24, min-width 190; 16 px color dot + name + "Stroke n · in play".
- **Top bar**: 96 px, brand Outfit 700 22, subtitle Manrope 400 18 muted, right: controller badge (10 px mint dot + label) and Back hint.
- **Hole/start zone overlay**: play area stroke #f2efe8 @ .7–.8, 3 px, round joins, fill rgba(242,239,232,.04–.07); start circle r=70 (at 1920 scale) stroke #f2efe8; hole circle r=42 stroke mint 4 px fill rgba(139,233,195,.22–.25); obstacle outlines: seen → stroke #f2efe8 fill rgba(242,239,232,.12); not yet → stroke mint fill rgba(139,233,195,.14); OOB → play area stroke coral 5 px. Obstacle states: proposed = mint 4 px dasharray 16 10, fill rgba(139,233,195,.12); low-confidence proposed = coral, fill rgba(255,107,87,.14); selected = mint solid 5 px, fill .16, handles 11 px #f2efe8 (dragged 16 px mint) with 3 px #15171c ring; confirmed = #f2efe8 solid 3–4 px, fill rgba(242,239,232,.08); deleted = coral .35 dashed 6 8, 3 px; drawing = #f2efe8 dashed 12 10 polyline; hidden ball = dashed circle (8 6) in player color with "?".
