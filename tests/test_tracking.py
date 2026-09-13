"""Mock-sensor and tracker checks for the webcam tracking fixes."""
from __future__ import annotations

import time

import numpy as np

from rumpus.models import Player
from rumpus.sensor.mock import MockBackend
from rumpus.vision.ball_tracker import (
    BallTracker, detect_setup_balls, hue_circular_dist, hue_clash_pairs,
    hue_window, nearest_setup_ball, sample_ball_at_pixel,
)
from rumpus.vision.geometry import HomographyMapper


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


def _setup_mapper():
    # 400 px / m so a 43 mm ball is ~17 px — same order as a 720p overhead cam.
    return HomographyMapper(np.array([[400.0, 0, 0], [0, 400.0, 0], [0, 0, 1.0]]))


def _oak_floor(h=240, w=320):
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (42, 108, 176)  # warm oak BGR
    for y in range(h):
        img[y] = np.clip(img[y].astype(np.int16) + (y % 9) * 4, 0, 255).astype(np.uint8)
    return img


def _play_area():
    return [(-0.2, -0.2), (1.0, -0.2), (1.0, 0.8), (-0.2, 0.8)]


def test_oak_grain_is_not_an_orange_ball():
    import cv2
    img = _oak_floor()
    cv2.circle(img, (160, 120), 10, (38, 100, 168), -1)
    mapper = _setup_mapper()
    dets = detect_setup_balls(img, img.copy(), mapper, _play_area())
    assert dets == []


def test_neon_orange_ball_is_detected():
    import cv2
    img = _oak_floor()
    cv2.circle(img, (160, 120), 10, (0, 110, 255), -1)
    mapper = _setup_mapper()
    dets = detect_setup_balls(img, _oak_floor(), mapper, _play_area())
    assert any(d["hue_name"] == "orange" for d in dets)


def test_click_selects_nearby_white_ball_not_the_plank():
    import cv2
    img = _oak_floor()
    cv2.circle(img, (160, 120), 10, (235, 236, 232), -1)
    mapper = _setup_mapper()
    assert sample_ball_at_pixel(img, 40, 40) is None
    dets = detect_setup_balls(img, _oak_floor(), mapper, _play_area(), loose=True)
    hit = nearest_setup_ball(dets, mapper, 168, 124, max_px=64)
    assert hit is not None
    assert hit["hue_name"] in ("white", "orange")


# --------------------------------------------------------------------------- #
# Color-only motion-first tracker (synthetic sequences)
# --------------------------------------------------------------------------- #

ORANGE_BGR = (0, 110, 255)
PINK_BGR = (180, 50, 255)


def _color_mapper():
    return HomographyMapper(np.array([[400.0, 0, 40.0], [0, 400.0, 40.0], [0, 0, 1.0]]))


def _color_play():
    return [(-0.05, -0.05), (3.2, -0.05), (3.2, 0.95), (-0.05, 0.95)]


def _blank_floor(h=400, w=1360):
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (42, 108, 176)
    return img


def _paint_ball(img, mapper, pos, bgr, rx_m=0.022, ry_m=None):
    import cv2
    u, v = mapper.floor_to_pixel(pos[0], pos[1])
    rx = max(5, int(round(mapper.radius_to_pixels(pos[0], pos[1], rx_m))))
    ry = rx if ry_m is None else max(3, int(round(mapper.radius_to_pixels(pos[0], pos[1], ry_m))))
    cv2.ellipse(img, (int(round(u)), int(round(v))), (rx, ry), 0, 0, 360, bgr, -1)


def _warm_mog(tracker, mapper, ref, play, now):
    for _ in range(24):
        now += 1.0 / 30.0
        tracker.update(ref.copy(), None, mapper, None, None, [], now,
                       play_area=play, reference_color=ref)
    return now


def _orange_tracker():
    t = BallTracker()
    t.add_ball("ball0", "p0", "#ff8a3d", hue_range=(8, 24),
               hue_center=14.0, sat_floor=80, val_floor=120)
    return t


def test_color_putt_coasts_through_dropout():
    """(a) 2.5 m/s with 40% dropout: coast error < 15 cm, not lost, one stroke."""
    mapper = _color_mapper()
    play = _color_play()
    ref = _blank_floor()
    tracker = _orange_tracker()
    now = time.time()
    now = _warm_mog(tracker, mapper, ref, play, now)
    x, y, v = 0.15, 0.40, 0.0
    tracker.seed_position("ball0", (x, y), now=now)
    rng = np.random.default_rng(3)
    coast_err = []
    moving = False
    strokes = 0
    stops = 0
    lost = False
    # Impulse putt, 40% dropout while rolling, then a visible rest so
    # the 0.5 s stop window no longer includes rolling samples.
    for i in range(90):
        if i < 6:
            v = min(2.5, v + 0.45)
        elif 29 <= i < 40:
            v = max(0.0, v - 0.24)
        elif i >= 40:
            v = 0.0
        x += v / 30.0
        now += 1.0 / 30.0
        drop = i >= 6 and i <= 28 and float(rng.random()) < 0.40
        frame = ref.copy()
        if not drop:
            _paint_ball(frame, mapper, (x, y), ORANGE_BGR)
        ball = tracker.balls["ball0"]
        was = ball.moving
        tracker.update(frame, None, mapper, None, None, [], now,
                       play_area=play, reference_color=ref)
        if ball.moving and not was:
            strokes += 1
        if (not ball.moving) and was and not ball.coasting:
            stops += 1
        if ball.coasting and ball.position is not None:
            coast_err.append(abs(ball.position[0] - x))
        if ball.lost:
            lost = True
        moving = ball.moving
    assert not lost
    assert coast_err == [] or max(coast_err) < 0.15
    assert strokes == 1
    assert stops == 1
    assert not moving


def test_color_brief_pause_does_not_split_stroke():
    """A short hitch mid-putt is still one stroke, not a new play."""
    mapper = _color_mapper()
    play = _color_play()
    ref = _blank_floor()
    tracker = _orange_tracker()
    now = time.time()
    now = _warm_mog(tracker, mapper, ref, play, now)
    x, y = 0.20, 0.40
    tracker.seed_position("ball0", (x, y), now=now)
    strokes = 0
    stops = 0
    # Roll, sit still for ~0.25 s, roll again, then a real rest.
    for i in range(90):
        if i < 12:
            x += 0.05
        elif i < 20:
            pass
        elif i < 36:
            x += 0.05
        now += 1.0 / 30.0
        frame = ref.copy()
        _paint_ball(frame, mapper, (x, y), ORANGE_BGR)
        ball = tracker.balls["ball0"]
        was = ball.moving
        tracker.update(frame, None, mapper, None, None, [], now,
                       play_area=play, reference_color=ref)
        if ball.moving and not was:
            strokes += 1
        if (not ball.moving) and was and not ball.coasting:
            stops += 1
    assert strokes == 1
    assert stops == 1
    assert not tracker.balls["ball0"].moving


def test_color_crossing_identities_hold():
    """(b) Two balls pass within 8 cm — identities never swap."""
    mapper = _color_mapper()
    play = _color_play()
    ref = _blank_floor()
    tracker = BallTracker()
    tracker.add_ball("ball0", "p0", "#ff8a3d", hue_range=(8, 24),
                     hue_center=14.0, sat_floor=80, val_floor=120)
    tracker.add_ball("ball1", "p1", "#ff5fa8", hue_range=(150, 8),
                     hue_center=165.0, sat_floor=80, val_floor=120)
    now = time.time()
    now = _warm_mog(tracker, mapper, ref, play, now)
    tracker.seed_position("ball0", (0.20, 0.40), now=now)
    tracker.seed_position("ball1", (1.70, 0.48), now=now)
    closest = 99.0
    for i in range(31):
        x0 = 0.20 + i * 0.05
        x1 = 1.70 - i * 0.05
        closest = min(closest, abs(x0 - x1))
        now += 1.0 / 30.0
        frame = ref.copy()
        _paint_ball(frame, mapper, (x0, 0.40), ORANGE_BGR)
        _paint_ball(frame, mapper, (x1, 0.48), PINK_BGR)
        tracker.update(frame, None, mapper, None, None, [], now,
                       play_area=play, reference_color=ref)
    assert closest < 0.09
    p0 = tracker.balls["ball0"].smoothed or tracker.balls["ball0"].position
    p1 = tracker.balls["ball1"].smoothed or tracker.balls["ball1"].position
    assert p0 is not None and p1 is not None
    assert p0[0] > p1[0]


def test_color_blurred_blob_tracks():
    """(c) Elongated 4:1 blob stays on the track."""
    mapper = _color_mapper()
    play = _color_play()
    ref = _blank_floor()
    tracker = _orange_tracker()
    now = time.time()
    now = _warm_mog(tracker, mapper, ref, play, now)
    tracker.seed_position("ball0", (0.20, 0.45), now=now)
    last = 0.20
    for i in range(24):
        x = 0.20 + i * 0.06
        now += 1.0 / 30.0
        frame = ref.copy()
        # 4:1 streak: minor axis ≈ ball diameter, major ≈ 4× (motion blur).
        _paint_ball(frame, mapper, (x, 0.45), ORANGE_BGR, rx_m=0.086, ry_m=0.0215)
        tracker.update(frame, None, mapper, None, None, [], now,
                       play_area=play, reference_color=ref)
        pos = tracker.balls["ball0"].smoothed or tracker.balls["ball0"].position
        assert pos is not None
        assert not tracker.balls["ball0"].lost
        last = pos[0]
    assert last > 1.2


def test_color_gate_rejects_same_hue_decoy():
    """(d) After a stop, a same-hue object elsewhere is not stolen."""
    mapper = _color_mapper()
    play = _color_play()
    ref = _blank_floor()
    tracker = _orange_tracker()
    now = time.time()
    now = _warm_mog(tracker, mapper, ref, play, now)
    tracker.seed_position("ball0", (0.70, 0.40), now=now)
    for _ in range(20):
        now += 1.0 / 30.0
        frame = ref.copy()
        _paint_ball(frame, mapper, (0.70, 0.40), ORANGE_BGR)
        tracker.update(frame, None, mapper, None, None, [], now,
                       play_area=play, reference_color=ref)
    rest = tracker.balls["ball0"].smoothed or tracker.balls["ball0"].position
    assert rest is not None
    for _ in range(60):
        now += 1.0 / 30.0
        frame = ref.copy()
        _paint_ball(frame, mapper, (0.70, 0.40), ORANGE_BGR)
        _paint_ball(frame, mapper, (1.70, 0.40), ORANGE_BGR)
        tracker.update(frame, None, mapper, None, None, [], now,
                       play_area=play, reference_color=ref)
    got = tracker.balls["ball0"].smoothed or tracker.balls["ball0"].position
    assert got is not None
    assert abs(got[0] - rest[0]) < 0.20
    assert got[0] < 1.20


if __name__ == "__main__":
    test_mock_lock_is_noop()
    test_gate_is_velocity_aware()
    test_hue_window_and_clash()
    test_mock_putt_tracks_without_teleport()
    test_two_ball_identities_survive_pass()
    test_oak_grain_is_not_an_orange_ball()
    test_neon_orange_ball_is_detected()
    test_click_selects_nearby_white_ball_not_the_plank()
    test_color_putt_coasts_through_dropout()
    test_color_brief_pause_does_not_split_stroke()
    test_color_crossing_identities_hold()
    test_color_blurred_blob_tracks()
    test_color_gate_rejects_same_hue_decoy()
    print("ok")
