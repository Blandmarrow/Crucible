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
import os
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

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


def generate_poster(
    video_path: str | Path,
    poster_path: str | Path,
    *,
    duration_ms: int | None = None,
    trim_start_ms: int = 0,
    trim_end_ms: int = 0,
    size: int = 512,
) -> bool:
    """Write one WebP poster frame for a video. Blocking — call in an executor.

    Returns True when a poster was written, False when nothing decodable came
    back. A poster is a nicety: a video whose frames will not decode must still
    list, play, rename and delete, so every failure path here returns False and
    leaves `poster_path` NULL for the caller. The UI draws a film glyph instead.

    OpenCV rather than ffmpeg because it is already a dependency and this is one
    seek plus one read — no filter chain. Frame extraction (a later phase) needs
    `bwdif`/crop and goes to the ffmpeg binary instead.

    The seek target is the midpoint of the *trimmed* span, so a video whose trim
    points are set later re-posters onto a frame that is actually in the kept
    range. Trims are all 0 today, which makes this the plain midpoint.
    """
    import cv2
    from PIL import Image as PILImage

    src = Path(video_path)
    dest = Path(poster_path)

    seek_ms = 0.0
    if duration_ms and duration_ms > 0:
        span = duration_ms - trim_start_ms - trim_end_ms
        if span > 0:
            seek_ms = trim_start_ms + span / 2

    cap = cv2.VideoCapture(str(src))
    try:
        if not cap.isOpened():
            logger.info("poster: could not open %s", src)
            return False

        frame = None
        if seek_ms > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, seek_ms)
            ok, candidate = cap.read()
            if ok and candidate is not None:
                frame = candidate
        if frame is None:
            # Fallback ladder: a midpoint seek fails on a container with no
            # index, and on one whose reported duration overshoots the real
            # stream. Rewinding to 0 costs one more read and rescues both.
            cap.set(cv2.CAP_PROP_POS_MSEC, 0)
            ok, candidate = cap.read()
            if ok and candidate is not None:
                frame = candidate
        if frame is None:
            logger.info("poster: no decodable frame in %s", src)
            return False
    finally:
        cap.release()

    # No ImageOps.exif_transpose here, and that is not a violation of the
    # "always transpose first" invariant — it is outside its scope. The input is
    # a decoded BGR ndarray handed over by cv2, not a file with an EXIF block;
    # container rotation metadata never reaches this array.
    img = PILImage.fromarray(frame[:, :, ::-1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name in the destination directory (same filesystem, so the
    # replace is atomic), matching version_service._store_object. Two concurrent
    # lazy backfills for one video would otherwise have one serving a
    # half-written file the other is still writing.
    tmp = dest.parent / f".{dest.name}.{uuid4().hex}.tmp"
    try:
        img.thumbnail((size, size), PILImage.Resampling.LANCZOS)
        img.save(tmp, "WEBP", quality=85)
        os.replace(tmp, dest)
        return True
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        img.close()


def probe_and_poster(video_path: str | Path, poster_path: str | Path) -> tuple[dict, str | None]:
    """Probe a video and write its poster in one blocking call.

    Shared by all three ingest paths (upload, folder import, rescan) so that a
    poster failure can never fail an ingest: the probe result is returned
    regardless and a poster that could not be written just leaves the second
    element None, i.e. `Video.poster_path` NULL. `UnreadableVideoError` from the
    probe still propagates — that one *is* the ingest gate.
    """
    info = probe_video(video_path)
    try:
        ok = generate_poster(video_path, poster_path, duration_ms=info["duration_ms"])
    except Exception:
        logger.warning("poster: generation failed for %s", video_path, exc_info=True)
        ok = False
    return info, (str(poster_path) if ok else None)


def claimed_poster_stems(
    rows: Sequence[tuple[str, str, str | None]],
    poster_dir: Path,
    exclude_id: str | None = None,
) -> set[str]:
    """Every poster stem a sibling video already owns. `rows` are (id, filename, poster_path).

    The single source of truth for "which poster names are taken", shared by the
    sites that pick a video filename (upload, folder import, rename — they feed
    it to `unique_filename_with_thumb`) and the sites that adopt one (rescan, the
    poster backfill — they feed it to `unique_poster_path`).

    Three terms, all load-bearing:

    - **`poster_path` stems** — what is actually claimed. Necessary because a
      poster stem is *not* guaranteed to equal its video's stem: rescan meets two
      containers sharing a stem and disambiguates the poster rather than renaming
      the user's file, so a row can hold `clip.mkv` with `clip_001.webp`.
    - **`filename` stems** — the conservative reservation for a poster that has
      not been cut yet. A row whose generation failed, or one created before
      posters existed, has nothing on disk and no `poster_path` to glob, but will
      claim its own stem the first time anything views it.
    - **on-disk `*.webp`** — covers a file whose row is gone or was never written.

    Pure: callers run the query, matching `licenses.materialize_by_source`. Pass
    `exclude_id` to drop the row being renamed so it does not block itself.
    """
    stems: set[str] = set()
    for video_id, filename, poster_path in rows:
        if exclude_id is not None and video_id == exclude_id:
            continue
        stems.add(Path(filename).stem)
        if poster_path:
            stems.add(Path(poster_path).stem)
    if poster_dir.exists():
        stems |= {p.stem for p in poster_dir.glob("*.webp")}
    return stems
