import asyncio
import time
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.licenses import resolve_provenance
from backend.media_types import codec_label, video_mime
from backend.models import Dataset, Video
from backend.schemas.video import RenameVideoRequest, VideoOut
from backend.services.dataset_busy import ensure_not_busy
from backend.services.dataset_service import refresh_stats
from backend.services.video_service import claimed_poster_stems, generate_poster
from backend.utils import (
    poster_path_for,
    safe_dataset_path,
    slugify_filename,
    unique_filename_with_thumb,
    unique_poster_path,
)

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
    #
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
