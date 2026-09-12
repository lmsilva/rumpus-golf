"""Above-floor detection primitives.

Everything "interesting" (balls, the cup, obstacles) sits slightly above the
floor plane. These helpers turn a depth frame + fitted plane into a height map
and connected regions, and simplify region outlines into floor-space polygons.
"""
from __future__ import annotations

import numpy as np

from ..models import CameraModel, FloorPlane


def height_map(depth_mm: np.ndarray, plane: FloorPlane, cam: CameraModel) -> np.ndarray:
    """Height (meters) of each depth pixel above the fitted floor plane.

    Invalid / out-of-range depth is marked NaN so callers can ignore it.
    """
    n = np.asarray(plane.normal, dtype=float)
    n = n / (np.linalg.norm(n) + 1e-9)
    d = float(plane.offset)

    dmm = np.asarray(depth_mm, dtype=np.float32)
    valid = (dmm > 200.0) & (dmm < 8000.0) & np.isfinite(dmm)
    h, w = dmm.shape
    v, u = np.mgrid[0:h, 0:w].astype(np.float32)
    z = np.where(valid, dmm / 1000.0, np.nan)
    rx = (u - cam.cx) / cam.fx
    ry = (v - cam.cy) / cam.fy
    height = z * (n[0] * rx + n[1] * ry + n[2]) - d
    height[~valid] = np.nan
    return height


def above_floor_mask(height: np.ndarray, min_height_m: float = 0.005) -> np.ndarray:
    return (np.isfinite(height)) & (height > min_height_m)


def connected_regions(mask: np.ndarray, min_area: int = 4) -> list[dict]:
    """Label 8-connected regions; return each as {mask, contour, area, bbox}."""
    try:
        import cv2
    except Exception:
        return []
    m = mask.astype(np.uint8)
    num, labels = cv2.connectedComponents(m, connectivity=8)
    out: list[dict] = []
    for label in range(1, num):
        region = labels == label
        area = int(region.sum())
        if area < min_area:
            continue
        ys, xs = np.where(region)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        out.append({"mask": region, "area": area, "bbox": bbox,
                    "center": (float(xs.mean()), float(ys.mean()))})
    return out


def largest_contour(region_mask: np.ndarray) -> np.ndarray | None:
    try:
        import cv2
    except Exception:
        return None
    m = region_mask.astype(np.uint8)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    return max(cnts, key=cv2.contourArea)


def simplify_polygon(contour: np.ndarray, target_points: int = 8) -> list[tuple[int, int]]:
    """Approximate a contour with a simplified polygon (4..12 points preferred)."""
    import cv2
    if contour is None or len(contour) == 0:
        return []
    cnt = contour.reshape(-1, 2).astype(np.float32)
    peri = cv2.arcLength(cnt, True)
    eps = peri * 0.02
    approx = cv2.approxPolyDP(cnt, eps, True).reshape(-1, 2)
    # Reduce/increase toward a sane vertex count.
    while len(approx) > max(target_points, 3):
        approx = cv2.approxPolyDP(cnt, cv2.arcLength(cnt, True) * (0.02 * (len(approx) / target_points)), True).reshape(-1, 2)
    return [(int(round(x)), int(round(y))) for x, y in approx]


def contour_to_floor(contour: np.ndarray, mapper, z_mm: np.ndarray) -> list[tuple[float, float]]:
    """Convert a pixel-space contour to floor coordinates using per-point depth."""
    pts: list[tuple[float, float]] = []
    for px, py in contour.reshape(-1, 2):
        x = int(np.clip(px, 0, z_mm.shape[1] - 1))
        y = int(np.clip(py, 0, z_mm.shape[0] - 1))
        z = float(z_mm[y, x])
        if z > 200.0:
            fx, fy = mapper.depth_pixel_to_floor(x, y, z)
            pts.append((fx, fy))
    return pts
