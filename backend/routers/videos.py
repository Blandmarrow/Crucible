import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import delete as sa_delete, func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.licenses import copy_provenance, resolve_provenance
from backend.media_types import codec_label, video_mime
from backend.models import BackgroundJob, Dataset, Image, Video
from backend.schemas.video import (
    CropRect,
    RenameVideoRequest,
    VideoExtractJob,
    VideoExtractRequest,
    VideoExtractResult,
    VideoFramesGroup,
    VideoFramesSummary,
    VideoOut,
    VideoProbeRequest,
    VideoProbeResult,
    VideoProbeSample,
    VideoReextractGroup,
    VideoReextractRequest,
    VideoReextractResult,
)
from backend.services import version_service, video_extract
from backend.services.dataset_busy import busy, ensure_not_busy
from backend.services.dataset_service import refresh_stats
from backend.services.image_service import generate_thumbnail, get_image_info
from backend.services.video_frames import clamp_crop
from backend.services.video_service import claimed_poster_stems, generate_poster, measure_duration_ms
from backend.utils import (
    InsufficientDiskSpaceError,
    chunked,
    normalize_subfolder,
    poster_path_for,
    require_free_space,
    safe_dataset_path,
    slugify_filename,
    unique_filename_with_thumb,
    unique_poster_path,
)
from backend.workers.job_queue import job_queue
from backend.workers.progress import broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/videos", tags=["videos"])

# Negative cache for the lazy poster backfill, keyed by video id → monotonic
# deadline. `VideoStrip` points an <img> at /poster for every card regardless of
# `has_poster` (the endpoint backfills, so that is correct), which means a video
# whose frames will not decode would re-run a full cv2 open on every render of
# every gallery visit — cheap per call, unbounded in visits. A failure parks the
# row for a few minutes instead. In-process and self-clearing on restart, and
# bounded by the number of undecodable videos; the durable form would be a
# `poster_failed_at` column, which is not worth a migration for a retry hint.
POSTER_RETRY_AFTER_SECONDS = 300.0
_poster_failures: dict[str, float] = {}

# Ceiling for the whole probe: twelve seeks at ~7.5 ms each leaves an enormous
# margin, so hitting this means slow or failing storage, not a slow video.
PROBE_TIMEOUT_SECONDS = 25.0


def _poster_backoff_active(video_id: str) -> bool:
    """True while a recent generation failure for this video is still parked."""
    deadline = _poster_failures.get(video_id)
    if deadline is None:
        return False
    if time.monotonic() >= deadline:
        del _poster_failures[video_id]
        return False
    return True


def _to_out(video: Video, ds: Dataset | None) -> VideoOut:
    out = VideoOut.model_validate(video)
    out.codec_label = codec_label(video.codec)
    out.has_poster = bool(video.poster_path)
    out.provenance = resolve_provenance(video, ds)
    return out


@router.get("/", response_model=list[VideoOut])
async def list_videos(dataset_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    result = await db.execute(
        select(Video).where(Video.dataset_id == dataset_id).order_by(Video.filename)
    )
    return [_to_out(v, ds) for v in result.scalars().all()]


@router.get("/capabilities")
async def get_extract_capabilities() -> dict:
    """What this install can actually do, independently of any one video.

    **Declared above `/{video_id}` on purpose** — FastAPI matches in declaration
    order, so a literal path segment that could be read as a path parameter has
    to come first.

    These also ride on the probe response, which is where the modal reads them
    from when it has one. They need their own route because a video that will not
    probe still extracts: without this, a probe failure leaves the deinterlace
    checkbox and the shot-detection warning with nothing to consult, so a host
    without imageio-ffmpeg would offer a filter that 503s. Pure, and answers in
    microseconds after the first import attempt, so the frontend caches it with a
    long `staleTime` rather than refetching per modal.
    """
    return video_extract.capabilities()


@router.get("/{video_id}", response_model=VideoOut)
async def get_video(video_id: str, db: AsyncSession = Depends(get_db)):
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    ds = await db.get(Dataset, video.dataset_id)
    return _to_out(video, ds)


@router.get("/{video_id}/file")
async def get_video_file(video_id: str, db: AsyncSession = Depends(get_db)):
    """Stream the video file.

    Range/206, If-Range and 416 all come free: Starlette's FileResponse sets
    `accept-ranges: bytes` and handles the request headers itself, which is what
    makes a <video> element able to seek.
    """
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    p = safe_dataset_path(video.file_path, settings.datasets_dir)
    if not p.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(str(p), media_type=video_mime(p.suffix))


@router.get("/{video_id}/poster")
async def get_video_poster(video_id: str, db: AsyncSession = Depends(get_db)):
    """Serve the poster frame, cutting one on demand if the row has none.

    The lazy backfill is the `generation_metadata` pattern from
    `GET /images/{image_id}`: rows ingested before posters existed heal the
    first time anything looks at them, so no migration job has to walk the
    table. It also covers a poster deleted from disk out from under the row.
    """
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video not found")

    p = safe_dataset_path(video.poster_path, settings.datasets_dir) if video.poster_path else None
    if p is None or not p.exists():
        if _poster_backoff_active(video_id):
            raise HTTPException(404, "No poster frame for this video")
        src = safe_dataset_path(video.file_path, settings.datasets_dir)
        if not src.exists():
            raise HTTPException(404, "File not found on disk")

        # The stem is *not* simply the video's own. Rescan adopts filenames off
        # disk, so `a.mp4` and `a.mkv` can coexist in one dataset; healing both
        # onto `a.webp` would have the second overwrite the first and leave two
        # rows pointing at one file. Resolve against what the siblings claim.
        poster_dir = src.parent / "thumbnails"
        siblings = await db.execute(
            select(Video.id, Video.filename, Video.poster_path).where(Video.dataset_id == video.dataset_id)
        )
        claimed = claimed_poster_stems(
            [(r.id, r.filename, r.poster_path) for r in siblings.all()], poster_dir, exclude_id=video_id
        )
        target = unique_poster_path(poster_dir, src.stem, claimed)

        ok = await asyncio.get_running_loop().run_in_executor(
            None,
            partial(
                generate_poster,
                src,
                target,
                duration_ms=video.duration_ms,
                trim_start_ms=video.trim_start_ms,
                trim_end_ms=video.trim_end_ms,
            ),
        )
        if not ok:
            _poster_failures[video_id] = time.monotonic() + POSTER_RETRY_AFTER_SECONDS
            raise HTTPException(404, "No poster frame for this video")
        _poster_failures.pop(video_id, None)
        video.poster_path = str(target)
        await db.commit()
        p = target

    return FileResponse(str(p), media_type="image/webp")


@router.get("/{video_id}/frames-summary", response_model=VideoFramesSummary)
async def get_video_frames_summary(video_id: str, db: AsyncSession = Depends(get_db)):
    """Where this video's extracted frames live, grouped by subfolder.

    The server-side counterpart of the rowcount `delete_video` logs, and it feeds
    three surfaces: the extraction history panel, the delete-confirm count, and
    the extraction modal's "Replace (deletes N previous frames)" label — all of
    which need the number *before* anything is destroyed.
    """
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video not found")

    rows = (await db.execute(
        select(
            Image.subfolder,
            func.count(Image.id).label("count"),
            func.max(Image.created_at).label("last_extracted_at"),
        )
        .where(Image.source_video_id == video_id)
        # "" is a real subfolder here (frames at the dataset root), so the group
        # by is on the raw column and nothing coalesces it away.
        .group_by(Image.subfolder)
        .order_by(func.max(Image.created_at).desc())
    )).all()

    groups = [
        VideoFramesGroup(
            subfolder=r.subfolder or "", count=r.count, last_extracted_at=r.last_extracted_at
        )
        for r in rows
    ]
    return VideoFramesSummary(total=sum(g.count for g in groups), groups=groups)


@router.post("/{video_id}/probe", response_model=VideoProbeResult)
async def probe_video_samples(
    video_id: str, body: VideoProbeRequest, db: AsyncSession = Depends(get_db)
):
    """Sample a video for the extraction modal's first step.

    A plain request rather than a job: a seek-and-decode measures at ~7.5 ms, so
    twelve samples is a request-path cost and a job would add a row, an SSE
    subscription and a re-attach path to something that finishes before the
    modal has finished animating.

    **The only write here is metadata correction** — `duration_ms`, and
    `width`/`height`/`fps` if they were NULL. Crop, deinterlace and trims are
    *not* written by the probe: the modal re-probes on every trim-handle drag,
    and a preview must not commit. `POST /videos/extract` writes them.
    """
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    src = safe_dataset_path(video.file_path, settings.datasets_dir)
    if not src.exists():
        raise HTTPException(404, "File not found on disk")

    loop = asyncio.get_running_loop()
    duration_ms = video.duration_ms
    duration_source = "header"
    if not duration_ms:
        duration_ms = await loop.run_in_executor(None, partial(measure_duration_ms, src))
        duration_source = "measured" if duration_ms else "unknown"

    samples = min(body.samples, video_extract.PROBE_MAX_SAMPLES)
    try:
        # This `asyncio.wait_for` is legitimate, unlike the one CLAUDE.md forbids
        # around a stdlib `re` match, and the difference is not stylistic. cv2
        # releases the GIL for every grab/retrieve and each of the <=12 sample
        # units is individually bounded, so the event loop keeps getting
        # scheduled, the timeout genuinely fires, and the abandoned executor
        # thread finishes one frame and discards the result. `re`'s matching loop
        # is C code that never releases the GIL and cannot be interrupted, which
        # is why wrapping *that* in a timeout can never fire.
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                partial(
                    video_extract.probe_samples,
                    src,
                    duration_ms=duration_ms,
                    samples=samples,
                    max_edge=body.max_edge,
                    trim_start_ms=body.trim_start_ms,
                    trim_end_ms=body.trim_end_ms,
                ),
            ),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Probing this video took too long — it may be on slow storage") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    # Metadata correction. Cheap, and it is the only chance the app gets to fill
    # a duration the container header could not supply — everything downstream
    # (progress percent, tail trim, sample positions) needs a real number.
    changed = False
    if duration_ms and video.duration_ms != duration_ms:
        video.duration_ms = duration_ms
        changed = True
    for column, value in (("width", result["width"]), ("height", result["height"]), ("fps", result["fps"])):
        if value and getattr(video, column) is None:
            setattr(video, column, value)
            changed = True
    if changed:
        await db.commit()

    crop = result["crop"]
    return VideoProbeResult(
        samples=[VideoProbeSample(**s) for s in result["samples"]],
        crop=CropRect(x=crop[0], y=crop[1], w=crop[2], h=crop[3]) if crop else None,
        crop_confidence=result["crop_confidence"],
        interlace=result["interlace"],
        telecine=result["telecine"],
        duration_source=duration_source,
        duration_ms=duration_ms,
        width=result["width"],
        height=result["height"],
        fps=result["fps"],
        samples_failed=result["samples_failed"],
        truncated=result["truncated"],
        warnings=result["warnings"],
        capabilities=video_extract.capabilities(),
    )


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

# Rough bytes per written frame, for the post-detection disk preflight. A
# 1024px JPEG at quality 95 plus its WebP thumbnail lands well under this; the
# point is an order-of-magnitude guard, not an accounting.
FRAME_SIZE_ESTIMATE_BYTES = 300_000
# Frames between commits. Detection-crop's single terminal commit is wrong for a
# job that can run twenty minutes (nothing appears in the gallery until it ends);
# folder import's 200 is tuned for a loop that does far less per item.
EXTRACT_COMMIT_EVERY = 25
EXTRACT_DISK_RECHECK_EVERY = 100
# Circuit breaker. A file that decoded well enough to poster can still die
# partway through; writing 4000 broken frames is worse than stopping.
EXTRACT_MAX_CONSECUTIVE_FAILURES = 10
EXTRACT_FAILURE_RATE_AFTER_SHOTS = 20
EXTRACT_MAX_FAILURE_RATE = 0.5
# Progress bands. One monotone percent across three phases, because a bar that
# restarts at zero reads as a job that restarted. `rewriting` is pass 2's single
# phase, so it spans the whole bar.
PHASE_BANDS = {
    "detecting": (0.0, 20.0), "replacing": (20.0, 25.0), "extracting": (25.0, 100.0),
    "rewriting": (0.0, 100.0),
}
DETECT_EMIT_INTERVAL = 0.5

# One copy of the text, quoted by the extract gate, the re-extract gate and
# `video_extract.require_deinterlace`.
DEINTERLACE_UNAVAILABLE = (
    "Deinterlacing needs the imageio-ffmpeg package, which is not installed. "
    "Run the update command (manage.sh update / manage.ps1 update), or extract "
    "with deinterlacing switched off."
)


def _phase_percent(phase: str, fraction: float) -> float:
    lo, hi = PHASE_BANDS[phase]
    return round(lo + (hi - lo) * max(0.0, min(1.0, fraction)), 1)


async def _existing_subfolders(db: AsyncSession, dataset_id: str) -> set[str]:
    """Every subfolder name already in use in a dataset, declared or occupied.

    Both halves matter for `new_subfolder`: stepping only against *this* video's
    own history would happily land video A's "new" subfolder inside one video B
    already fills.
    """
    rows = await db.execute(
        select(Image.subfolder).where(Image.dataset_id == dataset_id).distinct()
    )
    names = {r[0] for r in rows.all() if r[0]}
    ds = await db.get(Dataset, dataset_id)
    if ds and ds.declared_subfolders:
        names |= {s for s in ds.declared_subfolders if s}
    return names


async def _last_subfolder_for(db: AsyncSession, video_id: str) -> str | None:
    """The subfolder this video's most recent extraction wrote into."""
    row = await db.execute(
        select(Image.subfolder)
        .where(Image.source_video_id == video_id)
        .order_by(Image.created_at.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


async def _videos_with_running_extractions(db: AsyncSession) -> set[str]:
    """Video ids covered by a pending or running extraction of *either* pass.

    Both directions matter. Pass 1's `replace` mode deletes this video's previous
    frames, which is exactly the set pass 2 rewrites in place; letting the two
    overlap means one job unlinking files the other is mid-swap on.
    """
    inflight = (await db.execute(
        select(BackgroundJob.config).where(
            BackgroundJob.job_type.in_(("video_extract", "video_reextract")),
            BackgroundJob.status.in_(("pending", "running")),
        )
    )).scalars().all()
    return {
        (cfg or {}).get("video_id") for cfg in inflight if isinstance(cfg, dict)
    }


def _step_subfolder(base: str, taken: set[str]) -> str:
    """`base`, `base_2`, `base_3`, … — the first name nothing else claims."""
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


@router.post("/extract", response_model=VideoExtractResult)
async def extract_frames(body: VideoExtractRequest, db: AsyncSession = Depends(get_db)):
    """Turn videos into shot-segmented `Image` rows. One job per video.

    **The decode-fixup write happens here**, not in the probe and not in the job.
    Not the probe, because the modal re-probes on every trim-handle drag and a
    preview must not commit. Not the job, because the values have to survive a
    cancelled or failed run — "add to the existing subfolder" and any later
    re-extraction both read them off the row — and a write inside a job cannot
    return a 400. This endpoint has the request session, already holds the busy
    guard, and is the only place that can validate a rect against each video's
    real dimensions.
    """
    rows = (await db.execute(select(Video).where(Video.id.in_(body.video_ids)))).scalars().all()
    if not rows:
        raise HTTPException(404, "No matching videos found")

    for dataset_id in {v.dataset_id for v in rows}:
        ensure_not_busy(dataset_id)

    # Dedupe *first*, before any validation or write — `rescan_folder`'s
    # precedent for the skip itself, but the ordering matters beyond that: a
    # video already extracting must not have its stored crop, deinterlace or
    # trims mutated by a request that extracts nothing, and must not 400 the
    # whole batch over a rect that will never be applied.
    # Both job types, in both directions: a pass-1 `replace` deletes the very
    # rows a running pass 2 is rewriting, and a pass 2 rewrites rows a running
    # pass 1 may be about to delete.
    busy_video_ids = await _videos_with_running_extractions(db)

    skipped: list[dict] = []
    to_run: list[Video] = []
    for v in rows:
        if v.id in busy_video_ids:
            skipped.append({"video_id": v.id, "filename": v.filename, "reason": "already extracting"})
        else:
            to_run.append(v)

    caps = video_extract.capabilities()
    for v in to_run:
        # The *effective* value, not `body.deinterlace`: a row carrying a filter
        # from an earlier run plus a request that omits the field would otherwise
        # pass the gate and then have `require_deinterlace` raise inside every
        # shot, so the circuit breaker reports a missing package as a decode fault.
        effective = body.deinterlace if body.deinterlace is not None else (v.deinterlace or "")
        if effective and not caps["deinterlace"]:
            # Keep in sync with `video_extract.require_deinterlace`'s copy.
            raise HTTPException(503, DEINTERLACE_UNAVAILABLE)

    # The rect actually stored: `clamp_crop` snaps to even coordinates and
    # returns None for a full-frame rect, so comparing its output against the
    # request would 400 rects that plainly fit. Refuse genuine overflow only,
    # normalize the rest.
    normalized_crop: tuple[int, int, int, int] | None = None
    if body.crop is not None and to_run:
        # A series where half the episodes are letterboxed and half are not is a
        # silently inconsistent dataset, so a mixed batch is a 400 that names the
        # videos rather than a per-video skip nobody reads. This also refuses a
        # batch mixing probed and unprobed videos: a (None, None) entry makes
        # `dims` non-uniform.
        dims = {(v.width, v.height) for v in to_run}
        if len(dims) > 1:
            names = ", ".join(sorted(v.filename for v in to_run))
            raise HTTPException(
                400,
                f"One crop cannot apply to videos of different sizes: {names}. "
                "Extract them separately, or clear the crop.",
            )
        rect = body.crop.as_tuple()
        x, y, w, h = rect
        for v in to_run:
            if v.width and v.height and (x + w > v.width or y + h > v.height):
                raise HTTPException(
                    400,
                    f"Crop {rect} does not fit inside {v.filename} ({v.width}x{v.height})",
                )
        width, height = next(iter(dims))
        # `clamp_crop` is the single source of truth for the even-snap and the
        # no-op rule; a None result means the rect covers the whole frame, which
        # is stored as no crop rather than treated as an error. An unprobed batch
        # has nothing to normalize against — the per-frame clamp inside
        # `render_shot` is the real authority anyway.
        normalized_crop = clamp_crop(rect, width, height) if width and height else rect

    # The cheap request-path form of the disk preflight, so a full volume is a
    # 507 the user sees immediately rather than N jobs that each fail minutes
    # later. The job re-checks with a real estimate once detection has run.
    for dataset_id in {v.dataset_id for v in to_run}:
        ds = await db.get(Dataset, dataset_id)
        if ds:
            try:
                require_free_space(Path(ds.folder_path) / "images", 0)
            except InsufficientDiskSpaceError as exc:
                raise HTTPException(507, str(exc)) from None

    # Write the confirmed decode fixups, and commit before any job row exists.
    # The *normalized* rect, not the request's: the stored value is what a later
    # run replays, so it has to be what this run will actually apply.
    for v in to_run:
        if body.clear_crop:
            v.crop_x = v.crop_y = v.crop_w = v.crop_h = None
        elif body.crop is not None:
            v.crop_x, v.crop_y, v.crop_w, v.crop_h = normalized_crop or (None, None, None, None)
        if body.deinterlace is not None:
            v.deinterlace = body.deinterlace
        if body.trim_start_ms is not None:
            v.trim_start_ms = body.trim_start_ms
        if body.trim_end_ms is not None:
            v.trim_end_ms = body.trim_end_ms
    await db.commit()

    jobs: list[VideoExtractJob] = []
    # Claimed within this request as well as in the DB: two videos in one batch
    # must not both resolve `new_subfolder` to the same name.
    taken_by_dataset: dict[str, set[str]] = {}

    for v in to_run:
        if v.dataset_id not in taken_by_dataset:
            taken_by_dataset[v.dataset_id] = await _existing_subfolders(db, v.dataset_id)
        taken = taken_by_dataset[v.dataset_id]

        stem_slug = slugify_filename(Path(v.filename).stem) or "video"
        # `is not None`, not truthiness: `_last_subfolder_for` returns None for
        # "no previous extraction" and "" for one whose frames sit at the dataset
        # root, and "" is a legitimate subfolder throughout the codebase. Reading
        # the two as the same thing turns a `replace` into add-to-a-new-subfolder
        # and leaves behind the frames it exists to remove.
        previous = await _last_subfolder_for(db, v.id)
        if body.mode == "add":
            explicit = normalize_subfolder(body.subfolder) if body.subfolder else ""
            if explicit:
                target = explicit
            elif previous is not None:
                target = previous
            else:
                target = _step_subfolder(stem_slug, taken)
        elif body.mode == "replace":
            # No previous extraction is not an error — it just means there is
            # nothing to replace, so this behaves as a first run.
            target = previous if previous is not None else _step_subfolder(stem_slug, taken)
        else:
            base = normalize_subfolder(body.subfolder) if body.subfolder else stem_slug
            target = _step_subfolder(base or stem_slug, taken)
        taken.add(target)

        n = body.frames_per_shot
        auto_label = (
            f"Extract: {Path(v.filename).stem[:60]} — "
            f"{n} frame{'' if n == 1 else 's'}/shot"
        )
        config = {**body.model_dump(), "video_id": v.id, "resolved_subfolder": target}
        job = BackgroundJob(
            job_type="video_extract",
            label=body.label or auto_label,
            dataset_id=v.dataset_id,
            # 0 until detection lands and the real shot count is known —
            # `import_folder`'s precedent for a job whose size it cannot know up
            # front.
            total_items=0,
            config=config,
        )
        db.add(job)
        await db.commit()
        await job_queue.enqueue(job, _make_extract_runner(config, v.dataset_id))
        jobs.append(
            VideoExtractJob(video_id=v.id, filename=v.filename, job_id=job.id, subfolder=target)
        )

    return VideoExtractResult(jobs=jobs, skipped=skipped)


def _make_extract_runner(cfg: dict, dataset_id: str):
    """Build the worker coroutine for one video's extraction job."""

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await _run_extraction(session, job_id, cfg)

    async def _run_with_stats(job_id: str) -> None:
        """`_run` plus a stats refresh on every path that raises past step 8.

        `_run_extraction`'s final `refresh_stats` covers the normal return only;
        cancellation (`raise_if_cancelled`), the circuit-breaker `RuntimeError`
        and all three `require_free_space` preflights raise straight past it, and
        each of those can have committed frames the dataset counters would then
        undercount. `comfy_generate`'s `_run_with_stats` is the precedent, down to
        the three load-bearing details: `AsyncSessionLocal` is imported inside the
        coroutine (the test harness patches it at module level after import time),
        `BaseException` is caught so `CancelledError` is covered while the inner
        `except Exception` keeps a failed refresh from turning a cancelled job
        into a failed one, and the *call* is wrapped rather than the body so
        `_run`'s session is closed before the second one opens — no writer-lock
        contention on the abort path.
        """
        from backend.database import AsyncSessionLocal
        from backend.services.dataset_service import refresh_stats

        try:
            await _run(job_id)
        except BaseException:
            try:
                async with AsyncSessionLocal() as stats_session:
                    await refresh_stats(stats_session, dataset_id)
            except Exception:
                logger.warning("video_extract: final stats refresh failed", exc_info=True)
            raise

    return _run_with_stats


async def _emit(
    job_id: str, phase: str, fraction: float, *,
    video_id: str, job_type: str = "video_extract", **extra,
) -> None:
    """Emit one progress event for an extraction job, either pass.

    `video_id` is keyword-only and required so no call site can forget it: a batch
    runs one job per video, and without the key a frontend holding every event in
    one store cannot tell which video a payload belongs to. This mirrors
    `comfy_prompts`' `plan_id`, which exists for exactly the same reason.
    `jobStore` merges partials by job id, so the key survives onto the queue's
    terminal event, which does not carry it.
    """
    await broadcaster.emit(job_id, {
        "type": "progress", "job_id": job_id, "job_type": job_type,
        "status": "running", "phase": phase, "video_id": video_id,
        "percent": _phase_percent(phase, fraction),
        **extra,
    })


async def _detect_with_progress(
    job_id: str, src: Path, *, video: Video, cfg: dict
) -> tuple[list[video_extract.Shot], str]:
    """Run shot detection in the executor while emitting progress and honouring cancel.

    Detection is the long phase — a two-hour 4K file is ~173k full-resolution
    decodes before a single frame is written — so a silent bar here reads as a
    hang. The frame counter comes from the delegating stream inside
    `detect_shots`; polling `video.frame_number` instead would be a live
    `cv2.VideoCapture.get` on a handle scenedetect's decode thread is
    concurrently grabbing.
    """
    loop = asyncio.get_running_loop()
    progress = video_extract.Progress()
    crop = None
    if video.crop_w and video.crop_h:
        crop = (video.crop_x or 0, video.crop_y or 0, video.crop_w, video.crop_h)

    future = loop.run_in_executor(
        None,
        partial(
            video_extract.detect_shots,
            src,
            duration_ms=video.duration_ms,
            crop=crop,
            trim_start_ms=video.trim_start_ms,
            trim_end_ms=video.trim_end_ms,
            sensitivity=cfg["sensitivity"],
            min_shot_ms=cfg["min_shot_ms"],
            frame_skip=cfg["detector_frame_skip"],
            max_shots=cfg["max_shots"],
            progress=progress,
        ),
    )
    started = time.monotonic()
    while True:
        done, _ = await asyncio.wait({future}, timeout=DETECT_EMIT_INTERVAL)
        if done:
            break
        if job_queue.cancel_requested(job_id) and not progress.cancel:
            progress.cancel = True
            if callable(progress.stop_hook):
                progress.stop_hook()
        read, total = progress.frames_read, progress.total_frames
        fraction = (read / total) if total else 0.0
        elapsed = time.monotonic() - started
        message = f"Detecting shots — {read:,} frames"
        # An ETA only once there is enough data for one to mean anything. A
        # twenty-minute phase with no estimate reads as a hang.
        if total and read > 0 and elapsed > 5.0:
            remaining = elapsed / read * max(total - read, 0)
            message += f", about {int(remaining // 60)}m {int(remaining % 60)}s left"
        # `done`/`total` are pinned to zero, not omitted: `jobStore` merges
        # partials by job id, so an omitted key silently inherits whatever the
        # client last held. They must stay zero here because the job's counter
        # means one thing for the whole run — *frames a gallery refetch would
        # see* — and this phase writes none. The decoded-frame count and its ETA
        # ride on `message`; `_phase_percent` drives the bar. Same shape as
        # `tag_consolidation_service`'s non-per-item phases.
        await _emit(
            job_id, "detecting", fraction,
            video_id=video.id, message=message, done=0, total=0,
        )
    return await future


async def _delete_previous_frames(
    session: AsyncSession, job_id: str, *, video_id: str, dataset_id: str, subfolder: str
) -> int:
    """Replace-mode: remove this video's frames from the target subfolder.

    Scoped to `source_video_id == video_id` *within* that subfolder, so a
    subfolder a user also hand-filled does not lose the hand-filled part.

    Deletes go through the normal path — `mark_image_deleted_in_versions`, then
    the row, then the file, its `.txt` sidecar and its thumbnail — never a raw
    unlink. That is what lets a pre-existing snapshot restore them, and it is
    what makes running this step at all acceptable.
    """
    rows = (await session.execute(
        select(Image.id, Image.file_path, Image.thumbnail_path).where(
            Image.source_video_id == video_id,
            Image.dataset_id == dataset_id,
            Image.subfolder == subfolder,
        )
    )).all()
    if not rows:
        return 0

    # The busy flag is taken around *this step only*. Extraction as a whole does
    # not take it — jobs are already serialized by the queue, and holding it for
    # twenty minutes would 409 every caption edit for no safety gain. But a
    # replace deletes N rows, N files and N thumbnails, which is exactly the
    # class the flag fences, and that takes seconds.
    with busy(dataset_id, "Replacing extracted frames"):
        files: list[Path] = []
        for r in rows:
            await version_service.mark_image_deleted_in_versions(r.id, r.file_path, session)
            p = Path(r.file_path)
            files.extend([p, p.with_suffix(".txt")])
            if r.thumbnail_path:
                files.append(Path(r.thumbnail_path))
        await session.execute(sa_delete(Image).where(Image.id.in_([r.id for r in rows])))
        await session.commit()
        for f in files:
            f.unlink(missing_ok=True)
    await _emit(
        job_id, "replacing", 1.0,
        video_id=video_id, message=f"Removed {len(rows)} previous frame(s)",
    )
    return len(rows)


async def _run_extraction(session: AsyncSession, job_id: str, cfg: dict) -> None:
    """The `video_extract` worker.

    **Step order is load-bearing, and the replace-mode delete is deliberately
    fifth.** A replace that destroys the previous extraction and then fails to
    produce a replacement is the worst outcome this feature can produce, so it
    runs only once the video is known to decode, the shot list is non-empty and
    the disk has been shown to have room.
    """
    loop = asyncio.get_running_loop()
    video = await session.get(Video, cfg["video_id"])
    if video is None:
        raise RuntimeError("The video was deleted before extraction started")
    dataset = await session.get(Dataset, video.dataset_id)
    if dataset is None:
        raise RuntimeError("The dataset was deleted before extraction started")

    src = Path(video.file_path)
    images_dir = Path(dataset.folder_path) / "images"
    thumb_dir = Path(dataset.folder_path) / "thumbnails"
    subfolder = cfg["resolved_subfolder"]
    frames_per_shot = cfg["frames_per_shot"]
    counts = {
        "written": 0, "failed": 0, "shots": 0, "replaced": 0,
        "method": "adaptive", "subfolder": subfolder,
    }

    # 1. A NULL duration breaks everything downstream of it — percentage, tail
    #    trim, sample positions — so nothing past this point ever sees one that
    #    could have been measured.
    if not video.duration_ms:
        measured = await loop.run_in_executor(None, partial(measure_duration_ms, src))
        if measured:
            video.duration_ms = measured
            await session.commit()
        else:
            await _emit(job_id, "detecting", 0.0, video_id=video.id, done=0, total=0, message=(
                "This container reports no usable duration and will not seek, so progress "
                "is indeterminate and the tail trim is ignored"
            ))

    # 2. Cheapest possible failure, before twenty minutes of decoding.
    require_free_space(images_dir, 0)

    # 3. Shot detection — the long phase, and the one most likely to fail.
    await _emit(job_id, "detecting", 0.0, video_id=video.id, done=0, total=0, message="Detecting shots…")
    shots, method = await _detect_with_progress(job_id, src, video=video, cfg=cfg)
    counts["method"] = method
    if method == "cancelled" or job_queue.cancel_requested(job_id):
        job_queue.raise_if_cancelled(job_id)
        return
    counts["shots"] = len(shots)
    if method == "uniform":
        # A user who asked for shot detection and silently got time slicing has
        # been handed a different feature. Say so.
        await _emit(job_id, "detecting", 1.0, video_id=video.id, done=0, total=0, message=(
            f"No shot boundaries were found, so frames were sampled at fixed intervals "
            f"({len(shots)} windows)"
        ))

    # 4. Now the estimate is real, so the preflight can be.
    require_free_space(images_dir, len(shots) * frames_per_shot * FRAME_SIZE_ESTIMATE_BYTES)

    job_row = await session.get(BackgroundJob, job_id)
    if job_row:
        job_row.total_items = len(shots) * frames_per_shot
        await session.commit()

    # 5. Only now.
    if cfg["mode"] == "replace":
        counts["replaced"] = await _delete_previous_frames(
            session, job_id, video_id=video.id, dataset_id=dataset.id, subfolder=subfolder
        )

    # 6. Extraction. Files land flat in images/ and thumbnails flat in
    #    thumbnails/ — `Image.subfolder` is a DB-only grouping, never a
    #    directory — so one db_names set and one stem glob cover the whole job.
    images_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    existing = await session.execute(select(Image.filename).where(Image.dataset_id == dataset.id))
    db_names: set[str] = {r[0] for r in existing.all()}
    occupied_thumb_stems: set[str] = {p.stem for p in thumb_dir.glob("*.webp")}
    planned_thumb_stems: set[str] = set()

    stem_slug = slugify_filename(src.stem) or "video"
    crop = None
    if video.crop_w and video.crop_h:
        crop = (video.crop_x or 0, video.crop_y or 0, video.crop_w, video.crop_h)
    # A Video has no `source_meta` attribute at all, and copy_provenance's
    # getattr default correctly yields None for it — that is intended, not an
    # oversight, so do not "fix" it by adding the column to the model.
    provenance = copy_provenance(video)

    consecutive_failures = 0
    total_frames = len(shots) * frames_per_shot
    written_since_commit = 0
    # Counted, not derived from `counts["written"] % N`: with frames_per_shot > 1
    # the running total steps straight over the multiple and the recheck never
    # fires at all.
    written_since_disk_check = 0
    cancelled = False

    for shot_pos, shot in enumerate(shots):
        if job_queue.cancel_requested(job_id):
            cancelled = True
            break

        dests: list[tuple[str, str]] = []
        for pick in range(frames_per_shot):
            proposal = f"{stem_slug}_s{shot.index:04d}_{pick:02d}"
            filename = unique_filename_with_thumb(
                images_dir, proposal, ".jpg", db_names, occupied_thumb_stems, planned_thumb_stems
            )
            dests.append((str(images_dir / filename), str(thumb_dir / (Path(filename).stem + ".webp"))))

        try:
            result = await loop.run_in_executor(
                None,
                partial(
                    video_extract.render_shot,
                    src,
                    shot,
                    dests=dests,
                    crop=crop,
                    deinterlace=video.deinterlace or "",
                    long_edge=cfg["long_edge"],
                    policy=cfg["pick"],
                    candidates=cfg["candidates"],
                ),
            )
        except Exception as exc:
            logger.warning("video_extract: shot %d of %s failed: %s", shot.index, src.name, exc)
            counts["failed"] += frames_per_shot
            consecutive_failures += frames_per_shot
            result = None

        last_image_id: str | None = None
        if result is not None:
            counts["failed"] += result.failed
            # Parenthesised on purpose: the ternary binds looser than `+`, so the
            # unbracketed form reads as `consecutive_failures + (… if … else 0)`.
            consecutive_failures = (consecutive_failures + result.failed) if result.failed else 0
            for frame in result.written:
                info = await loop.run_in_executor(None, get_image_info, frame.path)
                if not info:
                    # get_image_info swallows every exception, so `{}` means the
                    # file it just wrote will not re-open. NEVER construct an
                    # Image from it: a NULL-dimension row silently breaks grid
                    # layout, the dimension filters, dedup and the detection
                    # remap, and nothing points at the cause.
                    Path(frame.path).unlink(missing_ok=True)
                    if frame.thumb_path:
                        Path(frame.thumb_path).unlink(missing_ok=True)
                    counts["failed"] += 1
                    consecutive_failures += 1
                    continue
                img = Image(
                    dataset_id=dataset.id,
                    filename=Path(frame.path).name,
                    original_filename=video.filename,
                    subfolder=subfolder,
                    file_path=frame.path,
                    thumbnail_path=frame.thumb_path,
                    is_auto_named=True,
                    width=info["width"],
                    height=info["height"],
                    file_size_bytes=info["file_size_bytes"],
                    format=info["format"],
                    phash=info["phash"],
                    source_video_id=video.id,
                    source_timestamp_ms=frame.timestamp_ms,
                    source_shot_index=frame.shot_index,
                    **provenance,
                )
                session.add(img)
                await session.flush()
                last_image_id = img.id
                counts["written"] += 1
                consecutive_failures = 0
                written_since_commit += 1
                written_since_disk_check += 1

        planned = (shot_pos + 1) * frames_per_shot
        if written_since_commit >= EXTRACT_COMMIT_EVERY:
            # Commit as we go so the gallery fills live rather than staying empty
            # for the length of the job.
            await session.commit()
            written_since_commit = 0
        if written_since_disk_check >= EXTRACT_DISK_RECHECK_EVERY:
            written_since_disk_check = 0
            # Commit first: `require_free_space` raises out of the job, and
            # anything flushed but uncommitted would be a file on disk with no
            # Image row. The circuit breaker below commits for the same reason.
            await session.commit()
            written_since_commit = 0
            require_free_space(images_dir, 0)

        # `done` is the *committed* frame count, not the planned one: the live
        # gallery invalidation in `TopBar` is a per-job monotonic high-water mark
        # on `done`, so it must step exactly when new rows become visible — once
        # per commit, not once per shot. The bar keeps its per-shot smoothness
        # because `fraction` is still planned frames.
        await _emit(
            job_id, "extracting", planned / max(total_frames, 1),
            video_id=video.id,
            done=counts["written"] - written_since_commit, total=total_frames,
            shot=shot_pos + 1, shots=len(shots),
            image_id=last_image_id,
            message=f"Shot {shot_pos + 1} of {len(shots)}",
        )

        if consecutive_failures >= EXTRACT_MAX_CONSECUTIVE_FAILURES or (
            shot_pos + 1 >= EXTRACT_FAILURE_RATE_AFTER_SHOTS
            and counts["failed"] > EXTRACT_MAX_FAILURE_RATE * planned
        ):
            # Frames already written stay — they are real. The job is `failed`,
            # not `cancelled`: nobody asked for this.
            await session.commit()
            raise RuntimeError(
                f"Extraction stopped at {shot.start_ms // 1000}s "
                f"({shot.start_ms // 60000}:{shot.start_ms // 1000 % 60:02d}) — "
                f"{counts['failed']} frame(s) failed to decode. "
                f"{counts['written']} frame(s) already written have been kept."
            )

    # 7.
    job_row = await session.get(BackgroundJob, job_id)
    if job_row:
        job_row.result_data = counts
    await session.commit()
    if cancelled:
        job_queue.raise_if_cancelled(job_id)

    # 8.
    await refresh_stats(session, dataset.id)


# ---------------------------------------------------------------------------
# Pass 2 — full-resolution re-extraction
# ---------------------------------------------------------------------------

# Bytes per re-extracted frame, per cropped pixel, for the disk preflight.
# Deliberately generous: a temp file coexists with the original during each swap.
REEXTRACT_BYTES_PER_PIXEL = {"jpeg": 0.5, "png": 2.0}
# Carried on `result_data` so the completion toast can say it as well as the
# form. Pass 2 leaves the scores alone (matching `batch_upscale`/`batch_lut`
# replace mode), and silence about that would be the misleading choice.
REEXTRACT_NOTE = (
    "Quality scores were measured on the triage frames and have been kept as they are. "
    "Re-run scoring if you want scores that reflect the full-resolution images."
)


def _edited_in_place(history) -> bool:
    """True when something other than pass 2 has already rewritten these pixels.

    This is the rule `backend/models/image.py` was written to state: the
    **replace** mode of crop/upscale/LUT/detection-crop mutates a frame's row in
    place, so it keeps its lineage while the pixels stop being the extracted
    frame. Re-extracting one would silently discard the edit.

    The `reextract` exclusion is load-bearing — without it pass 2 would refuse to
    run a second time, because its own history entry would look like third-party
    editing. Anything that is not a dict counts as an unknown edit and skips.
    """
    for entry in history or []:
        if not isinstance(entry, dict) or entry.get("op") != "reextract":
            return True
    return False


async def _reextract_rows(db: AsyncSession, body: VideoReextractRequest):
    """The `Image` rows in scope plus the ids that no longer resolve.

    A column select, not entities: the resolver is shared with the preview
    endpoint, which writes nothing and should not pay for an ORM load.
    """
    cols = (
        Image.id, Image.filename, Image.dataset_id, Image.file_path,
        Image.source_video_id, Image.source_timestamp_ms, Image.processing_history,
    )
    if body.image_ids is not None:
        found = {}
        for chunk in chunked(body.image_ids):
            for row in (await db.execute(select(*cols).where(Image.id.in_(chunk)))).all():
                found[row.id] = row
        # Request order, and every unresolved id named rather than dropped.
        ordered = [found[i] for i in body.image_ids if i in found]
        missing = [i for i in body.image_ids if i not in found]
        return ordered, missing

    q = select(*cols).where(Image.source_video_id == body.video_id)
    if body.subfolder is not None:
        q = q.where(Image.subfolder == normalize_subfolder(body.subfolder))
    rows = (await db.execute(q.order_by(Image.source_timestamp_ms))).all()
    return list(rows), []


async def _resolve_reextract_targets(
    db: AsyncSession, body: VideoReextractRequest
) -> tuple[list[dict], list[dict], int]:
    """Group the eligible frames by source video; name every skip and its reason.

    Shared by `/reextract/preview` and `/reextract` so the modal's accounting and
    the job's can never diverge — the one thing a preview endpoint exists to
    guarantee.
    """
    rows, missing = await _reextract_rows(db, body)
    skipped: list[dict] = [
        {"image_id": image_id, "filename": "", "reason": "no longer exists"}
        for image_id in missing
    ]

    video_ids = sorted({r.source_video_id for r in rows if r.source_video_id})
    videos: dict[str, Video] = {}
    for chunk in chunked(video_ids):
        for v in (await db.execute(select(Video).where(Video.id.in_(chunk)))).scalars().all():
            videos[v.id] = v
    on_disk: dict[str, bool] = {}

    groups: dict[str, dict] = {}
    for r in rows:
        video = videos.get(r.source_video_id) if r.source_video_id else None
        if video is not None and r.source_video_id not in on_disk:
            on_disk[r.source_video_id] = Path(video.file_path).exists()

        # The ladder, in order. The last rung is `models/image.py`'s mandate.
        reason = None
        if not r.source_video_id:
            reason = "not extracted from a video"
        elif r.source_timestamp_ms is None:
            # The timestamp is the artifact — without it there is nothing to seek.
            reason = "no recorded timestamp"
        elif video is None or video.dataset_id != r.dataset_id:
            reason = "source video is gone"
        elif not on_disk[r.source_video_id]:
            reason = "source video file is missing"
        elif _edited_in_place(r.processing_history):
            reason = "already edited in place"
        if reason is not None:
            skipped.append({"image_id": r.id, "filename": r.filename, "reason": reason})
            continue

        group = groups.setdefault(video.id, {"video": video, "frames": []})
        group["frames"].append(
            {"image_id": r.id, "filename": r.filename, "timestamp_ms": r.source_timestamp_ms}
        )

    # In-flight dedupe lives in the resolver rather than the enqueue path so the
    # preview is honest about it too.
    busy_video_ids = await _videos_with_running_extractions(db)
    for video_id in [v for v in groups if v in busy_video_ids]:
        for frame in groups.pop(video_id)["frames"]:
            skipped.append({
                "image_id": frame["image_id"], "filename": frame["filename"],
                "reason": "source video is already being extracted",
            })

    return list(groups.values()), skipped, len(rows) + len(missing)


def _reextract_result(groups: list[dict], skipped: list[dict], total: int,
                      job_ids: dict[str, str] | None = None) -> VideoReextractResult:
    job_ids = job_ids or {}
    return VideoReextractResult(
        groups=[
            VideoReextractGroup(
                video_id=g["video"].id,
                filename=g["video"].filename,
                frames=len(g["frames"]),
                job_id=job_ids.get(g["video"].id),
            )
            for g in groups
        ],
        skipped=skipped,
        eligible=sum(len(g["frames"]) for g in groups),
        total=total,
    )


@router.post("/reextract/preview", response_model=VideoReextractResult)
async def preview_reextract(body: VideoReextractRequest, db: AsyncSession = Depends(get_db)):
    """Resolve a re-extraction without writing anything. Drives the modal's accounting.

    Both this and `POST /videos/reextract` are literal segments under `/videos/`
    and are POSTs, so `GET /videos/{video_id}` cannot shadow them — but
    `test_video_reextract_http.py` pins that rather than trusting it, the same way
    `GET /videos/capabilities` is pinned.
    """
    groups, skipped, total = await _resolve_reextract_targets(db, body)
    return _reextract_result(groups, skipped, total)


@router.post("/reextract", response_model=VideoReextractResult)
async def reextract_frames(body: VideoReextractRequest, db: AsyncSession = Depends(get_db)):
    """Re-cut already-extracted frames from their source video at full resolution.

    One job per video — the grouping is what lets each label name its video, and
    what keeps a decode handle open on one file at a time.

    The job does **not** hold `dataset_busy` (following `batch_upscale`); the
    endpoint only refuses to start while something else does.
    """
    groups, skipped, total = await _resolve_reextract_targets(db, body)
    if not groups:
        return _reextract_result(groups, skipped, total)

    for dataset_id in {g["video"].dataset_id for g in groups}:
        ensure_not_busy(dataset_id)

    # The gate tests the *stored* filter, which is what the job will replay —
    # there is no request field to override it here. Without this the job dies
    # inside every frame and reports a missing package as a decode fault.
    caps = video_extract.capabilities()
    for g in groups:
        if (g["video"].deinterlace or "") and not caps["deinterlace"]:
            raise HTTPException(503, DEINTERLACE_UNAVAILABLE)

    needed: dict[str, int] = {}
    for g in groups:
        v = g["video"]
        w = v.crop_w or v.width or 1920
        h = v.crop_h or v.height or 1080
        if body.max_long_edge and max(w, h) > body.max_long_edge:
            scale = body.max_long_edge / max(w, h)
            w, h = max(1, int(w * scale)), max(1, int(h * scale))
        per_frame = int(w * h * REEXTRACT_BYTES_PER_PIXEL[body.format])
        needed[v.dataset_id] = needed.get(v.dataset_id, 0) + per_frame * len(g["frames"])
    for dataset_id, size in needed.items():
        ds = await db.get(Dataset, dataset_id)
        if ds:
            try:
                require_free_space(Path(ds.folder_path) / "images", size)
            except InsufficientDiskSpaceError as exc:
                raise HTTPException(507, str(exc)) from None

    job_ids: dict[str, str] = {}
    for g in groups:
        v = g["video"]
        n = len(g["frames"])
        # `[:60]` is not cosmetic — `BackgroundJob.label` is a String(200).
        auto_label = (
            f"Re-extract: {Path(v.filename).stem[:60]} — "
            f"{n} frame{'' if n == 1 else 's'} at full res"
        )
        config = {
            "video_id": v.id,
            "image_ids": [f["image_id"] for f in g["frames"]],
            "format": body.format,
            "max_long_edge": body.max_long_edge,
            "label": body.label,
        }
        job = BackgroundJob(
            job_type="video_reextract",
            label=body.label or auto_label,
            dataset_id=v.dataset_id,
            total_items=n,
            config=config,
        )
        db.add(job)
        await db.commit()
        await job_queue.enqueue(job, _make_reextract_runner(config, v.dataset_id))
        job_ids[v.id] = job.id

    return _reextract_result(groups, skipped, total, job_ids)


def _make_reextract_runner(cfg: dict, dataset_id: str):
    """Build the worker coroutine for one video's re-extraction job.

    `_run_with_stats` is `_make_extract_runner`'s, verbatim and for the same
    reasons: the circuit breaker, `raise_if_cancelled` and the disk recheck all
    raise past the final `refresh_stats` having committed real rewrites.
    """

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await _run_reextraction(session, job_id, cfg)

    async def _run_with_stats(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.services.dataset_service import refresh_stats

        try:
            await _run(job_id)
        except BaseException:
            try:
                async with AsyncSessionLocal() as stats_session:
                    await refresh_stats(stats_session, dataset_id)
            except Exception:
                logger.warning("video_reextract: final stats refresh failed", exc_info=True)
            raise

    return _run_with_stats


async def _run_reextraction(session: AsyncSession, job_id: str, cfg: dict) -> None:
    """The `video_reextract` worker.

    **The per-frame ordering is deliberately better than the upscale path's.**
    Upscale and LUT overwrite the original and only then discover whether the
    result re-opens; this writes a temp beside the image, verifies it, and only
    swaps once it is known good. A frame that fails leaves its original intact.
    """
    loop = asyncio.get_running_loop()
    video = await session.get(Video, cfg["video_id"])
    if video is None:
        raise RuntimeError("The video was deleted before re-extraction started")
    dataset = await session.get(Dataset, video.dataset_id)
    if dataset is None:
        raise RuntimeError("The dataset was deleted before re-extraction started")

    src = Path(video.file_path)
    images_dir = Path(dataset.folder_path) / "images"
    fmt = cfg["format"]
    suffix = ".png" if fmt == "png" else ".jpg"
    long_edge = cfg.get("max_long_edge") or 0
    # Geometry replays verbatim: these are the *normalized* values the extract
    # endpoint stored, so pass 2 applies them as-is. Trims never mattered to a
    # direct seek.
    crop = None
    if video.crop_w and video.crop_h:
        crop = (video.crop_x or 0, video.crop_y or 0, video.crop_w, video.crop_h)
    deinterlace = video.deinterlace or ""

    image_ids: list[str] = cfg["image_ids"]
    rows: list[Image] = []
    for chunk in chunked(image_ids):
        rows.extend(
            (await session.execute(select(Image).where(Image.id.in_(chunk)))).scalars().all()
        )
    # A row can be deleted between enqueue and run; a timestamp cannot come back.
    rows = [r for r in rows if r.source_timestamp_ms is not None]
    # Ascending: each frame opens its own decoder, so this buys page-cache
    # locality on the container rather than a single forward pass.
    rows.sort(key=lambda r: r.source_timestamp_ms)

    counts = {
        "rewritten": 0, "failed": 0, "skipped": len(image_ids) - len(rows),
        # A frame whose thumbnail could not be regenerated is still `rewritten`:
        # the file and the row agree and are committed. This counts the stale
        # thumbnails so the outcome is visible in `result_data` rather than lost.
        "thumbnails_stale": 0,
        "video_id": video.id, "format": fmt, "note": REEXTRACT_NOTE,
    }
    total = len(rows)
    require_free_space(images_dir, 0)

    async def _rewrite(img: Image) -> tuple[str, bool] | None:
        """Rewrite one frame in place.

        Returns None on success, else `(reason, is_fault)`. `is_fault` is False
        for a refusal that says nothing about the video's readability — a name
        collision — so a run cannot trip the consecutive-failure breaker over
        one. Either way the frame counts as `failed`: it was asked for and not
        delivered.

        On success the row has already been committed: nothing fallible runs
        between the swap and that commit, so disk and DB can never disagree.
        """
        src_path = Path(img.file_path)
        target = src_path.with_suffix(suffix)
        tmp = src_path.with_name(f"{src_path.stem}.{uuid4().hex}.tmp{suffix}")
        superseded: Path | None = None
        try:
            try:
                result = await loop.run_in_executor(None, partial(
                    video_extract.render_at_timestamps,
                    src,
                    [float(img.source_timestamp_ms)],
                    dests=[(str(tmp), None)],
                    crop=crop,
                    deinterlace=deinterlace,
                    long_edge=long_edge,
                ))
            except Exception as exc:
                logger.warning("video_reextract: %s could not be decoded: %s", img.filename, exc)
                return "decode failed", True
            if not result.written:
                return "no frame decoded at that timestamp", True
            tmp = Path(result.written[0].path)

            # Verify *before* anything is destroyed. `get_image_info` swallows every
            # exception, so `{}` means the file just written will not re-open — and
            # unlike upscale/LUT, the original is still sitting there untouched.
            info = await loop.run_in_executor(None, get_image_info, str(tmp))
            if not info:
                return "the written frame would not re-open", True

            if target != src_path and target.exists():
                # An unregistered file — hand-dropped into images/ and not yet
                # rescanned, so no DB row guards it. The only real hazard in the
                # extension swap; never clobber it. Not a fault: the video decodes
                # fine, so the rest of the run must still get its chance.
                return f"{target.name} already exists on disk", False

            # Mandatory before an in-place overwrite, and so is the commit: the hook
            # only *flushes* the hash backfill, so a crash during the swap would roll
            # it back and leave a pre-existing snapshot claiming "content unchanged".
            await version_service.protect_file_before_overwrite(img.id, img.file_path, session)
            await session.commit()

            os.replace(tmp, target)
            if target != src_path:
                # A *pure* extension change: the stem never moves, so the thumbnail
                # ({stem}.webp) and the caption sidecar ({stem}.txt) both stay exactly
                # where they are — no rename_with_sidecar, no thumbnail move. The name
                # is provably free because every image-name-picking site goes through
                # `unique_filename_with_thumb`, which rejects a candidate whose *stem*
                # is occupied in any extension; the only file that can be sitting there
                # is an unregistered one, refused above.
                #
                # The superseded original is unlinked in the epilogue, after the
                # commit: `unlink` is itself fallible, and a failure here would
                # leave the swap done and the row uncommitted.
                superseded = src_path
                img.filename = target.name
                img.file_path = str(target)

            now = datetime.now(timezone.utc)
            img.width = info["width"]
            img.height = info["height"]
            img.file_size_bytes = info["file_size_bytes"]
            img.format = info["format"]
            # phash is re-derived even though the scores are not: dedup depends on it,
            # and a full-res frame hashes differently from its 1024px triage version.
            img.phash = info["phash"]
            # What busts `imagesApi.thumbnailUrlVersioned` — without it an open detail
            # pane keeps showing the triage thumbnail.
            img.updated_at = now
            img.processing_history = (img.processing_history or []) + [{
                "op": "reextract",
                "video_id": video.id,
                "timestamp_ms": img.source_timestamp_ms,
                "format": fmt,
                "long_edge": long_edge,
                "at": now.isoformat(),
            }]
            # Nothing fallible may sit between the `os.replace` above and this
            # commit: the swap is irreversible, so a raise before here would roll
            # the row back onto a file that no longer exists (the 404 of PM-013).
            # The epilogue below is the place for anything that can fail.
            await session.commit()

            # --- epilogue: best-effort, cannot change this frame's outcome ---
            # `AsyncSessionLocal` is built with `expire_on_commit=False`
            # (backend/database.py), so `img`'s attributes stay readable after the
            # commit without a refresh. Flipping that setting would break this.
            if superseded is not None:
                try:
                    superseded.unlink(missing_ok=True)
                except OSError as exc:
                    # The row already names the new file, which exists. The old one
                    # is orphan residue a rescan can adopt — not a failed frame.
                    logger.warning(
                        "video_reextract: could not remove superseded %s: %s",
                        superseded.name, exc,
                    )
            if img.thumbnail_path:
                try:
                    await loop.run_in_executor(
                        None, generate_thumbnail, str(target), img.thumbnail_path
                    )
                except Exception as exc:
                    # A stale thumbnail is cosmetic and self-healing; counting the
                    # frame failed would both lie and feed the failure breaker.
                    counts["thumbnails_stale"] += 1
                    logger.warning(
                        "video_reextract: thumbnail for %s could not be regenerated: %s",
                        target.name, exc,
                    )
            return None
        finally:
            # The temp carries a real image extension and sits in `images/`, where
            # `rescan_dataset` would adopt it as a new image. Every handled return
            # is covered here, and so is any raise inside the `try`.
            # `tmp` is rebound to the written path after the render, so this must
            # read the live binding; after a successful `os.replace` it is gone and
            # `missing_ok` makes the unlink a no-op.
            tmp.unlink(missing_ok=True)

    consecutive_failures = 0
    written_since_disk_check = 0
    cancelled = False

    for i, img in enumerate(rows):
        if job_queue.cancel_requested(job_id):
            cancelled = True
            break

        reason = ""
        outcome = await _rewrite(img)
        if outcome is None:
            counts["rewritten"] += 1
            consecutive_failures = 0
            written_since_disk_check += 1
        else:
            reason, is_fault = outcome
            counts["failed"] += 1
            # A refusal that says nothing about the video's readability must not
            # feed the breaker, or one squatting file per frame aborts the run.
            if is_fault:
                consecutive_failures += 1
            logger.info("video_reextract: skipped %s — %s", img.filename, reason)

        # A no-op on every path — `_rewrite` commits the frame itself, and its
        # refusals mutate nothing — but kept, and kept here: `_rewrite` already
        # has to commit mid-frame for the COW hook, so a batching threshold would
        # never be reached and `done` would lag one frame behind forever. Three
        # lines above the emit is where "`done` is the committed count" — the
        # PM-008 invariant — is provable at a glance.
        await session.commit()
        await _emit(
            job_id, "rewriting", (i + 1) / max(total, 1),
            video_id=video.id, job_type="video_reextract",
            done=counts["rewritten"], total=total,
            image_id=img.id,
            message=f"Frame {i + 1} of {total}",
        )

        if consecutive_failures >= EXTRACT_MAX_CONSECUTIVE_FAILURES:
            # Frames already rewritten stay — they are real, and their COW backups
            # exist. The job is `failed`, not `cancelled`: nobody asked for this.
            raise RuntimeError(
                f"Re-extraction stopped after {consecutive_failures} consecutive failures "
                f"({reason}). {counts['rewritten']} frame(s) already rewritten have been kept."
            )

        if written_since_disk_check >= EXTRACT_DISK_RECHECK_EVERY:
            # After the emit: `require_free_space` raises out of the job, and the
            # frame that filled the disk should still have published its progress
            # event first. Row/disk agreement no longer depends on this placement
            # — `_rewrite` commits each frame itself.
            written_since_disk_check = 0
            require_free_space(images_dir, 0)

    job_row = await session.get(BackgroundJob, job_id)
    if job_row:
        job_row.result_data = counts
    await session.commit()
    if cancelled:
        # Everything already written stays: the files are real and their COW
        # backups exist.
        job_queue.raise_if_cancelled(job_id)

    await refresh_stats(session, dataset.id)


@router.patch("/{video_id}/rename")
async def rename_video(video_id: str, body: RenameVideoRequest, db: AsyncSession = Depends(get_db)):
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    ensure_not_busy(video.dataset_id)

    raw = body.new_stem.strip()
    if not raw or "/" in raw or "\\" in raw or len(raw) > 200:
        raise HTTPException(400, "Invalid new_stem")
    slug = slugify_filename(raw)
    if not slug:
        raise HTTPException(400, "Stem produces empty slug")

    old_path = Path(video.file_path)
    existing = await db.execute(
        select(Video.id, Video.filename, Video.poster_path).where(Video.dataset_id == video.dataset_id)
    )
    sibling_rows = [(r.id, r.filename, r.poster_path) for r in existing.all()]
    db_names: set[str] = {fn for vid, fn, _ in sibling_rows if vid != video_id}

    # Occupied poster stems come from the sibling *rows* as well as from disk —
    # a row whose poster has never been cut has nothing in videos/thumbnails/,
    # so globbing alone would let this rename take a stem that `a.mkv` will claim
    # on its first view. `claimed_poster_stems` also folds in stored poster
    # stems, which can differ from their row's filename stem once rescan has
    # disambiguated one.
    poster_dir = old_path.parent / "thumbnails"
    occupied = claimed_poster_stems(sibling_rows, poster_dir, exclude_id=video_id)
    # This row's own stems do not block it. Both, because they can differ: the
    # row-side terms are already gone via exclude_id, but its own poster file is
    # still in the directory glob.
    occupied.discard(old_path.stem)
    if video.poster_path:
        occupied.discard(Path(video.poster_path).stem)
    planned: set[str] = set()

    # The suffix is preserved and never user-settable: the container extension is
    # a claim about the bytes, and video_mime picks the browser's decoder from it.
    new_filename = unique_filename_with_thumb(
        old_path.parent, slug, old_path.suffix.lower(), db_names, occupied, planned
    )
    new_path = old_path.parent / new_filename
    new_poster = Path(poster_path_for(new_path))
    old_poster = Path(video.poster_path) if video.poster_path else None

    video.filename = new_filename
    video.file_path = str(new_path)
    if old_poster is not None:
        video.poster_path = str(new_poster)
    # Path.rename, not rename_with_sidecar: a video has no .txt companion —
    # captions belong to the frames extracted from it.
    old_path.rename(new_path)  # FS last — if this raises, commit never runs
    if old_poster is not None and old_poster.exists() and old_poster != new_poster:
        old_poster.replace(new_poster)
    await db.commit()
    return {"filename": new_filename}


@router.delete("/{video_id}", status_code=204)
async def delete_video(video_id: str, db: AsyncSession = Depends(get_db)):
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    dataset_id = video.dataset_id
    ensure_not_busy(dataset_id)

    # Frames already extracted from this video are ordinary Image rows and are
    # deliberately left alone — deleting a source must not destroy curated data.
    # Their lineage is cut explicitly rather than left to the FK's ON DELETE SET
    # NULL: the test harness builds its schema with `create_all` and never gets
    # the `PRAGMA foreign_keys=ON` that backend/database.py installs, so the FK's
    # behaviour is untestable end to end here. The FK stays as belt-and-braces.
    # The timestamp and shot index survive — a frame keeps knowing where in a
    # video it came from even once the video is gone.
    orphaned = await db.execute(
        sa_update(Image).where(Image.source_video_id == video_id).values(source_video_id=None)
    )
    frames_orphaned = orphaned.rowcount or 0

    # Files first, row second — the reverse of delete_image, on purpose. If the
    # commit below fails, the row survives pointing at nothing, which
    # _rescan_videos reports under `videos_missing` and the user can retry.
    # Committing first and then failing the unlink would leave an orphan in
    # videos/ that the next rescan silently re-registers, undoing the delete.
    for candidate in (video.file_path, video.poster_path):
        if candidate:
            Path(candidate).unlink(missing_ok=True)

    await db.delete(video)
    await db.commit()
    await refresh_stats(db, dataset_id)
    # 204 carries no body, and the confirm dialog that needs this number runs
    # *before* the delete anyway — it reads GET /videos/{id}/frames-summary,
    # which this rowcount is the server-side counterpart of.
    # Logged because "the frames survived" is the non-obvious half of the
    # contract and the only place it is observable server-side.
    if frames_orphaned:
        logger.info("delete_video %s: %d extracted frame(s) kept, lineage cleared", video_id, frames_orphaned)

