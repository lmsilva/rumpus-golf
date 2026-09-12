"""Shared data models.

These dataclasses are the lingua franca between the sensor layer, the vision
pipeline, the game rules and the websocket state publisher. Nothing here may
import a driver or a UI. Floor coordinates are always meters on the floor plane
(a right-handed frame where ``+x`` is across the frame and ``+y`` is toward the
camera), never pixels.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

import numpy as np


# --------------------------------------------------------------------------- #
# Design tokens (mirrors TOKENS.md). Kept here so both the game and any
# non-web front-end share one source of truth for the important colors.
# --------------------------------------------------------------------------- #
class Palette:
    ground = "#15171c"
    panel = "#1e2128"
    panel_2 = "#272b34"
    text = "#f2efe8"
    text_muted = "#a3a8b4"
    text_soft = "#d8d5cd"
    mint = "#8be9c3"
    mint_deep = "#0f7a5a"
    coral = "#ff6b57"
    orange = "#ff8a3d"
    pink = "#ff5fa8"
    blue = "#5b8cff"
    yellow = "#ffd84d"

    PLAYER_COLORS = [orange, pink, blue, yellow]
    PLAYER_TEXT = "#15171c"  # text on a saturated player fill

    # Hue names shown next to each sampled ball ("orange", "pink", …).
    HUE_NAMES = {
        orange: "orange",
        pink: "pink",
        blue: "blue",
        yellow: "yellow",
    }


# --------------------------------------------------------------------------- #
# Sensor
# --------------------------------------------------------------------------- #
@dataclass
class SensorDescription:
    model: str                      # "Kinect v1" | "Kinect v2" | "Mock sensor"
    color_res: tuple[int, int]
    depth_res: tuple[int, int]
    fov_h_deg: float
    reliable_min_m: float
    reliable_max_m: float
    note: str = ""
    fps: int = 30

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Frame:
    """One synchronized sensor sample. ``color`` is BGR (HxWx3 uint8), ``depth``
    is millimeters (HxW float32). ``depth`` may be None for color-only sources."""
    color: Optional[np.ndarray] = None
    depth: Optional[np.ndarray] = None
    t: float = 0.0
    source: str = ""


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
@dataclass
class FloorPlane:
    """Floor plane in depth-camera space: ``normal`` (unit) and ``offset`` such
    that a point ``p`` on the plane satisfies ``dot(normal, p) = offset``."""
    normal: np.ndarray = field(default_factory=lambda: np.array([0.0, -1.0, 0.0]))
    offset: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"normal": self.normal.tolist(), "offset": float(self.offset)}


@dataclass
class CameraModel:
    """Pinhole intrinsics used to go depth-pixel -> 3D and 3D -> color-pixel.

    ``fx, fy, cx, cy`` are in *depth* pixel units. ``color_scale`` maps a depth
    pixel to the color frame (registered depth is assumed; for the Kinect v1
    backend we register depth onto color so scale is 1)."""
    fx: float
    fy: float
    cx: float
    cy: float
    color_scale: float = 1.0


# --------------------------------------------------------------------------- #
# Zones & obstacles (floor coordinates, meters)
# --------------------------------------------------------------------------- #
@dataclass
class CircleZone:
    x: float
    y: float
    r: float


@dataclass
class Obstacle:
    id: str
    label: str
    kind: str                       # book | soft | tube | hazard | ramp
    polygon: list[tuple[float, float]]   # floor meters, closed ring
    item: str = ""
    real_size_cm: list[float] = field(default_factory=list)
    state: str = "proposed"         # proposed | selected | confirmed | deleted | drawing
    confidence: float = 1.0
    penalty: int = 0                # hazard (towel): +1 when stopped on
    bonus: int = 0                  # tube (tunnel): -1 when through

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "polygon": [[round(p[0], 3), round(p[1], 3)] for p in self.polygon],
            "item": self.item,
            "real_size_cm": self.real_size_cm,
            "state": self.state,
            "confidence": round(self.confidence, 2),
            "penalty": self.penalty,
            "bonus": self.bonus,
        }


# --------------------------------------------------------------------------- #
# Players & balls
# --------------------------------------------------------------------------- #
@dataclass
class Player:
    id: str
    name: str
    color: str                      # design token color (never green)
    hue_name: str = ""
    hue_range: Optional[tuple[int, int]] = None  # (lo, hi) in H [0..179]
    order: int = 0

    def as_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id, "name": self.name, "color": self.color,
            "hue_name": self.hue_name, "order": self.order,
        }
        if self.hue_range is not None:
            d["hue_range"] = list(self.hue_range)
        return d


@dataclass
class BallState:
    id: str
    player_id: str
    color: str
    position: Optional[tuple[float, float]] = None   # floor meters
    last_seen_t: float = 0.0
    moving: bool = False
    hidden: bool = False
    hidden_estimate: Optional[tuple[float, float]] = None
    lost: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "player_id": self.player_id, "color": self.color,
            "position": [round(self.position[0], 3), round(self.position[1], 3)] if self.position else None,
            "moving": self.moving, "hidden": self.hidden,
            "hidden_estimate": [round(self.hidden_estimate[0], 3), round(self.hidden_estimate[1], 3)] if self.hidden_estimate else None,
            "lost": self.lost,
        }


# --------------------------------------------------------------------------- #
# Events (append-only log — the undo stack)
# --------------------------------------------------------------------------- #
class EventType(str, Enum):
    STROKE = "STROKE"
    OOB = "OOB"
    HOLE_OUT = "HOLE_OUT"
    CAP = "CAP"
    MANUAL_ADJUST = "MANUAL_ADJUST"
    TURN = "TURN"
    TUNNEL_BONUS = "TUNNEL_BONUS"
    HAZARD = "HAZARD"


@dataclass
class Event:
    type: EventType
    player_id: str
    hole: int
    t: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value, "player_id": self.player_id,
            "hole": self.hole, "t": self.t, "data": self.data,
        }
