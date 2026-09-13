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
        self._depth_format = None
        self._last: Frame | None = None
        self._opened = False

    def open(self) -> bool:
        try:
            import freenect  # type: ignore
        except Exception:
            return False
        self._freenect = freenect
        # DEPTH_REGISTERED is millimeters already aligned to the RGB frame;
        # DEPTH_MM is millimeters but *not* aligned, so depth pixels would not
        # correspond to the color pixels the hue matching reads.
        self._depth_format = getattr(
            freenect, "DEPTH_REGISTERED", getattr(freenect, "DEPTH_MM", 4),
        )
        # freenect's sync API owns its own context and device. Calling
        # open_device() here as well claims the device and makes every
        # sync_get_* fail, so let the sync helpers do the opening.
        try:
            depth = freenect.sync_get_depth(format=self._depth_format)
            video = freenect.sync_get_video(format=freenect.VIDEO_RGB)
        except Exception:
            return False
        if depth is None or video is None:
            return False
        self._opened = True
        return True

    def close(self) -> None:
        if self._freenect is not None:
            try:
                self._freenect.sync_stop()
            except Exception:
                pass
        self._opened = False

    def is_open(self) -> bool:
        return self._opened

    def detach_handle(self) -> None:
        """Release the USB context so the game loop can claim it.

        libfreenect handles belong to the thread that opened them, and the
        sensor is opened on a worker thread so the boot menu stays responsive.
        """
        if self._freenect is not None:
            try:
                self._freenect.sync_stop()
            except Exception:
                pass
        self._last = None
        self._opened = False

    def reopen_on_this_thread(self, full: bool = False) -> bool:
        return self.open()

    def lock_capture(self, exposure: float | None = None) -> None:
        return

    def unlock_capture(self) -> None:
        return

    def grab(self) -> Frame:
        f = self._freenect
        if f is None:
            return Frame(color=None, depth=None, t=time.time(), source="kinect-v1")
        try:
            # Both sync helpers return (ndarray, timestamp), or None on error.
            d = f.sync_get_depth(format=self._depth_format)  # type: ignore[attr-defined]
            c = f.sync_get_video(format=f.VIDEO_RGB)  # type: ignore[attr-defined]
            if d is None or c is None:
                raise RuntimeError("kinect v1 delivered no frame")
            depth = np.asarray(d[0], dtype=np.float32)
            color = np.asarray(c[0], dtype=np.uint8)
            # VIDEO_RGB is RGB; our pipeline uses BGR.
            if color.ndim == 3 and color.shape[-1] == 3:
                color = color[:, :, ::-1].copy()
            frame = Frame(color=color, depth=depth, t=time.time(), source="kinect-v1")
            self._last = frame
            return frame
        except Exception:
            # Keep the stale frame's timestamp: the engine treats a ``t`` that
            # stops advancing as a dead stream rather than tracking on it.
            if self._last is not None:
                return self._last
            return Frame(color=None, depth=None, t=0.0, source="kinect-v1")
