"""Enumerate capture devices with the OS / driver friendly name.

OpenCV's ``VideoCapture(index)`` only knows integers. On Windows that index is
a DirectShow video-input device; we walk that category (same order as
``CAP_DSHOW``) and resolve each moniker to the driver name the user sees in
Device Manager — e.g. ``HD Pro Webcam C920``, ``Iriun Webcam``,
``OBS Virtual Camera``. Linux uses V4L2 card names.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess


_NAME_CACHE: list[str] | None = None


def list_capture_names(refresh: bool = False) -> list[str]:
    """Return capture-device names in the order OpenCV indexes them."""
    global _NAME_CACHE
    if _NAME_CACHE is not None and not refresh:
        return _NAME_CACHE
    names: list[str] = []
    if platform.system() == "Windows":
        names = _windows_dshow_names() or _windows_pnp_camera_names()
    elif platform.system() == "Linux":
        names = _v4l2_names()
    _NAME_CACHE = names
    return names


def attach_names(probed: list[dict], names: list[str] | None = None) -> list[dict]:
    """Stamp driver names onto OpenCV-probed ``{index, name, working}`` rows."""
    if names is None:
        names = list_capture_names()
    out: list[dict] = []
    seen: set[int] = set()
    for row in probed:
        idx = int(row.get("index", 0))
        seen.add(idx)
        label = names[idx] if 0 <= idx < len(names) else row.get("name") or f"Camera {idx}"
        item = dict(row)
        item["name"] = label
        item["driver"] = label
        out.append(item)
    for i, label in enumerate(names):
        if i in seen:
            continue
        out.append({"index": i, "name": label, "driver": label, "working": False})
    out.sort(key=lambda d: int(d.get("index", 0)))
    return out


# ---- Windows: DirectShow monikers + PnP / CLSID resolution ---------------- #

def _windows_dshow_names() -> list[str]:
    paths = _dshow_display_names()
    if not paths:
        return []
    pnp = _pnp_name_map()
    return [_resolve_display_name(p, pnp, i) for i, p in enumerate(paths)]


def _dshow_display_names() -> list[str]:
    """``IMoniker.GetDisplayName`` for every video-input device, DSHOW order."""
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import POINTER, byref, c_void_p, c_ulong, HRESULT
    except Exception:
        return []

    ole32 = ctypes.WinDLL("ole32")

    class GUID(ctypes.Structure):
        _fields_ = (
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        )

    ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, POINTER(GUID)]
    ole32.CLSIDFromString.restype = HRESULT
    ole32.CoInitializeEx.argtypes = [c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoUninitialize.argtypes = []
    ole32.CoCreateInstance.argtypes = [
        POINTER(GUID), c_void_p, ctypes.c_ulong, POINTER(GUID), POINTER(c_void_p)
    ]
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CreateBindCtx.argtypes = [ctypes.c_ulong, POINTER(c_void_p)]
    ole32.CreateBindCtx.restype = HRESULT
    ole32.CoTaskMemFree.argtypes = [c_void_p]

    def guid(text: str) -> GUID:
        g = GUID()
        ole32.CLSIDFromString(text, byref(g))
        return g

    def vfunc(obj: c_void_p, index: int, restype, *argtypes):
        vtbl = ctypes.cast(ctypes.cast(obj, POINTER(c_void_p))[0], POINTER(c_void_p))
        return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])

    def release(obj: c_void_p) -> None:
        if obj:
            vfunc(obj, 2, c_ulong)(obj)

    ole32.CoInitializeEx(None, 0)
    names: list[str] = []
    dev_enum = c_void_p()
    enum_mon = c_void_p()
    bind_ctx = c_void_p()
    try:
        hr = ole32.CoCreateInstance(
            byref(guid("{62BE5D10-60EB-11d0-BD3B-00A0C911CE86}")),
            None, 1,
            byref(guid("{29840822-5B84-11D0-BD3B-00A0C911CE86}")),
            byref(dev_enum),
        )
        if hr != 0 or not dev_enum:
            return []
        hr = vfunc(dev_enum, 3, ctypes.c_long, POINTER(GUID), POINTER(c_void_p), c_ulong)(
            dev_enum, byref(guid("{860BB310-5D01-11d0-BD3B-00A0C911CE86}")),
            byref(enum_mon), 0,
        )
        if hr != 0 or not enum_mon:
            return []
        ole32.CreateBindCtx(0, byref(bind_ctx))
        nxt = vfunc(enum_mon, 3, ctypes.c_long, c_ulong, POINTER(c_void_p), POINTER(c_ulong))
        getdn = None
        while True:
            mon = c_void_p()
            fetched = c_ulong(0)
            hr = nxt(enum_mon, 1, byref(mon), byref(fetched))
            if hr != 0 or fetched.value == 0 or not mon:
                break
            try:
                getdn = vfunc(mon, 20, ctypes.c_long, c_void_p, c_void_p, POINTER(c_void_p))
                raw = c_void_p()
                hr = getdn(mon, bind_ctx, None, byref(raw))
                if hr == 0 and raw:
                    names.append(ctypes.wstring_at(raw))
                    ole32.CoTaskMemFree(raw)
            finally:
                release(mon)
    except Exception:
        names = []
    finally:
        release(bind_ctx)
        release(enum_mon)
        release(dev_enum)
        try:
            ole32.CoUninitialize()
        except Exception:
            pass
    return names


def _pnp_name_map() -> dict[str, str]:
    """InstanceId (upper) -> FriendlyName for present devices."""
    script = (
        "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "ForEach-Object { '{0}|||{1}' -f $_.InstanceId, $_.FriendlyName }"
    )
    mapping: dict[str, str] = {}
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return mapping
    for line in out.stdout.splitlines():
        if "|||" not in line:
            continue
        inst, name = line.split("|||", 1)
        inst, name = inst.strip().upper(), name.strip()
        if inst and name:
            mapping[inst] = name
    return mapping


_PNP_PREFIX = re.compile(r"^@device:pnp:\\\\\?\\", re.I)
_SW_CLSID = re.compile(r"@device:sw:\{[0-9A-Fa-f-]+\}\\(\{[0-9A-Fa-f-]+\})", re.I)


_VIDPID = re.compile(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", re.I)


def _resolve_display_name(display: str, pnp: dict[str, str], index: int) -> str:
    inst = _display_to_instance_id(display)
    if inst:
        if inst in pnp:
            return pnp[inst]
        for key, name in pnp.items():
            if inst.startswith(key) or key.startswith(inst):
                return name
    m = _SW_CLSID.search(display or "")
    if m:
        clsid_name = _clsid_name(m.group(1))
        if clsid_name:
            return clsid_name
    vp = _VIDPID.search(display or "")
    if vp:
        needle = f"VID_{vp.group(1).upper()}&PID_{vp.group(2).upper()}"
        for key, name in pnp.items():
            if needle in key and name:
                return name
    return f"Camera {index}"


def _display_to_instance_id(display: str) -> str | None:
    r"""Turn a DSHOW @device:pnp path into a PnP InstanceId."""
    if not display:
        return None
    body = _PNP_PREFIX.sub("", display)
    if body == display:
        return None
    # usb#vid_046d&pid_082d&mi_00#9&b908854&0&0000#{ks-guid}\global
    parts = body.split("#")
    if len(parts) < 2:
        return None
    bus, rest = parts[0], parts[1:]
    # bus#id#instance#{ks-interface}\extra — keep one GUID as the instance
    # (ROOT\DEVGEN\{guid}) but stop at the following kernel-streaming GUID.
    kept: list[str] = []
    for p in rest:
        token = p.split("\\", 1)[0]
        if token.startswith("{") and token.endswith("}") and len(kept) >= 2:
            break
        if token.startswith("{") and any(c.startswith("{") for c in kept):
            break
        if token:
            kept.append(token)
    if not kept:
        return None
    return "\\".join([bus] + kept).upper()


def _clsid_name(clsid: str) -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Classes\CLSID\{clsid}") as key:
                val, _typ = winreg.QueryValueEx(key, None)
                if val:
                    return str(val)
        except OSError:
            continue
    return None


def _windows_pnp_camera_names() -> list[str]:
    """PnP Camera / USB Image devices — skip printers and scanners."""
    script = (
        "Get-PnpDevice -Status OK -ErrorAction SilentlyContinue | "
        "Where-Object { "
        "  ($_.Class -eq 'Camera') -or "
        "  ($_.Class -eq 'Image' -and $_.InstanceId -like 'USB\\*') "
        "} | Select-Object -ExpandProperty FriendlyName"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    names: list[str] = []
    for line in out.stdout.splitlines():
        name = line.strip()
        if name and name not in names:
            names.append(name)
    return names


def _v4l2_names() -> list[str]:
    names: list[str] = []
    sysfs = "/sys/class/video4linux"
    if not os.path.isdir(sysfs):
        return names
    for entry in sorted(os.listdir(sysfs)):
        if not entry.startswith("video"):
            continue
        path = os.path.join(sysfs, entry, "name")
        try:
            names.append(open(path, encoding="utf-8").read().strip() or entry)
        except OSError:
            names.append(entry)
    return names
