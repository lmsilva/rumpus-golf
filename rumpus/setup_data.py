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
from .paths import REFERENCE_PATH, SETUP_PATH


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _zone(d: Any) -> Optional[CircleZone]:
    """A CircleZone from loose JSON, or None. Never raises on a bad file."""
    if not isinstance(d, dict):
        return None
    if d.get("x") is None or d.get("y") is None:
        return None
    return CircleZone(_num(d.get("x")), _num(d.get("y")), _num(d.get("r"), 0.045))


def _points(raw: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for p in raw:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            out.append((_num(p[0]), _num(p[1])))
    return out


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
        """Build a Setup from loose JSON.

        A half-written or hand-edited file must degrade to "recalibrate that
        part", never raise — this runs inside the Boot -> Load input handler.
        """
        if not isinstance(d, dict):
            d = {}
        s = cls(
            name=d.get("name", "") or "",
            sensor_model=d.get("sensor_model", "") or "",
            play_area=_points(d.get("play_area")),
            courses=[str(c) for c in d.get("courses") or [] if isinstance(c, (str, int))],
            saved_at=_num(d.get("saved_at")),
        )
        s.start = _zone(d.get("start"))
        s.hole = _zone(d.get("hole"))
        for o in d.get("obstacles") or []:
            if not isinstance(o, dict):
                continue
            poly = _points(o.get("polygon"))
            if len(poly) < 3:
                continue
            s.obstacles.append(Obstacle(
                id=str(o.get("id", "")), label=str(o.get("label", "Object")),
                kind=str(o.get("kind", "soft")),
                polygon=poly,
                item=str(o.get("item", "")),
                real_size_cm=o.get("real_size_cm") or [],
                state=str(o.get("state", "confirmed")),
                confidence=_num(o.get("confidence"), 1.0),
                penalty=int(_num(o.get("penalty"))), bonus=int(_num(o.get("bonus"))),
            ))
        for p in d.get("players") or []:
            if not isinstance(p, dict):
                continue
            hue_range = p.get("hue_range")
            s.players.append(Player(
                id=str(p.get("id", "")), name=str(p.get("name", "")),
                color=str(p.get("color", "")),
                hue_name=str(p.get("hue_name", "")),
                hue_range=(int(_num(hue_range[0])), int(_num(hue_range[1])))
                if isinstance(hue_range, (list, tuple)) and len(hue_range) >= 2 else None,
                hue_center=_num(p["hue_center"]) if p.get("hue_center") is not None else None,
                sat_floor=int(_num(p["sat_floor"])) if p.get("sat_floor") is not None else None,
                val_floor=int(_num(p["val_floor"])) if p.get("val_floor") is not None else None,
                order=int(_num(p.get("order"))),
            ))
        fp = d.get("floor_plane")
        if isinstance(fp, dict) and isinstance(fp.get("normal"), (list, tuple)):
            try:
                normal = np.array([float(x) for x in fp["normal"]], dtype=float)
                if normal.size == 3 and np.isfinite(normal).all():
                    s.floor_plane = FloorPlane(normal, _num(fp.get("offset")))
            except (TypeError, ValueError):
                pass
        s.camera = d.get("camera") if isinstance(d.get("camera"), dict) else None
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
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None
        try:
            return cls.from_dict(raw)
        except Exception:
            return None

    def has_saved(self, path=SETUP_PATH) -> bool:
        return path.exists()


# --------------------------------------------------------------------------- #
# Empty-floor reference image
#
# The color-only (webcam) pipeline diffs each frame against a picture of the
# bare floor to find balls and objects. It is far too big for the JSON, so it
# is saved beside it — without this, a reloaded setup detects almost nothing
# until the user recaptures the floor.
# --------------------------------------------------------------------------- #
def save_reference_color(image, path=REFERENCE_PATH) -> None:
    if image is None:
        return
    try:
        import cv2
        cv2.imwrite(str(path), image)
    except Exception:
        pass


def load_reference_color(path=REFERENCE_PATH):
    try:
        import cv2
        if not path.exists():
            return None
        return cv2.imread(str(path))
    except Exception:
        return None
