"""Game engine — the single source of truth.

Owns the sensor backend, settings, calibration/setup, the vision pipeline and
the game rules. It is driven by two inputs:

- ``tick(frame)`` — one sensor frame each cycle (vision + rules + timers).
- ``handle_input(msg)`` — semantic user input from the UI.

and produces one output, ``snapshot()`` — a plain JSON-safe dict the UI renders.
The UI is a dumb display: it sends user intent, never game state.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from . import __version__
from .config import Settings
from .game.course import CourseLayout, course_by_id, course_for_hole, load_courses
from .game.events import EventLog
from .game.scoring import build_scorecard, standings, total, vs_par
from .models import (CircleZone, EventType, FloorPlane, Obstacle, Palette, Player,
                     SensorDescription)
from .sensor.base import SensorBackend
from .sensor.mock import MockBackend, hex_to_bgr
from .setup_data import Setup
from .vision.ball_tracker import BallTracker
from .vision.floor import fit_floor_plane
from .vision.geometry import CameraModel, FloorMapper, default_camera
from .vision.obstacle_detect import detect_obstacles, point_in_polygon


class S:
    BOOT = "BOOT"
    SENSOR_CHECK = "SENSOR_CHECK"
    VERIFY = "VERIFY"
    CAL_FLOOR = "CAL_FLOOR"
    CAL_AREA = "CAL_AREA"
    CAL_COURSE = "CAL_COURSE"
    CAL_PLACE = "CAL_PLACE"
    CAL_OBSTACLES = "CAL_OBSTACLES"
    CAL_CUP = "CAL_CUP"
    CAL_BALLS = "CAL_BALLS"
    GAME_START = "GAME_START"
    HOLE_START = "HOLE_START"
    PLAY = "PLAY"
    TURN_CHANGE = "TURN_CHANGE"
    HOLE_OUT = "HOLE_OUT"
    OOB = "OOB"
    PAUSE = "PAUSE"
    FIX_SCORE = "FIX_SCORE"
    HOLE_COMPLETE = "HOLE_COMPLETE"
    GAME_FINISH = "GAME_FINISH"
    SETTINGS = "SETTINGS"
    CREDITS = "CREDITS"
    CHANGELOG = "CHANGELOG"


SCREEN_BY_STATE = {
    S.BOOT: "S01", S.SENSOR_CHECK: "S02", S.VERIFY: "S03",
    S.CAL_FLOOR: "S04", S.CAL_AREA: "S05", S.CAL_COURSE: "S06",
    S.CAL_PLACE: "S07", S.CAL_OBSTACLES: "S07b", S.CAL_CUP: "S08",
    S.CAL_BALLS: "S09", S.GAME_START: "S10", S.HOLE_START: "S07",
    S.PLAY: "S11", S.TURN_CHANGE: "S12", S.HOLE_OUT: "S13", S.OOB: "S14",
    S.PAUSE: "S15", S.FIX_SCORE: "S16", S.HOLE_COMPLETE: "S17",
    S.GAME_FINISH: "S18", S.SETTINGS: "S19", S.CREDITS: "S20", S.CHANGELOG: "S21",
}

FEED_STATES = {S.VERIFY, S.CAL_FLOOR, S.CAL_AREA, S.CAL_PLACE, S.CAL_OBSTACLES,
               S.CAL_CUP, S.CAL_BALLS, S.PLAY, S.TURN_CHANGE, S.HOLE_OUT, S.OOB, S.HOLE_START}

HUE_NAMES = Palette.HUE_NAMES
PLAYER_COLORS = Palette.PLAYER_COLORS


class GameEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.backend: SensorBackend | None = None
        self.cam: CameraModel = default_camera((640, 480))
        self.sensor_status: str = "none"
        self.sensor_desc: dict | None = None

        self.state: str = S.BOOT
        self._prev_state_for_pause: str = S.BOOT
        self.setup = Setup()
        self.plane: FloorPlane | None = None
        self.mapper: FloorMapper | None = None
        self.reference_depth: np.ndarray | None = None

        self.tracker = BallTracker()
        self.events = EventLog()

        # Game round state.
        self.players: list[Player] = []
        self.player_scores: dict[str, list[int | None]] = {}
        self.hole = 1
        self.holes = self.settings.holes
        self.stroke_cap = self.settings.stroke_cap
        self.active_index = 0
        self.finished_hole: dict[str, bool] = {}
        self.course_ids: list[str] = []
        self.course_pars: list[int] = []
        self.current_course_id = "hallway"
        self.layout: CourseLayout | None = None
        self.ball_for_player: dict[str, str] = {}   # player_id -> ball_id

        # Live feed frame + transient UI state.
        self._frame_color: np.ndarray | None = None
        self._frame_depth: np.ndarray | None = None
        self._feed_w = 640
        self._feed_h = 480

        # Timers / capture state.
        self._avg_depth: np.ndarray | None = None
        self._avg_count: np.ndarray | None = None
        self._capture_until = 0.0
        self._capture_label = ""

        # Calibration drawing state.
        self._draw_poly: list[tuple[float, float]] = []
        self._draw_circle_center: tuple[float, float] | None = None
        self._draw_circle_r = 0.0
        self._selected_obstacle: int | None = None
        self._dragging: tuple | None = None  # ("poly", idx) | ("circle", "center"|"radius") | ("obstacle", idx, corner)

        # Putt animation (mock).
        self._putt: dict | None = None
        self._ball_positions: dict[str, tuple[float, float]] = {}
        self._mock_balls: list[dict] = []

        # Lost ball / new object prompt.
        self._lost_since: dict[str, float] = {}
        self._ignored_regions: list[list[tuple[float, float]]] = []

        # Turn-change / hole-out timing.
        self._transition_until = 0.0
        self._transition_next: str | None = None
        self._last_turn_player: str | None = None
        self._prev_strokes: dict[str, int] = {}
        self._ball_was_moving: dict[str, bool] = {}

        # Settings return-state.
        self._settings_return: str = S.BOOT
        self._pause_focus = 0
        self._recal_flyout = False
        self._rebuilding = False

        self._last_t = time.time()

    # ===================================================================== #
    # Startup
    # ===================================================================== #
    def attach_backend(self, backend: SensorBackend, cam: CameraModel | None = None) -> None:
        self.backend = backend
        self.sensor_desc = backend.description.as_dict()
        if cam is not None:
            self.cam = cam
        elif isinstance(backend, MockBackend):
            self.cam = backend.cam
        else:
            self.cam = default_camera(backend.description.depth_res)
        self._feed_w = backend.description.color_res[0]
        self._feed_h = backend.description.color_res[1]
        self.sensor_status = backend.description.model

    @property
    def is_mock(self) -> bool:
        return isinstance(self.backend, MockBackend)

    # ===================================================================== #
    # Main loop
    # ===================================================================== #
    def tick(self, frame) -> None:
        now = time.time()
        dt = now - self._last_t
        self._last_t = now

        self._frame_color = frame.color
        self._frame_depth = frame.depth
        if self._frame_color is not None:
            self._feed_h, self._feed_w = self._frame_color.shape[:2]

        # Keep the mock scene in sync with the model.
        if self.is_mock:
            self._sync_mock_scene()

        # Feed-state vision & game processing.
        if self.state in FEED_STATES:
            self._process_vision(now)

        self._process_timers(now)

    def _process_vision(self, now: float) -> None:
        if self._frame_depth is None:
            return
        # Accumulate depth while a capture is active.
        if now < self._capture_until:
            self._accumulate_depth(self._frame_depth)
        # Play state: track balls.
        if self.state in (S.PLAY, S.TURN_CHANGE, S.HOLE_OUT, S.OOB):
            if self.mapper is not None and self.plane is not None:
                obs = self._confirmed_obstacles()
                self.tracker.update(self._frame_color, self._frame_depth, self.mapper,
                                    self.cam, self.plane, obs, now)
                self._apply_ball_motion(now)

    # ===================================================================== #
    # Capture helpers
    # ===================================================================== #
    def _start_capture(self, duration: float, label: str) -> None:
        self._capture_until = time.time() + duration
        self._capture_label = label
        self._avg_depth = None
        self._avg_count = None

    def _accumulate_depth(self, depth: np.ndarray) -> None:
        d = depth.astype(np.float32)
        valid = (d > 200) & (d < 8000)
        if self._avg_depth is None:
            self._avg_depth = np.where(valid, d, 0.0)
            self._avg_count = valid.astype(np.float32)
        else:
            self._avg_depth += np.where(valid, d, 0.0)
            self._avg_count += valid.astype(np.float32)

    def _finish_capture(self) -> np.ndarray | None:
        if self._avg_depth is None or self._avg_count is None:
            return None
        avg = np.where(self._avg_count > 0, self._avg_depth / np.maximum(self._avg_count, 1), 0.0).astype(np.float32)
        self._avg_depth = None
        self._avg_count = None
        self._capture_until = 0.0
        return avg

    @property
    def capturing(self) -> bool:
        return time.time() < self._capture_until

    def _process_timers(self, now: float) -> None:
        # Timed capture completions.
        if self._capture_until and now >= self._capture_until:
            self._on_capture_done()

        # Turn-change / hole-out auto-transition.
        if self._transition_next is not None and now >= self._transition_until:
            nxt = self._transition_next
            self._transition_next = None
            self._set_state(nxt)

    # ===================================================================== #
    # State transitions (explicit)
    # ===================================================================== #
    def _set_state(self, state: str) -> None:
        self.state = state
        self._on_enter(state)

    def _on_enter(self, state: str) -> None:
        if state == S.BOOT:
            self.events = EventLog()
        elif state == S.CAL_AREA and not self.setup.play_area:
            self._apply_preset("medium")
        elif state == S.CAL_COURSE and not self.layout:
            self.layout = CourseLayout(course_by_id(self.current_course_id), self.setup.play_area)
        elif state == S.CAL_CUP:
            # Mock: seed a physical cup at the template hole so it is detectable.
            if self.is_mock and self.setup.hole is None and self.layout is not None:
                self.setup.hole = CircleZone(self.layout.hole[0], self.layout.hole[1], 0.045)
            self._start_capture(1.5, "cup")
        elif state == S.CAL_BALLS:
            self._ensure_mock_balls(2)
            self._derive_players_from_balls()
        elif state == S.PLAY:
            self._transition_until = 0.0

    # ===================================================================== #
    # Input handling
    # ===================================================================== #
    def handle_input(self, msg: dict) -> None:
        t = msg.get("t")
        if t == "action":
            self._handle_action(msg.get("a"), msg)
        elif t == "pointer":
            self._handle_pointer(msg)
        elif t == "text":
            self._handle_text(msg)
        elif t == "set":
            self._handle_set(msg)

    def _handle_action(self, action: str, msg: dict) -> None:
        st = self.state
        if action == "back":
            # Esc during play pauses (per INPUT.md: "Pause | Esc").
            if st in (S.PLAY, S.TURN_CHANGE, S.HOLE_OUT, S.OOB):
                self._prev_state_for_pause = st
                self._pause_focus = 0
                self._set_state(S.PAUSE)
            else:
                self._on_back()
            return
        if action == "menu":
            if st in (S.PLAY, S.TURN_CHANGE, S.HOLE_OUT, S.OOB):
                self._prev_state_for_pause = st
                self._pause_focus = 0
                self._set_state(S.PAUSE)
            return
        if st == S.BOOT:
            self._boot_action(action)
        elif st == S.SENSOR_CHECK:
            self._sensor_action(action)
        elif st == S.VERIFY:
            self._verify_action(action)
        elif st == S.CAL_FLOOR:
            self._floor_action(action)
        elif st == S.CAL_AREA:
            self._area_action(action)
        elif st == S.CAL_COURSE:
            self._course_action(action, msg)
        elif st in (S.CAL_PLACE, S.HOLE_START):
            self._place_action(action)
        elif st == S.CAL_OBSTACLES:
            self._obstacles_action(action)
        elif st == S.CAL_CUP:
            self._cup_action(action)
        elif st == S.CAL_BALLS:
            self._balls_action(action)
        elif st == S.GAME_START:
            self._gamestart_action(action, msg)
        elif st == S.PLAY:
            self._play_action(action)
        elif st == S.HOLE_OUT:
            if action == "undo":
                self._undo_last_shot()
        elif st == S.OOB:
            if action == "confirm":
                self._advance_turn()
        elif st == S.PAUSE:
            self._pause_action(action, msg)
        elif st == S.FIX_SCORE:
            self._fixscore_action(action)
        elif st == S.HOLE_COMPLETE:
            self._holecomplete_action(action)
        elif st == S.GAME_FINISH:
            self._finish_action(action)
        elif st == S.SETTINGS:
            if action == "back":
                self._set_state(self._settings_return)
        elif st in (S.CREDITS, S.CHANGELOG):
            if action == "back":
                self._set_state(S.SETTINGS)

    # ------------------------------------------------------------------ #
    def _on_back(self) -> None:
        st = self.state
        if st in (S.CAL_FLOOR,):
            self._set_state(S.SENSOR_CHECK)
        elif st == S.CAL_AREA:
            self._set_state(S.CAL_FLOOR)
        elif st == S.CAL_COURSE:
            self._set_state(S.CAL_AREA)
        elif st == S.CAL_PLACE:
            self._set_state(S.CAL_COURSE)
        elif st == S.CAL_OBSTACLES:
            self._set_state(S.CAL_PLACE)
        elif st == S.CAL_CUP:
            self._set_state(S.CAL_OBSTACLES)
        elif st == S.CAL_BALLS:
            self._set_state(S.CAL_CUP)
        elif st == S.PAUSE:
            self._set_state(self._prev_state_for_pause)
        elif st in (S.SETTINGS,):
            self._set_state(self._settings_return)

    # ===================================================================== #
    # Per-screen actions
    # ===================================================================== #
    def _boot_action(self, action: str) -> None:
        if action == "new_game":
            self._set_state(S.SENSOR_CHECK)
        elif action == "load":
            saved = Setup.load()
            if saved is not None:
                self.setup = saved
                self._apply_saved_setup()
                self._set_state(S.VERIFY)
            else:
                self._set_state(S.SENSOR_CHECK)
        elif action == "library":
            self._set_state(S.CAL_COURSE)
        elif action == "settings":
            self._settings_return = S.BOOT
            self._set_state(S.SETTINGS)

    def _sensor_action(self, action: str) -> None:
        if action == "load":
            saved = Setup.load()
            if saved is not None:
                self.setup = saved
                self._apply_saved_setup()
                self._set_state(S.VERIFY)
        elif action == "fresh" or action == "new_game":
            self.setup = Setup()
            self._set_state(S.CAL_FLOOR)
        elif action == "retry":
            # Re-probe handled by server; no-op here.
            pass

    def _verify_action(self, action: str) -> None:
        if action == "confirm":
            self._begin_game_from_setup()
        elif action == "recalibrate":
            self._set_state(S.CAL_FLOOR)

    def _floor_action(self, action: str) -> None:
        if action == "confirm":
            self._start_capture(2.0, "floor")
        elif action == "undo":
            self._set_state(S.SENSOR_CHECK)

    def _on_capture_done(self) -> None:
        avg = self._finish_capture()
        if self._capture_label == "floor" and avg is not None:
            plane = fit_floor_plane(avg, self.cam)
            if plane is not None:
                self.plane = plane
                self.mapper = FloorMapper(plane, self.cam)
                self.setup.floor_plane = plane
                self.setup.camera = {"fx": self.cam.fx, "fy": self.cam.fy,
                                     "cx": self.cam.cx, "cy": self.cam.cy}
                self.reference_depth = avg
                self._set_state(S.CAL_AREA)
        elif self._capture_label == "obstacles" and avg is not None and self.mapper is not None:
            self._propose_obstacles(avg)
        elif self._capture_label == "cup" and avg is not None and self.mapper is not None:
            self._propose_cup(avg)

    def _area_action(self, action: str) -> None:
        if action == "preset":
            pass  # presets arrive via set
        elif action == "undo":
            if self._draw_poly:
                self._draw_poly.pop()
        elif action == "clear":
            self._draw_poly = []
        elif action == "confirm":
            if len(self.setup.play_area) >= 3:
                self._set_state(S.CAL_COURSE)

    def _apply_preset(self, name: str) -> None:
        sizes = {"small": (2.0, 1.5), "medium": (3.0, 2.0), "large": (4.0, 2.5)}
        w, h = sizes.get(name, (3.0, 2.0))
        self.setup.play_area = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        self.setup.start = CircleZone(0.0, 0.0, 0.15)
        self._draw_poly = []

    def _course_action(self, action: str, msg: dict) -> None:
        if action == "select":
            idx = int(msg.get("index", 0))
            courses = load_courses()
            if 0 <= idx < len(courses):
                self.current_course_id = courses[idx]["id"]
                self.layout = CourseLayout(courses[idx], self.setup.play_area)
        elif action == "confirm":
            # "Choose" sends the card index too: select then advance in one click.
            if msg.get("index") is not None:
                idx = int(msg.get("index", 0))
                courses = load_courses()
                if 0 <= idx < len(courses):
                    self.current_course_id = courses[idx]["id"]
                    self.layout = CourseLayout(courses[idx], self.setup.play_area)
            if self.layout is not None:
                self._set_state(S.CAL_PLACE)
        elif action == "prev" or action == "next":
            courses = load_courses()
            cur = [c["id"] for c in courses].index(self.current_course_id) if self.current_course_id in [c["id"] for c in courses] else 0
            cur = (cur + (1 if action == "next" else -1)) % len(courses)
            self.current_course_id = courses[cur]["id"]
            self.layout = CourseLayout(courses[cur], self.setup.play_area)

    def _place_action(self, action: str) -> None:
        if action == "confirm":
            self._start_capture(1.5, "obstacles")

    def _propose_obstacles(self, avg: np.ndarray) -> None:
        if self.plane is None or self.mapper is None:
            return
        cup = (self.setup.hole.x, self.setup.hole.y, self.setup.hole.r) if self.setup.hole else None
        proposals = detect_obstacles(avg, self.plane, self.cam, self.mapper,
                                     self.setup.play_area, cup)
        # Keep already-confirmed obstacles; replace proposals.
        confirmed = [o for o in self.setup.obstacles if o.state == "confirmed"]
        new_obs: list[Obstacle] = list(confirmed)
        for i, p in enumerate(proposals):
            label = self._match_template_label(p["polygon"]) or f"Object {len(new_obs) + 1}"
            new_obs.append(Obstacle(
                id=f"ob{len(new_obs)}", label=label, kind=p["kind"],
                polygon=p["polygon"], state="proposed", confidence=p["confidence"]))
        self.setup.obstacles = new_obs
        self._set_state(S.CAL_OBSTACLES)

    def _match_template_label(self, poly) -> str | None:
        if self.layout is None:
            return None
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        for g in self.layout.ghost_polygons():
            if abs(cx - g["cx"]) < 0.3 and abs(cy - g["cy"]) < 0.3:
                return g["label"]
        return None

    def _obstacles_action(self, action: str) -> None:
        if action == "confirm":
            pending = [o for o in self.setup.obstacles if o.state in ("proposed", "drawing")]
            if not pending:
                self._obstacles_done()
        elif action == "confirm_all":
            flagged = [o for o in self.setup.obstacles if o.state == "proposed" and o.confidence < 0.7]
            if not flagged:
                for o in self.setup.obstacles:
                    if o.state == "proposed":
                        o.state = "confirmed"
                self._obstacles_done()
        elif action == "draw":
            self._start_drawing_obstacle()
        elif action == "redetect":
            self._start_capture(1.5, "obstacles")
        elif action == "delete":
            if self._selected_obstacle is not None:
                o = self.setup.obstacles[self._selected_obstacle]
                if o.state != "deleted":
                    o.state = "deleted"
        elif action == "undo":
            # Restore most-recently-deleted, else pop a manual drawing.
            for o in reversed(self.setup.obstacles):
                if o.state == "deleted":
                    o.state = "confirmed"
                    break
            else:
                if self.setup.obstacles and self.setup.obstacles[-1].state == "drawing":
                    self.setup.obstacles.pop()
        elif action == "select":
            pass  # selection via pointer

    def _obstacles_done(self) -> None:
        # After the course/obstacles are confirmed: resume play on a rebuild
        # (holes 2+), otherwise continue calibration to the cup.
        if self._rebuilding:
            self._set_state(S.PLAY)
        else:
            self._set_state(S.CAL_CUP)

    def _start_drawing_obstacle(self) -> None:
        if not self.setup.play_area:
            return
        xs = [p[0] for p in self.setup.play_area]
        ys = [p[1] for p in self.setup.play_area]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        w = h = 0.3
        poly = [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
                (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]
        o = Obstacle(id=f"ob{len(self.setup.obstacles)}",
                     label=f"Object {len(self.setup.obstacles) + 1}",
                     kind="soft", polygon=poly, state="drawing", confidence=1.0)
        self.setup.obstacles.append(o)
        self._selected_obstacle = len(self.setup.obstacles) - 1

    def _cup_action(self, action: str) -> None:
        if action == "confirm":
            self._set_state(S.CAL_BALLS)
        elif action == "redetect":
            self._start_capture(1.5, "cup")
        elif action == "draw":
            self._draw_circle_center = None
            self._draw_circle_r = 0.0
            self._dragging = ("circle", "radius")

    def _propose_cup(self, avg: np.ndarray) -> None:
        # The cup is a new static above-floor blob containing a white ring.
        # Simplified: look for a small bright region above the floor near the
        # course's expected hole position (or anywhere in the play area).
        if self.plane is None or self.mapper is None:
            return
        proposals = detect_obstacles(avg, self.plane, self.cam, self.mapper,
                                     self.setup.play_area, None,
                                     min_height_m=0.01, ball_diameter_m=0.02)
        # Prefer the proposal nearest the template hole.
        best = None
        best_d = 1e9
        target = self.layout.hole if self.layout else None
        for p in proposals:
            cx = sum(q[0] for q in p["polygon"]) / len(p["polygon"])
            cy = sum(q[1] for q in p["polygon"]) / len(p["polygon"])
            d = np.hypot(cx - target[0], cy - target[1]) if target else 0.0
            if d < best_d:
                best_d = d
                best = (cx, cy)
        if best is not None:
            self.setup.hole = CircleZone(best[0], best[1], 0.045)

    def _balls_action(self, action: str) -> None:
        if action == "confirm":
            self._set_state(S.GAME_START)
        elif action == "add_ball":
            self._ensure_mock_balls(min(6, len(self._mock_balls) + 1))

    def _gamestart_action(self, action: str, msg: dict) -> None:
        if action == "confirm":
            self._begin_game()

    def _play_action(self, action: str) -> None:
        if action == "undo":
            self._undo_last_shot()
        elif action == "confirm":
            # "Course is set" from new-object prompt handled elsewhere; no-op.
            pass

    def _pause_action(self, action: str, msg: dict) -> None:
        rows = ["resume", "undo", "fix", "recalibrate", "course", "music", "quit"]
        if action == "resume" or action == "back":
            self._set_state(self._prev_state_for_pause)
        elif action == "down":
            self._pause_focus = (self._pause_focus + 1) % len(rows)
        elif action == "up":
            self._pause_focus = (self._pause_focus - 1) % len(rows)
        elif action == "select":
            # A click/Enter on a pause row both focuses and activates it.
            self._pause_focus = int(msg.get("index", 0))
            self._pause_activate(rows[self._pause_focus])
        elif action == "confirm":
            row = rows[self._pause_focus]
            self._pause_activate(row)
        elif action == "undo":
            self._set_state(self._prev_state_for_pause)
            self._undo_last_shot()

    def _pause_activate(self, row: str) -> None:
        if row == "resume":
            self._set_state(self._prev_state_for_pause)
        elif row == "undo":
            self._set_state(self._prev_state_for_pause)
            self._undo_last_shot()
        elif row == "fix":
            self._set_state(S.FIX_SCORE)
        elif row == "recalibrate":
            self._recal_flyout = not self._recal_flyout
        elif row == "course":
            self._set_state(S.CAL_COURSE)
        elif row == "music":
            self._settings_return = S.PAUSE
            self._set_state(S.SETTINGS)
        elif row == "quit":
            self._set_state(S.BOOT)

    def _fixscore_action(self, action: str) -> None:
        if action == "confirm":
            self.events.append(EventType.MANUAL_ADJUST, self.players[self.active_index].id if self.players else "", self.hole)
            self._set_state(S.PAUSE)
        elif action == "back":
            self._set_state(S.PAUSE)

    def _holecomplete_action(self, action: str) -> None:
        if action == "confirm":
            if self.hole >= self.holes:
                self._set_state(S.GAME_FINISH)
            else:
                self.hole += 1
                self._enter_hole()
        elif action == "replay":
            self._reset_hole_scores()
            self._enter_hole()
        elif action == "fix":
            self._set_state(S.FIX_SCORE)

    def _finish_action(self, action: str) -> None:
        if action == "confirm" or action == "again":
            self._start_new_game()
        elif action == "new_courses":
            self.setup = Setup()
            self._set_state(S.CAL_COURSE)
        elif action == "start":
            self._set_state(S.BOOT)

    # ===================================================================== #
    # Pointer (feed-space normalized 0..1)
    # ===================================================================== #
    def _handle_pointer(self, msg: dict) -> None:
        st = self.state
        nx = float(msg.get("x", 0.0))
        ny = float(msg.get("y", 0.0))
        ptype = msg.get("type", "down")
        if st == S.CAL_AREA:
            self._pointer_area(ptype, nx, ny)
        elif st == S.CAL_CUP and self._dragging is not None:
            self._pointer_cup(ptype, nx, ny)
        elif st == S.CAL_OBSTACLES:
            self._pointer_obstacles(ptype, nx, ny)
        elif st == S.PLAY and ptype == "down":
            self._pointer_putt(nx, ny)

    def _feed_to_floor(self, nx: float, ny: float) -> tuple[float, float] | None:
        if self.mapper is None:
            return None
        px = nx * self._feed_w
        py = ny * self._feed_h
        # Prefer depth; fall back to ray intersection.
        if self._frame_depth is not None:
            x = int(np.clip(px, 0, self._feed_w - 1))
            y = int(np.clip(py, 0, self._feed_h - 1))
            z = float(self._frame_depth[y, x])
            if z > 200:
                return self.mapper.depth_pixel_to_floor(px, py, z)
        return self.mapper.pixel_ray_to_floor(px, py)

    def _pointer_area(self, ptype: str, nx: float, ny: float) -> None:
        f = self._feed_to_floor(nx, ny)
        if f is None:
            return
        if ptype == "down":
            self._draw_poly.append(f)
        elif ptype == "move":
            if self._draw_poly:
                self._draw_poly[-1] = f
        elif ptype == "up":
            pass

    def _pointer_cup(self, ptype: str, nx: float, ny: float) -> None:
        f = self._feed_to_floor(nx, ny)
        if f is None:
            return
        if ptype == "down":
            self._draw_circle_center = f
            self._dragging = ("circle", "radius")
        elif ptype == "move" and self._draw_circle_center is not None:
            self._draw_circle_r = np.hypot(f[0] - self._draw_circle_center[0], f[1] - self._draw_circle_center[1])
        elif ptype == "up":
            if self._draw_circle_center is not None and self._draw_circle_r > 0.02:
                self.setup.hole = CircleZone(self._draw_circle_center[0], self._draw_circle_center[1], self._draw_circle_r)
                self._dragging = None

    def _pointer_obstacles(self, ptype: str, nx: float, ny: float) -> None:
        f = self._feed_to_floor(nx, ny)
        if f is None:
            return
        if ptype == "down":
            # Select an obstacle under the pointer.
            idx = self._obstacle_at(f)
            if idx is not None:
                self._selected_obstacle = idx
            elif self._selected_obstacle is not None and self._dragging is None:
                # Add a corner? (simplified: no-op)
                pass

    def _obstacle_at(self, f) -> int | None:
        for i, o in enumerate(self.setup.obstacles):
            if o.state in ("deleted",):
                continue
            if point_in_polygon(f[0], f[1], o.polygon):
                return i
        return None

    def _pointer_putt(self, nx: float, ny: float) -> None:
        if self._putt is not None:
            return
        target = self._feed_to_floor(nx, ny)
        if target is None:
            return
        ball_id = self._active_ball_id()
        if ball_id is None:
            return
        start = self._ball_positions.get(ball_id)
        if start is None:
            return
        self._putt = {"ball_id": ball_id, "start": start, "target": target,
                      "t0": time.time(), "duration": 1.1}

    # ===================================================================== #
    # Text / set (settings, names, steppers)
    # ===================================================================== #
    def _handle_text(self, msg: dict) -> None:
        key = msg.get("key")
        val = str(msg.get("value", ""))
        if key == "player_name" and self.state == S.CAL_BALLS:
            idx = int(msg.get("index", 0))
            if 0 <= idx < len(self.players):
                self.players[idx].name = val

    def _handle_set(self, msg: dict) -> None:
        key = msg.get("key")
        val = msg.get("value")
        if key == "preset" and self.state == S.CAL_AREA:
            self._apply_preset(str(val))
        elif key == "holes":
            self.holes = max(1, min(9, int(val)))
        elif key == "stroke_cap":
            self.stroke_cap = max(3, min(12, int(val)))
        elif key == "stepper":
            # Fix-score +/-
            pid = msg.get("player_id")
            delta = int(msg.get("delta", 0))
            self._adjust_score(pid, delta)
        elif key.startswith("settings."):
            path = key.split(".")[1:]
            self.settings.set(val, *path)
            self.settings.save()

    # ===================================================================== #
    # Game setup / flow
    # ===================================================================== #
    def _ensure_mock_balls(self, n: int) -> None:
        if not self.is_mock:
            return
        self._mock_balls = []
        self._ball_positions = {}
        colors = PLAYER_COLORS
        for i in range(n):
            # Place sample balls at distinct spots near the start zone.
            ox = -0.4 + 0.3 * i
            oy = 0.0
            if self.layout is not None:
                ox = self.layout.start["x"] + (-0.3 + 0.3 * i)
                oy = self.layout.start["y"]
            pos = (ox, oy)
            self._mock_balls.append({"x": ox, "y": oy, "r": 0.025,
                                     "color": hex_to_bgr(colors[i % len(colors)]),
                                     "height_m": 0.05})
            self._ball_positions[f"ball{i}"] = pos

    def _sync_mock_scene(self) -> None:
        if not self.is_mock:
            return
        obstacles = []
        if (self.state in (S.CAL_PLACE, S.HOLE_START) and not self.setup.obstacles
                and self.layout is not None):
            # Simulate the player's objects already on the floor during the
            # "place" step, so the mock scan has something to detect.
            for g in self.layout.ghost_polygons():
                obstacles.append({"x": g["cx"], "y": g["cy"],
                                  "w": g["w"], "h": g["h"],
                                  "height_m": 0.05, "color": (120, 118, 112)})
        else:
            for o in self.setup.obstacles:
                if o.state in ("confirmed", "proposed", "selected", "drawing"):
                    cx = sum(p[0] for p in o.polygon) / max(1, len(o.polygon))
                    cy = sum(p[1] for p in o.polygon) / max(1, len(o.polygon))
                    obstacles.append({"x": cx, "y": cy,
                                      "w": self._poly_size(o.polygon, 0), "h": self._poly_size(o.polygon, 1),
                                      "height_m": 0.05, "color": (120, 118, 112)})
        cup = None
        if self.setup.hole is not None:
            cup = {"x": self.setup.hole.x, "y": self.setup.hole.y, "r": self.setup.hole.r}
        # Animate any active putt.
        if self._putt is not None:
            self._step_putt()
        balls = [dict(b) for b in self._mock_balls]
        self.backend.set_scene(obstacles=obstacles, cup=cup, balls=balls)

    def _poly_size(self, poly, axis: int) -> float:
        vals = [p[axis] for p in poly]
        return max(0.05, (max(vals) - min(vals)) or 0.2)

    def _step_putt(self) -> None:
        p = self._putt
        t = min(1.0, (time.time() - p["t0"]) / p["duration"])
        e = 1 - (1 - t) ** 3  # ease-out
        sx, sy = p["start"]
        tx, ty = p["target"]
        x = sx + (tx - sx) * e
        y = sy + (ty - sy) * e
        # Sink if within the hole.
        if self.setup.hole is not None:
            hx, hy, hr = self.setup.hole.x, self.setup.hole.y, self.setup.hole.r
            if np.hypot(x - hx, y - hy) < hr:
                x, y = hx, hy
        self._ball_positions[p["ball_id"]] = (x, y)
        # Update the mock ball's rendered position.
        idx = int(p["ball_id"].replace("ball", ""))
        if 0 <= idx < len(self._mock_balls):
            self._mock_balls[idx]["x"] = x
            self._mock_balls[idx]["y"] = y
        if t >= 1.0:
            # Clamp to play area -> OOB if outside.
            self._resolve_putt_end(p)

    def _resolve_putt_end(self, p: dict) -> None:
        ball_id = p["ball_id"]
        x, y = self._ball_positions[ball_id]
        self._putt = None
        inside = point_in_polygon(x, y, self.setup.play_area) if self.setup.play_area else True
        if not inside and self.setup.play_area:
            # Snap to the exit point (last in-bounds along the path).
            exit_pt = self._exit_point(p["start"], (x, y), self.setup.play_area)
            self._ball_positions[ball_id] = exit_pt
            idx = int(ball_id.replace("ball", ""))
            if 0 <= idx < len(self._mock_balls):
                self._mock_balls[idx]["x"], self._mock_balls[idx]["y"] = exit_pt

    def _exit_point(self, start, end, poly) -> tuple[float, float]:
        # Intersection of the segment with the polygon boundary, nearest start.
        best = start
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            hit = self._seg_seg(start, end, a, b)
            if hit is not None and np.hypot(hit[0] - start[0], hit[1] - start[1]) < np.hypot(best[0] - start[0], best[1] - start[1]):
                best = hit
        return best

    @staticmethod
    def _seg_seg(p0, p1, a, b):
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        ex, ey = b[0] - a[0], b[1] - a[1]
        denom = dx * ey - dy * ex
        if abs(denom) < 1e-9:
            return None
        t = ((a[0] - p0[0]) * ey - (a[1] - p0[1]) * ex) / denom
        u = ((a[0] - p0[0]) * dy - (a[1] - p0[1]) * dx) / denom
        if 0 <= t <= 1 and 0 <= u <= 1:
            return (p0[0] + t * dx, p0[1] + t * dy)
        return None

    # ===================================================================== #
    # Game rules
    # ===================================================================== #
    def _apply_saved_setup(self) -> None:
        if self.setup.floor_plane is not None:
            self.plane = self.setup.floor_plane
            cam = self.setup.camera
            self.cam = CameraModel(fx=cam.get("fx", self.cam.fx), fy=cam.get("fy", self.cam.fy),
                                   cx=cam.get("cx", self.cam.cx), cy=cam.get("cy", self.cam.cy))
            self.mapper = FloorMapper(self.plane, self.cam)
        self.players = list(self.setup.players)
        if self.setup.courses:
            self.course_ids = list(self.setup.courses)
        if self.layout is None and self.setup.play_area:
            self.layout = CourseLayout(course_by_id(self.course_ids[0] if self.course_ids else "hallway") or load_courses()[0], self.setup.play_area)

    def _begin_game_from_setup(self) -> None:
        self._setup_players_from_setup()
        self._begin_game()

    def _setup_players_from_setup(self) -> None:
        # Players from saved setup; ensure a ball mapping.
        self.players = list(self.setup.players)
        for i, p in enumerate(self.players):
            self.ball_for_player[p.id] = f"ball{i}"
        self._ensure_mock_balls(len(self.players))

    def _begin_game(self) -> None:
        # Build players from ball setup if not already.
        self._derive_players_from_balls()
        self.course_ids = [course_for_hole(h) for h in range(1, self.holes + 1)]
        self.course_pars = [course_by_id(c)["par"] for c in self.course_ids]
        self.player_scores = {p.id: [None] * self.holes for p in self.players}
        self.hole = 1
        self.active_index = 0
        self.events.clear()
        self._save_setup()
        self._enter_hole()

    def _derive_players_from_balls(self) -> None:
        if not self.players:
            self.players = []
            self.ball_for_player = {}
            for i in range(len(self._mock_balls)):
                color = PLAYER_COLORS[i % len(PLAYER_COLORS)]
                pid = f"p{i}"
                self.players.append(Player(id=pid, name=f"Player {i + 1}", color=color,
                                           hue_name=HUE_NAMES.get(color, "custom"), order=i))
                self.ball_for_player[pid] = f"ball{i}"
        # Register tracked balls with hue ranges sampled from their colors.
        for p in self.players:
            ball_id = self.ball_for_player.get(p.id)
            hue_range = self._hue_range_for_color(p.color)
            p.hue_range = hue_range
            self.tracker.add_ball(ball_id or p.id, p.id, p.color, hue_range)

    def _hue_range_for_color(self, hex_color: str) -> tuple[int, int]:
        import cv2
        bgr = np.uint8([[hex_to_bgr(hex_color)]])
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[0][0]
        h = int(hsv[0])
        lo = max(0, h - 15)
        hi = min(179, h + 15)
        return (lo, hi)

    def _save_setup(self) -> None:
        self.setup.players = list(self.players)
        self.setup.courses = list(self.course_ids)
        self.setup.name = self.setup.name or "Living room"
        self.setup.save()

    def _enter_hole(self) -> None:
        self.current_course_id = self.course_ids[self.hole - 1]
        self.layout = CourseLayout(course_by_id(self.current_course_id), self.setup.play_area)
        self.finished_hole = {p.id: False for p in self.players}
        # First unfinished player to lead.
        self.active_index = 0
        self._rebuilding = self.hole > 1
        self._putt = None
        self._ball_was_moving = {}
        # Mock: move the physical cup to the new course's hole.
        if self._rebuilding and self.is_mock and self.layout is not None:
            self.setup.hole = CircleZone(self.layout.hole[0], self.layout.hole[1], 0.045)
        # Position balls in the start zone for the first stroke.
        for i, p in enumerate(self.players):
            ball_id = self.ball_for_player.get(p.id, f"ball{i}")
            sx = self.layout.start["x"] + 0.15 * i
            sy = self.layout.start["y"]
            self._ball_positions[ball_id] = (sx, sy)
            idx = int(ball_id.replace("ball", ""))
            if 0 <= idx < len(self._mock_balls):
                self._mock_balls[idx]["x"] = sx
                self._mock_balls[idx]["y"] = sy
        self.tracker.reset_positions()
        if self.hole > 1:
            self._set_state(S.HOLE_START)
        else:
            self._set_state(S.PLAY)

    def _reset_hole_scores(self) -> None:
        for pid in self.player_scores:
            self.player_scores[pid][self.hole - 1] = None

    def _active_player(self) -> Player | None:
        if 0 <= self.active_index < len(self.players):
            return self.players[self.active_index]
        return None

    def _active_ball_id(self) -> str | None:
        p = self._active_player()
        if p is None:
            return None
        return self.ball_for_player.get(p.id)

    def _confirmed_obstacles(self) -> list[Obstacle]:
        return [o for o in self.setup.obstacles if o.state == "confirmed"]

    def _apply_ball_motion(self, now: float) -> None:
        if self.state != S.PLAY:
            return
        p = self._active_player()
        if p is None:
            return
        ball_id = self._active_ball_id()
        tb = self.tracker.balls.get(ball_id) if ball_id else None
        if tb is None:
            return
        strokes = self._current_strokes(p.id)
        was_moving = self._ball_was_moving.get(ball_id, False)
        # Stroke on stopped -> moving.
        if tb.moving and not was_moving:
            self._record_stroke(p.id)
        self._ball_was_moving[ball_id] = tb.moving
        # Resolve when the ball stops.
        if not tb.moving and was_moving:
            self._resolve_after_stop(p, tb, now)
        # Position for HUD.
        if tb.smoothed is not None:
            self._ball_positions[ball_id] = tb.smoothed

    def _current_strokes(self, pid: str) -> int:
        sc = self.player_scores.get(pid, [])
        return sc[self.hole - 1] if self.hole - 1 < len(sc) and sc[self.hole - 1] is not None else 0

    def _set_strokes(self, pid: str, n: int) -> None:
        if pid in self.player_scores:
            self.player_scores[pid][self.hole - 1] = n

    def _record_stroke(self, pid: str) -> None:
        self._set_strokes(pid, self._current_strokes(pid) + 1)
        self.events.append(EventType.STROKE, pid, self.hole)

    def _resolve_after_stop(self, p: Player, tb, now: float) -> None:
        pos = tb.smoothed
        if pos is None:
            return
        # Holed?
        if self.setup.hole is not None:
            hx, hy, hr = self.setup.hole.x, self.setup.hole.y, self.setup.hole.r
            if np.hypot(pos[0] - hx, pos[1] - hy) < hr:
                self.finished_hole[p.id] = True
                self.events.append(EventType.HOLE_OUT, p.id, self.hole)
                self._set_state(S.HOLE_OUT)
                return
        # Out of bounds?
        if self.setup.play_area and not point_in_polygon(pos[0], pos[1], self.setup.play_area):
            self._set_strokes(p.id, self._current_strokes(p.id) + 1)  # penalty
            self.events.append(EventType.OOB, p.id, self.hole)
            self._set_state(S.OOB)
            return
        # In play: advance turn.
        self._advance_turn()

    def _advance_turn(self) -> None:
        self._set_state(S.TURN_CHANGE)
        self._transition_next = S.PLAY
        self._transition_until = time.time() + 1.4
        # Advance to the next unfinished player.
        n = len(self.players)
        for _ in range(n):
            self.active_index = (self.active_index + 1) % n
            if not self.finished_hole.get(self.players[self.active_index].id, False):
                break
        if all(self.finished_hole.get(p.id, False) for p in self.players):
            self._transition_next = S.HOLE_COMPLETE

    def _undo_last_shot(self) -> None:
        e = self.events.pop_last_for_hole(self.hole)
        if e is None:
            return
        pid = e.player_id
        cur = self._current_strokes(pid)
        if e.type in (EventType.STROKE, EventType.OOB, EventType.MANUAL_ADJUST):
            self._set_strokes(pid, max(0, cur - 1))
        elif e.type == EventType.HOLE_OUT:
            self.finished_hole[pid] = False
        # Step the active player back.
        for i, pl in enumerate(self.players):
            if pl.id == pid:
                self.active_index = i
                break
        self._set_state(S.PLAY)

    def _adjust_score(self, pid: str, delta: int) -> None:
        if pid is None:
            return
        self._set_strokes(pid, max(0, self._current_strokes(pid) + delta))

    def _start_new_game(self) -> None:
        self._begin_game()

    # ===================================================================== #
    # Snapshot (UI state)
    # ===================================================================== #
    def snapshot(self) -> dict[str, Any]:
        return {
            "screen": SCREEN_BY_STATE.get(self.state, "S01"),
            "state": self.state,
            "version": __version__,
            "input_mode": "keyboard",
            "sensor": self.sensor_desc,
            "sensor_status": self.sensor_status,
            "settings": self.settings.data,
            "feed": {"enabled": self.settings.get("display", "showCameraFeed", default=True),
                     "w": self._feed_w, "h": self._feed_h},
            "setup": self._setup_snapshot(),
            "game": self._game_snapshot(),
            "overlay": self._overlay_snapshot(),
            "ui": self._ui_snapshot(),
        }

    def _setup_snapshot(self) -> dict:
        return {
            "name": self.setup.name,
            "play_area": [[round(p[0], 3), round(p[1], 3)] for p in self.setup.play_area],
            "start": self._circle_dict(self.setup.start),
            "hole": self._circle_dict(self.setup.hole),
            "obstacles": [o.as_dict() for o in self.setup.obstacles],
            "players": [p.as_dict() for p in self.players],
            "has_saved": self.setup.has_saved(),
            "capturing": self.capturing,
            "capture_label": self._capture_label,
        }

    @staticmethod
    def _circle_dict(c: CircleZone | None) -> dict | None:
        if c is None:
            return None
        return {"x": round(c.x, 3), "y": round(c.y, 3), "r": round(c.r, 3)}

    def _game_snapshot(self) -> dict:
        active = self._active_player()
        course = course_by_id(self.current_course_id)
        return {
            "hole": self.hole,
            "holes": self.holes,
            "stroke_cap": self.stroke_cap,
            "active_player_id": active.id if active else None,
            "course": course,
            "course_id": self.current_course_id,
            "pars": self.course_pars,
            "scores": self.player_scores,
            "finished_hole": self.finished_hole,
            "players": [p.as_dict() for p in self.players],
            "event_log": self.events.as_list(),
            "transitioning": self._transition_next is not None,
        }

    def _ui_snapshot(self) -> dict:
        st = self.state
        if st == S.BOOT:
            saved = Setup.load()
            return {"menu": [("New game", "A"), ("Load last setup", "A"), ("Course library", None), ("Settings", None)],
                    "saved_meta": "Living room · 3 courses" if saved else None}
        if st == S.SENSOR_CHECK:
            return {"sensor": self.sensor_desc}
        if st == S.VERIFY:
            return {}
        if st == S.CAL_FLOOR:
            return {"step": 1}
        if st == S.CAL_AREA:
            return {"step": 2, "presets": ["small", "medium", "large"]}
        if st == S.CAL_COURSE:
            return {"courses": self._courses_snapshot(), "step": 3}
        if st in (S.CAL_PLACE, S.HOLE_START):
            return {"step": 3, "ghosts": self._ghosts_snapshot(), "course": course_by_id(self.current_course_id)}
        if st == S.CAL_OBSTACLES:
            return {"step": 3, "obstacles": [o.as_dict() for o in self.setup.obstacles],
                    "selected": self._selected_obstacle}
        if st == S.CAL_CUP:
            return {"step": 4, "searching": self.capturing}
        if st == S.CAL_BALLS:
            return {"step": 5, "balls": self._mock_ball_snapshot(), "players": [p.as_dict() for p in self.players]}
        if st == S.GAME_START:
            return {"players": [p.as_dict() for p in self.players], "holes": self.holes, "stroke_cap": self.stroke_cap}
        if st == S.PLAY:
            return self._hud_snapshot()
        if st == S.TURN_CHANGE:
            return self._turnchange_snapshot()
        if st == S.HOLE_OUT:
            return self._holeout_snapshot()
        if st == S.OOB:
            return self._oob_snapshot()
        if st == S.PAUSE:
            return self._pause_snapshot()
        if st == S.FIX_SCORE:
            return {"players": [p.as_dict() for p in self.players]}
        if st == S.HOLE_COMPLETE:
            return self._holecomplete_snapshot()
        if st == S.GAME_FINISH:
            return self._finish_snapshot()
        if st == S.SETTINGS:
            return {"return": self._settings_return}
        if st == S.CREDITS:
            return {}
        if st == S.CHANGELOG:
            return {"changelog": self._changelog()}
        return {}

    # -- sub-snapshots ---------------------------------------------------- #
    def _courses_snapshot(self) -> list[dict]:
        out = []
        for c in load_courses():
            out.append({"id": c["id"], "level": c["level"], "name": c["name"],
                        "par": c["par"], "blurb": c["blurb"], "rule": c["rule"],
                        "needs": [o["item"] for o in c.get("obstacles", [])],
                        "obstacles": c.get("obstacles", []),
                        "start": c["start"], "hole": c["hole"]})
        return out

    def _ghosts_snapshot(self) -> list[dict]:
        if self.layout is None:
            return []
        ghosts = []
        for g in self.layout.ghost_polygons():
            ghosts.append({"label": g["label"], "item": g["item"],
                           "polygon": g["polygon"], "real_size_cm": g["real_size_cm"],
                           "kind": g["kind"]})
        return ghosts

    def _mock_ball_snapshot(self) -> list[dict]:
        out = []
        for i, b in enumerate(self._mock_balls):
            out.append({"id": f"ball{i}", "color": PLAYER_COLORS[i % len(PLAYER_COLORS)],
                        "x": b["x"], "y": b["y"], "hue_name": HUE_NAMES.get(PLAYER_COLORS[i % len(PLAYER_COLORS)], "custom")})
        return out

    def _hud_snapshot(self) -> dict:
        active = self._active_player()
        motion = self._ball_motion_status()
        return {
            "active_player": active.as_dict() if active else None,
            "others": [p.as_dict() for p in self.players if p.id != (active.id if active else None)],
            "hole": self.hole, "holes": self.holes,
            "course": course_by_id(self.current_course_id),
            "motion": motion,
            "scores": self.player_scores,
            "finished_hole": self.finished_hole,
            "lost_balls": self._lost_ball_snapshot(),
        }

    def _ball_motion_status(self) -> dict:
        ball_id = self._active_ball_id()
        tb = self.tracker.balls.get(ball_id) if ball_id else None
        moving = tb.moving if tb else False
        hidden = tb.hidden if tb else False
        dist = None
        if tb is not None and tb.smoothed is not None and self.setup.hole is not None:
            dist = round(np.hypot(tb.smoothed[0] - self.setup.hole.x, tb.smoothed[1] - self.setup.hole.y), 1)
        return {"moving": moving, "hidden": hidden, "dist_to_cup": dist}

    def _lost_ball_snapshot(self) -> list[dict]:
        return []

    def _turnchange_snapshot(self) -> dict:
        active = self._active_player()
        return {"player": active.as_dict() if active else None,
                "stroke": self._current_strokes(active.id) if active else 0}

    def _holeout_snapshot(self) -> dict:
        active = self._active_player()
        par = self.course_pars[self.hole - 1] if self.hole - 1 < len(self.course_pars) else 2
        strokes = self._current_strokes(active.id) if active else 0
        return {"player": active.as_dict() if active else None, "strokes": strokes, "par": par}

    def _oob_snapshot(self) -> dict:
        active = self._active_player()
        return {"player": active.as_dict() if active else None,
                "strokes": self._current_strokes(active.id) if active else 0}

    def _pause_snapshot(self) -> dict:
        active = self._active_player()
        return {"focus": self._pause_focus, "recal_flyout": self._recal_flyout,
                "hole": self.hole, "active": active.as_dict() if active else None,
                "scores": self.player_scores}

    def _holecomplete_snapshot(self) -> dict:
        return {"scorecard": build_scorecard(self.players, self.player_scores, self.hole, self.course_pars),
                "leading": self._leader_snapshot(),
                "next_hole": self.hole + 1,
                "next_course": course_by_id(self.course_ids[self.hole]) if self.hole < len(self.course_ids) else None,
                "is_last": self.hole >= self.holes}

    def _finish_snapshot(self) -> dict:
        winner = self._leader_snapshot()
        return {"champion": winner, "standings": self._standings_snapshot(),
                "stats": self._night_stats()}

    def _leader_snapshot(self) -> dict | None:
        if not self.players:
            return None
        ordered = standings([p.as_dict() for p in self.players], self.player_scores)
        top = ordered[0]
        par = sum(self.course_pars)
        t = total(self.player_scores, top["id"])
        return {"name": top["name"], "color": top["color"], "total": t, "vs_par": vs_par(t, par)}

    def _standings_snapshot(self) -> list[dict]:
        ordered = standings([p.as_dict() for p in self.players], self.player_scores)
        par = sum(self.course_pars)
        out = []
        for i, p in enumerate(ordered):
            t = total(self.player_scores, p["id"])
            out.append({**p, "pos": i + 1, "total": t, "vs_par": vs_par(t, par)})
        return out

    def _night_stats(self) -> dict:
        return {"total_strokes": sum(total(self.player_scores, p.id) for p in self.players),
                "holes_played": self.holes}

    def _changelog(self) -> list[dict]:
        try:
            text = (__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "CHANGELOG.md").read_text(encoding="utf-8")
        except OSError:
            return []
        releases = []
        for block in text.split("## ")[1:]:
            lines = block.strip().splitlines()
            header = lines[0]
            parts = header.split(" — ")
            version = parts[0].strip()
            rest = parts[1] if len(parts) > 1 else ""
            date = rest.split(" · ")[0].strip() if " · " in rest else rest
            tag = rest.split(" · ")[1].strip() if " · " in rest else ""
            items = []
            for ln in lines[1:]:
                ln = ln.strip()
                if ln.startswith("- new:"):
                    items.append(("new", ln[7:].strip()))
                elif ln.startswith("- improved:"):
                    items.append(("improved", ln[11:].strip()))
                elif ln.startswith("- fixed:"):
                    items.append(("fixed", ln[8:].strip()))
            releases.append({"version": version, "date": date, "tag": tag, "items": items})
        return releases

    # ===================================================================== #
    # Overlay (feed shapes, normalized 0..1)
    # ===================================================================== #
    def _norm(self, px: float, py: float) -> list[float]:
        return [round(px / max(1, self._feed_w), 4), round(py / max(1, self._feed_h), 4)]

    def _floor_poly_norm(self, poly: list[tuple[float, float]]) -> list[list[float]]:
        if self.mapper is None:
            return []
        out = []
        for x, y in poly:
            u, v = self.mapper.floor_to_pixel(x, y)
            out.append(self._norm(u, v))
        return out

    def _overlay_snapshot(self) -> dict:
        o = {"shapes": []}
        if self.mapper is None:
            return o
        st = self.state
        # Play area.
        if self.setup.play_area:
            o["shapes"].append({"type": "polygon", "pts": self._floor_poly_norm(self.setup.play_area),
                                "stroke": "#f2efe8", "stroke_opacity": 0.75, "stroke_width": 3,
                                "fill": "rgba(242,239,232,0.06)", "id": "play_area"})
        # Start.
        if self.setup.start is not None:
            u, v = self.mapper.floor_to_pixel(self.setup.start.x, self.setup.start.y)
            ru = self.setup.start.r * self.cam.fx / (self._depth_at_floor(self.setup.start.x, self.setup.start.y) / 1000.0)
            o["shapes"].append({"type": "circle", "x": u / self._feed_w, "y": v / self._feed_h,
                                "r": ru / self._feed_w, "stroke": "#f2efe8", "stroke_width": 3,
                                "fill": "none", "label": "START", "id": "start"})
        # Hole.
        if self.setup.hole is not None:
            u, v = self.mapper.floor_to_pixel(self.setup.hole.x, self.setup.hole.y)
            ru = self.setup.hole.r * self.cam.fx / (self._depth_at_floor(self.setup.hole.x, self.setup.hole.y) / 1000.0)
            o["shapes"].append({"type": "circle", "x": u / self._feed_w, "y": v / self._feed_h,
                                "r": ru / self._feed_w, "stroke": "#8be9c3", "stroke_width": 4,
                                "fill": "rgba(139,233,195,0.25)", "label": None, "id": "hole"})
        # Obstacles.
        for ob in self.setup.obstacles:
            if ob.state == "deleted":
                style = {"stroke": "#ff6b57", "stroke_width": 3, "dash": "6 8", "opacity": 0.35}
            elif ob.state in ("proposed",):
                low = ob.confidence < 0.7
                style = {"stroke": "#ff6b57" if low else "#8be9c3", "stroke_width": 4,
                         "dash": "16 10", "fill": "rgba(255,107,87,0.14)" if low else "rgba(139,233,195,0.12)"}
            elif ob.state == "selected":
                style = {"stroke": "#8be9c3", "stroke_width": 5, "fill": "rgba(139,233,195,0.16)"}
            elif ob.state == "drawing":
                style = {"stroke": "#f2efe8", "stroke_width": 3, "dash": "12 10"}
            else:  # confirmed
                style = {"stroke": "#f2efe8", "stroke_width": 3, "fill": "rgba(242,239,232,0.08)"}
            o["shapes"].append({"type": "polygon", "pts": self._floor_poly_norm(ob.polygon),
                                "id": f"obstacle_{ob.id}", "label": ob.label, **style})
        # Balls + trail.
        for i, p in enumerate(self.players):
            ball_id = self.ball_for_player.get(p.id, f"ball{i}")
            pos = self._ball_positions.get(ball_id)
            if pos is None:
                continue
            u, v = self.mapper.floor_to_pixel(*pos)
            o["shapes"].append({"type": "circle", "x": u / self._feed_w, "y": v / self._feed_h,
                                "r": 0.012, "fill": p.color, "stroke": "#f2efe8", "stroke_width": 3,
                                "label": p.name, "id": f"ball_{p.id}"})
        return o

    def _depth_at_floor(self, x: float, y: float) -> float:
        # Approximate distance from the camera to a floor point (meters -> mm).
        if self.plane is None or self.mapper is None:
            return 2000.0
        p = self.mapper.origin + x * self.mapper.e_x + y * self.mapper.e_y
        return float(np.linalg.norm(p)) * 1000.0

    # ===================================================================== #
    # Frame encoding for the websocket
    # ===================================================================== #
    def encode_frame(self) -> bytes | None:
        if self._frame_color is None:
            return None
        import cv2
        ok, buf = cv2.imencode(".jpg", self._frame_color, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            return None
        return buf.tobytes()

    def shutdown(self) -> None:
        if self.backend is not None:
            try:
                self.backend.close()
            except Exception:
                pass
