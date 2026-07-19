"""Tests for mask_utils.remap_detection_geometry — the pure crop remap.

``rect`` is (x, y, w, h) in pixels of the OLD image's transposed frame; ``old_size``
is (old_w, old_h). A coordinate maps ``new = clamp((n*old_dim - offset)/extent, 0, 1)``.
"""

import json

import pytest

from backend.ml.mask_utils import remap_detection_geometry


def _poly_json(polygons):
    return json.dumps({"polygons": polygons})


# A square left-half rect polygon (area 0.5) and quarter square (area 0.25).
QUARTER_SQUARE = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]


def test_bbox_interior_exact():
    # Crop the top-left quarter (0,0,50,50) of a 100x100 image.
    out = remap_detection_geometry(None, [0.1, 0.1, 0.2, 0.2], (0, 0, 50, 50), (100, 100))
    assert out is not None
    mask, bbox = out
    assert mask is None
    # 0.1*100=10 → (10-0)/50=0.2 ; 0.2*100=20 → 20/50=0.4
    assert bbox == pytest.approx([0.2, 0.2, 0.4, 0.4])


def test_bbox_partial_clamp():
    # bbox extends past the crop's right/bottom edge → clamps to 1.0.
    out = remap_detection_geometry(None, [0.4, 0.4, 0.8, 0.8], (0, 0, 50, 50), (100, 100))
    assert out is not None
    _, bbox = out
    # 0.4*100=40 → 40/50=0.8 ; 0.8*100=80 → 80/50=1.6 clamp→1.0
    assert bbox == pytest.approx([0.8, 0.8, 1.0, 1.0])


def test_bbox_fully_outside_returns_none():
    # bbox lies entirely to the bottom-right of the crop → collapses → None.
    out = remap_detection_geometry(None, [0.7, 0.7, 0.9, 0.9], (0, 0, 50, 50), (100, 100))
    assert out is None


def test_polygon_flattens_at_border():
    # Polygon spans the crop's right edge: outside vertices clamp to x=1.0, so the
    # remapped polygon keeps area (the outside part flattens against the border).
    poly = [[0.2, 0.2], [0.9, 0.2], [0.9, 0.4], [0.2, 0.4]]
    out = remap_detection_geometry(
        _poly_json([poly]), [0.2, 0.2, 0.9, 0.4], (0, 0, 50, 50), (100, 100)
    )
    assert out is not None
    mask, _ = out
    assert mask is not None
    polys = json.loads(mask)["polygons"]
    assert len(polys) == 1
    xs = [v[0] for v in polys[0]]
    assert max(xs) == pytest.approx(1.0)  # outside vertices flattened to the border


def test_polygon_collapse_keeps_bbox():
    # bbox stays inside the crop, but the mask polygon lies entirely outside it →
    # polygon collapses (area < 1e-4) → mask becomes None, bbox is kept.
    poly = [[0.6, 0.1], [0.7, 0.1], [0.7, 0.2], [0.6, 0.2]]  # x in [60,70]px, crop is x<50px
    out = remap_detection_geometry(
        _poly_json([poly]), [0.1, 0.1, 0.3, 0.3], (0, 0, 50, 100), (100, 100)
    )
    assert out is not None
    mask, bbox = out
    assert mask is None
    # bbox: x 0.1*100=10/50=0.2, 0.3*100=30/50=0.6 ; y unchanged (crop full height)
    assert bbox == pytest.approx([0.2, 0.1, 0.6, 0.3])


def test_full_frame_crop_is_identity():
    # rect == whole image → coords unchanged.
    out = remap_detection_geometry(
        _poly_json([QUARTER_SQUARE]), [0.1, 0.2, 0.3, 0.4], (0, 0, 100, 100), (100, 100)
    )
    assert out is not None
    mask, bbox = out
    assert bbox == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert json.loads(mask)["polygons"][0] == QUARTER_SQUARE


def test_degenerate_rect_returns_none():
    assert remap_detection_geometry(None, [0.1, 0.1, 0.2, 0.2], (0, 0, 0, 50), (100, 100)) is None


def test_malformed_bbox_returns_none():
    assert remap_detection_geometry(None, [0.1, 0.1], (0, 0, 50, 50), (100, 100)) is None
    assert remap_detection_geometry(None, None, (0, 0, 50, 50), (100, 100)) is None
