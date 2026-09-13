"""Regular 2D webcam backend (color-only).

A standard USB/webcam has no depth stream, so the floor cannot be plane-fitted
the way the Kinect backends do. Instead the engine maps pixels -> floor meters
with a homography the user calibrates by clicking the four corners of a
known-size rectangle (see ``vision/geometry.HomographyMapper``). Tracking then
runs on color alone (hue blobs for balls, frame differencing for obstacles).

This backend lives behind the same ``SensorBackend`` interface as everything
else, so the rest of the game is unchanged by the swap.

Capture is negotiated by *measurement*, not driver readback: we try DirectShow
and Media Foundation, two property orders, then step down resolution until a
configuration holds ≥ 25 fps. Exposure lock is verified by actually changing
brightness, not by trusting CAP_PROP_AUTO_EXPOSURE.
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

TARGET_FPS = 25.0
MEASURE_GRABS = 30
FALLBACK_SIZES = ((960, 540), (800, 600), (640, 480))
ORDERS = ("fourcc_first", "size_first")

EXPOSURE_NOTICE = (
    "This camera controls its own exposure. Bright, even room light keeps "
    "tracking fast; dim rooms will slow it down."
)


def fourcc_name(value) -> str:
    """Decode an OpenCV FOURCC int to a 4-character string."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "?"
    chars = [chr((n >> (8 * i)) & 0xFF) for i in range(4)]
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in chars)


def size_ladder(width: int, height: int) -> list[tuple[int, int]]:
    """Requested size first, then smaller fallbacks the driver may sustain."""
    out = [(int(width), int(height))]
    pixels = width * height
    for cand in FALLBACK_SIZES:
        if cand[0] * cand[1] < pixels and cand not in out:
            out.append(cand)
    return out


def capture_apis() -> list[tuple[str, int]]:
    """Windows: DirectShow then Media Foundation. Names stay on DirectShow."""
    if os.name == "nt":
        apis = []
        for name, attr in (("dshow", "CAP_DSHOW"), ("msmf", "CAP_MSMF")):
            code = getattr(cv2, attr, None)
            if code is not None:
                apis.append((name, int(code)))
        return apis or [("any", int(getattr(cv2, "CAP_ANY", 0)))]
    return [("any", int(getattr(cv2, "CAP_ANY", 0)))]


def api_label(name: str) -> str:
    return {"dshow": "DirectShow", "msmf": "Media Foundation"}.get(name, name)


def exposure_pairs(api_name: str) -> list[tuple[float, float]]:
    """(manual, auto) AUTO_EXPOSURE values. Backend-specific pair first."""
    dshow = (0.25, 0.75)
    msmf = (0.0, 1.0)
    if api_name == "msmf":
        return [msmf, dshow]
    return [dshow, msmf]


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


def _backend_api() -> int:
    # Enumeration stays on DirectShow so device names are unchanged.
    if os.name == "nt":
        return int(getattr(cv2, "CAP_DSHOW", getattr(cv2, "CAP_ANY", 0)))
    return int(getattr(cv2, "CAP_ANY", 0))


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


def _apply_format(cap: cv2.VideoCapture, w: int, h: int, order: str) -> tuple[int, int, str]:
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    if order == "fourcc_first":
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(w))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(h))
        cap.set(cv2.CAP_PROP_FPS, 30.0)
    else:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(w))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(h))
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FPS, 30.0)
    try:
        aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or w)
        ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or h)
        fcc = fourcc_name(cap.get(cv2.CAP_PROP_FOURCC))
    except Exception:
        aw, ah, fcc = w, h, "?"
    return aw, ah, fcc


def _open_index(index: int, api: int) -> cv2.VideoCapture | None:
    try:
        cap = cv2.VideoCapture(index, api)
    except Exception:
        return None
    if cap is None or not cap.isOpened():
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        return None
    return cap


def _release(cap: cv2.VideoCapture | None) -> None:
    if cap is None:
        return
    try:
        cap.release()
    except Exception:
        pass


def measure_fps(cap: cv2.VideoCapture, n: int = MEASURE_GRABS) -> float:
    """Time grabs. Bail early if the first slice is already far below 25."""
    for _ in range(5):
        try:
            cap.read()
        except Exception:
            pass
    t0 = time.perf_counter()
    ok_n = 0
    for i in range(n):
        try:
            ok, _frame = cap.read()
        except Exception:
            ok = False
        if ok:
            ok_n += 1
        if i == 9:
            elapsed = time.perf_counter() - t0
            if elapsed > 0 and ok_n / elapsed < 12.0:
                return ok_n / elapsed
    elapsed = time.perf_counter() - t0
    if elapsed <= 0 or ok_n < 2:
        return 0.0
    return ok_n / elapsed


def _mean_brightness(cap: cv2.VideoCapture, n: int = 6) -> float:
    acc = []
    for _ in range(n):
        try:
            ok, frame = cap.read()
        except Exception:
            ok, frame = False, None
        if ok and frame is not None:
            acc.append(float(np.mean(frame)))
    return sum(acc) / len(acc) if acc else 0.0


class WebcamBackend(SensorBackend):
    def __init__(self, index: int = 0, resolution: str = "1280x720",
                 settings=None) -> None:
        w, h = parse_resolution(resolution)
        self.index = index
        self.want_resolution = (w, h)
        self.resolution = (w, h)
        self._settings = settings
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
            exposure_control=True,
            fourcc="",
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
        self._api_name = "dshow"
        self._order = "fourcc_first"
        self._exposure_control = True
        self._ae_manual = 0.25
        self._ae_auto = 0.75
        self._stepped_down = False

    @property
    def has_depth(self) -> bool:
        return False

    def open(self) -> bool:
        # DirectShow is exclusive and can need a beat after another handle
        # (our own enumerator, Zoom, etc.) lets go.
        for attempt in range(3):
            if self._negotiate():
                self._probe_exposure()
                self._persist_cache()
                self._opened = True
                return True
            time.sleep(0.35)
        return False

    def close(self) -> None:
        _release(self._cap)
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
                self.description.fps = float(round(live, 1))
        f = Frame(color=color, depth=None, t=now, source="webcam")
        self._last = f
        return f

    def lock_capture(self, exposure: float | None = None) -> None:
        if exposure is not None:
            self._exposure = float(exposure)
        cap = self._cap
        if cap is None:
            return
        if not self._exposure_control:
            self._locked = False
            return
        self._set_prop(cap, cv2.CAP_PROP_AUTO_WB, 0.0, "AUTO_WB=off")
        try:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self._ae_manual)
        except Exception:
            pass
        self._apply_exposure(cap, self._exposure)
        self._locked = True

    def unlock_capture(self) -> None:
        cap = self._cap
        if cap is None:
            self._locked = False
            return
        self._set_prop(cap, cv2.CAP_PROP_AUTO_WB, 1.0, "AUTO_WB=on")
        try:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self._ae_auto)
        except Exception:
            pass
        self._locked = False

    def set_exposure(self, value: float) -> None:
        self._exposure = float(max(-8.0, min(-4.0, value)))
        cap = self._cap
        if cap is None or not self._exposure_control:
            return
        try:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self._ae_manual)
        except Exception:
            pass
        self._apply_exposure(cap, self._exposure)

    def open_driver_settings(self) -> bool:
        """Windows DirectShow property page. No-op elsewhere."""
        if os.name != "nt":
            return False
        cap = self._cap
        if cap is None:
            return False
        try:
            ok = bool(cap.set(cv2.CAP_PROP_SETTINGS, 1))
        except Exception:
            ok = False
        if ok:
            print("[webcam] opened driver settings dialog", flush=True)
        return ok

    def capture_status(self) -> dict:
        return {
            "measured_fps": float(round(self._measured_fps, 1)),
            "locked": bool(self._locked and self._exposure_control),
            "exposure": float(self._exposure),
            "ignored": list(self._ignored),
            "exposure_control": bool(self._exposure_control),
            "fourcc": self.description.fourcc or "",
            "capture_api": self._api_name,
            "negotiated_resolution": f"{self.resolution[0]}x{self.resolution[1]}",
            "show_driver_settings": os.name == "nt" and not self._exposure_control,
        }

    # -- negotiation -------------------------------------------------------- #
    def _negotiate(self) -> bool:
        want_w, want_h = self.want_resolution
        cached = self._cached_config()
        plans = self._plans(want_w, want_h, cached)
        best = None  # (fps, cap, meta)

        for api_name, api, order, w, h in plans:
            cap = _open_index(self.index, api)
            if cap is None:
                log.info("webcam skip %s (could not open index %s)",
                         api_label(api_name), self.index)
                continue
            aw, ah, fcc = _apply_format(cap, w, h, order)
            fps = measure_fps(cap)
            line = (f"[webcam] {api_label(api_name)} order={order} "
                    f"asked {w}x{h} got {aw}x{ah} FOURCC={fcc} "
                    f"measured {fps:.1f} fps")
            log.info(line)
            print(line, flush=True)
            cache_hit = False
            if cached:
                cw, ch = parse_resolution(str(cached.get("resolution") or "0x0"))
                cache_hit = (api_name, order, w, h) == (
                    str(cached.get("api") or ""),
                    str(cached.get("order") or ""),
                    cw, ch,
                )
            if fps >= TARGET_FPS or (
                cache_hit and cached.get("ok") is False and fps >= 5.0
            ):
                self._adopt(cap, api_name, order, aw, ah, fcc, fps,
                            asked=(w, h), want=(want_w, want_h))
                return True
            meta = (api_name, order, aw, ah, fcc, fps)
            if best is None or fps > best[0]:
                if best is not None:
                    _release(best[1])
                best = (fps, cap, meta)
            else:
                _release(cap)

        if best is None:
            return False
        fps, cap, meta = best
        api_name, order, aw, ah, fcc, fps = meta
        self._adopt(cap, api_name, order, aw, ah, fcc, fps,
                    asked=(aw, ah), want=(want_w, want_h))
        return True

    def _plans(self, want_w: int, want_h: int, cached: dict | None):
        apis = capture_apis()
        sizes = size_ladder(want_w, want_h)
        seen: set[tuple] = set()
        out = []
        if cached:
            api_name = str(cached.get("api") or "")
            order = str(cached.get("order") or "fourcc_first")
            cw, ch = parse_resolution(str(cached.get("resolution") or f"{want_w}x{want_h}"))
            api_code = dict(apis).get(api_name)
            if api_code is not None:
                key = (api_name, order, cw, ch)
                seen.add(key)
                out.append((api_name, api_code, order, cw, ch))
        for w, h in sizes:
            for api_name, api_code in apis:
                for order in ORDERS:
                    key = (api_name, order, w, h)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((api_name, api_code, order, w, h))
        return out

    def _adopt(self, cap, api_name, order, w, h, fcc, fps, asked, want) -> None:
        _release(self._cap)
        self._cap = cap
        self._api_name = api_name
        self._order = order
        self.resolution = (int(w), int(h))
        self._measured_fps = float(fps)
        self._stepped_down = (int(w) * int(h) < want[0] * want[1]
                             or asked[0] * asked[1] < want[0] * want[1])
        self.description.color_res = (int(w), int(h))
        self.description.fps = float(round(fps, 1))
        self.description.fourcc = fcc
        if self._stepped_down:
            msg = (f"Camera limited to {w}x{h} @ {fps:.1f} fps "
                   f"(driver refused MJPG at higher res).")
            log.warning(msg)
            print(f"[webcam] {msg}", flush=True)
            self.description.note = (
                f"Color-only — limited to {w}×{h} @ {fps:.0f} fps "
                f"(driver refused MJPG at {want[0]}×{want[1]})."
            )
        else:
            self.description.note = "Color-only — floor mapped by a 4-corner homography."
        self.cam = CameraModel(
            fx=float(w), fy=float(w), cx=w / 2.0, cy=h / 2.0, color_scale=1.0
        )
        print(f"[webcam] using {api_label(api_name)} FOURCC={fcc} "
              f"{w}x{h} @ {fps:.1f} fps", flush=True)

    def _cached_config(self) -> dict | None:
        if self._settings is None:
            return None
        raw = self._settings.get("camera", "negotiated", default=None)
        if not isinstance(raw, dict):
            return None
        want = f"{self.want_resolution[0]}x{self.want_resolution[1]}"
        if int(raw.get("device", -1)) != int(self.index):
            return None
        if str(raw.get("want") or "") != want:
            return None
        return raw

    def _persist_cache(self) -> None:
        if self._settings is None:
            return
        payload = {
            "device": int(self.index),
            "want": f"{self.want_resolution[0]}x{self.want_resolution[1]}",
            "api": self._api_name,
            "order": self._order,
            "resolution": f"{self.resolution[0]}x{self.resolution[1]}",
            "fourcc": self.description.fourcc,
            "fps": float(round(self._measured_fps, 1)),
            "ok": self._measured_fps >= TARGET_FPS,
        }
        self._settings.set(payload, "camera", "negotiated")
        try:
            self._settings.save()
        except Exception:
            pass

    # -- exposure ----------------------------------------------------------- #
    def _probe_exposure(self) -> None:
        cap = self._cap
        if cap is None:
            self._exposure_control = False
            return
        target = self._exposure
        for manual, auto in exposure_pairs(self._api_name):
            try:
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, manual)
            except Exception:
                continue
            if self._brightness_moves(cap, target):
                self._ae_manual = manual
                self._ae_auto = auto
                self._exposure_control = True
                self.description.exposure_control = True
                print(f"[webcam] exposure control: yes "
                      f"(manual AUTO_EXPOSURE={manual:g})", flush=True)
                log.info("webcam exposure control works (manual=%s)", manual)
                return
        self._exposure_control = False
        self._locked = False
        self.description.exposure_control = False
        self._note_ignored("exposure control (brightness unchanged)")
        print("[webcam] exposure control: no — " + EXPOSURE_NOTICE, flush=True)
        log.warning("webcam exposure control unsupported")

    def _brightness_moves(self, cap: cv2.VideoCapture, target: float) -> bool:
        other = target + 2.0 if target <= -6.0 else target - 2.0
        try:
            cap.set(cv2.CAP_PROP_EXPOSURE, float(target))
        except Exception:
            return False
        time.sleep(0.12)
        a = _mean_brightness(cap)
        try:
            cap.set(cv2.CAP_PROP_EXPOSURE, float(other))
        except Exception:
            return False
        time.sleep(0.12)
        b = _mean_brightness(cap)
        try:
            cap.set(cv2.CAP_PROP_EXPOSURE, float(target))
        except Exception:
            pass
        lo, hi = min(a, b), max(a, b)
        if hi < 1.0:
            return False
        ratio = hi / max(1.0, lo)
        delta = abs(a - b)
        ok = ratio >= 1.18 or delta >= 8.0
        log.info("webcam brightness probe %.1f -> %.1f (ratio %.2f, d %.1f) %s",
                 a, b, ratio, delta, "ok" if ok else "no")
        return ok

    def _apply_exposure(self, cap: cv2.VideoCapture, value: float) -> None:
        try:
            cap.set(cv2.CAP_PROP_EXPOSURE, float(value))
        except Exception:
            self._note_ignored(f"EXPOSURE={value:g}")

    def _set_prop(self, cap: cv2.VideoCapture, prop: int, value: float,
                  name: str, tol: float = 0.15) -> bool:
        try:
            cap.set(prop, value)
        except Exception:
            self._note_ignored(name)
            log.warning("webcam could not set %s", name)
            print(f"[webcam] warning: could not set {name}", flush=True)
            return False
        return True

    def _note_ignored(self, name: str) -> None:
        if name not in self._ignored:
            self._ignored.append(name)
