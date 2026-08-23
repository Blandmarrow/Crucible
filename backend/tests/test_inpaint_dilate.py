"""`routers/inpaint._dilate` — the bbox-limited dilation, pinned against the naive form.

The optimisation is an equivalence claim, so the test is a differential one: the
bbox-limited implementation must be **pixel-identical** to a full-canvas
`MaxFilter` over the same window. It is worth pinning because the reasoning is
about what `MaxFilter` sees at the crop's edges, and the two candidates for that
padding (zero-fill or edge-replicate) both happen to give the same answer only
because the crop's border columns are black by construction — the kind of
argument a future edit can quietly falsify.

Torch-free and cv2-free: `_dilate` is pure Pillow.
"""
from PIL import Image as PilImage, ImageDraw, ImageFilter

from backend.routers.inpaint import _dilate


def _naive(mask, px: int):
    """The implementation `_dilate` replaced: filter the whole canvas."""
    if px <= 0:
        return mask.copy()
    return mask.filter(ImageFilter.MaxFilter(2 * px + 1))


def _mask(size, boxes):
    m = PilImage.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for b in boxes:
        d.rectangle(b, fill=255)
    return m


def _same(a, b) -> bool:
    return a.tobytes() == b.tobytes()


def test_bbox_limited_dilation_matches_the_full_canvas_filter():
    """A box well inside the canvas — the ordinary watermark case."""
    for px in (1, 3, 6, 16):
        m = _mask((160, 120), [(40, 30, 90, 70)])
        assert _same(_dilate(m.copy(), px), _naive(m, px)), f"px={px}"


def test_two_disjoint_regions_still_match():
    """`getbbox()` is one box around *both*, so the gap between them is filtered
    too — which is exactly what the full-canvas run does."""
    m = _mask((160, 120), [(10, 10, 25, 25), (130, 95, 150, 112)])
    for px in (2, 8):
        assert _same(_dilate(m.copy(), px), _naive(m, px)), f"px={px}"


def test_a_mask_touching_the_border_matches():
    """The clamped case: the crop meets the image edge, so both runs face the
    filter's own border padding rather than a black column of canvas."""
    m = _mask((80, 60), [(0, 0, 12, 60)])
    for px in (1, 4, 10):
        assert _same(_dilate(m.copy(), px), _naive(m, px)), f"px={px}"


def test_a_full_canvas_mask_matches():
    """No room to grow in any direction — the crop is the whole canvas."""
    m = _mask((40, 40), [(0, 0, 40, 40)])
    assert _same(_dilate(m.copy(), 5), _naive(m, 5))


def test_an_empty_mask_dilates_to_itself():
    """`getbbox()` is None: there is nothing to grow, and the full-canvas filter
    would be a very slow no-op."""
    m = PilImage.new("L", (200, 200), 0)
    out = _dilate(m.copy(), 12)
    assert out.getbbox() is None
    assert _same(out, _naive(m, 12))


def test_zero_px_is_a_no_op():
    m = _mask((60, 60), [(20, 20, 30, 30)])
    assert _same(_dilate(m.copy(), 0), m)
