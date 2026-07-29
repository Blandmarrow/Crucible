"""Video metadata and poster frames.

Division of labour for the video arc: **OpenCV** reads headers, measures
durations and samples frames; the ffmpeg binary runs `bwdif` deinterlacing,
which OpenCV cannot express, and nothing else. Cropping is a numpy slice on an
already-decoded frame, so the progressive path never spawns a subprocess. See
`backend/services/video_extract.py` and `docs/dev/video-shots.md`.

This module is the ingest entry point (`probe_video`, header-only, no decode
pass), the duration search (`measure_duration_ms`, seeks rather than decodes)
and the poster cutter (`generate_poster`).

`imageio_ffmpeg.count_frames_and_secs()` is deliberately not used as a fallback
for a missing duration: its implementation is a full decode pass
(`-i … -vf null -f null -`) and its own docstring warns it is slow and not
certainly exact. `measure_duration_ms` brackets the end with ~30 seeks instead.
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


def apply_orientation(cap) -> None:
    """Make a `cv2.VideoCapture` honour the container's rotation metadata.

    Must be called on **every** capture this codebase opens — probe, poster and
    extraction alike. cv2 and ffmpeg disagree by default: ffmpeg autorotates, cv2
    does not, so the two decode paths (the cv2 one and the `bwdif` one) would
    hand the same crop rect frames in different orientations, and a poster would
    disagree with the frames extracted from the same file. Turning cv2's
    autorotate on is the cheaper half of making them agree; ffmpeg keeps its
    default. Verified present in cv2 5.0.0; guarded anyway, because the property
    is a no-op on backends that do not implement it and this must never be the
    reason a video fails to open.
    """
    import cv2

    try:
        cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    except Exception:  # noqa: BLE001 — an unsupported property is not an error
        logger.debug("CAP_PROP_ORIENTATION_AUTO not supported by this backend")


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
        apply_orientation(cap)

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


# A duration search never runs past this. 24 h is far beyond anything a user
# would curate frames from, and a stream whose header lies without bound must
# terminate the search rather than double forever.
MEASURE_MAX_MS = 24 * 60 * 60 * 1000
# Bisection stops one frame period from the answer, or at this floor for a file
# that reports no usable frame rate. The result feeds progress percentages,
# sample positions and the tail trim — not an edit decision list.
MEASURE_TOLERANCE_FLOOR_MS = 40.0
MEASURE_MAX_PROBES = 40
# Sequential grabs (no seek) used to walk the last bracket down to the real
# final frame. Bounded so a container that never reports EOF cannot spin.
MEASURE_TAIL_GRABS = 64
# The seekability verdict is only trusted once the probe has grown past this.
# See the comment at the check itself for why a floor is required at all.
NON_SEEKABLE_PROBE_FLOOR_MS = 2000.0


def measure_duration_ms(
    path: str | Path,
    *,
    hint_ms: int | None = None,
    max_ms: int = MEASURE_MAX_MS,
) -> int | None:
    """Find a video's real duration by seeking, not by decoding it. Blocking.

    `probe_video` leaves `duration_ms` NULL for any container whose frame count
    is missing or poisoned — matroska written to a non-seekable pipe being the
    common case. NULL is honest but it breaks everything downstream of it: no
    percentage, no tail trim, no sample positions. So extraction measures.

    Exponential probing to bracket the end, then bisection, on
    `CAP_PROP_POS_MSEC` + `cap.grab()`. About thirty grabs and no full decode
    pass — `imageio_ffmpeg.count_frames_and_secs()` is the alternative and is a
    complete `-f null -` decode, which its own docstring warns is slow.

    Returns None for a file that will not open **or one that will not seek**:
    a non-seekable stream ignores the `set()` and answers every probe with the
    next sequential frame, which would otherwise read as "reachable" forever.
    The caller must then fall back to head-only samples and indeterminate
    progress rather than trusting a fabricated number.
    """
    import cv2

    def reach(cap, ms: float) -> float | None:
        """Seek to `ms` and grab. Returns the reached position, or None."""
        cap.set(cv2.CAP_PROP_POS_MSEC, float(ms))
        before = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
        if not cap.grab():
            return None
        return before

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        apply_orientation(cap)

        # `reach` reports the *start* timestamp of the frame it grabbed, so the
        # duration is one frame period past the last reachable one. Without this
        # the answer is consistently short by 1/fps, which is invisible on a
        # feature but wrong on a 25-frame clip.
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        period = 1000.0 / fps if fps > 0 else 0.0
        tolerance = max(period, MEASURE_TOLERANCE_FLOOR_MS)

        best = reach(cap, 0.0)
        if best is None:
            return None  # nothing decodes at all

        probes = 1
        lo = 0.0
        hi: float | None = None
        probe = float(hint_ms) if hint_ms and hint_ms > 0 else 1000.0

        while probes < MEASURE_MAX_PROBES and probe <= max_ms:
            probes += 1
            pos = reach(cap, probe)
            if pos is None:
                hi = probe
                break
            if probe >= NON_SEEKABLE_PROBE_FLOOR_MS and pos < probe * 0.5:
                # The seek did not land anywhere near where it was asked to.
                # That is a non-seekable stream answering with the next
                # sequential frame, not a short video.
                #
                # The floor is what makes this safe. A non-seekable stream
                # advances one frame per grab while `probe` doubles, so the gap
                # becomes unmistakable within a few rounds — but early on, when
                # `probe` is still tens of milliseconds, a *correct* seek to
                # frame 0 also sits below half of it, and without the floor a
                # small `hint_ms` makes every seekable file measure as None.
                return None
            best = max(best, pos)
            lo = probe
            probe *= 2
        if hi is None:
            return None  # ran out of probes, or the file outlasts max_ms

        while hi - lo > tolerance and probes < MEASURE_MAX_PROBES:
            probes += 1
            mid = (lo + hi) / 2.0
            pos = reach(cap, mid)
            if pos is None:
                hi = mid
            else:
                best = max(best, pos)
                lo = mid

        # Bisection leaves the answer up to `tolerance` short, because it stops
        # as soon as the bracket is tight rather than landing on the last frame.
        # Close it by walking sequentially from the last known-good position:
        # these are grabs with no seek, so a handful of them costs nothing, and
        # they make the result exact rather than within-a-frame-or-two.
        if reach(cap, lo) is not None:
            for _ in range(MEASURE_TAIL_GRABS):
                ts = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
                if not cap.grab():
                    break
                best = max(best, ts)
    finally:
        cap.release()

    # `hi` is deliberately *not* used as a ceiling here. It is the first position
    # at which no frame *starts*, i.e. one epsilon past the last frame's own
    # timestamp — not the end of the stream, which is a whole frame period later.
    # Clamping to it returns a duration exactly 1/fps short on every file.
    return int(round(best + period))


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
    seek plus one read — no filter chain. Only the `bwdif` deinterlace path in
    `video_extract.py` goes to the ffmpeg binary.

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
        apply_orientation(cap)

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
