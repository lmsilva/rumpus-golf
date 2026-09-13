# Handoff: Rumpus Golf — camera-based floor mini golf (UI + game flow)

## Overview
Rumpus Golf turns any living-room floor into a mini golf course. A Kinect (v1 or v2) — or a regular 2D webcam — on a tripod watches the floor; players putt real colored balls through DIY courses built from books, cushions and shoeboxes. The app tracks the balls, counts strokes, detects hole-outs and keeps score on a laptop or TV.

This package documents the **complete screen flow (25 screens, "golden thread")**: boot → 5-step calibration → tee-off → gameplay HUD → turn change / hole-out / out-of-bounds → pause & corrections → scorecard → finish, plus Settings, Credits and Changelog. It complements `requirements/mini-golf-poc-requirements.md` (the engineering POC spec — sensor layer, CV pipeline, rules). Where this handoff and the POC spec disagree on scope, **this handoff extends** the spec (4 players, 1–9 holes, 3 course templates, gamepad, settings). CV/sensor architecture is unchanged.

### Camera support
The screens and behaviour are identical for every input. The prototype runs on a **Kinect (depth + color)** or a **regular 2D webcam (color only)**; the differences are internal (floor mapping and detection heuristics) and are documented in the POC spec §3.4. Two calibration steps read slightly differently on a webcam:

- **S04 · Floor** — on a Kinect this averages depth and fits the floor plane; on a webcam it captures the empty-floor *color* reference used for later obstacle/cup differencing (no plane to fit).
- **S05 · Play area** — on a Kinect the user draws a polygon directly on the floor; on a webcam the user clicks the **four corners of a known-size rectangle** (Small 2×1.5 / Medium 3×2 / Large 4×2.5 m) and the app solves a homography.

Everything else — course, obstacles, cup, balls, gameplay — is the same flow. See `README.md` (repo root) and the POC spec for the limitations of a regular camera versus a Kinect.

## About the design files
`design/Rumpus Golf Flow v2.dc.html` is a **design reference built in HTML** — a static artboard document (1920×1080 frames) showing intended look and behaviour. It is **not production code**. Recreate the screens in the target stack using its patterns; if no UI stack exists yet, the recommended choice (per the POC's portability rule) is **PySide6/QML** or a **locally-served browser UI** driven by the Python game loop over a websocket. Do not use WinForms/WPF.

`design/Rumpus Golf Flow.dc.html` (v1) is an earlier, rejected visual direction — included only for history. Build v2.

## Fidelity
**High-fidelity.** Colors, type, spacing, radii and copy are final. Recreate pixel-accurately at 1920×1080; scale proportionally for other 16:9 displays (see Responsive).

## Reading order for the implementer
1. `README.md` (this file) — overview, screens, behaviour
2. `TOKENS.md` — colors, type, radii, shadows, component recipes
3. `SCREENS.md` — per-screen layout & component spec
4. `GAME_RULES.md` — state machine, events, undo stack, scoring
5. `INPUT.md` — mouse / keyboard / touch / Xbox controller map + hint rendering rules
6. `COURSES.md` + `data/courses.json` — the three course templates
7. `ASSETS.md` — royalty-free music, SFX, photos with licenses/attribution strings
8. `data/settings.schema.json`, `data/CHANGELOG.md` — settings shape, seed changelog

## Screens (summary — full spec in SCREENS.md)
| ID | Screen | Purpose |
|---|---|---|
| S01 | Start | New game / Load last setup / Course library / Settings. Music fades in. |
| S02 | Sensor check + load | Auto-detected sensor facts; Load saved setup vs New calibration; no-sensor error state. |
| S03 | Verify calibration | Saved zones over live feed; "Looks right" (A) / "Recalibrate" (X). |
| S04 | Cal 1 · Floor | Reliable-depth band shaded; "Capture floor" averages 2 s and fits the plane. |
| S05 | Cal 2 · Play area | Click/drag polygon corners; presets Small 2×1.5 / Medium 3×2 / Large 4×2.5 m. |
| S06 | Cal 3 · Pick course | Beginner / Advanced / Expert cards with top-down map, par, "you will need" list. |
| S07 | Cal 3b · Place your objects | Template ghosts guide placement; ticks are a live hint; "Scan obstacles" runs detection. |
| S07b | Cal 3c · Obstacles detected | Dashed footprints numbered on the feed with confidence; low-confidence flagged coral; Confirm all / Re-detect / Draw one. |
| S07c | Cal 3c · Correcting | Selected footprint with drag handles; confirmed (solid) vs pending (dashed) vs deleted; manual polygon mid-draw; undo. |
| S08 | Cal 4 · Cup | Auto-detected ring; confirm / detect again / draw manually. |
| S09 | Cal 5 · Balls & players | Sampled hues → players; names; tee order; green ball rejected. |
| S10 | Game start | Holes 1–9, stroke cap, tee-off order in player colors. |
| S11 | Game HUD | Live feed + zones + obstacles + trails; active player card in their color; others as chips. |
| S11b | HUD · hidden ball + new object | Ball behind a confirmed obstacle shown as dashed "?" at the occlusion edge ("stopped, hidden", never "lost"); non-blocking Add/Ignore prompt for a new static object, only while no ball moves. |
| S12 | Turn change | 1.4 s full-bleed wipe in next player's color + two-note sting. |
| S13 | Hole-out | "In!" banner in player color; hole zone flashes; pulse rings; cheer. |
| S14 | Out of bounds / lost ball | +1 penalty card; exit point marked; non-blocking lost-ball bar. |
| S15 | Pause | Menu (Resume focused) + quiet scoreboard card; music ducks to 30%. |
| S15b | Pause · Recalibrate flyout | Six granular recalibrate options fly out only while that row has focus. |
| S16 | Undo & fix score | ± strokes per player; event log is the undo stack. |
| S17 | Hole complete | Scorecard grid; leader card; "Up next" course card with rebuild list. |
| S18 | Game finish | Champion in their color; standings; night stats; play again. |
| S19 | Settings | Theme dark/light/auto; music on/off/volume/playlist; SFX volume; announcer; rumble; camera feed. |
| S20 | Credits | Generated from the asset manifest; CC attributions verbatim. |
| S21 | What's new | Versioned changelog from CHANGELOG.md; tags new / improved / fixed. Hidden when file is empty. |

## Global behaviours
- **Turn clarity**: exactly one element on screen is saturated in the active player's color at any time (HUD card, wipe, celebration, pause accent). Everything else is charcoal/glass. Never show two players' colors as fills simultaneously except tee-order and standings lists.
- **Text overflow rule**: nothing wraps, clips or overlaps. Any text wider than its slot (names, hints, course titles, chip labels) **scrolls end-to-end and back** (translateX marquee: 20% hold, glide, 20% hold, ~6 s per cycle, ease-in-out) — measured at runtime (scrollWidth > clientWidth). No ellipsis anywhere.
- **Hints**: every action shows its trigger. With a controller connected, Xbox glyphs; otherwise keycaps. Both input paths always work (see INPUT.md).
- **Obstacles**: detection proposes, the user confirms. Line styles: mint dashed = proposed, mint solid + handles = selected, white solid = confirmed, faint coral dashed = deleted (undoable), white dashed polyline = drawing. "Course is set" is locked until nothing is pending; a low-confidence proposal must be confirmed or deleted before Confirm all. Obstacles are informational only (no physics) — see GAME_RULES.md.
- **Photography**: ambient photos sit behind non-feed screens at 28–35% opacity (or brightness .7–.75 + gradient scrim). Feed screens (S03–S05, S07–S09, S11–S14) never have photos — the camera is the picture.
- **Music**: menus/setup play "Lobby Time"; play state plays "Backed Vibes Clean" (or playlist per settings); ducks to 30% on Pause and Settings; −18 LUFS target.
- **Persistence**: rumpus-setup.json (calibration), rumpus-settings.json (settings), rumpus-game.json (in-progress game autosave after every event).
- **Responsive**: design at 1920×1080; scale the whole scene uniformly to fit 16:9 (letterbox otherwise). Minimum HUD type at 1080p is 15 px for kickers, 18 px for body, 26 px for chip names; TV mode = same layout, no changes.

## Files in this package
- `design/Rumpus Golf Flow v2.dc.html` — the design reference (open in a browser; canvas pans/zooms). Tweaks panel props: inputMode, activePlayer, course, holeCount, strokeCap.
- `design/Rumpus Golf Flow.dc.html` — v1 (history only)
- `requirements/mini-golf-poc-requirements.md` — engineering POC spec
- `TOKENS.md`, `SCREENS.md`, `GAME_RULES.md`, `INPUT.md`, `COURSES.md`, `ASSETS.md`
- `data/courses.json`, `data/settings.schema.json`, `data/CHANGELOG.md`
