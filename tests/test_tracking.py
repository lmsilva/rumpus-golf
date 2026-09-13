"""Mock-sensor and tracker checks for the webcam tracking fixes."""
from __future__ import annotations

import time

from rumpus.models import Player
from rumpus.sensor.mock import MockBackend
from rumpus.vision.ball_tracker import (
    BallTracker, hue_circular_dist, hue_clash_pairs, hue_window,
)


def test_mock_lock_is_noop():
    b = MockBackend()
    assert b.open()
    b.lock_capture(-6)
    b.unlock_capture()
    b.set_exposure(-5)
    frame = b.grab()
    assert frame.color is not None and frame.depth is not None
    status = b.capture_status()
    assert status["locked"] is False
    b.close()


def test_gate_is_velocity_aware():
    tracker = BallTracker()
    tracker.add_ball("ball0", "p0", "#ff8a3d", hue_range=(8, 24))
    ball = tracker.balls["ball0"]
    now = time.time()
    tracker.seed_position("ball0", (0.0, 0.0), now=now, hold=True)
    assert tracker.gate_radius(ball, now) == 0.55
    ball.held = False
    ball._speed = 1.0
    ball.last_seen_t = now - 0.2
    got = tracker.gate_radius(ball, now)
    assert abs(got - (0.3 + 1.0 * 0.2 * 1.5)) < 1e-6
    assert tracker._within_gate(ball, (0.5, 0.0), now)
    assert not tracker._within_gate(ball, (1.2, 0.0), now)


def test_hue_window_and_clash():
    lo, hi = hue_window(12.0, 10.0)
    assert lo == 2 and hi == 22
    wrap = hue_window(175.0, 10.0)
    assert wrap[0] == 165 and wrap[1] == 5
    assert hue_circular_dist(10, 25) == 15
    assert hue_circular_dist(175, 5) == 10
    close = [
        Player(id="a", name="A", color="#ff8a3d", hue_center=12.0),
        Player(id="b", name="B", color="#ffd84d", hue_center=20.0),
    ]
    far = [
        Player(id="a", name="A", color="#ff8a3d", hue_center=12.0),
        Player(id="c", name="C", color="#5b8cff", hue_center=110.0),
    ]
    assert hue_clash_pairs(close)
    assert not hue_clash_pairs(far)


def test_mock_putt_tracks_without_teleport():
    backend = MockBackend()
    backend.open()
    backend.set_scene(
        cup={"x": 0.8, "y": 0.0, "r": 0.045},
        balls=[{"x": -0.6, "y": 0.0, "r": 0.022, "color": (0, 110, 255), "height_m": 0.04}],
    )
    tracker = BallTracker()
    tracker.add_ball("ball0", "p0", "#ff8a3d", hue_range=(8, 24),
                     hue_center=14.0, sat_floor=80, val_floor=120)
    now = time.time()
    frame = backend.grab()
    tracker.update(frame.color, frame.depth, backend.mapper, backend.cam,
                   backend.plane, [], now)
    if tracker.balls["ball0"].position is None:
        tracker.seed_position("ball0", (-0.6, 0.0), now=now)
    last = tracker.balls["ball0"].position
    jumps = []
    for i in range(1, 25):
        x = -0.6 + i * 0.05
        backend.set_scene(
            balls=[{"x": x, "y": 0.0, "r": 0.022, "color": (0, 110, 255), "height_m": 0.04}],
        )
        now += 1.0 / 30.0
        frame = backend.grab()
        tracker.update(frame.color, frame.depth, backend.mapper, backend.cam,
                       backend.plane, [], now)
        pos = tracker.balls["ball0"].smoothed or tracker.balls["ball0"].position
        assert pos is not None
        jumps.append(abs(pos[0] - last[0]))
        last = pos
    # A real putt should walk, not freeze then jump a meter.
    assert max(jumps) < 0.35
    assert last[0] > 0.2
    backend.close()


def test_two_ball_identities_survive_pass():
    backend = MockBackend()
    backend.open()
    orange = (0, 110, 255)
    pink = (180, 50, 255)
    backend.set_scene(
        balls=[
            {"x": -0.5, "y": 0.0, "r": 0.022, "color": orange, "height_m": 0.04},
            {"x": 0.5, "y": 0.08, "r": 0.022, "color": pink, "height_m": 0.04},
        ],
    )
    tracker = BallTracker()
    tracker.add_ball("ball0", "p0", "#ff8a3d", hue_range=(8, 24),
                     hue_center=14.0, sat_floor=80, val_floor=120)
    tracker.add_ball("ball1", "p1", "#ff5fa8", hue_range=(150, 8),
                     hue_center=165.0, sat_floor=80, val_floor=120)
    now = time.time()
    frame = backend.grab()
    tracker.update(frame.color, frame.depth, backend.mapper, backend.cam,
                   backend.plane, [], now)
    tracker.seed_position("ball0", (-0.5, 0.0), now=now)
    tracker.seed_position("ball1", (0.5, 0.08), now=now)
    closest = 99.0
    for i in range(21):
        x0 = -0.5 + i * 0.05
        x1 = 0.5 - i * 0.05
        closest = min(closest, abs(x0 - x1))
        backend.set_scene(
            balls=[
                {"x": x0, "y": 0.0, "r": 0.022, "color": orange, "height_m": 0.04},
                {"x": x1, "y": 0.08, "r": 0.022, "color": pink, "height_m": 0.04},
            ],
        )
        now += 1.0 / 30.0
        frame = backend.grab()
        tracker.update(frame.color, frame.depth, backend.mapper, backend.cam,
                       backend.plane, [], now)
    assert closest < 0.10
    p0 = tracker.balls["ball0"].smoothed or tracker.balls["ball0"].position
    p1 = tracker.balls["ball1"].smoothed or tracker.balls["ball1"].position
    assert p0 is not None and p1 is not None
    # After the pass, orange should be on the right and pink on the left.
    assert p0[0] > p1[0]
    backend.close()


if __name__ == "__main__":
    test_mock_lock_is_noop()
    test_gate_is_velocity_aware()
    test_hue_window_and_clash()
    test_mock_putt_tracks_without_teleport()
    test_two_ball_identities_survive_pass()
    print("ok")
