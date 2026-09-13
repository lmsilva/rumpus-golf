"""Kinect v2 backend (Xbox One / Kinect for Windows v2) over libfreenect2.

Requires libfreenect2 plus pylibfreenect2 (``import pylibfreenect2``) and the
libfreenect2 runtime. Depth (512x424) is registered onto the color frame
(1920x1080) using pylibfreenect2's Registration so downstream code sees one
aligned pair.

Build this backend second, against the already-fixed ``SensorBackend``
interface — swapping v1 -> v2 must require zero changes above this layer.
"""
from __future__ import annotations

import time

import numpy as np

from ..models import Frame, SensorDescription
from .base import SensorBackend


class KinectV2Backend(SensorBackend):
    def __init__(self) -> None:
        self.description = SensorDescription(
            model="Kinect v2",
            color_res=(1920, 1080),
            depth_res=(512, 424),
            fov_h_deg=70.0,
            reliable_min_m=0.5,
            reliable_max_m=4.5,
            note="Time-of-flight depth; flatters indirect sunlight.",
            fps=30,
        )
        self._fn = None
        self._dev = None
        self._listener = None
        self._registration = None
        self._last: Frame | None = None
        self._opened = False

    def open(self) -> bool:
        try:
            from pylibfreenect2 import (  # type: ignore
                Freenect2, SyncMultiFrameListener, FrameType, Registration, Frame,
            )
        except Exception:
            return False
        self._fn = fn = Freenect2()
        try:
            self._dev = fn.openDefaultDevice()
        except Exception:
            return False
        if self._dev is None:
            return False
        listener = SyncMultiFrameListener(
            FrameType.Color | FrameType.Depth
        )
        self._dev.setColorFrameListener(listener)
        self._dev.setIrAndDepthFrameListener(listener)
        self._dev.start()
        self._listener = listener
        self._registration = Registration(self._dev.getIrCameraParams(), self._dev.getColorCameraParams())
        self._opened = True
        return True

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._dev.stop()
                self._dev.close()
        except Exception:
            pass
        self._opened = False

    def is_open(self) -> bool:
        return self._opened

    def lock_capture(self, exposure: float | None = None) -> None:
        return

    def unlock_capture(self) -> None:
        return

    def grab(self) -> Frame:
        try:
            from pylibfreenect2 import FrameType, Frame as FN2Frame  # type: ignore
        except Exception:
            if self._last is not None:
                return self._last
            return Frame(color=None, depth=None, t=time.time(), source="kinect-v2")

        frames = self._listener.waitForNewFrame()
        color = frames["color"]
        depth = frames["depth"]
        # Registered depth + undistorted color, both 512x424 / full-res color.
        reg_depth = FN2Frame(512, 424, 4)
        reg_color = FN2Frame(512, 424, 4)
        self._registration.apply(color, depth, reg_depth, reg_color)
        depth_mm = reg_depth.asarray(np.float32)  # meters in libfreenect2 -> convert
        # pylibfreenect2 stores depth in *meters*; our pipeline wants mm.
        depth_mm = (depth_mm * 1000.0).astype(np.float32)
        bgr = reg_color.asarray(np.uint8)[:, :, :3][:, :, ::-1].copy()
        self._listener.release(frames)
        frame = Frame(color=bgr, depth=depth_mm, t=time.time(), source="kinect-v2")
        self._last = frame
        return frame
