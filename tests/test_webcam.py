"""Camera-free checks for webcam negotiation helpers."""
from __future__ import annotations

import cv2

from rumpus.models import SensorDescription
from rumpus.sensor.base import SensorBackend
from rumpus.sensor.webcam import (
    EXPOSURE_NOTICE,
    TARGET_FPS,
    exposure_pairs,
    exposure_shutter,
    fourcc_name,
    frame_has_picture,
    parse_resolution,
    size_ladder,
)


def test_frame_has_picture_rejects_black():
    import numpy as np
    assert frame_has_picture(None) is False
    assert frame_has_picture(np.zeros((120, 160, 3), dtype=np.uint8)) is False
    assert frame_has_picture(np.full((120, 160, 3), 5, dtype=np.uint8)) is False
    rng = np.random.default_rng(0)
    noisy = rng.integers(0, 255, (120, 160, 3), dtype=np.uint8)
    assert frame_has_picture(noisy) is True
    dim = rng.integers(20, 80, (120, 160, 3), dtype=np.uint8)
    assert frame_has_picture(dim) is True


def test_fourcc_name_roundtrip():
    import struct
    mjpg = cv2.VideoWriter_fourcc(*"MJPG")
    assert fourcc_name(mjpg) == "MJPG"
    assert fourcc_name(cv2.VideoWriter_fourcc(*"YUY2")) == "YUY2"
    assert fourcc_name("nope") == "?"
    # MSMF hands back a float whose IEEE bits are the FOURCC.
    as_float = struct.unpack("<f", struct.pack("<I", mjpg & 0xFFFFFFFF))[0]
    assert fourcc_name(as_float) == "MJPG"


def test_exposure_shutter():
    assert exposure_shutter(-4) == (16, 16.0)
    assert exposure_shutter(-6) == (64, 64.0)
    assert exposure_shutter(-8) == (256, 256.0)


def test_size_ladder_steps_down():
    assert size_ladder(1280, 720) == [
        (1280, 720), (960, 540), (800, 600), (640, 480),
    ]
    assert size_ladder(1920, 1080)[0] == (1920, 1080)
    assert (1280, 720) in size_ladder(1920, 1080)
    assert (640, 480) in size_ladder(1920, 1080)
    assert size_ladder(640, 480) == [(640, 480)]
    assert size_ladder(800, 600) == [(800, 600), (640, 480)]


def test_parse_resolution_and_exposure_pairs():
    assert parse_resolution("1280x720") == (1280, 720)
    assert parse_resolution("960X540") == (960, 540)
    assert parse_resolution("bad") == (1280, 720)
    assert exposure_pairs("dshow")[0] == (0.25, 0.75)
    assert exposure_pairs("msmf")[0] == (0.0, 1.0)
    assert TARGET_FPS == 25.0
    assert "Bright, even room light" in EXPOSURE_NOTICE


def test_sensor_description_publishes_negotiation_fields():
    d = SensorDescription(
        model="Webcam 0", color_res=(640, 480), depth_res=(0, 0),
        fov_h_deg=70.0, reliable_min_m=0.5, reliable_max_m=5.0,
        fps=27.4, exposure_control=False, fourcc="MJPG",
    )
    payload = d.as_dict()
    assert payload["exposure_control"] is False
    assert payload["fourcc"] == "MJPG"
    assert payload["color_res"] == (640, 480)
    assert payload["fps"] == 27.4


def test_sensor_check_waits_while_camera_opens():
    from rumpus.engine import GameEngine, S
    e = GameEngine()
    e.state = S.SENSOR_CHECK
    e.sensor_status = "opening"
    e.handle_input({"t": "action", "a": "fresh"})
    e.handle_input({"t": "action", "a": "retry"})
    assert e.state == S.SENSOR_CHECK
    assert e.backend is None
    ui = e._ui_snapshot()
    assert ui.get("opening") is True


def test_duplicate_camera_labels_and_siblings():
    from rumpus.sensor.webcam import alternate_device_indices, unique_device_label
    names = ["HD Pro Webcam C920", "Iriun Webcam", "HD Pro Webcam C920", "OBS Virtual Camera"]
    assert unique_device_label(names, 0) == "HD Pro Webcam C920 (1)"
    assert unique_device_label(names, 2) == "HD Pro Webcam C920 (2)"
    assert unique_device_label(names, 1) == "Iriun Webcam"
    # Black stub at 0 should try the other C920 before virtual cams.
    assert alternate_device_indices(0, names)[0] == 2
    assert 1 not in alternate_device_indices(0, names)


def test_plans_try_dshow_before_cached_msmf():
    import os
    from rumpus.sensor.webcam import WebcamBackend
    b = WebcamBackend(0, "1920x1080")
    cached = {
        "api": "msmf", "order": "fourcc_first", "resolution": "1920x1080",
        "want": "1920x1080", "device": 0, "ok": True, "exposure": -6.0,
    }
    plans = b._plans(1920, 1080, cached)
    assert plans
    names = [p[0] for p in plans[:4]]
    if os.name == "nt":
        assert names[0] == "dshow"
        assert "msmf" in names
    assert (plans[0][3], plans[0][4]) == (1920, 1080)


def test_detach_handle_drops_cap():
    from rumpus.sensor.webcam import WebcamBackend

    class Cap:
        def __init__(self):
            self.released = False

        def release(self):
            self.released = True

    b = WebcamBackend(0, "640x480")
    cap = Cap()
    b._cap = cap
    b._opened = True
    b.detach_handle()
    assert cap.released is True
    assert b._cap is None
    assert b.is_open() is False


def test_grab_recovers_after_misses():
    from rumpus.sensor.webcam import GRAB_MISS_RECOVER, WebcamBackend

    class DeadCap:
        def read(self):
            return False, None

        def release(self):
            return None

    b = WebcamBackend(0, "640x480")
    b._cap = DeadCap()
    b._opened = True
    called = []
    b.reopen_on_this_thread = lambda full=False: called.append(full) or False
    for _ in range(GRAB_MISS_RECOVER):
        b.grab()
    assert called == [False]


def test_base_backend_driver_settings_is_noop():
    class Dummy(SensorBackend):
        def open(self):
            return True

        def close(self):
            return None

        def is_open(self):
            return False

        def grab(self):
            raise NotImplementedError

    b = Dummy()
    assert b.open_driver_settings() is False


def test_unusable_kinect_explains_itself_and_falls_back_to_the_webcam():
    """A detected-but-unopenable Kinect must not look like "no camera"."""
    from unittest.mock import patch

    import rumpus.sensor as sensor

    fake_webcam = object()
    with patch.object(sensor.detect, "detect_sensor", return_value="v1"), \
         patch.object(sensor, "_try_open", return_value=None), \
         patch.object(sensor, "_open_webcam", return_value=fake_webcam):
        report: dict = {}
        backend, kind = sensor.create_backend(
            backend_mode="kinect", allow_mock=True, report=report,
        )
    # The 2D camera, not the mock: a simulated scene is useless for a real putt.
    assert backend is fake_webcam and kind == "webcam"
    reason = report.get("reason", "")
    assert "Kinect v1" in reason and "freenect" in reason


def test_kinect_absent_from_usb_names_the_power_adapter():
    """The camera never enumerates on bus power alone — say so."""
    from unittest.mock import patch

    import rumpus.sensor as sensor

    with patch.object(sensor.detect, "detect_sensor", return_value=None), \
         patch.object(sensor, "_try_open", return_value=None), \
         patch.object(sensor, "_open_webcam", return_value=None):
        report: dict = {}
        backend, kind = sensor.create_backend(
            backend_mode="kinect", allow_mock=False, report=report,
        )
    assert backend is None and kind == "none"
    assert "12 V" in report.get("reason", "")


def test_doctor_blames_power_when_only_the_motor_enumerates():
    """Motor but no camera is the signature of a Kinect v1 on bus power."""
    from unittest.mock import patch

    from rumpus.sensor import kinect_doctor as kd

    motor = {"vid": 0x045E, "pid": 0x02B0, "status": "Error",
             "name": "Xbox NUI Motor", "problem": 28}
    with patch.object(kd.detect, "detect_sensor", return_value="v1"), \
         patch.object(kd.detect, "list_usb", return_value=[(0x045E, 0x02B0)]), \
         patch.object(kd, "_windows_devices", return_value=[motor]), \
         patch.object(kd.platform, "system", return_value="Windows"):
        text = "\n".join(kd.report())
    assert "Xbox NUI Camera" in text and "absent" in text
    assert "12 V power adapter" in text
    # The driver state it did find should be spelled out, not left as a number.
    assert "no driver installed" in text


def test_doctor_blames_the_binding_when_the_hardware_is_healthy():
    """A healthy sensor with no Python binding needs a different fix."""
    from unittest.mock import patch

    from rumpus.sensor import kinect_doctor as kd

    devs = [
        {"vid": 0x045E, "pid": 0x02B0, "status": "OK",
         "name": "Xbox NUI Motor", "problem": 0},
        {"vid": 0x045E, "pid": 0x02AE, "status": "OK",
         "name": "Xbox NUI Camera", "problem": 0},
    ]
    with patch.object(kd.detect, "detect_sensor", return_value="v1"), \
         patch.object(kd.detect, "list_usb",
                      return_value=[(0x045E, 0x02B0), (0x045E, 0x02AE)]), \
         patch.object(kd, "_windows_devices", return_value=devs), \
         patch.object(kd, "_binding", return_value=("freenect", False, "nope")), \
         patch.object(kd.platform, "system", return_value="Windows"):
        text = "\n".join(kd.report())
    assert "12 V power adapter" not in text
    assert "freenect" in text and "not installed" in text


def test_doctor_says_no_device_without_guessing():
    from unittest.mock import patch

    from rumpus.sensor import kinect_doctor as kd

    with patch.object(kd.detect, "detect_sensor", return_value=None), \
         patch.object(kd.detect, "list_usb", return_value=[(0x1234, 0x5678)]):
        text = "\n".join(kd.report())
    assert "No Kinect on the USB bus" in text


if __name__ == "__main__":
    test_frame_has_picture_rejects_black()
    test_fourcc_name_roundtrip()
    test_exposure_shutter()
    test_size_ladder_steps_down()
    test_parse_resolution_and_exposure_pairs()
    test_sensor_description_publishes_negotiation_fields()
    test_sensor_check_waits_while_camera_opens()
    test_duplicate_camera_labels_and_siblings()
    test_plans_try_dshow_before_cached_msmf()
    test_detach_handle_drops_cap()
    test_grab_recovers_after_misses()
    test_base_backend_driver_settings_is_noop()
    test_unusable_kinect_explains_itself_and_falls_back_to_the_webcam()
    test_kinect_absent_from_usb_names_the_power_adapter()
    test_doctor_blames_power_when_only_the_motor_enumerates()
    test_doctor_blames_the_binding_when_the_hardware_is_healthy()
    test_doctor_says_no_device_without_guessing()
    print("ok")
