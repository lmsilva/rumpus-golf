"""Kinect v1 through Microsoft's Kinect for Windows runtime (``Kinect10.dll``).

This is the backend that actually works on a stock Windows box. The other route,
libfreenect, needs libusbK bound to the camera interface, which means *removing*
Microsoft's ``KinectCamera`` driver with Zadig — destructive, and pointless when
the official runtime is already installed and healthy. The runtime ships
``Kinect10.dll`` in System32, so ctypes can drive it with nothing to compile.

Two things this file has to do that the driver will not do for us:

- Unpack depth. The stream hands back 16-bit words that may or may not carry a
  3-bit player index, depending on how the runtime was initialized, so the shift
  is measured from the data at open() instead of assumed.
- Register depth onto color. The flat API only exposes a *per-pixel* mapper
  (``NuiImageGetColorPixelCoordinatesFromDepthPixelAtResolution``), which is far
  too slow for 307k pixels a frame through ctypes, so the transform is done
  here in numpy. ``CameraModel`` assumes registered depth (``color_scale=1``).
"""
from __future__ import annotations

import ctypes as C
import threading
import time
from ctypes import wintypes as W

import cv2
import numpy as np

from ..models import CameraModel, Frame, SensorDescription
from .base import SensorBackend

# --------------------------------------------------------------------------- #
# NUI constants (NuiImageCamera.h)
# --------------------------------------------------------------------------- #
NUI_IMAGE_TYPE_COLOR = 1
NUI_IMAGE_TYPE_DEPTH = 4
NUI_IMAGE_RESOLUTION_640x480 = 2
NUI_INITIALIZE_FLAG_USES_COLOR = 0x00000002
NUI_INITIALIZE_FLAG_USES_DEPTH = 0x00000020

COLOR_W, COLOR_H = 640, 480
DEPTH_W, DEPTH_H = 640, 480

# Kinect v1 factory calibration (the OpenKinect / Burrus values libfreenect's
# own registration is built on). Per-device EEPROM values would be marginally
# better, but the floor calibration the player does absorbs a pixel of offset.
_DEPTH_FX, _DEPTH_FY, _DEPTH_CX, _DEPTH_CY = 594.21, 591.04, 339.31, 242.74
_COLOR_FX, _COLOR_FY, _COLOR_CX, _COLOR_CY = 529.22, 525.56, 328.94, 267.48
_R = np.array([
    [9.9984628826577793e-01, 1.2635359098409581e-03, -1.7487233004436643e-02],
    [-1.4779096108364480e-03, 9.9992385683542895e-01, -1.2251380107679535e-02],
    [1.7470421412464927e-02, 1.2275341476520762e-02, 9.9977202419716948e-01],
])
_T = np.array([1.9985242312092553e-02, -7.4423738761617583e-04,
               -1.0916736334336222e-02])  # meters

# The sensor cannot see nearer than ~0.4 m or further than ~8 m, so anything
# outside that is a decoding mistake rather than geometry.
_MIN_VALID_MM, _MAX_VALID_MM = 400, 8000


class _VIEW_AREA(C.Structure):
    _fields_ = [("eDigitalZoom", C.c_int), ("lCenterX", C.c_long),
                ("lCenterY", C.c_long)]


class _IMAGE_FRAME(C.Structure):
    _fields_ = [("liTimeStamp", C.c_longlong), ("dwFrameNumber", W.DWORD),
                ("eImageType", C.c_int), ("eResolution", C.c_int),
                ("pFrameTexture", C.c_void_p), ("dwFrameFlags", W.DWORD),
                ("ViewArea", _VIEW_AREA)]


class _LOCKED_RECT(C.Structure):
    _fields_ = [("Pitch", C.c_int), ("size", C.c_int),
                ("pBits", C.POINTER(C.c_ubyte))]


# INuiFrameTexture derives from IUnknown, so its own methods start after
# QueryInterface / AddRef / Release.
_VT_LOCK_RECT = 5
_VT_UNLOCK_RECT = 7
# c_int32 rather than ctypes.HRESULT throughout: "no frame ready yet" comes back
# as a failure HRESULT (E_NUI_FRAME_NO_DATA) many times a second, and HRESULT
# makes ctypes raise on it. Paying for an exception at frame rate is wasteful,
# and it buries the failures that actually mean something.
_LockRectFn = C.WINFUNCTYPE(C.c_int32, C.c_void_p, C.c_uint,
                            C.POINTER(_LOCKED_RECT), C.c_void_p, W.DWORD)
_UnlockRectFn = C.WINFUNCTYPE(C.c_int32, C.c_void_p, C.c_uint)

E_NUI_FRAME_NO_DATA = 0x83010001

# NuiInitialize is process-wide, so two backends must never race it.
_nui_lock = threading.Lock()


def _load_dll() -> C.WinDLL | None:
    """Load Kinect10.dll and bind the handful of calls we use."""
    try:
        k = C.WinDLL("Kinect10.dll")
    except OSError:
        return None
    try:
        k.NuiGetSensorCount.argtypes = [C.POINTER(C.c_int)]
        k.NuiGetSensorCount.restype = C.c_int32
        k.NuiInitialize.argtypes = [W.DWORD]
        k.NuiInitialize.restype = C.c_int32
        k.NuiShutdown.argtypes = []
        k.NuiShutdown.restype = None
        k.NuiImageStreamOpen.argtypes = [C.c_int, C.c_int, W.DWORD, W.DWORD,
                                         W.HANDLE, C.POINTER(W.HANDLE)]
        k.NuiImageStreamOpen.restype = C.c_int32
        k.NuiImageStreamGetNextFrame.argtypes = [
            W.HANDLE, W.DWORD, C.POINTER(C.POINTER(_IMAGE_FRAME))]
        k.NuiImageStreamGetNextFrame.restype = C.c_int32
        k.NuiImageStreamReleaseFrame.argtypes = [W.HANDLE,
                                                 C.POINTER(_IMAGE_FRAME)]
        k.NuiImageStreamReleaseFrame.restype = C.c_int32
        k.NuiCameraElevationSetAngle.argtypes = [C.c_long]
        k.NuiCameraElevationSetAngle.restype = C.c_int32
    except AttributeError:
        return None
    return k


def sensor_count() -> int:
    """How many Kinects the Microsoft runtime can see (0 when it is absent)."""
    k = _load_dll()
    if k is None:
        return 0
    n = C.c_int(0)
    try:
        if k.NuiGetSensorCount(C.byref(n)) != 0:
            return 0
    except OSError:
        return 0
    return max(0, int(n.value))


def _vtable_call(this: int, slot: int, proto):
    vtbl = C.cast(this, C.POINTER(C.c_void_p))[0]
    return proto(C.cast(vtbl, C.POINTER(C.c_void_p))[slot])


def _texture_bytes(texture: int) -> bytes | None:
    """Copy one locked frame texture out, then release it.

    The runtime recycles the buffer as soon as the frame is released, so this
    has to be a copy — a numpy view over it would decode a later frame.
    """
    rect = _LOCKED_RECT()
    if _vtable_call(texture, _VT_LOCK_RECT, _LockRectFn)(
            texture, 0, C.byref(rect), None, 0) != 0:
        return None
    try:
        if not rect.pBits or rect.size <= 0:
            return None
        return C.string_at(rect.pBits, rect.size)
    finally:
        _vtable_call(texture, _VT_UNLOCK_RECT, _UnlockRectFn)(texture, 0)


def depth_shift_for(raw: np.ndarray) -> int:
    """Whether the 16-bit depth words carry a 3-bit player index.

    Initializing with USES_DEPTH alone is documented to give plain millimeters,
    but runtimes disagree, and guessing wrong scales the whole world by 8. The
    data settles it: only one reading puts the room inside the sensor's range.
    """
    nz = raw[raw > 0]
    if nz.size == 0:
        return 0
    plain = np.count_nonzero((nz >= _MIN_VALID_MM) & (nz <= _MAX_VALID_MM))
    shifted = nz >> 3
    packed = np.count_nonzero((shifted >= _MIN_VALID_MM)
                              & (shifted <= _MAX_VALID_MM))
    return 3 if packed > plain else 0


class _Registrar:
    """Projects depth pixels into the color frame.

    A frame is 307k pixels and the game loop wants 30 of them a second, so the
    arithmetic is arranged to touch each pixel as few times as possible:

    - The rotation folds into a per-pixel constant. Since ``x = rx * Z``, the
      color-space coordinate is ``Z * (R00*rx + R01*ry + R02) + Tx``, and that
      bracket does not depend on ``Z`` — so it is computed once here, not nine
      multiply-adds per pixel per frame.
    - Everything stays float32 and every constant is a plain Python float.
      A stray numpy float64 scalar promotes the whole array under NEP 50, which
      alone cost more than all the rest of the work put together.
    - Millimeters throughout, so there is no metres conversion to pay for.
    """

    def __init__(self) -> None:
        u = np.arange(DEPTH_W, dtype=np.float32)
        v = np.arange(DEPTH_H, dtype=np.float32)
        uu, vv = np.meshgrid(u, v)
        rx = (uu - _DEPTH_CX) / _DEPTH_FX
        ry = (vv - _DEPTH_CY) / _DEPTH_FY
        self._ax = (_R[0, 0] * rx + _R[0, 1] * ry + _R[0, 2]).astype(np.float32)
        self._ay = (_R[1, 0] * rx + _R[1, 1] * ry + _R[1, 2]).astype(np.float32)
        self._az = (_R[2, 0] * rx + _R[2, 1] * ry + _R[2, 2]).astype(np.float32)
        self._tx = float(_T[0] * 1000.0)
        self._ty = float(_T[1] * 1000.0)
        self._tz = float(_T[2] * 1000.0)
        self._fx = float(_COLOR_FX)
        self._fy = float(_COLOR_FY)
        # The half-pixel folds the rounding into the truncating int cast.
        self._cx = float(_COLOR_CX) + 0.5
        self._cy = float(_COLOR_CY) + 0.5

    def __call__(self, depth_mm: np.ndarray) -> np.ndarray:
        d = depth_mm.astype(np.float32)
        with np.errstate(divide="ignore", invalid="ignore"):
            cz = d * self._az + self._tz
            u = (d * self._ax + self._tx) * self._fx / cz + self._cx
            v = (d * self._ay + self._ty) * self._fy / cz + self._cy
        ui = u.astype(np.int32)
        vi = v.astype(np.int32)
        good = ((depth_mm > 0) & (ui >= 0) & (ui < COLOR_W)
                & (vi >= 0) & (vi < COLOR_H))

        dd = depth_mm[good]
        # Nearest wins. Sorting descending means a near surface is written last
        # and overwrites the background it occludes, instead of the background
        # punching a hole through the ball. Integer depths make this a radix
        # sort, which is far cheaper than sorting the floats.
        order = np.argsort(dd, kind="stable")[::-1]
        flat = (vi[good] * COLOR_W + ui[good])[order]
        dd = dd[order].astype(np.float32)

        out = np.zeros(COLOR_H * COLOR_W, np.float32)
        out[flat] = dd
        # One depth pixel covers slightly more than one color pixel, so a bare
        # scatter leaves a lattice of holes across every surface. Filling only
        # pixels that are still empty closes it without smearing an object's
        # depth over the background at its edges.
        for off in (1, COLOR_W, COLOR_W + 1):
            tgt = np.clip(flat + off, 0, out.size - 1)
            empty = out[tgt] == 0
            out[tgt[empty]] = dd[empty]
        return out.reshape(COLOR_H, COLOR_W)


class KinectNuiBackend(SensorBackend):
    """Kinect v1 via the Microsoft runtime, delivering registered depth."""

    def __init__(self) -> None:
        self.description = SensorDescription(
            model="Kinect v1",
            color_res=(COLOR_W, COLOR_H),
            depth_res=(COLOR_W, COLOR_H),  # registered onto color
            fov_h_deg=57.0,
            reliable_min_m=0.8,
            reliable_max_m=4.0,
            note="Struggles in direct sunlight — draw the curtains.",
            fps=30.0,
            exposure_control=False,
        )
        # Real color intrinsics beat default_camera()'s guess, and depth is
        # registered onto color so these are the ones that apply.
        self.cam = CameraModel(fx=_COLOR_FX, fy=_COLOR_FY,
                               cx=_COLOR_CX, cy=_COLOR_CY, color_scale=1.0)
        self._k: C.WinDLL | None = None
        self._color_stream: W.HANDLE | None = None
        self._depth_stream: W.HANDLE | None = None
        self._initialized = False
        self._depth_shift: int | None = None
        self._register = _Registrar()
        # The two streams arrive out of step, so each is banked here until the
        # other catches up.
        self._color: np.ndarray | None = None
        self._depth: np.ndarray | None = None
        self._last: Frame | None = None
        self._fps_at = 0.0
        self._fps_n = 0
        self._measured_fps = 30.0

    # -- lifecycle ---------------------------------------------------------- #
    def open(self) -> bool:
        k = _load_dll()
        if k is None:
            return False
        n = C.c_int(0)
        try:
            if k.NuiGetSensorCount(C.byref(n)) != 0 or n.value < 1:
                return False
        except OSError:
            return False

        with _nui_lock:
            # A previous handle in this process leaves the sensor initialized;
            # shutting down first makes open() safe to retry.
            try:
                k.NuiShutdown()
            except Exception:
                pass
            try:
                hr = k.NuiInitialize(NUI_INITIALIZE_FLAG_USES_COLOR
                                     | NUI_INITIALIZE_FLAG_USES_DEPTH)
            except OSError:
                return False
            if hr != 0:
                print(f"[kinect] NuiInitialize failed (0x{hr & 0xFFFFFFFF:08X})",
                      flush=True)
                return False
            self._k = k
            self._initialized = True

            color = W.HANDLE()
            depth = W.HANDLE()
            ok_c = k.NuiImageStreamOpen(
                NUI_IMAGE_TYPE_COLOR, NUI_IMAGE_RESOLUTION_640x480,
                0, 2, None, C.byref(color)) == 0
            ok_d = k.NuiImageStreamOpen(
                NUI_IMAGE_TYPE_DEPTH, NUI_IMAGE_RESOLUTION_640x480,
                0, 2, None, C.byref(depth)) == 0
            if not (ok_c and ok_d):
                print("[kinect] could not open the color and depth streams",
                      flush=True)
                self._teardown()
                return False
            self._color_stream = color
            self._depth_stream = depth

        # Prove it actually delivers before claiming success: a sensor that
        # opens and then never yields a frame is worse than one that fails.
        deadline = time.time() + 4.0
        while time.time() < deadline:
            frame = self.grab()
            if frame.color is not None and frame.depth is not None:
                print(f"[kinect] Kinect v1 streaming {COLOR_W}x{COLOR_H} "
                      f"color + registered depth", flush=True)
                return True
            time.sleep(0.05)
        print("[kinect] sensor opened but delivered no frames", flush=True)
        self._teardown()
        return False

    def _teardown(self) -> None:
        k, self._k = self._k, None
        self._color_stream = None
        self._depth_stream = None
        self._depth_shift = None
        self._color = None
        self._depth = None
        if k is not None and self._initialized:
            try:
                k.NuiShutdown()
            except Exception:
                pass
        self._initialized = False

    def close(self) -> None:
        with _nui_lock:
            self._teardown()
        self._last = None

    def is_open(self) -> bool:
        return self._initialized and self._color_stream is not None

    def detach_handle(self) -> None:
        """Release the sensor so the game loop's thread can claim it.

        NuiInitialize sets up per-thread COM state, and the sensor is opened on
        a worker thread so the boot menu stays responsive.
        """
        with _nui_lock:
            self._teardown()
        self._last = None

    def reopen_on_this_thread(self, full: bool = False) -> bool:
        return self.open()

    # -- capture ------------------------------------------------------------ #
    @property
    def has_depth(self) -> bool:
        return True

    def capture_status(self) -> dict:
        return {
            "measured_fps": float(self._measured_fps),
            "locked": True,          # the sensor runs its own fixed exposure
            "exposure": None,
            "ignored": [],
            "exposure_control": False,
            "show_driver_settings": False,
        }

    def set_tilt(self, degrees: float) -> bool:
        """Aim the sensor head. Harmless to fail; the mount usually suffices."""
        k = self._k
        if k is None:
            return False
        try:
            return k.NuiCameraElevationSetAngle(
                C.c_long(int(max(-27, min(27, degrees))))) == 0
        except OSError:
            return False

    def _one(self, stream) -> bytes | None:
        """One frame's bytes off a stream, or None when none is ready."""
        k = self._k
        if k is None or stream is None:
            return None
        pf = C.POINTER(_IMAGE_FRAME)()
        try:
            hr = k.NuiImageStreamGetNextFrame(stream, 0, C.byref(pf))
        except OSError:
            return None
        if hr != 0 or not pf:
            return None
        try:
            texture = pf.contents.pFrameTexture
            return _texture_bytes(texture) if texture else None
        finally:
            try:
                k.NuiImageStreamReleaseFrame(stream, pf)
            except OSError:
                pass

    def _newest(self, stream, limit: int = 4) -> bytes | None:
        """The freshest frame on a stream, discarding any backlog.

        The runtime queues frames, and a putt is fast enough that tracking a
        queued one would draw the ball where it used to be. None means nothing
        arrived since the last call, which is not an error.

        Bounded because the sensor is still producing while we drain: if frames
        arrive at least as fast as they can be copied out — 1.2 MB each — an
        unbounded loop never reaches the end of the queue, and the game freezes
        with no error anywhere. Four is well past the two or three a slow tick
        can leave behind.
        """
        buf = None
        for _ in range(max(1, limit)):
            got = self._one(stream)
            if got is None:
                break
            buf = got
        return buf

    def grab(self) -> Frame:
        """Latest color plus registered depth.

        Both streams run at 30 Hz but tick independently and almost never have
        a frame ready in the same pass — measured on this sensor as 2
        coincidences against 116 single-stream hits. So each side is banked as
        it arrives: requiring both within one call cut the delivered rate to
        6 fps even though the sensor was producing a full 30.
        """
        if not self.is_open():
            return self._stale()

        color_buf = self._newest(self._color_stream)
        depth_buf = self._newest(self._depth_stream)
        if color_buf is None and depth_buf is None:
            return self._stale()

        need_c = COLOR_W * COLOR_H * 4
        if color_buf is not None and len(color_buf) >= need_c:
            bgra = np.frombuffer(color_buf[:need_c], np.uint8).reshape(
                COLOR_H, COLOR_W, 4)
            # NUI color is BGRA, so this only drops the alpha. cv2 does it in
            # one SIMD pass; slicing the channel out leaves a strided view that
            # costs more to make contiguous than the conversion does.
            self._color = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)

        need_d = DEPTH_W * DEPTH_H * 2
        if depth_buf is not None and len(depth_buf) >= need_d:
            raw = np.frombuffer(depth_buf[:need_d], np.uint16).reshape(
                DEPTH_H, DEPTH_W)
            if self._depth_shift is None:
                self._depth_shift = depth_shift_for(raw)
            depth_mm = (raw >> self._depth_shift) if self._depth_shift else raw
            self._depth = self._register(depth_mm)

        # Until the slower stream has produced its first frame there is no
        # coherent pair to hand over.
        if self._color is None or self._depth is None:
            return self._stale()

        now = time.time()
        self._fps_n += 1
        if now - self._fps_at >= 1.0:
            if self._fps_at:
                self._measured_fps = self._fps_n / (now - self._fps_at)
            self._fps_at, self._fps_n = now, 0

        frame = Frame(color=self._color, depth=self._depth, t=now,
                      source="kinect-nui")
        self._last = frame
        return frame

    def _stale(self) -> Frame:
        if self._last is not None:
            return self._last
        return Frame(color=None, depth=None, t=0.0, source="kinect-nui")
