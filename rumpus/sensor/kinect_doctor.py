"""Explain, in plain terms, why a Kinect is or is not usable on this machine.

Run it with::

    python -m rumpus.sensor.kinect_doctor

A Kinect fails in several unrelated ways that all look identical from the game
("no depth camera"), so the point here is to say *which* one: no device on the
bus, a device with no driver, a powered-down sensor whose camera interface never
enumerates, or a working sensor with no Python binding to read it.
"""
from __future__ import annotations

import platform
import subprocess

from . import detect

# The v1 sensor is three USB devices behind one cable. Which ones appear tells
# us a lot: on bus power alone only the motor shows up, and the camera - the
# one the game actually needs - stays invisible until the 12 V adapter is in.
V1_INTERFACES = {
    0x02B0: ("motor", "Xbox NUI Motor"),
    0x02AE: ("camera", "Xbox NUI Camera"),
    0x02AD: ("audio", "Xbox NUI Audio"),
    0x02BF: ("audio", "Xbox NUI Audio (Kinect for Windows)"),
}
V2_INTERFACES = {
    0x02C4: ("camera", "Kinect for Windows v2 sensor"),
    0x02D8: ("camera", "Kinect v2 sensor"),
    0x02D9: ("camera", "Kinect v2 sensor"),
}

# Windows CM_PROB_* codes we actually see on Kinects, in words.
PROBLEM_CODES = {
    0: "working",
    1: "not configured correctly",
    10: "cannot start",
    18: "drivers need reinstalling",
    19: "registry entry is damaged",
    22: "disabled",
    28: "no driver installed",
    31: "Windows could not load the driver",
    43: "the device reported a failure",
    45: "not currently connected",
}


# Get-PnpDevice only reports Status, so the numeric problem code comes from a
# second lookup. Building one string per device keeps the parse trivial.
PNP_SCRIPT = (
    "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
    "Where-Object { $_.InstanceId -like '*VID_*' } | "
    "ForEach-Object { "
    "$p = ($_ | Get-PnpDeviceProperty -KeyName DEVPKEY_Device_ProblemCode "
    "-ErrorAction SilentlyContinue).Data; "
    "Write-Output ($_.InstanceId + '~' + [string]$_.Status + '~' + "
    "[string]$_.FriendlyName + '~' + [string]$p) }"
)


def _windows_devices() -> list[dict]:
    """Every present USB device, with status and problem code."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", PNP_SCRIPT],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: list[dict] = []
    for line in out.stdout.splitlines():
        parts = line.strip().split("~")
        if len(parts) < 4 or "VID_" not in parts[0].upper():
            continue
        instance, status, name, problem = parts[0], parts[1], parts[2], parts[3]
        try:
            vid = int(instance.upper().split("VID_")[1][:4], 16)
            pid = int(instance.upper().split("PID_")[1][:4], 16)
        except (ValueError, IndexError):
            continue
        try:
            code = int(problem)
        except ValueError:
            code = None
        rows.append({"vid": vid, "pid": pid, "status": status,
                     "name": name, "problem": code})
    return rows


def _binding(kind: str) -> tuple[str, bool, str]:
    """Is the Python library that reads this Kinect importable?"""
    module = "freenect" if kind == "v1" else "pylibfreenect2"
    try:
        m = __import__(module)
    except Exception as exc:
        return module, False, f"{type(exc).__name__}: {exc}"
    return module, True, getattr(m, "__file__", "?") or "?"


def report() -> list[str]:
    """Build the diagnostic as a list of lines."""
    out: list[str] = ["Kinect check", "=" * 40]

    kind = detect.detect_sensor()
    pairs = detect.list_usb()
    ms = sorted({pid for vid, pid in pairs if vid == detect.KINECT_VID})

    if not pairs:
        out += ["Could not enumerate USB on this machine, so nothing here is",
                "conclusive. Try opening the sensor directly instead:",
                "    python run.py --prefer kinect"]
        return out

    if kind is None:
        out += ["No Kinect on the USB bus.",
                "",
                "If it is plugged in, the usual cause is power: a Kinect v1",
                "needs its 12 V adapter, and the sensor does not appear on USB",
                "without it. Check the adapter, then re-run this."]
        return out

    out += [f"Kinect {kind} detected (USB vendor 0x045E).", ""]

    table = V1_INTERFACES if kind == "v1" else V2_INTERFACES
    devices = _windows_devices() if platform.system() == "Windows" else []
    by_pid = {d["pid"]: d for d in devices}

    roles_present: set[str] = set()
    out.append("USB interfaces:")
    for pid, (role, label) in sorted(table.items()):
        if pid not in ms:
            continue
        roles_present.add(role)
        dev = by_pid.get(pid)
        note = ""
        if dev is not None:
            problem = PROBLEM_CODES.get(dev["problem"], f"code {dev['problem']}")
            note = f"  status={dev['status']} ({problem})"
        out.append(f"  found  0x{pid:04X}  {label}{note}")
    for pid, (role, label) in sorted(table.items()):
        if pid not in ms and role not in roles_present:
            out.append(f"  absent 0x{pid:04X}  {label}")

    module, ok, detail = _binding(kind)
    out += ["", "Python binding:",
            f"  {module}: {'installed (' + detail + ')' if ok else 'MISSING'}"]
    if not ok:
        out.append(f"  {detail}")

    # The verdict. Ordered by what blocks first, because fixing a later step
    # while an earlier one is broken teaches you nothing.
    out += ["", "Verdict", "-" * 40]
    if "camera" not in roles_present:
        out += ["The camera interface is not on the bus, so no software can read",
                "this sensor yet. Two things do that:",
                "",
                "  1. The 12 V power adapter is not connected. On USB power alone",
                "     a Kinect v1 shows only its motor, never its camera.",
                "  2. No driver is bound to the camera interface.",
                "",
                "Start with the adapter, then re-run this check."]
    elif any(d["problem"] not in (0, None) for d in devices
             if d["pid"] in table and table[d["pid"]][0] == "camera"):
        out += ["The camera interface is on the bus but Windows has no working",
                "driver for it. libfreenect needs libusbK bound to it (Zadig).",
                "Fix that, then re-run this check."]
    elif not ok:
        out += ["The sensor looks healthy, but the game has no way to read it:",
                f"the {module} Python binding is not installed. There is no pip",
                "wheel for it on Windows; it has to be built against",
                "libfreenect. Until then Rumpus will use your 2D camera."]
    else:
        out += ["Everything needed is present. Start the game with:",
                "    python run.py --prefer kinect"]
    return out


def main() -> None:
    print("\n".join(report()))


if __name__ == "__main__":
    main()
