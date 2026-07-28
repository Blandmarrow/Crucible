"""Pass 2's decode half: `video_extract.render_at_timestamps` and `_write_frame`.

Service level — real mp4 fixtures, real decoding, no HTTP. Nothing here needs
`scenedetect`: pass 2 never detects shots, because the pick already happened in
pass 1 and the recorded timestamp is the artifact.

The one test to keep if only one survives review is
`test_re_seeking_a_recorded_timestamp_lands_on_the_same_moment`. Off-by-one-keyframe
seeking silently yields the *wrong frame* at full resolution — a failure that
looks like success in every count the job reports and in every other test here.
"""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PilImage

from backend.services import video_extract as ve
from backend.tests.conftest import mp4_shots_bytes


@pytest.fixture(scope="module")
def shots_mp4(tmp_path_factory) -> Path:
    """3 shots x 30 frames at 25 fps = 1200 ms each, 320x240. Module-scoped:
    encoding it costs more than every test that reads it."""
    p = tmp_path_factory.mktemp("fixtures") / "shots.mp4"
    p.write_bytes(mp4_shots_bytes())
    return p


def _render(video: Path, tmp_path: Path, stamps, *, suffix=".jpg", **kwargs):
    dests = [(str(tmp_path / f"f{i}{suffix}"), None) for i in range(len(stamps))]
    return ve.render_at_timestamps(video, list(stamps), dests=dests, **kwargs)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_native_resolution_is_the_default(shots_mp4, tmp_path):
    """`long_edge=0` means no downscale, which is the whole point of pass 2 —
    `render_shot` already reads 0 that way, so nothing new had to learn it."""
    result = _render(shots_mp4, tmp_path, [600.0, 1800.0])
    assert result.failed == 0
    assert len(result.written) == 2
    for frame in result.written:
        with PilImage.open(frame.path) as img:
            assert img.size == (320, 240)


def test_a_long_edge_cap_downscales(shots_mp4, tmp_path):
    result = _render(shots_mp4, tmp_path, [600.0], long_edge=160)
    assert len(result.written) == 1
    with PilImage.open(result.written[0].path) as img:
        assert max(img.size) == 160
        assert img.size == (160, 120)


def test_a_cap_larger_than_the_source_does_not_upscale(shots_mp4, tmp_path):
    result = _render(shots_mp4, tmp_path, [600.0], long_edge=4096)
    with PilImage.open(result.written[0].path) as img:
        assert img.size == (320, 240)


# ---------------------------------------------------------------------------
# Geometry replay
# ---------------------------------------------------------------------------


def test_the_stored_crop_is_replayed(shots_mp4, tmp_path):
    result = _render(shots_mp4, tmp_path, [600.0], crop=(40, 20, 200, 120))
    with PilImage.open(result.written[0].path) as img:
        assert img.size == (200, 120)


def test_the_crop_is_clamped_against_the_decoded_frame_not_the_header(shots_mp4, tmp_path):
    """`frame.shape` is the authority — headers lie, and container rotation swaps
    the axes. A rect running off the right edge is trimmed, never an error."""
    result = _render(shots_mp4, tmp_path, [600.0], crop=(200, 100, 400, 400))
    assert result.failed == 0
    with PilImage.open(result.written[0].path) as img:
        w, h = img.size
    assert 0 < w <= 120 and 0 < h <= 140


def test_a_full_frame_crop_is_a_no_op(shots_mp4, tmp_path):
    result = _render(shots_mp4, tmp_path, [600.0], crop=(0, 0, 320, 240))
    with PilImage.open(result.written[0].path) as img:
        assert img.size == (320, 240)


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------


def test_both_output_formats_are_written_and_re_open(shots_mp4, tmp_path):
    """The format comes from the suffix, through `normalize_image_format` — so
    the caller picks by naming the file, and pass 1's `.jpg` is unchanged."""
    for suffix, fmt in ((".jpg", "JPEG"), (".png", "PNG")):
        out = tmp_path / f"only{suffix}"
        result = ve.render_at_timestamps(shots_mp4, [600.0], dests=[(str(out), None)])
        assert result.written[0].path == str(out)
        with PilImage.open(out) as img:
            assert img.format == fmt


def test_png_output_is_the_decoded_frame_exactly(shots_mp4, tmp_path):
    """"Lossless" has to mean this and nothing weaker: the written PNG decodes
    back to the same array the reader handed over."""
    out = tmp_path / "lossless.png"
    ve.render_at_timestamps(shots_mp4, [600.0], dests=[(str(out), None)])

    direct = None
    for _ts, frame in ve.read_positions(shots_mp4, [600.0]):
        direct = frame[:, :, ::-1].copy()
        break
    assert direct is not None

    with PilImage.open(out) as img:
        written = np.asarray(img.convert("RGB"))
    assert np.array_equal(written, direct)


def test_a_thumbnail_is_written_when_one_is_asked_for(shots_mp4, tmp_path):
    """Pass 2's job passes None (it regenerates from the swapped-in file), but
    the shared writer still owes pass 1 its 256px WebP."""
    out, thumb = tmp_path / "f.jpg", tmp_path / "f.webp"
    ve.render_at_timestamps(shots_mp4, [600.0], dests=[(str(out), str(thumb))])
    with PilImage.open(thumb) as img:
        assert img.format == "WEBP"
        assert max(img.size) <= 256


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------


def test_written_frames_carry_the_index_of_the_target_they_came_from(shots_mp4, tmp_path):
    """Targets are walked in ascending timestamp order so the reader moves
    forward, so `pick` — not position in `written` — is what maps a file back to
    its row."""
    stamps = [2800.0, 600.0, 1800.0]
    result = _render(shots_mp4, tmp_path, stamps)
    assert [f.pick for f in result.written] == [1, 2, 0]
    for frame in result.written:
        assert frame.path.endswith(f"f{frame.pick}.jpg")
        assert frame.timestamp_ms == int(round(stamps[frame.pick]))
        assert frame.shot_index == -1


def test_an_unreachable_timestamp_is_counted_failed_not_raised(shots_mp4, tmp_path):
    """A seek past the end costs one frame, never the run — the same contract the
    probe holds for a broken tail."""
    result = _render(shots_mp4, tmp_path, [600.0, 999_000.0])
    assert result.failed == 1
    assert len(result.written) == 1
    assert result.written[0].pick == 0
    assert not (tmp_path / "f1.jpg").exists()


# ---------------------------------------------------------------------------
# Seek exactness — the claim the whole feature rests on
# ---------------------------------------------------------------------------


def test_re_seeking_a_recorded_timestamp_lands_on_the_same_moment(shots_mp4, tmp_path):
    """Render a frame the way pass 1 does, then re-render at the timestamp pass 1
    recorded, and compare.

    Downscale the full-resolution pass-2 output to the triage size and assert the
    mean absolute difference is small. Off-by-one-keyframe extraction yields a
    *different frame* — in this fixture a moving white block seven pixels along,
    or in the worst case a different shot entirely — and every count the job
    reports would still say success.
    """
    for target in (600.0, 1800.0, 2800.0):
        triage = tmp_path / f"triage_{int(target)}.jpg"
        full = tmp_path / f"full_{int(target)}.png"

        pass1 = ve.render_at_timestamps(
            shots_mp4, [target], dests=[(str(triage), None)], long_edge=160
        )
        recorded = pass1.written[0].timestamp_ms

        pass2 = ve.render_at_timestamps(shots_mp4, [float(recorded)], dests=[(str(full), None)])
        assert pass2.written, f"nothing decoded at the recorded timestamp {recorded}"

        with PilImage.open(triage) as a, PilImage.open(full) as b:
            assert max(b.size) == 320, "pass 2 must be native resolution"
            shrunk = b.convert("RGB").resize(a.size, PilImage.Resampling.LANCZOS)
            diff = np.abs(
                np.asarray(shrunk, np.int16) - np.asarray(a.convert("RGB"), np.int16)
            ).mean()
        # JPEG at q95 plus two different resamplings of the same pixels; a
        # neighbouring frame in this fixture scores an order of magnitude above.
        assert diff < 12.0, f"pass 2 at {recorded}ms is not the same frame (MAD {diff:.1f})"
