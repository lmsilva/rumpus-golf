"""Correcting the software when it guesses wrong.

Detection will miss things in a real living room, so every automatic decision
the game makes has to be overridable by pointing at the camera feed. These
tests cover those override paths.
"""
from __future__ import annotations

from rumpus.engine import GameEngine, S
from rumpus.models import CircleZone, Obstacle, Player
from rumpus.sensor.mock import MockBackend


AREA = [(-1.2, -0.9), (1.2, -0.9), (1.2, 0.9), (-1.2, 0.9)]


def _engine(state=S.PLAY) -> GameEngine:
    """An engine wired to the mock sensor, mid-round, with one player."""
    e = GameEngine()
    e._save_setup = lambda: None
    backend = MockBackend()
    backend.open()
    e.attach_backend(backend, backend.cam)
    e.plane = backend.plane
    e.mapper = backend.mapper
    e.setup.play_area = list(AREA)
    e.setup.start = CircleZone(-0.8, 0.0, 0.15)
    e.setup.hole = CircleZone(0.9, 0.0, 0.05)
    e.players = [Player(id="p0", name="Ada", color="#ff8a3d", hue_name="orange",
                        hue_range=(8, 24), hue_center=14.0, sat_floor=80, val_floor=120)]
    e.ball_for_player = {"p0": "ball0"}
    e.player_scores = {"p0": [1]}
    e.finished_hole = {"p0": False}
    e.hole, e.holes = 1, 1
    e.course_pars = [2]
    e.tracker.add_ball("ball0", "p0", "#ff8a3d", hue_range=(8, 24),
                       hue_center=14.0, sat_floor=80, val_floor=120)
    e.state = state
    e._prev_state_for_pause = state
    e._shot_armed = True
    # Every pointer path maps normalised clicks through the live frame, so the
    # engine needs one before a click means anything.
    e.tick(e.backend.grab())
    return e


# The mock camera is 640x480 two metres up, so a real 21 mm ball lands on ~4
# pixels — below what the setup detector is built for. 30 mm gives it the same
# handful-of-pixels blob a real camera sees from a normal tripod height.
BALL_R = 0.03


def _pixel_of(e: GameEngine, x: float, y: float) -> tuple[float, float]:
    """Normalised feed coordinates for a floor point, the way a click arrives."""
    px, py = e.mapper.floor_to_pixel(x, y)
    h, w = e._frame_color.shape[:2]
    return px / w, py / h


# --------------------------------------------------------------------------- #
# Ball misses
# --------------------------------------------------------------------------- #
def test_clicking_a_lost_ball_puts_it_back_on_the_map():
    e = _engine()
    e.backend.set_scene(balls=[{"x": 0.35, "y": -0.2, "r": BALL_R,
                                "color": (0, 110, 255), "height_m": 0.04}])
    e.tick(e.backend.grab())
    tb = e.tracker.balls["ball0"]
    tb.lost = True
    tb.smoothed = None
    tb.position = None
    nx, ny = _pixel_of(e, 0.35, -0.2)
    e.handle_input({"t": "pointer", "type": "down", "x": nx, "y": ny})
    tb = e.tracker.balls["ball0"]
    assert tb.lost is False
    assert tb.smoothed is not None
    assert abs(tb.smoothed[0] - 0.35) < 0.12 and abs(tb.smoothed[1] + 0.2) < 0.12
    # A correction is not a putt: it must not start a stroke.
    assert tb.moving is False
    assert e.state == S.PLAY


def test_clicking_the_wrong_colored_ball_relearns_its_color():
    """The usual reason a ball goes missing is a bad stored color."""
    e = _engine()
    # Tell the game the ball is blue when it is really orange.
    e.players[0].hue_center = 110.0
    e.players[0].hue_range = (95, 128)
    e.tracker.recolor("ball0", hue_center=110.0, hue_range=(95, 128),
                      sat_floor=80, val_floor=120, color_hex="#5b8cff")
    e.backend.set_scene(balls=[{"x": 0.1, "y": 0.3, "r": BALL_R,
                                "color": (0, 110, 255), "height_m": 0.04}])
    e.tick(e.backend.grab())
    nx, ny = _pixel_of(e, 0.1, 0.3)
    e.handle_input({"t": "pointer", "type": "down", "x": nx, "y": ny})
    tb = e.tracker.balls["ball0"]
    # Both the tracker and the player record are re-taught, or the ball is lost
    # again on the next frame and the click achieved nothing.
    assert tb.hue_center is not None and abs(tb.hue_center - 110.0) > 20
    assert e.players[0].hue_center == tb.hue_center


def test_clicking_empty_floor_still_seeds_a_position():
    """No blob under the cursor: take the user's word for where the ball is."""
    e = _engine()
    e.backend.set_scene(balls=[])
    e.tick(e.backend.grab())
    e.tracker.balls["ball0"].lost = True
    nx, ny = _pixel_of(e, -0.5, 0.4)
    e.handle_input({"t": "pointer", "type": "down", "x": nx, "y": ny})
    pos = e.tracker.balls["ball0"].smoothed
    assert pos is not None
    assert abs(pos[0] + 0.5) < 0.2 and abs(pos[1] - 0.4) < 0.2


def test_a_click_during_turn_change_or_hole_out_is_not_swallowed():
    for state in (S.TURN_CHANGE, S.HOLE_OUT, S.OOB):
        e = _engine(state=state)
        e.backend.set_scene(balls=[])
        e.tick(e.backend.grab())
        nx, ny = _pixel_of(e, 0.0, 0.5)
        e.handle_input({"t": "pointer", "type": "down", "x": nx, "y": ny})
        pos = e.tracker.balls["ball0"].smoothed
        assert pos is not None, f"click ignored in {state}"
        assert e.state == state


# --------------------------------------------------------------------------- #
# Obstacle misses
# --------------------------------------------------------------------------- #
def _obstacles(e: GameEngine) -> None:
    e.state = S.CAL_OBSTACLES
    e.setup.obstacles = [
        Obstacle(id="o0", label="Book", kind="soft", state="proposed",
                 confidence=0.9, polygon=[(-0.6, -0.3), (-0.2, -0.3),
                                          (-0.2, 0.1), (-0.6, 0.1)]),
        Obstacle(id="o1", label="Mug", kind="hard", state="proposed",
                 confidence=0.5, polygon=[(0.2, -0.3), (0.6, -0.3),
                                          (0.6, 0.1), (0.2, 0.1)]),
    ]
    e._selected_obstacle = 0


def test_dragging_one_corner_moves_only_that_obstacle():
    e = _engine()
    _obstacles(e)
    before = list(e.setup.obstacles[1].polygon)
    nx, ny = _pixel_of(e, -0.45, -0.25)
    e.handle_input({"t": "pointer", "type": "down", "x": nx, "y": ny, "handle": "o:0:0"})
    e.handle_input({"t": "pointer", "type": "move", "x": nx, "y": ny, "handle": "o:0:0"})
    e.handle_input({"t": "pointer", "type": "up", "x": nx, "y": ny, "handle": "o:0:0"})
    moved = e.setup.obstacles[0].polygon[0]
    assert abs(moved[0] + 0.45) < 0.1 and abs(moved[1] + 0.25) < 0.1
    # The other outline must not follow along.
    assert e.setup.obstacles[1].polygon == before


def test_overlay_tags_each_obstacle_so_the_browser_can_tell_them_apart():
    e = _engine()
    _obstacles(e)
    shapes = e.snapshot()["overlay"]["shapes"]
    polys = [s for s in shapes if s.get("type") == "polygon" and s.get("group")]
    groups = {s["group"] for s in polys}
    # Without a per-obstacle group the front end matched every outline and all
    # of them jumped together while dragging one corner.
    assert {"o0", "o1"} <= groups


def test_deleting_a_false_positive_and_undoing_it():
    e = _engine()
    _obstacles(e)
    e.handle_input({"t": "action", "a": "undo"})       # delete the selected one
    assert e.setup.obstacles[0].state == "deleted"
    e.handle_input({"t": "action", "a": "prev"})       # undo
    assert e.setup.obstacles[0].state != "deleted"


def test_confirm_all_is_blocked_until_low_confidence_outlines_are_resolved():
    e = _engine()
    _obstacles(e)
    e.handle_input({"t": "action", "a": "confirm_all"})
    # The 0.5-confidence mug still needs a human decision.
    assert e.state == S.CAL_OBSTACLES
    e._selected_obstacle = 1
    e.handle_input({"t": "action", "a": "confirm_one"})
    assert e.setup.obstacles[1].state == "confirmed"
    e.handle_input({"t": "action", "a": "confirm_all"})
    assert e.state != S.CAL_OBSTACLES


# --------------------------------------------------------------------------- #
# Cup misses
# --------------------------------------------------------------------------- #
def test_drawing_the_cup_by_hand_when_detection_misses_it():
    e = _engine(state=S.CAL_CUP)
    e.setup.hole = None
    e.handle_input({"t": "action", "a": "draw"})
    nx, ny = _pixel_of(e, 0.7, -0.4)
    e.handle_input({"t": "pointer", "type": "down", "x": nx, "y": ny})
    assert e.setup.hole is not None
    assert abs(e.setup.hole.x - 0.7) < 0.1 and abs(e.setup.hole.y + 0.4) < 0.1
    # Confirm is only offered once there is a cup to confirm.
    assert e.snapshot()["ui"]["has_hole"] is True


def test_resizing_the_cup_by_dragging_its_edge():
    e = _engine(state=S.CAL_CUP)
    e.setup.hole = CircleZone(0.7, -0.4, 0.05)
    nx, ny = _pixel_of(e, 0.7 + 0.12, -0.4)
    e.handle_input({"t": "pointer", "type": "down", "x": nx, "y": ny, "handle": "cup_r"})
    e.handle_input({"t": "pointer", "type": "move", "x": nx, "y": ny, "handle": "cup_r"})
    assert e.setup.hole.r > 0.08


# --------------------------------------------------------------------------- #
# Snapshot robustness — the UI freezes on any snapshot() throw
# --------------------------------------------------------------------------- #
def test_snapshot_never_throws_in_any_state():
    for state in _all_states():
        e = _engine(state=state)
        e.tick(e.backend.grab())
        snap = e.snapshot()
        assert snap["screen"]
        for key in ("ui", "game", "setup", "overlay", "feed"):
            assert isinstance(snap.get(key), dict), f"{state}: {key}"


def _all_states() -> list[str]:
    return [v for k, v in vars(S).items()
            if not k.startswith("_") and isinstance(v, str)]


if __name__ == "__main__":
    test_clicking_a_lost_ball_puts_it_back_on_the_map()
    test_clicking_the_wrong_colored_ball_relearns_its_color()
    test_clicking_empty_floor_still_seeds_a_position()
    test_a_click_during_turn_change_or_hole_out_is_not_swallowed()
    test_dragging_one_corner_moves_only_that_obstacle()
    test_overlay_tags_each_obstacle_so_the_browser_can_tell_them_apart()
    test_deleting_a_false_positive_and_undoing_it()
    test_confirm_all_is_blocked_until_low_confidence_outlines_are_resolved()
    test_drawing_the_cup_by_hand_when_detection_misses_it()
    test_resizing_the_cup_by_dragging_its_edge()
    test_snapshot_never_throws_in_any_state()
    print("ok")
