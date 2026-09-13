"""Camera-free checks for webcam negotiation helpers."""
from __future__ import annotations

import cv2

from rumpus.models import SensorDescription
from rumpus.sensor.base import SensorBackend
from rumpus.sensor.webcam import (
    EXPOSURE_NOTICE,
    TARGET_FPS,
    exposure_pairs,
    fourcc_name,
    parse_resolution,
    size_ladder,
)


def test_fourcc_name_roundtrip():
    assert fourcc_name(cv2.VideoWriter_fourcc(*"MJPG")) == "MJPG"
    assert fourcc_name(cv2.VideoWriter_fourcc(*"YUY2")) == "YUY2"
    assert fourcc_name("nope") == "?"


def test_size_ladder_steps_down():
    assert size_ladder(1280, 720) == [
        (1280, 720), (960, 540), (800, 600), (640, 480),
    ]
    assert size_ladder(1920, 1080)[0] == (1920, 1080)
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


if __name__ == "__main__":
    test_fourcc_name_roundtrip()
    test_size_ladder_steps_down()
    test_parse_resolution_and_exposure_pairs()
    test_sensor_description_publishes_negotiation_fields()
    test_base_backend_driver_settings_is_noop()
    print("ok")
