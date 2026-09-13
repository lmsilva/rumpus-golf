"""Sensor backend selection.

The app probes for a real Kinect (v2 preferred, then v1) by USB VID/PID, then
tries a regular 2D webcam, and finally falls back to the ``MockBackend`` so the
game is always runnable. The user can override the whole decision in Settings
(Camera tab) or on the command line.
"""
from __future__ import annotations

from .base import SensorBackend
from .mock import MockBackend


def create_backend(
    force: str | None = None,
    allow_mock: bool = True,
    camera_index: int = 0,
    camera_res: str = "1280x720",
    backend_mode: str = "auto",
) -> tuple[SensorBackend | None, str]:
    """Return ``(backend, status)``.

    ``force`` overrides everything: 'v1' | 'v2' | 'webcam' | 'mock'.
    ``backend_mode`` is the persisted preference from Settings: 'auto' |
    'webcam' | 'kinect'. ``status`` is one of 'v1' | 'v2' | 'webcam' | 'mock' |
    'none'.
    """
    # Explicit force (CLI --sensor/--mock/--camera) always wins.
    if force == "mock":
        b = MockBackend()
        return (b, "mock") if b.open() else (None, "none")
    if force == "webcam":
        from .webcam import WebcamBackend
        b = WebcamBackend(camera_index, camera_res)
        return (b, "webcam") if b.open() else (None, "none")
    if force in ("v1", "v2"):
        b = _try_open(force)
        return (b, force) if b is not None else (None, "none")

    # Kinect (auto-detect), when the preference allows it.
    if backend_mode in ("auto", "kinect"):
        try:
            from . import detect
            kind = detect.detect_sensor()
        except Exception:
            kind = None
        if kind is not None:
            b = _try_open(kind)
            if b is not None:
                return b, kind
        if backend_mode == "kinect":
            if allow_mock:
                b = MockBackend()
                return (b, "mock") if b.open() else (None, "none")
            return None, "none"

    # Regular 2D webcam (color-only).
    if backend_mode in ("auto", "webcam"):
        from .webcam import WebcamBackend
        b = WebcamBackend(camera_index, camera_res)
        if b.open():
            return b, "webcam"
        # The user asked for a webcam — do not silently substitute the mock.
        if backend_mode == "webcam":
            return None, "none"

    if allow_mock:
        b = MockBackend()
        return (b, "mock") if b.open() else (None, "none")
    return None, "none"


def _try_open(kind: str) -> SensorBackend | None:
    if kind == "v1":
        try:
            from .kinect_v1 import KinectV1Backend
            b = KinectV1Backend()
            return b if b.open() else None
        except Exception:
            return None
    if kind == "v2":
        try:
            from .kinect_v2 import KinectV2Backend
            b = KinectV2Backend()
            return b if b.open() else None
        except Exception:
            return None
    return None
