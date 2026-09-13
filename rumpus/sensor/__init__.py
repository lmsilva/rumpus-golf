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
    settings=None,
    report: dict | None = None,
) -> tuple[SensorBackend | None, str]:
    """Return ``(backend, status)``.

    ``force`` overrides everything: 'v1' | 'v2' | 'webcam' | 'mock'.
    ``backend_mode`` is the persisted preference from Settings: 'auto' |
    'webcam' | 'kinect'. ``status`` is one of 'v1' | 'v2' | 'webcam' | 'mock' |
    'none'.

    ``report`` is filled with a ``reason`` the caller can show the player when
    a sensor was found but could not be used. Falling back without saying so
    reads as "my Kinect is not detected" when the app knew it was plugged in.
    """
    # Explicit force (CLI --sensor/--mock/--camera) always wins.
    if force == "mock":
        b = MockBackend()
        return (b, "mock") if b.open() else (None, "none")
    if force == "webcam":
        b = _open_webcam(camera_index, camera_res, settings, try_others=True)
        return (b, "webcam") if b is not None else (None, "none")
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
            _note(report, _kinect_failure_reason(kind))
        elif backend_mode == "kinect":
            # USB enumeration can time out or miss the device (custom driver,
            # no PowerShell). Asking the driver to open it is just as
            # conclusive, so don't give up on an explicit Kinect preference.
            for guess in ("v2", "v1"):
                b = _try_open(guess)
                if b is not None:
                    return b, guess
            _note(report, "No Kinect found on USB. Check the 12 V power "
                          "adapter — the camera does not appear without it.")
        if backend_mode == "kinect":
            # A real 2D camera beats a simulated scene as a substitute: the
            # player can still calibrate and putt, just without depth. Handing
            # them the mock instead made a plugged-in Kinect look like no
            # camera at all.
            b = _open_webcam(camera_index, camera_res, settings, try_others=True)
            if b is not None:
                return b, "webcam"
            if allow_mock:
                b = MockBackend()
                return (b, "mock") if b.open() else (None, "none")
            return None, "none"

    # Regular 2D webcam (color-only).
    if backend_mode in ("auto", "webcam"):
        b = _open_webcam(
            camera_index, camera_res, settings,
            try_others=(backend_mode == "webcam"),
        )
        if b is not None:
            return b, "webcam"
        # The user asked for a webcam — do not silently substitute the mock.
        if backend_mode == "webcam":
            return None, "none"

    if allow_mock:
        b = MockBackend()
        return (b, "mock") if b.open() else (None, "none")
    return None, "none"


def _note(report: dict | None, reason: str) -> None:
    """Record the first reason a sensor could not be used, and log it."""
    print(f"[sensor] {reason}", flush=True)
    if report is not None:
        report.setdefault("reason", reason)


def _kinect_failure_reason(kind: str) -> str:
    """Why a Kinect that USB can see still would not open.

    Almost always a missing Python binding or an unbound camera interface, and
    the two need different fixes, so name whichever one is actually wrong.
    """
    module = "freenect" if kind == "v1" else "pylibfreenect2"
    label = "Kinect v1" if kind == "v1" else "Kinect v2"
    try:
        __import__(module)
    except Exception:
        return (f"{label} is plugged in, but its driver library ({module}) is "
                f"not installed, so the game cannot read depth. Playing with "
                f"the 2D camera instead.")
    return (f"{label} is plugged in and {module} is installed, but the sensor "
            f"would not start. Check the 12 V power adapter, and that the "
            f"camera interface has a libusbK driver.")


def _open_webcam(camera_index: int, camera_res: str, settings,
                 try_others: bool = False) -> SensorBackend | None:
    from .webcam import WebcamBackend
    b = WebcamBackend(camera_index, camera_res, settings=settings)
    if b.open():
        return b
    if not try_others:
        return None
    from .webcam import list_webcams
    # open() walks the same-name siblings itself; negotiating them a second
    # time here doubled an already slow boot when no camera has a picture.
    seen = set(getattr(b, "tried_indices", None) or {int(camera_index)})
    for cam in list_webcams():
        try:
            idx = int(cam.get("index", -1))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx in seen:
            continue
        seen.add(idx)
        label = cam.get("name") or f"Camera {idx}"
        print(f"[webcam] index {camera_index} failed — trying {idx} ({label})",
              flush=True)
        b = WebcamBackend(idx, camera_res, settings=settings)
        if not b.open():
            continue
        if settings is not None:
            try:
                settings.set(idx, "camera", "device")
                settings.save()
            except Exception:
                pass
        return b
    return None


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
