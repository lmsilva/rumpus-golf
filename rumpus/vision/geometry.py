"""Coordinate mapping: depth pixels <-> 3D <-> floor coordinates (meters).

All game logic lives in *floor* coordinates. This module owns the only code that
touches pixel/3D conversions. It is sensor-agnostic: it consumes a fitted floor
plane (normal + offset in depth-camera space) plus pinhole intrinsics, and
exposes a small ``FloorMapper`` with two directions:

- ``depth_pixel_to_floor(u, v, z_mm) -> (x, y)``  (depth pixel + distance -> floor meters)
- ``floor_to_pixel(x, y) -> (u, v)``               (floor meters -> color pixel, for overlays)

Floor frame convention (see requirements): ``x`` is across the frame (right),
``y`` grows *toward* the camera. The origin is the point directly below the
camera (the camera position projected onto the floor plane).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models import CameraModel, FloorPlane


@dataclass
class FloorMapper:
    """Bi-directional depth<->floor mapping for one fitted floor plane."""

    cam: CameraModel
    n: np.ndarray      # unit normal, oriented "up" (toward the camera)
    d: float           # plane offset (n . p = d), d <= 0
    origin: np.ndarray  # camera position projected onto the floor (3D)
    e_x: np.ndarray    # unit basis: across the frame (right)
    e_y: np.ndarray    # unit basis: toward the camera

    def __init__(self, plane: FloorPlane, cam: CameraModel):
        self.cam = cam
        n = np.asarray(plane.normal, dtype=float)
        n = n / (np.linalg.norm(n) + 1e-9)
        d = float(plane.offset)
        # Orient "up" toward the camera (camera sits at the origin on the +n side).
        if d > 0:
            n = -n
            d = -d
        self.n = n
        self.d = d

        # e_x: camera X axis projected onto the plane.
        cx = np.array([1.0, 0.0, 0.0])
        ex = cx - n * float(np.dot(n, cx))
        if np.linalg.norm(ex) < 1e-6:
            ex = np.array([1.0, 0.0, 0.0])
        ex = ex / (np.linalg.norm(ex) + 1e-9)
        # e_y: toward the camera, completing a right-handed frame with n (up).
        ey = np.cross(n, ex)
        ey = ey / (np.linalg.norm(ey) + 1e-9)

        self.e_x = ex
        self.e_y = ey
        # Origin = camera (the depth-space origin) projected onto the plane:
        #   proj(0) = 0 - (n.0 - d) n = d n.
        self.origin = d * n

    # -- helpers ------------------------------------------------------------ #
    def depth_pixel_to_3d(self, u: float, v: float, z_mm: float) -> np.ndarray:
        z = z_mm / 1000.0
        x = (u - self.cam.cx) * z / self.cam.fx
        y = (v - self.cam.cy) * z / self.cam.fy
        return np.array([x, y, z], dtype=float)

    def project_to_floor(self, p3d: np.ndarray) -> tuple[float, float]:
        p = np.asarray(p3d, dtype=float)
        k = float(np.dot(self.n, p) - self.d)
        q = p - k * self.n            # drop a perpendicular onto the plane
        rel = q - self.origin
        return float(np.dot(rel, self.e_x)), float(np.dot(rel, self.e_y))

    def height_above_floor(self, p3d: np.ndarray) -> float:
        return float(np.dot(self.n, np.asarray(p3d, dtype=float)) - self.d)

    def depth_pixel_to_floor(self, u: float, v: float, z_mm: float) -> tuple[float, float]:
        return self.project_to_floor(self.depth_pixel_to_3d(u, v, z_mm))

    def floor_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        p = self.origin + x * self.e_x + y * self.e_y
        if p[2] < 1e-3:
            p = p.copy()
            p[2] = 1e-3
        u = self.cam.fx * p[0] / p[2] + self.cam.cx
        v = self.cam.fy * p[1] / p[2] + self.cam.cy
        return float(u), float(v)

    def pixel_ray_to_floor(self, u: float, v: float) -> tuple[float, float] | None:
        """Intersect the pixel's viewing ray with the floor plane (depth-free).

        Useful for aim clicks and drawing when the exact depth is unknown.
        Returns floor (x, y) or None if the ray runs parallel to the floor.
        """
        rx = (u - self.cam.cx) / self.cam.fx
        ry = (v - self.cam.cy) / self.cam.fy
        denom = float(np.dot(self.n, (rx, ry, 1.0)))
        if abs(denom) < 1e-6:
            return None
        t = self.d / denom
        if t <= 0:
            return None
        p = np.array([rx * t, ry * t, t])
        return self.project_to_floor(p)


def default_camera(resolution: tuple[int, int] = (640, 480)) -> CameraModel:
    """Reasonable pinhole for a Kinect-v1-sized frame (also used by the mock)."""
    w, h = resolution
    f = 525.0 * w / 640.0
    return CameraModel(fx=f, fy=f, cx=w / 2.0, cy=h / 2.0, color_scale=1.0)
