"""Calibration & course setup persistence (rumpus-setup.json).

Holds everything a saved course needs to reload without re-calibrating: the
floor plane, camera model, play-area polygon, start & hole circles, the
confirmed obstacle footprints, and the ball/player definitions. All shapes are
in floor coordinates (meters) so a tablet/AR edition can read the same file.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from .models import CircleZone, FloorPlane, Obstacle, Player
from .paths import SETUP_PATH


@dataclass
class Setup:
    name: str = ""
    sensor_model: str = ""
    play_area: list[tuple[float, float]] = field(default_factory=list)
    start: Optional[CircleZone] = None
    hole: Optional[CircleZone] = None
    obstacles: list[Obstacle] = field(default_factory=list)
    players: list[Player] = field(default_factory=list)
    courses: list[str] = field(default_factory=list)   # course id per hole, 1-indexed
    floor_plane: Optional[FloorPlane] = None
    camera: Optional[dict[str, Any]] = None
    saved_at: float = 0.0

    def touch(self) -> None:
        self.saved_at = time.time()

    # -- serialization ------------------------------------------------------ #
    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sensor_model": self.sensor_model,
            "play_area": [[round(p[0], 3), round(p[1], 3)] for p in self.play_area],
            "start": {"x": round(self.start.x, 3), "y": round(self.start.y, 3), "r": round(self.start.r, 3)} if self.start else None,
            "hole": {"x": round(self.hole.x, 3), "y": round(self.hole.y, 3), "r": round(self.hole.r, 3)} if self.hole else None,
            "obstacles": [o.as_dict() for o in self.obstacles],
            "players": [p.as_dict() for p in self.players],
            "courses": self.courses,
            "floor_plane": self.floor_plane.as_dict() if self.floor_plane else None,
            "camera": self.camera,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Setup":
        s = cls(
            name=d.get("name", "") or "",
            sensor_model=d.get("sensor_model", ""),
            play_area=[(p[0], p[1]) for p in d.get("play_area", [])],
            courses=d.get("courses", []),
            saved_at=d.get("saved_at", 0.0),
        )
        st = d.get("start")
        s.start = CircleZone(st["x"], st["y"], st["r"]) if st else None
        ho = d.get("hole")
        s.hole = CircleZone(ho["x"], ho["y"], ho["r"]) if ho else None
        for o in d.get("obstacles", []):
            s.obstacles.append(Obstacle(
                id=o.get("id", ""), label=o.get("label", "Object"),
                kind=o.get("kind", "soft"),
                polygon=[(p[0], p[1]) for p in o.get("polygon", [])],
                item=o.get("item", ""), real_size_cm=o.get("real_size_cm", []),
                state=o.get("state", "confirmed"), confidence=o.get("confidence", 1.0),
                penalty=o.get("penalty", 0), bonus=o.get("bonus", 0),
            ))
        for p in d.get("players", []):
            s.players.append(Player(
                id=p.get("id", ""), name=p.get("name", ""), color=p.get("color", ""),
                hue_name=p.get("hue_name", ""),
                hue_range=tuple(p["hue_range"]) if p.get("hue_range") else None,
                hue_center=float(p["hue_center"]) if p.get("hue_center") is not None else None,
                sat_floor=int(p["sat_floor"]) if p.get("sat_floor") is not None else None,
                val_floor=int(p["val_floor"]) if p.get("val_floor") is not None else None,
                order=p.get("order", 0),
            ))
        fp = d.get("floor_plane")
        if fp:
            s.floor_plane = FloorPlane(np.array(fp["normal"], dtype=float), float(fp["offset"]))
        s.camera = d.get("camera")
        return s

    # -- persistence -------------------------------------------------------- #
    def save(self, path=SETUP_PATH) -> None:
        self.touch()
        try:
            path.write_text(json.dumps(self.as_dict(), indent=2), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def load(cls, path=SETUP_PATH) -> Optional["Setup"]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(raw)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def has_saved(self, path=SETUP_PATH) -> bool:
        return path.exists()
