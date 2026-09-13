"""Hardware-free checks for the Kinect path.

The sensor is driven through Microsoft's Kinect for Windows runtime, so the
interesting logic is depth decoding, depth-to-color registration, backend
selection, and telling the player which piece is missing. All of that is
testable without a Kinect attached.
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np

import rumpus.sensor as sensor
from rumpus.sensor import kinect_doctor as kd
from rumpus.sensor import kinect_nui as kn


# --------------------------------------------------------------------------- #
# Depth decoding
# --------------------------------------------------------------------------- #
def test_packed_depth_is_detected_and_unpacked():
    """Guessing the player-index shift wrong scales the whole world by 8."""
    mm = np.full((16, 16), 1500, np.uint16)
    assert kn.depth_shift_for(mm << 3) == 3
    assert kn.depth_shift_for(mm) == 0


def test_depth_shift_ignores_empty_frames():
    """An all-zero frame carries no evidence, so do not invent a shift."""
    assert kn.depth_shift_for(np.zeros((8, 8), np.uint16)) == 0


def test_depth_shift_picks_the_reading_inside_the_sensor_range():
    """A 6 m room packed into 16 bits only makes sense one way round."""
    mm = np.full((32, 32), 6000, np.uint16)
    # Unshifted this reads 48000 mm, which the sensor cannot see.
    assert kn.depth_shift_for(mm << 3) == 3


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def test_registration_shifts_depth_onto_the_color_camera():
    """The RGB and IR cameras are ~2.5 cm apart, so depth must move to match.

    Without this the whole depth image sits about 13 px off at 1 m, and every
    ball would be sampled against the floor beside it.
    """
    reg = kn._Registrar()
    flat = np.full((kn.DEPTH_H, kn.DEPTH_W), 1000, np.uint16)
    out = reg(flat)
    assert out.dtype == np.float32
    assert out.shape == (kn.COLOR_H, kn.COLOR_W)
    # A flat wall stays a flat wall at the same distance.
    filled = out[out > 0]
    assert filled.size > 0.6 * out.size
    assert abs(float(np.median(filled)) - 1000.0) < 1.0

    # The mapping is a real shift, not the identity.
    src = np.zeros((kn.DEPTH_H, kn.DEPTH_W), np.uint16)
    src[240, 320] = 1000
    moved = reg(src)
    ys, xs = np.nonzero(moved)
    assert ys.size > 0, "the point vanished"
    # ~13 px of horizontal disparity at 1 m, and the vertical offset from the
    # two cameras' different principal points.
    assert 6 <= abs(int(xs[0]) - 320) <= 22, f"x moved {xs[0] - 320}"


def test_registration_disparity_falls_off_as_one_over_depth():
    """Parallax from the RGB/IR baseline is proportional to 1/Z.

    Not measured against the source pixel: the two cameras have different
    principal points and focal lengths, so even an infinitely distant point
    lands somewhere else entirely. What must shrink with distance is the
    *change* from one depth to the next.
    """
    reg = kn._Registrar()

    def column_at(mm):
        src = np.zeros((kn.DEPTH_H, kn.DEPTH_W), np.uint16)
        src[240, 320] = mm
        ys, xs = np.nonzero(reg(src))
        assert ys.size, f"point at {mm} mm vanished"
        return int(xs[0])

    p = [column_at(z) for z in (700, 1400, 2800, 5600)]
    steps = [abs(p[i + 1] - p[i]) for i in range(len(p) - 1)]
    assert steps[0] > steps[1] > steps[2], f"parallax steps {steps} not 1/Z"


def test_registration_keeps_the_nearer_surface():
    """Where two depths land on one pixel the ball must win, not the floor."""
    reg = kn._Registrar()
    # Two columns whose disparities collide: a near object and the wall behind.
    src = np.full((kn.DEPTH_H, kn.DEPTH_W), 3000, np.uint16)
    src[200:280, 300:360] = 800
    out = reg(src)
    # The near patch should survive as a solid block, not be shot through with
    # background pixels.
    ys, xs = np.nonzero(out == 800)
    assert ys.size > 0
    block = out[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    assert (block == 800).mean() > 0.9, "background punched holes in the object"


def test_registration_leaves_no_holes_across_a_flat_surface():
    """A bare scatter leaves a lattice of gaps that reads as broken geometry."""
    reg = kn._Registrar()
    out = reg(np.full((kn.DEPTH_H, kn.DEPTH_W), 1500, np.uint16))
    middle = out[120:360, 160:480]
    assert (middle > 0).mean() > 0.97, f"coverage {(middle > 0).mean():.2%}"


def test_registration_drops_invalid_depth():
    """Zero means "no reading" and must not project to a point at the lens."""
    reg = kn._Registrar()
    out = reg(np.zeros((kn.DEPTH_H, kn.DEPTH_W), np.uint16))
    assert not out.any()


# --------------------------------------------------------------------------- #
# Backend selection
# --------------------------------------------------------------------------- #
def test_v1_prefers_the_microsoft_runtime_over_libfreenect():
    """libfreenect needs the working KinectCamera driver replaced. Avoid it."""
    order: list[str] = []

    class FakeNui:
        def open(self):
            order.append("nui")
            return True

    def boom():
        order.append("freenect")
        raise AssertionError("libfreenect must not be tried when NUI works")

    with patch.dict("sys.modules", {}, clear=False), \
         patch("rumpus.sensor.kinect_nui.KinectNuiBackend", FakeNui), \
         patch("rumpus.sensor.kinect_v1.KinectV1Backend", boom):
        got = sensor._try_open("v1")
    assert isinstance(got, FakeNui)
    assert order == ["nui"]


def test_v1_falls_back_to_libfreenect_when_the_runtime_is_absent():
    """A machine with libfreenect set up properly should still work."""
    class DeadNui:
        def open(self):
            return False

    class LiveFreenect:
        def open(self):
            return True

    with patch("rumpus.sensor.kinect_nui.KinectNuiBackend", DeadNui), \
         patch("rumpus.sensor.kinect_v1.KinectV1Backend", LiveFreenect):
        got = sensor._try_open("v1")
    assert isinstance(got, LiveFreenect)


def test_unusable_kinect_explains_itself_and_falls_back_to_the_webcam():
    """A detected-but-unopenable Kinect must not look like "no camera"."""
    fake_webcam = object()
    with patch.object(sensor.detect, "detect_sensor", return_value="v1"), \
         patch.object(sensor, "_try_open", return_value=None), \
         patch("rumpus.sensor.kinect_nui.sensor_count", return_value=0), \
         patch.object(sensor, "_open_webcam", return_value=fake_webcam):
        report: dict = {}
        backend, kind = sensor.create_backend(
            backend_mode="kinect", allow_mock=True, report=report,
        )
    # The 2D camera, not the mock: a simulated scene cannot be putted on.
    assert backend is fake_webcam and kind == "webcam"
    reason = report.get("reason", "")
    assert "Kinect v1" in reason
    # The runtime cannot see it, so power/driver is the thing to check.
    assert "12 V" in reason or "Device Manager" in reason


def test_reason_distinguishes_a_visible_sensor_that_will_not_stream():
    """Runtime sees it but streams fail — a different problem, different fix."""
    with patch.object(sensor.detect, "detect_sensor", return_value="v1"), \
         patch.object(sensor, "_try_open", return_value=None), \
         patch("rumpus.sensor.kinect_nui.sensor_count", return_value=1), \
         patch.object(sensor, "_open_webcam", return_value=None):
        report: dict = {}
        sensor.create_backend(backend_mode="kinect", allow_mock=False,
                              report=report)
    reason = report.get("reason", "")
    assert "streams would not start" in reason


def test_kinect_absent_from_usb_names_the_power_adapter():
    """The camera never enumerates on bus power alone — say so."""
    with patch.object(sensor.detect, "detect_sensor", return_value=None), \
         patch.object(sensor, "_try_open", return_value=None), \
         patch.object(sensor, "_open_webcam", return_value=None):
        report: dict = {}
        backend, kind = sensor.create_backend(
            backend_mode="kinect", allow_mock=False, report=report,
        )
    assert backend is None and kind == "none"
    assert "12 V" in report.get("reason", "")


# --------------------------------------------------------------------------- #
# The diagnostic
# --------------------------------------------------------------------------- #
def _doctor(devices, pids, kind="v1", readers=None):
    pairs = [(0x045E, p) for p in pids]
    stack = [
        patch.object(kd.detect, "detect_sensor", return_value=kind),
        patch.object(kd.detect, "list_usb", return_value=pairs),
        patch.object(kd, "_windows_devices", return_value=devices),
        patch.object(kd.platform, "system", return_value="Windows"),
    ]
    if readers is not None:
        stack.append(patch.object(kd, "_readers", return_value=readers))
    for p in stack:
        p.start()
    try:
        return "\n".join(kd.report())
    finally:
        for p in reversed(stack):
            p.stop()


def test_doctor_blames_power_when_only_the_motor_enumerates():
    """Motor but no camera is the signature of a Kinect v1 on bus power."""
    motor = {"vid": 0x045E, "pid": 0x02B0, "status": "Error",
             "name": "Xbox NUI Motor", "problem": 28}
    text = _doctor([motor], [0x02B0])
    assert "Xbox NUI Camera" in text and "absent" in text
    assert "12 V power adapter" in text
    # The driver state it did find should be words, not a bare number.
    assert "no driver installed" in text


def test_doctor_points_at_the_runtime_when_nothing_can_read_the_camera():
    """The fix is Microsoft's runtime, not a destructive Zadig driver swap."""
    devs = [{"vid": 0x045E, "pid": 0x02AE, "status": "OK",
             "name": "Xbox NUI Camera", "problem": 0}]
    text = _doctor(devs, [0x02AE], readers=[
        ("Kinect for Windows runtime (Kinect10.dll)", False, "absent"),
        ("libfreenect binding (freenect)", False, "missing"),
    ])
    assert "12 V power adapter" not in text
    assert "Kinect for Windows runtime" in text
    assert "Zadig" in text, "should warn against replacing the working driver"


def test_doctor_reports_ready_when_the_runtime_can_see_it():
    """A healthy sensor should not be reported as needing freenect."""
    devs = [{"vid": 0x045E, "pid": 0x02AE, "status": "OK",
             "name": "Xbox NUI Camera", "problem": 0},
            {"vid": 0x045E, "pid": 0x02B0, "status": "OK",
             "name": "Xbox NUI Motor", "problem": 0}]
    text = _doctor(devs, [0x02AE, 0x02B0], readers=[
        ("Kinect for Windows runtime (Kinect10.dll)", True, "1 sensor(s)"),
        ("libfreenect binding (freenect)", False, "missing"),
    ])
    assert "Everything needed is present" in text
    assert "--prefer kinect" in text


def test_doctor_says_no_device_without_guessing():
    with patch.object(kd.detect, "detect_sensor", return_value=None), \
         patch.object(kd.detect, "list_usb", return_value=[(0x1234, 0x5678)]):
        text = "\n".join(kd.report())
    assert "No Kinect on the USB bus" in text


if __name__ == "__main__":
    test_packed_depth_is_detected_and_unpacked()
    test_depth_shift_ignores_empty_frames()
    test_depth_shift_picks_the_reading_inside_the_sensor_range()
    test_registration_shifts_depth_onto_the_color_camera()
    test_registration_disparity_falls_off_as_one_over_depth()
    test_registration_keeps_the_nearer_surface()
    test_registration_leaves_no_holes_across_a_flat_surface()
    test_registration_drops_invalid_depth()
    test_v1_prefers_the_microsoft_runtime_over_libfreenect()
    test_v1_falls_back_to_libfreenect_when_the_runtime_is_absent()
    test_unusable_kinect_explains_itself_and_falls_back_to_the_webcam()
    test_reason_distinguishes_a_visible_sensor_that_will_not_stream()
    test_kinect_absent_from_usb_names_the_power_adapter()
    test_doctor_blames_power_when_only_the_motor_enumerates()
    test_doctor_points_at_the_runtime_when_nothing_can_read_the_camera()
    test_doctor_reports_ready_when_the_runtime_can_see_it()
    test_doctor_says_no_device_without_guessing()
    print("ok")
