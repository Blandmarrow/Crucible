"""backend/services/video_extract.py and `video_service.measure_duration_ms`.

Service level: real mp4 fixtures, real decoding, no HTTP. The shot-detection
half is `importorskip`'d because `scenedetect` is optional — and that split is
deliberate rather than defensive, since the probe, the duration measurement and
`render_shot` all have to keep working on an install that never got it.

The one test to keep if only one survives review is
`test_a_video_with_no_cuts_yields_exactly_one_spanning_shot`. `get_scene_list()`
returns `[]` — not one scene — when the detector found nothing, and code that
takes that at face value writes zero frames and reports success.
"""

import threading
import time
from pathlib import Path

import pytest

from backend.services import video_extract as ve
from backend.services.video_service import UnreadableVideoError, measure_duration_ms
from backend.tests.conftest import frame_colour, mp4_bytes, mp4_corrupt_bytes, mp4_shots_bytes

pytest.importorskip("cv2", reason="opencv is not installed")

needs_scenedetect = pytest.mark.skipif(
    not ve.capabilities()["shot_detection"], reason="scenedetect is not installed"
)


@pytest.fixture(scope="module")
def shots_mp4(tmp_path_factory) -> Path:
    """3 shots x 30 frames at 25 fps = 1200 ms each. Module-scoped: encoding it
    costs more than every test that reads it."""
    p = tmp_path_factory.mktemp("fixtures") / "shots.mp4"
    p.write_bytes(mp4_shots_bytes())
    return p


@pytest.fixture(scope="module")
def flat_mp4(tmp_path_factory) -> Path:
    """A continuous grey ramp — no cuts anywhere in it."""
    p = tmp_path_factory.mktemp("fixtures") / "flat.mp4"
    p.write_bytes(mp4_bytes(frames=40, size=(320, 240)))
    return p


# ---------------------------------------------------------------------------
# measure_duration_ms
# ---------------------------------------------------------------------------


def test_duration_is_measured_by_seeking(shots_mp4):
    """Within one frame of the header's own answer. It reads slightly *lower*
    because it measures what actually decodes, and cv2 reliably decodes one
    frame fewer than VideoWriter emitted."""
    assert measure_duration_ms(shots_mp4) == pytest.approx(3600, abs=45)


def test_duration_measurement_ignores_a_wildly_wrong_hint(shots_mp4):
    assert measure_duration_ms(shots_mp4, hint_ms=999_999) == pytest.approx(3600, abs=45)
    assert measure_duration_ms(shots_mp4, hint_ms=10) == pytest.approx(3600, abs=45)


def test_duration_is_none_for_a_file_that_will_not_open(tmp_path):
    p = tmp_path / "broken.mp4"
    p.write_bytes(mp4_corrupt_bytes())
    assert measure_duration_ms(p) is None


class _NonSeekableCapture:
    """A stream that ignores every seek and answers with the next sequential
    frame — what a matroska written to a pipe does. Without the seekability
    check the exponential probe reads every position as reachable and the
    function invents a duration.

    Its `CAP_PROP_FRAME_COUNT` is the poisoned -230584300921369408 that
    `probe_video` already guards against, so the two guards are exercised
    against the same shape of file.
    """

    POISONED_FRAME_COUNT = -230584300921369408

    def __init__(self):
        self._pos_ms = 0.0

    def isOpened(self):
        return True

    def set(self, prop, value):
        return True  # the seek is silently ignored

    def get(self, prop):
        import cv2

        if prop == cv2.CAP_PROP_POS_MSEC:
            return self._pos_ms
        if prop == cv2.CAP_PROP_FPS:
            return 25.0
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(self.POISONED_FRAME_COUNT)
        return 0.0

    def grab(self):
        self._pos_ms += 40.0
        return True

    def release(self):
        pass


def test_a_non_seekable_stream_measures_as_none(tmp_path, monkeypatch):
    import cv2

    p = tmp_path / "pipe.mkv"
    p.write_bytes(b"\x00" * 128)
    monkeypatch.setattr(cv2, "VideoCapture", lambda _p: _NonSeekableCapture())

    assert measure_duration_ms(p) is None


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


def test_capabilities_reports_both_halves():
    caps = ve.capabilities()
    assert set(caps) == {"shot_detection", "deinterlace", "scenedetect_version", "ffmpeg_version"}
    assert isinstance(caps["shot_detection"], bool)
    assert isinstance(caps["deinterlace"], bool)


def test_require_deinterlace_raises_when_ffmpeg_is_absent(monkeypatch):
    monkeypatch.setattr(
        ve, "capabilities",
        lambda: {"shot_detection": True, "deinterlace": False,
                 "scenedetect_version": None, "ffmpeg_version": None},
    )
    with pytest.raises(ve.ExtractionUnavailable, match="imageio-ffmpeg"):
        ve.require_deinterlace()


# ---------------------------------------------------------------------------
# probe_samples
# ---------------------------------------------------------------------------


def _decode(data_url: str):
    import base64
    import io

    from PIL import Image as PilImage

    _prefix, _, payload = data_url.partition(",")
    return PilImage.open(io.BytesIO(base64.b64decode(payload)))


def test_probe_returns_the_requested_number_of_decodable_samples(shots_mp4):
    r = ve.probe_samples(shots_mp4, duration_ms=3600, samples=6)
    assert len(r["samples"]) == 6
    assert r["samples_failed"] == 0
    for sample in r["samples"]:
        with _decode(sample["data_url"]) as img:
            assert img.format == "JPEG"


def test_probe_caps_the_sample_count_server_side(shots_mp4):
    """The Pydantic bound is a courtesy to the client; this is the defence."""
    r = ve.probe_samples(shots_mp4, duration_ms=3600, samples=500)
    assert len(r["samples"]) <= ve.PROBE_MAX_SAMPLES


def test_probe_respects_the_payload_budget(shots_mp4):
    r = ve.probe_samples(shots_mp4, duration_ms=3600, samples=12, max_payload_bytes=4000)
    assert r["truncated"] is True
    assert 0 < len(r["samples"]) < 12
    assert sum(len(s["data_url"]) for s in r["samples"]) <= 4000
    assert any("truncated" in w.lower() for w in r["warnings"])


def test_probe_samples_stay_inside_the_trimmed_span(shots_mp4):
    r = ve.probe_samples(shots_mp4, duration_ms=3600, samples=5, trim_start_ms=1200, trim_end_ms=1200)
    stamps = [s["timestamp_ms"] for s in r["samples"]]
    assert all(1200 <= t <= 2400 for t in stamps), stamps
    # Everything sampled is inside shot 1, which is the green one.
    for sample in r["samples"]:
        with _decode(sample["data_url"]) as img:
            r_, g_, b_ = img.convert("RGB").resize((1, 1)).getpixel((0, 0))
        assert g_ > r_ and g_ > b_


def test_probe_downscales_the_preview_but_reports_real_dimensions(shots_mp4):
    r = ve.probe_samples(shots_mp4, duration_ms=3600, samples=2, max_edge=96)
    assert (r["width"], r["height"]) == (320, 240)
    for sample in r["samples"]:
        with _decode(sample["data_url"]) as img:
            assert max(img.size) <= 96


def test_probe_without_a_duration_falls_back_to_head_samples(shots_mp4):
    r = ve.probe_samples(shots_mp4, duration_ms=None, samples=3)
    assert [s["timestamp_ms"] for s in r["samples"]] == [0, 1000, 2000]
    assert any("no usable duration" in w for w in r["warnings"])


def test_probe_returns_partial_results_when_seeks_fail(shots_mp4):
    """Broken tails are common; a failed seek must cost one sample, not the
    whole probe."""
    # An overstated duration puts the later sample points past the real end,
    # which is exactly what a header that outlasts its stream does.
    r = ve.probe_samples(shots_mp4, duration_ms=7200, samples=8)
    assert r["samples_failed"] > 0
    assert r["samples"], "a partial result is still a result"
    assert any("could not be decoded" in w for w in r["warnings"])


def test_probe_refuses_a_file_that_will_not_open(tmp_path):
    p = tmp_path / "broken.mp4"
    p.write_bytes(mp4_corrupt_bytes())
    with pytest.raises(ValueError):
        ve.probe_samples(p, duration_ms=1000, samples=3)


def test_probe_finds_no_crop_in_a_full_frame_source(shots_mp4):
    assert ve.probe_samples(shots_mp4, duration_ms=3600, samples=6)["crop"] is None


# ---------------------------------------------------------------------------
# detect_shots
# ---------------------------------------------------------------------------


@needs_scenedetect
def test_shot_boundaries_land_within_a_couple_of_frames(shots_mp4):
    shots, method = ve.detect_shots(shots_mp4, duration_ms=3600)
    assert method == "adaptive"
    assert len(shots) == 3
    for i, (shot, expected) in enumerate(zip(shots, (0, 1200, 2400))):
        assert shot.index == i
        assert shot.start_ms == pytest.approx(expected, abs=80)  # ~2 frames


@needs_scenedetect
def test_a_video_with_no_cuts_yields_exactly_one_spanning_shot(flat_mp4):
    """The empty-list trap. `get_scene_list()` answers `[]`, not one scene, when
    nothing was detected — and code that believes it writes zero frames and
    reports success."""
    shots, _method = ve.detect_shots(flat_mp4, duration_ms=1600)
    assert len(shots) == 1
    assert shots[0].start_ms == 0
    assert shots[0].end_ms > 1000


@needs_scenedetect
def test_a_crop_does_not_break_detection(shots_mp4):
    """`SceneManager.crop` is inclusive corners, not x/y/w/h, and it only
    *warns* on an out-of-range rect instead of raising — which is why the rect
    is clamped before it gets there."""
    shots, method = ve.detect_shots(shots_mp4, duration_ms=3600, crop=(20, 20, 280, 200))
    assert method == "adaptive"
    assert len(shots) == 3


@needs_scenedetect
def test_an_out_of_range_crop_is_clamped_rather_than_passed_through(shots_mp4):
    shots, method = ve.detect_shots(shots_mp4, duration_ms=3600, crop=(0, 0, 9999, 9999))
    assert method == "adaptive"
    assert len(shots) == 3


@needs_scenedetect
def test_frame_skip_still_finds_the_cuts(shots_mp4):
    """Legal here only because no StatsManager is attached to the SceneManager."""
    shots, method = ve.detect_shots(shots_mp4, duration_ms=3600, frame_skip=1)
    assert method == "adaptive"
    assert len(shots) == 3


@needs_scenedetect
def test_trims_bound_the_detected_span(shots_mp4):
    shots, _ = ve.detect_shots(shots_mp4, duration_ms=3600, trim_start_ms=1300, trim_end_ms=1300)
    assert shots[0].start_ms >= 1200
    assert shots[-1].end_ms <= 2400


@needs_scenedetect
def test_max_shots_caps_the_list(shots_mp4):
    shots, _ = ve.detect_shots(shots_mp4, duration_ms=3600, max_shots=2)
    assert len(shots) == 2


@needs_scenedetect
def test_cancellation_returns_promptly_and_discards_partials(tmp_path):
    big = tmp_path / "big.mp4"
    big.write_bytes(mp4_shots_bytes(shots=6, frames_per_shot=200))
    progress = ve.Progress()

    def cancel_soon():
        time.sleep(0.05)
        progress.cancel = True

    threading.Thread(target=cancel_soon).start()
    started = time.monotonic()
    shots, method = ve.detect_shots(big, duration_ms=48_000, progress=progress)
    elapsed = time.monotonic() - started

    assert method == "cancelled"
    assert shots == []
    assert elapsed < 10.0, "cancel must not wait for the whole detection pass"


def test_a_file_that_will_not_open_raises_rather_than_falling_back(tmp_path):
    """Not the uniform fallback: slicing a stream that does not exist into
    windows produces a job that "completes" having written nothing."""
    p = tmp_path / "broken.mp4"
    p.write_bytes(mp4_corrupt_bytes())
    with pytest.raises(UnreadableVideoError):
        ve.detect_shots(p, duration_ms=1000)


def test_without_scenedetect_it_falls_back_to_uniform_windows(shots_mp4, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("scenedetect"):
            raise ImportError("scenedetect is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    shots, method = ve.detect_shots(shots_mp4, duration_ms=60_000, min_shot_ms=600)

    assert method == "uniform"
    assert len(shots) == 12  # 60 s / the 5 s default window
    assert shots[0].start_ms == 0
    assert shots[-1].end_ms == 60_000
    # Contiguous and ascending, with no gaps between windows.
    assert all(a.end_ms == b.start_ms for a, b in zip(shots, shots[1:]))


def test_uniform_windows_honour_min_shot_ms_and_max_shots(shots_mp4, monkeypatch):
    import builtins

    real_import = builtins.__import__
    monkeypatch.setattr(
        builtins, "__import__",
        lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n.startswith("scenedetect")
        else real_import(n, *a, **k),
    )
    shots, _ = ve.detect_shots(shots_mp4, duration_ms=600_000, max_shots=10)
    assert len(shots) <= 10


@needs_scenedetect
def test_one_enormous_shot_falls_back_to_uniform_sampling(flat_mp4, monkeypatch):
    """One frame out of two hours is not an extraction. Lowering the threshold
    rather than encoding a two-hour fixture."""
    monkeypatch.setattr(ve, "SINGLE_SHOT_FALLBACK_MS", 500)
    shots, method = ve.detect_shots(flat_mp4, duration_ms=1600)
    assert method == "uniform"
    assert len(shots) >= 1


# ---------------------------------------------------------------------------
# render_shot
# ---------------------------------------------------------------------------


def _dest(tmp_path: Path, name: str) -> tuple[str, str]:
    return str(tmp_path / f"{name}.jpg"), str(tmp_path / f"{name}.webp")


def test_render_writes_a_frame_and_its_thumbnail(shots_mp4, tmp_path):
    from PIL import Image as PilImage

    shot = ve.Shot(index=0, start_ms=0, end_ms=1200)
    result = ve.render_shot(shots_mp4, shot, dests=[_dest(tmp_path, "a")])

    assert result.failed == 0
    assert len(result.written) == 1
    frame = result.written[0]
    assert frame.shot_index == 0 and frame.pick == 0
    assert 0 <= frame.timestamp_ms <= 1200
    with PilImage.open(frame.path) as img:
        assert img.format == "JPEG"
        assert img.size == (320, 240)
    with PilImage.open(frame.thumb_path) as img:
        assert img.format == "WEBP"
        assert max(img.size) <= 256


def test_frames_per_shot_spreads_picks_across_the_shot(shots_mp4, tmp_path):
    shot = ve.Shot(index=2, start_ms=0, end_ms=1200)
    dests = [_dest(tmp_path, f"m{i}") for i in range(3)]
    result = ve.render_shot(shots_mp4, shot, dests=dests, candidates=3)

    assert len(result.written) == 3
    assert [f.pick for f in result.written] == [0, 1, 2]
    stamps = [f.timestamp_ms for f in result.written]
    assert stamps == sorted(stamps), stamps
    assert stamps[-1] - stamps[0] > 300, stamps
    assert all(f.shot_index == 2 for f in result.written)


def test_render_crops_and_resizes(shots_mp4, tmp_path):
    from PIL import Image as PilImage

    shot = ve.Shot(index=0, start_ms=0, end_ms=1200)
    result = ve.render_shot(
        shots_mp4, shot, dests=[_dest(tmp_path, "c")], crop=(40, 20, 240, 200), long_edge=120
    )
    with PilImage.open(result.written[0].path) as img:
        # 240x200 cropped, then scaled so the long edge is 120.
        assert img.size == (120, 100)


def test_a_crop_larger_than_the_frame_is_clamped_per_frame(shots_mp4, tmp_path):
    """`frame.shape` is the authority, not the container header — headers lie
    and container rotation swaps the axes."""
    from PIL import Image as PilImage

    shot = ve.Shot(index=0, start_ms=0, end_ms=1200)
    result = ve.render_shot(
        shots_mp4, shot, dests=[_dest(tmp_path, "big")], crop=(200, 100, 4000, 4000)
    )
    with PilImage.open(result.written[0].path) as img:
        assert img.size == (120, 140)


def test_render_reports_failure_rather_than_raising_on_a_dead_window(shots_mp4, tmp_path):
    shot = ve.Shot(index=0, start_ms=900_000, end_ms=901_000)
    result = ve.render_shot(shots_mp4, shot, dests=[_dest(tmp_path, "gone")])
    assert result.written == []
    assert result.failed == 1


def test_the_middle_policy_and_the_sharpest_policy_can_disagree(shots_mp4, tmp_path):
    """Both must produce a frame from inside the shot; which one they pick is a
    heuristic and belongs to test_video_frames.py."""
    shot = ve.Shot(index=1, start_ms=1200, end_ms=2400)
    a = ve.render_shot(shots_mp4, shot, dests=[_dest(tmp_path, "sharp")], policy="sharpest")
    b = ve.render_shot(shots_mp4, shot, dests=[_dest(tmp_path, "mid")], policy="middle")

    for result in (a, b):
        assert len(result.written) == 1
        assert 1200 <= result.written[0].timestamp_ms <= 2400
        red, green, blue = frame_colour(result.written[0].path)
        assert green > red and green > blue  # shot 1 is the green one


@pytest.mark.skipif(not ve.capabilities()["deinterlace"], reason="imageio-ffmpeg is not installed")
def test_the_bwdif_path_writes_the_same_shot_as_the_cv2_path(shots_mp4, tmp_path):
    """The two decoders must agree about *which* frames they are looking at.
    imageio-ffmpeg yields RGB where cv2 yields BGR, and the flip is normalised
    at one boundary; if that were missed, the colours here would swap."""
    shot = ve.Shot(index=1, start_ms=1200, end_ms=2400)
    plain = ve.render_shot(shots_mp4, shot, dests=[_dest(tmp_path, "p")])
    woven = ve.render_shot(shots_mp4, shot, dests=[_dest(tmp_path, "w")], deinterlace="bwdif")

    assert len(woven.written) == 1
    red, green, blue = frame_colour(woven.written[0].path)
    assert green > red and green > blue
    assert frame_colour(plain.written[0].path)[1] > 150


def test_the_deinterlace_path_refuses_when_ffmpeg_is_absent(shots_mp4, tmp_path, monkeypatch):
    monkeypatch.setattr(
        ve, "capabilities",
        lambda: {"shot_detection": True, "deinterlace": False,
                 "scenedetect_version": None, "ffmpeg_version": None},
    )
    shot = ve.Shot(index=0, start_ms=0, end_ms=1200)
    with pytest.raises(ve.ExtractionUnavailable):
        ve.render_shot(shots_mp4, shot, dests=[_dest(tmp_path, "x")], deinterlace="bwdif")


# ---------------------------------------------------------------------------
# Sampling geometry
# ---------------------------------------------------------------------------


def test_sample_positions_are_inset_from_both_ends():
    """Frame 0 is very often a black leader and the last frame very often a
    fade, so neither boundary is ever sampled."""
    pos = ve.sample_positions(duration_ms=10_000, samples=4)
    assert pos == [1250.0, 3750.0, 6250.0, 8750.0]


def test_sample_positions_respect_trims():
    # 2000..8000 is the kept span; two samples inset by half of the 3000 ms step.
    pos = ve.sample_positions(duration_ms=10_000, samples=2, trim_start_ms=2000, trim_end_ms=2000)
    assert pos == [3500.0, 6500.0]


def test_sample_positions_without_a_duration_walk_the_head():
    assert ve.sample_positions(duration_ms=None, samples=3) == [0.0, 1000.0, 2000.0]
