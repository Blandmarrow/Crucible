"""Tests for backend.ml.mask_utils.merge_detection_geometry (detection merge)."""

import json

from backend.ml.mask_utils import merge_detection_geometry


def _poly_json(polygons):
    return json.dumps({"polygons": polygons})


def _polys(mask_json):
    return json.loads(mask_json)["polygons"]


def test_union_bbox_envelope():
    mask_out, bbox = merge_detection_geometry([
        (None, [0.1, 0.2, 0.4, 0.5]),
        (None, [0.3, 0.1, 0.6, 0.7]),
    ])
    # No polygons anywhere → mask is None, bbox is the union envelope.
    assert mask_out is None
    assert bbox == [0.1, 0.1, 0.6, 0.7]


def test_polygons_concatenated():
    a = _poly_json([[[0.0, 0.0], [0.2, 0.0], [0.2, 0.2]]])
    b = _poly_json([[[0.5, 0.5], [0.7, 0.5], [0.7, 0.7]]])
    mask_out, bbox = merge_detection_geometry([
        (a, [0.0, 0.0, 0.2, 0.2]),
        (b, [0.5, 0.5, 0.7, 0.7]),
    ])
    assert mask_out is not None
    assert len(_polys(mask_out)) == 2
    assert bbox == [0.0, 0.0, 0.7, 0.7]


def test_bbox_only_entry_gets_rectangle_polygon_when_others_have_polys():
    a = _poly_json([[[0.0, 0.0], [0.2, 0.0], [0.2, 0.2]]])
    mask_out, bbox = merge_detection_geometry([
        (a, [0.0, 0.0, 0.2, 0.2]),
        (None, [0.5, 0.5, 0.8, 0.9]),   # bbox-only → rectangle polygon
    ])
    polys = _polys(mask_out)
    assert len(polys) == 2
    # The injected rectangle has the four bbox corners.
    rect = polys[1]
    assert rect == [[0.5, 0.5], [0.8, 0.5], [0.8, 0.9], [0.5, 0.9]]


def test_bbox_clamped_and_reordered():
    mask_out, bbox = merge_detection_geometry([
        (None, [0.6, 0.7, 0.2, 0.1]),   # reversed coords
        (None, [-0.3, 0.0, 1.5, 0.4]),  # out of range
    ])
    assert mask_out is None
    assert bbox == [0.0, 0.0, 1.0, 0.7]


def test_malformed_mask_json_ignored():
    mask_out, bbox = merge_detection_geometry([
        ("not json", [0.1, 0.1, 0.3, 0.3]),
        (None, [0.2, 0.2, 0.5, 0.5]),
    ])
    # Both entries reduce to bbox-only → no polygons.
    assert mask_out is None
    assert bbox == [0.1, 0.1, 0.5, 0.5]


def test_no_valid_boxes_yields_zero_bbox():
    mask_out, bbox = merge_detection_geometry([(None, []), (None, [1.0, 2.0])])
    assert mask_out is None
    assert bbox == [0.0, 0.0, 0.0, 0.0]
