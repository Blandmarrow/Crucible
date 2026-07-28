"""backend/services/video_service.py — header-only metadata and the ingest gate.

Two properties carry real weight here.

`isOpened()` is the *only* thing standing between an undecodable file and a
`Video` row that points at garbage. It is used instead of a suffix check because
a `.mp4` extension proves nothing about the bytes.

The duration guard is not defensive padding. A matroska written to a
non-seekable pipe — no duration, no cues, which is what stream-ripped and
partially-copied files look like — reports `CAP_PROP_FRAME_COUNT` as
-230584300921369408 while fps, dimensions and codec stay correct. A naive
`frames / fps` turns that into a duration of -9.2e15 seconds, which then renders
in the UI and gets stored on the row. NULL is the only honest answer.
"""

from pathlib import Path

import pytest

from backend.services.video_service import UnreadableVideoError, probe_video
from backend.tests.conftest import mp4_bytes

pytest.importorskip("cv2", reason="opencv is not installed")


def test_probe_reads_dimensions_fps_and_codec(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(mp4_bytes(frames=50, size=(64, 48), fps=25.0))

    info = probe_video(p)

    assert info["width"] == 64
    assert info["height"] == 48
    assert info["fps"] == pytest.approx(25.0)
    assert info["codec"]  # a 4-char FOURCC, whatever the writer chose
    assert info["file_size_bytes"] == p.stat().st_size


def test_duration_comes_from_frames_over_fps(tmp_path):
    """50 frames at 25 fps is 2 s. Exact, because the fixture writes exactly
    that many frames — a tolerance here would hide an off-by-one-frame error."""
    p = tmp_path / "clip.mp4"
    p.write_bytes(mp4_bytes(frames=50, fps=25.0))

    assert probe_video(p)["duration_ms"] == 2000


class _PoisonedCapture:
    """Stands in for a matroska with no duration and no cues: every header field
    is correct except the frame count, which is a huge negative number."""

    POISONED_FRAME_COUNT = -230584300921369408

    def __init__(self, frame_count):
        self._frame_count = frame_count

    def isOpened(self):
        return True

    def get(self, prop):
        import cv2

        return {
            cv2.CAP_PROP_FPS: 25.0,
            cv2.CAP_PROP_FRAME_WIDTH: 1920.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 1080.0,
            cv2.CAP_PROP_FRAME_COUNT: float(self._frame_count),
            cv2.CAP_PROP_FOURCC: 0.0,
        }.get(prop, 0.0)

    def release(self):
        pass


@pytest.mark.parametrize(
    "frame_count",
    [_PoisonedCapture.POISONED_FRAME_COUNT, 0, -1, 10**12],
    ids=["non-seekable-matroska", "zero", "negative", "absurdly-large"],
)
def test_untrustworthy_frame_counts_yield_no_duration(tmp_path, monkeypatch, frame_count):
    """duration_ms is None — never 0, never a negative — while the fields that
    were still correct survive."""
    import cv2

    p = tmp_path / "poisoned.mkv"
    p.write_bytes(b"\x00" * 128)
    monkeypatch.setattr(cv2, "VideoCapture", lambda _path: _PoisonedCapture(frame_count))

    info = probe_video(p)

    assert info["duration_ms"] is None
    assert info["width"] == 1920
    assert info["height"] == 1080
    assert info["fps"] == pytest.approx(25.0)
    # The same capture reports CAP_PROP_FOURCC = 0, which decodes to four NUL
    # bytes — a truthy string that would be stored on the row and echoed back
    # through codec_label. NULL is the only renderable answer.
    assert info["codec"] is None


def test_zero_fps_yields_no_duration(tmp_path, monkeypatch):
    """A plausible frame count divided by a zero fps would raise, not mislead —
    but the guard has to cover it or ingest crashes on such a file."""
    import cv2

    class _NoFps(_PoisonedCapture):
        def get(self, prop):
            if prop == cv2.CAP_PROP_FPS:
                return 0.0
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return 500.0
            return super().get(prop)

    p = tmp_path / "nofps.mkv"
    p.write_bytes(b"\x00" * 128)
    monkeypatch.setattr(cv2, "VideoCapture", lambda _path: _NoFps(500))

    info = probe_video(p)
    assert info["duration_ms"] is None
    assert info["fps"] is None


def test_undecodable_files_are_rejected(tmp_path):
    """The ingest gate. Each of these has a video extension and none is a video;
    a suffix check would have accepted all three."""
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")

    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"not a video at all" * 64)

    truncated = tmp_path / "truncated.mp4"
    full = mp4_bytes(frames=50)
    truncated.write_bytes(full[: len(full) // 3])

    for p in (empty, garbage, truncated):
        with pytest.raises(UnreadableVideoError):
            probe_video(p)


@pytest.mark.parametrize(
    "module", ["backend.services.video_service", "backend.services.video_extract"]
)
def test_heavy_decoders_are_imported_lazily(module):
    """cv2 costs ~1s to import, and scenedetect/imageio-ffmpeg are optional, so
    none of them may be pulled in at module scope — the same convention as
    backend/ml/technical_scorer.py.

    Checked by parsing rather than by splitting the source at the first function
    (which the original form of this test did): every import here lives inside
    some function body, and a text split cannot tell an indented lazy import
    above the split point from a module-level one below it.
    """
    import ast
    import importlib

    source = Path(importlib.import_module(module).__file__).read_text()
    top_level = {
        alias.name.split(".")[0]
        for node in ast.parse(source).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in getattr(node, "names", [])
    }
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    assert not top_level & {"cv2", "scenedetect", "imageio_ffmpeg", "PIL"}
