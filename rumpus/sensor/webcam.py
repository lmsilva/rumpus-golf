"""Regular 2D webcam backend (color-only).

A standard USB/webcam has no depth stream, so the floor cannot be plane-fitted
the way the Kinect backends do. Instead the engine maps pixels -> floor meters
with a homography the user calibrates by clicking the four corners of a
known-size rectangle (see ``vision/geometry.HomographyMapper``). Tracking then
runs on color alone (hue blobs for balls, frame differencing for obstacles).

This backend lives behind the same ``SensorBackend`` interface as everything
else, so the rest of the game is unchanged by the swap.
"""
from __future__ import annotations

import os
import time

import cv2
import numpy as np

from ..models import CameraModel, Frame, SensorDescription
from .base import SensorBackend


def _backend_api() -> int:
    # CAP_DSHOW avoids the slow MSMF enumeration/popups on Windows.
    if os.name == "nt":
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def parse_resolution(spec: str) -> tuple[int, int]:
    """Parse "1280x720" (or "1280X720") into (w, h); fall back to 720p."""
    try:
        w_s, h_s = str(spec).lower().split("x")
        w, h = int(w_s.strip()), int(h_s.strip())
        if w > 0 and h > 0:
            return w, h
    except (ValueError, AttributeError):
        pass
    return (1280, 720)


def list_webcams(max_index: int = 8) -> list[dict]:
    """Probe capture indices 0..max_index and return the ones that answer.

    Opening a device also does a throwaway read so the "working" flag reflects
    whether a real frame arrives (not just a driver that opens). This can take
    a moment — call it once at startup or on an explicit "rescan".
    """
    cams: list[dict] = []
    for i in range(max_index):
        cap = None
        try:
            cap = cv2.VideoCapture(i, _backend_api())
            if cap.isOpened():
                ok, _ = cap.read()
                cams.append({"index": i, "name": f"Camera {i}", "working": bool(ok)})
        except Exception:
            pass
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
    return cams


class WebcamBackend(SensorBackend):
    def __init__(self, index: int = 0, resolution: str = "1280x720") -> None:
        w, h = parse_resolution(resolution)
        self.index = index
        self.resolution = (w, h)
        self.description = SensorDescription(
            model=f"Webcam {index}",
            color_res=(w, h),
            depth_res=(0, 0),          # color-only marker
            fov_h_deg=70.0,
            reliable_min_m=0.5,
            reliable_max_m=5.0,
            note="Color-only — floor mapped by a 4-corner homography.",
            fps=30,
        )
        # Intrinsics are unused by the homography path but kept so downstream
        # code that touches ``cam`` has something reasonable.
        self.cam: CameraModel = CameraModel(
            fx=float(w), fy=float(w), cx=w / 2.0, cy=h / 2.0, color_scale=1.0
        )
        self._cap: cv2.VideoCapture | None = None
        self._last: Frame | None = None
        self._opened = False

    @property
    def has_depth(self) -> bool:
        return False

    def open(self) -> bool:
        try:
            cap = cv2.VideoCapture(self.index, _backend_api())
        except Exception:
            return False
        if not cap.isOpened():
            cap.release()
            return False
        w, h = self.resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.read()  # let the device settle / apply the requested size
        self._cap = cap
        self._opened = True
        return True

    def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None
        self._opened = False

    def is_open(self) -> bool:
        return self._opened

    def grab(self) -> Frame:
        cap = self._cap
        if cap is None:
            return Frame(color=None, depth=None, t=time.time(), source="webcam")
        try:
            ok, frame = cap.read()
        except Exception:
            ok, frame = False, None
        if not ok or frame is None:
            if self._last is not None:
                return self._last
            return Frame(color=None, depth=None, t=time.time(), source="webcam")
        color = np.asarray(frame, dtype=np.uint8)
        f = Frame(color=color, depth=None, t=time.time(), source="webcam")
        self._last = f
        return f
