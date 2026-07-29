"""backend/services/video_frames.py — the extraction heuristics.

Pure numpy, no fixtures, no decoder: that is the entire reason this module was
split out of `video_extract.py`. These are the judgement calls the feature
stands or falls on — where the black bars are, whether the source is interlaced,
which of five candidate frames to keep — and they only get tested at all if
testing them costs milliseconds rather than an mp4 fixture per case.

Three tests here pin decisions that look like implementation detail and are not:

- `test_gaussian_noise_does_not_beat_a_sharp_frame` pins the downscale that
  happens *before* the Laplacian. Without it, variance ranks noise as sharpness
  and the "sharpest frame" policy prefers the grainiest candidate.
- `test_a_one_pixel_stripe_pattern_is_not_combing` pins the d2 floor. Without
  it, every picket fence and pinstripe shirt reads as interlacing.
- `test_all_combed_is_not_telecine` pins that a constant series has no period.
  Reporting plain interlace as telecine recommends the wrong fix.
"""

import numpy as np
import pytest

from backend.services import video_frames as vf


def _frame(height: int, width: int, value: int = 128) -> np.ndarray:
    return np.full((height, width, 3), value, np.uint8)


def _profiles(frame: np.ndarray):
    return vf.edge_profiles(frame)


def _rect(frame: np.ndarray):
    rows, cols = vf.edge_profiles(frame)
    return vf.crop_rect_from_profiles(rows, cols)


# ---------------------------------------------------------------------------
# Cropdetect
# ---------------------------------------------------------------------------


def test_letterbox_bars_are_found():
    f = _frame(240, 320, 0)
    f[30:210, :] = 200  # 30px matte top and bottom
    assert _rect(f) == (0, 30, 320, 180)


def test_pillarbox_bars_are_found():
    f = _frame(240, 320, 0)
    f[:, 40:280] = 200
    assert _rect(f) == (40, 0, 240, 240)


def test_both_axes_at_once():
    f = _frame(240, 320, 0)
    f[20:220, 40:280] = 200
    assert _rect(f) == (40, 20, 240, 200)


def test_a_clean_frame_yields_no_crop():
    assert _rect(_frame(240, 320, 200)) is None


def test_a_uniformly_dark_frame_yields_no_crop_not_a_crop_to_nothing():
    """A fade-to-black sample set must produce None, not a 2x2 rect. This is the
    difference between 'we saw no bars' and 'we cropped the picture away'."""
    assert _rect(_frame(240, 320, 2)) is None


def test_near_black_bars_still_count_as_bars():
    """Real mattes are rarely exactly 0 — MPEG ringing puts them a few levels up.
    Anything at or under the studio-swing floor is still a bar."""
    f = _frame(240, 320, 12)
    f[30:210, :] = 200
    assert _rect(f) == (0, 30, 320, 180)


def test_a_hot_pixel_in_the_bar_does_not_defeat_detection():
    """The 95th percentile rather than the max. One stuck sample per bar row is
    enough to make a max-based profile see content edge to edge."""
    f = _frame(240, 320, 0)
    f[30:210, :] = 200
    f[0:30, 7] = 255  # a bright column running through the top matte
    f[210:240, 7] = 255
    assert _rect(f) == (0, 30, 320, 180)


def test_a_bar_thinner_than_min_bar_frac_is_ignored():
    """A hairline of black at the frame edge is encoding noise, not a matte.
    The threshold is on the axis's *combined* bar thickness, so a 1px top and a
    1px bottom come to 2 of the 3.6 rows 1.5% of 240 allows."""
    f = _frame(240, 320, 0)
    f[1:239, :] = 200
    assert _rect(f) is None


def test_merge_profiles_takes_the_elementwise_max():
    """A dark shot may only grow the content rect, never shrink it."""
    bright = _frame(240, 320, 0)
    bright[30:210, :] = 200
    dark = _frame(240, 320, 0)
    dark[60:180, :] = 20  # a night scene: less of the frame clears the threshold

    acc = None
    for f in (dark, bright):
        rows, _cols = vf.edge_profiles(f)
        acc = vf.merge_profiles(acc, rows)
    _r, cols = vf.edge_profiles(bright)
    assert vf.crop_rect_from_profiles(acc, cols) == (0, 30, 320, 180)


def test_merge_profiles_copies_rather_than_aliasing_the_first_sample():
    rows, _ = vf.edge_profiles(_frame(8, 8, 200))
    acc = vf.merge_profiles(None, rows)
    rows[0] = 0.0
    assert acc[0] == pytest.approx(200.0, abs=2.0)


def test_odd_bar_sizes_snap_to_even_coordinates():
    """bwdif needs an even y to keep field parity; chroma subsampling wants the
    rest even too."""
    f = _frame(240, 320, 0)
    f[31:209, 41:279] = 200
    rect = _rect(f)
    assert rect is not None
    assert all(v % 2 == 0 for v in rect)


def test_clamp_crop_trims_a_rect_that_overruns_the_frame():
    assert vf.clamp_crop((300, 200, 400, 400), 320, 240) == (300, 200, 20, 40)


def test_clamp_crop_returns_none_when_nothing_survives():
    assert vf.clamp_crop((320, 0, 100, 100), 320, 240) is None
    assert vf.clamp_crop(None, 320, 240) is None


def test_clamp_crop_returns_none_for_a_full_frame_rect():
    assert vf.clamp_crop((0, 0, 320, 240), 320, 240) is None


# ---------------------------------------------------------------------------
# Interlace and telecine
# ---------------------------------------------------------------------------


def _diagonal_texture(height: int, width: int, x_shift: float = 0.0) -> np.ndarray:
    """A picture-like plane with real detail in both axes.

    A smooth gradient will not do: it has almost no vertical detail, so both `d1`
    and `d2` collapse and the ratio measures nothing. A diagonal sinusoid gives
    the same-parity rows something genuine to differ by, which is what the
    denominator is supposed to represent.
    """
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    return 127.0 + 100.0 * np.sin(2 * np.pi * ((xx + x_shift) / 37.0 + yy / 23.0))


def _progressive(height=240, width=320) -> np.ndarray:
    plane = _diagonal_texture(height, width)
    return np.repeat(plane.astype(np.uint8)[:, :, None], 3, axis=2)


def _combed(height=240, width=320) -> np.ndarray:
    """Two fields of a horizontal pan, woven — real combing.

    Both fields are the same picture; the odd field is displaced horizontally,
    which is exactly what motion does to an interlaced capture. Same-parity rows
    come from one field and keep their ordinary vertical detail, so `d2` is
    unchanged and only `d1` blows up.
    """
    even = _diagonal_texture(height, width, 0.0)
    odd = _diagonal_texture(height, width, 18.0)
    plane = even.copy()
    plane[1::2] = odd[1::2]
    return np.repeat(plane.astype(np.uint8)[:, :, None], 3, axis=2)


def test_field_mismatch_reads_as_combing():
    assert vf.combing_ratio(_combed()) >= vf.COMBING_THRESHOLD


def test_progressive_content_does_not():
    assert vf.combing_ratio(_progressive()) < vf.COMBING_THRESHOLD


def test_a_one_pixel_stripe_pattern_is_not_combing():
    """The false positive that would make the feature useless. A picket fence or
    a pinstripe shirt has *identical* same-parity rows, so there is no second
    field for the first to disagree with — that reads as no evidence, not as an
    unbounded ratio."""
    f = np.zeros((240, 320, 3), np.uint8)
    f[1::2] = 255
    assert vf.combing_ratio(f) == 0.0


def test_two_combed_samples_are_needed_to_call_it_interlaced():
    one = [0.5, 0.6, 1.4, 0.55]
    assert vf.interlace_from_series(one)[0] is False
    two = [0.5, 1.3, 1.4, 0.55]
    flag, note = vf.interlace_from_series(two)
    assert flag is True
    assert "2/4" in note


def test_broadcast_format_corroborates_in_the_note_only():
    combed = [1.3, 1.4]
    assert vf.interlace_from_series([0.5, 1.3], height=576, fps=25.0)[0] is False
    _flag, note = vf.interlace_from_series(combed, height=576, fps=25.0)
    assert "broadcast" in note
    _flag, plain = vf.interlace_from_series(combed, height=720, fps=24.0)
    assert "broadcast" not in plain


def test_a_three_two_pattern_reads_as_telecine():
    series = ([1.5, 1.5, 0.5, 0.5, 0.5] * 5)
    flag, note = vf.telecine_from_series(series, fps=29.97)
    assert flag is True
    assert "3:2" in note


def test_all_combed_is_not_telecine():
    """Plain interlace, not pulldown. A constant series has no period, and
    reporting it as telecine would recommend the wrong fix."""
    assert vf.telecine_from_series([1.5] * 25, fps=29.97)[0] is False


def test_telecine_is_gated_on_the_frame_rate():
    series = [1.5, 1.5, 0.5, 0.5, 0.5] * 5
    assert vf.telecine_from_series(series, fps=25.0)[0] is False
    assert vf.telecine_from_series(series, fps=None)[0] is False


def test_telecine_needs_enough_consecutive_frames():
    """The floor is the shortest run the estimator can *possibly* detect, not a
    round number. Below it a perfect 3:2 series is admitted and then provably
    rejected by the autocorrelation, so the run was collected for nothing."""
    base = [1.5, 1.5, 0.5, 0.5, 0.5]

    assert vf.telecine_from_series(base, fps=29.97)[0] is False

    twelve = (base * 3)[:12]
    assert vf.telecine_from_series(twelve, fps=29.97)[0] is False

    thirteen = (base * 3)[:13]
    assert vf.telecine_from_series(thirteen, fps=29.97)[0] is True


def test_the_telecine_floor_is_derived_from_the_autocorrelation_threshold():
    """Retuning either constant without the floor must fail here rather than
    silently reintroduce the undetectable window."""
    import math

    assert vf.TELECINE_MIN_SAMPLES == math.ceil(
        vf.TELECINE_LAG / (1 - vf.TELECINE_MIN_AUTOCORR)
    )
    assert vf.TELECINE_MIN_SAMPLES == 13


# ---------------------------------------------------------------------------
# Sharpness and candidate selection
# ---------------------------------------------------------------------------


def _sharp(height=480, width=640, block=16) -> np.ndarray:
    """Hard-edged structure at a scale a real lens resolves.

    A checkerboard, not one-pixel lines: a 1px grid is literally the highest
    spatial frequency the sensor can carry and is indistinguishable from noise
    by any measure — including the human one — so scoring it as "sharp" would
    make the test agree with the bug it is supposed to catch.
    """
    yy, xx = np.mgrid[0:height, 0:width]
    plane = (((yy // block) + (xx // block)) % 2 * 255).astype(np.uint8)
    return np.repeat(plane[:, :, None], 3, axis=2)


def _noise(height=480, width=640, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (height, width, 3), dtype=np.uint8)


def test_gaussian_noise_does_not_beat_a_sharp_frame():
    """Pins the downscale-before-Laplacian ordering. Removing that line makes
    this fail, which is the point: raw Laplacian variance ranks noise as
    sharpness, so the sharpest-in-window policy would prefer the grainiest
    candidate in every window."""
    assert vf.sharpness(_noise()) < vf.sharpness(_sharp())


def test_a_blurred_frame_scores_below_a_sharp_one():
    sharp = _sharp()
    blurred = sharp.astype(np.float32)
    for _ in range(4):  # cheap box blur, no scipy
        blurred[:, 1:-1] = (blurred[:, :-2] + blurred[:, 1:-1] + blurred[:, 2:]) / 3.0
    assert vf.sharpness(blurred.astype(np.uint8)) < vf.sharpness(sharp)


def test_sharpness_is_comparable_across_resolutions():
    """Fixed analysis resolution: the same picture at 4K and at 720p must not
    score an order of magnitude apart, or nothing can be compared across shots."""
    small = vf.sharpness(_sharp(480, 640))
    assert small > 0


def test_is_degenerate_flags_black_white_and_flat_frames():
    assert vf.is_degenerate(_frame(64, 64, 2)) is True
    assert vf.is_degenerate(_frame(64, 64, 252)) is True
    assert vf.is_degenerate(_frame(64, 64, 128)) is True  # flat mid-grey: std 0
    assert vf.is_degenerate(_sharp(64, 64)) is False


def test_pick_index_picks_the_sharpest():
    assert vf.pick_index([1.0, 9.0, 3.0], [120.0, 120.0, 120.0]) == 1


def test_pick_index_skips_a_rejected_candidate_even_when_it_is_sharpest():
    """A slate is hard-edged text on a flat field and often outscores the shot."""
    got = vf.pick_index([1.0, 99.0, 3.0], [120.0, 120.0, 120.0], rejected=[False, True, False])
    assert got == 2


def test_pick_index_rejects_a_luma_outlier_from_a_missed_cut():
    """A candidate far off the window's median brightness came from the other
    side of a cut the detector missed; keeping it files a frame from the next
    scene under this shot's index."""
    got = vf.pick_index([1.0, 99.0, 3.0], [120.0, 20.0, 118.0])
    assert got == 2


def test_pick_index_rejects_black_and_white_candidates_by_luma():
    got = vf.pick_index([99.0, 1.0], [2.0, 130.0])
    assert got == 1


def test_pick_index_keeps_candidates_sitting_on_the_luma_floor():
    """The darkest a candidate can be and still be eligible is exactly
    `DEGENERATE_LUMA_MIN`, so the outlier filter's median can never be zero —
    which is what made the `if median > 0` guard around it unreachable. Pins the
    branch that guard sat on: the filter runs, nothing is dropped, and the
    sharpest wins."""
    floor = vf.DEGENERATE_LUMA_MIN
    assert vf.pick_index([1.0, 9.0, 3.0], [floor, floor, floor]) == 1


def test_pick_index_falls_back_to_the_middle_when_all_are_rejected():
    got = vf.pick_index([1.0, 2.0, 3.0], [1.0, 1.0, 1.0])
    assert got == 1


def test_middle_policy_returns_the_middle_of_what_survived():
    got = vf.pick_index([1.0, 2.0, 3.0], [120.0, 2.0, 118.0], policy="middle")
    assert got in (0, 2)
    assert vf.pick_index([1.0, 2.0, 3.0], [120.0, 121.0, 118.0], policy="middle") == 1


def test_pick_index_ties_go_to_the_earlier_timestamp():
    assert vf.pick_index([5.0, 5.0, 5.0], [120.0, 120.0, 120.0]) == 0


def test_pick_index_rejects_mismatched_input_lengths():
    with pytest.raises(ValueError):
        vf.pick_index([1.0, 2.0], [1.0])
    with pytest.raises(ValueError):
        vf.pick_index([], [])


# ---------------------------------------------------------------------------
# The luma plane
# ---------------------------------------------------------------------------
# `luma()` is computed once per frame by the callers in `video_extract.py` and
# handed to every consumer instead of each recomputing it — ~3x the wall clock
# and 2.5x the transient RSS on a 4K candidate. These tests are what stop that
# from being undone: the second one *is* the contract, and the rest pin the
# einsum rewrite to the Rec.601 expression it replaced.


def test_luma_matches_the_rec601_expression():
    f = _noise(48, 64, seed=3)
    b, g, r = (f[:, :, i].astype(np.float32) for i in range(3))
    # atol, not exact: einsum's summation order is an implementation detail.
    got = vf.luma(f)
    assert got.dtype == np.float32
    assert np.allclose(got, 0.114 * b + 0.587 * g + 0.299 * r, atol=1e-4)


def test_the_luma_plane_is_accepted_wherever_a_frame_is():
    """Every consumer takes a plane in place of a frame, with the same answer."""
    for f in (_noise(48, 64, seed=4), _sharp(96, 128), _frame(48, 64, 0)):
        plane = vf.luma(f)
        assert vf.sharpness(plane) == vf.sharpness(f)
        assert vf.is_degenerate(plane) == vf.is_degenerate(f)
        assert vf.combing_ratio(plane) == vf.combing_ratio(f)
        rows_p, cols_p = vf.edge_profiles(plane)
        rows_f, cols_f = vf.edge_profiles(f)
        assert np.array_equal(rows_p, rows_f)
        assert np.array_equal(cols_p, cols_f)


def test_luma_of_a_plane_is_the_plane():
    """The passthrough must not copy — that is what makes sharing it free."""
    p = np.full((16, 16), 100.0, np.float32)
    assert np.shares_memory(vf.luma(p), p)
    wide = np.full((16, 16), 100.0, np.float64)
    assert vf.luma(wide).dtype == np.float32


def test_luma_still_rejects_a_non_bgr_frame():
    with pytest.raises(ValueError):
        vf.luma(np.zeros((8, 8, 2), np.uint8))
    with pytest.raises(ValueError):
        vf.luma(np.zeros((2, 4, 8, 3), np.uint8))
    # BGRA is tolerated: the alpha channel is sliced off, not rejected.
    bgra = np.dstack([_noise(8, 8, seed=5), np.full((8, 8), 200, np.uint8)])
    assert np.array_equal(vf.luma(bgra), vf.luma(bgra[:, :, :3]))
