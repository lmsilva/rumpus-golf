# Screens — layout & component spec (1920×1080, all values px)

Conventions: **TopBar** = 96 px tall, padding 0 56, brand "Rumpus Golf" Outfit 700 22, subtitle Manrope 400 18 muted, right side: controller badge then Back hint (B). **Hint(x)** = trailing glyph per INPUT.md. **Glass**, **Primary**, **Secondary** per TOKENS.md. Photo scrims per ASSETS.md. Every frame has radius 28 (window corners on TV; ignore on fullscreen).

---
## S01 Start
- Photo (Alex Gruber) right 1040 px, full height, saturate .75 brightness .8; gradient scrim from ground #15171c solid to x=780 → .85 at 980 → .15 at 1500.
- Padding 72×96. Top row: four 28 px player dots (orange, pink, blue, yellow), gap 12; controller badge pill right.
- Title "Rumpus / Golf" Outfit 800 200/1 −.05em, pushed to bottom of upper block; tagline Manrope 400 32 muted "Turn any floor into a mini golf course." max-width 720.
- Menu (max-width 560, gap 12, margin-top 56): Primary "New game" + Hint(A) · Secondary "Load last setup" with right-aligned meta "Living room · 3 courses · Sep 9" (Manrope 400 17 muted) · Secondary "Course library" · Secondary "Settings". Each 28 px Outfit 700, padding 20–22 × 28, radius 18.
- Bottom-right status pill (glass, radius 999): mint dot, "Kinect v1 connected", "· Lobby Time — Kevin MacLeod" muted.

## S02 Sensor check + load
- TopBar (subtitle "New game").
- Body grid 2 cols, gap 32, padding 56.
- Left card #1e2128 radius 24 padding 56×64: kicker "Sensor" mint; H2 "Kinect v1 found." 80/1; 2-col fact grid Manrope 23 (Depth 640 × 480 · Color 640 × 480 · Field of view 57° · Reliable range 0.8 – 4.0 m · Note: sunlight). Bottom: error card rgba(255,107,87,.12) fill, 1px rgba(255,107,87,.4), radius 18, coral 12 px dot, title "Error state — no sensor" Outfit 700 22, body copy; in-app this replaces the facts when no sensor answers, with a Primary "Retry".
- Right column gap 20: mint card (radius 24, padding 40×44) "Load saved setup" Outfit 800 40 + meta 2 lines Manrope 21 @ .8 + Hint(A) "Load" bottom; photo card (Minh Pham, scrim 90° .92→.75→.35) "New calibration" + "Five steps, about 3 minutes. Clear the floor first." + Hint(Y) "Start fresh".

## S03 Verify
- Full-bleed feed. Overlay: play-area polygon, start circle, hole circle, and every confirmed obstacle footprint (white .7 stroke, fill .08) with its label (Outfit 700 22 @ .8) — all from the saved config. Top-left glass panel (radius 24, padding 32×40, max 900): kicker "Verify · Living room", H 60/1 "Do the lines still sit on the floor?", body 21 soft "White = play area, start and the obstacles you confirmed. Mint = hole. If only something moved, Recalibrate lets you redo just that — cup, obstacles or zones." Top-right pill "LIVE · 640 × 480 · 30 fps".
- Bottom row (inset 56/48, gap 14): Primary "Looks right" Hint(A) min-width 400; Glass "Recalibrate" Hint(X).

## S04 Floor · S05 Play area (shared frame)
- TopBar with **step rail** after brand: 5 pills (Floor, Play area, Course, Cup, Balls & players) per TOKENS step-pill recipe; badge right.
- Body: grid "1fr 520px", gap 24, padding 32 56 48. Left = feed panel radius 24 (#242629). Right = side panel #1e2128 radius 24 padding 44: kicker "Step n of 5", H2 56/1, body 21 muted, actions pinned to bottom.
- S04 feed overlay: dark bands (rgba(13,15,18,.6)) above y=230 and below y=1000 of the 1080 feed space; mint 3 px lines at both; labels "4.0 m — too far…", "0.8 m — too close" Manrope 600 30 #f2efe8; "Good floor — fit a course inside this band" Outfit 700 30 mint at y=290. Action: Primary "Capture floor" Hint(A); helper text below. During capture: a 4 px mint progress line along the top edge of the feed, 2 s.
- S05 feed overlay: polygon stroke #f2efe8 4 px round joins fill .07; corners = 18 px #f2efe8 circles, the dragged one 22 px mint; dimension labels Outfit 700 34 ("3.1 m" above top edge centered, "2.2 m" beside right edge); state label "dragging corner 1" Manrope 600 26 mint. Bottom-left hint pills: L "Move corner", A "Place", X "Undo corner", Y "Close shape". Side panel: preset segmented (Small 2 × 1.5 m · **Medium 3 × 2 m** · Large 4 × 2.5 m) with sublabels Manrope 14; Primary "Use this area" Hint(A); Secondary "Clear corners" Hint(X).

## S06 Pick a course
- Photo (mark tulin) opacity .28 saturate .8 + scrim 180° .4→.92 at 60%. TopBar (subtitle "Step 3 of 5 · Course"; right: LB RB "Browse").
- Body padding 40 56 48: H2 60/1 "Pick a course for hole 1."; body 21 muted. 3 cards grid gap 20, flex:1.
- Card: radius 24 padding 28; unselected #1e2128 / text #f2efe8; **selected** #f2efe8 / text #15171c with 2 px #f2efe8 border. Header row: level kicker (13, .16em, uppercase, .7) + name Outfit 800 40/1; par pill right (Outfit 700 24, radius 999; selected → #15171c/#f2efe8, else fill .08). Map: 3:2 box radius 16 (selected #e3e0d8, else #15171c) with obstacles per COURSES.md, start ring 11% / 3 px in card fg, hole dot 7% mint. Blurb Manrope 18 @ .8. "You will need" kicker + list Manrope 17. Button row: selected → mint "Selected" + Hint(A); others fill .08 "Choose" + Hint(A).

## S07 Place your objects (Step 3b)
- Full-bleed feed. Overlay: play area polygon; per-obstacle projected quad + label; start circle + "START"; hole circle + "Cup goes here" mint. Top-left: brand + "Step 3 of 5 · <course>". Bottom-left hint pills: L Move · A Grab / drop · X Rotate 90° · Y Reset template · RB Next object.
- Right glass-strong panel: inset 40, width 480, radius 24, padding 36 (blur 24, rgba .72). Kicker "<Level> · Par n"; H2 48/1 "Build “<name>”."; body 17 muted; checklist rows (radius 14, fill .05, padding 14×16): 30 px tick circle (mint fill + ✓ when seen; transparent with .3 border when not), label Outfit 700 19, item Manrope 14 muted, size right Manrope 600 14 muted. Bottom: Primary "Scan obstacles" Hint(A) → S07b; Secondary "Swap course" Hint(LB). Copy: "Put each object roughly inside its ghost — the ticks are a hint. When everything is down, scan: the app measures what’s really on the floor and you confirm it."

## S07b Obstacles detected (Step 3c)
- Full-bleed feed. Overlay: play area; each proposed footprint = polygon fill rgba(139,233,195,.12), stroke mint 4 px dasharray 16 10; a 44 px mint numbered badge (Outfit 800 22, text #0f1a15) as an HTML label at the polygon's top-left corner (translate −50%,−50%). Low-confidence proposal: fill rgba(255,107,87,.14), stroke coral, coral badge. Start ring / hole circle drawn muted (.5/.6).
- Top-left: brand + "Step 3 of 5 · Obstacles · <course>"; below it a glass pill: mint dot "Scanned 1.5 s of depth · dashed = proposed, not yet on the course". Bottom-left hint pills: L Move cursor · A Select object · X Delete · Y Draw one · LB Undo.
- Right glass-strong panel (width 520, radius 24, padding 36): kicker "Step 3 of 5 · Obstacles"; H2 48/1 "Found n objects."; body 17 muted ("Outlines are approximate on purpose…"); rows (radius 14 fill .05, padding 12×14): numbered badge 30 px mint, label Outfit 700 19/1.3 + "item · size" Manrope 14 muted (both nowrap → scroll rule), confidence pill right (Outfit 700 13, mint ≥ 70% / coral < 70%, text #0f1a15). Low-confidence row: fill rgba(255,107,87,.1) + 1 px rgba(255,107,87,.35) border, copy "Low confidence — a shadow or a foot? Select and delete if so."
- Bottom: Primary "Confirm all" Hint(A) (disabled at .55 while any low-confidence proposal remains); 2-col Secondary "Re-detect" Hint(RB) · "Draw one" Hint(Y); note Manrope 14 muted.
- Scanning state (not drawn): title "Scanning…", mint indeterminate bar along the top of the feed for 1–2 s; proposals fade in together.

## S07c Correcting (Step 3c)
- Same feed. Line styles (legend pills top-left under the title, glass, 26×4 swatches): Selected = mint solid 5 px, fill rgba(139,233,195,.16), corner handles 11 px #f2efe8 with 3 px #15171c ring, the dragged corner 16 px mint; Confirmed = #f2efe8 solid 4 px; Pending = dashed rgba(242,239,232,.3); Deleted = coral .35 dashed 6 8, 3 px, with caption Manrope 600 22 rgba(255,107,87,.8) "deleted · LB to undo"; Drawing = #f2efe8 dashed 12 10 polyline with 11 px points. HTML label above the selected outline: Outfit 700 26 mint "<Label> · drag a corner to resize".
- Bottom-left hint pills: L Drag corner · A Confirm this one · X Delete · R Add / remove corner · LB "Undo · 3" (count of undoable steps).
- Right panel: kicker "Object 1 · <Label>"; H2 48/1 "Fix the outline."; body 17; status rows (radius 14 fill .05): 28 px number badge fill .1, label + item, status pill right (Selected = mint/#0f1a15; Confirmed = fill .14; Pending = outline .3; Deleted = outline .2 muted text; Drawing = mint outline, mint text).
- Bottom: Primary "Confirm this outline" Hint(A); Secondary "Course is set" at .55 with right meta "2 pending · 1 drawing" (enabled and full-opacity once nothing is pending/drawing); note about undo.
- Interactions: click/A on an outline selects; drag a handle to move it (Shift/fine = 1 cm); click an edge to insert a corner; stick-press/Delete removes a corner (min 3); X deletes the object; Y starts a manual polygon (click corners, A/Enter closes, ≥ 3 points); RB reruns detection keeping confirmed + drawn; LB undoes the last edit (unbounded within the step).

## S08 Cup
- Full-bleed feed. Overlay: cup illustration is the real camera image; app draws mint 60 px circle (5 px) on the ring, 10 px #f2efe8 resize dot on its right edge, label under it Outfit 700 28 mint "Hole zone · Ø 9 cm · confidence 0.94". Top-left glass panel: kicker "Step 4 of 5 · Cup", H 60/1 "Found the cup.", body 21 soft. While searching: title "Place the putting cup inside the play area." and a mint indeterminate bar.
- Bottom row: Primary "That’s the hole" Hint(A); Glass "Detect again" Hint(X); Glass "Draw it myself" Hint(Y).

## S09 Balls & players
- Full-bleed feed. Overlay per detected ball: 22 px circle in sampled color with 3 px #f2efe8 stroke; pill label (rx 22, 44 tall, ball color) "<Hue> · <Name>" Outfit 700 24 #15171c. Rejected ball: coral 5 px stroke + coral pill "Too close to the cup color — swap it". Top-left title Outfit 800 48 "Put every ball on the floor."
- Right glass-strong panel width 640: kicker "4 balls found · 1 rejected"; H2 48 "Who’s who?"; body 17; player rows (radius 16 fill .05, padding 10 12 10 16): order # (Outfit 700 16 muted, 20 wide), 48 px color circle, name input (#15171c fill, 1 px .15 border, radius 12, padding 12×16, Outfit 700 22) with hue right (Manrope 14 muted), drag handle ⋮⋮. Empty slot row dashed .2 border "Room for two more balls — max 6 players." Bottom: Primary "Save setup & continue" Hint(A); note about rumpus-setup.json.

## S10 Game start
- Photo (Waldemar Brandt) opacity .3 + scrim 90° .95 to 35% → .6. Grid "1fr 760px" gap 32 padding 56 (border-box).
- Left: kicker "Setup saved · Living room"; H2 "Game / night." Outfit 800 112/1 −.045em; "Holes this game" kicker + 9-segment control (track fill .06 pad 6 radius 16; option Outfit 700 26 padding 14; selected #f2efe8/#15171c) max-width 760; helper line; "Stroke cap" kicker + stepper (− / "8 strokes" / +, 56 px square buttons fill .08 radius 12); bottom: Primary "Tee off" Outfit 700 34 padding 26×32 radius 20 Hint(A).
- Right: kicker "Tee-off order · 4 players"; 4 equal-height rows (flex:1, gap 12) filled in player color, radius 24, padding 0 40, text #15171c: order # Outfit 800 64 @ .45, name Outfit 800 60/1.2 nowrap (flex:1, overflow hidden → scroll rule), "<HUE> BALL" Manrope 600 18 .12em @ .7 right.

## S11 Game HUD
- Full-bleed feed; overlay: play area (stroke .7), obstacle quads (stroke .55 fill .08), start ring (stroke .55), hole circle mint, trail circles, balls (20 px, player color, 3 px #f2efe8 stroke) with name label above (Outfit 700 24 in player color).
- Top row inset 40, gap 16: **Active card** (player color, radius 24, padding 26×40, shadow 0 20 50 .35): "Your turn" kicker (15, .18em, .7) + name Outfit 800 76/1.15 nowrap; divider 2 px rgba(21,23,28,.25) padding-left 36; "Stroke" kicker + number 76/1.15; divider; status Manrope 600 26/1.25 max-width 420 @ .85 ("Ball stopped. Putt when ready." / "Ball rolling…" / "Place your ball in the start zone"). **Other chips** right (margin-left auto, gap 10): glass radius 20 padding 20×24 min-width 190; 16 px dot + name Outfit 700 24/1.25 (max-width 150 → scroll rule); line Manrope 18 muted "Stroke n · in play" / "Holed · n".
- Bottom-left glass card radius 20 padding 18×24: kicker "Hole 2 of 3 · Beginner · Par 2" + course name Outfit 800 34/1.
- Bottom-right hint pills: X "Undo last shot", Menu "Pause".
- Right, y=230: motion pill (glass, radius 999, Outfit 700 20) with player-color dot: "Ball stopped · 0.8 m from cup".

## S11b HUD · hidden ball + new object prompt
- HUD as S11. Hidden ball: dashed circle r 22 (stroke player color 4 px, dasharray 8 6, fill player color @ .25) with a "?" (Outfit 800 24, player color) at the estimated position on the occlusion edge of the confirmed obstacle; caption pill below it (glass rgba .7, radius 999, padding 4×12, Outfit 700 24 in player color) "<Name> · hidden behind <Obstacle>". The player's chip line reads "Stroke n · hidden". Right status pill (y 230): "<Name>'s ball · stopped, hidden · position estimated" with the player's dot. No lost-ball bar, no re-assign dialog.
- New-object prompt: bottom-center glass card (rgba .8, blur 24, radius 20, padding 18 20 18 24, shadow .35): title Outfit 700 22 "New object on the course", sub Manrope 16 muted "Still for 3 s near the sofa. Add it so balls behind it aren’t “lost”?"; buttons: mint "Add as obstacle" Hint(A), quiet "Ignore" Hint(B) (Outfit 700 18, radius 12). The candidate is drawn on the feed as a white dashed polygon (14 10) labelled "New object?". Rules: appears only when no ball is moving; slides up 250 ms; auto-dismisses if the object disappears; Ignore remembers the region for the rest of the hole; Add confirms it immediately (no correction step mid-game — editable later via Pause → Recalibrate → Edit obstacles).

## S12 Turn change (1.4 s)
- Feed dimmed to .4. Colored panel in **next** player's color: left 0, width 1240, full height, radius 0 120 120 0, shadow 40px 0 120px rgba(0,0,0,.4); slides in from x=−1240 over 350 ms ease-out, holds, then the whole overlay fades 250 ms as the HUD (already recolored) shows.
- In panel (text #15171c, inset 96 bottom 96): kicker "Next up" Manrope 600 24 .18em @ .65; name Outfit 800 230/1.15 −.05em nowrap (→ scroll rule); line Manrope 600 32/1.3 @ .8 "Play the <hue> ball where it lies · stroke n" (or "Place the <hue> ball in the start zone" on first stroke).
- Top-right (outside panel, right-aligned): kicker "<Prev> stopped" muted; "Stroke n · in play" Outfit 800 56/1.1 with 24 px prev-player dot.
- Audio: two-note sting at t=0; announcer name at t=300 ms if enabled. Rumble 120 ms.

## S13 Hole-out (2.5 s)
- Feed; hole circle fill rgba(139,233,195,.4) stroke 6 px mint flashing (opacity 1↔.3, .5 s ease-in-out infinite); pulse ring r=120 stroke 6 px player color scaling 1→1.7 opacity .9→0 every 1.2 s; ball dot 18 px player color at hole.
- Banner: inset 40 top 40, player color, radius 28, padding 48×64, shadow 0 30 80 .4, text #15171c: "In!" Outfit 800 200/1 −.05em + right block "<Name> holed in n." Outfit 800 64/1.05, sub "On par · Par 2" Manrope 600 30 @ .7 (variants: "Under par — birdie!", "Over par, still counts"). Banner drops from y=−300 with 400 ms spring.
- Bottom-left hint pill X "Wrong call? Undo within 5 s — the ball goes back in play at the cup."
- Audio: cup plink at detection, cheer 200 ms later. Rumble 400 ms.

## S14 Out of bounds + lost ball
- Play-area stroke turns coral 5 px. Player-color 4 px line from last in-bounds point to ball; ball dot 20 px; coral 34 px ring (6 px) at exit point; label Outfit 700 30 coral right-aligned "Exit point — put the ball back here".
- Top row: active card (as S11, stroke shows "n + 1"), then coral card (radius 24, padding 26×40, text #15171c): kicker "Out of bounds", "+1 penalty" Outfit 800 52/1.
- Lost-ball bar: inset 40 bottom 40, glass-strong radius 24 padding 22×28: 20 px player dot; "Lost Sam’s ball." Outfit 700 28; explanation Manrope 21 muted; right: hint pill Y "Re-assign balls". Appears after 2 s unseen, slides up 250 ms, dismisses itself when the ball is found.

## S15 Pause (Resume focused) · S15b (Recalibrate focused)
- Photo (Clay Banks) saturate .75 brightness .7 + scrim 90° .55→.2 (45%)→.75. Feed hidden under it.
- Left menu panel: inset 40, width 680 (border-box), glass rgba .8 blur 30, radius 28, padding 48. Header: player-color dot + "Hole 2 · Leo to play" kicker muted; H2 "Paused." 88/1. Rows (gap 8, radius 16, padding 18×22, Outfit 700 26, fill .06): Resume (B) · Undo last shot (X) · Fix score (F) · Recalibrate… (R) · Change course for this hole (C) · Music & sound (M) · Quit to start (Q). Focused row = mint fill, text #0f1a15; its hint glyph shows the button that activates it (B on Resume, A elsewhere). Footer note Manrope 15 muted.
- **S15**: right-top glass card (width 520, radius 24, padding 28×32): "Hole 2 of 3 · Par 3" kicker + course name; per-player rows (16 px dot, name Outfit 700 22/1.25, state muted, strokes Outfit 800 26 right). Bottom-right pill: mint dot "Lobby Time — Kevin MacLeod · ducked to 30%".
- **S15b**: when "Recalibrate…" is focused (or A / → pressed on it) a flyout appears at left 744, width 900, aligned to the row (top offset 236 within the padded column): kicker "Recalibrate — pick only what moved" + 2×3 glass cards (radius 20, padding 24×28; title Outfit 700 26; body Manrope 17 muted): Re-detect cup · Re-assign balls · Redraw play area · Edit obstacles (→ S07b/S07c with confirmed footprints preloaded) · Redo floor snapshot · Verify only. Flyout slides in 200 ms from x+24, fades out on B or when focus leaves the row.

## S16 Undo & fix score
- Grid "1fr 700px" gap 32 padding 56. Left: kicker "Fix score · Hole 2"; H2 "Strokes this hole" 68/1; rows (#1e2128 radius 20 padding 18×24, gap 10): 44 px color dot, name Outfit 700 36/1.25 width 240 nowrap, state Manrope 20 muted width 180, stepper right (− / count Outfit 800 40 min-width 100 / +, 56 px buttons). Bottom: Primary "Apply" Hint(A) min-width 340; Secondary "Cancel" Hint(B).
- Right card #1e2128 radius 24 padding 40: kicker "Event log · newest first"; **Undo card** (#f2efe8 text #15171c radius 18 padding 20×24): kicker "Undo last shot" + line Outfit 700 24 "Leo · stroke 2 → 1 (mis-tracked bump)" + Hint(X); event rows (padding 14 0, 1 px .08 divider, Manrope 18): time Manrope 600 14 muted width 52, 12 px color dot, text.

## S17 Hole complete
- Grid "1fr 560px" gap 32 padding 56. Left: kicker "Hole 2 of 3 done"; H2 "Scorecard" 80/1; grid card (#1e2128 radius 24 padding 12×32): columns auto + N holes + Total; header cells kicker 13 muted (Total in mint), 1 px .1 rule; rows: 26 px dot + name Outfit 700 30/1.25 (max 260 → scroll), cells Manrope 28 centered (over par coral; best on hole weight 800; unplayed "○" @ .3), total Outfit 800 30; row rule 1 px .08. Legend Manrope 16 muted. Bottom: Primary "Build hole 3" Hint(A) min-width 440; Secondary "Replay this hole"; Secondary "Fix a score".
- Right: leader card (leader's color, radius 24, padding 40, text #15171c): kicker "Leading after 2" @ .65; name Outfit 800 72/1.2; line Manrope 600 22 @ .75 "9 strokes · 2 ahead of Maya". Up-next photo card (Kayla Farmer, scrim 180° .35→.92 at 70%, radius 24, content bottom-aligned padding 36×40): kicker "Up next · Hole 3" soft; name Outfit 800 44/1; "Expert · Par 4. Rebuild: …" Manrope 19 muted.

## S18 Game finish
- Grid 2 cols gap 32 padding 56. Left champion panel (winner color, radius 28, padding 56×64, text #15171c): kicker "Champion · 3 holes" Manrope 600 20 .18em @ .65; name Outfit 800 190/1.15 −.05em nowrap; "n strokes · +2" Outfit 700 40 @ .8; bottom: 280 px snapshot box radius 20 (color-camera still from the winning putt; placeholder = diagonal stripes rgba(21,23,28,.18/.08) 14 px) with caption pill bottom-left (#15171c, mono 15) "live snapshot from color camera · winning putt".
- Right: kicker "Final standings"; rows (#1e2128 radius 18 padding 16×24, gap 8): position Outfit 800 26 muted width 36, 28 px dot, name Outfit 700 34/1.25 flex:1 nowrap, total Outfit 800 34, vs-par Manrope 20 muted width 70 right. Kicker "Night stats" + 2×3 cards (#1e2128 radius 18 padding 18×22): label kicker 13 muted; value Outfit 800 34/1 with 16 px dot (player color or mint). Bottom row 3 equal buttons: Primary "Play again" Hint(A); Secondary "New courses"; Secondary "Start screen".

## S19 Settings
- Photo (Spacejoy) saturate .7 brightness .75 + scrim 90° .97→.92 (48%)→.35. Custom top bar (no border): brand, "Settings", badge, Back(B).
- Left rail: left 56, top 120, bottom 56, width 300: tabs (radius 14 padding 16×20 Outfit 700 22; active #f2efe8/#15171c, others fill .06): Display & sound · Game rules · Players · Camera · About; footer note Manrope 14 muted.
- Center column: left 388, width 860, gap 12; cards glass rgba(30,33,40,.85) blur 20 radius 20 padding 24×28:
  1. **Theme** — title Outfit 700 24 + desc Manrope 16 muted; segmented Dark · Light · Auto (Outfit 700 18, padding 12×24).
  2. **Music** — On/Off segmented (mint when on); Volume slider (label 120 wide, value 62%); Playlist chips (radius 999 Manrope 600 15): Lounge jazz (selected #f2efe8) · Lo-fi hip hop · Both, shuffled · "+ Add a folder" dashed.
  3. **Sound effects** — On/Off; Volume 80%; **Announcer** toggle row (title Outfit 700 20, desc Manrope 15 muted).
  4. Two toggles in a 2-col grid: **Controller rumble**, **Show camera feed**.
- Right column: left 1288, right 56: kicker "Theme preview · Light" soft with text-shadow; preview card (#f4f1ea text #15171c radius 20 padding 20 shadow .35): active mini card in player color (kicker 11 + name Outfit 800 40/1.2), 3 mini chips (#fff, 1 px .08 border, radius 12, 12 px dot + name Outfit 700 15/1.3), primary button #0f7a5a "Primary action" + Hint(A), note Manrope 13 #5c6068. Bottom: two glass link rows radius 16 padding 18×22 Outfit 700 22: "Credits →", "What’s new" + version pill (mint, Outfit 700 13).
- Other tabs (not drawn; use the same card recipe): Game rules = holes, stroke cap, OOB penalty on/off, tunnel bonus on/off; Players = saved names & colors; Camera = sensor facts, mirror feed, exposure lock; About = version, licenses link.

## S20 Credits
- Photo (Aaron Burden) saturate .8 brightness .7 + scrim 90° .3→.85 (45%)→.97. Top bar: brand, "Credits" soft, Back(B).
- Left bottom (left 56, bottom 56, width 560): kicker "Made with" mint; "Books, / a cup and / a camera." Outfit 800 88/1 −.04em with text-shadow 0 2 20 .5; line Manrope 18/1.5 soft "Rumpus Golf v0.3.0 · Python 3.11 · OpenCV · libfreenect / libfreenect2. No machine learning, no cloud — just your living room."
- Right (left 760, right 56, top 120, bottom 56): 2-col grid gap 16 of glass cards (radius 20 padding 24×28): kicker group (Music · Sound effects · Photography · Open source) mint; items stacked: title Outfit 700 19/1.3, desc Manrope 15/1.4 muted, license pill right (Manrope 600 12 mint, 1 px rgba(139,233,195,.35) border, radius 999). Footer note spans both columns. List scrolls with right stick when taller than the frame.

## S21 What’s new
- Photo (Katja Rooke) saturate .7 brightness .7 + scrim 90° .97→.9 (55%)→.3. Top bar: brand, "What’s new", Back(B).
- Left (left 56, top 120, width 420): kicker "Current version" mint; "v0.3.0" Outfit 800 120/1 −.05em; note Manrope 18/1.5 muted with tag legend (new mint · improved #f2efe8 · fixed coral); hint pill R-stick "Scroll releases".
- Releases column (left 540, width 900, top 120, bottom 56, gap 14): glass cards radius 20 padding 24×28: header row version Outfit 800 30, date Manrope 16 muted, tag pill right (Latest = mint/#0f1a15; others fill .1); items: 78 px tag column (Manrope 700 11 .12em uppercase, colored) + text Manrope 18/1.4. Older releases collapse to header only after the first; expand on A.
- Parsing: `data/CHANGELOG.md` — "## <version> — <date> · <tag>" headings, "- new:/improved:/fixed:" bullets.
