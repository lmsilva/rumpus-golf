"""Start-to-finish navigation: pause, recalibrate, back, quit."""
from __future__ import annotations

from rumpus.engine import GameEngine, S
from rumpus.models import Player


def _playing(**extra) -> GameEngine:
    e = GameEngine()
    e._save_setup = lambda: None
    e.players = [Player(id="p0", name="Ada", color="#ff8a3d", hue_name="orange")]
    e.ball_for_player = {"p0": "ball0"}
    e._ball_positions = {"ball0": (0.2, 0.1)}
    e.player_scores = {"p0": [1]}
    e.hole = 1
    e.holes = 3
    e.state = S.PLAY
    e._prev_state_for_pause = S.PLAY
    e._shot_armed = True
    for k, v in extra.items():
        setattr(e, k, v)
    return e


def test_play_back_pauses_and_resume_returns():
    e = _playing()
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.PAUSE
    e.handle_input({"t": "action", "a": "resume"})
    assert e.state == S.PLAY


def test_game_start_back_returns_to_balls_not_pause():
    e = _playing(state=S.GAME_START, player_scores={})
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.CAL_BALLS


def test_clicking_feed_during_play_does_not_pause():
    e = _playing()
    e.handle_input({"t": "pointer", "type": "down", "x": 0.4, "y": 0.4})
    assert e.state == S.PLAY


def test_reassign_from_play_returns_to_play_not_boot():
    e = _playing()
    e._prev_state_for_pause = S.BOOT  # never opened pause
    e.handle_input({"t": "action", "a": "secondary"})
    assert e.state == S.CAL_BALLS
    assert e._recal_return == S.PLAY
    e.handle_input({"t": "action", "a": "confirm"})
    assert e.state == S.PLAY
    assert e.state != S.BOOT
    assert e.state != S.GAME_START


def test_pause_reassign_balls_returns_to_play():
    e = _playing()
    e.handle_input({"t": "action", "a": "menu"})
    assert e.state == S.PAUSE
    e.handle_input({"t": "action", "a": "recal_balls"})
    assert e.state == S.CAL_BALLS
    assert e.snapshot()["ui"].get("recalibrating") is True
    e.handle_input({"t": "action", "a": "confirm"})
    assert e.state == S.PLAY


def test_pause_reassign_back_also_returns_to_play():
    e = _playing()
    e.handle_input({"t": "action", "a": "menu"})
    e.handle_input({"t": "action", "a": "recal_balls"})
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.PLAY


def test_change_course_from_pause_returns_without_wizard():
    e = _playing()
    e.setup.play_area = [(-1.5, -1.0), (1.5, -1.0), (1.5, 1.0), (-1.5, 1.0)]
    e.handle_input({"t": "action", "a": "menu"})
    e.handle_input({"t": "action", "a": "course"})
    assert e.state == S.CAL_COURSE
    e.handle_input({"t": "action", "a": "confirm", "index": 0})
    assert e.state == S.PLAY
    assert e.state != S.CAL_PLACE
    assert e.state != S.GAME_START


def test_first_time_ball_confirm_still_goes_to_game_night():
    e = GameEngine()
    e._save_setup = lambda: None
    e.players = [Player(id="p0", name="Ada", color="#ff8a3d", hue_name="orange")]
    e.ball_for_player = {"p0": "ball0"}
    e.state = S.CAL_BALLS
    e.handle_input({"t": "action", "a": "confirm"})
    assert e.state == S.GAME_START


def test_settings_from_pause_returns_to_pause():
    e = _playing()
    e.handle_input({"t": "action", "a": "menu"})
    e.handle_input({"t": "action", "a": "music"})
    assert e.state == S.SETTINGS
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.PAUSE
    e.handle_input({"t": "action", "a": "resume"})
    assert e.state == S.PLAY


def test_quit_clears_round_and_lands_on_boot():
    e = _playing()
    e.handle_input({"t": "action", "a": "menu"})
    e.handle_input({"t": "action", "a": "quit"})
    assert e.state == S.BOOT
    assert e.player_scores == {}
    e.state = S.CAL_BALLS
    e.players = [Player(id="p0", name="Ada", color="#ff8a3d")]
    e.handle_input({"t": "action", "a": "confirm"})
    assert e.state == S.GAME_START


def test_resume_never_returns_to_boot_mid_round():
    e = _playing()
    e.state = S.PAUSE
    e._prev_state_for_pause = S.BOOT
    e.handle_input({"t": "action", "a": "resume"})
    assert e.state == S.PLAY


def test_settings_from_any_screen_returns():
    e = _playing()
    e.handle_input({"t": "action", "a": "settings"})
    assert e.state == S.SETTINGS
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.PLAY
    e.state = S.CAL_FLOOR
    e.handle_input({"t": "action", "a": "settings"})
    assert e.state == S.SETTINGS
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.CAL_FLOOR
    e.state = S.SENSOR_CHECK
    e.sensor_status = "opening"
    e.handle_input({"t": "action", "a": "settings"})
    assert e.state == S.SETTINGS
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.SENSOR_CHECK
    e.handle_input({"t": "action", "a": "settings"})
    e.handle_input({"t": "action", "a": "settings"})
    assert e.state == S.SETTINGS


def test_settings_from_boot_while_camera_opens():
    e = GameEngine()
    e.sensor_status = "opening"
    e.handle_input({"t": "action", "a": "settings"})
    assert e.state == S.SETTINGS
    snap = e.snapshot()
    assert snap["screen"] == "S19"
    assert snap["sensor"] is None
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.BOOT


def test_hole_complete_back_pauses():
    e = _playing(state=S.HOLE_COMPLETE)
    e.handle_input({"t": "action", "a": "back"})
    assert e.state == S.PAUSE


if __name__ == "__main__":
    test_play_back_pauses_and_resume_returns()
    test_game_start_back_returns_to_balls_not_pause()
    test_clicking_feed_during_play_does_not_pause()
    test_reassign_from_play_returns_to_play_not_boot()
    test_pause_reassign_balls_returns_to_play()
    test_pause_reassign_back_also_returns_to_play()
    test_change_course_from_pause_returns_without_wizard()
    test_first_time_ball_confirm_still_goes_to_game_night()
    test_settings_from_pause_returns_to_pause()
    test_settings_from_any_screen_returns()
    test_settings_from_boot_while_camera_opens()
    test_quit_clears_round_and_lands_on_boot()
    test_resume_never_returns_to_boot_mid_round()
    test_hole_complete_back_pauses()
    print("ok")
