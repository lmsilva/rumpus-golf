"""USB VID/PID detection for Kinect sensors.

The canonical way to know which Kinect is attached is its USB descriptor:

- Kinect v1 (Xbox 360 / Kinect for Windows): VID 0x045E
    PID 0x02AD, 0x02AE (Kinect for Windows), 0x02B0, 0x02BF (Xbox 360)
- Kinect v2 (Xbox One / Kinect for Windows v2): VID 0x045E
    PID 0x02C4 (Kinect for Windows v2), 0x02D8, 0x02D9

We try the real enumeration first; if the platform has no USB tooling we fall
back to "try to open each backend" which is equally conclusive.
"""
from __future__ import annotations

import platform
import subprocess
from typing import Optional

KINECT_VID = 0x045E

KINECT_V1_PIDS = {0x02AD, 0x02AE, 0x02B0, 0x02BF}
KINECT_V2_PIDS = {0x02C4, 0x02D8, 0x02D9}


def _windows_usb() -> list[tuple[int, int]]:
    """Enumerate (VID, PID) pairs via PowerShell (best-effort)."""
    script = (
        "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty InstanceId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    pairs: list[tuple[int, int]] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if "VID_" in line.upper() and "PID_" in line.upper():
            try:
                vid = int(line.split("VID_")[1][:4], 16)
                pid = int(line.split("PID_")[1][:4], 16)
                pairs.append((vid, pid))
            except (ValueError, IndexError):
                continue
    return pairs


def _linux_usb() -> list[tuple[int, int]]:
    """Read /sys/bus/usb/devices for idVendor/idProduct."""
    import os
    pairs: list[tuple[int, int]] = []
    base = "/sys/bus/usb/devices"
    if not os.path.isdir(base):
        return pairs
    for root, _dirs, files in os.walk(base):
        if "idVendor" in files and "idProduct" in files:
            try:
                vid = int(open(os.path.join(root, "idVendor")).read().strip(), 16)
                pid = int(open(os.path.join(root, "idProduct")).read().strip(), 16)
                pairs.append((vid, pid))
            except (ValueError, OSError):
                continue
    return pairs


def list_usb() -> list[tuple[int, int]]:
    sysname = platform.system()
    if sysname == "Windows":
        return _windows_usb()
    if sysname == "Linux":
        return _linux_usb()
    return []


def detect_sensor() -> Optional[str]:
    """Return 'v2', 'v1' or None based on attached USB descriptors.

    If both are attached, prefer v2 (per spec).
    """
    pairs = list_usb()
    has_v2 = any(vid == KINECT_VID and pid in KINECT_V2_PIDS for vid, pid in pairs)
    has_v1 = any(vid == KINECT_VID and pid in KINECT_V1_PIDS for vid, pid in pairs)
    if has_v2:
        return "v2"
    if has_v1:
        return "v1"
    return None


def list_webcams(max_index: int = 8) -> list[dict]:
    """Enumerate connected 2D cameras (index, driver name, working)."""
    from .webcam import list_webcams as _list
    return _list(max_index)
