"""Mock sensor backend — a simulated floor scene for development & tests.

Produces a *consistent* color + depth pair of a tilted floor with a few
objects on it, generated through the same pinhole model the vision code
inverts. This lets the full pipeline (floor fit -> blob/ball/obstacle/cup
detection -> tracking -> game rules) run end to end with no hardware.

The scene (obstacles, cup, balls) is driven by the engine via ``set_scene``.
Ball movement is done by the engine updating ball positions between frames.
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np

from ..models import CameraModel, FloorPlane, Frame, SensorDescription
from ..vision.geometry import FloorMapper
from .base import SensorBackend


def hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


class MockBackend(SensorBackend):
    # Scene geometry: a top-down camera over the centre of the play area.
    # The floor frame convention (origin = point directly below the camera, the
    # play area centred on that origin) is only self-consistent when the camera
    # looks straight down — a forward-pitched tripod camera would put the nadir
    # off-frame and push the far side of the play area behind the sensor.
    PITCH_DEG = 90.0
    HEIGHT_M = 2.0

    CUP_GREEN = hex_to_bgr("#2f9e5f")   # cup body
    CUP_WHITE = (245, 245, 240)          # white rim ring (what detection finds)

    def __init__(self) -> None:
        self.description = SensorDescription(
            model="Mock sensor",
            color_res=(640, 480),
            depth_res=(640, 480),
            fov_h_deg=85.0,
            reliable_min_m=1.0,
            reliable_max_m=4.0,
            note="Simulated top-down floor view (no camera attached).",
            fps=30,
        )
        self.cam: CameraModel = CameraModel(fx=350.0, fy=350.0, cx=320.0, cy=240.0, color_scale=1.0)

        theta = math.radians(self.PITCH_DEG)
        self._n = np.array([0.0, math.cos(theta), -math.sin(theta)])
        self._d = -self.HEIGHT_M
        self.plane = FloorPlane(self._n.copy(), self._d)
        self.mapper = FloorMapper(self.plane, self.cam)

        self.obstacles: list[dict] = []   # {x, y, w, h, height_m, color}
        self.cup: dict | None = None       # {x, y, r}
        self.balls: list[dict] = []        # {x, y, r, color, height_m}

        self._floor_depth: np.ndarray | None = None
        self._floor_color: np.ndarray | None = None
        self._opened = False

    # ------------------------------------------------------------------ #
    # Scene API (engine -> mock)
    # ------------------------------------------------------------------ #
    def set_scene(
        self,
        obstacles: list[dict] | None = None,
        cup: dict | None = None,
        balls: list[dict] | None = None,
    ) -> None:
        if obstacles is not None:
            self.obstacles = obstacles
        self.cup = cup
        if balls is not None:
            self.balls = balls

    # ------------------------------------------------------------------ #
    def open(self) -> bool:
        self._opened = True
        return True

    def close(self) -> None:
        self._opened = False

    def is_open(self) -> bool:
        return self._opened

    def lock_capture(self, exposure: float | None = None) -> None:
        return

    def unlock_capture(self) -> None:
        return

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _build_floor_depth(self) -> np.ndarray:
        w, h = self.cam.cx * 2, self.cam.cy * 2
        u = np.arange(int(w), dtype=np.float32)
        v = np.arange(int(h), dtype=np.float32)
        uu, vv = np.meshgrid(u, v)
        rx = (uu - self.cam.cx) / self.cam.fx
        ry = (vv - self.cam.cy) / self.cam.fy
        denom = self._n[0] * rx + self._n[1] * ry + self._n[2]
        depth = np.where(denom < -1e-4, self._d / denom, 0.0)  # meters
        return (depth * 1000.0).astype(np.float32)

    def _build_floor_color(self) -> np.ndarray:
        w, h = int(self.cam.cx * 2), int(self.cam.cy * 2)
        depth = self._floor_depth
        # Distance-driven shading: nearer floor is slightly lighter.
        t = np.clip((depth - 800.0) / 3200.0, 0.0, 1.0)  # 0 near .. 1 far
        base = 96.0 - 30.0 * t
        rng = np.random.default_rng(7)
        tex = rng.normal(0.0, 4.0, (h, w)).astype(np.float32)
        col = np.dstack([base, base, base]) + tex[..., None]
        # A faint warm cast + subtle horizontal planks.
        col[..., 2] += 4.0
        col = np.clip(col, 0, 255).astype(np.uint8)
        for y in range(20, h, 40):
            cv2.line(col, (0, y), (w, y), (0, 0, 0), 1)
        return col

    def _draw_polygon_footprint(self, color: np.ndarray, depth: np.ndarray, pts_floor, height_m, fill_bgr):
        """Rasterize a floor-space polygon (list of (x,y)) into color+depth."""
        pts_px = np.array([self.mapper.floor_to_pixel(x, y) for x, y in pts_floor], dtype=np.int32)
        cv2.fillPoly(color, [pts_px], fill_bgr)
        cv2.polylines(color, [pts_px], True, (255, 255, 255), 1)
        # Mark "above floor": nearer depth across the footprint.
        arr = np.asarray(pts_floor, dtype=float)
        cx, cy = float(arr[:, 0].mean()), float(arr[:, 1].mean())
        _, zc = self._floor_at(cx, cy)
        mask = np.zeros(depth.shape, dtype=np.uint8)
        cv2.fillPoly(mask, [pts_px], 255)
        depth[mask > 0] = np.minimum(depth[mask > 0], zc - height_m * 1000.0)

    def _floor_at(self, x: float, y: float) -> tuple[tuple[float, float], float]:
        u, v = self.mapper.floor_to_pixel(x, y)
        zc = self._floor_depth[int(np.clip(v, 0, self._floor_depth.shape[0] - 1)),
                               int(np.clip(u, 0, self._floor_depth.shape[1] - 1))]
        return (u, v), float(zc)

    def _draw_circle_footprint(self, color, depth, cx, cy, r, height_m, fill_bgr):
        (u, v), zc = self._floor_at(cx, cy)
        pr = max(1.5, r * self.cam.fx / (zc / 1000.0))
        cu, cv = int(round(u)), int(round(v))
        if not (0 <= cu < color.shape[1] and 0 <= cv < color.shape[0]):
            return
        cv2.circle(color, (cu, cv), max(1, int(pr)), fill_bgr, -1)
        mask = np.zeros(depth.shape, dtype=np.uint8)
        cv2.circle(mask, (cu, cv), max(1, int(pr)), 255, -1)
        depth[mask > 0] = np.minimum(depth[mask > 0], zc - height_m * 1000.0)

    def grab(self) -> Frame:
        if self._floor_depth is None:
            self._floor_depth = self._build_floor_depth()
            self._floor_color = self._build_floor_color()

        color = self._floor_color.copy()
        depth = self._floor_depth.copy()

        for o in self.obstacles:
            x, y, w, h = o["x"], o["y"], o["w"], o["h"]
            pts = [(x - w / 2, y - h / 2), (x + w / 2, y - h / 2),
                   (x + w / 2, y + h / 2), (x - w / 2, y + h / 2)]
            self._draw_polygon_footprint(color, depth, pts, o.get("height_m", 0.05), o.get("color", (120, 120, 120)))

        if self.cup is not None:
            c = self.cup
            (u, v), zc = self._floor_at(c["x"], c["y"])
            pr = max(1.5, c["r"] * self.cam.fx / (zc / 1000.0))
            cu, cv = int(round(u)), int(round(v))
            if 0 <= cu < color.shape[1] and 0 <= cv < color.shape[0]:
                cv2.circle(color, (cu, cv), max(1, int(pr)), self.CUP_GREEN, -1)
                cv2.circle(color, (cu, cv), max(1, int(pr)), self.CUP_WHITE, 3)
                cv2.circle(color, (cu, cv), max(1, int(pr * 0.6)), (20, 40, 30), -1)
                mask = np.zeros(depth.shape, dtype=np.uint8)
                cv2.circle(mask, (cu, cv), max(1, int(pr)), 255, -1)
                depth[mask > 0] = np.minimum(depth[mask > 0], zc - 26.0)

        for b in self.balls:
            self._draw_circle_footprint(color, depth, b["x"], b["y"], b.get("r", 0.025),
                                        b.get("height_m", 0.05), b.get("color", (0, 0, 255)))

        return Frame(color=color, depth=depth, t=time.time(), source="mock")
