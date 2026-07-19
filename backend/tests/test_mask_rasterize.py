"""Tests for backend.ml.mask_utils.rasterize_detections (mask export rasterizer)."""

import json

import numpy as np
import pytest

from backend.ml.mask_utils import compose_loss_mask, rasterize_detections


def _poly_json(polygons):
    return json.dumps({"polygons": polygons})


def _arr(img):
    return np.asarray(img)


def test_polygon_fills_expected_region():
    # Square covering the left half of a 100x100 canvas
    mask_json = _poly_json([[[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]])
    img = rasterize_detections([(mask_json, [0.0, 0.0, 0.5, 1.0])], 100, 100)
    assert img.mode == "L"
    assert img.size == (100, 100)
    a = _arr(img)
    assert a[50, 10] == 255   # inside
    assert a[50, 90] == 0     # outside
    # Interior of each half is uniform
    assert (a[:, :49] == 255).all()
    assert (a[:, 52:] == 0).all()


def test_bbox_fallback_fills_rectangle():
    # No mask JSON (Florence-2 / NudeNet style row) → bbox rectangle
    img = rasterize_detections([(None, [0.25, 0.25, 0.75, 0.75])], 100, 100)
    a = _arr(img)
    assert a[50, 50] == 255
    assert a[10, 10] == 0
    assert a[90, 90] == 0


def test_union_of_overlapping_detections():
    left = _poly_json([[[0.0, 0.0], [0.6, 0.0], [0.6, 1.0], [0.0, 1.0]]])
    img = rasterize_detections(
        [(left, None), (None, [0.4, 0.0, 1.0, 1.0])], 100, 100
    )
    a = _arr(img)
    assert (a == 255).all()


def test_invert_flips_values():
    mask_json = _poly_json([[[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]])
    normal = _arr(rasterize_detections([(mask_json, None)], 100, 100))
    inverted = _arr(rasterize_detections([(mask_json, None)], 100, 100, invert=True))
    assert ((normal.astype(int) + inverted.astype(int)) == 255).all()


def test_empty_detections_yield_black_canvas():
    a = _arr(rasterize_detections([], 64, 32))
    assert a.shape == (32, 64)
    assert (a == 0).all()


@pytest.mark.parametrize(
    "mask_json",
    [
        "not json",
        json.dumps(["wrong shape"]),
        _poly_json([[[0.1, 0.1], [0.2, 0.2]]]),  # degenerate: < 3 vertices
    ],
)
def test_bad_mask_json_falls_back_to_bbox(mask_json):
    img = rasterize_detections([(mask_json, [0.0, 0.0, 1.0, 1.0])], 10, 10)
    a = _arr(img)
    assert (a == 255).all()


# ---- compose_loss_mask (include minus exclude) ----

_LEFT_HALF = _poly_json([[[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]])


def test_compose_exclude_punches_hole():
    # Include the left half; exclude a bbox inside it → hole 0, rest of half 255.
    include = [(_LEFT_HALF, None)]
    exclude = [(None, [0.1, 0.1, 0.3, 0.3])]
    a = _arr(compose_loss_mask(include, exclude, 100, 100))
    assert a[20, 20] == 0     # inside the excluded hole
    assert a[80, 10] == 255   # left half, outside the hole
    assert a[50, 90] == 0     # right half, never included


def test_compose_exclude_applies_after_invert():
    # invert=True makes the right half white; exclude a bbox there → 0 (ordering pin).
    include = [(_LEFT_HALF, None)]
    exclude = [(None, [0.7, 0.7, 0.9, 0.9])]
    a = _arr(compose_loss_mask(include, exclude, 100, 100, invert=True))
    assert a[50, 10] == 0     # left half: inverted to black
    assert a[80, 80] == 0     # excluded region punched out of inverted-white bg
    assert a[20, 90] == 255   # right half elsewhere: inverted to white


def test_compose_no_include_full_white_minus_exclude():
    exclude = [(None, [0.25, 0.25, 0.75, 0.75])]
    a = _arr(compose_loss_mask([], exclude, 100, 100))
    assert a[50, 50] == 0     # punched out of the white fallback
    assert a[10, 10] == 255   # white elsewhere


def test_compose_no_include_no_exclude_full_white_even_inverted():
    a = _arr(compose_loss_mask([], [], 64, 32, invert=True))
    assert a.shape == (32, 64)
    assert (a == 255).all()


def test_compose_identical_region_in_both_is_black():
    include = [(_LEFT_HALF, None)]
    exclude = [(_LEFT_HALF, None)]
    a = _arr(compose_loss_mask(include, exclude, 100, 100))
    assert (a == 0).all()


def test_compose_no_exclude_matches_rasterize():
    include = [(_LEFT_HALF, [0.0, 0.0, 0.5, 1.0])]
    composed = _arr(compose_loss_mask(include, [], 100, 100))
    raster = _arr(rasterize_detections(include, 100, 100))
    assert (composed == raster).all()
