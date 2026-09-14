"""Play-area calibration (S05) with a depth camera aimed across a room.

The floor origin is the sensor projected straight down, which for a Kinect on a
shelf is below the bottom of the picture. Everything here exists because a
preset seeded around that origin was invisible, and clicking the floor to draw
one instead did nothing at all.
"""
from __future__ import annotations

import numpy as np

from rumpus.engine import (CUP_R_MAX, CUP_R_MIN, MAX_AREA_CORNERS, GameEngine,
                           S)
from rumpus.models import FloorPlane, Frame
from rumpus.sensor.mock import MockBackend
from rumpus.vision.geometry import FloorMapper, default_camera

FEED_W, FEED_H = 640, 480


def _tilted_plane(height_m: float = 1.4, tilt_deg: float = 35.0) -> FloorPlane:
    """Floor as seen by a camera that high up, pitched that far down.

    A camera looking straight down would put the origin in the middle of the
    frame and hide the bug entirely.
    """
    t = np.radians(tilt_deg)
    # Camera at the origin looking along +z, image y pointing down. "Up" is -y
    # when the camera is level and -z when it is aimed straight at the floor, so
    # the normal swings between the two as it pitches down.
    n = np.array([0.0, -np.cos(t), -np.sin(t)])
    n = n / np.linalg.norm(n)
    plane = FloorPlane(normal=n, offset=-height_m)
    # Sanity: the floor has to end up in front of the lens and below it, or the
    # fixture is testing nothing real.
    origin = plane.offset * n
    assert origin[2] > 0 and origin[1] > 0, f"floor behind the camera: {origin}"
    return plane


def _engine(tilt_deg: float = 35.0, height_m: float = 1.4) -> GameEngine:
    e = GameEngine()
    e._save_setup = lambda: None
    backend = MockBackend()
    backend.open()
    cam = default_camera((FEED_W, FEED_H))
    e.attach_backend(backend, cam)
    e.cam = cam
    e._feed_w, e._feed_h = FEED_W, FEED_H
    plane = _tilted_plane(height_m=height_m, tilt_deg=tilt_deg)
    e.plane = plane
    e.mapper = FloorMapper(plane, cam)
    e.setup.floor_plane = plane
    # A depth frame is not needed: the ray intersection carries the geometry,
    # and this keeps the fixture honest about what the code may rely on.
    e._frame_depth = None
    e._frame_color = np.zeros((FEED_H, FEED_W, 3), np.uint8)
    return e


def _pixels(e: GameEngine) -> list[tuple[float, float]]:
    return [e._corner_feed_xy(p) for p in e._draw_poly]


def _on_screen(pts, margin: float = 0.0) -> bool:
    return all(-margin <= x <= 1 + margin and -margin <= y <= 1 + margin
               for x, y in pts)


# --------------------------------------------------------------------------- #
# The floor origin is not where the camera is looking
# --------------------------------------------------------------------------- #
def test_the_floor_origin_really_is_off_screen():
    """Guards the premise: without this the other tests prove nothing."""
    e = _engine()
    ox, oy = e._corner_feed_xy((0.0, 0.0))
    assert not (0.0 <= oy <= 1.0), (
        f"origin at {ox:.2f},{oy:.2f} is in frame, so this fixture cannot "
        f"reproduce the bug")


def test_preset_rectangle_lands_where_the_camera_can_see_it():
    e = _engine()
    e._apply_preset("small")
    pts = _pixels(e)
    assert len(pts) == 4
    assert _on_screen(pts), f"preset corners off screen: {pts}"


def _assert_usable(e: GameEngine, label: str) -> None:
    """Every corner is inside the frame with room to grab it.

    All four, not "most of them". A corner off the picture cannot be dragged,
    which is the dead end this screen kept landing in, and an area running past
    the edge of the view could not be watched for balls anyway. A size that will
    not fit gets trimmed and the trim gets announced, so demanding all four
    costs nothing real.
    """
    pts = _pixels(e)
    for i, (x, y) in enumerate(pts):
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0, (
            f"{label}: corner {i + 1} off screen at ({x:.3f}, {y:.3f})")
    assert e._area_corners_on_screen(), f"{label}: no grabbable corner"
    # Grabbable means the engine's own hit test finds it from a click on it.
    for i, (x, y) in enumerate(pts):
        assert e._nearest_area_corner(x, y) is not None, (
            f"{label}: corner {i + 1} cannot be picked up")


def test_every_preset_size_is_visible_and_adjustable():
    for name in ("small", "medium", "large"):
        e = _engine()
        e._apply_preset(name)
        _assert_usable(e, name)


def test_presets_that_fit_are_fully_on_screen():
    for name in ("small", "medium"):
        e = _engine()
        e._apply_preset(name)
        assert _on_screen(_pixels(e), margin=0.02), f"{name}: {_pixels(e)}"


def test_preset_keeps_its_aspect_and_reports_what_it_gave_you():
    """A trim may shrink the rectangle, but never distort it or hide it."""
    for name, (rw, rh) in (("small", (2.0, 1.5)), ("medium", (3.0, 2.0)),
                           ("large", (4.0, 2.5))):
        e = _engine()
        e._apply_preset(name)
        xs = [p[0] for p in e._draw_poly]
        ys = [p[1] for p in e._draw_poly]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        assert w <= rw + 1e-6 and h <= rh + 1e-6, f"{name} grew to {w}x{h}"
        assert abs(w / h - rw / rh) < 1e-6, f"{name} aspect skewed: {w}x{h}"
        # Whatever it ended up as, the screen reports the measured size.
        aw, ah = e._draft_area_size()
        assert abs(aw - w) < 1e-6 and abs(ah - h) < 1e-6
        trimmed = w < rw - 0.01 or h < rh - 0.01
        assert e._preset_trimmed == trimmed, f"{name}: trim not reported"
        if trimmed:
            assert "trimmed" in e._area_hint_text(4, f"{aw:.1f} × {ah:.1f} m")


def test_the_near_corners_of_a_preset_are_not_off_the_sides():
    """The reported dead end: "cant reach the bottom corners".

    Perspective widens the near edge, so a rectangle sitting comfortably inside
    the *bounds* of the visible floor can still push its two near corners off the
    left and right of the picture — leaving exactly the two reachable handles in
    the report. Placement is judged by projecting the corners now, rather than by
    comparing floor extents.

    Swept over mounting poses because the single pose this file used to test
    happened to be one where the bug did not show. Shallow pitches put the
    horizon inside the frame, which is where it bit.
    """
    for height in (0.7, 1.0, 1.4):
        for tilt in (8.0, 12.0, 16.0, 20.0, 25.0, 30.0):
            for name in ("small", "medium", "large"):
                e = _engine(tilt_deg=tilt, height_m=height)
                e._apply_preset(name)
                label = f"{height} m at {tilt}°, {name}"
                _assert_usable(e, label)
                near = sorted(_pixels(e), key=lambda p: -p[1])[:2]
                for x, y in near:
                    assert 0.0 <= x <= 1.0, (
                        f"{label}: near corner off the side at x={x:.3f}")


def test_trimming_is_a_last_resort_not_the_default():
    """A shrunken course would be its own bug, so most poses must keep full size."""
    full = kept = 0
    for height in (0.7, 1.0, 1.4):
        for tilt in (8.0, 12.0, 16.0, 20.0, 25.0, 30.0):
            for name, (rw, rh) in (("small", (2.0, 1.5)), ("medium", (3.0, 2.0)),
                                   ("large", (4.0, 2.5))):
                e = _engine(tilt_deg=tilt, height_m=height)
                e._apply_preset(name)
                xs = [p[0] for p in e._draw_poly]
                full += 1
                if (max(xs) - min(xs)) >= rw - 0.01:
                    kept += 1
    assert kept >= 0.9 * full, (
        f"only {kept} of {full} poses kept the requested size")


def test_start_zone_follows_the_rectangle():
    """A start circle left at the origin would sit outside the play area."""
    e = _engine()
    e._apply_preset("medium")
    xs = [p[0] for p in e._draw_poly]
    ys = [p[1] for p in e._draw_poly]
    assert min(xs) <= e.setup.start.x <= max(xs)
    assert min(ys) <= e.setup.start.y <= max(ys)


def test_a_steeper_camera_still_gets_a_visible_rectangle():
    """Mounting angle varies wildly between rooms."""
    for tilt in (20.0, 35.0, 50.0, 65.0):
        e = _engine(tilt_deg=tilt)
        e._apply_preset("small")
        _assert_usable(e, f"tilt {tilt}")


def test_a_half_reachable_saved_area_is_brought_back_into_reach():
    """The reported dead end, from a saved area rather than a preset.

    Two far corners in frame and two near ones past the sides looks like a
    working screen, so the old all-or-nothing rescue never fired and those two
    corners could not be touched.
    """
    e = _engine()
    # A wide area: far corners in view, near corners off both sides.
    e.setup.play_area = [(-2.6, 1.0), (2.6, 1.0), (2.6, 2.6), (-2.6, 2.6)]
    e._draw_poly = []
    e._set_state(S.CAL_AREA)
    pts = _pixels(e)
    assert len(pts) == 4
    for i, (x, y) in enumerate(pts):
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0, (
            f"corner {i + 1} still out of reach at ({x:.3f}, {y:.3f})")
        assert e._nearest_area_corner(x, y) is not None


def test_pulling_corners_in_leaves_the_reachable_ones_alone():
    """Only the strays move: the rest of the player's shape is theirs."""
    e = _engine()
    e._apply_preset("small")
    keep = list(e._draw_poly)
    # Shove one corner far off to the side.
    e._draw_poly[2] = (keep[2][0] + 9.0, keep[2][1])
    e._pull_area_corners_into_view()
    for i in (0, 1, 3):
        assert e._draw_poly[i] == keep[i], f"corner {i + 1} moved unasked"
    x, y = e._corner_feed_xy(e._draw_poly[2])
    assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0


def test_an_area_fully_in_view_is_untouched_on_entry():
    e = _engine()
    e._apply_preset("small")
    before = list(e._draw_poly)
    e._draw_poly = []
    e.setup.play_area = list(before)
    e._set_state(S.CAL_AREA)
    for a, b in zip(e._draw_poly, before):
        assert abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9


# --------------------------------------------------------------------------- #
# A dragged corner has to end up under the mouse
# --------------------------------------------------------------------------- #
def test_a_dragged_corner_lands_exactly_under_the_pointer():
    e = _engine()
    e._apply_preset("medium")
    for nx, ny in ((0.30, 0.60), (0.72, 0.55), (0.50, 0.88), (0.20, 0.95)):
        e._pointer_area("down", nx, ny, handle=0)
        e._pointer_area("move", nx, ny, handle=0)
        gx, gy = e._corner_feed_xy(e._draw_poly[0])
        assert abs(gx - nx) < 0.01 and abs(gy - ny) < 0.01, (
            f"asked for ({nx}, {ny}), corner drew at ({gx:.3f}, {gy:.3f})")
        e._pointer_area("up", nx, ny, handle=0)


def test_dragging_above_the_horizon_keeps_following_instead_of_freezing():
    """The reported bug: "they do not follow where my mouse pointer is".

    A ray aimed above the horizon never meets the floor, so the mapper returned
    nothing and the corner simply stayed where it was — then snapped back to that
    stale spot the moment the button came up. It now parks at the furthest floor
    the camera can see and still tracks sideways.
    """
    e = _engine()
    e._apply_preset("medium")
    e._pointer_area("down", 0.5, 0.9, handle=0)

    # Walk the pointer up past the horizon and out the top of the frame.
    seen = []
    for ny in (0.9, 0.7, 0.5, 0.3, 0.15, 0.02):
        e._pointer_area("move", 0.5, ny, handle=0)
        seen.append(e._corner_feed_xy(e._draw_poly[0]))
        assert e._draw_poly[0] is not None
    ys = [p[1] for p in seen]
    assert ys == sorted(ys, reverse=True), f"corner jumped around: {ys}"
    assert all(0.0 <= y <= 1.0 for _, y in seen), f"corner left the frame: {seen}"

    # Far above the horizon it must still answer sideways movement.
    e._pointer_area("move", 0.25, 0.02, handle=0)
    left = e._corner_feed_xy(e._draw_poly[0])
    e._pointer_area("move", 0.75, 0.02, handle=0)
    right = e._corner_feed_xy(e._draw_poly[0])
    assert right[0] > left[0] + 0.05, (
        f"sideways drag ignored above the horizon: {left} then {right}")


def test_a_corner_can_never_be_dragged_out_of_reach():
    """Whatever the player does, the corner stays pickable next time."""
    e = _engine()
    e._apply_preset("medium")
    for nx, ny in ((-0.5, -0.5), (1.5, 1.5), (0.0, 0.0), (1.0, 1.0),
                   (-3.0, 0.5), (0.5, 9.0)):
        e._pointer_area("down", nx, ny, handle=2)
        e._pointer_area("move", nx, ny, handle=2)
        e._pointer_area("up", nx, ny, handle=2)
        gx, gy = e._corner_feed_xy(e._draw_poly[2])
        assert 0.0 <= gx <= 1.0 and 0.0 <= gy <= 1.0, (
            f"drag to ({nx}, {ny}) parked the corner off screen at "
            f"({gx:.3f}, {gy:.3f})")
        assert e._nearest_area_corner(gx, gy) is not None


def test_releasing_a_drag_does_not_move_the_corner():
    """The snap-back: what you let go of is where it stays."""
    e = _engine()
    e._apply_preset("medium")
    e._pointer_area("down", 0.4, 0.7, handle=1)
    e._pointer_area("move", 0.62, 0.82, handle=1)
    before = e._corner_feed_xy(e._draw_poly[1])
    e._pointer_area("up", 0.62, 0.82, handle=1)
    after = e._corner_feed_xy(e._draw_poly[1])
    assert abs(after[0] - before[0]) < 1e-9 and abs(after[1] - before[1]) < 1e-9
    assert abs(after[0] - 0.62) < 0.01 and abs(after[1] - 0.82) < 0.01


# --------------------------------------------------------------------------- #
# Placing the hole (S08)
# --------------------------------------------------------------------------- #
def _cup_engine() -> GameEngine:
    e = _engine(tilt_deg=20.0, height_m=1.0)
    e._apply_preset("medium")
    e.state = S.CAL_CUP
    e._cup_searching = False
    return e


def _cup_xy(e: GameEngine) -> tuple[float, float]:
    return e._corner_feed_xy((e.setup.hole.x, e.setup.hole.y))


def test_clicking_the_floor_puts_the_hole_under_the_cursor():
    e = _cup_engine()
    for nx, ny in ((0.45, 0.70), (0.62, 0.55), (0.30, 0.85), (0.70, 0.90)):
        e._pointer_cup("down", nx, ny)
        e._pointer_cup("up", nx, ny)
        hx, hy = _cup_xy(e)
        assert abs(hx - nx) < 0.01 and abs(hy - ny) < 0.01, (
            f"clicked ({nx}, {ny}), ring drew at ({hx:.3f}, {hy:.3f})")


def test_clicking_inside_the_zone_still_moves_it():
    """The reported bug: "cant position it over the actual hole".

    A click inside the ring armed a drag and moved nothing, so clicking straight
    on the real cup left the ring where it was. The bigger the zone, the more of
    the picture behaved that way — and an unclamped resize could make it most of
    the floor.
    """
    e = _cup_engine()
    e._pointer_cup("down", 0.50, 0.75)
    e._pointer_cup("up", 0.50, 0.75)
    e._nudge_cup_r(CUP_R_MAX)         # a big zone, so "inside" is a wide target
    e._pointer_cup("up", 0.50, 0.75)
    start = _cup_xy(e)

    # A click just inside the ring, nowhere near the resize dot.
    target = (start[0] - 0.02, start[1] + 0.02)
    e._pointer_cup("down", target[0], target[1])
    e._pointer_cup("up", target[0], target[1])
    moved = _cup_xy(e)
    assert abs(moved[0] - target[0]) < 0.01 and abs(moved[1] - target[1]) < 0.01, (
        f"click inside the zone did not move it: {start} -> {moved}")


def test_the_zone_cannot_be_dragged_to_an_absurd_size():
    """61 cm across in the report. The keyboard clamped; the drag did not."""
    e = _cup_engine()
    e._pointer_cup("down", 0.5, 0.8)
    e._pointer_cup("up", 0.5, 0.8)
    cx, cy = _cup_xy(e)
    for far in (0.1, 0.25, 0.45, 0.9):
        e._pointer_cup("down", cx, cy)          # grab, then reach for the edge
        e._dragging = ("circle", "radius")
        e._draw_circle_center = (e.setup.hole.x, e.setup.hole.y)
        e._pointer_cup("move", min(0.98, cx + far), cy)
        e._pointer_cup("up", min(0.98, cx + far), cy)
        r = e.setup.hole.r
        assert CUP_R_MIN <= r <= CUP_R_MAX, f"dragged to r={r:.3f} m"
    assert round(e.setup.hole.r * 200) <= 36, "zone diameter still over 36 cm"


def test_grabbing_the_cream_dot_resizes_instead_of_moving():
    e = _cup_engine()
    e._pointer_cup("down", 0.5, 0.8)
    e._pointer_cup("up", 0.5, 0.8)
    before = (e.setup.hole.x, e.setup.hole.y)
    cx, cy = _cup_xy(e)
    ru = e.mapper.radius_to_pixels(before[0], before[1], e.setup.hole.r) / FEED_W
    e._pointer_cup("down", cx + ru, cy)
    assert e._dragging == ("circle", "radius"), f"got {e._dragging}"
    assert abs(e.setup.hole.x - before[0]) < 1e-9, "resize moved the hole"
    assert abs(e.setup.hole.y - before[1]) < 1e-9, "resize moved the hole"


def test_releasing_never_drags_the_hole_back_across_the_floor():
    """A leftover centre used to be committed on any later release."""
    e = _cup_engine()
    e._pointer_cup("down", 0.35, 0.85)
    e._pointer_cup("up", 0.35, 0.85)
    # Resize once, which is what used to leave a centre and radius lying around.
    cx, cy = _cup_xy(e)
    ru = e.mapper.radius_to_pixels(e.setup.hole.x, e.setup.hole.y, e.setup.hole.r) / FEED_W
    e._pointer_cup("down", cx + ru, cy)
    e._pointer_cup("move", cx + ru + 0.04, cy)
    e._pointer_cup("up", cx + ru + 0.04, cy)
    assert e._draw_circle_center is None and e._draw_circle_r == 0.0

    # Now place it somewhere else. It must stay put.
    e._pointer_cup("down", 0.70, 0.60)
    e._pointer_cup("up", 0.70, 0.60)
    hx, hy = _cup_xy(e)
    assert abs(hx - 0.70) < 0.01 and abs(hy - 0.60) < 0.01, (
        f"hole snapped back to ({hx:.3f}, {hy:.3f})")


def test_a_hand_placed_zone_is_not_labelled_low_confidence():
    """"confidence 0.00" beside your own click reads as a detection failure."""
    e = _cup_engine()
    e._pointer_cup("down", 0.5, 0.8)
    e._pointer_cup("up", 0.5, 0.8)
    labels = [s.get("label") for s in e._overlay_snapshot()["shapes"]
              if s.get("id") == "hole"]
    assert labels and labels[0], "hole zone has no label"
    assert "confidence 0.00" not in labels[0], labels[0]
    assert "placed by hand" in labels[0], labels[0]

    e._cup_confidence = 0.87
    labels = [s.get("label") for s in e._overlay_snapshot()["shapes"]
              if s.get("id") == "hole"]
    assert "confidence 0.87" in labels[0], labels[0]


def test_the_other_floor_screens_also_answer_a_click_near_the_horizon():
    """Same dead end, different steps: the cup and obstacle handles.

    All of them mapped a click straight through the plane intersection and gave
    up on None, so a click above the horizon did nothing at all.
    """
    e = _engine(tilt_deg=12.0, height_m=1.0)
    e._apply_preset("medium")
    horizon_ny = 0.10          # well above the floor at this pose

    e.state = S.CAL_CUP
    e._pointer_cup("down", 0.5, horizon_ny)
    assert e.setup.hole is not None, "click above the horizon placed no cup"
    hx, hy = e._corner_feed_xy((e.setup.hole.x, e.setup.hole.y))
    assert 0.0 <= hx <= 1.0 and 0.0 <= hy <= 1.0, f"cup off screen at {hx}, {hy}"


# --------------------------------------------------------------------------- #
# Drawing an outline by clicking
# --------------------------------------------------------------------------- #
def test_clicking_the_floor_starts_a_new_outline_over_a_preset():
    """The reported bug: a click on open floor was silently discarded."""
    e = _engine()
    e._apply_preset("medium")
    assert len(e._draw_poly) == 4
    e._pointer_area("down", 0.45, 0.62)
    assert len(e._draw_poly) == 1, "click on open floor did nothing"
    e._pointer_area("up", 0.45, 0.62)
    for x, y in ((0.7, 0.62), (0.7, 0.85), (0.45, 0.85)):
        e._pointer_area("down", x, y)
        e._pointer_area("up", x, y)
    assert len(e._draw_poly) == 4


def test_a_depth_outline_can_have_more_than_four_corners():
    """The screen has always advertised "Close shape"; L-shaped rooms need it."""
    e = _engine()
    e._area_action("clear")
    hexagon = ((0.30, 0.55), (0.55, 0.50), (0.80, 0.58),
               (0.82, 0.85), (0.52, 0.92), (0.25, 0.82))
    for x, y in hexagon:
        e._pointer_area("down", x, y)
        e._pointer_area("up", x, y)
    assert len(e._draw_poly) == 6


def test_a_new_corner_can_go_down_beside_an_existing_one():
    """The old grab radius was a fifth of the frame, so this was impossible."""
    e = _engine()
    e._area_action("clear")
    e._pointer_area("down", 0.5, 0.7)
    e._pointer_area("up", 0.5, 0.7)
    e._pointer_area("down", 0.58, 0.7)
    e._pointer_area("up", 0.58, 0.7)
    assert len(e._draw_poly) == 2, "second click was swallowed as a drag"


def test_corner_count_is_capped():
    """An unbounded click count is a way to wreck the screen by accident."""
    e = _engine()
    e._area_action("clear")
    rng = np.random.default_rng(7)
    for _ in range(MAX_AREA_CORNERS + 20):
        e._pointer_area("down", float(rng.uniform(0.1, 0.9)),
                        float(rng.uniform(0.45, 0.95)))
        e._pointer_area("up", 0.5, 0.7)
    assert len(e._draw_poly) <= MAX_AREA_CORNERS


def test_close_shape_stops_adding_corners():
    """Y / Close shape was wired to nothing at all."""
    e = _engine()
    e._area_action("clear")
    for x, y in ((0.35, 0.6), (0.65, 0.6), (0.65, 0.9), (0.35, 0.9)):
        e._pointer_area("down", x, y)
        e._pointer_area("up", x, y)
    n = len(e._draw_poly)
    assert n == 4
    e._area_action("secondary")
    assert e._area_open is False
    # Far from any corner, so it would have been a new one before.
    e._pointer_area("down", 0.05, 0.95)
    assert len(e._draw_poly) == n, "Close shape did not close the outline"


def test_close_shape_needs_a_real_outline():
    e = _engine()
    e._area_action("clear")
    e._pointer_area("down", 0.4, 0.7)
    e._area_action("secondary")
    assert e._area_open is True, "closed a shape with a single corner"


def test_dragging_a_preset_corner_moves_only_that_corner():
    e = _engine()
    e._apply_preset("medium")
    before = list(e._draw_poly)
    cx, cy = e._corner_feed_xy(before[0])
    e._pointer_area("down", cx, cy)
    e._pointer_area("move", cx + 0.05, cy + 0.05)
    e._pointer_area("up", cx + 0.05, cy + 0.05)
    assert len(e._draw_poly) == 4
    assert e._draw_poly[0] != before[0]
    assert e._draw_poly[1:] == before[1:], "dragging one corner moved others"


def test_grabbing_a_corner_does_not_restart_the_outline():
    """Dragging must never be read as "draw a new one"."""
    e = _engine()
    e._apply_preset("small")
    cx, cy = e._corner_feed_xy(e._draw_poly[2])
    e._pointer_area("down", cx, cy)
    assert len(e._draw_poly) == 4
    assert e._area_from_preset is False


def test_clear_then_click_draws_from_scratch():
    e = _engine()
    e._apply_preset("large")
    e._area_action("clear")
    assert e._draw_poly == []
    e._pointer_area("down", 0.5, 0.7)
    assert len(e._draw_poly) == 1


def test_undo_reopens_a_closed_outline():
    e = _engine()
    e._apply_preset("medium")
    e._area_action("undo")
    assert len(e._draw_poly) == 3
    e._pointer_area("down", 0.1, 0.95)
    assert len(e._draw_poly) == 4, "undo left the outline closed"


def test_an_offscreen_saved_area_can_still_be_redrawn():
    """If the sensor moved, no corner is grabbable — do not dead-end."""
    e = _engine()
    # Straight from a saved setup, sitting where the camera no longer looks.
    e.setup.play_area = [(-0.9, -8.0), (0.9, -8.0), (0.9, -6.5), (-0.9, -6.5)]
    e._seed_area_from_setup(force=True)
    assert not e._area_corners_on_screen()
    e._pointer_area("down", 0.5, 0.7)
    assert len(e._draw_poly) == 1, "no way to recover from an off-screen area"


def test_entering_the_screen_rescues_an_offscreen_saved_area():
    """Reopening calibration after moving the sensor must not show a blank feed."""
    e = _engine()
    e._apply_preset("large")          # remember the size the player wanted
    e.setup.play_area = [(-2.0, -9.0), (2.0, -9.0), (2.0, -6.5), (-2.0, -6.5)]
    e._draw_poly = []
    e._set_state(S.CAL_AREA)
    assert e._area_corners_on_screen(), "still nothing on screen to work with"
    _assert_usable(e, "rescued")
    xs = [p[0] for p in e._draw_poly]
    assert abs((max(xs) - min(xs)) - 4.0) < 1e-6, "lost the chosen preset size"


def test_an_onscreen_saved_area_survives_entering_the_screen():
    """Only a useless outline gets replaced — a good one is left alone."""
    e = _engine()
    e._apply_preset("medium")
    e.setup.play_area = list(e._draw_poly)
    kept = list(e._draw_poly)
    e._draw_poly = []
    e._set_state(S.CAL_AREA)
    assert e._draw_poly == kept


def test_an_onscreen_saved_area_is_adjusted_not_replaced():
    """A deliberate outline must not be thrown away by one stray click."""
    e = _engine()
    e._apply_preset("medium")
    e.setup.play_area = list(e._draw_poly)
    e._draw_poly = []
    e._seed_area_from_setup(force=True)
    assert e._area_corners_on_screen()
    n = len(e._draw_poly)
    e._pointer_area("down", 0.5, 0.5)
    assert len(e._draw_poly) == n


# --------------------------------------------------------------------------- #
# Handles: how big, and do they land under the cursor
# --------------------------------------------------------------------------- #
def test_handle_size_does_not_depend_on_sensor_resolution():
    """A 640-wide Kinect drew every marker 3x the size a 1920 webcam did."""
    small = _engine()
    small._feed_w, small._feed_h = 640, 480
    big = _engine()
    big._feed_w, big._feed_h = 1920, 1080
    assert small._px(18) == big._px(18)


def test_corner_handles_are_a_sane_fraction_of_the_picture():
    """18 design px in a 1920 space is about 1%, not 3%."""
    e = _engine()
    r = e._px(18)
    assert 0.005 < r < 0.015, f"handle radius {r:.4f} of the frame width"


def test_a_clicked_corner_lands_exactly_under_the_cursor():
    """Projecting the stored floor point back must return the clicked pixel.

    Intersecting the ray with the floor plane round-trips exactly. Reading the
    measured depth at the pixel does not, which is why corners drifted off the
    click.
    """
    e = _engine()
    e._area_action("clear")
    for nx, ny in ((0.31, 0.58), (0.72, 0.61), (0.66, 0.93), (0.24, 0.88)):
        e._pointer_area("down", nx, ny)
        e._pointer_area("up", nx, ny)
        cx, cy = e._corner_feed_xy(e._draw_poly[-1])
        assert abs(cx - nx) < 1e-6 and abs(cy - ny) < 1e-6, (
            f"clicked {nx:.3f},{ny:.3f} but corner sits at {cx:.3f},{cy:.3f}")


def test_a_dragged_corner_follows_the_cursor_exactly():
    e = _engine()
    e._apply_preset("medium")
    cx, cy = e._corner_feed_xy(e._draw_poly[0])
    e._pointer_area("down", cx, cy)
    for nx, ny in ((0.30, 0.70), (0.35, 0.75), (0.42, 0.80)):
        e._pointer_area("move", nx, ny)
        gx, gy = e._corner_feed_xy(e._draw_poly[0])
        assert abs(gx - nx) < 1e-6 and abs(gy - ny) < 1e-6, (
            f"dragged to {nx:.3f},{ny:.3f}, handle at {gx:.3f},{gy:.3f}")


def test_something_standing_on_the_floor_cannot_capture_a_corner():
    """A leg or table between the lens and the floor used to steal the point."""
    e = _engine()
    # Everything reads as 0.8 m away: an object right in front of the sensor.
    e._frame_depth = np.full((FEED_H, FEED_W), 800.0, np.float32)
    e._area_action("clear")
    nx, ny = 0.5, 0.75
    e._pointer_area("down", nx, ny)
    cx, cy = e._corner_feed_xy(e._draw_poly[0])
    assert abs(cx - nx) < 1e-6 and abs(cy - ny) < 1e-6, (
        f"corner pulled to {cx:.3f},{cy:.3f} by the occluder")


def test_corner_placement_is_stable_under_depth_noise():
    """Two clicks on the same pixel must give the same floor point."""
    rng = np.random.default_rng(11)
    e = _engine()
    e._area_action("clear")
    e._frame_depth = (np.full((FEED_H, FEED_W), 2500.0, np.float32)
                      + rng.normal(0, 15, (FEED_H, FEED_W)).astype(np.float32))
    e._pointer_area("down", 0.5, 0.8)
    first = e._draw_poly[0]
    e._frame_depth = (np.full((FEED_H, FEED_W), 2500.0, np.float32)
                      + rng.normal(0, 15, (FEED_H, FEED_W)).astype(np.float32))
    e._set_area_corner(0, 0.5, 0.8)
    assert e._draw_poly[0] == first, "same pixel gave a different floor point"


# --------------------------------------------------------------------------- #
# What the player is told
# --------------------------------------------------------------------------- #
def test_overlay_reports_the_measured_size_not_the_preset():
    """Drag a corner in and the label has to follow, or it is just wrong."""
    e = _engine()
    e._apply_preset("medium")
    assert "3.0 × 2.0 m" in e._overlay_cal_area()["shapes"][0]["text"]
    # Halve the width by hand.
    e._draw_poly = [(x / 2 if i in (1, 2) else x, y)
                    for i, (x, y) in enumerate(e._draw_poly)]
    e._area_from_preset = False
    txt = e._overlay_cal_area()["shapes"][0]["text"]
    assert "3.0 ×" not in txt, f"still quoting the preset size: {txt}"


def test_overlay_tells_you_both_ways_to_use_a_preset():
    e = _engine()
    e._apply_preset("small")
    txt = e._overlay_cal_area()["shapes"][0]["text"]
    assert "drag" in txt.lower() and "draw your own" in txt.lower()


def test_overlay_prompts_for_the_first_click_when_empty():
    e = _engine()
    e._area_action("clear")
    txt = e._overlay_cal_area()["shapes"][0]["text"]
    assert "click" in txt.lower()
    assert "of 4 corners" not in txt, "depth mode is not limited to four"


def test_confirm_accepts_a_hand_drawn_outline():
    e = _engine()
    e._set_state(S.CAL_AREA)
    e._area_action("clear")
    for x, y in ((0.35, 0.6), (0.65, 0.6), (0.65, 0.88), (0.35, 0.88)):
        e._pointer_area("down", x, y)
        e._pointer_area("up", x, y)
    e._area_action("confirm")
    assert len(e.setup.play_area) == 4
    assert e.state == S.CAL_COURSE


def test_color_only_still_needs_exactly_four_corners():
    """The homography is solved from four points — a fifth must not be added."""
    e = _engine()

    class ColorOnlyMock(MockBackend):
        has_depth = False

    e.backend.__class__ = ColorOnlyMock
    assert e.is_color_only
    e._area_action("clear")
    for x, y in ((0.2, 0.3), (0.8, 0.3), (0.8, 0.9), (0.2, 0.9)):
        e._pointer_area("down", x, y)
        e._pointer_area("up", x, y)
    assert len(e._draw_poly) == 4
    e._pointer_area("down", 0.5, 0.05)
    assert len(e._draw_poly) == 4


if __name__ == "__main__":
    test_the_floor_origin_really_is_off_screen()
    test_preset_rectangle_lands_where_the_camera_can_see_it()
    test_every_preset_size_is_visible_and_adjustable()
    test_presets_that_fit_are_fully_on_screen()
    test_preset_keeps_its_aspect_and_reports_what_it_gave_you()
    test_the_near_corners_of_a_preset_are_not_off_the_sides()
    test_trimming_is_a_last_resort_not_the_default()
    test_start_zone_follows_the_rectangle()
    test_a_steeper_camera_still_gets_a_visible_rectangle()
    test_a_half_reachable_saved_area_is_brought_back_into_reach()
    test_pulling_corners_in_leaves_the_reachable_ones_alone()
    test_an_area_fully_in_view_is_untouched_on_entry()
    test_a_dragged_corner_lands_exactly_under_the_pointer()
    test_dragging_above_the_horizon_keeps_following_instead_of_freezing()
    test_a_corner_can_never_be_dragged_out_of_reach()
    test_releasing_a_drag_does_not_move_the_corner()
    test_clicking_the_floor_puts_the_hole_under_the_cursor()
    test_clicking_inside_the_zone_still_moves_it()
    test_the_zone_cannot_be_dragged_to_an_absurd_size()
    test_grabbing_the_cream_dot_resizes_instead_of_moving()
    test_releasing_never_drags_the_hole_back_across_the_floor()
    test_a_hand_placed_zone_is_not_labelled_low_confidence()
    test_the_other_floor_screens_also_answer_a_click_near_the_horizon()
    test_clicking_the_floor_starts_a_new_outline_over_a_preset()
    test_a_depth_outline_can_have_more_than_four_corners()
    test_a_new_corner_can_go_down_beside_an_existing_one()
    test_corner_count_is_capped()
    test_close_shape_stops_adding_corners()
    test_close_shape_needs_a_real_outline()
    test_dragging_a_preset_corner_moves_only_that_corner()
    test_grabbing_a_corner_does_not_restart_the_outline()
    test_clear_then_click_draws_from_scratch()
    test_undo_reopens_a_closed_outline()
    test_an_offscreen_saved_area_can_still_be_redrawn()
    test_entering_the_screen_rescues_an_offscreen_saved_area()
    test_an_onscreen_saved_area_survives_entering_the_screen()
    test_an_onscreen_saved_area_is_adjusted_not_replaced()
    test_handle_size_does_not_depend_on_sensor_resolution()
    test_corner_handles_are_a_sane_fraction_of_the_picture()
    test_a_clicked_corner_lands_exactly_under_the_cursor()
    test_a_dragged_corner_follows_the_cursor_exactly()
    test_something_standing_on_the_floor_cannot_capture_a_corner()
    test_corner_placement_is_stable_under_depth_noise()
    test_overlay_reports_the_measured_size_not_the_preset()
    test_overlay_tells_you_both_ways_to_use_a_preset()
    test_overlay_prompts_for_the_first_click_when_empty()
    test_confirm_accepts_a_hand_drawn_outline()
    test_color_only_still_needs_exactly_four_corners()
    print("ok")
