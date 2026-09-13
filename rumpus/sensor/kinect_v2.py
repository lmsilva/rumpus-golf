"""Kinect v2 backend (Xbox One / Kinect for Windows v2) over libfreenect2.

Requires libfreenect2 plus pylibfreenect2 (``import pylibfreenect2``) and the
libfreenect2 runtime. The 1920x1080 color frame is registered *into* the depth
camera's 512x424 geometry, so downstream code sees one aligned 512x424 pair and
the depth intrinsics apply to both.

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
            # grab() publishes the registered pair, not the raw 1080p color.
            color_res=(512, 424),
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
        self._reg_depth = None
        self._reg_color = None
        self._last: Frame | None = None
        self._opened = False

    def open(self) -> bool:
        try:
            from pylibfreenect2 import (  # type: ignore
                Freenect2, SyncMultiFrameListener, FrameType, Registration,
                Frame as FN2Frame,
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
        try:
            self._dev.start()
        except Exception:
            return False
        self._listener = listener
        self._registration = Registration(self._dev.getIrCameraParams(), self._dev.getColorCameraParams())
        # Allocate the registration targets once; a fresh pair every grab leaks
        # native buffers over a long game night.
        self._reg_depth = FN2Frame(512, 424, 4)
        self._reg_color = FN2Frame(512, 424, 4)
        self._opened = True
        return True

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._dev.stop()
                self._dev.close()
        except Exception:
            pass
        # Drop the native handles so a grab racing with close cannot use them.
        self._registration = None
        self._listener = None
        self._reg_depth = None
        self._reg_color = None
        self._opened = False

    def is_open(self) -> bool:
        return self._opened

    def detach_handle(self) -> None:
        """Release the device so the game loop can claim it.

        libfreenect2 pipeline handles belong to the thread that opened them, and
        the sensor is opened on a worker thread so the boot menu stays
        responsive.
        """
        self.close()
        self._last = None

    def reopen_on_this_thread(self, full: bool = False) -> bool:
        return self.open()

    def lock_capture(self, exposure: float | None = None) -> None:
        return

    def unlock_capture(self) -> None:
        return

    def _wait_frames(self, timeout_s: float = 1.0):
        """Frames if they arrive within ``timeout_s``, else None.

        ``waitForNewFrame()`` alone blocks forever, so unplugging the sensor
        mid-game would hang the game loop with the websocket still up.
        """
        listener = self._listener
        if listener is None:
            return None
        has_new = getattr(listener, "hasNewFrame", None)
        if callable(has_new):
            deadline = time.time() + timeout_s
            while not has_new():
                if time.time() >= deadline:
                    return None
                time.sleep(0.002)
        return listener.waitForNewFrame()

    def _stale(self) -> Frame:
        # Keep the stale frame's timestamp: the engine treats a ``t`` that stops
        # advancing as a dead stream rather than tracking on it.
        if self._last is not None:
            return self._last
        return Frame(color=None, depth=None, t=0.0, source="kinect-v2")

    def grab(self) -> Frame:
        if self._registration is None:
            return self._stale()
        frames = None
        try:
            frames = self._wait_frames()
            if frames is None:
                return self._stale()
            self._registration.apply(
                frames["color"], frames["depth"], self._reg_depth, self._reg_color,
            )
            # libfreenect2 depth frames are already float millimeters.
            depth_mm = self._reg_depth.asarray(np.float32).astype(np.float32, copy=True)
            bgr = self._reg_color.asarray(np.uint8)[:, :, :3][:, :, ::-1].copy()
        except Exception:
            return self._stale()
        finally:
            if frames is not None:
                try:
                    self._listener.release(frames)
                except Exception:
                    pass
        frame = Frame(color=bgr, depth=depth_mm, t=time.time(), source="kinect-v2")
        self._last = frame
        return frame
