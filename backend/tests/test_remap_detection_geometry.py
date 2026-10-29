"""Tests for mask_utils.remap_detection_geometry — the pure crop remap.

``rect`` is (x, y, w, h) in pixels of the OLD image's transposed frame; ``old_size``
is (old_w, old_h). A coordinate maps ``new = (n*old_dim - offset)/extent``; the bbox
is then clamped to [0, 1] and each polygon clipped to the unit square (GPT-017). The
last test drives the same remap through ``POST /images/{id}/crop``.
"""

import json

import pytest
from sqlalchemy import select

from backend.ml.mask_utils import remap_detection_geometry
from backend.models.detection import Detection
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


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


def test_polygon_clipped_at_border():
    # Polygon spans the crop's right edge: it is clipped at x=1.0, so the remapped
    # polygon keeps the area inside the crop and loses the part outside it.
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
    assert max(xs) == pytest.approx(1.0)  # cut off at the border


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


def _shoelace(poly):
    n = len(poly)
    return abs(sum(
        poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1]
        for i in range(n)
    )) / 2.0


def test_polygon_outside_crop_corner_is_dropped():
    """GPT-017: a triangle that misses the crop must not come back as a mask.

    On a 100×100 image the triangle (0,0),(60,0),(0,60) is x+y <= 60 px, and the
    crop (40,40,60,60) needs x, y >= 40, i.e. x+y >= 80 — no overlap. Clamping
    each vertex used to map it onto the crop's top-left corner as the invented
    triangle [[0,0],[0.3333,0],[0,0.3333]]. Its bbox does overlap the crop, so the
    row survives with the bbox and no mask.
    """
    mask = _poly_json([[[0, 0], [0.6, 0], [0, 0.6]]])
    out = remap_detection_geometry(mask, [0, 0, 0.6, 0.6], (40, 40, 60, 60), (100, 100))
    assert out is not None
    new_mask, bbox = out
    assert new_mask is None
    assert bbox == pytest.approx([0.0, 0.0, 0.3333, 0.3333])


def test_polygon_on_one_side_of_crop_is_dropped():
    # The polygon lies entirely left of the crop (x >= 50 px): nothing survives
    # the clip, while the wider bbox still reaches into it and is kept.
    mask = _poly_json([[[0.0, 0.5], [0.3, 0.5], [0.0, 0.9]]])
    out = remap_detection_geometry(mask, [0.0, 0.5, 0.6, 0.9], (50, 0, 50, 100), (100, 100))
    assert out is not None
    assert out[0] is None
    assert out[1] == pytest.approx([0.0, 0.5, 0.2, 0.9])


def test_edge_crossing_polygon_clips_to_exact_vertices():
    """A triangle whose hypotenuse passes outside the crop's corner.

    Mapped into the 50×50 crop, (0.2,0.2),(0.9,0.2),(0.2,0.9) becomes
    (0.4,0.4),(1.8,0.4),(0.4,1.8). The hypotenuse x+y=2.2 never enters the unit
    square, so the clip is the rectangle up to the crop's corner (1,1). Clamping
    vertices instead gave the triangle (0.4,0.4),(1,0.4),(0.4,1): it lost that
    corner, half the true area.
    """
    mask = _poly_json([[[0.2, 0.2], [0.9, 0.2], [0.2, 0.9]]])
    out = remap_detection_geometry(mask, [0.2, 0.2, 0.9, 0.9], (0, 0, 50, 50), (100, 100))
    assert out is not None
    polys = json.loads(out[0])["polygons"]
    assert len(polys) == 1
    assert sorted(map(tuple, polys[0])) == [(0.4, 0.4), (0.4, 1.0), (1.0, 0.4), (1.0, 1.0)]
    assert _shoelace(polys[0]) == pytest.approx(0.36)


def test_clipped_vertices_are_interpolated_on_the_border():
    # (0.1,0.3),(0.9,0.3),(0.1,0.5) in a 50×100 crop maps to (0.2,0.3),(1.8,0.3),
    # (0.2,0.5). The edges leaving through x=1 are cut there: the long one at
    # y=0.3, the hypotenuse at y = 0.3 + 0.2*(0.8/1.6) = 0.4.
    mask = _poly_json([[[0.1, 0.3], [0.9, 0.3], [0.1, 0.5]]])
    out = remap_detection_geometry(mask, [0.1, 0.3, 0.9, 0.5], (0, 0, 50, 100), (100, 100))
    polys = json.loads(out[0])["polygons"]
    assert sorted(map(tuple, polys[0])) == [(0.2, 0.3), (0.2, 0.5), (1.0, 0.3), (1.0, 0.4)]


def test_concave_polygon_keeps_its_true_area():
    """A U shape whose arms cross the crop's bottom edge.

    Sutherland–Hodgman keeps it one ring; any zero-width run along the border
    encloses nothing, so the area is the true intersection's: the 0.8×(1-1/6)
    outer box minus the 0.4×0.5 notch.
    """
    u = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.7, 0.9], [0.7, 0.3], [0.3, 0.3], [0.3, 0.9], [0.1, 0.9]]
    out = remap_detection_geometry(_poly_json([u]), [0.1, 0.1, 0.9, 0.9], (0, 0, 100, 60), (100, 100))
    polys = json.loads(out[0])["polygons"]
    assert len(polys) == 1
    assert all(0.0 <= v <= 1.0 for pt in polys[0] for v in pt)
    assert _shoelace(polys[0]) == pytest.approx(0.8 * (1 - 0.1667) - 0.4 * 0.5, abs=1e-3)


def test_crop_route_persists_bbox_area_for_an_outside_polygon(tmp_path):
    """GPT-017 end to end: the crop keeps the row, drops the invented mask, and
    ``mask_area`` falls back to the bbox's area (1/3 × 1/3), not the 0.0555 of
    the invented triangle."""
    mask = _poly_json([[[0, 0], [0.6, 0], [0, 0.6]]])

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(100, 100)))
            async with env.Session() as db:
                db.add(Detection(
                    image_id=img["id"], label="x", bbox=[0, 0, 0.6, 0.6], mask=mask,
                    model="m", task="t",
                ))
                await db.commit()
            r = await env.client.post(
                f"{API}/images/{img['id']}/crop",
                json={"x": 40, "y": 40, "width": 60, "height": 60, "replace": True},
            )
            assert r.status_code == 200, r.text
            async with env.Session() as db:
                dets = (await db.execute(
                    select(Detection).where(Detection.image_id == img["id"])
                )).scalars().all()
            assert len(dets) == 1
            assert dets[0].mask is None
            assert dets[0].bbox == pytest.approx([0.0, 0.0, 0.3333, 0.3333])
            assert dets[0].mask_area == pytest.approx(0.3333 * 0.3333, abs=1e-4)

    run(scenario())
