"""Obstacle footprint detection.

Compares averaged live depth against the empty-floor reference (Step 1) to find
every *new static above-floor region* inside the play area. Approximate
footprints are fine: each region is simplified to a 4–12 point floor polygon.

This is the only place depth-specific obstacle detection lives — everything
above it (states, edit stack, config polygons, the "hidden" rule) is agnostic,
so a tablet/AR edition swaps only this detector.
"""
from __future__ import annotations

import numpy as np

from ..models import CameraModel, FloorPlane
from ..vision.blobs import (above_floor_mask, connected_regions, contour_to_floor,
                            height_map, largest_contour, simplify_polygon)
from ..vision.geometry import FloorMapper


def point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def detect_obstacles(
    average_depth: np.ndarray,
    reference_plane: FloorPlane,
    cam: CameraModel,
    mapper: FloorMapper,
    play_area: list[tuple[float, float]],
    cup: tuple[float, float, float] | None = None,   # (x, y, r) meters
    min_height_m: float = 0.02,
    ball_diameter_m: float = 0.06,
) -> list[dict]:
    """Return proposed obstacles: {polygon, confidence, area_m2, label_kind}."""
    hmap = height_map(average_depth, reference_plane, cam)
    mask = above_floor_mask(hmap, min_height_m=min_height_m)
    regions = connected_regions(mask, min_area=12)
    proposals: list[dict] = []
    for r in regions:
        cx, cy = r["center"]
        z = float(average_depth[int(cy), int(cx)])
        if z <= 200.0:
            continue
        fx, fy = mapper.depth_pixel_to_floor(cx, cy, z)

        # Exclude the cup (already known).
        if cup is not None:
            if np.hypot(fx - cup[0], fy - cup[1]) < cup[2] + 0.12:
                continue

        # Exclude ball-sized blobs.
        scale = z / 1000.0
        area_m2 = float(r["area"]) * (scale / cam.fx) ** 2
        ball_area = np.pi * (ball_diameter_m / 2.0) ** 2
        if area_m2 < ball_area * 0.8:
            continue

        # Require the region to sit inside the play area.
        if play_area and not point_in_polygon(fx, fy, play_area):
            continue

        contour = largest_contour(r["mask"])
        if contour is None:
            continue
        simple = simplify_polygon(contour, target_points=8)
        if len(simple) < 3:
            continue
        floor_poly = contour_to_floor(np.array(simple, dtype=np.int32), mapper, average_depth)
        if len(floor_poly) < 3:
            continue

        # Confidence: blob-size plausibility (very small or enormous -> low).
        conf = 1.0
        if area_m2 < 0.02 or area_m2 > 0.6:
            conf = 0.55
        proposals.append({
            "polygon": floor_poly,
            "confidence": round(conf, 2),
            "area_m2": round(area_m2, 3),
            "kind": "soft",
        })
    return proposals
