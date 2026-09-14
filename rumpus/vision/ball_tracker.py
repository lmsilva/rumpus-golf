"""Ball detection & tracking.

Both sensor paths feed one tracker. A depth camera (Kinect) proposes candidates
from blobs standing above the fitted floor plane; a 2D webcam proposes them from
motion against the empty-floor reference. Either way hue identifies them, a
constant-velocity Kalman filter gates and coasts through dropouts, and a global
assignment keeps identities from swapping when two balls pass close.
"""
from __future__ import annotations

import time
from collections import deque

import cv2
import numpy as np

from ..models import CameraModel
from ..vision.blobs import (above_floor_mask, connected_regions, denoise_mask,
                            height_map)
from ..vision.geometry import FloorMapper


# Color-only: process noise on velocity is ~0.5 m/s² so friction is followable.
# A putt is an impulse — P also grows on a miss so the gate can re-acquire.
# Measurement noise is ~1.5 cm.
_ACCEL_VAR = 0.5 * 0.5
_MEAS_VAR = 0.015 * 0.015
_COAST_MAX_S = 0.70
_COAST_DECAY = 0.35
_HUE_INFEASIBLE = 35.0
_GATE_MIN_M = 0.12
_GATE_MAX_M = 1.20
_START_SPEED = 0.06
_START_DISP = 0.025
_STOP_SPEED = 0.10
_STOP_DISP = 0.015
# A rolling ball can look still for a couple of frames (filter dip, dropout).
# Stay "in play" until rest holds on real detections.
_REST_HOLD_S = 0.40
_REST_HITS = 8


class BallFilter:
    """Floor-meter constant-velocity Kalman: state [x, y, vx, vy]."""

    def __init__(self) -> None:
        self.x = np.zeros(4, dtype=np.float64)
        self.P = np.eye(4, dtype=np.float64)
        self.ready = False

    def seed(self, pos: tuple[float, float]) -> None:
        self.x[:] = (float(pos[0]), float(pos[1]), 0.0, 0.0)
        self.P = np.diag([0.02 ** 2, 0.02 ** 2, 0.4 ** 2, 0.4 ** 2])
        self.ready = True

    def predict(self, dt: float) -> None:
        if not self.ready:
            return
        dt = float(np.clip(dt, 1e-3, 0.25))
        F = np.array([
            [1.0, 0.0, dt, 0.0],
            [0.0, 1.0, 0.0, dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ])
        dt2 = dt * dt
        dt3 = dt2 * dt
        dt4 = dt2 * dt2
        q = _ACCEL_VAR
        Q = np.array([
            [dt4 / 4.0 * q, 0.0, dt3 / 2.0 * q, 0.0],
            [0.0, dt4 / 4.0 * q, 0.0, dt3 / 2.0 * q],
            [dt3 / 2.0 * q, 0.0, dt2 * q, 0.0],
            [0.0, dt3 / 2.0 * q, 0.0, dt2 * q],
        ])
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

    def decay_velocity(self, dt: float, rate: float = _COAST_DECAY) -> None:
        if not self.ready:
            return
        scale = max(0.0, 1.0 - rate * float(dt))
        self.x[2] *= scale
        self.x[3] *= scale
        # No measurement this frame — open the gate a little for the next one.
        grow = (0.04 + 0.20 * self.speed * float(dt)) ** 2
        self.P[0, 0] += grow
        self.P[1, 1] += grow

    def correct(self, pos: tuple[float, float]) -> None:
        z = np.array([float(pos[0]), float(pos[1])])
        if not self.ready:
            self.seed(pos)
            return
        H = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
        R = np.diag([_MEAS_VAR, _MEAS_VAR])
        y = z - H @ self.x
        S = H @ self.P @ H.T + R
        try:
            K = self.P @ H.T @ np.linalg.inv(S)
        except np.linalg.LinAlgError:
            self.seed(pos)
            return
        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ H) @ self.P

    @property
    def pos(self) -> tuple[float, float]:
        return (float(self.x[0]), float(self.x[1]))

    @property
    def vel(self) -> tuple[float, float]:
        return (float(self.x[2]), float(self.x[3]))

    @property
    def speed(self) -> float:
        return float(np.hypot(self.x[2], self.x[3]))

    def pos_std(self) -> float:
        if not self.ready:
            return 0.4
        return float(np.sqrt(max(self.P[0, 0], self.P[1, 1], 1e-8)))

    def mahalanobis(self, pos: tuple[float, float]) -> float:
        if not self.ready:
            return float(np.hypot(pos[0] - self.x[0], pos[1] - self.x[1]) / 0.05)
        innov = np.array([pos[0] - self.x[0], pos[1] - self.x[1]])
        S = self.P[:2, :2] + np.diag([_MEAS_VAR, _MEAS_VAR])
        try:
            return float(np.sqrt(max(0.0, innov @ np.linalg.inv(S) @ innov)))
        except np.linalg.LinAlgError:
            return float(np.hypot(innov[0], innov[1]) / max(self.pos_std(), 0.01))


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
        self.coasting = False
        self.blurred = False
        self.last_seen_t = -np.inf
        self._heading: tuple[float, float] = (0.0, 0.0)
        self._speed = 0.0
        self._rest_since = None
        self._rest_hits = 0
        self.filter = BallFilter()

    @property
    def stopped_duration(self) -> float:
        if not self.history:
            return 0.0
        return time.time() - self.history[-1][0]


class BallTracker:
    BALL_DIAMETER_M = 0.043

    def __init__(self) -> None:
        self.balls: dict[str, TrackedBall] = {}
        self.debug: dict = {"dets": [], "masks": {}, "gate": {},
                            "motion_mask": None, "pred": {}, "costs": []}
        self._mog2 = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=16, detectShadows=True,
        )
        self._last_t: float | None = None

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
            b.coasting = False
            b.blurred = False
            b.last_seen_t = -np.inf
            b._rest_since = None
            b._rest_hits = 0
            b.filter = BallFilter()

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
        ball.coasting = False
        ball.blurred = False
        ball._rest_since = None
        ball._rest_hits = 0
        ball.filter.seed((float(pos[0]), float(pos[1])))

    # ------------------------------------------------------------------ #
    def update(self, color_bgr, depth_mm, mapper: FloorMapper, cam: CameraModel,
               plane, confirmed_obstacles: list, now: float | None = None,
               play_area: list | None = None,
               reference_color=None) -> None:
        now = now if now is not None else time.time()
        dt = 1.0 / 30.0
        if self._last_t is not None:
            dt = float(np.clip(now - self._last_t, 1e-3, 0.25))
        self._last_t = now
        self.debug = {"dets": [], "masks": {}, "gate": {},
                      "motion_mask": None, "pred": {}, "costs": []}

        for ball in self.balls.values():
            if ball.filter.ready:
                ball.filter.predict(dt)
            self.debug["pred"][ball.id] = ball.filter.pos if ball.filter.ready else None
            self.debug["gate"][ball.id] = self.predict_gate_radius(ball)

        cands = None
        if mapper is not None:
            if depth_mm is not None and plane is not None and cam is not None:
                cands = self._depth_candidates(
                    color_bgr, depth_mm, mapper, cam, plane, play_area)
            elif color_bgr is not None:
                cands = self._color_candidates(
                    color_bgr, mapper, play_area, reference_color)
        if cands is None:
            self._coast_or_lose(now, dt)
        else:
            assigned = self._assign_global(cands)
            for ball in self.balls.values():
                j = assigned.get(ball.id)
                if j is None:
                    self._miss_color(ball, now, dt)
                else:
                    self._accept_color(ball, cands[j], now)

        self._update_motion(now, cam, confirmed_obstacles, mapper)

    # -- candidate sources ------------------------------------------------ #
    def _depth_candidates(self, color_bgr, depth_mm, mapper, cam, plane, play_area):
        """Above-floor blobs, plus hue blobs so a ball resting in depth noise
        (thin, dark, or right at the plane) still has something to match."""
        dets = self._detect(color_bgr, depth_mm, mapper, cam, plane, play_area)
        if color_bgr is None:
            return dets
        hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
        return self._merge_candidates(
            dets, self._stationary_hue_candidates(hsv, mapper, play_area))

    def _color_candidates(self, color_bgr, mapper, play_area, reference_color):
        hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
        return self._merge_candidates(
            self._motion_candidates(color_bgr, hsv, mapper, play_area, reference_color),
            self._stationary_hue_candidates(hsv, mapper, play_area))

    def _detect(self, color_bgr, depth_mm, mapper, cam, plane, play_area=None):
        if depth_mm is None or plane is None or cam is None or not cam.fx:
            return []
        hmap = height_map(depth_mm, plane, cam)
        mask = above_floor_mask(hmap, min_height_m=0.005)
        # 5 mm is well inside a real sensor's noise, which speckles the whole
        # floor with stray pixels — thousands of candidate blobs per frame, every
        # frame. A ball is a solid disc about ten pixels across and survives.
        mask = denoise_mask(mask, open_px=3)
        regions = connected_regions(mask, min_area=3)
        hh, ww = depth_mm.shape[:2]
        out = []
        for r in regions:
            cx, cy = r["center"]
            iy = int(np.clip(cy, 0, hh - 1))
            ix = int(np.clip(cx, 0, ww - 1))
            z = float(depth_mm[iy, ix])
            if not np.isfinite(z) or z <= 200.0:
                continue
            expected_r = (self.BALL_DIAMETER_M / 2.0) * cam.fx / (z / 1000.0)
            # Area plausibility: within ~2.5x of the expected ball disc.
            exp_area = np.pi * expected_r * expected_r
            if r["area"] > exp_area * 4.0 or r["area"] < max(3, exp_area * 0.25):
                continue
            if color_bgr is not None:
                hue, sat, val = sample_hsv_blob(color_bgr, r["mask"], cy, cx)
            else:
                hue, sat, val = None, 0, 0
            fx, fy = mapper.depth_pixel_to_floor(cx, cy, z)
            if play_area and not _point_in_poly(float(fx), float(fy), play_area):
                continue
            out.append({
                "pos": (float(fx), float(fy)), "pixel": (float(cx), float(cy)),
                "hue": hue, "sat": sat, "val": val,
                "blurred": False, "aspect": 1.0, "src": "depth", "z": z,
            })
        return out

    def _motion_candidates(self, color_bgr, hsv, mapper, play_area, reference_color) -> list[dict]:
        fg = self._mog2.apply(color_bgr)
        motion = (fg == 255).astype(np.uint8) * 255
        appeared = _new_object_mask(color_bgr, reference_color, loose=False)
        combined = cv2.bitwise_or(motion, appeared)
        area_mask = self._play_area_mask(color_bgr.shape[:2], mapper, play_area)
        combined = cv2.bitwise_and(combined, area_mask)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        self.debug["motion_mask"] = combined
        return self._blobs_from_motion_mask(combined, hsv, color_bgr, mapper, play_area)

    def _play_area_mask(self, hw, mapper, play_area) -> np.ndarray:
        hh, ww = hw
        mask = np.zeros((hh, ww), np.uint8)
        if play_area:
            pts = []
            for x, y in play_area:
                u, v = mapper.floor_to_pixel(x, y)
                pts.append([int(round(u)), int(round(v))])
            if len(pts) >= 3:
                cv2.fillPoly(mask, [np.array(pts, np.int32)], 255)
            else:
                mask[:] = 255
        else:
            mask[:] = 255
        return mask

    def _blobs_from_motion_mask(self, mask, hsv, color_bgr, mapper, play_area) -> list[dict]:
        out = []
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            if len(c) < 5:
                continue
            area = float(cv2.contourArea(c))
            if area < 8:
                continue
            rect = cv2.minAreaRect(c)
            (cx, cy), (rw, rh), _ang = rect
            minor = float(min(rw, rh))
            major = float(max(rw, rh))
            if minor < 1.5:
                continue
            aspect = major / minor
            if aspect > 5.0:
                continue
            blurred = aspect >= 2.0
            # Streaked (blurred) blobs: use the mass centroid, not the box center.
            if blurred:
                mom = cv2.moments(c)
                if mom["m00"] > 1e-3:
                    cx = float(mom["m10"] / mom["m00"])
                    cy = float(mom["m01"] / mom["m00"])
            floor = mapper.pixel_to_floor(float(cx), float(cy))
            if floor is None:
                continue
            fx, fy = float(floor[0]), float(floor[1])
            if play_area and not _point_in_poly(fx, fy, play_area):
                continue
            exp_d = max(4.0, 2.0 * float(mapper.radius_to_pixels(fx, fy, self.BALL_DIAMETER_M / 2.0)))
            if minor < 0.5 * exp_d or minor > 2.5 * exp_d:
                continue
            blob_mask = np.zeros(mask.shape, np.uint8)
            cv2.drawContours(blob_mask, [c], -1, 255, -1)
            hue, sat, val = sample_hsv_blob(color_bgr, blob_mask, cy, cx)
            out.append({
                "pos": (fx, fy), "pixel": (float(cx), float(cy)),
                "hue": hue, "sat": sat, "val": val,
                "blurred": blurred, "aspect": aspect, "src": "motion",
            })
        return out

    def _stationary_hue_candidates(self, hsv, mapper, play_area) -> list[dict]:
        """Hue-mask circular blobs — precise when the ball is sitting still."""
        out = []
        for ball in self.balls.values():
            mask = self._ball_mask(ball, hsv)
            self.debug["masks"][ball.id] = mask
            for _score, pos, cx, cy in self._circular_blobs(
                ball, hsv, mapper, play_area, ranked=False, mask=mask,
            ):
                out.append({
                    "pos": pos, "pixel": (cx, cy),
                    "hue": ball.hue_center, "sat": ball.sat_floor or 0,
                    "val": ball.val_floor or 0,
                    "blurred": False, "aspect": 1.0, "src": "hue",
                    "hint": ball.id,
                })
        return out

    def _merge_candidates(self, motion: list[dict], hue: list[dict]) -> list[dict]:
        merged = list(motion)
        for h in hue:
            too_close = False
            for m in merged:
                if float(np.hypot(h["pos"][0] - m["pos"][0], h["pos"][1] - m["pos"][1])) < 0.06:
                    too_close = True
                    break
            if not too_close:
                merged.append(h)
        return merged

    def predict_gate_radius(self, ball: TrackedBall) -> float:
        if ball.held:
            return max(0.55, _GATE_MIN_M)
        if not ball.filter.ready:
            return 2.0
        r = 3.0 * ball.filter.pos_std()
        return float(np.clip(r, _GATE_MIN_M, _GATE_MAX_M))

    def _hue_identity(self, ball: TrackedBall, cand: dict) -> float:
        """Hue distance vs the sampled center. Blur drops the sat floor to 40%."""
        hue = cand.get("hue")
        sat = int(cand.get("sat") or 0)
        blurred = bool(cand.get("blurred"))
        # Depth already proved something ball-sized stands above the floor, so a
        # weak color read there downgrades the match instead of vetoing it.
        weak = 18.0 if cand.get("src") == "depth" else 180.0
        # sat_floor is 60% of the sampled S; blur desaturates so use 40%.
        sat_lim = int(ball.sat_floor) if ball.sat_floor is not None else 80
        if blurred:
            sat_lim = max(8, int(sat_lim * (0.4 / 0.6)))
        if ball.hue_center is None:
            return 0.0 if sat <= max(sat_lim, 40) else min(40.0, weak)
        if hue is None:
            return weak
        hd = self._hue_dist(hue, ball)
        if ball.sat_floor is not None and sat < sat_lim:
            return max(hd, weak)
        return hd

    def _pair_feasible(self, ball: TrackedBall, cand: dict) -> bool:
        pred = ball.filter.pos if ball.filter.ready else (ball.position or cand["pos"])
        gate = self.predict_gate_radius(ball)
        if ball.position is not None or ball.filter.ready:
            pd = float(np.hypot(cand["pos"][0] - pred[0], cand["pos"][1] - pred[1]))
            if pd > gate:
                return False
        hd = self._hue_identity(ball, cand)
        if hd > _HUE_INFEASIBLE:
            return False
        if cand.get("hint") and cand["hint"] != ball.id and hd > 12.0:
            return False
        return True

    def _pair_cost(self, ball: TrackedBall, cand: dict) -> float:
        md = ball.filter.mahalanobis(cand["pos"]) if ball.filter.ready else (
            float(np.hypot(cand["pos"][0] - (ball.position or cand["pos"])[0],
                           cand["pos"][1] - (ball.position or cand["pos"])[1]))
        )
        hd = self._hue_identity(ball, cand)
        hue_w = 0.5 if cand.get("blurred") else 1.0
        return md + hue_w * hd

    def _assign_global(self, cands: list[dict]) -> dict[str, int]:
        """Best assignment of balls to candidates. At most 6! = 720 tries."""
        balls = list(self.balls.values())
        n, m = len(balls), len(cands)
        costs = np.full((n, m), np.inf)
        for i, ball in enumerate(balls):
            for j, cand in enumerate(cands):
                if self._pair_feasible(ball, cand):
                    costs[i, j] = self._pair_cost(ball, cand)
                self.debug["costs"].append({
                    "ball_id": ball.id, "pos": cand["pos"],
                    "cost": None if not np.isfinite(costs[i, j]) else float(costs[i, j]),
                    "ok": bool(np.isfinite(costs[i, j])),
                })
        best_map: dict[int, int] = {}
        best_key = (-1, np.inf)

        def rec(i: int, used: set[int], acc: float, mapping: dict[int, int]) -> None:
            nonlocal best_map, best_key
            if i == n:
                # Prefer more assigned balls, then lower cost.
                cmp = (len(mapping), -acc)
                if cmp[0] > best_key[0] or (cmp[0] == best_key[0] and acc < best_key[1]):
                    best_key = (cmp[0], acc)
                    best_map = dict(mapping)
                return
            rec(i + 1, used, acc, mapping)
            for j in range(m):
                if j in used or not np.isfinite(costs[i, j]):
                    continue
                mapping[i] = j
                used.add(j)
                rec(i + 1, used, acc + float(costs[i, j]), mapping)
                used.remove(j)
                del mapping[i]

        if n and m:
            rec(0, set(), 0.0, {})
        return {balls[i].id: j for i, j in best_map.items()}

    def _accept_color(self, ball: TrackedBall, cand: dict, now: float) -> None:
        pos = cand["pos"]
        ball.filter.correct(pos)
        ball._speed = ball.filter.speed
        ball._heading = ball.filter.vel
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
        ball.coasting = False
        ball.blurred = bool(cand.get("blurred"))
        gate = self.predict_gate_radius(ball)
        self.debug["dets"].append({
            "ball_id": ball.id, "pos": pos, "ok": True, "gate": gate,
            "blurred": ball.blurred,
        })

    def _miss_color(self, ball: TrackedBall, now: float, dt: float) -> None:
        unseen = now - ball.last_seen_t if np.isfinite(ball.last_seen_t) else 1e9
        # Coast only while the filter still thinks the ball is rolling.
        # A miss at the end of a putt must not wipe the rest timer.
        still_rolling = ball.filter.speed > _START_SPEED
        if ball.moving and still_rolling and unseen <= _COAST_MAX_S and ball.filter.ready:
            ball.filter.decay_velocity(dt)
            pred = ball.filter.pos
            ball.position = pred
            ball.smoothed = pred
            ball._speed = ball.filter.speed
            ball._heading = ball.filter.vel
            ball.coasting = True
            ball.lost = False
            self.debug["dets"].append({
                "ball_id": ball.id, "pos": pred, "ok": True,
                "gate": self.predict_gate_radius(ball), "coast": True,
            })
            return
        ball.coasting = False
        self._coast_or_lose_one(ball, now)

    def _coast_or_lose(self, now: float, dt: float) -> None:
        for ball in self.balls.values():
            self._miss_color(ball, now, dt)

    def _coast_or_lose_one(self, ball: TrackedBall, now: float) -> None:
        unseen = now - ball.last_seen_t if np.isfinite(ball.last_seen_t) else 1e9
        if ball.held and ball.position is not None and unseen > 0.05:
            ball.lost = False
            return
        ball.lost = bool((unseen > 2.0) and not ball.hidden)

    def recolor(self, ball_id: str, hue_center=None, sat_floor=None, val_floor=None,
                hue_range=None, color_hex: str | None = None) -> None:
        """Re-teach a tracked ball its appearance after a manual correction.

        The user only has to click a missed ball because the stored color was
        wrong, so adopting what they pointed at is what stops the next miss.
        """
        ball = self.balls.get(ball_id)
        if ball is None:
            return
        ball.hue_center = float(hue_center) if hue_center is not None else None
        ball.hue_range = tuple(hue_range) if hue_range else None
        if sat_floor is not None:
            ball.sat_floor = int(sat_floor)
        if val_floor is not None:
            ball.val_floor = int(val_floor)
        if color_hex:
            ball.color = color_hex

    def find_near_pixel(self, ball_id: str, color_bgr, mapper, nx: float, ny: float,
                        play_area=None) -> tuple[float, float] | None:
        """Return the ball-like blob nearest a normalized click, or None.

        This ball's own hue mask is tried first, then any golf-ball color. The
        second pass matters because a wrong stored hue is the usual reason the
        user is clicking at all — without it the snap would ignore the ball
        that is plainly under the cursor.
        """
        ball = self.balls.get(ball_id)
        if ball is None or color_bgr is None or mapper is None:
            return None
        hh, ww = color_bgr.shape[:2]
        hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
        click = (float(nx) * ww, float(ny) * hh)
        limit = 0.14 * max(ww, hh)
        for mask in (None, _allowed_ball_mask(hsv, loose=True)):
            best, best_d = None, limit
            for _score, pos, cx, cy in self._circular_blobs(
                ball, hsv, mapper, play_area, ranked=False, mask=mask,
            ):
                d = float(np.hypot(cx - click[0], cy - click[1]))
                if d < best_d:
                    best_d = d
                    best = pos
            if best is not None:
                return best
        return None

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

    # -- motion / hidden / lost ------------------------------------------- #
    def _update_motion(self, now, cam, confirmed_obstacles, mapper) -> None:
        for ball in self.balls.values():
            if ball.coasting:
                # A coast never ends a stroke. Keep the ball marked moving.
                ball.moving = True
                ball._rest_since = None
                ball._rest_hits = 0
                ball._speed = ball.filter.speed if ball.filter.ready else ball._speed
            elif ball.filter.ready:
                spd = ball.filter.speed
                ball._speed = spd
                cutoff = now - 0.5
                recent = [(x, y) for (t, x, y) in ball.history if t >= cutoff]
                disp = 0.0
                if len(recent) >= 2:
                    x0, y0 = recent[0]
                    x1, y1 = recent[-1]
                    disp = float(np.hypot(x1 - x0, y1 - y0))
                seen_now = np.isfinite(ball.last_seen_t) and (now - ball.last_seen_t) < 1e-6
                if ball.moving:
                    # Sitting still (tiny 0.5 s travel) ends the stroke, even if
                    # leftover filter speed has not quite died yet.
                    if seen_now:
                        if disp < _STOP_DISP and spd < _STOP_SPEED:
                            if ball._rest_since is None:
                                ball._rest_since = now
                                ball._rest_hits = 1
                            else:
                                ball._rest_hits += 1
                            held = (now - ball._rest_since) >= _REST_HOLD_S
                            if held and ball._rest_hits >= _REST_HITS:
                                ball.moving = False
                                ball._rest_since = None
                                ball._rest_hits = 0
                                if ball.filter.ready:
                                    ball.filter.x[2] = 0.0
                                    ball.filter.x[3] = 0.0
                                    ball._speed = 0.0
                        else:
                            ball._rest_since = None
                            ball._rest_hits = 0
                else:
                    ball._rest_since = None
                    ball._rest_hits = 0
                    ball.moving = spd > _START_SPEED and disp >= _START_DISP
            else:
                ball.moving = False
                ball._rest_since = None
                ball._rest_hits = 0

            unseen = now - ball.last_seen_t
            if ball.coasting:
                continue
            if ball.position is not None and unseen > 0.3 and not ball.hidden:
                inter = self._heading_intersects(ball.position, ball._heading, confirmed_obstacles)
                if inter is not None:
                    ball.hidden = True
                    ball.hidden_estimate = inter
                    ball.moving = False
                    continue
            if ball.held and ball.position is not None and unseen > 0.05:
                ball.lost = False

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
        motion = self.debug.get("motion_mask")
        if motion is not None and getattr(motion, "shape", None) == (hh, ww):
            tint = np.zeros_like(img)
            tint[motion > 0] = (40, 200, 40)
            cv2.addWeighted(tint, 0.22, img, 1.0, 0, img)
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
            if det.get("coast"):
                color = (80, 200, 255)
            cv2.circle(img, pt, 10, color, 2 if ok else 1)
            label = "coast" if det.get("coast") else ("ok" if ok else "no")
            cv2.putText(img, label, (pt[0] + 8, pt[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        for c in self.debug.get("costs") or []:
            pos = c.get("pos")
            if pos is None or mapper is None or c.get("cost") is None:
                continue
            u, v = mapper.floor_to_pixel(pos[0], pos[1])
            cv2.putText(img, f"{c['ball_id']}:{c['cost']:.1f}",
                        (int(u) + 6, int(v) + 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (242, 239, 232), 1, cv2.LINE_AA)
        for ball in self.balls.values():
            pred = (self.debug.get("pred") or {}).get(ball.id)
            pos = pred or ball.smoothed or ball.position
            if pos is None or mapper is None:
                continue
            gate = float(self.debug.get("gate", {}).get(
                ball.id, self.predict_gate_radius(ball)))
            u, v = mapper.floor_to_pixel(pos[0], pos[1])
            pu, pv = int(round(u)), int(round(v))
            if pred is not None:
                cv2.drawMarker(img, (pu, pv), (255, 220, 80), cv2.MARKER_CROSS, 14, 2)
            r_px = max(6.0, float(mapper.radius_to_pixels(pos[0], pos[1], gate)))
            cv2.ellipse(img, (pu, pv), (int(r_px), int(r_px)), 0, 0, 360, (242, 239, 232), 1)
            tag = f"{ball.id} r={gate:.2f}m"
            if ball.coasting:
                tag += " coast"
            cv2.putText(img, tag, (pu + 8, pv + 16),
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


def classify_ball_swatch(bgr: tuple[int, int, int], loose: bool = False,
                         v_ref: float | None = None) -> dict | None:
    """Return a player swatch only for white or neon ball colors.

    Carpet / wood / shadows return None. ``loose`` is for a deliberate click.
    ``v_ref`` is the scene's median brightness; pass it whenever the frame is at
    hand so a dim room does not disqualify a genuinely white ball.
    """
    patch = np.uint8([[bgr]])
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[0][0]
    h, s, v = int(hsv[0]), int(hsv[1]), int(hsv[2])
    name = _name_allowed_ball(h, s, v, loose=loose, v_ref=v_ref)
    if name is None:
        return None
    spec = _BALL_SWATCHES[name]
    return {
        "color": spec["color"],
        "hue_name": name,
        "hue_range": spec["hue_range"],
        "rejected": False,
    }


def _name_allowed_ball(h: int, s: int, v: int, loose: bool = False,
                       v_ref: float | None = None) -> str | None:
    s_white = 80 if loose else 28
    v_white = 155 if loose else 210
    s_neon = 70 if loose else 170
    v_neon = 110 if loose else 160
    if v_ref:
        # Scale the "bright enough to be white" bar to the room. A white ball
        # under a locked indoor exposure measured V=85, so the fixed bar called
        # it carpet and the player could not even assign it by clicking.
        # This is only a coarse pre-filter: the low-saturation gate below and
        # the local pop-from-floor test are what actually rule out carpet, so
        # brightness parity with the room is enough to stay in the running.
        v_white = min(float(v_white), max(55.0, float(v_ref) * (0.92 if loose else 1.0)))
        v_neon = min(float(v_neon), max(50.0, float(v_ref) * 0.9))
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


def _allowed_ball_mask(hsv: np.ndarray, loose: bool = False) -> np.ndarray:
    """Hue window only. Oak lives in the orange band — never use this alone."""
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # A white ball is only "bright" relative to the room it is in. On a locked
    # indoor exposure a real white ball measured V=82 against a V=72 rug, so an
    # absolute v>=150 erased 97% of it and the blob never reached the size test.
    # What actually separates it is being *less saturated* than the floor.
    s_med = float(np.median(s))
    v_med = float(np.median(v))
    if loose:
        white = (s <= max(40.0, s_med * 0.62)) & (v >= min(150.0, v_med * 0.85))
        sat = (s >= 55) & (v >= 100)
    else:
        white = (s <= max(36.0, s_med * 0.5)) & (v >= min(190.0, v_med * 1.05))
        sat = (s >= 70) & (v >= 120)
    orange = sat & (h >= 8) & (h <= 24)
    yellow = sat & (h >= 22) & (h <= 38)
    pink = sat & ((h >= 150) | (h <= 8))
    blue = sat & (h >= 95) & (h <= 128)
    return (white | orange | yellow | pink | blue).astype(np.uint8) * 255


def _new_object_mask(color_bgr, reference_bgr, loose: bool = False,
                     r_px: float | None = None) -> np.ndarray:
    """Pixels that changed versus the empty floor, ignoring a global exposure shift."""
    hh, ww = color_bgr.shape[:2]
    if reference_bgr is None or reference_bgr.shape != color_bgr.shape:
        hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
        # With no empty-floor picture the background is estimated by blurring
        # the frame, so the kernel has to span several ball widths. A fixed
        # 31 px is narrower than a golf ball at 1080p, which made the ball its
        # own background and left only its rim standing out.
        span = r_px * 6.0 if r_px else min(hh, ww) / 12.0
        k = int(max(31, min(201, round(span)))) | 1
        blur = cv2.GaussianBlur(hsv, (k, k), 0)
        gap = 16 if loose else 26
        sat = hsv[..., 1].astype(np.int16)
        val = hsv[..., 2].astype(np.int16)
        b_sat = blur[..., 1].astype(np.int16)
        b_val = blur[..., 2].astype(np.int16)
        # A white ball on a colored floor is a saturation *drop*, not a rise.
        pop = ((sat > b_sat + gap) | (val > b_val + gap) | (sat < b_sat - gap))
        return pop.astype(np.uint8) * 255
    diff = cv2.absdiff(color_bgr, reference_bgr)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    # A room-light change lifts every pixel; require a bump above the frame median.
    med = float(np.median(gray))
    gap = 8 if loose else 14
    changed = gray > med + gap
    # Brightness alone is weak for a white ball on a warm rug: the two sit at
    # nearly the same luminance, so the ball scored 17 against a threshold of
    # 14. Their saturation differs by 56, so check that too. Saturation is also
    # steadier than brightness when the room lights change.
    now_s = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)[..., 1].astype(np.int16)
    ref_s = cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2HSV)[..., 1].astype(np.int16)
    d_sat = np.abs(now_s - ref_s)
    changed |= d_sat > float(np.median(d_sat)) + gap * 2
    return changed.astype(np.uint8) * 255


def _blob_pops_from_floor(hsv, blob_mask, cy: float, cx: float, r_px: float) -> bool:
    """True when the blob is neon-er or brighter than the ring around it."""
    hh, ww = blob_mask.shape
    inner = blob_mask > 0
    if int(inner.sum()) < 8:
        return False
    yy, xx = np.ogrid[:hh, :ww]
    dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    ring = (dist > r_px * 1.15) & (dist < r_px * 2.6) & (~inner)
    if int(ring.sum()) < 12:
        return True
    s_in = float(np.median(hsv[..., 1][inner]))
    v_in = float(np.median(hsv[..., 2][inner]))
    s_out = float(np.median(hsv[..., 1][ring]))
    v_out = float(np.median(hsv[..., 2][ring]))
    sat_delta = s_in - s_out
    val_delta = v_in - v_out
    # Neon pops by saturation; a white ball pops by being brighter / less sat.
    if sat_delta >= 32 or val_delta >= 28 or (s_in <= 55 and val_delta >= 16):
        return True
    # A white ball indoors is often no brighter than a warm rug — measured V 85
    # against 76 — but it is far less saturated: S 25 against 78. That drop is
    # the strongest signal available, so treat it as a pop. Bare floor and a
    # pale border both sit near zero, and requiring the blob not to be darker
    # rules out the shadow beside the ball, which also desaturates.
    return sat_delta <= -30 and val_delta >= -8


def _blob_is_new(color_bgr, reference_bgr, blob_mask) -> bool:
    """Reject blobs that already existed on the empty-floor reference."""
    if reference_bgr is None or reference_bgr.shape != color_bgr.shape:
        return True
    inner = blob_mask > 0
    if not inner.any():
        return False
    diff = cv2.absdiff(color_bgr, reference_bgr)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    if float(np.median(gray[inner])) >= 20:
        return True
    # A white ball placed on a warm rug changes the brightness there by only 8,
    # so brightness alone called it "was already here" and dropped it. What it
    # really changes is the saturation.
    now_s = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)[..., 1].astype(np.int16)
    ref_s = cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2HSV)[..., 1].astype(np.int16)
    return float(np.median(np.abs(now_s - ref_s)[inner])) >= 25


def detect_setup_balls(
    color_bgr,
    reference_bgr,
    mapper: FloorMapper,
    play_area: list[tuple[float, float]] | None,
    cup: tuple[float, float, float] | None = None,
    loose: bool = False,
) -> list[dict]:
    """Find white / neon golf balls on a color frame for the S09 assign step.

    A blob must look like a ball *and* stand off the floor (and the empty
    reference, when we have one). Hue alone matches oak grain.
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

    if play_area:
        cx = sum(p[0] for p in play_area) / len(play_area)
        cy = sum(p[1] for p in play_area) / len(play_area)
    else:
        cx = cy = 0.0
    r_px = max(4.0, float(mapper.radius_to_pixels(cx, cy, 0.0215)))

    hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
    v_ref = float(np.median(hsv[..., 2]))
    color_ok = _allowed_ball_mask(hsv, loose=loose)
    appeared = _new_object_mask(color_bgr, reference_bgr, loose=loose, r_px=r_px)
    mask = cv2.bitwise_and(cv2.bitwise_and(color_ok, appeared), area_mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    min_a = max(18.0, np.pi * (r_px * 0.4) ** 2)
    max_a = np.pi * (r_px * 2.4) ** 2
    # A real ball on carpet has a ragged outline, which costs a lot of contour
    # circularity: a live white ball measured 0.60 and was thrown away by 0.62.
    # The size checks below carry the weight instead.
    min_circ = 0.50 if loose else 0.55

    out: list[dict] = []
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        area = float(cv2.contourArea(c))
        if area < min_a or area > max_a:
            continue
        peri = float(cv2.arcLength(c, True))
        if peri < 8:
            continue
        # Ball-sized, not just ball-area-sized: a sprawling streak can hit the
        # area window while spanning four ball widths.
        _center, encl_r = cv2.minEnclosingCircle(c)
        encl_r = float(encl_r)
        if not 0.55 * r_px <= encl_r <= 1.95 * r_px:
            continue
        circ = 4.0 * np.pi * area / (peri * peri)
        # Contour circularity is dominated by perimeter noise, and a ball on
        # carpet has a very noisy edge. When the blob spans exactly one ball
        # width, that size agreement is the better evidence, so allow a rougher
        # outline — a live white ball scored 0.52 with an enclosing radius
        # within 1% of the expected one.
        exact = 0.75 * r_px <= encl_r <= 1.35 * r_px
        if circ < (min_circ - 0.10 if exact else min_circ):
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
        blob_mask = np.zeros((hh, ww), np.uint8)
        cv2.drawContours(blob_mask, [c], -1, 255, -1)
        if not _blob_pops_from_floor(hsv, blob_mask, py, px, max(r_px, (area / np.pi) ** 0.5)):
            continue
        if not loose and not _blob_is_new(color_bgr, reference_bgr, blob_mask):
            continue
        inner = blob_mask > 0
        med = np.median(color_bgr[inner], axis=0)
        bgr = tuple(int(x) for x in med)
        swatch = classify_ball_swatch(bgr, loose=loose, v_ref=v_ref)
        if swatch is None:
            continue
        if not loose and swatch.get("hue_name") == "orange":
            # Wood grain is streaky; a neon ball is a flat color chip.
            gray = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2GRAY)
            if float(np.std(gray[inner])) > 32:
                continue
        sampled = attach_sampled_hsv(color_bgr, blob_mask, py, px, swatch)
        out.append({"pos": (float(floor[0]), float(floor[1])),
                    "pixel": (float(px), float(py)), **sampled})
    return out


def nearest_setup_ball(dets: list[dict], mapper, u: float, v: float,
                       max_px: float = 56.0) -> dict | None:
    """Detection whose centroid is closest to a click, or None if too far."""
    best, best_d = None, float(max_px)
    for det in dets:
        pos = det.get("pos")
        pix = det.get("pixel")
        if pix is not None:
            pu, pv = float(pix[0]), float(pix[1])
        elif pos is not None and mapper is not None:
            pu, pv = mapper.floor_to_pixel(pos[0], pos[1])
        else:
            continue
        d = float(np.hypot(pu - u, pv - v))
        if d < best_d:
            best, best_d = det, d
    return best


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
    # The floors are what the tracker matches against every frame, so they have
    # to stay below what this ball actually measured. Absolute guards of 160 /
    # 80 sat above a dim-room white ball (V=85) and lost it on the next frame,
    # right after the player had corrected it by hand.
    if name == "white" or hue is None:
        out["hue_center"] = None
        out["hue_range"] = None
        out["sat_floor"] = max(8, int(sat * 0.6))
        out["val_floor"] = min(160, max(50, int(val * 0.8)))
        return out
    out["hue_center"] = float(hue)
    out["hue_range"] = list(hue_window(hue, 10.0))
    out["sat_floor"] = max(40, int(sat * 0.6))
    out["val_floor"] = min(80, max(40, int(val * 0.6)))
    return out


def sample_ball_at_pixel(color_bgr, u: float, v: float,
                         r_px: float | None = None) -> dict | None:
    """Classify + sample a clicked neighborhood (S09 manual assign).

    A single pixel is the wrong test: a highlight, a miss by a few pixels, or
    warm floor spill all fail. Use the median of a disk, and only accept it
    if that disk stands off the floor the way a ball does.

    ``r_px`` is the expected ball radius in pixels. Pass it whenever a mapper is
    available: a fixed disk that is smaller than the ball puts the comparison
    ring *on the ball*, so a click straight onto a real ball was rejected for
    not standing out from itself.
    """
    if color_bgr is None:
        return None
    hh, ww = color_bgr.shape[:2]
    ix = int(np.clip(u, 0, ww - 1))
    iy = int(np.clip(v, 0, hh - 1))
    rad = float(r_px) if r_px else min(hh, ww) / 34.0
    rad = float(np.clip(rad, 5.0, 60.0))
    mask = np.zeros((hh, ww), np.uint8)
    cv2.circle(mask, (ix, iy), int(round(rad)), 255, -1)
    inner = mask > 0
    if int(inner.sum()) < 8:
        return None
    hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
    if not _blob_pops_from_floor(hsv, mask, float(iy), float(ix), rad):
        return None
    med = np.median(color_bgr[inner], axis=0)
    bgr = tuple(int(x) for x in med)
    swatch = classify_ball_swatch(bgr, loose=True,
                                  v_ref=float(np.median(hsv[..., 2])))
    if swatch is None:
        return None
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
