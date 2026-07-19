"""Tests for backend.ml.mask_utils.polygons_to_mask_input (SAM2 mask_input logits)."""

import json

import numpy as np
import pytest

from backend.ml.mask_utils import polygons_to_mask_input


def _poly_json(polygons):
    return json.dumps({"polygons": polygons})


def test_polygon_produces_signed_logit_map():
    # Left half of the square canvas filled.
    mask_json = _poly_json([[[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]])
    out = polygons_to_mask_input(mask_json, size=256, logit=8.0)
    assert out is not None
    assert out.shape == (1, 256, 256)
    assert out.dtype == np.float32
    # Inside the filled region → +logit; outside → -logit.
    assert out[0, 128, 10] == pytest.approx(8.0)
    assert out[0, 128, 250] == pytest.approx(-8.0)
    # Only two distinct values, symmetric around zero.
    assert set(np.unique(out).tolist()) == {-8.0, 8.0}


def test_bbox_fallback_when_no_polygons():
    out = polygons_to_mask_input(None, bbox=[0.25, 0.25, 0.75, 0.75], size=64)
    assert out is not None
    assert out.shape == (1, 64, 64)
    assert out[0, 32, 32] > 0   # center inside bbox
    assert out[0, 2, 2] < 0     # corner outside bbox


def test_custom_size_and_logit():
    mask_json = _poly_json([[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]])
    out = polygons_to_mask_input(mask_json, size=32, logit=4.0)
    assert out.shape == (1, 32, 32)
    assert set(np.unique(out).tolist()) == {4.0}


def test_empty_geometry_returns_none():
    assert polygons_to_mask_input(None, bbox=None) is None
    assert polygons_to_mask_input(_poly_json([]), bbox=None) is None


@pytest.mark.parametrize(
    "mask_json",
    ["not json", json.dumps(["wrong shape"]), _poly_json([[[0.1, 0.1], [0.2, 0.2]]])],
)
def test_malformed_mask_json_without_bbox_returns_none(mask_json):
    # No polygons survive and no bbox to fall back to → None.
    assert polygons_to_mask_input(mask_json, bbox=None) is None
