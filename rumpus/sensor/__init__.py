"""Sensor backend selection.

The app probes for a real Kinect (v2 preferred, then v1) by USB VID/PID, then
falls back to trying each backend's ``open``. In dev/``--mock`` mode, or when no
hardware answers, the ``MockBackend`` is used so the game is still runnable.
"""
from __future__ import annotations

from .base import SensorBackend
from .mock import MockBackend


def create_backend(force: str | None = None, allow_mock: bool = True) -> tuple[SensorBackend | None, str]:
    """Return ``(backend, status)``.

    ``force`` overrides auto-detection: 'v1' | 'v2' | 'mock'.
    ``status`` is one of 'v1' | 'v2' | 'mock' | 'none'.
    """
    if force == "mock" or (allow_mock and force is None and _no_hardware()):
        b = MockBackend()
        if b.open():
            return b, "mock"

    order = ["v2", "v1"] if force is None else ([force] if force in ("v1", "v2") else [])

    for kind in order:
        backend = _try_open(kind)
        if backend is not None:
            return backend, kind

    if allow_mock:
        b = MockBackend()
        if b.open():
            return b, "mock"
    return None, "none"


def _no_hardware() -> bool:
    """Quick USB probe; if it says no Kinect, skip straight to mock."""
    try:
        from . import detect
        return detect.detect_sensor() is None
    except Exception:
        return True


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
