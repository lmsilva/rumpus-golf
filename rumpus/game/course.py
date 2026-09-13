"""Course templates and their floor-space layout.

``data/courses.json`` stores three templates in *percent of the play-area
bounding box* (x across, y toward the camera). At runtime we scale those into
floor meters using the user's drawn polygon, and clamp obstacles inside it.
"""
from __future__ import annotations

import json
from typing import Any

from ..paths import DATA_DIR


_COURSES_CACHE: list[dict[str, Any]] | None = None


def load_courses() -> list[dict[str, Any]]:
    global _COURSES_CACHE
    if _COURSES_CACHE is None:
        raw = json.loads((DATA_DIR / "courses.json").read_text(encoding="utf-8"))
        _COURSES_CACHE = raw.get("courses", [])
    return _COURSES_CACHE


def course_by_id(course_id: str) -> dict[str, Any] | None:
    for c in load_courses():
        if c["id"] == course_id:
            return c
    return None


def play_area_bbox(play_area: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) of a floor polygon."""
    xs = [p[0] for p in play_area]
    ys = [p[1] for p in play_area]
    return min(xs), min(ys), max(xs), max(ys)


class CourseLayout:
    """A course template resolved into floor coordinates."""

    def __init__(self, course: dict[str, Any], play_area: list[tuple[float, float]]):
        self.course = course
        self.play_area = play_area or [(-1.5, -1.0), (1.5, -1.0), (1.5, 1.0), (-1.5, 1.0)]
        self.min_x, self.min_y, self.max_x, self.max_y = play_area_bbox(self.play_area)
        self.width = self.max_x - self.min_x
        self.height = self.max_y - self.min_y

        self.start = self._resolve_circle(course["start"], default_r=0.15)
        self.hole = (self._px(course["hole"]["x"]), self._py(course["hole"]["y"]))
        self.obstacles = [self._resolve_obstacle(o) for o in course.get("obstacles", [])]

    def _px(self, pct: float) -> float:
        return self.min_x + (pct / 100.0) * self.width

    def _py(self, pct: float) -> float:
        return self.min_y + (pct / 100.0) * self.height

    def _resolve_circle(self, d: dict, default_r: float) -> dict:
        return {"x": self._px(d["x"]), "y": self._py(d["y"]), "r": d.get("r_m", default_r)}

    def _resolve_obstacle(self, o: dict) -> dict:
        x0, y0 = self._px(o["x"]), self._py(o["y"])
        x1, y1 = self._px(o["x"] + o["w"]), self._py(o["y"] + o["h"])
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        w, h = abs(x1 - x0), abs(y1 - y0)
        # Clamp to the play area bbox.
        cx = min(max(cx, self.min_x), self.max_x)
        cy = min(max(cy, self.min_y), self.max_y)
        return {
            "id": o["id"],
            "kind": o.get("kind", "soft"),
            "label": o.get("label", "Object"),
            "item": o.get("item", ""),
            "real_size_cm": o.get("realSize_cm", []),
            "cx": cx, "cy": cy, "w": w, "h": h,
            "bonus": o.get("bonus", 0),
            "penalty": o.get("penalty", 0),
        }

    def ghost_polygons(self) -> list[dict]:
        """Obstacle outline quads for the 'place your objects' overlay."""
        out = []
        for o in self.obstacles:
            x, y, w, h = o["cx"], o["cy"], o["w"], o["h"]
            poly = [(x - w / 2, y - h / 2), (x + w / 2, y - h / 2),
                    (x + w / 2, y + h / 2), (x - w / 2, y + h / 2)]
            out.append({**o, "polygon": poly})
        return out


DEFAULT_COURSE_CYCLE = ["hallway", "dogleg", "gauntlet"]


def course_for_hole(hole_number: int, override: str | None = None) -> str:
    """Course per hole cycles Beginner -> Advanced -> Expert."""
    if override:
        return override
    return DEFAULT_COURSE_CYCLE[(hole_number - 1) % len(DEFAULT_COURSE_CYCLE)]
