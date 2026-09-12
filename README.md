# Rumpus Golf

Turn any living-room floor into a mini golf course. A Kinect (v1 or v2) on a
tripod watches the floor; players putt real colored balls through DIY courses
built from books, cushions and shoeboxes. The app tracks the balls, counts
strokes, detects hole-outs and keeps score on a laptop or TV.

This is a **prototype** implementing the full design handoff
(`requirements/design_handoff_rumpus_golf/`) and the POC spec
(`requirements/…/mini-golf-poc-requirements.md`): 25 screens, a 5-step
calibration, obstacle detection & correction, ball tracking with occlusion
("hidden") handling, and the complete game flow for 2–6 players over 1–9 holes.

---

## Architecture

The core rule of this codebase: **the UI is a dumb display and Python owns
everything that matters.** That seam is what makes the UI replaceable and the
Python core portable (Windows → Linux → future tablet/AR edition).

```
┌─────────────── browser (web/) ───────────────┐      ┌────────── Python (rumpus/) ──────────┐
│  screens.js   HTML/CSS/JS renderers           │      │  engine.py    state machine + rules  │
│  feed.js      JPEG canvas + SVG overlay       │◄────►│  vision/      floor/ball/obstacle CV │
│  audio.js     WebAudio SFX + music            │  ws  │  sensor/      Kinect v1/v2/mock      │
│  main.js      websocket client + input        │      │  game/        course/scoring/events  │
└───────────────────────────────────────────────┘      └──────────────────────────────────────┘
```

- **State over websocket** is a hard boundary. The server publishes a JSON
  snapshot + JPEG feed frames; the browser renders and sends back user intent
  (`action`, `pointer`, `set`, `text`). The browser never holds game state.
- **Sensor abstraction** (`rumpus/sensor/`). Game/vision code never imports a
  driver. `SensorBackend` exposes color + depth + a `SensorDescription`, and
  three backends implement it: `KinectV1Backend` (libfreenect),
  `KinectV2Backend` (pylibfreenect2), and `MockBackend` (simulated floor).
- **Coordinate mapping** (`rumpus/vision/geometry.py`). All game logic is in
  floor meters; pixel↔3D↔floor conversion lives in one `FloorMapper`.
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

# 3. run (auto-detects a Kinect; falls back to the mock sensor)
python run.py

# or force the simulated sensor / a specific backend
python run.py --mock
python run.py --sensor v1
python run.py --sensor v2
```

Open **http://127.0.0.1:8000** in a browser (fullscreen on a TV: press `F`).

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

## Assets (fonts / photos / music / SFX)

- **Fonts** (Outfit, Manrope — OFL) are bundled in `web/assets/fonts/`.
- **Photos** (Unsplash, no attribution required) and **music/SFX** (CC BY 4.0 /
  CC0) are referenced but *not committed*. Drop files into:
  - `web/assets/photos/{s01..s21}.jpg` — see `requirements/…/ASSETS.md` for the
    exact images and the Credits strings (already wired into S20).
  - `web/assets/music/LobbyTime.mp3`, `web/assets/music/BackedVibesClean.mp3`
  - `web/assets/sfx/*` (optional; the turn sting, cheer, putter tick, thunk and
    plink are synthesized in the browser via WebAudio already).
  Without them the screens fall back to gradients and stay silent except for the
  synthesized SFX. Attribution strings are shown on the Credits screen.

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
- **Photos/music** are drop-in (see Assets) rather than committed binaries.
- **Announcer voice** is a stub (the turn-change "sting" is synthesized; a TTS
  hook is left for a future pass).
