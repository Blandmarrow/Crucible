"""backend/services/video_service.py — poster frames.

Three properties are load-bearing.

The seek target is the *midpoint* of the trimmed span, not frame 0. Frame 0 of a
real clip is very often a black leader or a fade-in, which makes for a poster
that identifies nothing; every card in the strip would look the same. The
fixture writes a different flat grey per frame, so a poster's pixel value says
which frame it came from and the test can prove the seek actually happened.

A poster failure is never an ingest failure. A video whose frames will not
decode must still list, play, rename and delete — `poster_path` simply stays
NULL and the UI draws its film glyph. Every path here returns rather than
raises, and the ingest tests upstream depend on that.

The write is temp-then-replace because two concurrent lazy backfills for one
video are ordinary: two strip cards, or a strip and a detail view. Without it
one request serves a half-written file the other is still writing.
"""

from pathlib import Path

import pytest
from PIL import Image as PilImage

from backend.services.video_service import generate_poster, probe_and_poster, probe_video
from backend.tests.conftest import mp4_bytes

pytest.importorskip("cv2", reason="opencv is not installed")


def _fixture(tmp_path: Path, name: str = "clip.mp4", **kwargs) -> Path:
    p = tmp_path / name
    p.write_bytes(mp4_bytes(**kwargs))
    return p


def _grey(poster: Path) -> int:
    """Mean channel value of the poster. The fixture paints frame `i` a flat
    grey of `(i * 5) % 255`, so this reads back which frame was captured."""
    with PilImage.open(poster) as img:
        px = img.convert("RGB").resize((1, 1)).getpixel((0, 0))
    return sum(px) // 3


def test_poster_is_written_as_webp(tmp_path):
    src = _fixture(tmp_path)
    dest = tmp_path / "thumbnails" / "clip.webp"

    assert generate_poster(src, dest, duration_ms=1000) is True
    with PilImage.open(dest) as img:
        assert img.format == "WEBP"


def test_the_poster_directory_is_created(tmp_path):
    """Phase 0 computed videos/thumbnails/ for stem globbing but never created
    it, so the first write has to."""
    src = _fixture(tmp_path)
    dest = tmp_path / "videos" / "thumbnails" / "clip.webp"
    assert not dest.parent.exists()

    assert generate_poster(src, dest, duration_ms=1000) is True
    assert dest.exists()


def test_the_frame_comes_from_the_midpoint_not_frame_zero(tmp_path):
    """50 frames at 25 fps: frame 0 is grey 0, the midpoint frame ~25 is grey
    ~125. A poster near 0 means the seek was skipped."""
    src = _fixture(tmp_path, frames=50, fps=25.0)
    dest = tmp_path / "t" / "clip.webp"

    assert generate_poster(src, dest, duration_ms=2000) is True
    assert _grey(dest) == pytest.approx(125, abs=20)


def test_trims_move_the_midpoint(tmp_path):
    """Trims are all 0 until frame extraction writes them, but they are threaded
    through now so a video whose trim points are set later re-posters onto a
    frame that is actually inside the kept range. Trimming the first half puts
    the midpoint at frame ~37, grey ~185."""
    src = _fixture(tmp_path, frames=50, fps=25.0)
    dest = tmp_path / "t" / "clip.webp"

    assert generate_poster(src, dest, duration_ms=2000, trim_start_ms=1000) is True
    assert _grey(dest) == pytest.approx(185, abs=20)


def test_an_unknown_duration_falls_back_to_the_first_frame(tmp_path):
    """duration_ms is NULL for containers with a poisoned frame count. There is
    no midpoint to seek to, so frame 0 is the only honest choice — and it must
    still produce a poster rather than nothing."""
    src = _fixture(tmp_path, frames=50, fps=25.0)
    dest = tmp_path / "t" / "clip.webp"

    assert generate_poster(src, dest, duration_ms=None) is True
    assert _grey(dest) == pytest.approx(0, abs=20)


def test_a_seek_past_the_end_falls_back_to_the_first_frame(tmp_path):
    """The fallback ladder's real case: a header whose duration overshoots the
    stream. The seek read returns nothing and the rewind rescues it."""
    src = _fixture(tmp_path, frames=50, fps=25.0)
    dest = tmp_path / "t" / "clip.webp"

    assert generate_poster(src, dest, duration_ms=10_000_000) is True
    assert dest.exists()


def test_an_undecodable_file_yields_no_poster_and_no_exception(tmp_path):
    """False, not a raise: the ingest paths call this inside the same executor
    hop as the probe, and a poster must never fail an import."""
    src = tmp_path / "garbage.mp4"
    src.write_bytes(b"not a video at all" * 64)
    dest = tmp_path / "t" / "garbage.webp"

    assert generate_poster(src, dest, duration_ms=1000) is False
    assert not dest.exists()


def test_the_poster_is_downscaled_and_keeps_its_aspect_ratio(tmp_path):
    src = _fixture(tmp_path, frames=10, size=(1024, 768))
    dest = tmp_path / "t" / "clip.webp"

    assert generate_poster(src, dest, duration_ms=400, size=512) is True
    with PilImage.open(dest) as img:
        assert img.size == (512, 384)


def test_a_small_video_is_not_upscaled(tmp_path):
    src = _fixture(tmp_path, frames=10, size=(64, 48))
    dest = tmp_path / "t" / "clip.webp"

    generate_poster(src, dest, duration_ms=400, size=512)
    with PilImage.open(dest) as img:
        assert img.size == (64, 48)


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    src = _fixture(tmp_path)
    dest = tmp_path / "t" / "clip.webp"

    generate_poster(src, dest, duration_ms=1000)

    assert [p.name for p in dest.parent.iterdir()] == ["clip.webp"]


def test_replacing_an_existing_poster_never_leaves_a_partial_file(tmp_path):
    """The second write goes to a temp name and lands with os.replace, so a
    reader either sees the whole old poster or the whole new one."""
    src = _fixture(tmp_path, frames=50, fps=25.0)
    dest = tmp_path / "t" / "clip.webp"

    generate_poster(src, dest, duration_ms=None)          # frame 0, grey ~0
    generate_poster(src, dest, duration_ms=2000)          # midpoint, grey ~125

    assert _grey(dest) == pytest.approx(125, abs=20)
    assert [p.name for p in dest.parent.iterdir()] == ["clip.webp"]


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    """The atomic-write tests above only cover the success path. A save that
    raises must take its temp with it: the temp sits in the poster directory, so
    a survivor is a `.tmp` file the next `thumbnails/*.webp` stem scan would see.

    `generate_poster` imports PIL *inside the function*, so there is no module
    attribute to patch — the class method is the hook. Distinct from
    `test_probe_and_poster_swallows_a_poster_failure` below, which is about the
    caller and asserts nothing about what is left on disk.
    """
    src = _fixture(tmp_path)
    dest = tmp_path / "t" / "clip.webp"

    def boom(self, *a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(PilImage.Image, "save", boom)

    with pytest.raises(OSError):
        generate_poster(src, dest, duration_ms=1000)

    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []


def test_probe_and_poster_returns_the_path_only_on_success(tmp_path):
    src = _fixture(tmp_path)
    dest = tmp_path / "t" / "clip.webp"

    info, poster = probe_and_poster(src, dest)

    assert poster == str(dest)
    assert info == probe_video(src)


def test_probe_and_poster_swallows_a_poster_failure(tmp_path, monkeypatch):
    """An unwritable poster directory, a full disk, a PIL error — the probe
    result still comes back and the row is created with poster_path NULL."""
    from backend.services import video_service

    src = _fixture(tmp_path)
    monkeypatch.setattr(
        video_service, "generate_poster", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    )

    info, poster = probe_and_poster(src, tmp_path / "t" / "clip.webp")

    assert poster is None
    assert info["width"] == 64
