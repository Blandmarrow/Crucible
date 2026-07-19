"""Tests for Detection.mask_area — the pure helper and the ORM sync listeners.

The listeners on Detection.mask / Detection.bbox fire on ORM attribute assignment
(including constructor kwargs, exactly like the Image.caption_text listener), so
they are exercised here by constructing/mutating Detection objects directly — no
DB session round-trip is involved in keeping mask_area in sync.
"""

import json

import pytest

from backend.ml.mask_utils import detection_mask_area, remap_detection_geometry
from backend.models.detection import Detection


def _poly_json(polygons):
    return json.dumps({"polygons": polygons})


# Reusable geometry: a quarter-image square (area 0.25) and a left-half
# rectangle (area 0.5), both in normalized 0–1 coordinates.
QUARTER_SQUARE = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
HALF_RECT = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.5], [0.0, 0.5]]


# ── detection_mask_area (pure helper) ────────────────────────────────────────

def test_quarter_square_polygon():
    assert detection_mask_area(_poly_json([QUARTER_SQUARE]), None) == pytest.approx(0.25)


def test_multi_polygon_sum():
    area = detection_mask_area(_poly_json([QUARTER_SQUARE, QUARTER_SQUARE]), None)
    assert area == pytest.approx(0.5)


def test_bbox_fallback_when_no_polygon():
    assert detection_mask_area(None, [0.25, 0.25, 0.75, 0.75]) == pytest.approx(0.25)


def test_polygon_wins_over_bbox():
    # A valid polygon is present, so the bbox is ignored entirely.
    area = detection_mask_area(_poly_json([QUARTER_SQUARE]), [0.0, 0.0, 1.0, 1.0])
    assert area == pytest.approx(0.25)


def test_malformed_json_falls_back_to_bbox():
    assert detection_mask_area("{not valid json", [0.0, 0.0, 0.5, 0.5]) == pytest.approx(0.25)


def test_polygons_too_few_points_ignored():
    # A 2-point "polygon" is skipped; falls back to bbox.
    bad = _poly_json([[[0.0, 0.0], [0.5, 0.5]]])
    assert detection_mask_area(bad, [0.0, 0.0, 0.5, 0.5]) == pytest.approx(0.25)


def test_neither_returns_none():
    assert detection_mask_area(None, None) is None
    assert detection_mask_area(None, [0.0, 0.0]) is None  # malformed bbox (not 4)


def test_area_clamped_above_one():
    # Overlapping full-image polygons sum to 3.0 — clamped to 1.0.
    full = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    assert detection_mask_area(_poly_json([full, full, full]), None) == pytest.approx(1.0)


# ── ORM listener sync ────────────────────────────────────────────────────────

def _make(**kw):
    base = dict(image_id="img-1", label="cat", model="sam2", task="text_prompt")
    base.update(kw)
    return Detection(**base)


def test_listener_bbox_only_construction():
    det = _make(bbox=[0.0, 0.0, 0.5, 0.5])
    assert det.mask_area == pytest.approx(0.25)


def test_listener_mask_wins_bbox_before_mask():
    det = _make(bbox=[0.0, 0.0, 0.5, 0.5], mask=_poly_json([HALF_RECT]))
    assert det.mask_area == pytest.approx(0.5)


def test_listener_mask_wins_mask_before_bbox():
    det = _make(mask=_poly_json([HALF_RECT]), bbox=[0.0, 0.0, 0.5, 0.5])
    assert det.mask_area == pytest.approx(0.5)


def test_listener_refine_style_mutation():
    # Start as a plain drawn box, then attach a mask (the /refine in-place path).
    det = _make(bbox=[0.0, 0.0, 0.5, 0.5])
    assert det.mask_area == pytest.approx(0.25)
    det.mask = _poly_json([HALF_RECT])
    assert det.mask_area == pytest.approx(0.5)


def test_listener_clearing_mask_reverts_to_bbox():
    det = _make(bbox=[0.0, 0.0, 0.5, 0.5], mask=_poly_json([HALF_RECT]))
    assert det.mask_area == pytest.approx(0.5)
    det.mask = None
    assert det.mask_area == pytest.approx(0.25)


def test_listener_remap_assignment_updates_mask_area():
    # Emulate remap_detections_for_crop's mask-then-bbox assignment order and
    # confirm the listener recomputes mask_area from the remapped geometry.
    det = _make(mask=_poly_json([HALF_RECT]), bbox=[0.0, 0.0, 1.0, 0.5])
    assert det.mask_area == pytest.approx(0.5)
    # Crop the top-left quarter (0,0,50,50) of a 100x100 image.
    new_mask, new_bbox = remap_detection_geometry(
        det.mask, det.bbox, (0, 0, 50, 50), (100, 100)
    )
    det.mask = new_mask
    det.bbox = new_bbox
    assert det.mask_area == pytest.approx(detection_mask_area(new_mask, new_bbox))
