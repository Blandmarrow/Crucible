"""Tests for backend.ml.mask_utils.rasterize_detections (mask export rasterizer)."""

import json

import numpy as np
import pytest

from backend.ml.mask_utils import rasterize_detections


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
