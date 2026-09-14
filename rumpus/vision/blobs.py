"""Above-floor detection primitives.

Everything "interesting" (balls, the cup, obstacles) sits slightly above the
floor plane. These helpers turn a depth frame + fitted plane into a height map
and connected regions, and simplify region outlines into floor-space polygons.
"""
from __future__ import annotations

import numpy as np

from ..models import CameraModel, FloorPlane

# Coarsening passes allowed when simplifying one outline, and the largest
# tolerance worth trying, as a fraction of the outline's perimeter. A shape can
# refuse to reach the target vertex count at any tolerance, so returning a
# rougher polygon has to be an option — looping until it complies is not.
SIMPLIFY_PASSES = 12
SIMPLIFY_MAX_FRAC = 0.5


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


def denoise_mask(mask: np.ndarray, open_px: int = 3) -> np.ndarray:
    """Drop speckle from an above-floor mask.

    A real depth sensor is noisy by a centimetre or two, so any low height
    threshold marks scattered lone pixels right across an empty floor. Left in,
    they label up as thousands of separate "objects".
    """
    try:
        import cv2
    except Exception:
        return mask
    if open_px < 2:
        return mask
    m = np.ascontiguousarray(mask.astype(np.uint8))
    k = np.ones((open_px, open_px), np.uint8)
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, k).astype(bool)


class Region:
    """One connected component, with the pixel mask built only if asked for.

    Subscriptable so it reads like the plain dict it replaced. The mask is lazy
    because callers reject most regions on area or position first, and a
    full-frame boolean per component is what made a noisy depth frame take
    minutes instead of milliseconds.
    """

    __slots__ = ("_labels", "_label", "_cached", "area", "bbox", "center")

    def __init__(self, labels: np.ndarray, label: int, area: int,
                 bbox: tuple[int, int, int, int], center: tuple[float, float]):
        self._labels = labels
        self._label = label
        self._cached: np.ndarray | None = None
        self.area = area
        self.bbox = bbox
        self.center = center

    @property
    def mask(self) -> np.ndarray:
        if self._cached is None:
            x0, y0, x1, y1 = self.bbox
            m = np.zeros(self._labels.shape, dtype=bool)
            # Compare inside the bounding box only. A ball is a few dozen pixels
            # in a 307k-pixel frame.
            m[y0:y1 + 1, x0:x1 + 1] = (
                self._labels[y0:y1 + 1, x0:x1 + 1] == self._label)
            self._cached = m
        return self._cached

    def __getitem__(self, key: str):
        if key == "mask":
            return self.mask
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc


def connected_regions(mask: np.ndarray, min_area: int = 4,
                      max_regions: int | None = None) -> list[Region]:
    """Label 8-connected regions as {mask, area, bbox, center}.

    ``max_regions`` keeps only that many largest components. Use it where the
    caller wants the few real objects in the scene; leave it off where small
    blobs are the point, as they are when the blobs are golf balls.
    """
    try:
        import cv2
    except Exception:
        return []
    m = np.ascontiguousarray(mask.astype(np.uint8))
    # WithStats hands back area, bounding box and centroid for every label in
    # one pass, so rejecting a component no longer costs a full-frame scan.
    num, labels, stats, centroids = cv2.connectedComponentsWithStats(
        m, connectivity=8)
    keep = [(int(stats[i, cv2.CC_STAT_AREA]), i) for i in range(1, num)
            if int(stats[i, cv2.CC_STAT_AREA]) >= min_area]
    if max_regions is not None and len(keep) > max_regions:
        keep = sorted(keep, reverse=True)[:max_regions]
        keep.sort(key=lambda kv: kv[1])
    out: list[Region] = []
    for area, i in keep:
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        out.append(Region(labels, i, area, (x, y, x + w - 1, y + h - 1),
                          (float(centroids[i][0]), float(centroids[i][1]))))
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
    """Approximate a contour with a simplified polygon (4..12 points preferred).

    The tolerance grows by a fixed factor each pass, and the passes are capped.
    Deriving it from the current vertex count instead — ``peri * 0.02 * n /
    target`` — reaches a fixed point on about one in a hundred real blob
    outlines: at ``n`` = 9 with a target of 8 the tolerance simplifies to 9
    points again, so the next pass recomputes exactly the same tolerance and
    gets exactly the same answer, forever. That hangs the game loop inside
    OpenCV with the process still alive and nothing raised, which reads as the
    camera freezing rather than as a bug.
    """
    import cv2
    if contour is None or len(contour) == 0:
        return []
    cnt = contour.reshape(-1, 2).astype(np.float32)
    peri = float(cv2.arcLength(cnt, True))
    if peri <= 0.0:
        return []
    target = max(target_points, 3)
    frac = 0.02
    approx = cv2.approxPolyDP(cnt, peri * frac, True).reshape(-1, 2)
    for _ in range(SIMPLIFY_PASSES):
        if len(approx) <= target:
            break
        frac *= 1.5
        if frac > SIMPLIFY_MAX_FRAC:
            break
        nxt = cv2.approxPolyDP(cnt, peri * frac, True).reshape(-1, 2)
        # A tolerance wide enough to flatten the outline into a line has lost
        # the object: callers discard anything under three points, so chasing
        # the target that far would silently drop a real obstacle. Keep the
        # roughest outline that is still a polygon.
        if len(nxt) < 3:
            break
        approx = nxt
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


def contour_to_floor_pixels(contour: np.ndarray, mapper) -> list[tuple[float, float]]:
    """Convert a pixel-space contour to floor coordinates depth-free (homography)."""
    pts: list[tuple[float, float]] = []
    for px, py in contour.reshape(-1, 2):
        f = mapper.pixel_to_floor(float(px), float(py))
        if f is not None:
            pts.append(f)
    return pts
