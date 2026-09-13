"""Kinect v1 backend (Xbox 360 / Kinect for Windows) over libfreenect.

Requires the libfreenect library plus its Python bindings (``import freenect``).
Install per the libfreenect README — it is not pip-installable in a stable way.
Depth is registered onto the color frame by the backend so downstream code sees
a single aligned pair (both 640x480).

Build and test this backend first — it is the sensor on hand per the spec.
"""
from __future__ import annotations

import time

import numpy as np

from ..models import Frame, SensorDescription
from .base import SensorBackend


class KinectV1Backend(SensorBackend):
    def __init__(self) -> None:
        self.description = SensorDescription(
            model="Kinect v1",
            color_res=(640, 480),
            depth_res=(640, 480),
            fov_h_deg=57.0,
            reliable_min_m=0.8,
            reliable_max_m=4.0,
            note="Struggles in direct sunlight — draw the curtains.",
            fps=30,
        )
        self._freenect = None
        self._dev = None
        self._last: Frame | None = None
        self._opened = False

    def open(self) -> bool:
        try:
            import freenect  # type: ignore
        except Exception:
            return False
        self._freenect = freenect
        try:
            freenect.init()
            self._dev = freenect.open_device(freenect.num_devices() - 1)
        except Exception:
            return False
        try:
            freenect.set_depth_mode(self._dev, freenect.DEPTH_MM)
            freenect.set_video_mode(self._dev, freenect.VIDEO_RGB)
        except Exception:
            pass
        self._opened = True
        return True

    def close(self) -> None:
        if self._freenect is not None:
            try:
                self._freenect.close_device(self._dev)
                self._freenect.shutdown()
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
        f = self._freenect
        try:
            # sync_get_depth/video (registered) return numpy arrays.
            depth = f.sync_get_depth(format=f.DEPTH_MM)  # type: ignore[attr-defined]
            color = f.sync_get_video(format=f.VIDEO_RGB)  # type: ignore[attr-defined]
            depth = np.asarray(depth, dtype=np.float32)
            color = np.asarray(color, dtype=np.uint8)
            # VIDEO_RGB is RGB; our pipeline uses BGR.
            if color.ndim == 3 and color.shape[-1] == 3:
                color = color[:, :, ::-1].copy()
            frame = Frame(color=color, depth=depth, t=time.time(), source="kinect-v1")
            self._last = frame
            return frame
        except Exception:
            if self._last is not None:
                return self._last
            return Frame(color=None, depth=None, t=time.time(), source="kinect-v1")
