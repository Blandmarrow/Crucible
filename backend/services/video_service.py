"""Video metadata extraction.

Division of labour for the video arc: **OpenCV** reads headers and samples
frames; the ffmpeg binary (a later phase) runs the extraction filter chain
(`bwdif` deinterlace, crop) that OpenCV cannot express. This module is the
OpenCV half's ingest entry point — header-only, no decode pass.

`imageio_ffmpeg.count_frames_and_secs()` is deliberately not used as a fallback
for a missing duration: its implementation is a full decode pass
(`-i … -vf null -f null -`) and its own docstring warns it is slow and not
certainly exact. The probe step of frame extraction backfills a true duration
later, since it is already decoding.
"""

import logging
from pathlib import Path

from backend.media_types import fourcc_to_code

logger = logging.getLogger(__name__)


class UnreadableVideoError(Exception):
    """cv2 could not open the file — truncated, zero-byte, or not a video."""


def probe_video(path: str | Path) -> dict:
    """Header-only metadata for one video. Blocking — call in an executor.

    Returns `{width, height, fps, codec, duration_ms, file_size_bytes}`.
    Raises `UnreadableVideoError` when the file cannot be opened, which is the
    ingest gate: `isOpened()` returns False for zero-byte, truncated and
    non-video payloads, and True only for something we can actually decode.
    """
    # Lazy import: cv2 costs ~1s at module scope. Mirrors the convention in
    # backend/ml/technical_scorer.py and the SAM predictors.
    import cv2

    p = Path(path)
    cap = cv2.VideoCapture(str(p))
    try:
        if not cap.isOpened():
            raise UnreadableVideoError(f"Could not decode video: {p.name}")

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) or None
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) or None
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)

        # NULL for a container that reported no usable code; media_types owns the
        # decode so the degenerate 0 / -1 values cannot reach the row.
        codec = fourcc_to_code(int(cap.get(cv2.CAP_PROP_FOURCC) or 0))
    finally:
        cap.release()

    # The frame count is the one header field that can be actively poisoned, and
    # the guard is not defensive padding. A matroska written to a non-seekable
    # pipe — no duration, no cues, i.e. what stream-ripped and partially-copied
    # files look like — reports CAP_PROP_FRAME_COUNT = -230584300921369408,
    # which a naive `frames / fps` turns into a duration of -9.2e15 seconds.
    # fps, dimensions and codec are all still correct for such a file; only the
    # count is garbage. Parsing the `ffmpeg -i` banner is no rescue either — it
    # prints "Duration: N/A" for exactly that case. NULL renders as "unknown".
    duration_ms = int(frames / fps * 1000) if (fps > 0 and 0 < frames < 1e9) else None

    try:
        file_size_bytes = p.stat().st_size
    except OSError:
        file_size_bytes = None

    return {
        "width": width,
        "height": height,
        "fps": fps or None,
        "codec": codec,
        "duration_ms": duration_ms,
        "file_size_bytes": file_size_bytes,
    }
