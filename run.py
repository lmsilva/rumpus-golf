"""Rumpus Golf — launch the local server.

Usage:
    python run.py                     # auto-detect: Kinect, then webcam, then mock
    python run.py --mock              # force the simulated sensor
    python run.py --sensor v1         # force Kinect v1 (libfreenect)
    python run.py --sensor v2         # force Kinect v2 (pylibfreenect2)
    python run.py --camera 0          # force 2D webcam 0 (color-only)
    python run.py --camera 0 --resolution 1280x720
    python run.py --host 0.0.0.0 --port 8000   # LAN: prints a one-time ?t= token URL

Then open http://127.0.0.1:8000 in a browser (fullscreen on a TV).
LAN binds require the printed token URL so the camera feed is not open to the network.
"""
from __future__ import annotations

import argparse

from rumpus.server import run


def main() -> None:
    p = argparse.ArgumentParser(description="Rumpus Golf local server")
    p.add_argument("--mock", action="store_true", help="force the simulated sensor")
    p.add_argument("--sensor", choices=["v1", "v2", "webcam", "mock"], default=None,
                   help="force a specific sensor backend")
    p.add_argument("--camera", type=int, default=None,
                   help="use 2D webcam N (color-only); implies --sensor webcam")
    p.add_argument("--resolution", default=None, help="webcam resolution WxH (e.g. 1280x720)")
    p.add_argument("--no-mock", action="store_true",
                   help="do not fall back to the mock when no hardware is found")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    force = args.sensor or ("mock" if args.mock else None)
    backend_mode = None
    if args.camera is not None:
        force = "webcam"
    run(host=args.host, port=args.port, force_sensor=force,
        allow_mock=not args.no_mock, camera_index=args.camera,
        camera_res=args.resolution, backend_mode=backend_mode)


if __name__ == "__main__":
    main()
