"""Tests for backend.ml.mask_utils.detection_crop_rect (detection-driven crop math)."""

import pytest

from backend.ml.mask_utils import detection_crop_rect


def test_union_covers_disjoint_boxes():
    rect = detection_crop_rect(
        [[0.1, 0.1, 0.2, 0.2], [0.7, 0.6, 0.9, 0.8]], 1000, 1000, mode="union"
    )
    assert rect == (100, 100, 800, 700)


def test_largest_picks_biggest_area():
    rect = detection_crop_rect(
        [[0.1, 0.1, 0.2, 0.2], [0.5, 0.5, 0.9, 0.9]], 1000, 1000, mode="largest"
    )
    assert rect == (500, 500, 400, 400)


def test_padding_expands_each_side():
    # 200x200 box at (400, 400); 10% padding adds 20px per side
    rect = detection_crop_rect([[0.4, 0.4, 0.6, 0.6]], 1000, 1000, padding_pct=10.0)
    assert rect == (380, 380, 240, 240)


def test_padding_clamps_at_image_corner():
    # Box touching the top-left corner: padding cannot go negative
    rect = detection_crop_rect([[0.0, 0.0, 0.2, 0.2]], 1000, 1000, padding_pct=50.0)
    assert rect == (0, 0, 300, 300)


def test_ar_snap_grows_wider():
    # Tall box 100x400 → 16:9 needs width 400*16/9 ≈ 711
    rect = detection_crop_rect([[0.45, 0.3, 0.55, 0.7]], 1000, 1000, target_ar=16 / 9)
    assert rect is not None
    x, y, w, h = rect
    assert h == 400
    assert w == round(400 * 16 / 9)
    # Subject box (450..550, 300..700) fully contained
    assert x <= 450 and x + w >= 550
    assert y <= 300 and y + h >= 700


def test_ar_snap_grows_taller():
    # Wide box 400x100 → 9:16 grows height
    rect = detection_crop_rect([[0.3, 0.45, 0.7, 0.55]], 1000, 1000, target_ar=9 / 16)
    assert rect is not None
    x, y, w, h = rect
    assert w == 400
    assert h == round(400 * 16 / 9)
    assert y <= 450 and y + h >= 550


def test_ar_snap_secondary_axis_growth():
    # 400x800 image, subject 400x300 (full width), target 1:1.
    # Primary growth (width) clamps at the image edge, but height can still
    # legally grow to 400 → exact 1:1 preserved.
    rect = detection_crop_rect([[0.0, 0.25, 1.0, 0.625]], 400, 800, target_ar=1.0)
    assert rect is not None
    x, y, w, h = rect
    assert (w, h) == (400, 400)
    assert y <= 200 and y + h >= 500


def test_ar_best_effort_when_subject_cannot_fit():
    # Subject 380x100 in a 400x110 image at target 1:1: no legal square
    # contains the subject, so AR deviates but the subject stays inside.
    rect = detection_crop_rect([[0.025, 0.0455, 0.975, 0.9545]], 400, 110, target_ar=1.0)
    assert rect is not None
    x, y, w, h = rect
    assert w >= 380 and h >= 100
    assert w <= 400 and h <= 110
    assert w != h  # exact AR unreachable


def test_shift_clamp_preserves_size():
    # Box near the right edge; AR growth would overflow → rect slides left
    rect = detection_crop_rect([[0.8, 0.4, 0.95, 0.6]], 1000, 1000, target_ar=16 / 9)
    assert rect is not None
    x, y, w, h = rect
    assert x + w <= 1000
    assert h == 200
    assert w == round(200 * 16 / 9)
    assert x <= 800 and x + w >= 950  # subject contained


def test_empty_and_malformed_inputs():
    assert detection_crop_rect([], 100, 100) is None
    assert detection_crop_rect([[0.1, 0.2], [None], ["a", "b", "c", "d"]], 100, 100) is None


def test_zero_area_bbox_returns_none():
    assert detection_crop_rect([[0.5, 0.5, 0.5, 0.5]], 1000, 1000) is None


def test_swapped_coordinates_normalized():
    rect = detection_crop_rect([[0.6, 0.6, 0.4, 0.4]], 1000, 1000)
    assert rect == (400, 400, 200, 200)


def test_full_image_bbox_returns_full_rect():
    rect = detection_crop_rect([[0.0, 0.0, 1.0, 1.0]], 640, 480)
    assert rect == (0, 0, 640, 480)


def test_out_of_range_coords_clamped():
    rect = detection_crop_rect([[-0.5, -0.5, 1.5, 0.5]], 1000, 1000)
    assert rect == (0, 0, 1000, 500)
