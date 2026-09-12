# Game rules & state machine

## States
`BOOT → SENSOR_CHECK → (VERIFY | CAL_FLOOR → CAL_AREA → CAL_COURSE → CAL_PLACE → CAL_CUP → CAL_BALLS) → GAME_START → HOLE_START → PLAY ⇄ (TURN_CHANGE | HOLE_OUT | OOB | LOST_BALL) → HOLE_COMPLETE → (HOLE_START | GAME_FINISH)`
Modal overlays from PLAY: PAUSE → (RESUME | UNDO | FIX_SCORE | RECAL_* | CHANGE_COURSE | SETTINGS | QUIT). Recalibrate re-enters the matching CAL_* state with current values prefilled and returns to PAUSE on Back.

## State details
| State | Screen | Enter | Exit |
|---|---|---|---|
| BOOT | S01 | app start; music fade-in 1.5 s | New game → SENSOR_CHECK; Load → SENSOR_CHECK(load=true); Settings → SETTINGS |
| SENSOR_CHECK | S02 | probe USB VID/PID; prefer v2 | sensor found → Load (VERIFY) or New (CAL_FLOOR); none → error card + Retry |
| VERIFY | S03 | saved zones drawn over live feed | A → GAME_START; X → PAUSE-style recal picker (S15b flyout, no game context) |
| CAL_FLOOR…CAL_BALLS | S04–S09 (CAL_PLACE = S07 → CAL_OBSTACLES = S07b/S07c) | rail pill = current step | A/Y confirms → next step; B → previous step (values kept) |
| GAME_START | S10 | setup saved | A "Tee off" → HOLE_START |
| HOLE_START | S07 (rebuild) for holes ≥ 2, else PLAY | shows the hole's course to rebuild | Menu/"Course is set" → PLAY |
| PLAY | S11 | active player waits with ball in start zone (hole start) or where it lies | ball stops → resolve → TURN_CHANGE / HOLE_OUT / OOB; Menu → PAUSE; X hold → UNDO |
| TURN_CHANGE | S12 | 1.4 s wipe | auto → PLAY |
| HOLE_OUT | S13 | 2.5 s celebration; undo window 5 s | auto → TURN_CHANGE or HOLE_COMPLETE |
| OOB | S14 (top cards) | +1 applied; exit point drawn | player replaces ball; stopped in bounds → TURN_CHANGE |
| LOST_BALL | S14 (bottom bar) | overlay on PLAY, non-blocking; NOT entered when the ball is "stopped, hidden" behind a confirmed obstacle | ball found → clear; Y → CAL_BALLS(reassign) |
| HIDDEN_BALL | S11b | last position + heading lead into a confirmed footprint, ball unseen | matching-hue blob within ~30 cm of the estimate → PLAY (same identity, no confirmation) |
| NEW_OBJECT_PROMPT | S11b | static above-floor region inside the play area for ~3 s while no ball moves | A → add as confirmed obstacle; B → ignore for the rest of the hole; auto-dismiss if it disappears |
| PAUSE | S15/S15b | music duck 30%, feed dims | Resume/B → PLAY; rows → sub-states |
| FIX_SCORE | S16 | current hole counts | Apply → PLAY (writes MANUAL_ADJUST); Cancel → PAUSE |
| HOLE_COMPLETE | S17 | all players holed/capped | A → HOLE_START(next) or GAME_FINISH; Replay → HOLE_START(same, reset hole) |
| GAME_FINISH | S18 | totals, stats, snapshot | Play again → HOLE_START(1, scores reset); New courses → CAL_COURSE; Start → BOOT |
| SETTINGS | S19 | duck music | B → previous state |
| CREDITS / CHANGELOG | S20 / S21 | | B → SETTINGS |

## Core rules (from POC spec, extended)
- Players: 2–4 (UI allows up to 6 balls). Each has a name, a sampled hue range, a color chip (never green, never mint/coral).
- Holes per game: 1–9 (default 3). Course per hole cycles Beginner → Advanced → Expert; changeable from Pause → Change course.
- Stroke = ball transitions stopped → moving (stopped: < 2 cm over 0.5 s). Increment active player's stroke for the hole; play a putter tick.
- Turn ends when the ball stops. Then:
  - **Holed**: stopped inside hole zone (v2: also height ≈ cup rim) → HOLE_OUT celebration 2.5 s (banner + flashing zone + pulse rings + cheer). Player is done for the hole.
  - **Out of bounds**: stopped or last seen outside play area → +1 stroke, mark exit point (last in-bounds position) on the feed, prompt to replace there; same player continues after the other players' turns.
  - **In play**: pass to next player who hasn't finished the hole.
- Stroke cap (default 8, 3–12): reaching it ends the player's hole at cap.
- Turn change = TURN_CHANGE wipe 1.4 s: next player's color slides in from the left (border-radius 0 120px 120px 0), name at 230 px, "Play the <hue> ball where it lies · stroke n"; two-note vibraphone sting; announcer says the name if enabled. HUD recolors underneath during the wipe.
- Hole complete when every player is holed or capped → S17. Game finish after last hole → S18 (winner = lowest total; ties broken by fewest strokes on last hole, then shared).

## Tracking events surfaced in UI
- Lost ball > 2 s: bottom bar "Lost <name>'s ball." (player color dot), non-blocking; auto-resumes; Y → re-assign balls flow.
- Ambiguous re-appearance (two similar hues): auto-pause into S09-style re-assign.
- Ball motion status pill on HUD: "Ball moving" / "Ball stopped · x m from cup".
- Trail: last ~7 positions of the active ball as circles, radius 6→14, opacity .2→.9, in player color.

## Obstacles
- Detection (CAL_OBSTACLES): compare averaged live depth (1–2 s) against the empty-floor reference; every new static above-floor region inside the play area, excluding the cup and ball-sized blobs, becomes a **proposed** footprint polygon (simplified to 4–12 points, floor coordinates in meters). Confidence = fraction of frames the region was present × blob-size plausibility; < 0.7 is flagged.
- States per obstacle: proposed → selected → confirmed | deleted; manual polygons start as drawing → confirmed. Only **confirmed** footprints are saved, rendered in-game, used by tracking, and shown at verify-on-load.
- Edit stack: every action (delete, reshape, add corner, remove corner, draw, re-detect, confirm) pushes an entry; LB/Z pops. Re-detect replaces proposed items only.
- "Course is set" requires zero proposed/drawing items. Confirm all is blocked while a flagged (< 0.7) proposal exists.
- Obstacles have no physics. They render on the overlay and the top-down map and drive the "stopped, hidden" rule. A ball stopped on top of one is just "in play".
- Hidden rule: a ball whose last position and heading intersect a confirmed footprint and which is not re-detected is "stopped, hidden" (never "lost"). Estimated position = intersection point on the footprint edge; drawn as a dashed "?" ball with caption. Re-acquire immediately on a matching-hue blob within ~30 cm. The lost-ball banner and re-assign dialog are suppressed for hidden balls.
- Mid-game new object: prompt (S11b) only while no ball is moving; never blocks input; ignored regions are remembered for the hole.
- Portability: detection lives behind the sensor layer; everything above it (states, edit stack, config polygons, hidden rule) is sensor-agnostic (future AR/tablet edition swaps only the detector).

## Undo & fix score
- Event log (append-only, persisted): STROKE, OOB, HOLE_OUT, CAP, MANUAL_ADJUST, TURN.
- Undo (X / Z) pops the latest event of the **current hole**, reverting counts and turn; available for 5 s after HOLE_OUT directly from the celebration, and any time from HUD/Pause.
- Fix score (S16): ± per player for the current hole; Apply writes MANUAL_ADJUST events (never rewrites history). Cancel discards.

## Scoring display
- Scorecard grid: players × holes + Total. Over par in coral; best on hole bold; unplayed "○".
- Standings: sorted by total; vs-par label "even" / "+n" / "−n".
- Night stats (S18): best hole, longest holed putt (m, from tracker), most OOB, tunnel shots (Advanced), total strokes, time played.

## Audio
- Music: Lobby Time (menus/setup), Backed Vibes Clean (play), or user playlist folder. −18 LUFS; duck to 30% in PAUSE/SETTINGS; fade 400 ms.
- SFX: menu click/confirm/back (Kenney Interface), putter tick + book thunk + cup plink (Kenney Impact), cheer (Freesound CC0), turn sting (two-note vibraphone).
