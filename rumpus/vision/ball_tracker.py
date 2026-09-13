"""Two-ball (up to 6-ball) detection & tracking.

Depth-first, color-second, per the spec: a ball is a *small* above-floor blob
whose sampled dominant hue matches a player's stored hue range. Each ball has a
smoothed position, a plausibility gate (a ball can't teleport), a moving/stopped
detector (2 cm over 0.5 s) and occlusion ("hidden") / loss handling.
"""
from __future__ import annotations

import time
from collections import deque

import cv2
import numpy as np

from ..models import CameraModel
from ..vision.blobs import above_floor_mask, connected_regions, height_map
from ..vision.geometry import FloorMapper


class TrackedBall:
    def __init__(self, ball_id: str, player_id: str, color_hex: str, hue_range=None):
        self.id = ball_id
        self.player_id = player_id
        self.color = color_hex
        self.hue_range = hue_range          # (lo, hi) in H [0..179]
        self.position: tuple[float, float] | None = None
        self.smoothed: tuple[float, float] | None = None
        self.history: deque[tuple[float, float, float]] = deque(maxlen=40)  # (t, x, y)
        self.moving = False
        self.hidden = False
        self.hidden_estimate: tuple[float, float] | None = None
        self.lost = False
        self.last_seen_t = -np.inf
        self._heading: tuple[float, float] = (0.0, 0.0)

    @property
    def stopped_duration(self) -> float:
        if not self.history:
            return 0.0
        return time.time() - self.history[-1][0]


class BallTracker:
    BALL_DIAMETER_M = 0.043

    def __init__(self) -> None:
        self.balls: dict[str, TrackedBall] = {}

    def add_ball(self, ball_id, player_id, color_hex, hue_range=None) -> None:
        self.balls[ball_id] = TrackedBall(ball_id, player_id, color_hex, hue_range)

    def reset_positions(self) -> None:
        for b in self.balls.values():
            b.position = None
            b.smoothed = None
            b.history.clear()
            b.moving = False
            b.hidden = False
            b.hidden_estimate = None
            b.lost = False
            b.last_seen_t = -np.inf

    # ------------------------------------------------------------------ #
    def update(self, color_bgr, depth_mm, mapper: FloorMapper, cam: CameraModel,
               plane, confirmed_obstacles: list, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        if depth_mm is None:
            # Color-only source (webcam): detect by hue mask, map via homography.
            dets = self._detect_color(color_bgr, mapper)
            self._match_color(dets, now)
        else:
            detections = self._detect(color_bgr, depth_mm, mapper, cam, plane)
            self._match(detections, mapper, now)
        self._update_motion(now, cam, confirmed_obstacles, mapper)

    # -- detection -------------------------------------------------------- #
    def _detect(self, color_bgr, depth_mm, mapper, cam, plane):
        if depth_mm is None or color_bgr is None:
            return []
        hmap = height_map(depth_mm, plane, cam)
        mask = above_floor_mask(hmap, min_height_m=0.005)
        regions = connected_regions(mask, min_area=3)
        out = []
        for r in regions:
            cx, cy = r["center"]
            z = float(depth_mm[int(cy), int(cx)])
            if z <= 200.0:
                continue
            expected_r = (self.BALL_DIAMETER_M / 2.0) * cam.fx / (z / 1000.0)
            # Area plausibility: within ~2.5x of the expected ball disc.
            exp_area = np.pi * expected_r * expected_r
            if r["area"] > exp_area * 4.0 or r["area"] < max(3, exp_area * 0.25):
                continue
            hue = self._sample_hue(color_bgr, r["mask"], cy, cx)
            fx, fy = mapper.depth_pixel_to_floor(cx, cy, z)
            out.append({"pos": (fx, fy), "hue": hue, "z": z})
        return out

    def _detect_color(self, color_bgr, mapper) -> list[dict]:
        """Color-only detection: per-ball hue mask -> largest plausible blob."""
        out: list[dict] = []
        if color_bgr is None:
            return out
        hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
        h = hsv[..., 0]
        s = hsv[..., 1]
        for ball in self.balls.values():
            if ball.hue_range is None:
                continue
            lo, hi = ball.hue_range
            mask = self._hue_mask(h, s, lo, hi)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in sorted(cnts, key=cv2.contourArea, reverse=True):
                area = float(cv2.contourArea(c))
                if area < 20.0:
                    break
                m = cv2.moments(c)
                if m["m00"] == 0:
                    continue
                cx = m["m10"] / m["m00"]
                cy = m["m01"] / m["m00"]
                f = mapper.pixel_to_floor(float(cx), float(cy))
                if f is None:
                    continue
                out.append({"ball_id": ball.id, "pos": (float(f[0]), float(f[1]))})
                break
        return out

    def _hue_mask(self, h: np.ndarray, s: np.ndarray, lo: int, hi: int) -> np.ndarray:
        """Binary mask of pixels whose hue is in [lo, hi] (circular) & saturated."""
        sat = s > 60
        if lo <= hi:
            m = (h >= lo) & (h <= hi) & sat
        else:  # wraps across the 0/180 boundary (e.g. red/pink)
            m = ((h >= lo) | (h <= hi)) & sat
        return (m.astype(np.uint8)) * 255

    def _sample_hue(self, color_bgr, mask, cy, cx) -> float | None:
        h, w = mask.shape
        y0, y1 = max(0, int(cy) - 8), min(h, int(cy) + 8)
        x0, x1 = max(0, int(cx) - 8), min(w, int(cx) + 8)
        patch = color_bgr[y0:y1, x0:x1]
        pm = mask[y0:y1, x0:x1]
        if patch.size == 0 or pm.sum() < 2:
            return None
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        hue = hsv[..., 0].astype(np.float32)
        sat = hsv[..., 1].astype(np.float32)
        sel = (sat > 60) & pm
        if sel.sum() < 2:
            return None
        hvals = hue[sel]
        # Circular mean.
        ang = np.deg2rad(hvals * 2.0)
        mean = np.rad2deg(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) / 2.0
        return float(mean % 180.0)

    # -- matching --------------------------------------------------------- #
    def _hue_dist(self, hue, ball: TrackedBall) -> float:
        if hue is None or ball.hue_range is None:
            return 180.0
        lo, hi = ball.hue_range
        # Hue is circular; map everything relative to the range midpoint.
        mid = (lo + hi) / 2.0
        d = abs(((hue - mid + 90) % 180.0) - 90.0)
        return d

    def _match(self, detections, mapper, now) -> None:
        # Greedy nearest-hue assignment; fall back to nearest position.
        free = list(detections)
        for ball in self.balls.values():
            if not free:
                break
            best = None
            best_score = np.inf
            for d in free:
                hd = self._hue_dist(d["hue"], ball)
                pd = 0.0
                if ball.position is not None:
                    pd = float(np.hypot(d["pos"][0] - ball.position[0], d["pos"][1] - ball.position[1]))
                # Plausibility: a ball can't jump unless it was lost a while.
                if ball.position is not None and pd > 0.5 and (now - ball.last_seen_t) < 1.0:
                    continue
                score = hd + pd * 3.0
                if score < best_score:
                    best_score = score
                    best = d
            if best is not None and best_score < 40.0:
                self._accept(ball, best["pos"], now)
                free.remove(best)
        # Balls with no match: hidden vs lost handled in _update_motion.

    def _match_color(self, detections: list[dict], now: float) -> None:
        """Assign color-only detections (already keyed by ball id)."""
        for d in detections:
            ball = self.balls.get(d["ball_id"])
            if ball is None:
                continue
            # Plausibility: a ball can't teleport unless lost a while.
            if ball.position is not None:
                pd = float(np.hypot(d["pos"][0] - ball.position[0],
                                    d["pos"][1] - ball.position[1]))
                if pd > 0.5 and (now - ball.last_seen_t) < 1.0:
                    continue
            self._accept(ball, d["pos"], now)

    def _accept(self, ball, pos, now) -> None:
        if ball.position is not None:
            dx, dy = pos[0] - ball.position[0], pos[1] - ball.position[1]
            ball._heading = (dx, dy)
        ball.position = pos
        if ball.smoothed is None:
            ball.smoothed = pos
        else:
            a = 0.5
            ball.smoothed = (ball.smoothed[0] * (1 - a) + pos[0] * a,
                             ball.smoothed[1] * (1 - a) + pos[1] * a)
        ball.history.append((now, pos[0], pos[1]))
        ball.last_seen_t = now
        ball.hidden = False
        ball.hidden_estimate = None
        ball.lost = False

    # -- motion / hidden / lost ------------------------------------------- #
    def _update_motion(self, now, cam, confirmed_obstacles, mapper) -> None:
        for ball in self.balls.values():
            # Moving/stopped over a 0.5 s window.
            cutoff = now - 0.5
            recent = [(x, y) for (t, x, y) in ball.history if t >= cutoff]
            if len(recent) >= 2:
                x0, y0 = recent[0]
                x1, y1 = recent[-1]
                ball.moving = np.hypot(x1 - x0, y1 - y0) >= 0.02
            else:
                ball.moving = False

            # Occlusion: heading leads into a confirmed obstacle & not seen.
            unseen = now - ball.last_seen_t
            if ball.position is not None and unseen > 0.3 and not ball.hidden:
                inter = self._heading_intersects(ball.position, ball._heading, confirmed_obstacles)
                if inter is not None:
                    ball.hidden = True
                    ball.hidden_estimate = inter
                    ball.moving = False
                    continue

            # Re-acquire near a hidden estimate.
            if ball.hidden and ball.position is not None:
                pass  # re-acquisition happens via _match (nearby hue blob).

            # Lost: > 2 s unseen and not hidden behind an obstacle.
            ball.lost = (unseen > 2.0) and not ball.hidden

    def _heading_intersects(self, pos, heading, obstacles):
        hx, hy = heading
        if abs(hx) < 1e-6 and abs(hy) < 1e-6:
            return None
        # Extend the heading ray and test each obstacle polygon edge.
        best = None
        for ob in obstacles:
            poly = ob.polygon
            n = len(poly)
            for i in range(n):
                a = poly[i]
                b = poly[(i + 1) % n]
                hit = _ray_segment(pos, (pos[0] + hx * 50.0, pos[1] + hy * 50.0), a, b)
                if hit is not None:
                    if best is None or np.hypot(hit[0] - pos[0], hit[1] - pos[1]) < np.hypot(best[0] - pos[0], best[1] - pos[1]):
                        best = hit
        return best


def _ray_segment(p0, p1, a, b):
    """Intersect ray p0->p1 with segment a-b; return point or None."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    ex, ey = b[0] - a[0], b[1] - a[1]
    denom = dx * ey - dy * ex
    if abs(denom) < 1e-9:
        return None
    t = ((a[0] - p0[0]) * ey - (a[1] - p0[1]) * ex) / denom
    u = ((a[0] - p0[0]) * dy - (a[1] - p0[1]) * dx) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (p0[0] + t * dx, p0[1] + t * dy)
    return None
