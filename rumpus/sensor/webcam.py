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
import struct
import time

# Safe even if cv2 is already imported — also set in rumpus/__init__.py.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2
import numpy as np

from ..models import CameraModel, Frame, SensorDescription
from .base import SensorBackend

log = logging.getLogger("rumpus.sensor.webcam")

TARGET_FPS = 25.0
MEASURE_GRABS = 30
HANDOFF_GRABS = 12
GRAB_MISS_RECOVER = 6
FALLBACK_SIZES = ((1280, 720), (960, 540), (800, 600), (640, 480))
ORDERS = ("fourcc_first", "size_first")

EXPOSURE_NOTICE = (
    "This camera controls its own exposure. Bright, even room light keeps "
    "tracking fast; dim rooms will slow it down."
)


def _fourcc_chars(n: int) -> str:
    chars = [chr((int(n) >> (8 * i)) & 0xFF) for i in range(4)]
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in chars)


def fourcc_name(value) -> str:
    """Decode an OpenCV FOURCC value to a 4-character string.

    Media Foundation returns CAP_PROP_FOURCC as a float. Large FOURCC ints
    do not survive float32, so we also reinterpret the IEEE bits.
    """
    candidates: list[int] = []
    try:
        candidates.append(int(round(float(value))))
    except (TypeError, ValueError):
        return "?"
    try:
        candidates.append(struct.unpack("<I", struct.pack("<f", float(value)))[0])
    except (TypeError, ValueError, struct.error, OverflowError):
        pass
    for n in candidates:
        name = _fourcc_chars(n)
        if name and "?" not in name:
            return name
    return _fourcc_chars(candidates[0]) if candidates else "?"


def exposure_shutter(value: float) -> tuple[int, float]:
    """(denominator of ~1/N s, theoretical fps cap) for a log2-second exposure."""
    steps = max(1, int(round(-float(value))))
    return (1 << steps), float(1 << steps)


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


_VIRTUAL_CAM = (
    "iriun", "obs virtual", "many-cam", "manycam", "snap camera",
    "mmhmm", "nvidia broadcast", "xsplit",
)


def is_virtual_camera(name: str) -> bool:
    n = (name or "").lower()
    return any(token in n for token in _VIRTUAL_CAM)


def unique_device_label(names: list[str], index: int) -> str:
    """Disambiguate duplicate driver names: 'HD Pro Webcam C920 (2)'."""
    if index < 0 or index >= len(names):
        return f"Camera {index}"
    name = names[index]
    same = [i for i, n in enumerate(names) if n == name]
    if len(same) <= 1:
        return name
    return f"{name} ({same.index(index) + 1})"


def alternate_device_indices(index: int, names: list[str] | None = None) -> list[int]:
    """Same-name siblings first (the other C920 listing), then other real cams."""
    if names is None:
        try:
            from .devices import list_capture_names
            names = list_capture_names()
        except Exception:
            return []
    idx = int(index)
    mine = names[idx] if 0 <= idx < len(names) else None
    siblings: list[int] = []
    others: list[int] = []
    for i, n in enumerate(names):
        if i == idx:
            continue
        if mine and n == mine:
            siblings.append(i)
        elif not is_virtual_camera(n):
            others.append(i)
    return siblings + others


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
            {"index": i, "name": unique_device_label(names, i),
             "driver": n, "working": True}
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
    if not fcc or "?" in fcc:
        fcc = "unknown"
    try:
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
    except Exception:
        pass
    # C920 at 1080p often stays on YUY2 (~5 fps) unless MJPG is forced again.
    if fcc.upper() in ("YUY2", "YUYV") and w >= 1280:
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(w))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(h))
        cap.set(cv2.CAP_PROP_FPS, 30.0)
        try:
            aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or aw)
            ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or ah)
            fcc = fourcc_name(cap.get(cv2.CAP_PROP_FOURCC)) or fcc
        except Exception:
            pass
        if not fcc or "?" in fcc:
            fcc = "unknown"
    return aw, ah, fcc


def _prime_exposure(cap: cv2.VideoCapture, api_name: str, exposure: float) -> None:
    """Best-effort manual exposure so fps is measured at the shutter we'll use."""
    for manual, _auto in exposure_pairs(api_name):
        try:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, manual)
            cap.set(cv2.CAP_PROP_EXPOSURE, float(exposure))
            return
        except Exception:
            continue


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


def frame_has_picture(frame) -> bool:
    """False for the empty near-black buffers MSMF often returns at 30 fps."""
    if frame is None:
        return False
    try:
        img = np.asarray(frame)
    except Exception:
        return False
    if img.size == 0 or img.ndim < 2:
        return False
    sample = img[::8, ::8]
    mean = float(sample.mean())
    std = float(sample.std())
    peak = float(sample.max())
    if peak < 16.0 and mean < 8.0:
        return False
    if std < 2.0 and mean < 12.0:
        return False
    return True


def measure_fps(cap: cv2.VideoCapture, n: int = MEASURE_GRABS) -> float:
    """Time grabs that actually contain a picture. Black MSMF buffers do not count."""
    for _ in range(5):
        try:
            cap.read()
        except Exception:
            pass
    t0 = time.perf_counter()
    pictured = 0
    for i in range(n):
        try:
            ok, frame = cap.read()
        except Exception:
            ok, frame = False, None
        if ok and frame_has_picture(frame):
            pictured += 1
        if i == 9:
            elapsed = time.perf_counter() - t0
            if pictured == 0:
                return 0.0
            if elapsed > 0 and pictured / elapsed < 12.0:
                return pictured / elapsed
    elapsed = time.perf_counter() - t0
    if elapsed <= 0 or pictured < 2:
        return 0.0
    return pictured / elapsed


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
        self.index = int(index)
        self.want_resolution = (w, h)
        self.resolution = (w, h)
        self._settings = settings
        try:
            from .devices import list_capture_names
            names = list_capture_names()
            model = unique_device_label(names, index) if names else f"Webcam {index}"
        except Exception:
            model = f"Webcam {index}"
        self.description = SensorDescription(
            model=model,
            color_res=(w, h),
            depth_res=(0, 0),          # color-only marker
            fov_h_deg=70.0,
            reliable_min_m=0.5,
            reliable_max_m=5.0,
            note="Webcam — no depth. Floor mapped by a 4-corner homography.",
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
        if settings is not None:
            try:
                self._exposure = float(settings.get("camera", "exposure", default=-6) or -6)
            except (TypeError, ValueError):
                self._exposure = -6.0
        self._ignored: list[str] = []
        self._grab_times: list[float] = []
        self._api_name = "dshow"
        self._order = "fourcc_first"
        self._exposure_control = True
        self._ae_manual = 0.25
        self._ae_auto = 0.75
        self._stepped_down = False
        self._misses = 0
        self._recovering = False
        self._recover_after = 0.0

    @property
    def has_depth(self) -> bool:
        return False

    def open(self) -> bool:
        # DirectShow is exclusive and can need a beat after another handle
        # (our own enumerator, Zoom, etc.) lets go.
        tried = {int(self.index)}
        if self._negotiate():
            self._probe_exposure()
            self._persist_cache()
            self._opened = True
            return True
        # Exclusive APIs sometimes need a beat after Zoom / our enumerator.
        time.sleep(0.35)
        if self._negotiate():
            self._probe_exposure()
            self._persist_cache()
            self._opened = True
            return True
        # Windows often lists the same webcam twice; one moniker is a black
        # stub, the other has the picture. Try the sibling index next.
        for idx in alternate_device_indices(self.index):
            if idx in tried:
                continue
            print(f"[webcam] index {self.index} has no picture — trying {idx}",
                  flush=True)
            self.index = idx
            tried.add(idx)
            try:
                from .devices import list_capture_names
                names = list_capture_names()
                if 0 <= idx < len(names):
                    self.description.model = unique_device_label(names, idx)
            except Exception:
                self.description.model = f"Webcam {idx}"
            if self._negotiate():
                self._probe_exposure()
                self._persist_cache()
                self._opened = True
                return True
        return False

    def close(self) -> None:
        _release(self._cap)
        self._cap = None
        self._opened = False
        self._last = None
        self._misses = 0

    def detach_handle(self) -> None:
        """Drop the capture handle but keep the negotiated plan.

        Media Foundation (and often DirectShow) only grab reliably on the
        thread that opened the handle. Background negotiation opens, then
        the game loop calls ``reopen_on_this_thread``.
        """
        _release(self._cap)
        self._cap = None
        self._opened = False
        self._last = None

    def reopen_on_this_thread(self, full: bool = False) -> bool:
        """Open a pictured stream on the calling thread.

        Tries the other API and 720p first — MSMF at 1080p often returns a
        30 fps black buffer that still counts as a successful ``read()``.
        """
        _release(self._cap)
        self._cap = None
        self._last = None
        sizes: list[tuple[int, int]] = [self.resolution]
        if os.name == "nt":
            sizes.append((1280, 720))
        sizes.append(self.want_resolution)
        uniq: list[tuple[int, int]] = []
        seen_sz: set[tuple[int, int]] = set()
        for sz in sizes:
            if sz[0] > 0 and sz not in seen_sz:
                seen_sz.add(sz)
                uniq.append(sz)
        others = [name for name, _code in capture_apis() if name != self._api_name]
        candidates = [self._api_name] + others
        orders = [self._order] + [o for o in ORDERS if o != self._order]
        tried: set[tuple[str, str, int, int]] = set()
        for w, h in uniq:
            for api_name in candidates:
                for order in orders:
                    key = (api_name, order, w, h)
                    if key in tried:
                        continue
                    tried.add(key)
                    if self._open_plan(api_name, order, w, h):
                        self._opened = True
                        self._misses = 0
                        self._persist_cache()
                        return True
        if full:
            if self.open():
                self._misses = 0
                return True
        self._opened = False
        return False

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
        pictured = bool(ok and frame is not None and frame_has_picture(frame))
        if not pictured:
            self._misses += 1
            if (
                self._misses >= GRAB_MISS_RECOVER
                and not self._recovering
                and time.time() >= self._recover_after
            ):
                self._recovering = True
                try:
                    print("[webcam] stream stalled — reopening on this thread",
                          flush=True)
                    if self.reopen_on_this_thread(full=False):
                        self._misses = 0
                        cap = self._cap
                        if cap is not None:
                            try:
                                ok, frame = cap.read()
                            except Exception:
                                ok, frame = False, None
                            pictured = bool(
                                ok and frame is not None and frame_has_picture(frame)
                            )
                    else:
                        self._recover_after = time.time() + 2.5
                        self._misses = 0
                finally:
                    self._recovering = False
        if not pictured:
            if self._last is not None:
                return self._last
            return Frame(color=None, depth=None, t=time.time(), source="webcam")
        self._misses = 0
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
            "shutter_denom": exposure_shutter(self._exposure)[0],
            "shutter_fps_cap": exposure_shutter(self._exposure)[1],
        }

    # -- negotiation -------------------------------------------------------- #
    def _negotiate(self) -> bool:
        want_w, want_h = self.want_resolution
        cached = self._cached_config()
        plans = self._plans(want_w, want_h, cached)
        best = None  # (fps, api_name, order, w, h, aw, ah, fcc)
        cur_size: tuple[int, int] | None = None
        size_max_fps = 0.0
        black_sizes = 0

        for api_name, api, order, w, h in plans:
            if cur_size != (w, h):
                if cur_size is not None:
                    if size_max_fps < 1.0:
                        black_sizes += 1
                        if black_sizes >= 2:
                            print(f"[webcam] index {self.index} has no picture "
                                  f"— skipping remaining sizes", flush=True)
                            return False
                    else:
                        black_sizes = 0
                cur_size = (w, h)
                size_max_fps = 0.0
            cap = _open_index(self.index, api)
            if cap is None:
                log.info("webcam skip %s (could not open index %s)",
                         api_label(api_name), self.index)
                continue
            aw, ah, fcc = _apply_format(cap, w, h, order)
            _prime_exposure(cap, api_name, self._exposure)
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
            accept = fps >= TARGET_FPS or (
                cache_hit and cached.get("ok") is False and fps >= 5.0
            )
            if accept:
                self._adopt(cap, api_name, order, aw, ah, fcc, fps,
                            asked=(w, h), want=(want_w, want_h))
                return True
            # Release before the next open — exclusive APIs (DShow/MSMF)
            # cannot share the device, so a held runner-up blocks the rest.
            _release(cap)
            size_max_fps = max(size_max_fps, fps)
            if best is None or fps > best[0]:
                best = (fps, api_name, order, w, h, aw, ah, fcc)

        if best is None or best[0] < 5.0:
            return False
        fps, api_name, order, w, h, aw, ah, fcc = best
        return self._open_plan(api_name, order, w, h)

    def _open_plan(self, api_name: str, order: str, w: int, h: int) -> bool:
        """Open one API/size/order on this thread. True when grabs stay alive."""
        api = dict(capture_apis()).get(api_name)
        if api is None:
            return False
        cap = _open_index(self.index, api)
        if cap is None:
            return False
        aw, ah, fcc = _apply_format(cap, w, h, order)
        _prime_exposure(cap, api_name, self._exposure)
        fps = measure_fps(cap, n=HANDOFF_GRABS)
        line = (f"[webcam] {api_label(api_name)} order={order} "
                f"asked {w}x{h} got {aw}x{ah} FOURCC={fcc} "
                f"handoff {fps:.1f} fps")
        log.info(line)
        print(line, flush=True)
        if fps < 5.0:
            _release(cap)
            return False
        self._adopt(cap, api_name, order, aw, ah, fcc, fps,
                    asked=(w, h), want=self.want_resolution)
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
            by_name = dict(apis)
            # DirectShow first at the known-good size. MSMF often measures
            # fine then dies on the next thread (HRESULT -1072873821).
            if os.name == "nt" and "dshow" in by_name:
                key = ("dshow", order, cw, ch)
                seen.add(key)
                out.append(("dshow", by_name["dshow"], order, cw, ch))
            api_code = by_name.get(api_name)
            if api_code is not None:
                key = (api_name, order, cw, ch)
                if key not in seen:
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
            self.description.note = "Webcam — no depth. Floor mapped by a 4-corner homography."
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
        if "exposure" not in raw:
            return None
        try:
            if abs(float(raw.get("exposure")) - float(self._exposure)) > 0.1:
                return None
        except (TypeError, ValueError):
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
            "exposure": float(self._exposure),
        }
        self._settings.set(payload, "camera", "negotiated")
        try:
            if int(self._settings.get("camera", "device", default=self.index)) != int(self.index):
                self._settings.set(int(self.index), "camera", "device")
        except (TypeError, ValueError):
            self._settings.set(int(self.index), "camera", "device")
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
