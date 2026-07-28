"""Frame extraction: the decode half. cv2, PySceneDetect and (rarely) ffmpeg.

Everything here is blocking and executor-only. The three heavy imports — `cv2`,
`scenedetect`, `imageio_ffmpeg` — are all lazy *inside* the functions, matching
`video_service.py`: cv2 alone costs about a second at module scope, and two of
the three are optional dependencies whose absence must degrade the feature
rather than break the import graph.

**Division of labour.** OpenCV decodes; numpy crops. The roadmap originally
assigned the whole filter chain to the ffmpeg binary, but a crop is a numpy
slice on a frame cv2 has already decoded for shot detection, so the progressive
path — the overwhelming majority of sources — never spawns a subprocess.
`imageio_ffmpeg` earns its place for exactly one thing: `bwdif`, which OpenCV
cannot express.

**Why this is not in `video_service.py`.** `docs/dev/video-decode.md` once
promised extraction would join the probe and the poster there; it should not. The
judgement calls live in `video_frames.py` and need no video at all, this module
needs mp4 fixtures and an optional dependency, and `video_service` is already
covered by its own tests. One module would make the fast tests hostage to the
slow ones. See `docs/dev/video-extract.md`.

**RSS discipline** runs through every loop below. A decoded 4K frame is 24.9 MB;
twelve are ~300 MB. Nothing here ever builds a `list[np.ndarray]` and maps over
it afterwards — everything a frame contributes is extracted inside the iteration
and the array released before the next seek. Same failure class as the
"close PIL Images after preprocessing" invariant in CLAUDE.md.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from backend.services import video_frames as vf
from backend.services.video_service import UnreadableVideoError, apply_orientation
from backend.utils import image_save_kwargs, normalize_image_format

logger = logging.getLogger(__name__)


class ExtractionUnavailable(RuntimeError):
    """An optional dependency this request needs is not installed.

    Raised at the *request* boundary and mapped to 503 with actionable text.
    The alternative — letting the import fail inside the worker — produces a job
    that dies with an ImportError five minutes after the user pressed the button.
    `capabilities()` exists so the UI can prevent the request entirely.
    """


@dataclass(slots=True)
class Shot:
    index: int
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(slots=True)
class Progress:
    """Shared, mutable, read from the event loop while the executor writes it.

    `frames_read` is a plain int and `cancel` a plain bool on purpose: both are
    single-word attribute assignments, which the GIL makes atomic, so neither
    needs a lock and neither can tear. Do **not** replace this with a poll of
    `video.frame_number` — that is a live `cv2.VideoCapture.get` on a handle
    scenedetect's decode thread is concurrently `grab()`ing, i.e. a data race on
    a C++ object.
    """
    frames_read: int = 0
    total_frames: int = 0
    cancel: bool = False
    # `SceneManager.stop()`, published by `detect_shots` so the event loop can
    # call it. Documented thread-safe. Belt-and-braces alongside the stream's
    # cancel check, which is what actually ends the run.
    stop_hook: object | None = None


@dataclass(slots=True)
class WrittenFrame:
    shot_index: int
    pick: int
    timestamp_ms: int
    path: str
    thumb_path: str | None = None


@dataclass(slots=True)
class ShotRenderResult:
    written: list[WrittenFrame] = field(default_factory=list)
    failed: int = 0


# A shot list of exactly one entry longer than this is not a shot list — it is a
# detector that found nothing, and one frame out of two hours is not a result.
SINGLE_SHOT_FALLBACK_MS = 120_000
# Target window for the uniform sampler, used both as the shot-detection
# fallback and as the "one enormous shot" rescue.
UNIFORM_INTERVAL_MS = 5_000

PROBE_MAX_SAMPLES = 12
PROBE_MAX_PAYLOAD_BYTES = 2_000_000
PROBE_PREVIEW_EDGE = 640
PROBE_JPEG_QUALITY = 72
# Consecutive frames read in one seek for the telecine pass. Five is the pattern
# period; twenty gives four cycles, enough for a lag-5 autocorrelation to mean
# something without a second noticeable pause in the modal.
PROBE_TELECINE_RUN = 20

# Candidate frames for the sharpest-in-window pick are taken close together, not
# spread across the shot: the point is the sharpest frame *at the moment being
# sampled*, and a candidate two seconds away is a different composition.
CANDIDATE_SPACING_MS = 120.0
# Bound on the sequential grab walk inside one candidate window, so a container
# reporting a stuck timestamp cannot spin.
CANDIDATE_WALK_LIMIT = 400


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def capabilities() -> dict:
    """What this install can actually do. Rides on the probe response, and is
    also served on its own by `GET /videos/capabilities` — a video that will not
    probe still extracts, so the modal needs an answer that does not depend on
    one. Pure and cheap enough to be a request-path call either way. Both entries
    are False on a fresh checkout until
    `manage.sh update` installs the two optional dependencies, and that is
    useful — the degraded branches run for real in CI rather than only in
    theory.
    """
    caps: dict = {
        "shot_detection": False,
        "deinterlace": False,
        "scenedetect_version": None,
        "ffmpeg_version": None,
    }
    try:
        import scenedetect

        caps["shot_detection"] = True
        caps["scenedetect_version"] = getattr(scenedetect, "__version__", None)
    except Exception:  # noqa: BLE001 — a broken optional dep reads as absent
        logger.debug("scenedetect unavailable", exc_info=True)
    try:
        import imageio_ffmpeg

        # Resolving the binary is the real test — the package installs cleanly
        # on platforms it ships no ffmpeg for.
        if imageio_ffmpeg.get_ffmpeg_exe():
            caps["deinterlace"] = True
            caps["ffmpeg_version"] = imageio_ffmpeg.get_ffmpeg_version()
    except Exception:  # noqa: BLE001
        logger.debug("imageio-ffmpeg unavailable", exc_info=True)
    return caps


def require_deinterlace() -> None:
    """Raise `ExtractionUnavailable` unless the bwdif path can run."""
    if not capabilities()["deinterlace"]:
        raise ExtractionUnavailable(
            "Deinterlacing needs the imageio-ffmpeg package, which is not installed. "
            "Run the update command (manage.sh update / manage.ps1 update), or extract "
            "with deinterlacing switched off."
        )


# ---------------------------------------------------------------------------
# Sampling geometry
# ---------------------------------------------------------------------------


def sample_positions(
    *,
    duration_ms: int | None,
    samples: int,
    trim_start_ms: int = 0,
    trim_end_ms: int = 0,
) -> list[float]:
    """Evenly-spaced sample timestamps inside the trimmed span.

    With no duration — an unmeasurable, non-seekable stream — this degrades to
    head-only positions one second apart rather than guessing a span. The caller
    must then say so: no percentage, no tail trim, an explicit warning.
    """
    samples = max(1, samples)
    if not duration_ms or duration_ms <= 0:
        return [i * 1000.0 for i in range(samples)]
    start = max(0, trim_start_ms)
    end = max(start + 1, duration_ms - max(0, trim_end_ms))
    span = end - start
    # Inset by half a step so neither the first nor the last sample sits on a
    # boundary — frame 0 is very often a black leader, and the final frame is
    # very often a fade.
    step = span / samples
    return [start + step * (i + 0.5) for i in range(samples)]


def _shot_windows(shot: Shot, frames_per_shot: int) -> list[float]:
    """Centre timestamp of each sub-window a shot is divided into."""
    n = max(1, frames_per_shot)
    span = max(shot.end_ms - shot.start_ms, 1)
    step = span / n
    return [shot.start_ms + step * (i + 0.5) for i in range(n)]


def _candidate_positions(centre_ms: float, count: int, lo_ms: float, hi_ms: float) -> list[float]:
    """`count` closely-spaced positions around `centre_ms`, clamped to the shot."""
    count = max(1, count)
    if count == 1:
        return [min(max(centre_ms, lo_ms), hi_ms)]
    spacing = min(CANDIDATE_SPACING_MS, max(hi_ms - lo_ms, 1.0) / count)
    half = spacing * (count - 1) / 2.0
    first = min(max(centre_ms - half, lo_ms), max(hi_ms - spacing * (count - 1), lo_ms))
    return [first + spacing * i for i in range(count)]


# ---------------------------------------------------------------------------
# Frame reading — the two decode paths behind one shape
# ---------------------------------------------------------------------------


def _read_positions_cv2(path: Path, positions: list[float]):
    """Yield `(timestamp_ms, bgr_frame)` for each ascending position.

    One seek to the first position, then a sequential grab walk, `retrieve()`ing
    only where a candidate falls. Repeated seeking is the obvious alternative and
    is worse here: candidates are ~120 ms apart, and a container that snaps seeks
    to keyframes would hand back the *same* frame five times, silently reducing
    the sharpest-of-five pick to a coin toss with one side.
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return
        apply_orientation(cap)
        cap.set(cv2.CAP_PROP_POS_MSEC, float(positions[0]))
        walked = 0
        for target in positions:
            hit = False
            while walked < CANDIDATE_WALK_LIMIT:
                ts = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
                if not cap.grab():
                    return
                walked += 1
                if ts >= target:
                    ok, frame = cap.retrieve()
                    if ok and frame is not None:
                        hit = True
                        yield ts, frame
                    break
            if not hit and walked >= CANDIDATE_WALK_LIMIT:
                return
    finally:
        cap.release()


def _read_positions_ffmpeg(path: Path, positions: list[float], *, deinterlace: str):
    """The `bwdif` path: one ffmpeg subprocess spanning the candidate window.

    `imageio_ffmpeg.read_frames` owns that subprocess, and a `break` out of the
    generator without closing it orphans the process — hence `contextlib.closing`
    around every use. It also yields **RGB** where cv2 yields BGR; the flip
    happens here so that exactly one boundary in the codebase knows about it and
    everything downstream sees BGR.
    """
    import imageio_ffmpeg

    start_s = max(0.0, positions[0] / 1000.0)
    span_s = max((positions[-1] - positions[0]) / 1000.0, 0.0) + 0.5
    gen = imageio_ffmpeg.read_frames(
        str(path),
        pix_fmt="rgb24",
        # -ss before -i is the fast (keyframe-then-decode) seek; ffmpeg
        # autorotates by default and we leave it that way, which is why cv2 gets
        # CAP_PROP_ORIENTATION_AUTO turned on to match.
        input_params=["-ss", f"{start_s:.3f}"],
        output_params=["-vf", f"{deinterlace}=mode=send_frame", "-t", f"{span_s:.3f}"],
    )
    with contextlib.closing(gen):
        meta = next(gen)
        width, height = meta["size"]
        fps = float(meta.get("fps") or 0.0) or 25.0
        period = 1000.0 / fps
        wanted = list(positions)
        for i, raw in enumerate(gen):
            if not wanted:
                break
            ts = positions[0] + i * period
            if ts + period / 2.0 < wanted[0]:
                continue
            wanted.pop(0)
            rgb = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
            yield ts, np.ascontiguousarray(rgb[:, :, ::-1])


def read_positions(path: Path, positions: list[float], *, deinterlace: str = ""):
    """Yield `(timestamp_ms, bgr_frame)` through whichever decoder is required."""
    if not positions:
        return
    if deinterlace:
        require_deinterlace()
        yield from _read_positions_ffmpeg(path, positions, deinterlace=deinterlace)
    else:
        yield from _read_positions_cv2(path, positions)


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def _encode_preview(frame_bgr: np.ndarray, *, max_edge: int, quality: int) -> str:
    """A `data:image/jpeg;base64,…` URL for one frame.

    Data URLs rather than temp files, deliberately. A temp file needs a serving
    endpoint, a cleanup sweep and a path-traversal guard, on a server with no
    authentication (see the memory note on this API) — three new surfaces to
    hold a preview that lives for as long as a modal is open.
    """
    import cv2

    h, w = frame_bgr.shape[:2]
    longest = max(h, w)
    if longest > max_edge:
        scale = max_edge / longest
        frame_bgr = cv2.resize(
            frame_bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA
        )
    ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def probe_samples(
    path: str | Path,
    *,
    duration_ms: int | None,
    samples: int = 8,
    max_edge: int = PROBE_PREVIEW_EDGE,
    jpeg_quality: int = PROBE_JPEG_QUALITY,
    trim_start_ms: int = 0,
    trim_end_ms: int = 0,
    max_payload_bytes: int = PROBE_MAX_PAYLOAD_BYTES,
    telecine_run: int = PROBE_TELECINE_RUN,
) -> dict:
    """Sample a video for the extraction modal's first step. Blocking.

    Measured at ~7.5 ms per seek-and-decode, which is why the endpoint on top of
    this is a plain request rather than a job.

    Every frame is consumed **inside** the loop: its two edge profiles, its
    combing ratio and its encoded preview are all taken and the array dropped
    before the next seek, so peak RSS is one frame plus a few hundred KB of
    strings rather than `samples` × 24.9 MB.

    Cropdetect and combing both run on the **full-resolution** frame. The preview
    is downscaled; the analysis must not be. Resampling averages adjacent rows
    together, which is precisely the field structure `combing_ratio` measures,
    and it would turn every interlaced source progressive.
    """
    import cv2

    path = Path(path)
    samples = max(1, min(samples, PROBE_MAX_SAMPLES))
    positions = sample_positions(
        duration_ms=duration_ms, samples=samples, trim_start_ms=trim_start_ms, trim_end_ms=trim_end_ms
    )

    out_samples: list[dict] = []
    warnings: list[str] = []
    samples_failed = 0
    truncated = False
    payload = 0

    acc_rows: np.ndarray | None = None
    acc_cols: np.ndarray | None = None
    per_sample_rects: list[vf.CropRect | None] = []
    combing: list[float] = []
    width = height = None

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError(f"Could not decode video: {path.name}")
        apply_orientation(cap)
        for ts in positions:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(ts))
            ok, frame = cap.read()
            if not ok or frame is None:
                # Broken tails are common; a failed seek must cost one sample,
                # not the whole probe.
                samples_failed += 1
                continue
            try:
                height, width = frame.shape[:2]
                rows, cols = vf.edge_profiles(frame)
                acc_rows = vf.merge_profiles(acc_rows, rows)
                acc_cols = vf.merge_profiles(acc_cols, cols)
                per_sample_rects.append(vf.crop_rect_from_profiles(rows, cols))
                combing.append(vf.combing_ratio(frame))
                url = _encode_preview(frame, max_edge=max_edge, quality=jpeg_quality)
            finally:
                del frame
            if not url:
                samples_failed += 1
                continue
            if payload + len(url) > max_payload_bytes:
                truncated = True
                break
            payload += len(url)
            out_samples.append({"timestamp_ms": int(round(ts)), "data_url": url})

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or None

        # Telecine needs *consecutive* frames, so it is its own short pass from a
        # single seek — the scattered samples above are seconds apart and a
        # period-5 pattern is invisible to them.
        telecine_series: list[float] = []
        if telecine_run > 0 and fps and (abs(fps - 29.97) < 0.2 or abs(fps - 30.0) < 0.2):
            mid = positions[len(positions) // 2] if positions else 0.0
            cap.set(cv2.CAP_PROP_POS_MSEC, float(mid))
            for _ in range(telecine_run):
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                try:
                    telecine_series.append(vf.combing_ratio(frame))
                finally:
                    del frame
    finally:
        cap.release()

    crop = None
    crop_confidence = 0.0
    if acc_rows is not None and acc_cols is not None:
        crop = vf.crop_rect_from_profiles(acc_rows, acc_cols)
        if crop is not None:
            crop = vf.clamp_crop(crop, width or 0, height or 0)
    if crop is not None:
        # Confidence is agreement, not certainty: what fraction of the samples
        # that saw *any* matte saw this one. A rect derived from a single
        # letterboxed shot in eight is exactly the case a user should override.
        voted = [r for r in per_sample_rects if r is not None]
        if voted:
            agree = sum(1 for r in voted if all(abs(a - b) <= 2 for a, b in zip(r, crop)))
            crop_confidence = round(agree / len(voted), 3)

    interlace, interlace_note = vf.interlace_from_series(combing, height=height, fps=fps)
    telecine, telecine_note = vf.telecine_from_series(telecine_series, fps=fps)
    for note in (interlace_note, telecine_note):
        if note:
            warnings.append(note)
    if samples_failed:
        warnings.append(f"{samples_failed} of {len(positions)} sample points could not be decoded")
    if truncated:
        warnings.append("Preview truncated to stay inside the response size budget")
    if not duration_ms:
        warnings.append(
            "This container reports no usable duration, so samples were taken from the "
            "head of the file only and the tail trim is unavailable"
        )

    return {
        "samples": out_samples,
        "crop": crop,
        "crop_confidence": crop_confidence,
        "interlace": interlace,
        "telecine": telecine,
        "samples_failed": samples_failed,
        "truncated": truncated,
        "warnings": warnings,
        "width": width,
        "height": height,
        "fps": fps,
    }


# ---------------------------------------------------------------------------
# Shot detection
# ---------------------------------------------------------------------------


def _uniform_shots(start_ms: float, end_ms: float, *, min_shot_ms: int, max_shots: int) -> list[Shot]:
    """Fixed-interval windows, the fallback for both no-detector and no-cuts."""
    span = max(end_ms - start_ms, 1.0)
    interval = max(float(UNIFORM_INTERVAL_MS), float(min_shot_ms), span / max(max_shots, 1))
    count = max(1, min(max_shots, int(math.ceil(span / interval))))
    step = span / count
    return [
        Shot(index=i, start_ms=int(start_ms + step * i), end_ms=int(start_ms + step * (i + 1)))
        for i in range(count)
    ]


def _counting_stream_class():
    """Build the delegating VideoStream subclass, lazily.

    Defined inside a function because the base class only exists once
    `scenedetect` is importable, and this module must import on a machine where
    it is not.
    """
    from scenedetect.video_stream import VideoStream

    class _CountingStream(VideoStream):
        """A pass-through `VideoStream` that counts frames and can cancel.

        `detect_scenes`'s own `callback=` fires only on cuts, which on a low-cut
        file means no progress at all for the whole run, and `show_progress` is
        tqdm on stderr. The decode thread's *only* contact with the stream is
        `read()`, so wrapping that gives exact per-frame progress with no shared
        state beyond one int.

        Cancellation returns `False` — a clean EOF — rather than raising.
        `detect_scenes` has a `finally` that drains its frame queue and joins the
        decode thread; an exception thrown through it can leave that daemon
        thread alive, and an orphaned daemon thread can crash interpreter
        shutdown.
        """

        def __init__(self, inner, progress: Progress):
            self._inner = inner
            self._progress = progress

        # -- the counting part ------------------------------------------------
        def read(self, *args, **kwargs):
            if self._progress.cancel:
                return False
            frame = self._inner.read(*args, **kwargs)
            if frame is False:
                return False
            self._progress.frames_read += 1
            return frame

        # -- everything else is delegation ------------------------------------
        def reset(self, *args, **kwargs):
            return self._inner.reset(*args, **kwargs)

        def seek(self, target):
            return self._inner.seek(target)

        @property
        def aspect_ratio(self):
            return self._inner.aspect_ratio

        @property
        def duration(self):
            return self._inner.duration

        @property
        def frame_number(self):
            return self._inner.frame_number

        @property
        def frame_rate(self):
            return self._inner.frame_rate

        @property
        def frame_size(self):
            return self._inner.frame_size

        @property
        def is_seekable(self):
            return self._inner.is_seekable

        @property
        def name(self):
            return self._inner.name

        @property
        def path(self):
            return self._inner.path

        @property
        def position(self):
            return self._inner.position

        @property
        def position_ms(self):
            return self._inner.position_ms

    return _CountingStream


def detect_shots(
    path: str | Path,
    *,
    duration_ms: int | None,
    crop: vf.CropRect | None = None,
    trim_start_ms: int = 0,
    trim_end_ms: int = 0,
    sensitivity: float = 3.0,
    min_shot_ms: int = 600,
    frame_skip: int = 0,
    max_shots: int = 5000,
    progress: Progress | None = None,
) -> tuple[list[Shot], str]:
    """Segment a video into shots. Returns `(shots, method)`. Blocking.

    `method` is `"adaptive"` when PySceneDetect produced the boundaries and
    `"uniform"` when the fixed-interval sampler did. The caller must surface the
    difference: a user who asked for shot detection and silently got time
    slicing has been given a different feature.

    Three ways `"uniform"` happens, all through one code path:

    1. `scenedetect` is not installed.
    2. It found no cuts at all. `get_scene_list()` returns **`[]`** in that case,
       not one scene — naive code writes zero frames and reports success — so
       `start_in_scene=True` is passed *and* a spanning shot is synthesized if
       the list is still empty.
    3. It found exactly one shot longer than `SINGLE_SHOT_FALLBACK_MS`. One
       frame out of two hours is not an extraction.

    **Cost, honestly.** `auto_downscale` cheapens the *analysis*, but the
    downscale happens after `video.read()`, so every frame is still fully
    decoded: a two-hour 24 fps 4K file is ~173k decodes before a single frame is
    written. The levers, in order, are `frame_skip` (legal here only because no
    `StatsManager` is attached), `max_shots` and `min_shot_ms`.
    """
    path = Path(path)
    start_ms = float(max(0, trim_start_ms))
    end_ms = float(duration_ms - max(0, trim_end_ms)) if duration_ms else 0.0
    progress = progress or Progress()

    def fallback(reason: str) -> tuple[list[Shot], str]:
        if not duration_ms:
            # No span to divide. One open-ended window; render_shot clamps
            # against what actually decodes.
            return [Shot(index=0, start_ms=int(start_ms), end_ms=int(start_ms))], "uniform"
        logger.info("detect_shots: uniform fallback for %s (%s)", path.name, reason)
        return _uniform_shots(start_ms, end_ms, min_shot_ms=min_shot_ms, max_shots=max_shots), "uniform"

    try:
        from scenedetect import AdaptiveDetector, SceneManager, open_video
    except Exception:  # noqa: BLE001
        return fallback("scenedetect is not installed")

    try:
        video = open_video(str(path), backend="opencv")
    except Exception as exc:  # scenedetect.VideoOpenFailure and friends
        # Deliberately *not* the uniform fallback. A file that will not open has
        # no frames to slice into windows, and falling back would produce a
        # shot list whose every render fails — a "completed" job with zero
        # output. The job must fail with the reason instead.
        raise UnreadableVideoError(f"Could not decode video: {path.name}") from exc
    fps = float(video.frame_rate or 0.0) or 25.0
    min_shot_frames = max(1, int(round(min_shot_ms / 1000.0 * fps)))

    manager = SceneManager()
    manager.add_detector(
        AdaptiveDetector(
            adaptive_threshold=sensitivity,
            min_scene_len=min_shot_frames,
            min_content_val=15.0,
        )
    )
    if crop is not None:
        # SceneManager.crop is INCLUSIVE corners (x0, y0, x1, y1), not x/y/w/h.
        # It only *warns* on an out-of-range rect rather than raising, which is
        # worse than raising — so clamp before it gets here.
        cw, ch = video.frame_size
        safe = vf.clamp_crop(crop, cw, ch)
        if safe is not None:
            x, y, w, h = safe
            manager.crop = (x, y, x + w - 1, y + h - 1)

    if duration_ms:
        progress.total_frames = int(max(0.0, end_ms - start_ms) / 1000.0 * fps)
    if start_ms > 0:
        video.seek(start_ms / 1000.0)

    stream = _counting_stream_class()(video, progress)
    progress.stop_hook = manager.stop
    try:
        manager.detect_scenes(
            video=stream,
            end_time=(end_ms / 1000.0) if duration_ms else None,
            frame_skip=frame_skip,
        )
    except Exception:
        logger.warning("detect_shots: scenedetect failed on %s", path.name, exc_info=True)
        return fallback("shot detection raised")

    if progress.cancel:
        return [], "cancelled"

    scenes = manager.get_scene_list(start_in_scene=True)
    if not scenes:
        return fallback("no cuts found")

    shots = [
        Shot(index=i, start_ms=int(a.get_seconds() * 1000), end_ms=int(b.get_seconds() * 1000))
        for i, (a, b) in enumerate(scenes)
    ]
    if len(shots) == 1 and shots[0].duration_ms > SINGLE_SHOT_FALLBACK_MS:
        return fallback("a single shot longer than the fallback threshold")
    if len(shots) > max_shots:
        logger.info(
            "detect_shots: %s produced %d shots, capping at %d", path.name, len(shots), max_shots
        )
        shots = shots[:max_shots]
    return shots, "adaptive"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _write_frame(
    frame_bgr: np.ndarray,
    out_path: str,
    thumb_path: str | None,
    *,
    crop: vf.CropRect | None = None,
    long_edge: int = 0,
) -> str:
    """Crop → resize → save one decoded frame, plus its thumbnail. Blocking.

    The shared write half of both passes: pass 1 (`render_shot`) hands it the
    frame it picked out of a shot, pass 2 (`render_at_timestamps`) the frame it
    re-seeked. Returns the path actually written, which can differ from
    `out_path` when `normalize_image_format` falls back to PNG.

    `long_edge=0` means "no downscale", which is what makes native-resolution
    pass-2 output a default rather than a special case.

    **No `ImageOps.exif_transpose`**, and that is not a violation of CLAUDE.md's
    "always transpose first" invariant: the input is a decoded ndarray, not a
    file with an EXIF block — the same reasoning as `video_service.generate_poster`.
    Container rotation is handled by `CAP_PROP_ORIENTATION_AUTO` upstream.
    """
    import cv2
    from PIL import Image as PILImage

    # frame.shape is the authority for the crop, not the container header:
    # headers lie, and container rotation swaps the axes.
    fh, fw = frame_bgr.shape[:2]
    safe = vf.clamp_crop(crop, fw, fh)
    if safe is not None:
        x, y, w, h = safe
        frame_bgr = frame_bgr[y: y + h, x: x + w]

    fh, fw = frame_bgr.shape[:2]
    longest = max(fh, fw)
    if long_edge and longest > long_edge:
        scale = long_edge / longest
        frame_bgr = cv2.resize(
            frame_bgr,
            (max(1, int(round(fw * scale))), max(1, int(round(fh * scale)))),
            interpolation=cv2.INTER_AREA,
        )

    # Format from the suffix, through the one helper that owns the JPG→JPEG and
    # unsupported→PNG rules. `image_save_kwargs("JPEG")` is `{quality: 95,
    # subsampling: 0}`, so pass 1's output is unchanged bit for bit.
    fmt, out_path = normalize_image_format(Path(out_path).suffix, out_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img = PILImage.fromarray(frame_bgr[:, :, ::-1])
    try:
        img.save(out_path, fmt, **image_save_kwargs(fmt))
        if thumb_path:
            Path(thumb_path).parent.mkdir(parents=True, exist_ok=True)
            thumb = img.copy()
            try:
                thumb.thumbnail((256, 256), PILImage.Resampling.LANCZOS)
                thumb.save(thumb_path, "WEBP", quality=85)
            finally:
                thumb.close()
    finally:
        img.close()
    return out_path


def render_at_timestamps(
    path: str | Path,
    timestamps: list[float],
    *,
    dests: list[tuple[str, str | None]],
    crop: vf.CropRect | None = None,
    deinterlace: str = "",
    long_edge: int = 0,
) -> ShotRenderResult:
    """Pass 2: re-seek recorded timestamps and write them full resolution. Blocking.

    **The timestamp is the artifact.** `Image.source_timestamp_ms` is
    authoritative, so this re-seeks it rather than upscaling the triage JPEG —
    and it does *not* re-detect shots or re-pick a frame, because the pick
    already happened in pass 1. `_shot_windows`, `_candidate_positions`,
    `sharpness`, `pick_index` and `is_degenerate` are all unused here.

    Geometry replays verbatim from the stored `Video.crop_*` / `Video.deinterlace`
    the extract endpoint normalized; trims are irrelevant to a direct seek.

    `dests` is `[(image_path, thumbnail_path | None), …]`, index-aligned with
    `timestamps`, and the returned `WrittenFrame.pick` carries that index back so
    the caller can match a written file to the row it belongs to
    (`shot_index` is `-1`: pass 2 knows nothing about shots).

    **Each target gets its own decoder open**: `read_positions` is called once per
    timestamp, and every call opens and releases its own `cv2.VideoCapture` — or
    spawns its own ffmpeg subprocess on the deinterlace path. There is no shared
    walk through the file. Targets are still visited in ascending timestamp order,
    but that buys page-cache locality on the container, not a single forward pass.

    The `video_reextract` job compounds this deliberately, passing one timestamp
    per call: the per-frame structure is what buys the cancel check, the
    copy-on-write protect+commit, the "does it re-open" verification and the
    progress event, and batching would mean N temp files coexisting against a disk
    preflight that budgets for one.

    Each frame is released before the next seek — never a `list[np.ndarray]`, per
    this module's RSS rule.
    """
    path = Path(path)
    result = ShotRenderResult()
    for i in sorted(range(len(timestamps)), key=lambda n: timestamps[n]):
        target = float(timestamps[i])
        out_path, thumb_path = dests[i]
        frame = None
        for _ts, candidate in read_positions(path, [target], deinterlace=deinterlace):
            frame = candidate
            break
        if frame is None:
            result.failed += 1
            continue
        try:
            written = _write_frame(frame, out_path, thumb_path, crop=crop, long_edge=long_edge)
        finally:
            del frame
        result.written.append(
            WrittenFrame(
                shot_index=-1,
                pick=i,
                timestamp_ms=int(round(target)),
                path=written,
                thumb_path=str(thumb_path) if thumb_path else None,
            )
        )
    return result


def render_shot(
    path: str | Path,
    shot: Shot,
    *,
    dests: list[tuple[str, str]],
    crop: vf.CropRect | None = None,
    deinterlace: str = "",
    long_edge: int = 1024,
    policy: str = "sharpest",
    candidates: int = 5,
) -> ShotRenderResult:
    """Write one shot's frames. Blocking; one executor hop per shot.

    `dests` is `[(image_path, thumbnail_path), …]`, one entry per frame wanted —
    allocated on the async side because only that side can see the DB names a
    filename has to dodge. `len(dests)` is therefore `frames_per_shot`.

    **Two decode passes per frame, on purpose.** The first walks the candidate
    window scoring each frame and releasing it; the second re-fetches only the
    winner. The single-pass alternative holds every candidate in memory — five
    4K frames is 125 MB, and both `frames_per_shot` and `candidates` are
    user-settable — and cannot be short-circuited, because `pick_index`'s
    luma-outlier rejection is defined against the median of the whole candidate
    set and so can change the winner retroactively. A second seek-and-decode is
    about 7.5 ms.

    The crop → resize → save → thumbnail tail is `_write_frame`, shared with
    pass 2 so the two passes cannot drift on format or quality.
    """
    path = Path(path)
    result = ShotRenderResult()
    lo = float(shot.start_ms)
    hi = float(shot.end_ms) if shot.end_ms > shot.start_ms else float(shot.start_ms)

    for pick, (out_path, thumb_path) in enumerate(dests):
        centre = _shot_windows(shot, len(dests))[pick]
        positions = _candidate_positions(centre, candidates, lo, hi)

        # Pass 1: score and discard. Nothing but numbers survives the loop.
        scores: list[float] = []
        lumas: list[float] = []
        degenerate: list[bool] = []
        stamps: list[float] = []
        for ts, frame in read_positions(path, positions, deinterlace=deinterlace):
            try:
                lum_mean = float(vf._luma(frame).mean())
                scores.append(vf.sharpness(frame))
                lumas.append(lum_mean)
                degenerate.append(vf.is_degenerate(frame))
                stamps.append(ts)
            finally:
                del frame
        if not scores:
            result.failed += 1
            continue

        chosen = vf.pick_index(scores, lumas, policy, rejected=degenerate)
        chosen_ts = stamps[chosen]

        # Pass 2: re-fetch the winner alone.
        frame = None
        for _ts, candidate in read_positions(path, [chosen_ts], deinterlace=deinterlace):
            frame = candidate
            break
        if frame is None:
            result.failed += 1
            continue

        try:
            out_path = _write_frame(
                frame, out_path, thumb_path, crop=crop, long_edge=long_edge
            )
        finally:
            del frame

        result.written.append(
            WrittenFrame(
                shot_index=shot.index,
                pick=pick,
                timestamp_ms=int(round(chosen_ts)),
                path=str(out_path),
                thumb_path=str(thumb_path) if thumb_path else None,
            )
        )

    return result
