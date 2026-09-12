# Input map & hint rendering

## Verbs
| Verb | Mouse / touch | Keyboard | Xbox controller |
|---|---|---|---|
| Confirm / primary | click / tap | Enter · Space | A |
| Back / cancel | back button | Esc | B |
| Undo / alternate | secondary button | Z · R | X (in play: hold 0.5 s) |
| Secondary action | tertiary button | Y · D | Y |
| Move zone / obstacle / focus | drag | Arrows (Shift = fine 1 cm) | Left stick · D-pad |
| Resize circle | drag dot handle | + / − | Right stick ↕ |
| Next / prev object or course | click it | Tab · Shift-Tab | RB · LB |
| Pause / finish step | HUD button | Esc | Menu (≡) |
| Pause menu extras | click | F fix score · R recalibrate · C course · M music · Q quit | focus + A |
| Scroll long lists | wheel / swipe | PgUp/PgDn | Right stick ↕ |

## Detection
- Poll `navigator.getGamepads()` / SDL / pygame joystick each frame; treat "connected" when any pad with ≥ 8 buttons exists. Switch hint mode on connect/disconnect events with a 200 ms crossfade of the glyphs.
- Badge in every top bar: mint dot + "Controller connected" or "Keyboard & mouse".
- Focus ring: 2 px mint outline, radius inherits, moves with stick/D-pad; hidden while the mouse is moving; reappears on first stick input.
- Left-stick repeat: 350 ms initial, 90 ms repeat. Deadzone 0.25.
- Rumble (if setting on): 120 ms medium on turn change, 400 ms strong on hole-out, 60 ms weak on menu confirm.

## Glyph rendering
- Circle 28–32 px, letter Outfit 800 13–15 px. A green #3aa655, B red #e04343, X blue #3d7dff, Y yellow #f2c230 (letter #15171c), LB/RB rounded rect (radius 8, padding 0 8) #3a3f4a, Menu ≡ circle #3a3f4a, stick "L"/"R" circle #3a3f4a.
- Keycap fallback: same slot, rounded rect radius 8, fill rgba(242,239,232,.14), text #f2efe8, min-width 30, padding 0 8; labels "Enter", "Esc", "Z", "Y", "⇥", "Shift ⇥", "← ↑ → ↓", "+ / −".
- A hint is always attached to the element it triggers (trailing, margin-left:auto). Free-floating hint pills (feed screens) sit bottom-left of the feed, in action order.
- Long hint labels follow the overflow rule (scroll, never clip).

## Touch
All targets ≥ 56 px tall on 1080p. Drag handles (corners, circle dot) have a 48 px invisible hit area. Pinch is ignored (no zoom of the feed).
