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
    def __init__(self, ball_id: str, player_id: str, color_hex: str, hue_range=None,
                 hue_center=None, sat_floor=None, val_floor=None):
        self.id = ball_id
        self.player_id = player_id
        self.color = color_hex
        self.hue_range = hue_range          # (lo, hi) in H [0..179], fallback only
        self.hue_center = hue_center        # sampled OpenCV hue
        self.sat_floor = sat_floor          # 60% of sampled S
        self.val_floor = val_floor          # 60% of sampled V
        self.position: tuple[float, float] | None = None
        self.smoothed: tuple[float, float] | None = None
        self.history: deque[tuple[float, float, float]] = deque(maxlen=40)  # (t, x, y)
        self.moving = False
        self.hidden = False
        self.hidden_estimate: tuple[float, float] | None = None
        self.lost = False
        self.held = False
        self.last_seen_t = -np.inf
        self._heading: tuple[float, float] = (0.0, 0.0)
        self._speed = 0.0

    @property
    def stopped_duration(self) -> float:
        if not self.history:
            return 0.0
        return time.time() - self.history[-1][0]


class BallTracker:
    BALL_DIAMETER_M = 0.043

    def __init__(self) -> None:
        self.balls: dict[str, TrackedBall] = {}
        self.debug: dict = {"dets": [], "masks": {}, "gate": {}}

    def add_ball(self, ball_id, player_id, color_hex, hue_range=None,
                 hue_center=None, sat_floor=None, val_floor=None) -> None:
        self.balls[ball_id] = TrackedBall(
            ball_id, player_id, color_hex, hue_range,
            hue_center=hue_center, sat_floor=sat_floor, val_floor=val_floor,
        )

    def reset_positions(self) -> None:
        for b in self.balls.values():
            b.position = None
            b.smoothed = None
            b.history.clear()
            b.moving = False
            b.hidden = False
            b.hidden_estimate = None
            b.lost = False
            b.held = False
            b.last_seen_t = -np.inf

    def seed_position(self, ball_id: str, pos: tuple[float, float], now: float | None = None,
                      hold: bool = False) -> None:
        ball = self.balls.get(ball_id)
        if ball is None or pos is None:
            return
        now = now if now is not None else time.time()
        ball.position = (float(pos[0]), float(pos[1]))
        ball.smoothed = (float(pos[0]), float(pos[1]))
        ball.history.clear()
        ball.history.append((now, float(pos[0]), float(pos[1])))
        ball.last_seen_t = now
        ball.moving = False
        ball.hidden = False
        ball.hidden_estimate = None
        ball.lost = False
        ball.held = bool(hold)

    # ------------------------------------------------------------------ #
    def update(self, color_bgr, depth_mm, mapper: FloorMapper, cam: CameraModel,
               plane, confirmed_obstacles: list, now: float | None = None,
               play_area: list | None = None) -> None:
        now = now if now is not None else time.time()
        self.debug = {"dets": [], "masks": {}, "gate": {}}
        if depth_mm is None:
            # Color-only source (webcam): detect by hue mask, map via homography.
            dets = self._detect_color(color_bgr, mapper, play_area)
            self._match_color(dets, now)
        else:
            detections = self._detect(color_bgr, depth_mm, mapper, cam, plane, play_area)
            self._match(detections, mapper, now)
        self._update_motion(now, cam, confirmed_obstacles, mapper)

    # -- detection -------------------------------------------------------- #
    def _detect(self, color_bgr, depth_mm, mapper, cam, plane, play_area=None):
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
            if play_area and not _point_in_poly(float(fx), float(fy), play_area):
                continue
            out.append({"pos": (fx, fy), "hue": hue, "z": z})
        return out

    def _detect_color(self, color_bgr, mapper, play_area=None) -> list[dict]:
        """Color-only detection: small circular hue blobs, not the carpet.

        Prefer the blob nearest the last pose so a lamp does not steal the
        track, but never fall back to averaging a window of beige pixels.
        """
        out: list[dict] = []
        if color_bgr is None or mapper is None:
            return out
        hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
        for ball in self.balls.values():
            mask = self._ball_mask(ball, hsv)
            self.debug["masks"][ball.id] = mask
            cands = self._circular_blobs(ball, hsv, mapper, play_area, mask=mask)
            if not cands:
                continue
            out.append({"ball_id": ball.id, "cands": cands})
        return out

    def find_near_pixel(self, ball_id: str, color_bgr, mapper, nx: float, ny: float,
                        play_area=None) -> tuple[float, float] | None:
        """Return the matching circular blob nearest a normalized click."""
        ball = self.balls.get(ball_id)
        if ball is None or color_bgr is None or mapper is None:
            return None
        hh, ww = color_bgr.shape[:2]
        hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
        click = (float(nx) * ww, float(ny) * hh)
        best = None
        best_d = 0.14 * max(ww, hh)
        for _score, pos, cx, cy in self._circular_blobs(ball, hsv, mapper, play_area, ranked=False):
            d = float(np.hypot(cx - click[0], cy - click[1]))
            if d < best_d:
                best_d = d
                best = pos
        return best

    def _circular_blobs(self, ball: TrackedBall, hsv, mapper, play_area=None,
                        ranked: bool = True, mask=None):
        """Ball-sized circular contours for this player's mask.

        Returns (score, floor_pos, cx, cy). Lower score is better when ranked.
        Minimum area is 0.3× the expected ball disc at that floor point, so a
        single-pixel glint cannot beat the real ball just by being nearer.
        """
        if mask is None:
            mask = self._ball_mask(ball, hsv)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        anchor = ball.smoothed or ball.position
        if anchor is None and play_area:
            ax = sum(p[0] for p in play_area) / len(play_area)
            ay = sum(p[1] for p in play_area) / len(play_area)
            anchor = (ax, ay)
        if anchor is None:
            anchor = (0.0, 0.0)
        r_px = max(4.0, float(mapper.radius_to_pixels(anchor[0], anchor[1], 0.0215)))
        min_a = np.pi * (r_px * 0.3) ** 2
        max_a = np.pi * (r_px * 2.8) ** 2
        exp_a = np.pi * r_px * r_px
        out = []
        for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            area = float(cv2.contourArea(c))
            if area < min_a or area > max_a:
                continue
            peri = float(cv2.arcLength(c, True))
            if peri < 8:
                continue
            circ = 4.0 * np.pi * area / (peri * peri)
            if circ < 0.52:
                continue
            m = cv2.moments(c)
            if m["m00"] == 0:
                continue
            cx = m["m10"] / m["m00"]
            cy = m["m01"] / m["m00"]
            f = mapper.pixel_to_floor(float(cx), float(cy))
            if f is None:
                continue
            fx, fy = float(f[0]), float(f[1])
            if play_area and not _point_in_poly(fx, fy, play_area):
                continue
            if ball.position is not None:
                dist = float(np.hypot(fx - ball.position[0], fy - ball.position[1]))
            else:
                dist = 0.0
            size_err = abs(area - exp_a) / max(exp_a, 1.0)
            # Quality first. Distance only breaks ties so a rolled ball
            # still beats a bright rug glint at the last pose.
            score = (1.0 - circ) * 3.0 + size_err * 2.0 + min(dist, 0.35) * 0.15
            out.append((score, (fx, fy), float(cx), float(cy)))
        if ranked:
            near = [t for t in out if ball.position is not None
                    and float(np.hypot(t[1][0] - ball.position[0],
                                       t[1][1] - ball.position[1])) < 0.45]
            pool = near if near else out
            pool.sort(key=lambda t: t[0])
            return pool
        return out

    def _ball_mask(self, ball: TrackedBall, hsv: np.ndarray) -> np.ndarray:
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        green = (h >= 40) & (h <= 90)
        sat_min = int(ball.sat_floor) if ball.sat_floor is not None else 80
        val_min = int(ball.val_floor) if ball.val_floor is not None else 110
        if ball.hue_center is not None:
            lo, hi = hue_window(ball.hue_center, 10.0)
            raw = self._hue_mask(h, s, lo, hi, sat_min=sat_min, val=v, val_min=val_min)
        elif ball.hue_range is None:
            # White / low-sat: beige carpet is mid-sat and not this bright.
            s_max = max(40, sat_min)
            raw = ((s <= s_max) & (v >= val_min)).astype(np.uint8) * 255
        else:
            lo, hi = ball.hue_range
            raw = self._hue_mask(h, s, lo, hi, sat_min=sat_min, val=v, val_min=val_min)
        raw[green] = 0
        return raw

    def _hue_mask(self, h: np.ndarray, s: np.ndarray, lo: int, hi: int,
                  sat_min: int = 110, val: np.ndarray | None = None,
                  val_min: int = 0) -> np.ndarray:
        """Binary mask of pixels whose hue is in [lo, hi] (circular)."""
        sat = s > sat_min
        if val is not None and val_min > 0:
            sat = sat & (val > val_min)
        if lo <= hi:
            m = (h >= lo) & (h <= hi) & sat
        else:  # wraps across the 0/180 boundary (e.g. red/pink)
            m = ((h >= lo) | (h <= hi)) & sat
        return (m.astype(np.uint8)) * 255

    def _sample_hue(self, color_bgr, mask, cy, cx) -> float | None:
        hue, _s, _v = sample_hsv_blob(color_bgr, mask, cy, cx)
        return hue

    # -- matching --------------------------------------------------------- #
    def _hue_dist(self, hue, ball: TrackedBall) -> float:
        if hue is None:
            return 180.0
        if ball.hue_center is not None:
            return hue_circular_dist(hue, ball.hue_center)
        if ball.hue_range is None:
            return 180.0
        lo, hi = ball.hue_range
        if lo <= hi:
            mid = (lo + hi) / 2.0
        else:
            mid = ((lo + hi + 180.0) / 2.0) % 180.0
        return hue_circular_dist(hue, mid)

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
                if ball.position is not None and not self._within_gate(ball, d["pos"], now):
                    self.debug["dets"].append({
                        "ball_id": ball.id, "pos": d["pos"], "ok": False,
                        "gate": self.gate_radius(ball, now),
                    })
                    continue
                score = hd + pd * 3.0
                if score < best_score:
                    best_score = score
                    best = d
            if best is not None and best_score < 40.0:
                self._accept(ball, best["pos"], now)
                self.debug["dets"].append({
                    "ball_id": ball.id, "pos": best["pos"], "ok": True,
                    "gate": self.gate_radius(ball, now),
                })
                free.remove(best)
        # Balls with no match: hidden vs lost handled in _update_motion.

    def _match_color(self, detections: list[dict], now: float) -> None:
        """Assign color-only detections (already keyed by ball id)."""
        for d in detections:
            ball = self.balls.get(d["ball_id"])
            if ball is None:
                continue
            gate = self.gate_radius(ball, now)
            self.debug["gate"][ball.id] = gate
            accepted = None
            for _score, pos, _cx, _cy in d.get("cands") or []:
                if ball.position is not None and not self._within_gate(ball, pos, now):
                    self.debug["dets"].append({
                        "ball_id": ball.id, "pos": pos, "ok": False, "gate": gate,
                    })
                    continue
                accepted = pos
                break
            if accepted is not None:
                self._accept(ball, accepted, now)
                self.debug["dets"].append({
                    "ball_id": ball.id, "pos": accepted, "ok": True, "gate": gate,
                })

    def gate_radius(self, ball: TrackedBall, now: float) -> float:
        """Meters from last pose that a new detection may land in."""
        if ball.held:
            return 0.55
        if ball.position is None or not np.isfinite(ball.last_seen_t):
            return 2.0
        dt = max(1e-3, now - ball.last_seen_t)
        return 0.3 + max(0.0, ball._speed) * dt * 1.5

    def _within_gate(self, ball: TrackedBall, pos, now: float) -> bool:
        if ball.position is None:
            return True
        pd = float(np.hypot(pos[0] - ball.position[0], pos[1] - ball.position[1]))
        return pd <= self.gate_radius(ball, now)

    def _recent_speed(self, ball: TrackedBall) -> float:
        if len(ball.history) < 2:
            return 0.0
        t1, x1, y1 = ball.history[-1]
        for t, x, y in reversed(ball.history):
            if t1 - t >= 0.25:
                dt = t1 - t
                return float(np.hypot(x1 - x, y1 - y) / dt) if dt > 1e-4 else 0.0
        t0, x0, y0 = ball.history[0]
        dt = t1 - t0
        if dt < 1e-3:
            return 0.0
        return float(np.hypot(x1 - x0, y1 - y0) / dt)

    def _accept(self, ball, pos, now) -> None:
        if ball.position is not None:
            dx, dy = pos[0] - ball.position[0], pos[1] - ball.position[1]
            ball._heading = (dx, dy)
        ball.position = pos
        if ball.smoothed is None:
            ball.smoothed = pos
        else:
            a = 0.85 if ball.moving else 0.5
            ball.smoothed = (ball.smoothed[0] * (1 - a) + pos[0] * a,
                             ball.smoothed[1] * (1 - a) + pos[1] * a)
        ball.history.append((now, pos[0], pos[1]))
        ball.last_seen_t = now
        ball.hidden = False
        ball.hidden_estimate = None
        ball.lost = False
        ball.held = False
        ball._speed = self._recent_speed(ball)

    # -- motion / hidden / lost ------------------------------------------- #
    def _update_motion(self, now, cam, confirmed_obstacles, mapper) -> None:
        for ball in self.balls.values():
            # Moving/stopped over a 0.5 s window.
            cutoff = now - 0.5
            recent = [(x, y) for (t, x, y) in ball.history if t >= cutoff]
            moved = False
            if len(recent) >= 2:
                x0, y0 = recent[0]
                x1, y1 = recent[-1]
                disp = float(np.hypot(x1 - x0, y1 - y0))
                # Enter moving above 3 cm; leave only below 1.5 cm.
                if ball.moving:
                    moved = disp >= 0.015
                else:
                    moved = disp >= 0.03
            ball.moving = moved
            if moved:
                ball._speed = max(ball._speed, self._recent_speed(ball))

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

            # A just-clicked tee stays visible for a moment if the camera
            # has not re-acquired yet. Do not freeze motion once it has.
            if ball.held and ball.position is not None and unseen > 0.05:
                ball.lost = False
            else:
                ball.lost = bool((unseen > 2.0) and not ball.hidden)

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

    def draw_debug(self, color_bgr, mapper, fps: float | None = None):
        """Tint mask pixels and mark accept / reject / gate on a copy of the feed."""
        if color_bgr is None:
            return None
        img = color_bgr.copy()
        hh, ww = img.shape[:2]
        for ball in self.balls.values():
            mask = self.debug.get("masks", {}).get(ball.id)
            if mask is None or mask.shape[:2] != (hh, ww):
                continue
            tint = np.zeros_like(img)
            hex_c = (ball.color or "#8be9c3").lstrip("#")
            try:
                r, g, b = int(hex_c[0:2], 16), int(hex_c[2:4], 16), int(hex_c[4:6], 16)
            except Exception:
                r, g, b = 139, 233, 195
            tint[mask > 0] = (b, g, r)
            cv2.addWeighted(tint, 0.45, img, 1.0, 0, img)
        for det in self.debug.get("dets", []):
            pos = det.get("pos")
            if pos is None or mapper is None:
                continue
            u, v = mapper.floor_to_pixel(pos[0], pos[1])
            pt = (int(round(u)), int(round(v)))
            ok = bool(det.get("ok"))
            color = (139, 233, 195) if ok else (87, 107, 255)
            cv2.circle(img, pt, 10, color, 2 if ok else 1)
            cv2.putText(img, "ok" if ok else "no", (pt[0] + 8, pt[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        now = time.time()
        for ball in self.balls.values():
            pos = ball.smoothed or ball.position
            if pos is None or mapper is None:
                continue
            gate = float(self.debug.get("gate", {}).get(ball.id, self.gate_radius(ball, now)))
            u, v = mapper.floor_to_pixel(pos[0], pos[1])
            r_px = max(6.0, float(mapper.radius_to_pixels(pos[0], pos[1], gate)))
            cv2.circle(img, (int(round(u)), int(round(v))), int(r_px), (242, 239, 232), 1)
            cv2.putText(img, f"{ball.id} r={gate:.2f}m",
                        (int(u) + 8, int(v) + 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (242, 239, 232), 1, cv2.LINE_AA)
        if fps is not None:
            cv2.putText(img, f"{fps:.1f} fps", (16, 32),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (139, 233, 195), 2, cv2.LINE_AA)
        return img


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


# Spec colors: white, neon orange, neon pink, neon yellow. Never green (cup).
# Beige carpet sits in the orange hue band at medium saturation — require
# high S/V so the rug cannot register as a ball.
_BALL_SWATCHES = {
    "white":  {"color": "#f2efe8", "hue_range": None},
    "orange": {"color": "#ff8a3d", "hue_range": (8, 24)},
    "yellow": {"color": "#ffd84d", "hue_range": (22, 38)},
    "pink":   {"color": "#ff5fa8", "hue_range": (150, 8)},
    "blue":   {"color": "#5b8cff", "hue_range": (95, 128)},
}


def classify_ball_swatch(bgr: tuple[int, int, int], loose: bool = False) -> dict | None:
    """Return a player swatch only for white or neon ball colors.

    Carpet / wood / shadows return None. ``loose`` is for a deliberate click.
    """
    patch = np.uint8([[bgr]])
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[0][0]
    h, s, v = int(hsv[0]), int(hsv[1]), int(hsv[2])
    name = _name_allowed_ball(h, s, v, loose=loose)
    if name is None:
        return None
    spec = _BALL_SWATCHES[name]
    return {
        "color": spec["color"],
        "hue_name": name,
        "hue_range": spec["hue_range"],
        "rejected": False,
    }


def _name_allowed_ball(h: int, s: int, v: int, loose: bool = False) -> str | None:
    s_white = 40 if loose else 28
    v_white = 190 if loose else 210
    s_neon = 90 if loose else 125
    v_neon = 130 if loose else 155
    if s <= s_white and v >= v_white:
        return "white"
    if s < s_neon or v < v_neon:
        return None
    # Green cup band — never a ball, even if saturated.
    if 40 <= h <= 90:
        return None
    if 8 <= h <= 24:
        return "orange"
    if 22 <= h <= 38:
        return "yellow"
    if h >= 150 or h <= 8:
        return "pink"
    if 95 <= h <= 128:
        return "blue"
    return None


def _allowed_ball_mask(hsv: np.ndarray) -> np.ndarray:
    """Pixels that look like a white or neon ball — not a beige floor."""
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    white = (s <= 28) & (v >= 210)
    sat = (s >= 125) & (v >= 155)
    orange = sat & (h >= 8) & (h <= 24)
    yellow = sat & (h >= 22) & (h <= 38)
    pink = sat & ((h >= 150) | (h <= 8))
    blue = sat & (h >= 95) & (h <= 128)
    return (white | orange | yellow | pink | blue).astype(np.uint8) * 255


def detect_setup_balls(
    color_bgr,
    reference_bgr,
    mapper: FloorMapper,
    play_area: list[tuple[float, float]] | None,
    cup: tuple[float, float, float] | None = None,
) -> list[dict]:
    """Find white / neon golf balls on a color frame for the S09 assign step.

    Hue + saturation only — no floor differencing (that matched carpet).
    """
    if color_bgr is None or mapper is None:
        return []
    hh, ww = color_bgr.shape[:2]
    area_mask = np.zeros((hh, ww), np.uint8)
    if play_area:
        pts = []
        for x, y in play_area:
            u, v = mapper.floor_to_pixel(x, y)
            pts.append([int(u), int(v)])
        if len(pts) >= 3:
            cv2.fillPoly(area_mask, [np.array(pts, np.int32)], 255)
        else:
            area_mask[:] = 255
    else:
        area_mask[:] = 255

    hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_and(_allowed_ball_mask(hsv), area_mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    if play_area:
        cx = sum(p[0] for p in play_area) / len(play_area)
        cy = sum(p[1] for p in play_area) / len(play_area)
    else:
        cx = cy = 0.0
    r_px = max(4.0, float(mapper.radius_to_pixels(cx, cy, 0.0215)))
    min_a = max(18.0, np.pi * (r_px * 0.4) ** 2)
    max_a = np.pi * (r_px * 2.4) ** 2

    out: list[dict] = []
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        area = float(cv2.contourArea(c))
        if area < min_a or area > max_a:
            continue
        peri = float(cv2.arcLength(c, True))
        if peri < 8:
            continue
        circ = 4.0 * np.pi * area / (peri * peri)
        if circ < 0.58:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        px = m["m10"] / m["m00"]
        py = m["m01"] / m["m00"]
        floor = mapper.pixel_to_floor(float(px), float(py))
        if floor is None:
            continue
        if play_area and not _point_in_poly(floor[0], floor[1], play_area):
            continue
        if cup is not None and np.hypot(floor[0] - cup[0], floor[1] - cup[1]) < cup[2] + 0.06:
            continue
        ix, iy = int(np.clip(px, 0, ww - 1)), int(np.clip(py, 0, hh - 1))
        bgr = tuple(int(x) for x in color_bgr[iy, ix])
        swatch = classify_ball_swatch(bgr)
        if swatch is None:
            continue
        blob_mask = np.zeros((hh, ww), np.uint8)
        cv2.drawContours(blob_mask, [c], -1, 255, -1)
        sampled = attach_sampled_hsv(color_bgr, blob_mask, py, px, swatch)
        out.append({"pos": (float(floor[0]), float(floor[1])), **sampled})
    return out


def hue_circular_dist(a: float, b: float) -> float:
    """Shortest distance on the OpenCV hue circle (0..179)."""
    return float(abs(((float(a) - float(b) + 90.0) % 180.0) - 90.0))


def hue_window(center: float, half: float = 10.0) -> tuple[int, int]:
    lo = int(round(float(center) - half)) % 180
    hi = int(round(float(center) + half)) % 180
    return lo, hi


def sample_hsv_blob(color_bgr, mask, cy, cx) -> tuple[float | None, int, int]:
    """Circular-mean hue plus median sat/val of a blob (or a small patch)."""
    h, w = mask.shape
    y0, y1 = max(0, int(cy) - 10), min(h, int(cy) + 11)
    x0, x1 = max(0, int(cx) - 10), min(w, int(cx) + 11)
    patch = color_bgr[y0:y1, x0:x1]
    pm = mask[y0:y1, x0:x1] > 0
    if patch.size == 0:
        return None, 0, 0
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    if int(pm.sum()) < 2:
        pm = np.ones(hsv.shape[:2], dtype=bool)
    hue = hsv[..., 0].astype(np.float32)[pm]
    sat = hsv[..., 1].astype(np.float32)[pm]
    val = hsv[..., 2].astype(np.float32)[pm]
    if hue.size < 2:
        return None, 0, 0
    sat_m = int(np.median(sat))
    val_m = int(np.median(val))
    # Low-sat pixels have noisy hue — skip them for the circular mean.
    sel = sat > 40
    if int(sel.sum()) < 2:
        return None, sat_m, val_m
    hvals = hue[sel]
    ang = np.deg2rad(hvals * 2.0)
    mean = np.rad2deg(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) / 2.0
    return float(mean % 180.0), sat_m, val_m


def attach_sampled_hsv(color_bgr, mask, cy, cx, swatch: dict) -> dict:
    """Keep the swatch for the chip / name; store the real sampled hue."""
    hue, sat, val = sample_hsv_blob(color_bgr, mask, cy, cx)
    out = dict(swatch)
    name = out.get("hue_name")
    if name == "white" or hue is None:
        out["hue_center"] = None
        out["hue_range"] = None
        out["sat_floor"] = max(8, int(sat * 0.6))
        out["val_floor"] = max(160, int(val * 0.85))
        return out
    out["hue_center"] = float(hue)
    out["hue_range"] = list(hue_window(hue, 10.0))
    out["sat_floor"] = max(40, int(sat * 0.6))
    out["val_floor"] = max(80, int(val * 0.6))
    return out


def sample_ball_at_pixel(color_bgr, u: float, v: float) -> dict | None:
    """Classify + sample a clicked pixel (S09 manual assign)."""
    if color_bgr is None:
        return None
    hh, ww = color_bgr.shape[:2]
    ix = int(np.clip(u, 0, ww - 1))
    iy = int(np.clip(v, 0, hh - 1))
    bgr = tuple(int(x) for x in color_bgr[iy, ix])
    swatch = classify_ball_swatch(bgr, loose=True)
    if swatch is None:
        return None
    mask = np.zeros((hh, ww), np.uint8)
    cv2.circle(mask, (ix, iy), 12, 255, -1)
    return attach_sampled_hsv(color_bgr, mask, iy, ix, swatch)


def hue_clash_pairs(players: list) -> list[tuple[str, str, float]]:
    """Pairs whose sampled hues sit closer than 20 units (S09 warning)."""
    samples = []
    for p in players:
        center = getattr(p, "hue_center", None)
        if center is None:
            continue
        samples.append((getattr(p, "name", "?") or "ball", float(center)))
    clashes = []
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            d = hue_circular_dist(samples[i][1], samples[j][1])
            if d < 20.0:
                clashes.append((samples[i][0], samples[j][0], d))
    return clashes


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
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
