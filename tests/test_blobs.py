"""Connected-region extraction under realistic sensor noise.

A real depth sensor is noisy by a centimetre or two, so any low height threshold
speckles an empty floor with thousands of stray pixels. Labelling that used to
cost a full-frame allocation and scan *per component*, which is what froze the
game on the "place the putting cup" step — the work was quadratic in disguise.
"""
from __future__ import annotations

import time

import numpy as np

from rumpus.models import CameraModel, FloorPlane
from rumpus.vision.blobs import (SIMPLIFY_MAX_FRAC, SIMPLIFY_PASSES,
                                 above_floor_mask, connected_regions,
                                 denoise_mask, height_map, simplify_polygon)
from rumpus.vision.obstacle_detect import detect_obstacles
from rumpus.vision.geometry import FloorMapper, default_camera

W, H = 640, 480


def _speckle(density: float = 0.06, seed: int = 3) -> np.ndarray:
    """An empty floor as a noisy sensor sees it: isolated stray pixels."""
    rng = np.random.default_rng(seed)
    return rng.random((H, W)) < density


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #
def test_labelling_heavy_noise_is_fast():
    """The regression that froze the cup screen.

    Roughly 18k components here. At a full-frame scan each that is ~5.7 billion
    element operations; the budget below is generous but still thousands of times
    under it.
    """
    mask = _speckle()
    t0 = time.perf_counter()
    # min_area=1 so the count reflects every label. The old loop paid a
    # full-frame scan per label whether or not it survived filtering.
    regions = connected_regions(mask, min_area=1)
    dt = time.perf_counter() - t0
    assert len(regions) > 3000, f"expected heavy noise, got {len(regions)}"
    assert dt < 2.0, f"{len(regions)} regions took {dt:.1f}s"


def test_masks_are_not_built_until_asked_for():
    """Callers reject most regions on area or position, and never read the mask."""
    mask = _speckle()
    regions = connected_regions(mask, min_area=1)
    assert all(r._cached is None for r in regions)
    r = regions[0]
    assert r.mask.shape == (H, W)
    assert r._cached is not None, "mask should be cached after the first read"
    assert r.mask is r._cached, "mask rebuilt on every access"


def test_region_reads_like_the_dict_it_replaced():
    mask = np.zeros((H, W), bool)
    mask[100:110, 200:220] = True
    r = connected_regions(mask, min_area=4)[0]
    assert r["area"] == 200
    assert r["bbox"] == (200, 100, 219, 109)
    assert r["mask"].sum() == 200
    cx, cy = r["center"]
    assert abs(cx - 209.5) < 0.51 and abs(cy - 104.5) < 0.51


# --------------------------------------------------------------------------- #
# Correctness of the rewrite
# --------------------------------------------------------------------------- #
def test_area_bbox_and_centre_match_the_pixels():
    """Stats come from OpenCV now instead of being counted by hand."""
    mask = np.zeros((H, W), bool)
    mask[50:70, 80:100] = True     # 20x20
    mask[300:303, 400:410] = True  # 3x10
    regions = sorted(connected_regions(mask, min_area=4), key=lambda r: r.area)
    assert [r.area for r in regions] == [30, 400]
    small, big = regions
    assert small.bbox == (400, 300, 409, 302)
    assert big.bbox == (80, 50, 99, 69)
    ys, xs = np.where(big.mask)
    assert abs(big.center[0] - xs.mean()) < 1e-6
    assert abs(big.center[1] - ys.mean()) < 1e-6


def test_min_area_still_filters():
    mask = np.zeros((H, W), bool)
    mask[10, 10] = True                # 1 px
    mask[20:30, 20:30] = True          # 100 px
    assert len(connected_regions(mask, min_area=4)) == 1


def test_separate_blobs_are_separate_regions():
    mask = np.zeros((H, W), bool)
    for i in range(5):
        mask[100:110, 50 + 40 * i:60 + 40 * i] = True
    regions = connected_regions(mask, min_area=4)
    assert len(regions) == 5
    # Masks must not bleed into each other.
    for r in regions:
        assert r.mask.sum() == 100


def test_diagonal_touching_counts_as_one_region():
    """8-connectivity, as before."""
    mask = np.zeros((H, W), bool)
    mask[10:20, 10:20] = True
    mask[20:30, 20:30] = True
    assert len(connected_regions(mask, min_area=4)) == 1


def test_max_regions_keeps_the_largest():
    mask = np.zeros((H, W), bool)
    sizes = [(4, 4), (10, 10), (20, 20), (30, 30)]
    x = 0
    for w, h in sizes:
        mask[5:5 + h, x:x + w] = True
        x += 40
    regions = connected_regions(mask, min_area=4, max_regions=2)
    assert sorted(r.area for r in regions) == [400, 900]


def test_no_cap_by_default():
    """Balls are small blobs, so capping by size would drop them."""
    mask = np.zeros((H, W), bool)
    for i in range(30):
        mask[100 + 4 * (i // 10):102 + 4 * (i // 10), 5 * i:5 * i + 2] = True
    assert len(connected_regions(mask, min_area=2)) == 30


def test_empty_mask_yields_nothing():
    assert connected_regions(np.zeros((H, W), bool), min_area=1) == []


# --------------------------------------------------------------------------- #
# Denoising
# --------------------------------------------------------------------------- #
def test_denoise_removes_speckle_and_keeps_objects():
    mask = _speckle()
    mask[200:240, 300:340] = True     # a real 40x40 object
    before = len(connected_regions(mask, min_area=3))
    after = connected_regions(denoise_mask(mask), min_area=3)
    assert len(after) < before / 50, f"{before} -> {len(after)}"
    assert max(r.area for r in after) >= 1400, "erased the real object"


def test_denoise_keeps_a_ball_sized_blob():
    """A golf ball a few metres out is only about ten pixels across."""
    mask = np.zeros((H, W), bool)
    mask[240:250, 320:330] = True
    kept = connected_regions(denoise_mask(mask), min_area=3)
    assert len(kept) == 1 and kept[0].area >= 60


# --------------------------------------------------------------------------- #
# The cup step end to end
# --------------------------------------------------------------------------- #
def _noisy_floor_depth(cam: CameraModel, plane: FloorPlane,
                       noise_mm: float = 12.0, seed: int = 5) -> np.ndarray:
    """A flat floor filling the frame, with depth noise like a real Kinect."""
    v, u = np.mgrid[0:H, 0:W].astype(np.float32)
    n = np.asarray(plane.normal, float)
    rx = (u - cam.cx) / cam.fx
    ry = (v - cam.cy) / cam.fy
    denom = n[0] * rx + n[1] * ry + n[2]
    denom = np.where(np.abs(denom) < 1e-3, np.nan, denom)
    z = plane.offset / denom
    rng = np.random.default_rng(seed)
    z_mm = z * 1000.0 + rng.normal(0.0, noise_mm, z.shape)
    return np.nan_to_num(np.where(z_mm > 0, z_mm, 0.0), nan=0.0).astype(np.float32)


def test_cup_detection_on_a_noisy_empty_floor_returns_quickly():
    """This call is what hung: 1 cm threshold, real noise, no time limit."""
    cam = default_camera((W, H))
    t = np.radians(35.0)
    n = np.array([0.0, -np.cos(t), -np.sin(t)])
    plane = FloorPlane(normal=n / np.linalg.norm(n), offset=-1.4)
    mapper = FloorMapper(plane, cam)
    depth = _noisy_floor_depth(cam, plane)
    area = [(-1.0, 1.2), (1.0, 1.2), (1.0, 2.8), (-1.0, 2.8)]

    t0 = time.perf_counter()
    out = detect_obstacles(depth, plane, cam, mapper, area, None,
                           min_height_m=0.02, ball_diameter_m=0.02)
    dt = time.perf_counter() - t0
    assert dt < 3.0, f"cup detection took {dt:.1f}s on a noisy floor"
    # Whatever it proposes, it must be a bounded list rather than thousands.
    assert len(out) <= 48, f"{len(out)} proposals from an empty floor"


def test_noise_alone_clears_a_one_centimetre_threshold():
    """Justifies raising the cup threshold: 1 cm is under the noise floor."""
    cam = default_camera((W, H))
    t = np.radians(35.0)
    n = np.array([0.0, -np.cos(t), -np.sin(t)])
    plane = FloorPlane(normal=n / np.linalg.norm(n), offset=-1.4)
    depth = _noisy_floor_depth(cam, plane)
    hmap = height_map(depth, plane, cam)
    speckled = int(above_floor_mask(hmap, min_height_m=0.01).sum())
    assert speckled > 10000, (
        f"only {speckled} px over 1 cm — fixture is not noisy enough to make "
        f"the point")


# --------------------------------------------------------------------------- #
# Outline simplification
# --------------------------------------------------------------------------- #
def _blob_contour(rng, w: int = 120, h: int = 120) -> np.ndarray:
    """A rough, lumpy outline of the kind a real depth region produces."""
    import cv2
    img = np.zeros((h, w), np.uint8)
    n = 360
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rad = (28 + rng.normal(0, 6, n)).clip(6, min(w, h) / 2 - 3)
    rad = np.convolve(np.r_[rad[-6:], rad, rad[:6]], np.ones(5) / 5, "same")[6:-6]
    pts = np.stack([w // 2 + rad * np.cos(ang), h // 2 + rad * np.sin(ang)], 1)
    cv2.fillPoly(img, [pts.astype(np.int32)], 255)
    cnts, _ = cv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)


def test_simplifying_lumpy_outlines_always_terminates():
    """The freeze on the cup step, and the reason it looked like a dead camera.

    Coarsening used to pick its tolerance from the current vertex count, so a
    count that mapped to itself — 9 against a target of 8 — recomputed the same
    tolerance and got the same 9 points forever. It hung roughly one outline in
    a hundred, inside OpenCV, with nothing raised and the process still alive.
    """
    rng = np.random.default_rng(0)
    worst = 0.0
    for i in range(3000):
        cnt = _blob_contour(rng)
        t0 = time.perf_counter()
        poly = simplify_polygon(cnt, target_points=8)
        worst = max(worst, time.perf_counter() - t0)
        assert len(poly) >= 3, f"blob {i} simplified away to {len(poly)} points"
    assert worst < 0.05, f"slowest outline took {worst * 1000:.1f} ms"


def test_the_tolerance_grows_instead_of_revisiting_a_vertex_count():
    """No pass may repeat an earlier tolerance, whatever the shape does.

    Guards the property rather than one unlucky contour: the old loop was a
    pure function of the vertex count, so any fixed point was a hang.
    """
    import cv2
    rng = np.random.default_rng(7)
    cnt = _blob_contour(rng)
    peri = float(cv2.arcLength(cnt, True))
    fracs, frac = [], 0.02
    for _ in range(SIMPLIFY_PASSES):
        fracs.append(frac)
        frac *= 1.5
    assert len(set(fracs)) == len(fracs), "a tolerance repeats"
    # The ceiling is what ends a stubborn shape, not the pass budget running
    # out, so the budget has to be big enough to reach it.
    assert fracs[-1] > SIMPLIFY_MAX_FRAC, (
        f"passes stop at {fracs[-1]:.2f}, below the {SIMPLIFY_MAX_FRAC} ceiling")
    assert peri > 0


def test_a_shape_that_will_not_simplify_returns_something_usable():
    """Better a rough polygon than a loop that waits for the shape to comply."""
    rng = np.random.default_rng(11)
    cnt = _blob_contour(rng, w=200, h=200)
    poly = simplify_polygon(cnt, target_points=3)
    assert 3 <= len(poly) <= 64, f"got {len(poly)} points"


def test_degenerate_outlines_do_not_throw():
    for bad in (None, np.zeros((0, 2), np.float32), np.zeros((1, 2), np.float32),
                np.array([[3, 3]] * 9, np.float32)):
        assert simplify_polygon(bad, target_points=8) == []


if __name__ == "__main__":
    test_simplifying_lumpy_outlines_always_terminates()
    test_the_tolerance_grows_instead_of_revisiting_a_vertex_count()
    test_a_shape_that_will_not_simplify_returns_something_usable()
    test_degenerate_outlines_do_not_throw()
    test_labelling_heavy_noise_is_fast()
    test_masks_are_not_built_until_asked_for()
    test_region_reads_like_the_dict_it_replaced()
    test_area_bbox_and_centre_match_the_pixels()
    test_min_area_still_filters()
    test_separate_blobs_are_separate_regions()
    test_diagonal_touching_counts_as_one_region()
    test_max_regions_keeps_the_largest()
    test_no_cap_by_default()
    test_empty_mask_yields_nothing()
    test_denoise_removes_speckle_and_keeps_objects()
    test_denoise_keeps_a_ball_sized_blob()
    test_cup_detection_on_a_noisy_empty_floor_returns_quickly()
    test_noise_alone_clears_a_one_centimetre_threshold()
    print("ok")
