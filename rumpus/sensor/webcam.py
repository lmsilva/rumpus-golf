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

import logging
import os
import time

import cv2
import numpy as np

from ..models import CameraModel, Frame, SensorDescription
from .base import SensorBackend

log = logging.getLogger("rumpus.sensor.webcam")


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


def list_webcams(max_index: int = 8, probe: bool = False) -> list[dict]:
    """Return capture devices with driver names.

    By default this is names-only (DirectShow / V4L2) so we never steal the
    camera the game is already using. Set ``probe=True`` only on an explicit
    rescan when no backend is holding a device.
    """
    from .devices import attach_names, list_capture_names
    names = list_capture_names()
    if names and not probe:
        return [
            {"index": i, "name": n, "driver": n, "working": True}
            for i, n in enumerate(names)
        ]
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
    return attach_names(cams, names or None)


class WebcamBackend(SensorBackend):
    def __init__(self, index: int = 0, resolution: str = "1280x720") -> None:
        w, h = parse_resolution(resolution)
        self.index = index
        self.resolution = (w, h)
        try:
            from .devices import list_capture_names
            names = list_capture_names()
            model = names[index] if 0 <= index < len(names) else f"Webcam {index}"
        except Exception:
            model = f"Webcam {index}"
        self.description = SensorDescription(
            model=model,
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
        self._measured_fps = 30.0
        self._locked = False
        self._exposure = -6.0
        self._ignored: list[str] = []
        self._grab_times: list[float] = []

    @property
    def has_depth(self) -> bool:
        return False

    def open(self) -> bool:
        # DirectShow is exclusive and can need a beat after another handle
        # (our own enumerator, Zoom, etc.) lets go.
        for attempt in range(4):
            cap = None
            try:
                cap = cv2.VideoCapture(self.index, _backend_api())
            except Exception:
                cap = None
            if cap is not None and cap.isOpened():
                w, h = self.resolution
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                # DirectShow defaults to uncompressed video, which caps most
                # Logitech cameras at 5–10 fps at 720p. MJPG unlocks 30.
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FPS, 30)
                self._ignored = []
                fps = self._measure_fps(cap)
                self._measured_fps = fps
                self.description.fps = float(round(fps, 1))
                log.info("webcam %s measured %.1f fps at %sx%s",
                         self.description.model, fps, w, h)
                print(f"[webcam] measured {fps:.1f} fps "
                      f"({self.description.model}, {w}x{h})", flush=True)
                if fps < 15:
                    self._note_ignored("FOURCC=MJPG (still under 15 fps)")
                    log.warning("webcam still under 15 fps after MJPG — driver may have ignored compression")
                    print("[webcam] warning: still under 15 fps after MJPG", flush=True)
                self._cap = cap
                self._opened = True
                return True
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if attempt < 3:
                time.sleep(0.35)
        return False

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
        now = time.time()
        self._grab_times.append(now)
        if len(self._grab_times) > 45:
            self._grab_times = self._grab_times[-45:]
        if len(self._grab_times) >= 8:
            span = self._grab_times[-1] - self._grab_times[0]
            if span > 0.2:
                live = (len(self._grab_times) - 1) / span
                self._measured_fps = live
        f = Frame(color=color, depth=None, t=now, source="webcam")
        self._last = f
        return f

    def lock_capture(self, exposure: float | None = None) -> None:
        if exposure is not None:
            self._exposure = float(exposure)
        cap = self._cap
        if cap is None:
            return
        # Short fixed exposure so a rolling ball stays a disk, not a smear.
        self._set_prop(cap, cv2.CAP_PROP_AUTO_WB, 0.0, "AUTO_WB=off")
        # 0.25 selects manual exposure under DirectShow.
        self._set_prop(cap, cv2.CAP_PROP_AUTO_EXPOSURE, 0.25, "AUTO_EXPOSURE=manual",
                       tol=0.2)
        self._apply_exposure(cap, self._exposure)
        self._locked = True

    def unlock_capture(self) -> None:
        cap = self._cap
        if cap is None:
            self._locked = False
            return
        self._set_prop(cap, cv2.CAP_PROP_AUTO_WB, 1.0, "AUTO_WB=on")
        # 0.75 selects auto exposure under DirectShow.
        self._set_prop(cap, cv2.CAP_PROP_AUTO_EXPOSURE, 0.75, "AUTO_EXPOSURE=auto",
                       tol=0.2)
        self._locked = False

    def set_exposure(self, value: float) -> None:
        self._exposure = float(max(-8.0, min(-4.0, value)))
        cap = self._cap
        if cap is None:
            return
        # Slider preview: force manual mode so the value actually sticks.
        self._set_prop(cap, cv2.CAP_PROP_AUTO_EXPOSURE, 0.25, "AUTO_EXPOSURE=manual",
                       tol=0.2)
        self._apply_exposure(cap, self._exposure)

    def capture_status(self) -> dict:
        return {
            "measured_fps": float(round(self._measured_fps, 1)),
            "locked": self._locked,
            "exposure": float(self._exposure),
            "ignored": list(self._ignored),
        }

    def _apply_exposure(self, cap: cv2.VideoCapture, value: float) -> None:
        self._set_prop(cap, cv2.CAP_PROP_EXPOSURE, float(value),
                       f"EXPOSURE={value:g}", tol=0.6)

    def _set_prop(self, cap: cv2.VideoCapture, prop: int, value: float,
                  name: str, tol: float = 0.15) -> bool:
        try:
            cap.set(prop, value)
        except Exception:
            self._note_ignored(name)
            log.warning("webcam could not set %s", name)
            print(f"[webcam] warning: could not set {name}", flush=True)
            return False
        return self._check_prop(cap, prop, value, name, tol=tol)

    def _check_prop(self, cap: cv2.VideoCapture, prop: int, wanted: float,
                    name: str, tol: float = 0.15, fourcc: bool = False) -> bool:
        try:
            got = float(cap.get(prop))
        except Exception:
            self._note_ignored(name)
            return False
        if fourcc:
            ok = int(got) == int(wanted)
        else:
            ok = abs(got - wanted) <= tol
        if not ok:
            self._note_ignored(name)
            log.warning("webcam ignored %s (wanted %s, got %s)", name, wanted, got)
            print(f"[webcam] warning: ignored {name} (wanted {wanted}, got {got})",
                  flush=True)
        return ok

    def _note_ignored(self, name: str) -> None:
        if name not in self._ignored:
            self._ignored.append(name)

    def _measure_fps(self, cap: cv2.VideoCapture, n: int = 60) -> float:
        """Time the first ~60 grabs so a 5 fps default cannot hide."""
        for _ in range(4):
            cap.read()
        t0 = time.perf_counter()
        ok_n = 0
        for _ in range(n):
            ok, _frame = cap.read()
            if ok:
                ok_n += 1
        elapsed = time.perf_counter() - t0
        if elapsed <= 0 or ok_n < 2:
            return 0.0
        return ok_n / elapsed
