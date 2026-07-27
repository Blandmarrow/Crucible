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
from backend.schemas.video import VideoOut
from backend.services.dataset_busy import ensure_not_busy
from backend.services.dataset_service import refresh_stats
from backend.utils import safe_dataset_path

router = APIRouter(prefix="/videos", tags=["videos"])


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
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if not video.poster_path:
        raise HTTPException(404, "No poster frame for this video")
    p = safe_dataset_path(video.poster_path, settings.datasets_dir)
    if not p.exists():
        raise HTTPException(404, "Poster not found on disk")
    return FileResponse(str(p), media_type="image/webp")


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
