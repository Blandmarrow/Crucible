import asyncio
import json
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import BackgroundJob, Dataset, Image
from backend.schemas.image import (
    BatchCropRequest,
    BatchResizeRequest,
    ImageCropRequest,
    ImageListItem,
    ImageOut,
    ImageResizeRequest,
)
from backend.services.dataset_service import refresh_stats
from backend.services.image_service import (
    crop_image,
    crop_to_aspect,
    extract_generation_metadata,
    generate_thumbnail,
    get_image_info,
    resize_image,
)
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/images", tags=["images"])

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}

_ALLOWED_SCORE_FIELDS = frozenset({
    "aesthetic_score", "blur_score", "noise_score", "uniformity_score",
    "watermark_score", "color_score", "saturation_score", "style_similarity_score",
})
_ALLOWED_FLAG_KEYS = frozenset({
    "is_blurry", "is_noisy", "is_uniform", "has_watermark", "is_duplicate",
})


def _safe_path(path_str: str, base_dir: Path) -> Path:
    resolved = Path(path_str).resolve()
    if not str(resolved).startswith(str(base_dir.resolve())):
        raise HTTPException(403, "Access denied")
    return resolved


@router.get("/", response_model=list[ImageListItem])
async def list_images(
    dataset_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    sort: str = "created_at",
    order: str = "desc",
    captioned: bool | None = None,
    search: str | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    score_field: str | None = None,
    score_is_null: bool | None = None,
    quality_flag: str | None = None,
    file_size_min: int | None = None,
    file_size_max: int | None = None,
    mp_min: float | None = None,
    mp_max: float | None = None,
    ar_min: float | None = None,
    ar_max: float | None = None,
    format_filter: str | None = None,
    score_filters: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if score_field and score_field not in _ALLOWED_SCORE_FIELDS:
        raise HTTPException(400, f"Invalid score_field: {score_field}")
    if quality_flag and quality_flag not in _ALLOWED_FLAG_KEYS:
        raise HTTPException(400, f"Invalid quality_flag: {quality_flag}")

    q = select(Image).where(Image.dataset_id == dataset_id)

    if captioned is True:
        q = q.where(Image.caption_text != "")
    elif captioned is False:
        q = q.where(Image.caption_text == "")

    if search:
        term = f"%{search}%"
        q = q.where(or_(Image.original_filename.ilike(term), Image.caption_text.ilike(term)))

    # Score filtering — score_field selects the column; defaults to aesthetic_score
    score_col = getattr(Image, score_field) if score_field else Image.aesthetic_score
    if score_is_null is True:
        q = q.where(score_col.is_(None))
    else:
        if min_score is not None:
            q = q.where(score_col >= min_score)
        if max_score is not None:
            q = q.where(score_col <= max_score)

    if quality_flag:
        q = q.where(Image.quality_flags[quality_flag].as_boolean() == True)  # noqa: E712

    if file_size_min is not None:
        q = q.where(Image.file_size_bytes >= file_size_min)
    if file_size_max is not None:
        q = q.where(Image.file_size_bytes <= file_size_max)

    if mp_min is not None:
        q = q.where(Image.width * Image.height >= int(mp_min * 1_000_000))
    if mp_max is not None:
        q = q.where(Image.width * Image.height < int(mp_max * 1_000_000))

    if ar_min is not None:
        q = q.where(Image.width >= ar_min * Image.height)
    if ar_max is not None:
        q = q.where(Image.width < ar_max * Image.height)

    if format_filter:
        q = q.where(Image.format == format_filter)

    if score_filters:
        try:
            for f in json.loads(score_filters):
                field = f.get("field", "")
                if field not in _ALLOWED_SCORE_FIELDS:
                    continue
                col = getattr(Image, field)
                mn = f.get("min")
                mx = f.get("max")
                if mn is not None:
                    q = q.where(col >= float(mn))
                if mx is not None:
                    q = q.where(col <= float(mx))
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass

    sort_col = getattr(Image, sort, Image.created_at)
    q = q.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    q = q.offset((page - 1) * limit).limit(limit)

    result = await db.execute(q)
    return result.scalars().all()


@router.post("/upload", status_code=201)
async def upload_images(
    dataset_id: str,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    dest_images = Path(ds.folder_path) / "images"
    dest_thumbs = Path(ds.folder_path) / "thumbnails"
    added = []

    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            continue
        filename = f"{uuid4().hex}{suffix}"
        dest = dest_images / filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(upload.file, f)

        info = get_image_info(str(dest))
        gen_meta = extract_generation_metadata(str(dest))
        thumb_path = str(dest_thumbs / (dest.stem + ".webp"))
        await asyncio.get_event_loop().run_in_executor(None, generate_thumbnail, str(dest), thumb_path)

        img = Image(
            dataset_id=dataset_id,
            filename=filename,
            original_filename=upload.filename or filename,
            file_path=str(dest),
            thumbnail_path=thumb_path,
            generation_metadata=gen_meta,
            **info,
        )
        db.add(img)
        added.append(filename)

    await db.commit()
    await refresh_stats(db, dataset_id)
    return {"added": len(added), "files": added}


@router.get("/{image_id}", response_model=ImageOut)
async def get_image(image_id: str, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    if img.generation_metadata is None and img.file_path and Path(img.file_path).exists():
        gen_meta = await asyncio.get_event_loop().run_in_executor(
            None, extract_generation_metadata, img.file_path
        )
        if gen_meta:
            img.generation_metadata = gen_meta
            await db.commit()
    return img


@router.delete("/{image_id}", status_code=204)
async def delete_image(image_id: str, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    dataset_id = img.dataset_id
    p = Path(img.file_path)
    t = Path(img.thumbnail_path) if img.thumbnail_path else None
    txt = p.with_suffix(".txt")
    await db.delete(img)
    await db.commit()
    for f in [p, t, txt]:
        if f and f.exists():
            f.unlink(missing_ok=True)
    await refresh_stats(db, dataset_id)


@router.delete("/batch/delete", status_code=204)
async def batch_delete(image_ids: list[str], db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Image.id, Image.dataset_id, Image.file_path, Image.thumbnail_path)
        .where(Image.id.in_(image_ids))
    )
    rows = result.all()
    if not rows:
        return

    dataset_ids = {r.dataset_id for r in rows}
    files_to_delete: list[Path] = []
    for r in rows:
        p = Path(r.file_path)
        files_to_delete.extend([p, p.with_suffix(".txt")])
        if r.thumbnail_path:
            files_to_delete.append(Path(r.thumbnail_path))

    await db.execute(delete(Image).where(Image.id.in_(image_ids)))
    await db.commit()

    for f in files_to_delete:
        f.unlink(missing_ok=True)

    for did in dataset_ids:
        await refresh_stats(db, did)


@router.get("/{image_id}/file")
async def serve_file(image_id: str, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    p = _safe_path(img.file_path, settings.datasets_dir)
    if not p.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(str(p))


@router.get("/{image_id}/thumbnail")
async def serve_thumbnail(image_id: str, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    if img.thumbnail_path and Path(img.thumbnail_path).exists():
        return FileResponse(img.thumbnail_path)
    # Fallback: generate on demand
    p = _safe_path(img.file_path, settings.datasets_dir)
    thumb = str(p.parent.parent / "thumbnails" / (p.stem + ".webp"))
    await asyncio.get_event_loop().run_in_executor(None, generate_thumbnail, str(p), thumb)
    img.thumbnail_path = thumb
    await db.commit()
    return FileResponse(thumb)


@router.post("/{image_id}/resize")
async def resize(image_id: str, body: ImageResizeRequest, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    new_w, new_h = await asyncio.get_event_loop().run_in_executor(
        None, resize_image, img.file_path, body.width, body.height, body.scale, body.maintain_ar, body.resample
    )
    img.width, img.height = new_w, new_h
    # Regenerate thumbnail
    await asyncio.get_event_loop().run_in_executor(None, generate_thumbnail, img.file_path, img.thumbnail_path)
    await db.commit()
    return {"width": new_w, "height": new_h}


@router.post("/{image_id}/crop")
async def crop(image_id: str, body: ImageCropRequest, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    new_w, new_h = await asyncio.get_event_loop().run_in_executor(
        None, crop_image, img.file_path, body.x, body.y, body.width, body.height
    )
    img.width, img.height = new_w, new_h
    await asyncio.get_event_loop().run_in_executor(None, generate_thumbnail, img.file_path, img.thumbnail_path)
    await db.commit()
    return {"width": new_w, "height": new_h}


@router.post("/batch/resize")
async def batch_resize(body: BatchResizeRequest, db: AsyncSession = Depends(get_db)):
    job = BackgroundJob(job_type="batch_resize", total_items=len(body.image_ids), config=body.model_dump())
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.workers.progress import broadcaster
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Image).where(Image.id.in_(body.image_ids)))
            images = result.scalars().all()
            for i, img in enumerate(images):
                loop = asyncio.get_event_loop()
                new_w, new_h = await loop.run_in_executor(
                    None, resize_image, img.file_path, body.width, body.height, body.scale, body.maintain_ar
                )
                img.width, img.height = new_w, new_h
                if img.thumbnail_path:
                    await loop.run_in_executor(None, generate_thumbnail, img.file_path, img.thumbnail_path)
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_resize",
                    "status": "running", "done": i + 1, "total": len(images),
                    "percent": round((i + 1) / len(images) * 100, 1),
                    "current_item": img.filename,
                })
            await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/batch/crop")
async def batch_crop(body: BatchCropRequest, db: AsyncSession = Depends(get_db)):
    job = BackgroundJob(job_type="batch_crop", total_items=len(body.image_ids), config=body.model_dump())
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.workers.progress import broadcaster
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Image).where(Image.id.in_(body.image_ids)))
            images = result.scalars().all()
            for i, img in enumerate(images):
                loop = asyncio.get_event_loop()
                new_w, new_h = await loop.run_in_executor(
                    None, crop_to_aspect, img.file_path, body.target_ar, body.strategy
                )
                img.width, img.height = new_w, new_h
                if img.thumbnail_path:
                    await loop.run_in_executor(None, generate_thumbnail, img.file_path, img.thumbnail_path)
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_crop",
                    "status": "running", "done": i + 1, "total": len(images),
                    "percent": round((i + 1) / len(images) * 100, 1),
                    "current_item": img.filename,
                })
            await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}
