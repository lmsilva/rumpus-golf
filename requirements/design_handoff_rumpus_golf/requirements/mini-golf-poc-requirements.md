# Mini Golf Floor Game — Proof of Concept Requirements

_Revision 2 — adds user-placed physical obstacles with automatic footprint detection and on-screen correction (4.1b), occlusion handling (4.2), and the tablet design note (8)._

## 1. What this is

A camera-based mini golf game played on a real floor. A depth camera (Microsoft Kinect) or a regular 2D webcam on a tripod watches a patch of floor. The player defines a play area, a start zone, and a hole zone on screen, then places real household objects (pillows, boxes, books, ramps) on the floor as obstacles; the app detects their footprints and draws them on the course map. Two players take turns putting real golf balls across the floor. The app tracks the balls, counts strokes, detects when a ball stops inside the hole zone, and keeps score.

The real world does the physics. Obstacles exist in software only so the course can be drawn, so a ball hidden behind a box is not reported as lost, and so setup feels finished. Approximate footprints are fine.

The prototype runs on **either** a Kinect (depth + color) or a **regular 2D camera** (color only). The two share one sensor abstraction, one state machine, one UI and one set of rules; only the floor mapping and the detection heuristics differ. Color-only cameras are fully supported but carry limitations relative to a depth camera — see §3.4.

There is no projector and nothing to print. All zones are defined and shown on the computer screen. The hole is a real plastic putting cup (a low ramp with a white ring opening, ~7 × 5.5 in, ~1 in tall) placed on the floor — the app detects it during setup to position the hole zone. The ball physically rolls up the cup's ramp and rests in it when holed.

## 2. Platform and technology requirements

- The app must run on Windows 10/11. This is the primary development and target platform.
- The code must be portable to Linux in a future iteration. Do not use any Windows-only framework or API.
  - Specifically: do NOT use the Microsoft Kinect SDK. It is Windows-only.
- Language: Python 3.11+.
- Computer vision: OpenCV (opencv-python).
- Sensor drivers:
  - Kinect v1 (Xbox 360): libfreenect with its Python bindings.
  - Kinect v2 (Xbox One): libfreenect2 (Python bindings, e.g. pylibfreenect2).
  - Regular 2D webcam: OpenCV `VideoCapture` (color only, no depth). Selected by index and resolution from Settings or the CLI.
- UI: keep it simple and portable. OpenCV windows with mouse callbacks are acceptable for the POC. A lightweight cross-platform UI library (e.g. PySide6 or a browser-based UI served locally) is acceptable if it stays portable. Do not use WinForms/WPF.
- Hardware target: must run at full speed on a mid-range laptop. Keep CPU use low. Design with a future low-power machine (used mini PC, possibly Raspberry Pi with Kinect v1) in mind: avoid heavy dependencies and avoid machine learning models. All tracking is classical computer vision.

## 3. Architecture requirements

### 3.1 Sensor abstraction layer

- All sensor access goes through one small module with a single interface. Game and vision code must never import the driver libraries directly.
- The interface provides:
  - The latest color frame.
  - The latest depth frame (distances in millimeters) — **optional**: a color-only backend reports `has_depth = False` and yields `depth = None`.
  - A sensor description: color resolution, depth resolution, field of view, reliable depth range (min/max), and sensor model name.
- Backends implement this interface:
  - `KinectV1Backend` (libfreenect). Build and test this one first — this is the sensor on hand.
  - `KinectV2Backend` (libfreenect2). Build second, against the already-fixed interface.
  - `WebcamBackend` (OpenCV `VideoCapture`). Color-only (`has_depth = False`), for any regular 2D camera.
  - `MockBackend` (simulated floor) for development and smoke tests with no hardware.
- At startup the app detects which sensor is connected by USB vendor/product ID and loads the matching backend. If both are connected, prefer v2. If none, try a webcam, then fall back to the mock. The decision can be overridden in Settings (Camera tab) or on the command line.
- Known sensor facts to encode in the sensor descriptions:
  - Kinect v1: depth 640×480, color 640×480, ~57° horizontal field of view, reliable depth ~0.8 m to ~4.0 m. Unreliable in direct sunlight.
  - Kinect v2: depth 512×424, color 1920×1080, ~70° horizontal field of view, reliable depth ~0.5 m to ~4.5 m.
  - Webcam: no depth (depth resolution 0×0), color resolution as requested (default 1280×720), field of view ~70°, no reliable depth range.

### 3.2 Coordinate mapping

- All game logic works in floor coordinates (real-world meters on the floor plane), never in pixels.
- **Depth path (Kinect)**: fit the floor plane from the depth data during calibration (RANSAC plane fit or equivalent — a simple, robust method that finds the dominant flat surface). Provide functions to convert: depth pixel → 3D point → floor position, and floor position → color pixel (for drawing overlays).
- **Color-only path (webcam)**: there is no plane to fit. Instead the user clicks the four corners of a known-size rectangle (Small 2×1.5 / Medium 3×2 / Large 4×2.5 m presets) and the app solves a 2D homography `H` that maps floor meters to pixels (and back). The same floor-facing methods (`floor_to_pixel`, `pixel_to_floor`, `radius_to_pixels`) are provided by a `HomographyMapper` so game/vision code never branches on the source.
- The height of any object above the floor plane must be available (needed for ramps later; in the POC it is used to separate balls from the floor). This is available **only on the depth path**; a color-only camera has no height information.

### 3.3 Configuration persistence

- Save calibration and setup to a config file on disk (JSON). Includes: play area polygon, start zone, hole zone, obstacle polygons (4.1b), floor plane, ball color definitions, player names.
- On startup, offer to load the saved setup or start a new calibration.
- The saved setup records which mapping was used (fitted plane vs. homography) so a webcam-calibrated course reloads correctly.

### 3.4 Color-only camera — differences and limitations

A regular 2D camera replaces depth with color heuristics. The game, states, UI and rules are identical; only these internals change, and only the depth camera gets the full robustness described in §4.

| Concern | Kinect (depth + color) | Regular 2D webcam (color only) |
|---|---|---|
| Floor mapping | Automatic plane fit (RANSAC) from depth | Homography from 4 clicked corners of a **known-size rectangle**; assumes a flat floor and a single plane |
| Floor reference | Depth snapshot; height is absolute | Empty-floor **color** snapshot, differenced against later frames |
| Ball detection | Depth-first (any small above-floor blob) + color identity | **Hue mask only** — the ball must be a saturated, distinct color that does not appear elsewhere on the floor |
| Obstacle detection | Height above the floor; works for any object, including a dark object on a dark floor | Frame differencing — needs visual contrast; a dark object on a dark floor, or a flat mat, may be missed |
| Cup detection | Height + white-ring color | White-ring color + differencing; needs a clearly visible ring against the floor |
| Lighting | Robust — depth is largely light-independent | Sensitive — shadows, sunlight and auto-exposure/white-balance drift can create false obstacles |
| Height / ramps | Ball height is a secondary holed check on v2 | No height — a ball on a raised ramp maps to a slightly wrong floor position |
| Setup accuracy | Automatic | Depends on how carefully the four corners are clicked |

Practical guidance: use a Kinect if available. A webcam is playable but wants a plain, evenly-lit floor and balls/obstacles that contrast strongly with it. These differences are deliberately contained — the `has_depth` flag on the backend is the only branching point the rest of the code sees.

## 4. Functional requirements

### 4.1 Calibration and course setup screen

- Show the live color feed with an overlay.
- Shade the part of the floor where tracking is reliable (inside the sensor's good depth range) so the user can see where a course fits. Use the sensor description for the range values.
- Step 1 — empty floor snapshot: user clears the floor and presses a key. The app captures the floor depth reference and fits the floor plane. Everything added to the floor afterward is detected as "above floor."
- Step 2 — play area: user draws a polygon on the live feed with the mouse (click corners, close the shape). This is the course boundary. Balls outside it are out of bounds.
- Step 3 — start zone: user draws a circle (click center, drag radius) inside the play area. Balls must begin each turn inside it.
- Step 4 — hole zone (putting cup detection): the app prompts "Place the putting cup inside the play area." It then finds the cup automatically:
  - Look for a new static above-floor blob (compared against the empty-floor snapshot) that contains a bright white ring in the color image.
  - Average the depth over 1–2 seconds of frames before deciding. The cup is only ~2.6 cm tall, which is near the Kinect v1's depth noise at long range; frame averaging is required for reliable detection on v1. Do not rely on the flag or flag stick — they are too thin and too small for either sensor.
  - Propose the hole zone centered on the white ring, with the radius matched to the ring's real opening (~9 cm / 3.5 in across).
  - The user confirms with one click, or adjusts by dragging (move center, resize radius) if detection picked the wrong spot. Manual drawing (click center, drag radius) remains available as a fallback if detection fails.
- All zones render as colored overlays on the live feed at all times (setup and gameplay).
- Zones must be editable: user can select and redraw any zone without redoing the others.
- Verify-calibration check: on loading a saved setup, show the saved zones and obstacle footprints over the live feed and ask the user to confirm they still line up with the real floor (the camera may have been moved, or an object may have been put away). One key to confirm, one key to recalibrate. Recalibrating lets the user pick what to redo — zones, obstacles, or both.

### 4.1b Obstacle placement and detection (Step 5 — "Place your obstacles")

Runs after the zones are defined (Steps 1–4). Detection proposes; the user confirms. An obstacle that the user has not confirmed is never part of the course.

- Prompt: "Place your obstacles on the course, then press [key]." The user puts pillows, boxes, books, ramps or anything else inside the play area.
- Detection: on the key press, compare live depth against the empty-floor reference from Step 1 and find every new static above-floor region inside the play area. Average 1–2 seconds of frames before deciding — the same method as the putting-cup detection, and for the same reason: thin objects such as a book on its edge are close to the Kinect v1's depth noise at long range. Ignore the putting cup (already known from Step 4) and any blob small enough to be a ball.
- Each detected obstacle is proposed as an outlined footprint polygon in floor coordinates (meters on the floor plane), drawn on the live feed with a subtle fill. Approximate outlines are acceptable; a simplified polygon (for example 4–12 points) is preferred over a pixel-exact one.
- Correction interactions, each doable in seconds with the mouse:
  - Click an obstacle to select it. The selected outline shows draggable points.
  - Delete: remove a selected obstacle (false detection such as a shadow or a foot).
  - Reshape / resize: drag outline points; add a point by clicking on an edge, remove one with a key.
  - Add manually: draw a polygon on empty floor (click corners, close the shape) for something detection missed, e.g. a dark object on a dark floor.
  - Re-detect: rerun detection without touching the zones. Keeps obstacles the user has already confirmed or drawn; re-proposes everything else.
  - Confirm all: one key marks every proposed outline as confirmed and finishes the step. The step cannot be finished with unconfirmed proposals on screen — they are either confirmed or deleted.
- Undo: every obstacle edit during setup (delete, reshape, add, re-detect, confirm) is undoable with one key, back to the start of the step.
- Rendering: confirmed obstacles draw on the live-feed overlay and on the top-down course map, in a style clearly different from the play area, start zone and hole zone. Proposed (unconfirmed) obstacles use a distinct "pending" style.
- Mid-game: if a new static above-floor region appears inside the play area and stays for ~3 seconds while no ball is moving, show a small non-blocking prompt: "New object on the course — add as obstacle / ignore." Never show it while a putt is in progress (a ball is moving), and never pause play for it. Ignored objects are remembered for the rest of the hole so the prompt does not repeat.
- Height and shape are not measured. An obstacle is a flat footprint on the floor plane; whether it is a ramp, a box or a pillow makes no difference to the app.

### 4.2 Ball detection and player assignment

- Ball detection uses depth first, color second:
  - A ball is a small blob above the floor plane, inside the play area, within a plausible size range (scale the expected pixel size by distance from the sensor).
  - The color camera samples the blob's dominant hue to identify which ball it is.
- Ball color definition (during setup):
  - User places both balls on the floor inside the play area.
  - The app auto-detects both blobs, samples each ball's hue, and shows them on screen with labels ("Ball A — orange", "Ball B — pink").
  - Recommended ball colors: matte neon orange and matte neon pink (or neon yellow). Do NOT use green balls — the putting cup is green, and a green ball resting next to it can merge with the cup in both color and depth. General rule: ball colors must not match the cup or any other course object.
  - During ball setup, if a detected ball's hue is close to the cup's sampled color, warn the user and suggest a different ball.
  - The app asks the user to assign each detected ball to a player (e.g. click ball, then click player name).
  - Store each ball's hue range in the config.
- Tracking:
  - Track both balls every frame. Each ball has its own tracker with position smoothing and a plausibility check (a ball cannot jump across the course in one frame — reject detections far from the last known position unless the ball has been lost for a while).
  - A ball is "moving" or "stopped" based on position change over a short window (e.g. stopped = moved less than 2 cm over 0.5 s). The stopped/moving state drives the game rules.
- Near-cup robustness: when a ball is on or beside the cup, its depth blob can merge with the cup's blob. The tracker must handle this: inside the hole zone, rely on the ball's color match rather than blob separation, and treat "ball last seen entering the hole zone, then only the cup blob remains, then ball color found inside the ring" as a valid holed sequence.
- Obstacle occlusion ("stopped, hidden"):
  - If a ball's last known position and heading lead into a confirmed obstacle footprint and the ball is not seen again, mark it "stopped, hidden" rather than lost. Keep its identity, estimate its position at the point where it disappeared behind the obstacle (the occlusion edge), and treat it as stopped for the game rules after the usual stopped window.
  - Draw the hidden ball at the estimated position in a distinct "hidden" style so players know where the app thinks it is.
  - Re-acquire immediately when a blob with a matching hue appears near that obstacle (within ~30 cm of the estimate); no confirmation needed.
  - Occlusion by a confirmed obstacle must never trigger the tracking-loss banner or the re-assignment dialog below.
- Tracking-loss recovery:
  - If a ball is not detected for more than ~2 seconds during a game, and it is not "stopped, hidden" behind an obstacle, show a non-blocking banner: "Lost Player X's ball."
  - When a matching-hue blob reappears, resume automatically.
  - If both balls are lost and then two blobs reappear ambiguously (hues too similar to distinguish confidently), pause and ask the user to re-confirm which ball belongs to which player, same interaction as setup.

### 4.3 Game rules and flow

- Two players. Setup screen collects two player names and their ball assignments (from 4.2).
- Turn structure (standard mini golf, one hole):
  - Player 1 places their ball in the start zone. The app detects "ball placed and stopped in start zone" and shows "Player 1 — ready. Putt when ready."
  - Each time the ball transitions from stopped to moving, that is one stroke. Increment the player's stroke count.
  - When the ball stops again, it is either: holed (stopped inside hole zone → player is done for this hole; the ball physically rests inside the cup, so on Kinect v2 additionally confirm the ball's height is slightly above floor level as a secondary check — on v1, position alone is sufficient), out of bounds (stopped or last seen outside the play area → apply a +1 penalty stroke and have the player replace the ball where it left the course; the app shows the exit point on the overlay), or in play (player putts again from where it lies).
  - A player finishes the hole when holed, or when reaching a stroke cap (default 8, configurable) — then they score the cap.
  - Players alternate turns: after each stroke resolves (ball stops), play passes to the other player, per normal mini golf. The app clearly shows whose turn it is at all times.
- Scoring screen:
  - Always visible during play: both player names, current stroke counts, whose turn it is.
  - When both players finish the hole: show final result (winner, stroke counts) and offer "Play again" (same course, scores reset) or "Back to setup."
- Hole-out feedback: when a ball is holed, show an unmistakable on-screen celebration (banner + the hole zone flashing).
- Obstacles are informational only. The app does not simulate collisions, bounces or ramps — the real objects do that. Obstacles affect only the overlay/map, the "stopped, hidden" tracking rule, and setup. A ball stopped on top of an obstacle (for example resting on a ramp) is simply "in play" at that floor position.

### 4.4 Main screens summary

1. Startup: sensor auto-detect, load saved setup or new calibration.
2. Calibration and course setup (4.1), including the obstacle step (4.1b).
3. Ball and player setup (4.2).
4. Game screen (4.3): live feed with zone overlays, ball trails, turn indicator, scores.

## 5. Out of scope for the POC

- Projector output, printed markers, ArUco codes.
- Obstacle height and 3D shape (only the floor footprint is detected), ball-height tracking off ramps, moving obstacles, and virtual-only obstacles (an obstacle must exist as a real object on the floor). Obstacle footprint detection itself is in scope — see 4.1b.
- More than two players, multiple holes per round, persistent leaderboards.
- Raspberry Pi support (design for portability, but do not test on Pi).
- Sound effects, polished art. Function first.

## 6. Acceptance criteria

- On Windows, with a Kinect v1 connected, the app starts, auto-detects the sensor, and reaches the calibration screen.
- A user can complete calibration (floor snapshot, play area, start zone, hole zone) in under 3 minutes using only the mouse and keyboard.
- With the putting cup placed anywhere inside the play area, the setup flow detects it and proposes a correctly centered hole zone within 5 seconds, on both v1 and v2 (v2 tested when available). Manual adjustment works when detection is off.
- With two matte balls of clearly different colors (e.g. neon orange and neon pink), both balls are tracked simultaneously at 15+ frames per second, and identities do not swap when the balls pass within 10 cm of each other.
- A full two-player, one-hole game can be played end to end: turns alternate correctly, strokes count correctly (verified against a human count), out-of-bounds applies the penalty, and hole-out is detected when a ball comes to rest in the cup (a ball rolling through the hole zone, or bouncing off the cup and rolling away, must NOT count).
- Killing and restarting the app, then loading the saved setup, restores the full course (zones and obstacles) and ball definitions with only the verify-calibration confirmation step.
- With three household objects placed on the course (a pillow, a shoebox, a hardback book on its edge), the obstacle step proposes footprints for all three within 5 seconds on Kinect v1; a false detection can be deleted, an off footprint corrected, and a missed object drawn manually, each in under 10 seconds with the mouse; undo restores the previous state.
- A ball rolled behind a confirmed obstacle and left there is reported as "stopped, hidden" at the occlusion edge — not as lost — and play continues; when it is pushed back into view it is re-acquired with the same identity.
- Placing a new object on the course between turns produces the add/ignore prompt; placing it while a ball is rolling produces no prompt until the ball stops.
- Swapping in a Kinect v2 later requires changes only inside the sensor layer (new backend + sensor description), with zero changes to calibration, tracking, or game code. (This criterion is checked by code review in the POC, tested for real when the v2 arrives.)

## 7. Suggested build order

1. Sensor layer + v1 backend: live color and depth side by side on screen.
2. Floor snapshot, plane fit, above-floor blob map (top-down debug view).
3. Calibration UI: zones drawn on the live feed, saved to config.
3b. Obstacle step: detection from the above-floor map, footprint polygons, correction UI, undo, persistence, verify-on-load.
4. Two-ball detection, color assignment, tracking with smoothing, "stopped, hidden" handling against confirmed obstacles.
5. Game rules, turn flow, scoring screen.

Each step is independently runnable and testable before the next begins.

## 8. Design note — future tablet edition

A later version will run on a tablet without a depth camera, using an AR (augmented reality — the tablet's camera plus motion sensors) scan of the floor instead of the Kinect depth snapshot. The **color-only webcam path (§3.4) already exercises this shape**: a 2D camera, no depth, homography mapping, and color-based detection. To keep that port cheap:

- Obstacle detection lives behind the sensor layer (3.1), like everything else. The rest of the app only ever sees "a list of proposed footprint polygons in floor coordinates." The tablet edition swaps the depth-snapshot detector for an AR-based one and changes nothing above it.
- The correction interactions (select, delete, reshape, add, re-detect, confirm, undo), the config format (obstacles as floor-plane polygons next to the zones), the "stopped, hidden" occlusion rule and the game rules must be reusable unchanged. Do not let Kinect-specific details (pixel coordinates, depth units, frame counts) leak into them.
