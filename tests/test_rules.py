"""Scoring, undo, out-of-bounds and setup-loading rules."""
from __future__ import annotations

import json
import time

import numpy as np

from rumpus.engine import FEED_STALE_MSG, FEED_STALE_S, GameEngine, S
from rumpus.game.scoring import build_scorecard
from rumpus.models import CircleZone, EventType, Frame, Player
from rumpus.setup_data import Setup


AREA = [(-1.5, -1.0), (1.5, -1.0), (1.5, 1.0), (-1.5, 1.0)]


def _round(**extra) -> GameEngine:
    e = GameEngine()
    e._save_setup = lambda: None
    e.players = [
        Player(id="p0", name="Ada", color="#ff8a3d", hue_name="orange"),
        Player(id="p1", name="Bo", color="#ff5fa8", hue_name="pink"),
    ]
    e.ball_for_player = {"p0": "ball0", "p1": "ball1"}
    e._ball_positions = {"ball0": (0.2, 0.1), "ball1": (-0.2, 0.1)}
    e.player_scores = {"p0": [1, None], "p1": [None, None]}
    e.finished_hole = {"p0": False, "p1": False}
    e.holes = 2
    e.hole = 1
    e.course_pars = [2, 3]
    e.setup.play_area = list(AREA)
    e.setup.start = CircleZone(0.0, 0.0, 0.15)
    e.setup.hole = CircleZone(1.0, 0.0, 0.045)
    e.state = S.PLAY
    e._prev_state_for_pause = S.PLAY
    e._shot_armed = True
    for k, v in extra.items():
        setattr(e, k, v)
    for pid, bid in e.ball_for_player.items():
        e.tracker.add_ball(bid, pid, "#ff8a3d", hue_range=(8, 24))
    return e


# --------------------------------------------------------------------------- #
# Out of bounds
# --------------------------------------------------------------------------- #
def test_oob_confirm_puts_the_ball_back_in_bounds():
    e = _round()
    e._last_in_bounds["ball0"] = (0.5, 0.2)
    e._oob_ball = (2.4, 0.0)
    e._oob_exit = (0.5, 0.2)
    e.state = S.OOB
    e.handle_input({"t": "action", "a": "confirm"})
    pos = e._ball_positions["ball0"]
    assert pos == (0.5, 0.2)
    # A ball left outside would trip out-of-bounds again on the very next stop.
    from rumpus.vision.obstacle_detect import point_in_polygon
    assert point_in_polygon(pos[0], pos[1], AREA)
    assert e._oob_ball is None


def test_oob_without_penalty_setting_adds_no_stroke_and_undo_matches():
    e = _round()
    e.settings.set(False, "rules", "oobPenalty")
    tb = e.tracker.balls["ball0"]
    tb.smoothed = (2.4, 0.0)
    e._last_in_bounds["ball0"] = (0.5, 0.2)
    e._resolve_after_stop(e.players[0], tb, time.time())
    assert e.state == S.OOB
    assert e._current_strokes("p0") == 1     # no penalty stroke
    e._undo_last_shot()
    assert e._current_strokes("p0") == 1     # and undo must not invent one


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #
def test_undo_stroke_restores_the_ball_position():
    e = _round()
    e._ball_positions["ball0"] = (0.20, 0.10)
    e._record_stroke("p0")
    assert e._current_strokes("p0") == 2
    e._ball_positions["ball0"] = (0.90, 0.30)   # where the putt ended up
    e._undo_last_shot()
    assert e._current_strokes("p0") == 1
    assert e._ball_positions["ball0"] == (0.20, 0.10)


def test_undo_after_stroke_cap_puts_the_player_back_in_play():
    e = _round()
    e.stroke_cap = 3
    e._set_strokes("p0", 3)
    e._finish_player_hole("p0", EventType.CAP)
    assert e.finished_hole["p0"] is True
    e._undo_last_shot()
    assert e.finished_hole["p0"] is False


def test_fix_score_undo_reverses_the_whole_edit():
    e = _round()
    e.state = S.FIX_SCORE
    for _ in range(3):
        e.handle_input({"t": "set", "key": "stepper", "player_id": "p0", "delta": 1})
    assert e._current_strokes("p0") == 4
    e.handle_input({"t": "action", "a": "confirm"})
    e._undo_last_shot()
    assert e._current_strokes("p0") == 1


def test_fix_score_confirm_with_no_change_is_not_undoable():
    e = _round()
    e.state = S.FIX_SCORE
    e.handle_input({"t": "action", "a": "confirm"})
    assert e._current_strokes("p0") == 1
    e._undo_last_shot()
    assert e._current_strokes("p0") == 1


# --------------------------------------------------------------------------- #
# Scorecard
# --------------------------------------------------------------------------- #
def test_scorecard_pars_cover_only_the_holes_played():
    card = build_scorecard(
        [Player(id="p0", name="Ada", color="#ff8a3d")],
        {"p0": [2, None, None]}, 1, [2, 3, 4],
    )
    assert card["holes"] == [1]
    assert card["pars"] == [2]
    assert card["par_total"] == 2


def test_vs_par_after_one_hole_is_not_measured_against_the_whole_night():
    e = _round()
    e._set_strokes("p0", 2)     # par 2 on hole 1
    e._set_strokes("p1", 3)
    e.state = S.HOLE_COMPLETE
    leader = e._leader_snapshot()
    # Against the full 2-hole par of 5 this player read as -3 after one hole.
    assert leader["name"] == "Ada"
    assert leader["vs_par"] == "even"


# --------------------------------------------------------------------------- #
# Hole rebuild flow
# --------------------------------------------------------------------------- #
def test_rebuilt_hole_confirms_a_new_cup_before_play():
    e = _round()
    e.course_ids = ["hallway", "hallway"]
    e.hole = 2
    e._enter_hole()
    assert e.state == S.HOLE_START
    assert e._rebuilding is True
    # Objects placed -> obstacles -> the cup must be re-confirmed, because it
    # physically moves between holes.
    e._obstacles_done()
    assert e.state == S.CAL_CUP
    e.setup.hole = CircleZone(-0.8, 0.4, 0.045)
    e.handle_input({"t": "action", "a": "confirm"})
    assert e.state == S.PLAY
    assert e._rebuilding is False
    assert (e.setup.hole.x, e.setup.hole.y) == (-0.8, 0.4)


def test_back_during_a_rebuild_stays_in_the_rebuild():
    e = _round()
    e.hole = 2
    e._rebuilding = True
    e.state = S.CAL_CUP
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.CAL_OBSTACLES
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.HOLE_START
    # And never into the first-run play-area wizard, which would overwrite it.
    e.state = S.CAL_COURSE
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.PAUSE


def test_game_finish_back_ends_the_night():
    e = _round()
    e.state = S.GAME_FINISH
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.BOOT
    assert e.player_scores == {}


# --------------------------------------------------------------------------- #
# Dead camera
# --------------------------------------------------------------------------- #
def test_repeated_frame_is_reported_as_no_signal():
    e = _round()
    color = np.zeros((48, 64, 3), np.uint8)
    t0 = time.time()
    fresh = Frame(color=color, depth=None, t=t0, source="test")
    e.tick(fresh)
    assert e._feed_stale is False
    # The backend hands back the same frame, timestamp included, when a grab
    # fails, so a stamp that stops advancing is the only signal of a dead cable.
    e._frame_fresh_at = time.time() - (FEED_STALE_S + 1.0)
    e.tick(fresh)
    assert e._feed_stale is True
    assert e._camera_error == FEED_STALE_MSG
    assert e.snapshot()["feed"]["stale"] is True
    # Recovering clears it without a restart.
    e.tick(Frame(color=color, depth=None, t=t0 + 1.0, source="test"))
    assert e._feed_stale is False
    assert e._camera_error == ""


# --------------------------------------------------------------------------- #
# Setup persistence
# --------------------------------------------------------------------------- #
def test_corrupt_setup_file_loads_as_much_as_it_can(tmp_path=None):
    import tempfile
    from pathlib import Path
    raw = {
        "name": "Den",
        "play_area": [[0, 0], [1, 0], "nope", [1, 1]],
        "start": {"x": 0.1},                       # missing r
        "hole": {"x": 0.9, "y": 0.0, "r": None},
        "obstacles": [{"id": "a", "polygon": [[0, 0], [1, 0]]},   # too few points
                      {"id": "b", "polygon": [[0, 0], [1, 0], [1, 1]]}],
        "players": ["junk", {"id": "p0", "name": "Ada", "hue_center": "x"}],
        "floor_plane": {"normal": ["a", "b", "c"], "offset": 1.0},
        "camera": "not a dict",
    }
    path = Path(tempfile.mkdtemp()) / "setup.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    s = Setup.load(path)
    assert s is not None
    assert s.name == "Den"
    assert len(s.play_area) == 3          # the bad entry is dropped
    assert s.start is None                # no y -> unusable, not a crash
    assert s.hole is not None and s.hole.r == 0.045
    assert [o.id for o in s.obstacles] == ["b"]
    assert [p.id for p in s.players] == ["p0"]
    assert s.floor_plane is None
    assert s.camera is None


def test_loading_a_setup_with_no_camera_block_does_not_crash():
    from rumpus.models import FloorPlane
    e = GameEngine()
    e.setup = Setup()
    e.setup.floor_plane = FloorPlane(np.array([0.0, -1.0, 0.0]), -2.0)
    e.setup.camera = None
    e._apply_saved_setup()
    assert e.mapper is not None


if __name__ == "__main__":
    test_oob_confirm_puts_the_ball_back_in_bounds()
    test_oob_without_penalty_setting_adds_no_stroke_and_undo_matches()
    test_undo_stroke_restores_the_ball_position()
    test_undo_after_stroke_cap_puts_the_player_back_in_play()
    test_fix_score_undo_reverses_the_whole_edit()
    test_fix_score_confirm_with_no_change_is_not_undoable()
    test_scorecard_pars_cover_only_the_holes_played()
    test_vs_par_after_one_hole_is_not_measured_against_the_whole_night()
    test_rebuilt_hole_confirms_a_new_cup_before_play()
    test_back_during_a_rebuild_stays_in_the_rebuild()
    test_game_finish_back_ends_the_night()
    test_repeated_frame_is_reported_as_no_signal()
    test_corrupt_setup_file_loads_as_much_as_it_can()
    test_loading_a_setup_with_no_camera_block_does_not_crash()
    print("ok")
