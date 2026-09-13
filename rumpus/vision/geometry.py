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

    def pixel_to_floor(self, u: float, v: float) -> tuple[float, float] | None:
        """Depth-free alias used by the color-only pipeline."""
        return self.pixel_ray_to_floor(u, v)

    def radius_to_pixels(self, x: float, y: float, r: float) -> float:
        """Physical floor radius (meters) -> pixel radius at floor point (x,y)."""
        p = self.origin + x * self.e_x + y * self.e_y
        z_mm = float(np.linalg.norm(p)) * 1000.0
        if z_mm < 1.0:
            z_mm = 1000.0
        return r * self.cam.fx / (z_mm / 1000.0)


def default_camera(resolution: tuple[int, int] = (640, 480)) -> CameraModel:
    """Reasonable pinhole for a Kinect-v1-sized frame (also used by the mock)."""
    w, h = resolution
    f = 525.0 * w / 640.0
    return CameraModel(fx=f, fy=f, cx=w / 2.0, cy=h / 2.0, color_scale=1.0)


class HomographyMapper:
    """Pixel <-> floor mapping for a *color-only* camera, via a planar homography.

    A regular webcam has no depth, so there is no plane to fit. Instead the user
    clicks the four corners of a known-size rectangle on the floor; we solve for
    the 3x3 homography ``H`` that maps floor meters ``(x, y)`` to pixels
    ``(u, v)`` (and back). This presents the same floor-facing methods as
    ``FloorMapper`` so the game and vision code never branch on the source.

    Floor frame: origin at the rectangle centre, ``+x`` to image right, ``+y``
    to image bottom (toward the near edge of the rectangle).
    """

    def __init__(self, H: np.ndarray):
        self.H = np.asarray(H, dtype=float).reshape(3, 3)
        self.Hinv = np.linalg.inv(self.H)
        # The color-only pipeline never consults a plane; expose a sentinel.
        self.plane = None

    def floor_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        p = self.H @ np.array([x, y, 1.0])
        w = p[2]
        if abs(w) < 1e-9:
            return (0.0, 0.0)
        return (float(p[0] / w), float(p[1] / w))

    def pixel_to_floor(self, u: float, v: float) -> tuple[float, float] | None:
        p = self.Hinv @ np.array([u, v, 1.0])
        w = p[2]
        if abs(w) < 1e-9:
            return None
        return (float(p[0] / w), float(p[1] / w))

    def pixel_ray_to_floor(self, u: float, v: float) -> tuple[float, float] | None:
        return self.pixel_to_floor(u, v)

    def depth_pixel_to_floor(self, u: float, v: float, z_mm: float) -> tuple[float, float] | None:
        # Depth is ignored for a homography.
        return self.pixel_to_floor(u, v)

    def radius_to_pixels(self, x: float, y: float, r: float) -> float:
        u0, v0 = self.floor_to_pixel(x, y)
        u1, v1 = self.floor_to_pixel(x + r, y)
        return float(np.hypot(u1 - u0, v1 - v0))


def _order_corners(pixels: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort four clicked corners into (top-left, top-right, bottom-right,
    bottom-left) using image row/col. Works for any non-pathological quad."""
    pts = sorted(pixels, key=lambda p: (p[1], p[0]))
    top = sorted(pts[:2], key=lambda p: p[0])      # top-left, top-right
    bot = sorted(pts[2:], key=lambda p: p[0])      # bottom-left, bottom-right
    return [top[0], top[1], bot[1], bot[0]]


def homography_from_corners(
    pixels: list[tuple[float, float]],
    width_m: float,
    height_m: float,
) -> np.ndarray | None:
    """Solve the pixel<->floor homography from four clicked pixel corners.

    ``pixels`` are image coordinates (any order); ``width_m``/``height_m`` are
    the physical size of the rectangle they trace. Image top-left maps to floor
    ``(-w/2, -h/2)`` and image bottom-right to ``(+w/2, +h/2)``.
    """
    import cv2
    if len(pixels) != 4:
        return None
    ordered = _order_corners(list(pixels))
    w, h = width_m / 2.0, height_m / 2.0
    floor = [(-w, -h), (w, -h), (w, h), (-w, h)]
    src = np.array(ordered, dtype=np.float32).reshape(4, 2)
    dst = np.array(floor, dtype=np.float32).reshape(4, 2)
    H, _status = cv2.findHomography(dst, src, cv2.RANSAC)
    if H is None:
        return None
    return H.astype(float)
