"""Floor-plane fitting (RANSAC) from the depth frame.

Finds the dominant flat surface — the floor — robustly, ignoring the objects
and balls that sit slightly above it. Returns the plane in depth-camera space.
"""
from __future__ import annotations

import numpy as np

from ..models import CameraModel, FloorPlane


def _points_from_depth(depth_mm: np.ndarray, cam: CameraModel, step: int = 4):
    """Subsample valid depth pixels into 3D points (meters, camera space)."""
    d = depth_mm[::step, ::step].astype(np.float32)
    h, w = d.shape
    v, u = np.mgrid[0:h, 0:w]
    u = (u * step).astype(np.float32)
    v = (v * step).astype(np.float32)
    valid = (d > 200.0) & (d < 8000.0) & np.isfinite(d)
    z = d[valid] / 1000.0
    x = (u[valid] - cam.cx) * z / cam.fx
    y = (v[valid] - cam.cy) * z / cam.fy
    return np.stack([x, y, z], axis=1), z


def fit_floor_plane(
    depth_mm: np.ndarray,
    cam: CameraModel,
    dist_thresh_m: float = 0.02,
    iterations: int = 200,
    min_inliers: int = 400,
) -> FloorPlane | None:
    """RANSAC + least-squares refinement of the dominant plane in ``depth_mm``."""
    if depth_mm is None or depth_mm.size == 0:
        return None
    pts, _z = _points_from_depth(depth_mm, cam)
    if pts.shape[0] < min_inliers:
        return None

    best = (None, -1, None)  # normal, count, inlier mask
    rng = np.random.default_rng(0)
    npts = pts.shape[0]
    n = min(iterations, max(50, npts // 3))

    for _ in range(n):
        idx = rng.choice(npts, size=3, replace=False)
        p0, p1, p2 = pts[idx[0]], pts[idx[1]], pts[idx[2]]
        normal = np.cross(p1 - p0, p2 - p0)
        norm = np.linalg.norm(normal)
        if norm < 1e-9:
            continue
        normal = normal / norm
        dists = np.abs(pts @ normal - float(np.dot(normal, p0)))
        inliers = dists < dist_thresh_m
        count = int(inliers.sum())
        if count > best[1]:
            best = (normal, count, inliers)

    if best[0] is None or best[1] < min_inliers:
        return None

    normal = best[0]
    inliers = best[2]
    # Refine with least squares: n.p = 1  (using centroid-shifted points).
    pin = pts[inliers]
    centroid = pin.mean(axis=0)
    pc = pin - centroid
    # Solve pc @ n = 0 in least-squares sense: n is the smallest singular vector.
    _, _, vt = np.linalg.svd(pc, full_matrices=False)
    normal = vt[-1]
    if np.dot(normal, centroid) > 0:
        normal = -normal  # ensure offset sign is stable
    offset = float(np.dot(normal, centroid))

    return FloorPlane(normal=normal.astype(float), offset=offset)
