"""Game engine — the single source of truth.

Owns the sensor backend, settings, calibration/setup, the vision pipeline and
the game rules. It is driven by two inputs:

- ``tick(frame)`` — one sensor frame each cycle (vision + rules + timers).
- ``handle_input(msg)`` — semantic user input from the UI.

and produces one output, ``snapshot()`` — a plain JSON-safe dict the UI renders.
The UI is a dumb display: it sends user intent, never game state.
"""
from __future__ import annotations

import copy
import threading
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
from .vision.ball_tracker import (
    BallTracker, detect_setup_balls, hue_clash_pairs, nearest_setup_ball,
    sample_ball_at_pixel,
)
from .vision.floor import fit_floor_plane
from .vision.geometry import (CameraModel, FloorMapper, HomographyMapper,
                              default_camera, homography_from_corners)
from .vision.obstacle_detect import detect_obstacles, detect_obstacles_color, point_in_polygon


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
               S.CAL_CUP, S.CAL_BALLS, S.GAME_START, S.PLAY, S.TURN_CHANGE,
               S.HOLE_OUT, S.OOB, S.HOLE_START}

CAL_STATES = {
    S.CAL_FLOOR, S.CAL_AREA, S.CAL_COURSE, S.CAL_PLACE,
    S.CAL_OBSTACLES, S.CAL_CUP, S.CAL_BALLS,
}

# Esc / Menu opens pause during a live hole. GAME_START is still "game
# night" setup — Back returns to ball assignment; Menu still pauses.
PAUSE_ON_BACK = {
    S.HOLE_START, S.PLAY, S.TURN_CHANGE,
    S.HOLE_OUT, S.OOB, S.HOLE_COMPLETE,
}
PLAYABLE_ROUND = PAUSE_ON_BACK | {S.GAME_START}
# Celebration overlays are not a useful resume target after recalibrate.
RESUME_FROM_RECAL = {
    S.TURN_CHANGE: S.PLAY,
    S.HOLE_OUT: S.PLAY,
}
IN_GAME_STATES = PLAYABLE_ROUND

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

        # Color-only support (regular 2D webcam): reference frame for obstacle
        # differencing and a homography mapper established during calibration.
        self.reference_color: np.ndarray | None = None
        self._avg_color: np.ndarray | None = None
        self._avg_color_count: float = 0.0
        self._preset_w = 3.0
        self._preset_h = 2.0
        self._preset_name = "medium"

        # Camera enumeration (for the Settings → Camera tab). Names-only so we
        # never open a device while the live backend holds it.
        self._camera_list: list[dict] = []
        self._camera_scan_done = False
        self._camera_error = ""
        threading.Thread(target=self._scan_cameras, daemon=True).start()

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
        self._selected_ghost: int | None = None
        self._place_ghosts: list[dict] = []
        self._place_ghosts_course: str | None = None
        self._drag_anchor: tuple[float, float] | None = None
        self._dragging: tuple | None = None  # ("poly", idx) | ("area", idx) | ("ghost", idx) | ("circle", ...)

        # Putt animation (mock).
        self._putt: dict | None = None
        self._ball_positions: dict[str, tuple[float, float]] = {}
        self._mock_balls: list[dict] = []

        # Lost ball / new object prompt.
        self._lost_since: dict[str, float] = {}
        self._ignored_regions: list[list[tuple[float, float]]] = []

        # Cup detection (S08): search → found → confirm, or Y to draw.
        self._cup_searching = False
        self._cup_manual = False
        self._cup_confidence = 0.0

        # Obstacle edit undo (S07c) — unbounded within the step.
        self._obs_undo: list[list[Obstacle]] = []
        self._obs_drag_shift = False

        # OOB: last in-bounds floor point and the exit crossing.
        self._last_in_bounds: dict[str, tuple[float, float]] = {}
        self._oob_exit: tuple[float, float] | None = None
        self._oob_ball: tuple[float, float] | None = None

        # Turn-change previous player (the one who just stopped).
        self._turn_prev: dict | None = None
        self._turn_prev_stroke = 0

        # Finish still from the last hole-out (served at /snapshot.jpg).
        self._finish_jpeg: bytes | None = None
        self._finish_jpeg_t = 0.0
        self._hole_started_at = 0.0

        # Turn-change / hole-out timing.
        self._transition_until = 0.0
        self._transition_next: str | None = None
        self._last_turn_player: str | None = None
        self._prev_strokes: dict[str, int] = {}
        self._ball_was_moving: dict[str, bool] = {}
        self._last_stroke_t: dict[str, float] = {}
        self._shot_armed = False
        self._tee_seen_since = 0.0
        self._tee_click: tuple[float, float, float] | None = None

        # Settings return-state.
        self._settings_return: str = S.BOOT
        self._settings_tab = "display"     # display | rules | players | camera | about
        self._verify_return: str = S.SENSOR_CHECK  # where Escape returns from Verify
        self._course_return: str | None = None     # Course library opened from Boot
        self._pause_focus = 0
        self._recal_flyout = False
        self._recal_return: str | None = None
        self._recal_single = False
        self._rebuilding = False
        self._last_ball_scan = 0.0
        self._rejected_balls: set[str] = set()
        self._dismissed_hues: set[str] = set()
        self._selected_setup_player: int | None = None

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
        self._camera_error = ""

    @property
    def is_mock(self) -> bool:
        return isinstance(self.backend, MockBackend)

    @property
    def is_color_only(self) -> bool:
        """True when the attached source has no depth (regular 2D webcam)."""
        return self.backend is not None and not getattr(self.backend, "has_depth", True)

    def _scan_cameras(self) -> None:
        try:
            from .sensor.detect import list_webcams
            from .sensor.devices import list_capture_names
            list_capture_names(refresh=True)
            self._camera_list = list_webcams()
        except Exception:
            self._camera_list = []
        self._camera_scan_done = True

    def rescan_cameras(self) -> None:
        self._camera_scan_done = False
        threading.Thread(target=self._scan_cameras, daemon=True).start()

    def grab_frame(self):
        """Return the latest backend frame (or None). The server loop calls this
        instead of holding its own backend reference so the engine can hot-swap
        the camera from the Settings screen."""
        if self.backend is None:
            return None
        try:
            return self.backend.grab()
        except Exception:
            return None

    def _reconfigure_camera(self) -> bool:
        """Close the current backend and reopen per the persisted camera settings.

        Stays on the current screen (Settings) so the Sensor card can show the
        new device. Returns True when a real backend is attached.
        """
        from .sensor import create_backend
        cfg = self.settings.get("camera", default={}) or {}
        mode = str(cfg.get("backend", "auto"))
        index = int(cfg.get("device", 0))
        res = str(cfg.get("resolution", "1280x720"))

        if self.backend is not None:
            try:
                self.backend.close()
            except Exception:
                pass
            self.backend = None

        # Never fall back to the mock during an explicit Settings change.
        backend, _kind = create_backend(
            force=None, allow_mock=False,
            camera_index=index, camera_res=res, backend_mode=mode,
            settings=self.settings,
        )

        self.plane = None
        self.mapper = None
        self.reference_depth = None
        self.reference_color = None
        self._draw_poly = []

        if backend is None:
            self.sensor_status = "none"
            self.sensor_desc = None
            self._camera_error = (
                "Could not open that camera. Close Zoom / Teams / Iriun if it "
                "has the device, then click Apply camera."
            )
            return False
        cam = getattr(backend, "cam", None) or default_camera(
            backend.description.depth_res if backend.description.depth_res != (0, 0)
            else backend.description.color_res
        )
        self.attach_backend(backend, cam)
        self._apply_saved_exposure()
        if self.state not in CAL_STATES:
            self._lock_capture()
        self._camera_error = ""
        return True

    # ===================================================================== #
    # Main loop
    # ===================================================================== #
    def tick(self, frame) -> None:
        now = float(getattr(frame, "t", 0) or 0) or time.time()
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
        if self.is_color_only:
            # Color-only: capture reference/live color, track balls by hue.
            if now < self._capture_until:
                self._accumulate_color(self._frame_color)
            if self.state == S.CAL_BALLS:
                self._maybe_scan_setup_balls(now)
            if self.state in (S.GAME_START, S.PLAY, S.TURN_CHANGE, S.HOLE_OUT, S.OOB):
                self._ensure_mapper()
                if self.mapper is not None:
                    self.tracker.update(self._frame_color, None, self.mapper,
                                        self.cam, self.plane, self._confirmed_obstacles(), now,
                                        play_area=self.setup.play_area,
                                        reference_color=self.reference_color)
                    self._apply_ball_motion(now)
            return
        if self.state == S.CAL_BALLS:
            self._maybe_scan_setup_balls(now)
        if self._frame_depth is None:
            return
        # Accumulate depth while a capture is active.
        if now < self._capture_until:
            self._accumulate_depth(self._frame_depth)
        # Play state: track balls.
        if self.state in (S.GAME_START, S.PLAY, S.TURN_CHANGE, S.HOLE_OUT, S.OOB):
            if self.mapper is not None and self.plane is not None:
                obs = self._confirmed_obstacles()
                self.tracker.update(self._frame_color, self._frame_depth, self.mapper,
                                    self.cam, self.plane, obs, now,
                                    play_area=self.setup.play_area)
                self._apply_ball_motion(now)

    # ===================================================================== #
    # Capture helpers
    # ===================================================================== #
    def _start_capture(self, duration: float, label: str) -> None:
        self._capture_until = time.time() + duration
        self._capture_label = label
        self._avg_depth = None
        self._avg_count = None
        self._avg_color = None
        self._avg_color_count = 0.0

    def _accumulate_depth(self, depth: np.ndarray) -> None:
        d = depth.astype(np.float32)
        valid = (d > 200) & (d < 8000)
        if self._avg_depth is None:
            self._avg_depth = np.where(valid, d, 0.0)
            self._avg_count = valid.astype(np.float32)
        else:
            self._avg_depth += np.where(valid, d, 0.0)
            self._avg_count += valid.astype(np.float32)

    def _accumulate_color(self, color) -> None:
        if color is None:
            return
        c = color.astype(np.float32)
        if self._avg_color is None:
            self._avg_color = c.copy()
            self._avg_color_count = 1.0
        else:
            self._avg_color += c
            self._avg_color_count += 1.0

    def _finish_capture_color(self) -> np.ndarray | None:
        if self._avg_color is None or self._avg_color_count <= 0:
            self._avg_color = None
            self._avg_color_count = 0.0
            self._capture_until = 0.0
            return None
        avg = (self._avg_color / max(self._avg_color_count, 1.0)).astype(np.uint8)
        self._avg_color = None
        self._avg_color_count = 0.0
        self._capture_until = 0.0
        return avg

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
        if self.state == S.PAUSE:
            return
        # Timed capture completions.
        if self._capture_until and now >= self._capture_until:
            label = self._capture_label
            try:
                self._on_capture_done()
            except Exception:
                self._capture_until = 0.0
                if label == "obstacles":
                    self._set_state(S.CAL_OBSTACLES)
                elif label == "cup":
                    self._finish_cup_search()

        # Turn-change / hole-out auto-transition.
        if self._transition_next is not None and now >= self._transition_until:
            nxt = self._transition_next
            self._transition_next = None
            if nxt == "__advance_turn__":
                self._advance_turn()
            elif nxt in SCREEN_BY_STATE:
                self._set_state(nxt)

    # ===================================================================== #
    # State transitions (explicit)
    # ===================================================================== #
    def _set_state(self, state: str) -> None:
        prev = self.state
        self.state = state
        if state in CAL_STATES and prev not in CAL_STATES:
            self._unlock_capture()
        self._on_enter(state)

    def _lock_capture(self) -> None:
        if self.backend is None:
            return
        exp = float(self.settings.get("camera", "exposure", default=-6) or -6)
        self.backend.lock_capture(exposure=exp)

    def _unlock_capture(self) -> None:
        if self.backend is None:
            return
        self.backend.unlock_capture()

    def _apply_saved_exposure(self) -> None:
        if self.backend is None:
            return
        exp = float(self.settings.get("camera", "exposure", default=-6) or -6)
        self.backend.set_exposure(exp)

    def _on_enter(self, state: str) -> None:
        if state == S.BOOT:
            self.events = EventLog()
        elif state == S.CAL_AREA:
            if not self.setup.play_area and not self._draw_poly:
                self._apply_preset("medium")
            elif not self._draw_poly:
                self._seed_area_from_setup()
        elif state == S.CAL_COURSE and not self.layout:
            self.layout = CourseLayout(course_by_id(self.current_course_id), self.setup.play_area)
        elif state in (S.CAL_PLACE, S.HOLE_START):
            self._capture_until = 0.0
            self._capture_label = ""
            self._init_place_ghosts()
            if self.layout is not None:
                s = self.layout.start
                self.setup.start = CircleZone(s["x"], s["y"], s.get("r", 0.15))
        elif state == S.CAL_CUP:
            self._capture_until = 0.0
            self._capture_label = ""
            self._dragging = None
            self._cup_manual = False
            self._start_cup_search()
        elif state == S.CAL_BALLS:
            self._last_ball_scan = 0.0
            if self.is_mock:
                self._ensure_mock_balls(max(2, len(self.players) or 2))
                self._derive_players_from_balls()
        elif state == S.PLAY:
            self._transition_next = None
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
            # Esc during a live hole pauses (INPUT.md). Game night Back
            # returns to ball assignment instead of opening pause.
            if st in PAUSE_ON_BACK:
                self._enter_pause()
            elif st == S.PAUSE and (self._recal_flyout or self._pause_focus == 3):
                self._pause_set_focus(0)
            else:
                self._on_back()
            return
        if action == "menu":
            if st in PLAYABLE_ROUND:
                self._enter_pause()
            return
        if action == "settings":
            self._open_settings()
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
            self._place_action(action, msg)
        elif st == S.CAL_OBSTACLES:
            if action == "prev":
                self._obstacles_action("undo_edit", msg)
            else:
                self._obstacles_action(action, msg)
        elif st == S.CAL_CUP:
            self._cup_action(action)
        elif st == S.CAL_BALLS:
            self._balls_action(action, msg)
        elif st == S.GAME_START:
            self._gamestart_action(action, msg)
        elif st == S.PLAY:
            self._play_action(action)
        elif st == S.HOLE_OUT:
            if action == "undo":
                self._transition_next = None
                self._undo_last_shot()
            elif action == "confirm":
                self._transition_next = None
                self._advance_turn()
        elif st == S.TURN_CHANGE:
            if action == "confirm":
                nxt = self._transition_next
                self._transition_next = None
                if nxt == "__advance_turn__":
                    self._advance_turn()
                elif nxt in SCREEN_BY_STATE:
                    self._set_state(nxt)
                else:
                    self._set_state(S.PLAY)
        elif st == S.OOB:
            if action == "confirm":
                self._advance_turn()
            elif action == "secondary":
                self._start_recal("balls")
        elif st == S.PAUSE:
            self._pause_action(action, msg)
        elif st == S.FIX_SCORE:
            self._fixscore_action(action)
        elif st == S.HOLE_COMPLETE:
            self._holecomplete_action(action)
        elif st == S.GAME_FINISH:
            self._finish_action(action)
        elif st == S.SETTINGS:
            self._settings_action(action, msg)
        elif st in (S.CREDITS, S.CHANGELOG):
            pass  # only "back" is meaningful here, handled via _on_back

    # ------------------------------------------------------------------ #
    def _on_back(self) -> None:
        st = self.state
        if self._recal_return and st in (
            S.CAL_FLOOR, S.CAL_AREA, S.CAL_COURSE, S.CAL_PLACE,
            S.CAL_OBSTACLES, S.CAL_CUP, S.CAL_BALLS, S.VERIFY,
        ):
            self._finish_recal()
            return
        if st in (S.CAL_FLOOR,):
            self._capture_until = 0.0
            self._set_state(S.SENSOR_CHECK)
        elif st == S.VERIFY:
            self._set_state(self._verify_return)
        elif st == S.CAL_AREA:
            self._set_state(S.CAL_FLOOR)
        elif st == S.CAL_COURSE:
            dest = self._course_return or S.CAL_AREA
            self._course_return = None
            if dest == S.CAL_AREA and not self.setup.play_area and self.mapper is None:
                dest = S.BOOT
            self._set_state(dest)
        elif st == S.CAL_PLACE:
            self._set_state(S.CAL_COURSE)
        elif st == S.CAL_OBSTACLES:
            self._set_state(S.CAL_PLACE)
        elif st == S.CAL_CUP:
            self._set_state(S.CAL_OBSTACLES)
        elif st == S.CAL_BALLS:
            self._set_state(S.CAL_CUP)
        elif st == S.GAME_START:
            self._set_state(S.CAL_BALLS)
        elif st == S.HOLE_COMPLETE:
            self._enter_pause()
        elif st == S.PAUSE:
            self._set_state(self._pause_resume_dest())
        elif st == S.FIX_SCORE:
            self._set_state(S.PAUSE)
        elif st == S.SENSOR_CHECK:
            self._set_state(S.BOOT)
        elif st in (S.SETTINGS, S.CREDITS, S.CHANGELOG):
            self._set_state(S.SETTINGS if st in (S.CREDITS, S.CHANGELOG) else self._settings_return)

    def _open_settings(self) -> None:
        """Open Settings from anywhere. Back returns to the screen you left."""
        if self.state == S.SETTINGS:
            return
        if self.state in (S.CREDITS, S.CHANGELOG):
            self._set_state(S.SETTINGS)
            return
        self._settings_return = self.state
        self._set_state(S.SETTINGS)

    # ===================================================================== #
    # Per-screen actions
    # ===================================================================== #
    def _boot_action(self, action: str) -> None:
        if action == "confirm":
            action = "new_game"
        if action == "new_game":
            self._course_return = None
            self._set_state(S.SENSOR_CHECK)
        elif action == "load":
            saved = Setup.load()
            if saved is not None:
                self.setup = saved
                self._apply_saved_setup()
                self._verify_return = S.BOOT
                self._set_state(S.VERIFY)
            else:
                self._set_state(S.SENSOR_CHECK)
        elif action == "library":
            self._course_return = S.BOOT
            self._set_state(S.CAL_COURSE)
        elif action == "settings":
            self._open_settings()

    def _sensor_action(self, action: str) -> None:
        opening = self.backend is None and self.sensor_status == "opening"
        if action == "load":
            if opening:
                return
            saved = Setup.load()
            if saved is not None:
                self.setup = saved
                self._apply_saved_setup()
                self._verify_return = S.SENSOR_CHECK
                self._set_state(S.VERIFY)
        elif action == "fresh" or action == "new_game":
            if self.backend is None:
                return
            self.setup = Setup()
            self._set_state(S.CAL_FLOOR)
        elif action == "retry":
            if opening:
                return
            self.rescan_cameras()
            self._reconfigure_camera()

    def _verify_action(self, action: str) -> None:
        if action == "confirm":
            if self._recal_return:
                self._finish_recal()
            else:
                self._begin_game_from_setup()
        elif action == "recalibrate":
            # Full wizard from Verify — do not jump back after the first step.
            self._recal_single = False
            if self._round_in_progress() and not self._recal_return:
                self._remember_round_resume()
                self._recal_return = self._resume_after_recal()
            self._set_state(S.CAL_FLOOR)

    def _floor_action(self, action: str) -> None:
        if action == "confirm":
            self._start_capture(2.0, "floor")
        elif action == "undo":
            self._set_state(S.SENSOR_CHECK)

    def _on_capture_done(self) -> None:
        if self.is_color_only:
            avg_color = self._finish_capture_color()
            if self._capture_label == "floor" and avg_color is not None:
                self.reference_color = avg_color
                self._set_state(S.CAL_AREA)
            elif self._capture_label == "obstacles":
                if avg_color is not None and self.mapper is not None:
                    self._propose_obstacles_color(avg_color)
                else:
                    self._set_state(S.CAL_OBSTACLES)
            elif self._capture_label == "cup":
                if avg_color is not None and self.mapper is not None:
                    self._propose_cup_color(avg_color)
                self._finish_cup_search()
            return
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
        elif self._capture_label == "obstacles":
            if avg is not None and self.mapper is not None:
                self._propose_obstacles(avg)
            else:
                self._set_state(S.CAL_OBSTACLES)
        elif self._capture_label == "cup":
            if avg is not None and self.mapper is not None:
                self._propose_cup(avg)
            self._finish_cup_search()

    def _area_action(self, action: str) -> None:
        if action == "preset":
            pass  # presets arrive via set
        elif action == "undo":
            if self._draw_poly:
                self._draw_poly.pop()
            self._dragging = None
        elif action == "clear":
            self._draw_poly = []
            self._dragging = None
        elif action == "confirm":
            if self.is_color_only:
                if len(self._draw_poly) == 4:
                    self._build_homography()
            else:
                if len(self._draw_poly) >= 3:
                    self.setup.play_area = [(float(x), float(y)) for x, y in self._draw_poly]
                if len(self.setup.play_area) < 3:
                    return
                if self._recal_return and self._recal_single:
                    self._refresh_layout_after_area()
                    self._finish_recal()
                else:
                    self._set_state(S.CAL_COURSE)

    def _apply_preset(self, name: str) -> None:
        sizes = {"small": (2.0, 1.5), "medium": (3.0, 2.0), "large": (4.0, 2.5)}
        w, h = sizes.get(name, (3.0, 2.0))
        self._preset_w, self._preset_h = w, h
        self._preset_name = name if name in sizes else "medium"
        self.setup.play_area = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        self.setup.start = CircleZone(0.0, 0.0, 0.15)
        # Color-only: the size is the assumed real-world size of the clicked
        # rectangle. Keep the corners. Depth: seed a floor-space rectangle.
        if not self.is_color_only:
            self._draw_poly = list(self.setup.play_area)
            self._dragging = None

    def _build_homography(self) -> None:
        """Solve the pixel->floor homography from the four clicked corners."""
        if len(self._draw_poly) != 4:
            return
        pixels = [(nx * self._feed_w, ny * self._feed_h) for nx, ny in self._draw_poly]
        H = homography_from_corners(pixels, self._preset_w, self._preset_h)
        if H is None:
            return
        w, h = self._preset_w, self._preset_h
        self.mapper = HomographyMapper(H)
        self.plane = None
        self.setup.play_area = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        self.setup.start = CircleZone(0.0, 0.0, 0.15)
        self.setup.camera = {"mode": "homography", "w": w, "h": h, "H": H.tolist()}
        self.setup.floor_plane = None
        self._refresh_layout_after_area()
        if self._recal_return and self._recal_single:
            self._finish_recal()
        else:
            self._set_state(S.CAL_COURSE)

    def _refresh_layout_after_area(self) -> None:
        """Keep start / hole / ghosts in the new play-area frame after a redraw."""
        if not self.setup.play_area:
            return
        course = None
        if self.layout is not None:
            course = self.layout.course
        if course is None:
            course = course_by_id(self.current_course_id)
        if course is None:
            return
        try:
            self.layout = CourseLayout(course, self.setup.play_area)
        except Exception:
            return
        s = self.layout.start
        self.setup.start = CircleZone(s["x"], s["y"], s.get("r", 0.15))

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
                if self._recal_return and self._recal_single:
                    self._refresh_layout_after_area()
                    self._finish_recal()
                elif self._course_return == S.BOOT and (self.mapper is None or not self.setup.play_area):
                    self._course_return = None
                    self._set_state(S.SENSOR_CHECK)
                else:
                    self._course_return = None
                    self._set_state(S.CAL_PLACE)
        elif action == "prev" or action == "next":
            courses = load_courses()
            cur = [c["id"] for c in courses].index(self.current_course_id) if self.current_course_id in [c["id"] for c in courses] else 0
            cur = (cur + (1 if action == "next" else -1)) % len(courses)
            self.current_course_id = courses[cur]["id"]
            self.layout = CourseLayout(courses[cur], self.setup.play_area)

    def _place_action(self, action: str, msg: dict | None = None) -> None:
        msg = msg or {}
        self._capture_until = 0.0
        self._capture_label = ""
        if action == "confirm":
            self._commit_place_ghosts()
        elif action == "prev":
            self._set_state(S.CAL_COURSE)
        elif action == "select":
            idx = int(msg.get("index", 0))
            if 0 <= idx < len(self._place_ghosts):
                self._selected_ghost = idx
        elif action == "undo":
            if self._selected_ghost is not None:
                self._rotate_ghost(self._selected_ghost)
        elif action == "secondary":
            if self._selected_ghost is not None:
                self._reset_ghost(self._selected_ghost)
        elif action == "next":
            self._cycle_ghost(1)
        elif action == "draw":
            self._add_custom_ghost()
        elif action == "delete":
            self._delete_ghost(self._selected_ghost)

    def _ghost_poly(self, g: dict) -> list[tuple[float, float]]:
        x, y, w, h = g["cx"], g["cy"], g["w"], g["h"]
        return [(x - w / 2, y - h / 2), (x + w / 2, y - h / 2),
                (x + w / 2, y + h / 2), (x - w / 2, y + h / 2)]

    def _init_place_ghosts(self) -> None:
        if self.layout is None:
            self._place_ghosts = []
            self._selected_ghost = None
            self._place_ghosts_course = None
            return
        cid = self.current_course_id
        if self._place_ghosts and self._place_ghosts_course == cid:
            return
        self._place_ghosts = []
        for g in self.layout.ghost_polygons():
            item = dict(g)
            item["home"] = (g["cx"], g["cy"], g["w"], g["h"])
            item["polygon"] = self._ghost_poly(item)
            self._place_ghosts.append(item)
        self._place_ghosts_course = cid
        self._selected_ghost = 0 if self._place_ghosts else None

    def _sync_ghost_bbox(self, g: dict) -> None:
        xs = [p[0] for p in g["polygon"]]
        ys = [p[1] for p in g["polygon"]]
        g["cx"] = (min(xs) + max(xs)) / 2.0
        g["cy"] = (min(ys) + max(ys)) / 2.0
        g["w"] = max(xs) - min(xs)
        g["h"] = max(ys) - min(ys)

    def _rotate_ghost(self, idx: int) -> None:
        if not (0 <= idx < len(self._place_ghosts)):
            return
        g = self._place_ghosts[idx]
        cx, cy = g["cx"], g["cy"]
        g["polygon"] = [(-(y - cy) + cx, (x - cx) + cy) for x, y in g["polygon"]]
        self._sync_ghost_bbox(g)

    def _reset_ghost(self, idx: int) -> None:
        if not (0 <= idx < len(self._place_ghosts)):
            return
        g = self._place_ghosts[idx]
        cx, cy, w, h = g["home"]
        g["cx"], g["cy"], g["w"], g["h"] = cx, cy, w, h
        g["polygon"] = self._ghost_poly(g)

    def _add_custom_ghost(self) -> None:
        if self.layout is None and not self.setup.play_area:
            return
        if self.setup.play_area:
            xs = [p[0] for p in self.setup.play_area]
            ys = [p[1] for p in self.setup.play_area]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        else:
            cx, cy = 0.0, 0.0
        w = h = 0.35
        n = len(self._place_ghosts) + 1
        item = {
            "id": f"custom{n}", "label": f"Object {n}", "item": "custom outline",
            "kind": "soft", "real_size_cm": [], "cx": cx, "cy": cy, "w": w, "h": h,
            "home": (cx, cy, w, h),
        }
        item["polygon"] = self._ghost_poly(item)
        self._place_ghosts.append(item)
        self._selected_ghost = len(self._place_ghosts) - 1

    def _delete_ghost(self, idx: int | None) -> None:
        if idx is None or not (0 <= idx < len(self._place_ghosts)):
            return
        self._place_ghosts.pop(idx)
        if not self._place_ghosts:
            self._selected_ghost = None
        else:
            self._selected_ghost = min(idx, len(self._place_ghosts) - 1)

    def _commit_place_ghosts(self) -> None:
        obstacles = []
        for i, g in enumerate(self._place_ghosts):
            poly = [(float(x), float(y)) for x, y in g["polygon"]]
            if len(poly) < 3:
                continue
            obstacles.append(Obstacle(
                id=str(g.get("id") or f"ob{i}"),
                label=str(g.get("label") or f"Object {i + 1}"),
                kind=str(g.get("kind") or "soft"),
                item=str(g.get("item") or ""),
                polygon=poly, state="confirmed", confidence=1.0,
            ))
        self.setup.obstacles = obstacles
        self._obstacles_done()

    def _cycle_ghost(self, delta: int) -> None:
        n = len(self._place_ghosts)
        if not n:
            self._selected_ghost = None
            return
        cur = self._selected_ghost if self._selected_ghost is not None else 0
        self._selected_ghost = (cur + delta) % n

    def _hit_ghost(self, nx: float, ny: float) -> int | None:
        # Prefer the smallest outline that contains the click so a book
        # sitting on a cushion is selectable instead of the larger shape.
        best_i, best_a = None, None
        for i, g in enumerate(self._place_ghosts):
            pts = self._floor_poly_norm(g["polygon"])
            if len(pts) < 3 or not point_in_polygon(nx, ny, [(p[0], p[1]) for p in pts]):
                continue
            area = 0.0
            for a, b in zip(pts, pts[1:] + pts[:1]):
                area += a[0] * b[1] - b[0] * a[1]
            area = abs(area)
            if best_a is None or area < best_a:
                best_i, best_a = i, area
        return best_i

    def _ghost_corner_feed(self, pt: tuple[float, float]) -> tuple[float, float]:
        if self.mapper is None:
            return 0.0, 0.0
        u, v = self.mapper.floor_to_pixel(pt[0], pt[1])
        return u / max(1, self._feed_w), v / max(1, self._feed_h)

    def _nearest_ghost_corner(self, gidx: int, nx: float, ny: float, max_d: float = 0.07) -> int | None:
        if not (0 <= gidx < len(self._place_ghosts)):
            return None
        best_i, best_d = None, max_d
        for i, pt in enumerate(self._place_ghosts[gidx]["polygon"]):
            cx, cy = self._ghost_corner_feed(pt)
            d = float(np.hypot(nx - cx, ny - cy))
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    def _nearest_any_ghost_corner(self, nx: float, ny: float, max_d: float = 0.07) -> tuple[int, int] | None:
        best: tuple[int, int] | None = None
        best_d = max_d
        for i, g in enumerate(self._place_ghosts):
            for ci, pt in enumerate(g["polygon"]):
                cx, cy = self._ghost_corner_feed(pt)
                d = float(np.hypot(nx - cx, ny - cy))
                if d < best_d:
                    best_d = d
                    best = (i, ci)
        return best

    def _parse_place_handle(self, handle: object) -> tuple[int | None, int | None]:
        if handle is None:
            return None, None
        if isinstance(handle, str) and ":" in handle:
            a, b = handle.split(":", 1)
            try:
                return int(a), int(b)
            except ValueError:
                return None, None
        try:
            return None, int(handle)
        except (TypeError, ValueError):
            return None, None

    def _set_ghost_corner(self, gidx: int, cidx: int, nx: float, ny: float) -> None:
        floor = self._feed_to_floor(nx, ny)
        if floor is None or not (0 <= gidx < len(self._place_ghosts)):
            return
        g = self._place_ghosts[gidx]
        poly = list(g["polygon"])
        if not (0 <= cidx < len(poly)):
            return
        poly[cidx] = floor
        g["polygon"] = poly
        self._sync_ghost_bbox(g)

    def _pointer_place(self, ptype: str, nx: float, ny: float, handle: object = None) -> None:
        if ptype in ("up", "cancel"):
            self._dragging = None
            self._drag_anchor = None
            return
        floor = self._feed_to_floor(nx, ny)
        if ptype == "down":
            gidx, cidx = self._parse_place_handle(handle)
            if gidx is None or cidx is None:
                hit = self._nearest_any_ghost_corner(nx, ny)
                if hit is not None:
                    gidx, cidx = hit
                elif cidx is not None and self._selected_ghost is not None:
                    gidx = self._selected_ghost
            if gidx is not None and cidx is not None:
                if not (0 <= gidx < len(self._place_ghosts)):
                    gidx, cidx = None, None
                elif not (0 <= cidx < len(self._place_ghosts[gidx]["polygon"])):
                    gidx, cidx = None, None
            if gidx is not None and cidx is not None:
                self._selected_ghost = gidx
                self._dragging = ("ghost_corner", gidx, cidx)
                self._set_ghost_corner(gidx, cidx, nx, ny)
                return
            hit = self._hit_ghost(nx, ny)
            if hit is None:
                return
            self._selected_ghost = hit
            self._dragging = ("ghost", hit)
            g = self._place_ghosts[hit]
            if floor is not None:
                self._drag_anchor = (floor[0] - g["cx"], floor[1] - g["cy"])
            return
        if ptype != "move":
            return
        if self._dragging and self._dragging[0] == "ghost_corner":
            _, gidx, cidx = self._dragging
            self._set_ghost_corner(int(gidx), int(cidx), nx, ny)
            return
        if self._dragging and self._dragging[0] == "ghost" and floor is not None:
            idx = int(self._dragging[1])
            if not (0 <= idx < len(self._place_ghosts)):
                return
            g = self._place_ghosts[idx]
            ax, ay = self._drag_anchor or (0.0, 0.0)
            dx, dy = floor[0] - ax - g["cx"], floor[1] - ay - g["cy"]
            g["cx"] += dx
            g["cy"] += dy
            g["polygon"] = [(x + dx, y + dy) for x, y in g["polygon"]]

    def _propose_obstacles(self, avg: np.ndarray) -> None:
        if self.plane is None or self.mapper is None:
            self._set_state(S.CAL_OBSTACLES)
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

    def _propose_obstacles_color(self, avg_color: np.ndarray) -> None:
        if self.mapper is None:
            self._set_state(S.CAL_OBSTACLES)
            return
        cup = (self.setup.hole.x, self.setup.hole.y, self.setup.hole.r) if self.setup.hole else None
        ref = self.reference_color
        if ref is not None and avg_color is not None and ref.shape != avg_color.shape:
            import cv2
            ref = cv2.resize(ref, (avg_color.shape[1], avg_color.shape[0]), interpolation=cv2.INTER_AREA)
        try:
            proposals = detect_obstacles_color(ref, avg_color, self.mapper,
                                               self.setup.play_area, cup)
        except Exception:
            proposals = []
        confirmed = [o for o in self.setup.obstacles if o.state == "confirmed"]
        new_obs: list[Obstacle] = list(confirmed)
        for p in proposals:
            label = self._match_template_label(p["polygon"]) or f"Object {len(new_obs) + 1}"
            new_obs.append(Obstacle(
                id=f"ob{len(new_obs)}", label=label, kind=p["kind"],
                polygon=p["polygon"], state="proposed", confidence=p["confidence"]))
        self.setup.obstacles = new_obs
        self._set_state(S.CAL_OBSTACLES)

    def _match_template_label(self, poly) -> str | None:
        ghosts = self._place_ghosts or (self.layout.ghost_polygons() if self.layout else [])
        if not ghosts:
            return None
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        for g in ghosts:
            if abs(cx - g["cx"]) < 0.3 and abs(cy - g["cy"]) < 0.3:
                return g["label"]
        return None

    def _obstacles_action(self, action: str, msg: dict | None = None) -> None:
        if action == "confirm":
            pending = [o for o in self.setup.obstacles if o.state in ("proposed", "drawing")]
            if not pending:
                self._obstacles_done()
        elif action == "confirm_one":
            if self._selected_obstacle is not None:
                self._push_obs_undo()
                o = self.setup.obstacles[self._selected_obstacle]
                if o.state in ("proposed", "drawing", "selected"):
                    o.state = "confirmed"
                self._selected_obstacle = None
        elif action == "confirm_all":
            flagged = [o for o in self.setup.obstacles if o.state == "proposed" and o.confidence < 0.7]
            if not flagged:
                self._push_obs_undo()
                for o in self.setup.obstacles:
                    if o.state == "proposed":
                        o.state = "confirmed"
                self._obstacles_done()
        elif action == "draw" or action == "secondary":
            self._push_obs_undo()
            if action == "secondary" and self._selected_obstacle is not None:
                self._toggle_obstacle_corner()
            else:
                self._start_drawing_obstacle()
        elif action == "redetect":
            self._push_obs_undo()
            self._start_capture(1.5, "obstacles")
        elif action == "delete" or action == "undo":
            # X deletes the selected object (undoable via LB).
            if self._selected_obstacle is not None:
                o = self.setup.obstacles[self._selected_obstacle]
                if o.state != "deleted":
                    self._push_obs_undo()
                    o.state = "deleted"
        elif action == "undo_edit":
            self._pop_obs_undo()
        elif action == "remove_corner":
            if self._selected_obstacle is not None:
                o = self.setup.obstacles[self._selected_obstacle]
                if o.state != "deleted" and len(o.polygon) > 3:
                    self._push_obs_undo()
                    self._remove_obstacle_corner()
        elif action == "select":
            idx = (msg or {}).get("index")
            if idx is not None and 0 <= int(idx) < len(self.setup.obstacles):
                self._selected_obstacle = int(idx)

    def _obstacles_done(self) -> None:
        # After the course/obstacles are confirmed: return from a mid-game
        # recal, resume play on a rebuild (holes 2+), or continue to the cup.
        if self._recal_return and self._recal_single:
            self._finish_recal()
        elif self._rebuilding and not self._recal_return:
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

    def _clone_obstacles(self) -> list[Obstacle]:
        return [copy.deepcopy(o) for o in self.setup.obstacles]

    def _push_obs_undo(self) -> None:
        self._obs_undo.append(self._clone_obstacles())

    def _pop_obs_undo(self) -> None:
        if not self._obs_undo:
            return
        self.setup.obstacles = self._obs_undo.pop()
        if self._selected_obstacle is not None and self._selected_obstacle >= len(self.setup.obstacles):
            self._selected_obstacle = len(self.setup.obstacles) - 1 if self.setup.obstacles else None

    def _toggle_obstacle_corner(self) -> None:
        # R: add a corner on the longest edge, or drop one if already dense.
        if self._selected_obstacle is None:
            return
        o = self.setup.obstacles[self._selected_obstacle]
        if o.state == "deleted":
            return
        poly = list(o.polygon)
        if len(poly) > 6:
            self._remove_obstacle_corner()
            return
        if len(poly) < 2:
            return
        best_i, best_len = 0, -1.0
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            length = float(np.hypot(b[0] - a[0], b[1] - a[1]))
            if length > best_len:
                best_len = length
                best_i = i
        a, b = poly[best_i], poly[(best_i + 1) % len(poly)]
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        o.polygon = poly[:best_i + 1] + [mid] + poly[best_i + 1:]

    def _remove_obstacle_corner(self) -> None:
        if self._selected_obstacle is None:
            return
        o = self.setup.obstacles[self._selected_obstacle]
        if o.state == "deleted" or len(o.polygon) <= 3:
            return
        if self._dragging and self._dragging[0] == "obs_corner":
            cidx = int(self._dragging[2])
            if 0 <= cidx < len(o.polygon) and len(o.polygon) > 3:
                poly = list(o.polygon)
                poly.pop(cidx)
                o.polygon = poly
                self._dragging = None
                return
        o.polygon = list(o.polygon)[:-1]

    def _start_cup_search(self) -> None:
        self.setup.hole = None
        self._draw_circle_center = None
        self._draw_circle_r = 0.0
        self._dragging = None
        self._cup_searching = True
        self._cup_confidence = 0.0
        self._start_capture(1.5, "cup")

    def _plant_layout_cup(self, confidence: float = 0.94) -> None:
        if self.layout is not None:
            self.setup.hole = CircleZone(self.layout.hole[0], self.layout.hole[1], 0.045)
        elif self.setup.play_area:
            xs = [p[0] for p in self.setup.play_area]
            ys = [p[1] for p in self.setup.play_area]
            self.setup.hole = CircleZone(sum(xs) / len(xs), sum(ys) / len(ys), 0.045)
        if self.setup.hole is not None:
            self._cup_confidence = float(confidence)

    def _finish_cup_search(self) -> None:
        self._cup_searching = False
        if self.setup.hole is None and self.is_mock:
            self._plant_layout_cup(0.94)

    def _ensure_cup(self) -> None:
        if self.setup.hole is not None:
            return
        self._plant_layout_cup(0.94)

    def _reset_cup(self) -> None:
        self._cup_manual = False
        self._start_cup_search()

    def _cup_action(self, action: str) -> None:
        if action == "confirm":
            self._capture_until = 0.0
            self._capture_label = ""
            self._cup_searching = False
            if self.setup.hole is None:
                return
            if self._recal_return and self._recal_single:
                self._finish_recal()
            else:
                self._set_state(S.CAL_BALLS)
        elif action == "redetect" or action == "undo":
            self._reset_cup()
        elif action == "draw" or action == "secondary":
            self._capture_until = 0.0
            self._capture_label = ""
            self._cup_searching = False
            self._cup_manual = True
            self._cup_confidence = 1.0
            self.setup.hole = None
            self._draw_circle_center = None
            self._draw_circle_r = 0.0
            self._dragging = None
        elif action == "grow":
            self._nudge_cup_r(0.008)
        elif action == "shrink":
            self._nudge_cup_r(-0.008)

    def _nudge_cup_r(self, delta: float) -> None:
        self._ensure_cup()
        if self.setup.hole is None:
            return
        r = float(np.clip(self.setup.hole.r + delta, 0.03, 0.18))
        self.setup.hole = CircleZone(self.setup.hole.x, self.setup.hole.y, r)
        self._draw_circle_r = r

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
        best_conf = 0.85
        target = self.layout.hole if self.layout else None
        for p in proposals:
            cx = sum(q[0] for q in p["polygon"]) / len(p["polygon"])
            cy = sum(q[1] for q in p["polygon"]) / len(p["polygon"])
            d = np.hypot(cx - target[0], cy - target[1]) if target else 0.0
            if d < best_d:
                best_d = d
                best = (cx, cy)
                best_conf = float(p.get("confidence", 0.85))
        if best is not None:
            self.setup.hole = CircleZone(best[0], best[1], 0.045)
            self._cup_confidence = best_conf

    def _propose_cup_color(self, avg_color: np.ndarray) -> None:
        # Color-only: the cup is a small new region vs. the empty-floor reference.
        if self.mapper is None or self.reference_color is None or avg_color is None:
            return
        proposals = detect_obstacles_color(self.reference_color, avg_color, self.mapper,
                                           self.setup.play_area, None,
                                           min_area_px=80)
        best = None
        best_d = 1e9
        best_conf = 0.85
        target = self.layout.hole if self.layout else None
        for p in proposals:
            cx = sum(q[0] for q in p["polygon"]) / max(1, len(p["polygon"]))
            cy = sum(q[1] for q in p["polygon"]) / max(1, len(p["polygon"]))
            d = np.hypot(cx - target[0], cy - target[1]) if target else 0.0
            if d < best_d:
                best_d = d
                best = (cx, cy)
                best_conf = float(p.get("confidence", 0.85))
        if best is not None:
            self.setup.hole = CircleZone(best[0], best[1], 0.045)
            self._cup_confidence = best_conf

    def _balls_action(self, action: str, msg: dict | None = None) -> None:
        msg = msg or {}
        if action == "confirm":
            if not self.players:
                return
            self._renumber_setup_players()
            self._derive_players_from_balls()
            self._lock_capture()
            if self._recal_return or self._round_in_progress():
                if not self._recal_return:
                    self._recal_return = self._resume_after_recal()
                self._finish_recal()
            else:
                self._set_state(S.GAME_START)
        elif action == "add_ball":
            if self.is_mock:
                self._ensure_mock_balls(min(6, len(self._mock_balls) + 1))
                self._derive_players_from_balls()
        elif action == "select":
            idx = int(msg.get("index", -1))
            if 0 <= idx < len(self.players):
                self._selected_setup_player = idx
        elif action == "delete":
            idx = int(msg.get("index", len(self.players) - 1))
            self._remove_setup_player(idx)
        elif action == "move_up":
            self._move_setup_player(int(msg.get("index", 0)), -1)
        elif action == "move_down":
            self._move_setup_player(int(msg.get("index", 0)), 1)
        elif action in ("next", "prev") and self.players:
            cur = self._selected_setup_player or 0
            step = 1 if action == "next" else -1
            self._selected_setup_player = (cur + step) % len(self.players)

    def _maybe_scan_setup_balls(self, now: float) -> None:
        if self.is_mock or self.mapper is None or self._frame_color is None:
            return
        if now - self._last_ball_scan < 0.4:
            return
        self._last_ball_scan = now
        cup = None
        if self.setup.hole is not None:
            cup = (self.setup.hole.x, self.setup.hole.y, self.setup.hole.r)
        for det in detect_setup_balls(
            self._frame_color, self.reference_color, self.mapper,
            self.setup.play_area, cup,
        ):
            self._offer_setup_ball(det)

    def _offer_setup_ball(self, det: dict, manual: bool = False) -> None:
        pos = det.get("pos")
        hue = det.get("hue_name")
        if pos is None or hue not in ("white", "orange", "yellow", "pink", "blue"):
            return
        if not manual and hue in self._dismissed_hues:
            return
        if manual:
            self._dismissed_hues.discard(hue)
        # One player per color — move the existing marker instead of minting another.
        for i, p in enumerate(self.players):
            if p.hue_name != hue:
                continue
            bid = self.ball_for_player.get(p.id, f"ball{i}")
            self._ball_positions[bid] = (float(pos[0]), float(pos[1]))
            if det.get("color"):
                p.color = det["color"]
                p.hue_range = tuple(det["hue_range"]) if det.get("hue_range") else None
            if "hue_center" in det:
                p.hue_center = det.get("hue_center")
            if det.get("sat_floor") is not None:
                p.sat_floor = int(det["sat_floor"])
            if det.get("val_floor") is not None:
                p.val_floor = int(det["val_floor"])
            self._selected_setup_player = i
            return
        if len(self.players) >= 6:
            return
        self._add_setup_player(pos, det)

    def _add_setup_player(self, pos: tuple[float, float], det: dict) -> None:
        used = {p.id for p in self.players}
        n = 0
        while f"p{n}" in used:
            n += 1
        pid, bid = f"p{n}", f"ball{n}"
        i = len(self.players)
        color = det.get("color") or PLAYER_COLORS[i % len(PLAYER_COLORS)]
        hue_name = det.get("hue_name") or HUE_NAMES.get(color, "custom")
        player = Player(
            id=pid, name=f"Player {i + 1}", color=color,
            hue_name=hue_name,
            hue_range=tuple(det["hue_range"]) if det.get("hue_range") else None,
            hue_center=det.get("hue_center"),
            sat_floor=det.get("sat_floor"),
            val_floor=det.get("val_floor"),
            order=i,
        )
        self.players.append(player)
        self.ball_for_player[pid] = bid
        self._ball_positions[bid] = (float(pos[0]), float(pos[1]))
        self._selected_setup_player = i
        if det.get("rejected"):
            self._rejected_balls.add(bid)

    def _remove_setup_player(self, idx: int) -> None:
        if not (0 <= idx < len(self.players)):
            return
        p = self.players.pop(idx)
        if p.hue_name:
            self._dismissed_hues.add(p.hue_name)
        bid = self.ball_for_player.pop(p.id, None)
        if bid:
            self._ball_positions.pop(bid, None)
            self._rejected_balls.discard(bid)
        self._renumber_setup_players()
        if not self.players:
            self._selected_setup_player = None
        else:
            self._selected_setup_player = min(idx, len(self.players) - 1)

    def _move_setup_player(self, idx: int, delta: int) -> None:
        j = idx + delta
        if not (0 <= idx < len(self.players) and 0 <= j < len(self.players)):
            return
        self.players[idx], self.players[j] = self.players[j], self.players[idx]
        self._renumber_setup_players()
        self._selected_setup_player = j

    def _renumber_setup_players(self) -> None:
        for i, p in enumerate(self.players):
            p.order = i

    def _pointer_balls(self, ptype: str, nx: float, ny: float) -> None:
        if ptype != "down" or self.mapper is None:
            return
        floor = self._feed_to_floor(nx, ny)
        if self._frame_color is None:
            return
        h, w = self._frame_color.shape[:2]
        ix = int(np.clip(nx * w, 0, w - 1))
        iy = int(np.clip(ny * h, 0, h - 1))
        # Clicking an already-added marker selects that player.
        if floor is not None:
            best_i, best_d = None, 0.12
            for i, p in enumerate(self.players):
                bid = self.ball_for_player.get(p.id, f"ball{i}")
                pos = self._ball_positions.get(bid)
                if pos is None:
                    continue
                d = float(np.hypot(floor[0] - pos[0], floor[1] - pos[1]))
                if d < best_d:
                    best_d, best_i = d, i
            if best_i is not None:
                self._selected_setup_player = best_i
                return
        cup = None
        if self.setup.hole is not None:
            cup = (self.setup.hole.x, self.setup.hole.y, self.setup.hole.r)
        dets = detect_setup_balls(
            self._frame_color, self.reference_color, self.mapper,
            self.setup.play_area, cup, loose=True,
        )
        hit = nearest_setup_ball(dets, self.mapper, float(ix), float(iy), max_px=64.0)
        if hit is not None:
            self._offer_setup_ball(hit, manual=True)
            return
        swatch = sample_ball_at_pixel(self._frame_color, float(ix), float(iy))
        if swatch is None:
            return
        if floor is None:
            return
        swatch["pos"] = floor
        self._offer_setup_ball(swatch, manual=True)

    def _gamestart_action(self, action: str, msg: dict) -> None:
        if action == "confirm":
            self._begin_game()

    def _play_action(self, action: str) -> None:
        if action == "undo":
            self._undo_last_shot()
        elif action in ("confirm", "ready"):
            if not self._shot_armed:
                self._confirm_tee()
        elif action == "secondary":
            self._start_recal("balls")

    def _round_in_progress(self) -> bool:
        """True once Game night has started (scores exist). Setup confirm
        must not jump to the tee-off screen mid-round."""
        return bool(self.player_scores)

    def _remember_round_resume(self) -> None:
        if self.state in PLAYABLE_ROUND:
            self._prev_state_for_pause = self.state

    def _resume_after_recal(self) -> str:
        raw = self._recal_return or self._prev_state_for_pause
        dest = RESUME_FROM_RECAL.get(raw, raw)
        if dest in PLAYABLE_ROUND:
            return dest
        if self._round_in_progress():
            return S.PLAY
        return S.PAUSE

    def _end_round(self) -> None:
        self.player_scores = {}
        self.finished_hole = {}
        self.hole = 1
        self.active_index = 0
        self._recal_return = None
        self._recal_single = False
        self._recal_flyout = False
        self._prev_state_for_pause = S.BOOT
        self._settings_return = S.BOOT
        self._transition_next = None
        self._shot_armed = False

    def _enter_pause(self) -> None:
        if self.state == S.PAUSE:
            return
        self._remember_round_resume()
        self._pause_focus = 0
        self._recal_flyout = False
        self._set_state(S.PAUSE)

    def _pause_resume_dest(self) -> str:
        dest = RESUME_FROM_RECAL.get(self._prev_state_for_pause, self._prev_state_for_pause)
        if dest in PLAYABLE_ROUND:
            return dest
        if self._round_in_progress():
            return S.PLAY
        return S.BOOT

    def _pause_action(self, action: str, msg: dict) -> None:
        rows = ["resume", "undo", "fix", "recalibrate", "course", "settings", "quit"]
        if action == "resume":
            self._recal_flyout = False
            self._set_state(self._pause_resume_dest())
        elif action == "back":
            if self._recal_flyout or self._pause_focus == 3:
                self._pause_set_focus(0)
                return
            self._set_state(self._pause_resume_dest())
        elif action == "down":
            self._pause_set_focus((self._pause_focus + 1) % len(rows))
        elif action == "up":
            self._pause_set_focus((self._pause_focus - 1) % len(rows))
        elif action == "select":
            # A click/Enter on a pause row both focuses and activates it.
            # Recalibrate only opens the flyout — the six cards do the work.
            self._pause_set_focus(int(msg.get("index", 0)))
            row = rows[self._pause_focus]
            if row != "recalibrate":
                self._pause_activate(row)
        elif action == "confirm":
            row = rows[self._pause_focus]
            if row == "recalibrate":
                self._recal_flyout = True
                return
            self._pause_activate(row)
        elif action == "recalibrate":
            self._pause_set_focus(3)
        elif action in ("fix", "course", "music", "settings", "quit"):
            self._pause_activate(action)
        elif action and action.startswith("recal_"):
            self._start_recal(action[6:])
        elif action == "undo":
            self._set_state(self._pause_resume_dest())
            self._undo_last_shot()

    def _pause_activate(self, row: str) -> None:
        if row == "resume":
            self._set_state(self._pause_resume_dest())
        elif row == "undo":
            self._set_state(self._pause_resume_dest())
            self._undo_last_shot()
        elif row == "fix":
            self._set_state(S.FIX_SCORE)
        elif row == "recalibrate":
            self._recal_flyout = True
        elif row == "course":
            self._recal_return = self._resume_after_recal()
            self._recal_single = True
            self._set_state(S.CAL_COURSE)
        elif row in ("music", "settings"):
            self._open_settings()
        elif row == "quit":
            self._end_round()
            self._set_state(S.BOOT)

    def _pause_set_focus(self, index: int) -> None:
        self._pause_focus = int(index)
        self._recal_flyout = self._pause_focus == 3

    def _start_recal(self, kind: str) -> None:
        kind = (kind or "").strip().lower()
        targets = {"cup", "balls", "area", "obstacles", "floor", "verify"}
        if kind not in targets:
            return
        self._remember_round_resume()
        self._recal_return = None
        self._recal_return = self._resume_after_recal()
        self._recal_single = kind != "floor"
        self._recal_flyout = False
        if kind == "cup":
            self._set_state(S.CAL_CUP)
        elif kind == "balls":
            self._set_state(S.CAL_BALLS)
        elif kind == "area":
            self._seed_area_from_setup(force=True)
            self._set_state(S.CAL_AREA)
        elif kind == "obstacles":
            self._seed_place_ghosts_from_obstacles()
            self._set_state(S.CAL_PLACE)
        elif kind == "floor":
            self._set_state(S.CAL_FLOOR)
        else:
            self._verify_return = self._recal_return
            self._set_state(S.VERIFY)

    def _finish_recal(self) -> None:
        dest = self._resume_after_recal()
        if dest in CAL_STATES or dest in (S.BOOT, S.SENSOR_CHECK, S.SETTINGS):
            dest = S.PLAY if self._round_in_progress() else S.PAUSE
        self._recal_return = None
        self._recal_single = False
        self._recal_flyout = False
        self._pause_focus = 0
        self._refresh_layout_after_area()
        self._save_setup()
        self._seed_tracker_from_positions()
        self._sync_shot_arm()
        self._set_state(dest)

    def _seed_area_from_setup(self, force: bool = False) -> None:
        """Load the current play-area corners into the S05 editor."""
        if self._draw_poly and not force:
            return
        self._dragging = None
        if self.is_color_only:
            if self.mapper is not None and self.setup.play_area:
                pts = self._floor_poly_norm(self.setup.play_area)
                if len(pts) >= 3:
                    self._draw_poly = [(float(x), float(y)) for x, y in pts[:4]]
            return
        if self.setup.play_area:
            self._draw_poly = [(float(x), float(y)) for x, y in self.setup.play_area]

    def _seed_place_ghosts_from_obstacles(self) -> None:
        kept = [o for o in self.setup.obstacles if o.state != "deleted" and len(o.polygon) >= 3]
        if not kept:
            return
        ghosts = []
        for o in kept:
            poly = [(float(x), float(y)) for x, y in o.polygon]
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            ghosts.append({
                "id": o.id, "label": o.label, "kind": o.kind, "item": o.item,
                "cx": cx, "cy": cy, "w": max(w, 0.05), "h": max(h, 0.05),
                "home": (cx, cy, max(w, 0.05), max(h, 0.05)),
                "polygon": poly,
            })
        self._place_ghosts = ghosts
        self._place_ghosts_course = self.current_course_id
        self._selected_ghost = 0 if ghosts else None

    def _fixscore_action(self, action: str) -> None:
        if action == "confirm":
            self.events.append(EventType.MANUAL_ADJUST, self.players[self.active_index].id if self.players else "", self.hole)
            self._set_state(S.PAUSE)
        elif action == "back":
            self._set_state(S.PAUSE)

    def _settings_action(self, action: str, msg: dict) -> None:
        if action == "back":
            self._set_state(self._settings_return)
        elif action == "settings-tab":
            tabs = ["display", "rules", "players", "camera", "about"]
            idx = int(msg.get("index", 0))
            self._settings_tab = tabs[idx] if 0 <= idx < len(tabs) else "display"
        elif action in ("next", "prev"):
            tabs = ["display", "rules", "players", "camera", "about"]
            try:
                i = tabs.index(self._settings_tab)
            except ValueError:
                i = 0
            self._settings_tab = tabs[(i + (1 if action == "next" else -1)) % len(tabs)]
        elif action == "credits":
            self._set_state(S.CREDITS)
        elif action == "changelog":
            self._set_state(S.CHANGELOG)
        elif action == "refresh_cameras":
            self.rescan_cameras()
        elif action == "apply_camera":
            self._reconfigure_camera()
        elif action == "driver_settings":
            if self.backend is not None:
                try:
                    self.backend.open_driver_settings()
                except Exception:
                    pass

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
            self._course_return = None
            self._set_state(S.SENSOR_CHECK)
        elif action == "start":
            self._end_round()
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
            handle = msg.get("handle")
            self._pointer_area(ptype, nx, ny, handle)
        elif st in (S.CAL_PLACE, S.HOLE_START):
            self._pointer_place(ptype, nx, ny, msg.get("handle"))
        elif st == S.CAL_CUP:
            self._pointer_cup(ptype, nx, ny, msg.get("handle"))
        elif st == S.CAL_BALLS:
            self._pointer_balls(ptype, nx, ny)
        elif st == S.CAL_OBSTACLES:
            self._pointer_obstacles(ptype, nx, ny, msg.get("handle"), bool(msg.get("shift")))
        elif st in (S.PLAY, S.OOB, S.TURN_CHANGE, S.HOLE_OUT) and ptype == "down":
            self._pointer_play_click(nx, ny)
            if st == S.PLAY and self.is_mock and self._shot_armed:
                self._pointer_putt(nx, ny)

    def _ensure_mapper(self) -> bool:
        if self.mapper is not None:
            return True
        cam_cfg = self.setup.camera or {}
        if cam_cfg.get("mode") == "homography" and cam_cfg.get("H"):
            try:
                H = np.array(cam_cfg["H"], dtype=float).reshape(3, 3)
                self.mapper = HomographyMapper(H)
                self.plane = None
                return True
            except Exception:
                pass
        if self.is_color_only and len(self._draw_poly) == 4:
            self._build_homography()
        return self.mapper is not None

    def _feed_to_floor(self, nx: float, ny: float) -> tuple[float, float] | None:
        self._ensure_mapper()
        if self.mapper is None:
            return None
        px = nx * self._feed_w
        py = ny * self._feed_h
        # Prefer depth; fall back to ray/homography intersection.
        if self._frame_depth is not None and not self.is_color_only:
            x = int(np.clip(px, 0, self._feed_w - 1))
            y = int(np.clip(py, 0, self._feed_h - 1))
            z = float(self._frame_depth[y, x])
            if z > 200:
                return self.mapper.depth_pixel_to_floor(px, py, z)
        return self.mapper.pixel_to_floor(px, py)

    def _corner_feed_xy(self, pt: tuple[float, float]) -> tuple[float, float]:
        """Return a _draw_poly point in normalized feed coordinates."""
        if self.is_color_only:
            return float(pt[0]), float(pt[1])
        if self.mapper is None:
            return 0.0, 0.0
        u, v = self.mapper.floor_to_pixel(pt[0], pt[1])
        return u / max(1, self._feed_w), v / max(1, self._feed_h)

    def _nearest_area_corner(self, nx: float, ny: float, max_d: float = 0.2) -> int | None:
        best_i, best_d = None, max_d
        for i, pt in enumerate(self._draw_poly):
            cx, cy = self._corner_feed_xy(pt)
            d = float(np.hypot(nx - cx, ny - cy))
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    def _set_area_corner(self, idx: int, nx: float, ny: float) -> None:
        if not (0 <= idx < len(self._draw_poly)):
            return
        if self.is_color_only:
            self._draw_poly[idx] = (nx, ny)
            return
        f = self._feed_to_floor(nx, ny)
        if f is not None:
            self._draw_poly[idx] = f

    def _pointer_area(self, ptype: str, nx: float, ny: float, handle: object = None) -> None:
        if ptype == "up" or ptype == "cancel":
            self._dragging = None
            return
        if ptype == "down":
            hit = None
            if handle is not None:
                try:
                    hi = int(handle)
                except (TypeError, ValueError):
                    hi = -1
                if 0 <= hi < len(self._draw_poly):
                    hit = hi
            if hit is None:
                hit = self._nearest_area_corner(nx, ny)
            if hit is not None:
                self._dragging = ("area", hit)
                self._set_area_corner(hit, nx, ny)
                return
            if len(self._draw_poly) >= 4:
                return
            if self.is_color_only:
                self._draw_poly.append((nx, ny))
            else:
                f = self._feed_to_floor(nx, ny)
                if f is None:
                    return
                self._draw_poly.append(f)
            self._dragging = ("area", len(self._draw_poly) - 1)
            return
        if ptype == "move":
            idx = None
            if handle is not None:
                try:
                    idx = int(handle)
                except (TypeError, ValueError):
                    idx = None
            if idx is None and self._dragging and self._dragging[0] == "area":
                idx = int(self._dragging[1])
            if idx is None:
                return
            self._dragging = ("area", idx)
            self._set_area_corner(idx, nx, ny)

    def _pointer_cup(self, ptype: str, nx: float, ny: float, handle: object = None) -> None:
        if ptype in ("up", "cancel"):
            if self._draw_circle_center is not None and self._draw_circle_r > 0.02:
                self.setup.hole = CircleZone(self._draw_circle_center[0], self._draw_circle_center[1], self._draw_circle_r)
            self._dragging = None
            self._draw_circle_center = None
            return
        f = self._feed_to_floor(nx, ny)
        if f is None:
            return
        if ptype == "down":
            if self.setup.hole is None:
                self.setup.hole = CircleZone(f[0], f[1], 0.045)
                self._dragging = ("circle", "center")
                return
            u, v = self.mapper.floor_to_pixel(self.setup.hole.x, self.setup.hole.y) if self.mapper else (0, 0)
            cx = u / max(1, self._feed_w)
            cy = v / max(1, self._feed_h)
            ru = 0.03
            if self.mapper is not None:
                ru = self.mapper.radius_to_pixels(self.setup.hole.x, self.setup.hole.y, self.setup.hole.r) / max(1, self._feed_w)
            handle_hit = handle is not None or float(np.hypot(nx - (cx + ru), ny - cy)) < 0.05
            if handle_hit:
                self._dragging = ("circle", "radius")
                self._draw_circle_center = (self.setup.hole.x, self.setup.hole.y)
                self._draw_circle_r = max(0.03, float(np.hypot(f[0] - self.setup.hole.x, f[1] - self.setup.hole.y)))
                return
            if float(np.hypot(nx - cx, ny - cy)) < max(ru + 0.03, 0.06):
                self._dragging = ("circle", "center")
                return
            self.setup.hole = CircleZone(f[0], f[1], self.setup.hole.r)
            self._dragging = ("circle", "center")
            return
        if ptype == "move" and self._dragging and self._dragging[0] == "circle":
            mode = self._dragging[1]
            if mode == "center":
                r = self.setup.hole.r if self.setup.hole else 0.045
                self.setup.hole = CircleZone(f[0], f[1], r)
            elif mode == "radius":
                c = self._draw_circle_center
                if c is None and self.setup.hole is not None:
                    c = (self.setup.hole.x, self.setup.hole.y)
                if c is None:
                    return
                self._draw_circle_r = max(0.03, float(np.hypot(f[0] - c[0], f[1] - c[1])))
                self.setup.hole = CircleZone(c[0], c[1], self._draw_circle_r)

    def _pointer_obstacles(self, ptype: str, nx: float, ny: float,
                           handle: object = None, shift: bool = False) -> None:
        f = self._feed_to_floor(nx, ny)
        if ptype in ("up", "cancel"):
            self._dragging = None
            return
        if f is None:
            return
        self._obs_drag_shift = bool(shift)
        if ptype == "down":
            parsed = self._parse_obs_handle(handle)
            if parsed is None:
                parsed = self._nearest_obs_corner(nx, ny)
            if parsed is not None:
                oidx, cidx = parsed
                self._push_obs_undo()
                self._selected_obstacle = oidx
                self._dragging = ("obs_corner", oidx, cidx, f)
                self._set_obs_corner(oidx, cidx, f, shift)
                return
            if self._selected_obstacle is not None:
                edge = self._hit_obs_edge(self._selected_obstacle, f)
                if edge is not None:
                    self._push_obs_undo()
                    o = self.setup.obstacles[self._selected_obstacle]
                    poly = list(o.polygon)
                    poly.insert(edge + 1, f)
                    o.polygon = poly
                    self._dragging = ("obs_corner", self._selected_obstacle, edge + 1, f)
                    return
            idx = self._obstacle_at(f)
            if idx is not None:
                self._selected_obstacle = idx
            return
        if ptype != "move":
            return
        if self._dragging and self._dragging[0] == "obs_corner":
            _, oidx, cidx, origin = self._dragging
            self._set_obs_corner(int(oidx), int(cidx), f, shift, origin)

    def _parse_obs_handle(self, handle: object) -> tuple[int, int] | None:
        if handle is None:
            return None
        if isinstance(handle, str) and handle.startswith("o:"):
            parts = handle.split(":")
            if len(parts) == 3:
                try:
                    return int(parts[1]), int(parts[2])
                except ValueError:
                    return None
        return None

    def _nearest_obs_corner(self, nx: float, ny: float) -> tuple[int, int] | None:
        if self.mapper is None:
            return None
        best = None
        best_d = 0.03
        for i, o in enumerate(self.setup.obstacles):
            if o.state == "deleted":
                continue
            for ci, pt in enumerate(o.polygon):
                u, v = self.mapper.floor_to_pixel(*pt)
                d = float(np.hypot(nx - u / self._feed_w, ny - v / self._feed_h))
                if d < best_d:
                    best_d = d
                    best = (i, ci)
        return best

    def _hit_obs_edge(self, oidx: int, f: tuple[float, float]) -> int | None:
        if not (0 <= oidx < len(self.setup.obstacles)):
            return None
        poly = self.setup.obstacles[oidx].polygon
        best_i, best_d = None, 0.04
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            d = self._dist_point_seg(f, a, b)
            if d < best_d:
                best_d = d
                best_i = i
        return best_i

    @staticmethod
    def _dist_point_seg(p, a, b) -> float:
        ax, ay = a
        bx, by = b
        vx, vy = bx - ax, by - ay
        ll = vx * vx + vy * vy
        if ll <= 1e-12:
            return float(np.hypot(p[0] - ax, p[1] - ay))
        t = max(0.0, min(1.0, ((p[0] - ax) * vx + (p[1] - ay) * vy) / ll))
        return float(np.hypot(p[0] - (ax + t * vx), p[1] - (ay + t * vy)))

    def _set_obs_corner(self, oidx: int, cidx: int, f: tuple[float, float],
                        shift: bool = False, origin: tuple[float, float] | None = None) -> None:
        if not (0 <= oidx < len(self.setup.obstacles)):
            return
        o = self.setup.obstacles[oidx]
        if not (0 <= cidx < len(o.polygon)):
            return
        pt = f
        if shift and origin is not None:
            step = 0.01
            dx, dy = f[0] - origin[0], f[1] - origin[1]
            pt = (origin[0] + round(dx / step) * step, origin[1] + round(dy / step) * step)
        elif shift:
            pt = (round(f[0] * 100) / 100, round(f[1] * 100) / 100)
        poly = list(o.polygon)
        poly[cidx] = pt
        o.polygon = poly

    def _obstacle_at(self, f) -> int | None:
        for i, o in enumerate(self.setup.obstacles):
            if o.state in ("deleted",):
                continue
            if point_in_polygon(f[0], f[1], o.polygon):
                return i
        return None

    def _pointer_place_tee(self, nx: float, ny: float) -> None:
        """Click the live feed to tell the game where the teeing ball is."""
        self._ensure_mapper()
        bid = self._active_ball_id()
        if bid is None:
            return
        floor = self.tracker.find_near_pixel(
            bid, self._frame_color, self.mapper, nx, ny, self.setup.play_area,
        )
        if floor is None:
            floor = self._feed_to_floor(nx, ny)
        if floor is None and self.setup.start is not None:
            floor = (self.setup.start.x, self.setup.start.y)
        if floor is None:
            return
        self._tee_click = (float(nx), float(ny), time.time() + 1.6)
        self._place_tee_at(bid, (float(floor[0]), float(floor[1])), arm=not self._shot_armed)

    def _retrack_ball(self, bid: str, pos: tuple[float, float]) -> None:
        self._ball_positions[bid] = (float(pos[0]), float(pos[1]))
        self.tracker.seed_position(bid, (float(pos[0]), float(pos[1])), hold=False)
        self._lost_since.pop(bid, None)

    def _pointer_play_click(self, nx: float, ny: float) -> None:
        """Click the camera to (re)track a ball. Never opens pause."""
        self._ensure_mapper()
        floor = self._feed_to_floor(nx, ny)
        best_pid, best_d = None, 0.2
        if floor is not None:
            for p in self.players:
                bid = self.ball_for_player.get(p.id)
                pos = None
                if bid:
                    tb = self.tracker.balls.get(bid)
                    if tb is not None and getattr(tb, "smoothed", None) is not None:
                        pos = tb.smoothed
                    else:
                        pos = self._ball_positions.get(bid)
                if pos is None:
                    continue
                d = float(np.hypot(floor[0] - pos[0], floor[1] - pos[1]))
                if d < best_d:
                    best_d, best_pid = d, p.id
        if best_pid is not None and floor is not None:
            bid = self.ball_for_player.get(best_pid)
            if bid:
                self._retrack_ball(bid, floor)
                active = self._active_player()
                if active is not None and active.id == best_pid and not self._shot_armed:
                    self._place_tee_at(bid, floor, arm=False)
            return
        if self._frame_color is not None and self.mapper is not None:
            h, w = self._frame_color.shape[:2]
            ix = int(np.clip(nx * w, 0, w - 1))
            iy = int(np.clip(ny * h, 0, h - 1))
            cup = None
            if self.setup.hole is not None:
                cup = (self.setup.hole.x, self.setup.hole.y, self.setup.hole.r)
            dets = detect_setup_balls(
                self._frame_color, self.reference_color, self.mapper,
                self.setup.play_area, cup, loose=True,
            )
            hit = nearest_setup_ball(dets, self.mapper, float(ix), float(iy), max_px=72.0)
            if hit is not None:
                self._retrack_from_detection(hit, floor)
                return
        self._pointer_place_tee(nx, ny)

    def _retrack_from_detection(self, det: dict, floor: tuple[float, float] | None) -> None:
        pos = det.get("pos") or floor
        if pos is None:
            return
        hue = det.get("hue_name")
        target = None
        if hue:
            target = next((p for p in self.players if p.hue_name == hue), None)
        if target is None:
            lost = self._lost_ball_snapshot()
            if len(lost) == 1:
                target = next((p for p in self.players if p.id == lost[0]["id"]), None)
        if target is None:
            target = self._active_player()
        if target is None:
            return
        bid = self.ball_for_player.get(target.id)
        if not bid:
            return
        if det.get("color"):
            target.color = det["color"]
        if hue:
            target.hue_name = hue
        if det.get("hue_range"):
            target.hue_range = tuple(det["hue_range"])
        self._retrack_ball(bid, (float(pos[0]), float(pos[1])))
        active = self._active_player()
        if active is not None and active.id == target.id and not self._shot_armed:
            self._place_tee_at(bid, (float(pos[0]), float(pos[1])), arm=False)

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
        val = str(msg.get("value", "")).strip()[:40]
        if key == "player_name" and self.state == S.CAL_BALLS:
            try:
                idx = int(msg.get("index", 0))
            except (TypeError, ValueError):
                return
            if 0 <= idx < len(self.players):
                self.players[idx].name = val
        elif key == "setup_name":
            self.setup.name = val

    def _handle_set(self, msg: dict) -> None:
        try:
            self._apply_set(msg)
        except (TypeError, ValueError):
            return

    def _apply_set(self, msg: dict) -> None:
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
        elif key == "settings.rules.holes":
            self.holes = max(1, min(9, int(val)))
            self.settings.set(self.holes, "rules", "holes")
            self.settings.save()
        elif key == "settings.rules.strokeCap":
            self.stroke_cap = max(3, min(12, int(val)))
            self.settings.set(self.stroke_cap, "rules", "strokeCap")
            self.settings.save()
        elif key == "settings.rules.oobPenalty":
            self.settings.set(bool(val), "rules", "oobPenalty")
            self.settings.save()
        elif key == "settings.rules.tunnelBonus":
            self.settings.set(bool(val), "rules", "tunnelBonus")
            self.settings.save()
        elif key == "settings.camera.device":
            self.settings.set(int(val), "camera", "device")
            self.settings.save()
            self._reconfigure_camera()
        elif key == "settings.camera.resolution":
            self.settings.set(str(val), "camera", "resolution")
            self.settings.save()
            self._reconfigure_camera()
        elif key == "settings.camera.backend":
            self.settings.set(str(val), "camera", "backend")
            self.settings.save()
            self._reconfigure_camera()
        elif key == "settings.camera.exposure":
            exp = max(-8.0, min(-4.0, float(val)))
            self.settings.set(exp, "camera", "exposure")
            self.settings.save()
            if self.backend is not None:
                self.backend.set_exposure(exp)
        elif key == "settings.display.debugOverlay":
            self.settings.set(bool(val), "display", "debugOverlay")
            self.settings.save()
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
        cam_cfg = self.setup.camera
        if cam_cfg and cam_cfg.get("mode") == "homography" and cam_cfg.get("H"):
            H = np.array(cam_cfg["H"], dtype=float).reshape(3, 3)
            self.mapper = HomographyMapper(H)
            self.plane = None
        elif self.setup.floor_plane is not None:
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
        self._lock_capture()
        self.course_ids = [course_for_hole(h) for h in range(1, self.holes + 1)]
        self.course_pars = [(course_by_id(c) or {}).get("par", 3) for c in self.course_ids]
        self.player_scores = {p.id: [None] * self.holes for p in self.players}
        self.hole = 1
        self.active_index = 0
        self.events.clear()
        try:
            self._save_setup()
        except Exception:
            pass
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
            if p.hue_center is None and p.hue_range is None and p.hue_name not in ("white", "black"):
                p.hue_range = self._hue_range_for_color(p.color)
            self.tracker.add_ball(
                ball_id or p.id, p.id, p.color, p.hue_range,
                hue_center=p.hue_center, sat_floor=p.sat_floor, val_floor=p.val_floor,
            )
        self._seed_tracker_from_positions()

    def _hue_range_for_color(self, hex_color: str) -> tuple[int, int] | None:
        import cv2
        bgr = np.uint8([[hex_to_bgr(hex_color)]])
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[0][0]
        h, s, v = int(hsv[0]), int(hsv[1]), int(hsv[2])
        if s < 50:
            return None
        lo = max(0, h - 15)
        hi = min(179, h + 15)
        return (lo, hi)

    def _save_setup(self) -> None:
        self.setup.players = list(self.players)
        self.setup.courses = list(self.course_ids)
        self.setup.name = (self.setup.name or "").strip() or "Living room"
        self.setup.save()

    def _enter_hole(self) -> None:
        if self.hole - 1 < len(self.course_ids):
            self.current_course_id = self.course_ids[self.hole - 1]
        course = course_by_id(self.current_course_id) or load_courses()[0]
        self.current_course_id = course["id"]
        area = self.setup.play_area or [(-1.5, -1.0), (1.5, -1.0), (1.5, 1.0), (-1.5, 1.0)]
        self.layout = CourseLayout(course, area)
        self.finished_hole = {p.id: False for p in self.players}
        # First unfinished player to lead.
        self.active_index = 0
        self._rebuilding = self.hole > 1
        self._putt = None
        self._ball_was_moving = {}
        self._last_stroke_t = {}
        s = self.layout.start
        self.setup.start = CircleZone(s["x"], s["y"], s.get("r", 0.15))
        # Mock: move the physical cup to the new course's hole.
        if self._rebuilding and self.is_mock and self.layout is not None:
            self.setup.hole = CircleZone(self.layout.hole[0], self.layout.hole[1], 0.045)
        # Keep the last seen webcam positions. Only the mock tees off for you.
        for i, p in enumerate(self.players):
            ball_id = self.ball_for_player.get(p.id, f"ball{i}")
            if self.is_mock:
                sx = s["x"] + 0.15 * i
                sy = s["y"]
                self._ball_positions[ball_id] = (sx, sy)
                raw = ball_id.replace("ball", "")
                if raw.isdigit():
                    idx = int(raw)
                    if 0 <= idx < len(self._mock_balls):
                        self._mock_balls[idx]["x"] = sx
                        self._mock_balls[idx]["y"] = sy
            pos = self._ball_positions.get(ball_id)
            if pos is not None:
                self.tracker.seed_position(ball_id, pos)
        self._sync_shot_arm()
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
        if self.state not in (S.PLAY, S.GAME_START, S.TURN_CHANGE, S.HOLE_OUT, S.OOB):
            return
        p = self._active_player()
        if p is None:
            return
        ball_id = self._active_ball_id()
        tb = self.tracker.balls.get(ball_id) if ball_id else None
        if tb is None:
            return
        if tb.smoothed is not None:
            self._ball_positions[ball_id] = tb.smoothed
            self._track_in_bounds(ball_id, tb.smoothed)
        if self.state != S.PLAY:
            return
        if not self._shot_armed:
            pos = tb.smoothed if tb.smoothed is not None else self._ball_positions.get(ball_id)
            if pos is not None and not tb.moving and self._in_start_zone(pos):
                if self._tee_seen_since <= 0:
                    self._tee_seen_since = now
                elif now - self._tee_seen_since >= 0.5:
                    self._place_tee_at(ball_id, pos, arm=True)
            else:
                self._tee_seen_since = 0.0
            self._ball_was_moving[ball_id] = False
            return
        was_moving = self._ball_was_moving.get(ball_id, False)
        # Stroke on stopped -> moving. Ignore a restart that is still the same putt.
        if tb.moving and not was_moving:
            last = self._last_stroke_t.get(ball_id, 0.0)
            if now - last >= 0.60:
                self._record_stroke(p.id)
                self._last_stroke_t[ball_id] = now
        self._ball_was_moving[ball_id] = tb.moving
        # A coasted prediction is not a real rest — only a detection at rest ends the stroke.
        if not tb.moving and was_moving and not getattr(tb, "coasting", False):
            self._resolve_after_stop(p, tb, now)

    def _track_in_bounds(self, ball_id: str, pos: tuple[float, float]) -> None:
        if not self.setup.play_area:
            return
        if point_in_polygon(pos[0], pos[1], self.setup.play_area):
            self._last_in_bounds[ball_id] = (float(pos[0]), float(pos[1]))

    def _in_start_zone(self, pos: tuple[float, float] | None) -> bool:
        if pos is None or self.setup.start is None:
            return False
        s = self.setup.start
        return float(np.hypot(pos[0] - s.x, pos[1] - s.y)) <= (s.r + 0.22)

    def _sync_shot_arm(self) -> None:
        p = self._active_player()
        if p is None:
            self._shot_armed = False
            self._tee_seen_since = 0.0
            return
        # Later strokes already lie on the course — no tee required.
        self._shot_armed = self._current_strokes(p.id) > 0
        self._tee_seen_since = 0.0

    def _seed_tracker_from_positions(self) -> None:
        """Keep click / last-seen floor points after the tracker is rebuilt."""
        for p in self.players:
            bid = self.ball_for_player.get(p.id)
            pos = self._ball_positions.get(bid) if bid else None
            if bid and pos:
                self.tracker.seed_position(bid, pos, hold=False)

    def _place_tee_at(self, bid: str, pos: tuple[float, float], arm: bool) -> None:
        self._ball_positions[bid] = (float(pos[0]), float(pos[1]))
        self.tracker.seed_position(bid, (float(pos[0]), float(pos[1])), hold=arm)
        if arm:
            self._arm_shot()

    def _confirm_tee(self) -> None:
        """Player says the ball is ready — pin it and start the hole."""
        bid = self._active_ball_id()
        pos = self._ball_positions.get(bid) if bid else None
        if pos is None and bid:
            tb = self.tracker.balls.get(bid)
            if tb is not None and tb.smoothed is not None:
                pos = tb.smoothed
        if pos is None and self.setup.start is not None:
            pos = (self.setup.start.x, self.setup.start.y)
        if bid and pos is not None:
            self._place_tee_at(bid, pos, arm=True)
        else:
            self._arm_shot()

    def _arm_shot(self) -> None:
        self._shot_armed = True
        self._tee_seen_since = 0.0
        bid = self._active_ball_id()
        if bid:
            self._ball_was_moving[bid] = False
            self._last_stroke_t[bid] = 0.0

    def _current_strokes(self, pid: str) -> int:
        sc = self.player_scores.get(pid, [])
        return sc[self.hole - 1] if self.hole - 1 < len(sc) and sc[self.hole - 1] is not None else 0

    def _set_strokes(self, pid: str, n: int) -> None:
        if pid in self.player_scores:
            self.player_scores[pid][self.hole - 1] = n

    def _player_name(self, pid: str) -> str:
        for p in self.players:
            if p.id == pid:
                return p.name
        return "Player"

    def _record_stroke(self, pid: str) -> None:
        self._set_strokes(pid, self._current_strokes(pid) + 1)
        n = self._current_strokes(pid)
        name = self._player_name(pid)
        self.events.append(EventType.STROKE, pid, self.hole, text=f"{name} putted — stroke {n}")

    def _resolve_after_stop(self, p: Player, tb, now: float) -> None:
        pos = tb.smoothed
        if pos is None:
            return
        # Holed?
        if self.setup.hole is not None:
            hx, hy, hr = self.setup.hole.x, self.setup.hole.y, self.setup.hole.r
            if np.hypot(pos[0] - hx, pos[1] - hy) < (hr + 0.04):
                self._finish_player_hole(p.id, EventType.HOLE_OUT)
                return
        # Stroke cap ends the hole for this player.
        if self._current_strokes(p.id) >= self.stroke_cap:
            self._finish_player_hole(p.id, EventType.CAP)
            return
        # Out of bounds?
        if self.setup.play_area and not point_in_polygon(pos[0], pos[1], self.setup.play_area):
            if self.settings.get("rules", "oobPenalty", default=True):
                self._set_strokes(p.id, self._current_strokes(p.id) + 1)  # penalty
            self.events.append(EventType.OOB, p.id, self.hole,
                               text=f"{p.name} out of bounds — +1 penalty")
            bid = self.ball_for_player.get(p.id)
            self._oob_ball = pos
            self._oob_exit = self._last_in_bounds.get(bid or "", pos)
            self._set_state(S.OOB)
            return
        # In play: advance turn.
        self._advance_turn()

    def _hole_is_complete(self) -> bool:
        return bool(self.players) and all(
            self.finished_hole.get(p.id, False) for p in self.players
        )

    def _finish_player_hole(self, pid: str, kind: EventType) -> None:
        self.finished_hole[pid] = True
        name = self._player_name(pid)
        n = self._current_strokes(pid)
        if kind == EventType.HOLE_OUT:
            self.events.append(kind, pid, self.hole, text=f"{name} holed in {n}")
            self._capture_finish_still()
            self._set_state(S.HOLE_OUT)
            self._transition_next = S.HOLE_COMPLETE if self._hole_is_complete() else "__advance_turn__"
            self._transition_until = time.time() + 2.5
            return
        if kind == EventType.CAP:
            self.events.append(kind, pid, self.hole, text=f"{name} reached the stroke cap")
        else:
            self.events.append(kind, pid, self.hole, text=f"{name} · {kind.value}")
        if self._hole_is_complete():
            self._transition_next = None
            self._set_state(S.HOLE_COMPLETE)
        else:
            self._advance_turn()

    def _advance_turn(self) -> None:
        prev = self._active_player()
        if prev is not None:
            self._turn_prev = prev.as_dict()
            self._turn_prev_stroke = self._current_strokes(prev.id)
        n = len(self.players)
        for _ in range(max(1, n)):
            if n:
                self.active_index = (self.active_index + 1) % n
            if n and not self.finished_hole.get(self.players[self.active_index].id, False):
                break
        if self._hole_is_complete():
            self._transition_next = None
            self._set_state(S.HOLE_COMPLETE)
            return
        self._set_state(S.TURN_CHANGE)
        self._transition_next = S.PLAY
        self._transition_until = time.time() + 1.4
        self._sync_shot_arm()

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
        self._sync_shot_arm()
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
        try:
            ui = self._ui_snapshot()
        except Exception:
            ui = {}
        try:
            sensor = (
                self.backend.description.as_dict() if self.backend is not None
                else self.sensor_desc
            )
        except Exception:
            sensor = self.sensor_desc
        return {
            "screen": (
                "S07c" if self.state == S.CAL_OBSTACLES and self._selected_obstacle is not None
                else SCREEN_BY_STATE.get(self.state, "S01")
            ),
            "state": self.state,
            "version": __version__,
            "input_mode": "keyboard",
            "sensor": sensor,
            "sensor_status": self.sensor_status,
            "settings": self.settings.data,
            "feed": {"enabled": self.settings.get("display", "showCameraFeed", default=True),
                     "w": self._feed_w, "h": self._feed_h},
            "setup": self._safe_part(self._setup_snapshot, {}),
            "game": self._safe_part(self._game_snapshot, {}),
            "overlay": self._safe_part(self._overlay_snapshot, {"shapes": []}),
            "ui": ui,
        }

    @staticmethod
    def _safe_part(fn, fallback):
        try:
            return fn()
        except Exception:
            return fallback

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
            "event_log": self._event_log_snapshot(),
            "transitioning": self._transition_next is not None,
        }

    def _event_log_snapshot(self) -> list[dict]:
        colors = {p.id: p.color for p in self.players}
        out = []
        for e in self.events.as_list():
            e = dict(e)
            e["color"] = colors.get(e.get("player_id"), "#f2efe8")
            out.append(e)
        return out

    def _ui_snapshot(self) -> dict:
        st = self.state
        if st == S.BOOT:
            meta, when = self._saved_setup_meta()
            return {"menu": [("New game", "A"), ("Load last setup", "A"), ("Course library", None), ("Settings", None)],
                    "saved_meta": meta, "saved_when": when}
        if st == S.SENSOR_CHECK:
            meta, when = self._saved_setup_meta()
            return {"sensor": self.sensor_desc, "saved_meta": meta, "saved_when": when,
                    "opening": self.backend is None and self.sensor_status == "opening",
                    "camera_error": self._camera_error}
        if st == S.VERIFY:
            return {}
        if st == S.CAL_FLOOR:
            return {"step": 1}
        if st == S.CAL_AREA:
            return {"step": 2, "presets": ["small", "medium", "large"],
                    "preset": self._preset_name,
                    "color_only": self.is_color_only,
                    "area_w": round(self._preset_w, 1),
                    "area_h": round(self._preset_h, 1),
                    "corners": len(self._draw_poly)}
        if st == S.CAL_COURSE:
            aw, ah = self._play_area_size()
            return {"courses": self._courses_snapshot(), "step": 3,
                    "area_w": aw, "area_h": ah,
                    "recalibrating": bool(self._recal_return)}
        if st in (S.CAL_PLACE, S.HOLE_START):
            return {"step": 3, "ghosts": self._ghosts_snapshot(),
                    "course": course_by_id(self.current_course_id),
                    "selected": self._selected_ghost}
        if st == S.CAL_OBSTACLES:
            return {"step": 3, "obstacles": [o.as_dict() for o in self.setup.obstacles],
                    "selected": self._selected_obstacle,
                    "undo_count": len(self._obs_undo)}
        if st == S.CAL_CUP:
            h = self.setup.hole
            return {"step": 4, "has_hole": h is not None,
                    "hole_r_cm": round((h.r if h else 0.045) * 100),
                    "hole_d_cm": round((h.r if h else 0.045) * 200),
                    "searching": bool(self._cup_searching),
                    "found": h is not None and not self._cup_searching,
                    "manual": bool(self._cup_manual),
                    "confidence": round(float(self._cup_confidence), 2)}
        if st == S.CAL_BALLS:
            clashes = hue_clash_pairs(self.players)
            warn = None
            if clashes:
                bits = [f"{a} and {b} ({d:.0f} hue apart)" for a, b, d in clashes]
                warn = ("These balls are too close in color: "
                        + "; ".join(bits)
                        + ". Use a different ball so tracking can tell them apart.")
            return {
                "step": 5,
                "balls": self._setup_ball_snapshot(),
                "players": [p.as_dict() for p in self.players],
                "rejected": len(self._rejected_balls),
                "color_only": self.is_color_only,
                "selected": self._selected_setup_player,
                "hue_clash": warn,
                "recalibrating": bool(self._recal_return),
            }
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
            return {"players": [p.as_dict() for p in self.players],
                    "undo": self._undo_card_snapshot()}
        if st == S.HOLE_COMPLETE:
            return self._holecomplete_snapshot()
        if st == S.GAME_FINISH:
            return self._finish_snapshot()
        if st == S.SETTINGS:
            return {"return": self._settings_return, "tab": self._settings_tab,
                    "camera": self._camera_snapshot()}
        if st == S.CREDITS:
            return {}
        if st == S.CHANGELOG:
            return {"changelog": self._changelog()}
        return {}

    def _play_area_size(self) -> tuple[float, float]:
        if self.setup.play_area:
            xs = [p[0] for p in self.setup.play_area]
            ys = [p[1] for p in self.setup.play_area]
            return round(max(xs) - min(xs), 1), round(max(ys) - min(ys), 1)
        return round(self._preset_w, 1), round(self._preset_h, 1)

    def _saved_setup_meta(self) -> tuple[str | None, str]:
        saved = Setup.load()
        if saved is None:
            return None, ""
        n_courses = len(saved.courses) or 3
        n_balls = len(saved.players)
        aw, ah = None, None
        if saved.play_area:
            xs = [p[0] for p in saved.play_area]
            ys = [p[1] for p in saved.play_area]
            aw, ah = round(max(xs) - min(xs), 1), round(max(ys) - min(ys), 1)
        parts = [saved.name]
        if aw is not None:
            parts.append(f"{aw} × {ah} m")
        parts.append(f"{n_courses} courses")
        if n_balls:
            parts.append(f"{n_balls} balls")
        line = " · ".join(parts)
        when = ""
        if saved.saved_at:
            dt = time.localtime(saved.saved_at)
            when = f"saved {time.strftime('%b', dt)} {dt.tm_mday}, {time.strftime('%H:%M', dt)}"
        return line, when

    def _camera_snapshot(self) -> dict:
        cfg = self.settings.get("camera", default={}) or {}
        status = {}
        if self.backend is not None:
            try:
                status = self.backend.capture_status() or {}
            except Exception:
                status = {}
        ignored = list(status.get("ignored") or [])
        exposure_ok = status.get("exposure_control", True)
        notice = ""
        if exposure_ok is False:
            notice = (
                "This camera controls its own exposure. Bright, even room light "
                "keeps tracking fast; dim rooms will slow it down."
            )
        elif ignored:
            notice = ("This driver ignored: "
                      + ", ".join(ignored)
                      + ". Exposure lock may not stick on this camera.")
        fps = status.get("measured_fps")
        if fps is None and self.backend is not None:
            fps = getattr(self.backend.description, "fps", None)
        actual = None
        if self.backend is not None and getattr(self.backend, "description", None):
            cr = self.backend.description.color_res
            if cr:
                actual = f"{cr[0]}x{cr[1]}"
        exp = float(cfg.get("exposure", -6))
        try:
            from .sensor.webcam import exposure_shutter
            denom, cap_fps = exposure_shutter(exp)
        except Exception:
            denom, cap_fps = 64, 64.0
        fps_notice = ""
        if fps is not None and float(fps) < 20:
            fps_notice = (
                f"Live rate is {float(fps):.0f} fps. Exposure {exp:g} is ~1/{denom} s "
                f"(shutter cannot exceed ~{cap_fps:.0f} fps). Use −6 or −8 and turn "
                f"the lights up for a rolling ball at 25 fps — lengthening the "
                f"shutter to −4 brightens the picture but caps the camera."
            )
        return {
            "devices": self._camera_list,
            "scanning": not self._camera_scan_done,
            "active_device": int(cfg.get("device", 0)),
            "resolution": str(cfg.get("resolution", "1280x720")),
            "actual_resolution": actual or status.get("negotiated_resolution"),
            "backend": str(cfg.get("backend", "auto")),
            "capture_api": status.get("capture_api") or "",
            "fourcc": status.get("fourcc") or "",
            "exposure": exp,
            "shutter_denom": denom,
            "shutter_fps_cap": cap_fps,
            "measured_fps": None if fps is None else float(fps),
            "lock_notice": notice,
            "fps_notice": fps_notice,
            "exposure_control": exposure_ok is not False,
            "show_driver_settings": bool(status.get("show_driver_settings")),
            "locked": bool(status.get("locked")),
            "is_color_only": self.is_color_only,
            "is_mock": self.is_mock,
            "sensor_status": self.sensor_status,
            "error": self._camera_error,
            "debug_overlay": bool(self.settings.get("display", "debugOverlay", default=False)),
        }

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
        self._init_place_ghosts()
        ghosts = []
        for g in self._place_ghosts:
            ghosts.append({"label": g["label"], "item": g["item"],
                           "polygon": g["polygon"], "real_size_cm": g.get("real_size_cm", []),
                           "kind": g.get("kind", "soft"), "seen": bool(g.get("seen"))})
        return ghosts

    def _mock_ball_snapshot(self) -> list[dict]:
        return self._setup_ball_snapshot()

    def _setup_ball_snapshot(self) -> list[dict]:
        out = []
        for i, p in enumerate(self.players):
            bid = self.ball_for_player.get(p.id, f"ball{i}")
            pos = self._ball_positions.get(bid)
            if pos is None and i < len(self._mock_balls):
                pos = (self._mock_balls[i]["x"], self._mock_balls[i]["y"])
            out.append({
                "id": bid, "color": p.color,
                "x": pos[0] if pos else 0.0, "y": pos[1] if pos else 0.0,
                "hue_name": p.hue_name or HUE_NAMES.get(p.color, "custom"),
                "rejected": bid in self._rejected_balls,
            })
        return out

    def _hud_snapshot(self) -> dict:
        active = self._active_player()
        motion = self._ball_motion_status()
        bid = self._active_ball_id()
        tb = self.tracker.balls.get(bid) if bid else None
        pos = tb.smoothed if tb is not None and tb.smoothed is not None else self._ball_positions.get(bid)
        return {
            "active_player": active.as_dict() if active else None,
            "others": [p.as_dict() for p in self.players if p.id != (active.id if active else None)],
            "hole": self.hole, "holes": self.holes,
            "course": course_by_id(self.current_course_id),
            "motion": motion,
            "scores": self.player_scores,
            "finished_hole": self.finished_hole,
            "lost_balls": self._lost_ball_snapshot(),
            "awaiting_tee": bool(not self._shot_armed),
            "in_start": bool(self._in_start_zone(pos)),
            "ball_seen": bool(
                tb is not None and tb.position is not None
                and (not tb.lost or getattr(tb, "held", False))
            ),
        }

    def _ball_motion_status(self) -> dict:
        ball_id = self._active_ball_id()
        tb = self.tracker.balls.get(ball_id) if ball_id else None
        moving = bool(tb.moving) if tb else False
        hidden = bool(tb.hidden) if tb else False
        dist = None
        if tb is not None and tb.smoothed is not None and self.setup.hole is not None:
            dist = abs(float(round(float(np.hypot(tb.smoothed[0] - self.setup.hole.x, tb.smoothed[1] - self.setup.hole.y)), 1)))
        return {"moving": moving, "hidden": hidden, "dist_to_cup": dist}

    def _lost_ball_snapshot(self) -> list[dict]:
        now = time.time()
        out = []
        for p in self.players:
            bid = self.ball_for_player.get(p.id)
            tb = self.tracker.balls.get(bid) if bid else None
            if tb is None:
                continue
            unseen = bool(getattr(tb, "lost", False)) or tb.position is None
            if unseen and not getattr(tb, "held", False):
                t0 = self._lost_since.setdefault(bid or p.id, now)
                if now - t0 >= 2.0:
                    out.append({"id": p.id, "name": p.name, "color": p.color,
                                "since": round(now - t0, 1)})
            else:
                self._lost_since.pop(bid or p.id, None)
        return out

    def _turnchange_snapshot(self) -> dict:
        active = self._active_player()
        return {"player": active.as_dict() if active else None,
                "stroke": self._current_strokes(active.id) if active else 0,
                "prev": self._turn_prev,
                "prev_stroke": self._turn_prev_stroke}

    def _holeout_snapshot(self) -> dict:
        active = self._active_player()
        par = self.course_pars[self.hole - 1] if self.hole - 1 < len(self.course_pars) else 2
        strokes = self._current_strokes(active.id) if active else 0
        return {"player": active.as_dict() if active else None, "strokes": strokes, "par": par}

    def _oob_snapshot(self) -> dict:
        active = self._active_player()
        exit_pt = self._oob_exit
        last_in = None
        bid = self._active_ball_id()
        if bid and bid in self._last_in_bounds:
            last_in = self._last_in_bounds[bid]
        return {"player": active.as_dict() if active else None,
                "strokes": self._current_strokes(active.id) if active else 0,
                "exit": [round(exit_pt[0], 3), round(exit_pt[1], 3)] if exit_pt else None,
                "last_in_bounds": [round(last_in[0], 3), round(last_in[1], 3)] if last_in else None,
                "lost_balls": self._lost_ball_snapshot()}

    def _pause_snapshot(self) -> dict:
        active = self._active_player()
        course = course_by_id(self.current_course_id)
        return {"focus": self._pause_focus, "recal_flyout": self._recal_flyout,
                "hole": self.hole, "holes": self.holes,
                "par": course.get("par") if course else None,
                "course": course,
                "active": active.as_dict() if active else None,
                "scores": self.player_scores}

    def _undo_card_snapshot(self) -> dict | None:
        last = None
        for e in reversed(self.events.events):
            if e.hole == self.hole:
                last = e
                break
        if last is None:
            return None
        n = self._current_strokes(last.player_id)
        return {"name": self._player_name(last.player_id),
                "player_id": last.player_id,
                "from": n, "to": max(0, n - 1),
                "text": last.data.get("text") if last.data else ""}

    def _holecomplete_snapshot(self) -> dict:
        return {"scorecard": build_scorecard(self.players, self.player_scores, self.hole, self.course_pars),
                "leading": self._leader_snapshot(),
                "next_hole": self.hole + 1,
                "next_course": course_by_id(self.course_ids[self.hole]) if self.hole < len(self.course_ids) else None,
                "is_last": self.hole >= self.holes}

    def _finish_snapshot(self) -> dict:
        winner = self._leader_snapshot()
        return {"champion": winner, "standings": self._standings_snapshot(),
                "stats": self._night_stats(),
                "snapshot": bool(self._finish_jpeg),
                "snapshot_url": "/snapshot.jpg" if self._finish_jpeg else ""}

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
        mint = "#8be9c3"
        total_st = sum(total(self.player_scores, p.id) for p in self.players)
        cards = [
            {"label": "Total strokes", "value": str(total_st), "color": mint},
            {"label": "Holes played", "value": str(self.holes), "color": mint},
        ]
        best = None  # (score, name, color, hole)
        longest = None
        for p in self.players:
            scores = self.player_scores.get(p.id, [])
            for i, v in enumerate(scores):
                if v is None:
                    continue
                if best is None or v < best[0]:
                    best = (v, p.name, p.color, i + 1)
                if longest is None or v > longest[0]:
                    longest = (v, p.name, p.color, i + 1)
        if best:
            cards.append({"label": "Best hole",
                          "value": f"{best[1]} · {best[0]}", "color": best[2]})
        else:
            cards.append({"label": "Best hole", "value": "—", "color": mint})
        if longest:
            cards.append({"label": "Longest hole",
                          "value": f"{longest[1]} · {longest[0]}", "color": longest[2]})
        else:
            cards.append({"label": "Longest hole", "value": "—", "color": mint})
        pen = {}
        for e in self.events.events:
            if e.type == EventType.OOB:
                pen[e.player_id] = pen.get(e.player_id, 0) + 1
        if pen:
            pid = max(pen, key=pen.get)
            p = next((x for x in self.players if x.id == pid), None)
            cards.append({"label": "Most penalties",
                          "value": f"{p.name if p else pid} · {pen[pid]}",
                          "color": p.color if p else mint})
        else:
            cards.append({"label": "Most penalties", "value": "—", "color": mint})
        fastest = None  # (duration, name, color)
        first_stroke: dict[tuple[str, int], float] = {}
        for e in self.events.events:
            key = (e.player_id, e.hole)
            if e.type == EventType.STROKE and key not in first_stroke:
                first_stroke[key] = e.t
            if e.type == EventType.HOLE_OUT and key in first_stroke:
                dur = max(0.0, e.t - first_stroke[key])
                if fastest is None or dur < fastest[0]:
                    p = next((x for x in self.players if x.id == e.player_id), None)
                    fastest = (dur, p.name if p else e.player_id, p.color if p else mint)
        if fastest:
            cards.append({"label": "Fastest hole-out",
                          "value": f"{fastest[1]} · {fastest[0]:.1f}s",
                          "color": fastest[2]})
        else:
            cards.append({"label": "Fastest hole-out", "value": "—", "color": mint})
        return {"total_strokes": total_st, "holes_played": self.holes, "cards": cards}

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

    def _overlay_cal_area(self) -> dict:
        o = {"shapes": []}
        pts = [list(self._corner_feed_xy(pt)) for pt in self._draw_poly]
        n = len(pts)
        size_txt = f"{self._preset_w:g} × {self._preset_h:g} m"
        o["shapes"].append({
            "type": "label", "x": 0.5, "y": 0.055,
            "text": (f"{n} of 4 corners · this rectangle is {size_txt}"
                     if n else f"Click the four corners of a {size_txt} rectangle"),
            "fill": "#8be9c3", "size": 0.026, "anchor": "middle",
            "id": "area_hint",
        })
        if n >= 2:
            o["shapes"].append({
                "type": "polygon" if n >= 4 else "polyline",
                "pts": pts,
                "stroke": "#f2efe8", "stroke_width": 4,
                "fill": "rgba(242,239,232,0.07)" if n >= 4 else "none",
                "id": "play_area_draft",
            })
        drag = self._dragging[1] if self._dragging and self._dragging[0] == "area" else None
        for i, (nx, ny) in enumerate(pts):
            active = i == drag
            o["shapes"].append({
                "type": "circle", "x": nx, "y": ny,
                "r": self._px(22 if active else 18),
                "fill": "#8be9c3" if active else "#f2efe8",
                "stroke": "#15171c", "stroke_width": 3,
                "label": f"dragging corner {i + 1}" if active else str(i + 1),
                "id": f"corner{i}",
            })
        if n >= 4:
            o["shapes"].extend(self._rect_dimension_labels(pts, self._preset_w, self._preset_h))
        return o

    def _rect_dimension_labels(self, pts: list[list[float]], width_m: float, height_m: float) -> list[dict]:
        """Width along the top edge, height along the right edge (design S05)."""
        if len(pts) < 4:
            return []
        ordered = sorted(((float(p[0]), float(p[1])) for p in pts), key=lambda p: (p[1], p[0]))
        top = sorted(ordered[:2], key=lambda p: p[0])
        bot = sorted(ordered[2:], key=lambda p: p[0])
        tl, tr, br = top[0], top[1], bot[1]
        top_mid = ((tl[0] + tr[0]) / 2, min(tl[1], tr[1]) - 0.035)
        right_mid = (max(tr[0], br[0]) + 0.03, (tr[1] + br[1]) / 2)
        return [
            {"type": "label", "x": max(0.06, min(0.94, top_mid[0])),
             "y": max(0.09, top_mid[1]), "text": f"{width_m:g} m",
             "fill": "#f2efe8", "size": 0.032, "anchor": "middle", "id": "dim_w"},
            {"type": "label", "x": min(0.94, right_mid[0]),
             "y": max(0.08, min(0.94, right_mid[1])), "text": f"{height_m:g} m",
             "fill": "#f2efe8", "size": 0.032, "anchor": "start", "id": "dim_h"},
        ]

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
        # Play-area editor always draws the live corners — even when a
        # homography already exists (recalibrate / redraw).
        if self.state == S.CAL_AREA:
            return self._overlay_cal_area()
        if self.mapper is None:
            return o
        st = self.state
        # Play area.
        if self.setup.play_area:
            area_pts = self._floor_poly_norm(self.setup.play_area)
            oob = st == S.OOB
            o["shapes"].append({"type": "polygon", "pts": area_pts,
                                "stroke": "#ff6b57" if oob else "#f2efe8",
                                "stroke_opacity": 1 if oob else 0.75,
                                "stroke_width": 5 if oob else 3,
                                "fill": "rgba(242,239,232,0.06)", "id": "play_area"})
            if st == S.CAL_AREA and len(area_pts) >= 4:
                aw, ah = self._play_area_size()
                o["shapes"].extend(self._rect_dimension_labels(area_pts, aw, ah))
        # Course ghosts (place-your-objects).
        if st in (S.CAL_PLACE, S.HOLE_START):
            self._init_place_ghosts()
            for i, g in enumerate(self._place_ghosts):
                selected = i == self._selected_ghost
                pts = self._floor_poly_norm(g["polygon"])
                o["shapes"].append({
                    "type": "polygon",
                    "pts": pts,
                    "stroke": "#8be9c3" if selected else "#f2efe8",
                    "stroke_width": 5 if selected else 3,
                    "dash": "" if selected else "16 10",
                    "fill": "rgba(139,233,195,0.16)" if selected else "rgba(242,239,232,0.07)",
                    "label": g.get("label") or f"Object {i + 1}",
                    "id": f"ghost{i}",
                })
                drag = None
                if self._dragging and self._dragging[0] == "ghost_corner" and int(self._dragging[1]) == i:
                    drag = int(self._dragging[2])
                for ci, (px, py) in enumerate(pts):
                    active = selected and ci == drag
                    o["shapes"].append({
                        "type": "circle", "x": px, "y": py,
                        "r": 0.026 if active else (0.018 if selected else 0.012),
                        "fill": "#8be9c3" if active else ("#f2efe8" if selected else "rgba(242,239,232,0.7)"),
                        "stroke": "#15171c", "stroke_width": 2,
                        "label": str(ci + 1) if selected else None,
                        "id": f"g{i}c{ci}",
                    })
            if self.setup.hole is None and self.layout is not None:
                hx, hy = self.layout.hole
                u, v = self.mapper.floor_to_pixel(hx, hy)
                ru = self.mapper.radius_to_pixels(hx, hy, 0.045)
                o["shapes"].append({"type": "circle",
                                    "x": u / self._feed_w, "y": v / self._feed_h,
                                    "r": ru / self._feed_w, "stroke": "#8be9c3",
                                    "stroke_width": 3, "dash": "10 8",
                                    "fill": "none", "label": "Cup goes here", "id": "hole_ghost"})
        # Start.
        if self.setup.start is not None:
            u, v = self.mapper.floor_to_pixel(self.setup.start.x, self.setup.start.y)
            ru = self.mapper.radius_to_pixels(self.setup.start.x, self.setup.start.y, self.setup.start.r)
            waiting = st == S.PLAY and not self._shot_armed
            o["shapes"].append({"type": "circle", "x": u / self._feed_w, "y": v / self._feed_h,
                                "r": ru / self._feed_w,
                                "stroke": "#8be9c3" if waiting else "#f2efe8",
                                "stroke_width": 5 if waiting else 3,
                                "dash": "10 8" if waiting else "",
                                "fill": "rgba(139,233,195,0.12)" if waiting else "none",
                                "label": "START — put the ball here" if waiting else "START",
                                "id": "start"})
        # Hole.
        if self.setup.hole is not None:
            u, v = self.mapper.floor_to_pixel(self.setup.hole.x, self.setup.hole.y)
            ru = self.mapper.radius_to_pixels(self.setup.hole.x, self.setup.hole.y, self.setup.hole.r)
            hx, hy = u / self._feed_w, v / self._feed_h
            hr = ru / max(1, self._feed_w)
            active = self._active_player()
            if st == S.HOLE_OUT:
                o["shapes"].append({
                    "type": "circle", "x": hx, "y": hy, "r": hr,
                    "stroke": "#8be9c3", "stroke_width": 6,
                    "fill": "rgba(139,233,195,0.4)",
                    "class": "rg-flash", "id": "hole",
                })
                o["shapes"].append({
                    "type": "circle", "x": hx, "y": hy, "r": self._px(120),
                    "stroke": (active.color if active else "#8be9c3"),
                    "stroke_width": 6, "fill": "none",
                    "class": "rg-pulse", "id": "hole_pulse",
                })
                o["shapes"].append({
                    "type": "circle", "x": hx, "y": hy, "r": self._px(18),
                    "fill": (active.color if active else "#8be9c3"),
                    "stroke": "#15171c", "stroke_width": 2, "id": "hole_ball",
                })
            else:
                cup_label = None
                if st == S.CAL_CUP and not self._cup_searching:
                    cm = round(self.setup.hole.r * 200)
                    cup_label = f"Hole zone · Ø {cm} cm · confidence {self._cup_confidence:.2f}"
                o["shapes"].append({
                    "type": "circle", "x": hx, "y": hy, "r": hr,
                    "stroke": "#8be9c3", "stroke_width": 5,
                    "fill": "rgba(139,233,195,0.25)",
                    "label": cup_label, "id": "hole",
                })
                if st == S.CAL_CUP:
                    o["shapes"].append({
                        "type": "circle", "x": hx + hr, "y": hy, "r": self._px(10),
                        "fill": "#f2efe8", "stroke": "#15171c", "stroke_width": 3,
                        "id": "corner0",
                    })
        # OOB exit mark.
        if st == S.OOB and self._oob_exit is not None:
            eu, ev = self.mapper.floor_to_pixel(*self._oob_exit)
            ex, ey = eu / self._feed_w, ev / self._feed_h
            active = self._active_player()
            bid = self._active_ball_id()
            ball_pos = self._ball_positions.get(bid) if bid else self._oob_ball
            if ball_pos is not None:
                bu, bv = self.mapper.floor_to_pixel(*ball_pos)
                o["shapes"].append({
                    "type": "polyline",
                    "pts": [[ex, ey], [bu / self._feed_w, bv / self._feed_h]],
                    "stroke": (active.color if active else "#f2efe8"),
                    "stroke_width": 4, "fill": "none", "id": "oob_line",
                })
            o["shapes"].append({
                "type": "circle", "x": ex, "y": ey, "r": self._px(34),
                "fill": "none", "stroke": "#ff6b57", "stroke_width": 6,
                "id": "oob_exit",
            })
            o["shapes"].append({
                "type": "html", "x": ex, "y": max(0.04, ey - 0.04),
                "text": "Exit point — put the ball back here",
                "class": "overlay-exit-label", "anchor": "end",
            })
        # Obstacles: only while placing / verifying / recalibrating. During
        # play the physical blocks are on the floor — extra outlines just clutter.
        if st not in (S.PLAY, S.TURN_CHANGE, S.HOLE_OUT, S.OOB, S.GAME_START):
            for oi, ob in enumerate(self.setup.obstacles):
                selected = st == S.CAL_OBSTACLES and oi == self._selected_obstacle
                pts = self._floor_poly_norm(ob.polygon)
                if ob.state == "deleted":
                    style = {"stroke": "#ff6b57", "stroke_width": 3, "dash": "6 8", "opacity": 0.35}
                elif selected:
                    style = {"stroke": "#8be9c3", "stroke_width": 5, "fill": "rgba(139,233,195,0.16)"}
                elif ob.state in ("proposed",):
                    low = ob.confidence < 0.7
                    style = {"stroke": "#ff6b57" if low else "#8be9c3", "stroke_width": 4,
                             "dash": "16 10", "fill": "rgba(255,107,87,0.14)" if low else "rgba(139,233,195,0.12)"}
                elif ob.state == "drawing":
                    style = {"stroke": "#f2efe8", "stroke_width": 3, "dash": "12 10"}
                else:  # confirmed
                    style = {"stroke": "#f2efe8", "stroke_width": 4, "fill": "rgba(242,239,232,0.08)"}
                o["shapes"].append({"type": "polygon", "pts": pts,
                                    "id": f"obstacle_{ob.id}",
                                    "label": "deleted · LB to undo" if ob.state == "deleted" else None,
                                    **style})
                if selected and pts:
                    drag_c = None
                    if self._dragging and self._dragging[0] == "obs_corner" and int(self._dragging[1]) == oi:
                        drag_c = int(self._dragging[2])
                    for ci, (px, py) in enumerate(pts):
                        active = ci == drag_c
                        o["shapes"].append({
                            "type": "circle", "x": px, "y": py,
                            "r": self._px(16 if active else 11),
                            "fill": "#8be9c3" if active else "#f2efe8",
                            "stroke": "#15171c", "stroke_width": 3,
                            "id": f"o{oi}c{ci}",
                        })
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    o["shapes"].append({
                        "type": "html",
                        "x": sum(xs) / len(xs),
                        "y": max(0.03, min(ys) - 0.035),
                        "text": f"{ob.label} · drag a corner to resize",
                        "class": "overlay-outline-label",
                    })
        # Balls + trail. (S09 panel already has the click hint — don't paint
        # another line on top of the title; it stole clicks and ate the words.)
        for i, p in enumerate(self.players):
            ball_id = self.ball_for_player.get(p.id, f"ball{i}")
            tb = self.tracker.balls.get(ball_id)
            live = st in (S.PLAY, S.TURN_CHANGE, S.HOLE_OUT, S.OOB, S.GAME_START)
            if live and tb is not None and tb.smoothed is not None:
                pos = tb.smoothed
            else:
                pos = self._ball_positions.get(ball_id)
                if pos is None and tb is not None and tb.smoothed is not None:
                    pos = tb.smoothed
            if pos is None:
                continue
            u, v = self.mapper.floor_to_pixel(*pos)
            x, y = u / self._feed_w, v / self._feed_h
            rejected = ball_id in self._rejected_balls
            selected = st == S.CAL_BALLS and i == self._selected_setup_player
            active = st == S.PLAY and ball_id == self._active_ball_id()
            held = bool(tb is not None and getattr(tb, "held", False))
            label = f"{p.hue_name or 'ball'} · {p.name}" if st == S.CAL_BALLS else p.name
            if rejected:
                label = "Too close to the cup color — swap it"
            if active and not self._shot_armed:
                label = "Ball — click if this is wrong"
            if live and tb is not None and getattr(tb, "history", None):
                hist = list(tb.history)
                step = max(1, len(hist) // 12)
                for hi, (_t, hx, hy) in enumerate(hist[::step]):
                    hu, hv = self.mapper.floor_to_pixel(hx, hy)
                    o["shapes"].append({
                        "type": "circle",
                        "x": hu / self._feed_w, "y": hv / self._feed_h,
                        "r": self._px(5), "fill": p.color, "opacity": 0.28,
                        "stroke": "none", "id": f"trail_{p.id}_{hi}",
                    })
            if st == S.CAL_BALLS or active:
                o["shapes"].append({
                    "type": "circle", "x": x, "y": y,
                    "r": 0.042 if active else (0.038 if selected else 0.03),
                    "fill": "none",
                    "stroke": "#ff6b57" if rejected else ("#8be9c3" if (selected or active) else p.color),
                    "stroke_width": 5 if rejected else (6 if (selected or active) else 4),
                    "dash": "8 6" if (active and held) else "",
                    "id": f"ballring_{p.id}",
                })
            if rejected:
                o["shapes"].append({
                    "type": "html", "x": x, "y": max(0.04, y - 0.045),
                    "text": label, "class": "overlay-reject-pill",
                })
                label = None
            o["shapes"].append({
                "type": "circle", "x": x, "y": y,
                "r": self._px(20) if live else (0.016 if selected else 0.013),
                "fill": p.color,
                "stroke": "#ff6b57" if rejected else "#f2efe8",
                "stroke_width": 3,
                "label": None if live or rejected else label,
                "id": f"ball_{p.id}",
            })
            if live and not rejected:
                o["shapes"].append({
                    "type": "html", "x": x, "y": max(0.03, y - 0.04),
                    "text": p.name, "class": "overlay-ball-name",
                    "color": p.color,
                })
        if self._tee_click and time.time() < self._tee_click[2]:
            o["shapes"].append({
                "type": "circle", "x": self._tee_click[0], "y": self._tee_click[1],
                "r": 0.034, "fill": "none", "stroke": "#8be9c3",
                "stroke_width": 5, "dash": "6 5", "label": "Locked",
                "id": "tee_click",
            })
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
    def _px(self, px: float) -> float:
        return float(px) / max(1.0, float(self._feed_w))

    def _capture_finish_still(self) -> None:
        if self._frame_color is None:
            return
        import cv2
        ok, buf = cv2.imencode(".jpg", self._frame_color, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok:
            self._finish_jpeg = buf.tobytes()
            self._finish_jpeg_t = time.time()

    @property
    def finish_jpeg(self) -> bytes | None:
        return self._finish_jpeg

    def encode_frame(self) -> bytes | None:
        if self._frame_color is None:
            return None
        import cv2
        img = self._frame_color
        if self.settings.get("display", "debugOverlay", default=False):
            fps = None
            if self.backend is not None:
                try:
                    fps = (self.backend.capture_status() or {}).get("measured_fps")
                except Exception:
                    fps = getattr(self.backend.description, "fps", None)
            drawn = self.tracker.draw_debug(img, self.mapper, fps=fps)
            if drawn is not None:
                img = drawn
        h, w = img.shape[:2]
        if w > 960:
            img = cv2.resize(img, (960, max(1, int(h * 960 / w))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 62])
        if not ok:
            return None
        return buf.tobytes()

    def shutdown(self) -> None:
        if self.backend is not None:
            try:
                self.backend.close()
            except Exception:
                pass
