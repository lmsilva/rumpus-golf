# Rumpus Golf

Turn any living-room floor into a mini golf course. A Kinect (v1 or v2) or a
regular webcam on a tripod watches the floor; players putt real colored balls
through DIY courses built from books, cushions and shoeboxes. The app tracks
the balls, counts strokes, detects hole-outs and keeps score on a laptop or TV.

This is a **prototype** implementing the full design handoff
(`requirements/design_handoff_rumpus_golf/`) and the POC spec
(`requirements/…/mini-golf-poc-requirements.md`): 25 screens, a 5-step
calibration, obstacle detection & correction, ball tracking with occlusion
("hidden") handling, and the complete game flow for 2–6 players over 1–9 holes.

The HTML files in `requirements/design_handoff_rumpus_golf/design/` are
documentation artboards only. Opening them in a browser fetches React and Babel
from unpkg; they are not part of the served application.

---

## Architecture

The core rule of this codebase: **the UI is a dumb display and Python owns
everything that matters.** That seam is what makes the UI replaceable and the
Python core portable (Windows → Linux → future tablet/AR edition).

```
┌─────────────── browser (web/) ───────────────┐      ┌────────── Python (rumpus/) ──────────┐
│  screens.js   HTML/CSS/JS renderers           │      │  engine.py    state machine + rules  │
│  feed.js      JPEG canvas + SVG overlay       │◄────►│  vision/      floor/ball/obstacle CV │
│  audio.js     WebAudio SFX + music            │  ws  │  sensor/      Kinect v1/v2/webcam/mock │
│  main.js      websocket client + input        │      │  game/        course/scoring/events  │
└───────────────────────────────────────────────┘      └──────────────────────────────────────┘
```

- **State over websocket** is a hard boundary. The server publishes a JSON
  snapshot + JPEG feed frames; the browser renders and sends back user intent
  (`action`, `pointer`, `set`, `text`). The browser never holds game state.
- **Sensor abstraction** (`rumpus/sensor/`). Game/vision code never imports a
  driver. `SensorBackend` exposes color (+ optional depth) + a
  `SensorDescription`, and four backends implement it: `KinectV1Backend`
  (libfreenect), `KinectV2Backend` (pylibfreenect2), `WebcamBackend` (regular
  2D camera, color-only) and `MockBackend` (simulated floor).
- **Coordinate mapping** (`rumpus/vision/geometry.py`). All game logic is in
  floor meters; pixel↔3D↔floor conversion lives in one `FloorMapper`. For a
  color-only webcam there is no depth plane, so `HomographyMapper` solves the
  same mapping from four clicked corners of a known-size rectangle.
- **Portability note.** Obstacle detection sits behind the sensor layer too;
  everything above it (states, edit stack, config polygons, the "hidden" rule)
  is sensor-agnostic. A tablet/AR edition swaps only the detector.

## Stack

Python 3.11+, OpenCV, NumPy, FastAPI + uvicorn, and a vanilla HTML/CSS/JS
front-end. No Windows-only APIs. No machine learning.

## Run

Requires Python 3.11+ (not yet installed on the current dev machine — install
from python.org, then the steps below).

```powershell
# 1. create + activate a venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. install deps
pip install -r requirements.txt

# 3. run (auto-detects: Kinect → webcam → mock)
python run.py

# or force the simulated sensor / a specific backend
python run.py --mock
python run.py --sensor v1
python run.py --sensor v2
python run.py --camera 0 --resolution 1280x720
```

Open **http://127.0.0.1:8000** in a browser (fullscreen on a TV: press `F`).

## Security

The server streams a live camera feed and accepts game input over a websocket.
It is meant for a laptop or TV on your own network, not the public internet.

- **Localhost by default.** `python run.py` binds `127.0.0.1`. Other pages on
  this machine cannot talk to `/ws` unless their Origin host and port match
  the server (the browser does not enforce same-origin on WebSockets; the
  server does).
- **Origin-checked websocket.** A page served from another host or port is
  refused (close code 4403) before the connection is accepted. Non-browser
  clients that send no `Origin` header are allowed.
- **Token required for LAN mode.** `python run.py --host 0.0.0.0` prints a
  one-line warning and a URL with `?t=<token>`. Open that URL on the TV.
  The index page and `/ws` reject requests that omit the token. Loopback
  stays tokenless.

### Playing without a Kinect (mock sensor)

`--mock` (the default fallback when no hardware answers) renders a synthetic
floor with a cup, course obstacles and colored balls through the same pinhole
model the vision code inverts — so the full pipeline runs end to end:

1. **Start** → *New game* → the mock sensor is detected.
2. **Calibrate** (floor → play area → course → obstacles → cup → balls) using
   mouse + keyboard (`Enter` confirm, `Esc` back, `Z` undo, `Y` secondary).
3. **Tee off.** In the HUD, **click a spot on the floor** to aim the active
   player's putt; the ball rolls there, the stroke counts when it stops, and
   play passes to the next player. Sink it in the hole to finish.

### Kinect backends

- **Kinect v1** (`rumpus/sensor/kinect_v1.py`) needs libfreenect + its Python
  bindings. Install per the libfreenect README; not pip-installable reliably.
- **Kinect v2** (`rumpus/sensor/kinect_v2.py`) needs libfreenect2 +
  `pylibfreenect2`.

Both are imported lazily and fail open cleanly (`open()` → `False`), so the app
still runs and falls back to the mock.

### Playing with a regular 2D webcam

No Kinect needed. Run `python run.py --camera 0` (or pick the camera in
**Settings → Camera**). A webcam has no depth, so calibration differs:

1. **Floor** — clear the floor and capture; this stores the empty-floor
   reference used for obstacle/cup differencing.
2. **Play area** — click the **four corners of a known-size rectangle** (choose
   Small/Medium/Large first). The app solves a homography from those corners to
   floor meters, which is what makes aiming, distances and the course layout
   meaningful.
3. Course, obstacles (frame differencing vs. the reference), cup and balls
   (by hue) proceed as usual.

### 2D webcam vs. Kinect — what a regular camera can and can't do

A regular camera has no depth, so the same game runs through a different vision
path. It works well on a plain, evenly-lit floor with high-contrast balls and
obstacles; it degrades faster than a Kinect as those assumptions weaken.

| Concern | Kinect v1 / v2 (depth) | Regular 2D webcam (color only) |
|---|---|---|
| Floor mapping | Plane fitted from depth (RANSAC); automatic, works on any floor texture | Homography solved from 4 clicked corners of a **known-size rectangle** (Small/Medium/Large preset); assumes a flat floor and a single plane |
| Floor reference | Depth snapshot is absolute (height above floor) | Empty-floor **color** snapshot; later frames are differenced against it |
| Ball detection | Depth-first: any small above-floor blob, then color to identify | **Hue only**: the ball must be a saturated, distinct color that doesn't appear elsewhere on the floor |
| Obstacle detection | Height above the floor → works for any object, even a dark object on a dark floor | Frame differencing → needs visual contrast; a dark object on a dark floor, or a flat mat, may be missed |
| Lighting | Robust (depth is mostly light-independent) | Sensitive: shadows, sunlight and auto-exposure/white-balance drift can create false obstacles |
| Cup detection | Height + white-ring color | White-ring color + differencing; needs a clearly visible ring against the floor |
| Height / ramps | Ball height is a secondary holed check on v2 | No height: a ball on a raised ramp maps to a slightly wrong floor position |
| Setup accuracy | Automatic and quick | Depends on how carefully you click the four rectangle corners |

In short: use a Kinect if you have one. A webcam is fully supported and playable,
but give it a plain floor, good even lighting, and balls/obstacles that contrast
strongly with the floor. The sensor abstraction means nothing else in the game
changes — only the floor mapping and the detection heuristics differ, and both
are already isolated behind `rumpus/sensor/` and `rumpus/vision/`.

## Assets (fonts / photos / music / SFX)

- **Fonts** (Outfit, Manrope — OFL) are bundled in `web/assets/fonts/`.
- **Photos** (Unsplash, no attribution required) are bundled in
  `web/assets/photos/{s01..s21}.jpg` — see `requirements/…/ASSETS.md` for the
  exact images and Credits strings (already wired into S20).
- **Music** (Kevin MacLeod — CC BY 4.0) is bundled in `web/assets/music/`
  (`LobbyTime.mp3`, `BackedVibesClean.mp3`).
- **SFX** (Kenney — CC0) are bundled in `web/assets/sfx/` for click, confirm,
  back, putter tick, thunk and plink. The turn sting and cheer are synthesized
  in the browser (WebAudio) and used as fallbacks if a bundled file is missing.
  Attribution strings are shown on the Credits screen.

## Project layout

```
run.py                  CLI entry point
rumpus/
  engine.py             game engine: state machine, rules, publishing
  server.py             FastAPI + websocket + game-loop thread
  models.py             shared dataclasses (players, balls, obstacles, events…)
  config.py             settings (rumpus-settings.json)
  setup_data.py         calibration persistence (rumpus-setup.json)
  game_save.py          in-progress autosave (rumpus-game.json)
  sensor/               backend interface + detect + mock + kinect v1/v2
  vision/               geometry, floor fit, blobs, ball tracker, obstacle detect
  game/                 course layout, event log, scoring
web/                    the browser front-end (index.html, css/, js/, assets/)
data/                   courses.json, settings.schema.json, CHANGELOG.md
requirements/           the design handoff + POC spec (source of truth)
```

## Prototype scope notes

The full spec is implemented; a few areas are deliberately simplified and marked
in code:

- **Obstacle correction (S07c)** is merged into the S07b "obstacles detected"
  screen (propose → confirm-all / delete / re-detect / undo are wired; drag
  corner editing is represented but minimal).
- **Ball setup on real Kinect** detects the mock's balls; on hardware the same
  tracker path is used, with a "reject green" check at the hue-sampling layer.
- **Photos/music/SFX** are bundled and committed (see Assets) so the game works
  offline.
- **Announcer voice** is a stub (the turn-change "sting" is synthesized; a TTS
  hook is left for a future pass).
