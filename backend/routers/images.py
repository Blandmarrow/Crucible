import asyncio
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, delete, func, or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from backend.config import settings
from backend.utils import ALLOWED_FLAG_KEYS, copy_with_sidecar, normalize_subfolder, rename_with_sidecar, slugify_filename, thumbnail_path_for, unique_filename
from backend.database import get_db
from backend.models import BackgroundJob, Dataset, Image
from backend.models.detection import Detection
from backend.schemas.detection import DetectionOut
from backend.schemas.image import (
    BatchCopyDatasetResult,
    BatchCropRequest,
    BatchMoveDatasetRequest,
    BatchMoveSubfolderRequest,
    BatchResizeRequest,
    BulkCountRequest,
    BulkDeleteRequest,
    BulkRenameRequest,
    ImageCropRequest,
    ImageListItem,
    ImageOut,
    ImageResizeRequest,
    RenameImageRequest,
)
from backend.services.dataset_service import refresh_stats
from backend.services import version_service
from backend.services.image_service import (
    crop_image_to_dest,
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

def _safe_path(path_str: str, base_dir: Path) -> Path:
    resolved = Path(path_str).resolve()
    if not str(resolved).startswith(str(base_dir.resolve())):
        raise HTTPException(403, "Access denied")
    return resolved


def _apply_bulk_filters(query, image_ids, subfolder, quality_flags):
    if image_ids is not None:
        query = query.where(Image.id.in_(image_ids))
    elif subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(subfolder))
    if quality_flags:
        valid_flags = [f for f in quality_flags if f in ALLOWED_FLAG_KEYS]
        if valid_flags:
            query = query.where(and_(*[Image.quality_flags[f].as_boolean().is_not(True) for f in valid_flags]))
    return query



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
    subfolder: str | None = Query(None),
    detection_label: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if score_field and score_field not in _ALLOWED_SCORE_FIELDS:
        raise HTTPException(400, f"Invalid score_field: {score_field}")
    if quality_flag and quality_flag not in ALLOWED_FLAG_KEYS:
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

    if subfolder is not None:
        escaped_subfolder = subfolder.replace("%", r"\%").replace("_", r"\_")
        q = q.where(
            (Image.subfolder == subfolder)
            | Image.subfolder.like(escaped_subfolder + "/%", escape="\\")
        )

    if detection_label:
        q = q.where(
            select(Detection.id)
            .where(Detection.image_id == Image.id, Detection.label.ilike(f"%{detection_label}%"))
            .exists()
        )

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
    subfolder: str = Query(""),
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    dest_images = Path(ds.folder_path) / "images"
    dest_thumbs = Path(ds.folder_path) / "thumbnails"
    added = []

    existing_result = await db.execute(select(Image.filename).where(Image.dataset_id == dataset_id))
    db_names: set[str] = {r[0] for r in existing_result.all()}

    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            continue
        raw_stem = Path(upload.filename or "").stem
        slug = slugify_filename(raw_stem) or "image"
        filename = unique_filename(dest_images, slug, suffix, db_names)
        db_names.add(filename)
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
            subfolder=normalize_subfolder(subfolder),
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
    img = await db.get(Image, image_id, options=[undefer(Image.dino_layer_embeddings)])
    if not img:
        raise HTTPException(404, "Image not found")
    if img.generation_metadata is None and img.file_path and Path(img.file_path).exists():
        gen_meta = await asyncio.get_event_loop().run_in_executor(
            None, extract_generation_metadata, img.file_path
        )
        if gen_meta:
            img.generation_metadata = gen_meta
            await db.commit()
    det_result = await db.execute(
        select(Detection).where(Detection.image_id == image_id).order_by(Detection.id)
    )
    detections = det_result.scalars().all()
    img_out = ImageOut.model_validate(img)
    img_out.detections = [DetectionOut.model_validate(d) for d in detections]
    return img_out


@router.delete("/{image_id}", status_code=204)
async def delete_image(image_id: str, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    dataset_id = img.dataset_id
    p = Path(img.file_path)
    t = Path(img.thumbnail_path) if img.thumbnail_path else None
    txt = p.with_suffix(".txt")
    await version_service.mark_image_deleted_in_versions(img.id, img.file_path, db)
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
        await version_service.mark_image_deleted_in_versions(r.id, r.file_path, db)
        files_to_delete.extend([p, p.with_suffix(".txt")])
        if r.thumbnail_path:
            files_to_delete.append(Path(r.thumbnail_path))

    await db.execute(delete(Image).where(Image.id.in_(image_ids)))
    await db.commit()

    for f in files_to_delete:
        f.unlink(missing_ok=True)

    for did in dataset_ids:
        await refresh_stats(db, did)


@router.post("/bulk-count")
async def bulk_count(body: BulkCountRequest, db: AsyncSession = Depends(get_db)):
    query = _apply_bulk_filters(
        select(func.count(Image.id)).where(Image.dataset_id == body.dataset_id),
        body.image_ids, body.subfolder, body.quality_flags,
    )
    count = (await db.execute(query)).scalar_one()
    return {"count": count}


@router.post("/bulk-rename")
async def bulk_rename(body: BulkRenameRequest, db: AsyncSession = Depends(get_db)):
    raw = body.new_stem.strip()
    if not raw:
        raise HTTPException(400, "new_stem cannot be empty")
    stem = slugify_filename(raw)
    if not stem:
        raise HTTPException(400, "new_stem produces empty slug")

    query = _apply_bulk_filters(
        select(Image.id, Image.file_path, Image.filename).where(Image.dataset_id == body.dataset_id),
        body.image_ids, body.subfolder, body.quality_flags,
    ).order_by(Image.created_at)

    rows = (await db.execute(query)).all()
    if not rows:
        return {"affected": 0}

    images_dir = Path(rows[0].file_path).parent

    batch_ids = [r.id for r in rows]
    existing = await db.execute(
        select(Image.filename).where(
            Image.dataset_id == body.dataset_id,
            ~Image.id.in_(batch_ids),
        )
    )
    db_names: set[str] = {r[0] for r in existing.all()}

    plan: list[tuple[Path, Path, str, str]] = []
    for row in rows:
        old_path = Path(row.file_path)
        new_filename = unique_filename(images_dir, stem, old_path.suffix.lower(), db_names)
        new_path = images_dir / new_filename
        db_names.add(new_filename)
        plan.append((old_path, new_path, row.id, new_filename))

    await db.execute(
        sa_update(Image),
        [{"id": img_id, "filename": new_fn, "file_path": str(new_path), "is_auto_named": True}
         for _, new_path, img_id, new_fn in plan],
    )

    for old_path, new_path, *_ in plan:
        if new_path != old_path:
            rename_with_sidecar(old_path, new_path)

    await db.commit()
    return {"affected": len(plan)}


@router.post("/bulk-delete")
async def bulk_delete_filtered(body: BulkDeleteRequest, db: AsyncSession = Depends(get_db)):
    query = _apply_bulk_filters(
        select(Image.id, Image.dataset_id, Image.file_path, Image.thumbnail_path).where(
            Image.dataset_id == body.dataset_id
        ),
        body.image_ids, body.subfolder, body.quality_flags,
    )

    result = await db.execute(query)
    rows = result.all()
    if not rows:
        return {"deleted": 0}

    image_ids = [r.id for r in rows]
    files_to_delete: list[Path] = []
    for r in rows:
        p = Path(r.file_path)
        await version_service.mark_image_deleted_in_versions(r.id, r.file_path, db)
        files_to_delete.extend([p, p.with_suffix(".txt")])
        if r.thumbnail_path:
            files_to_delete.append(Path(r.thumbnail_path))

    await db.execute(delete(Image).where(Image.id.in_(image_ids)))
    await db.commit()

    for f in files_to_delete:
        f.unlink(missing_ok=True)

    await refresh_stats(db, body.dataset_id)
    return {"deleted": len(image_ids)}


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
    await version_service.protect_file_before_overwrite(image_id, img.file_path, db)
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

    src_path = Path(img.file_path)
    dest_images = src_path.parent
    dest_thumbs = src_path.parent.parent / "thumbnails"
    loop = asyncio.get_running_loop()

    # --- Replace mode: overwrite the original image ---
    if body.replace:
        await version_service.protect_file_before_overwrite(img.id, img.file_path, db)
        tmp_path = src_path.with_name(src_path.stem + "_croptmp" + src_path.suffix)

        if not body.upscale_model:
            # Synchronous replace crop
            info = await loop.run_in_executor(
                None, crop_image_to_dest,
                str(src_path), str(tmp_path),
                body.x, body.y, body.width, body.height,
                body.output_width, body.output_height,
            )
            tmp_path.replace(src_path)
            if img.thumbnail_path:
                await loop.run_in_executor(None, generate_thumbnail, str(src_path), img.thumbnail_path)
            img.width = info["width"]
            img.height = info["height"]
            img.file_size_bytes = info["file_size_bytes"]
            img.format = info["format"]
            img.phash = info["phash"]
            await db.commit()
            return {"id": img.id, "filename": img.filename, "width": img.width, "height": img.height}

        # Replace + upscale: crop to temp, enqueue job that upscales to original path
        await loop.run_in_executor(
            None, crop_image_to_dest,
            str(src_path), str(tmp_path),
            body.x, body.y, body.width, body.height,
            body.output_width, body.output_height,
        )
        replace_cfg = {
            "tmp_path": str(tmp_path),
            "dest_path": str(src_path),
            "thumb_path": img.thumbnail_path or thumbnail_path_for(src_path),
            "image_id": img.id,
            "upscale_model": body.upscale_model,
            "upscale_target_width": body.upscale_target_width,
            "upscale_target_height": body.upscale_target_height,
        }
        job = BackgroundJob(job_type="crop_upscale", dataset_id=img.dataset_id, total_items=1, config=replace_cfg)
        db.add(job)
        await db.commit()

        async def _run_crop_upscale_replace(job_id: str) -> None:
            import os
            from backend.database import AsyncSessionLocal
            from backend.ml.upscaler import upscale_image_sync
            from backend.workers.progress import broadcaster

            loop2 = asyncio.get_running_loop()
            try:
                info = await loop2.run_in_executor(
                    None, upscale_image_sync,
                    replace_cfg["tmp_path"], replace_cfg["dest_path"], replace_cfg["upscale_model"], False,
                    replace_cfg["upscale_target_width"], replace_cfg["upscale_target_height"],
                )
            finally:
                try:
                    os.remove(replace_cfg["tmp_path"])
                except OSError:
                    pass

            await loop2.run_in_executor(None, generate_thumbnail, replace_cfg["dest_path"], replace_cfg["thumb_path"])

            async with AsyncSessionLocal() as session:
                updated = await session.get(Image, replace_cfg["image_id"])
                if updated:
                    updated.width = info["width"]
                    updated.height = info["height"]
                    updated.file_size_bytes = info["file_size_bytes"]
                    await session.commit()

            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "crop_upscale",
                "status": "completed", "done": 1, "total": 1, "percent": 100.0,
                "replace": True,
            })

        await job_queue.enqueue(job, _run_crop_upscale_replace)
        return {"job_id": job.id}

    # --- New-file mode (original behaviour) ---
    crop_stem = slugify_filename(src_path.stem + "_crop")
    existing = await db.execute(
        select(Image.filename).where(
            Image.dataset_id == img.dataset_id,
            Image.filename.like(f"{crop_stem}%"),
        )
    )
    db_names: set[str] = {r[0] for r in existing.all()}
    new_filename = unique_filename(dest_images, crop_stem, src_path.suffix, db_names)
    dest_path = dest_images / new_filename

    # Crop + upscale path (async background job)
    if body.upscale_model:
        tmp_path = dest_images / (dest_path.stem + "_tmp" + dest_path.suffix)
        await loop.run_in_executor(
            None, crop_image_to_dest,
            str(src_path), str(tmp_path),
            body.x, body.y, body.width, body.height,
            body.output_width, body.output_height,
        )

        job_cfg = {
            "tmp_path": str(tmp_path),
            "dest_path": str(dest_path),
            "dest_thumbs": str(dest_thumbs),
            "new_filename": new_filename,
            "dataset_id": img.dataset_id,
            "original_filename": img.original_filename,
            "subfolder": img.subfolder,
            "upscale_model": body.upscale_model,
            "upscale_target_width": body.upscale_target_width,
            "upscale_target_height": body.upscale_target_height,
        }
        job = BackgroundJob(job_type="crop_upscale", dataset_id=img.dataset_id, total_items=1, config=job_cfg)
        db.add(job)
        await db.commit()

        async def _run_crop_upscale(job_id: str) -> None:
            import os
            from backend.database import AsyncSessionLocal
            from backend.ml.upscaler import upscale_image_sync
            from backend.workers.progress import broadcaster

            cfg = job_cfg
            tmp = cfg["tmp_path"]
            dst = cfg["dest_path"]
            loop2 = asyncio.get_running_loop()
            try:
                info = await loop2.run_in_executor(
                    None, upscale_image_sync,
                    tmp, dst, cfg["upscale_model"], False,
                    cfg["upscale_target_width"], cfg["upscale_target_height"],
                )
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

            thumb_path = str(Path(cfg["dest_thumbs"]) / (Path(cfg["new_filename"]).stem + ".webp"))
            await loop2.run_in_executor(None, generate_thumbnail, dst, thumb_path)

            async with AsyncSessionLocal() as session:
                new_img = Image(
                    dataset_id=cfg["dataset_id"],
                    filename=cfg["new_filename"],
                    original_filename=cfg["original_filename"],
                    subfolder=cfg["subfolder"],
                    file_path=dst,
                    thumbnail_path=thumb_path,
                    width=info["width"],
                    height=info["height"],
                    file_size_bytes=info["file_size_bytes"],
                    format=info["format"],
                )
                session.add(new_img)
                await session.commit()
                await session.refresh(new_img)

            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "crop_upscale",
                "status": "completed", "done": 1, "total": 1, "percent": 100.0,
                "image_id": new_img.id, "filename": new_img.filename,
            })

        await job_queue.enqueue(job, _run_crop_upscale)
        return {"job_id": job.id}

    # Synchronous crop-only path
    info = await loop.run_in_executor(
        None, crop_image_to_dest,
        str(src_path), str(dest_path),
        body.x, body.y, body.width, body.height,
        body.output_width, body.output_height,
    )
    thumb_path = str(dest_thumbs / (Path(new_filename).stem + ".webp"))
    await loop.run_in_executor(None, generate_thumbnail, str(dest_path), thumb_path)

    new_img = Image(
        dataset_id=img.dataset_id,
        filename=new_filename,
        original_filename=img.original_filename,
        subfolder=img.subfolder,
        file_path=str(dest_path),
        thumbnail_path=thumb_path,
        **info,
    )
    db.add(new_img)
    await db.commit()
    await db.refresh(new_img)
    return {"id": new_img.id, "filename": new_img.filename, "width": new_img.width, "height": new_img.height}


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
                await version_service.protect_file_before_overwrite(img.id, img.file_path, session)
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


@router.patch("/{image_id}/rename")
async def rename_image(image_id: str, body: RenameImageRequest, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")

    raw = body.new_stem.strip()
    if not raw or "/" in raw or "\\" in raw or len(raw) > 200:
        raise HTTPException(400, "Invalid new_stem")
    slug = slugify_filename(raw)
    if not slug:
        raise HTTPException(400, "Stem produces empty slug")

    old_path = Path(img.file_path)
    existing = await db.execute(
        select(Image.filename).where(Image.dataset_id == img.dataset_id, Image.id != image_id)
    )
    db_names: set[str] = {r[0] for r in existing.all()}
    new_filename = unique_filename(old_path.parent, slug, old_path.suffix.lower(), db_names)
    new_path = old_path.parent / new_filename

    img.filename = new_filename
    img.file_path = str(new_path)
    img.is_auto_named = False
    rename_with_sidecar(old_path, new_path)  # FS last — if this raises, commit never runs
    await db.commit()
    return {"filename": new_filename}


@router.post("/batch/move-subfolder")
async def batch_move_subfolder(body: BatchMoveSubfolderRequest, db: AsyncSession = Depends(get_db)):
    target = normalize_subfolder(body.subfolder)
    result = await db.execute(
        select(Image.id, Image.filename, Image.file_path, Image.dataset_id)
        .where(Image.id.in_(body.image_ids))
    )
    rows = result.all()
    if not rows:
        raise HTTPException(404, "No matching images found")

    dataset_id = rows[0].dataset_id
    images_dir = Path(rows[0].file_path).parent

    if body.rename_on_move:
        existing = await db.execute(select(Image.filename).where(Image.dataset_id == dataset_id))
        db_names: set[str] = {r[0] for r in existing.all()}

        target_stem = slugify_filename(target.replace("/", "_")) if target else "image"

        # Build the full rename plan before touching anything.
        renames: list[tuple[Path, Path, Path, Path, str, str, str]] = []  # (old, new, old_thumb, new_thumb, id, new_fn, new_fp)
        for row in rows:
            old_path = Path(row.file_path)
            suf = old_path.suffix.lower()
            db_names.discard(row.filename)
            new_filename = unique_filename(images_dir, target_stem, suf, db_names)
            new_path = images_dir / new_filename
            old_thumb = Path(thumbnail_path_for(str(old_path)))
            new_thumb = Path(thumbnail_path_for(str(new_path)))
            db_names.add(new_filename)
            renames.append((old_path, new_path, old_thumb, new_thumb, row.id, new_filename, str(new_path)))

        # Apply all DB mutations in-memory (no commit yet).
        for _old, _new, _ot, new_thumb, img_id, new_fn, new_fp in renames:
            await db.execute(
                sa_update(Image).where(Image.id == img_id).values(
                    subfolder=target,
                    filename=new_fn,
                    file_path=new_fp,
                    thumbnail_path=str(new_thumb),
                    is_auto_named=True,
                )
            )

        # Perform filesystem renames — if any raise, the exception propagates and
        # db.commit() is never reached, so all DB mutations are rolled back.
        for old_path, new_path, old_thumb, new_thumb, *_ in renames:
            if new_path != old_path:
                rename_with_sidecar(old_path, new_path)
                if old_thumb.exists():
                    old_thumb.rename(new_thumb)
    else:
        # Just update the subfolder field; filenames stay unchanged.
        for row in rows:
            await db.execute(
                sa_update(Image).where(Image.id == row.id).values(subfolder=target)
            )

    await db.commit()
    return {"moved": len(rows), "subfolder": target}


@router.post("/batch/move-dataset")
async def batch_move_dataset(body: BatchMoveDatasetRequest, db: AsyncSession = Depends(get_db)):
    target = normalize_subfolder(body.subfolder)

    if body.image_ids:
        result = await db.execute(
            select(Image.id, Image.filename, Image.file_path, Image.dataset_id, Image.thumbnail_path)
            .where(Image.id.in_(body.image_ids))
        )
        rows = result.all()
        source_dataset_id = rows[0].dataset_id if rows else None
    elif body.source_dataset_id is not None and body.source_subfolder is not None:
        source_subfolder = normalize_subfolder(body.source_subfolder)
        result = await db.execute(
            select(Image.id, Image.filename, Image.file_path, Image.dataset_id, Image.thumbnail_path)
            .where(Image.dataset_id == body.source_dataset_id)
            .where(Image.subfolder == source_subfolder)
        )
        rows = result.all()
        source_dataset_id = body.source_dataset_id
    else:
        raise HTTPException(400, "Provide image_ids or source_dataset_id+source_subfolder")

    if not rows:
        raise HTTPException(404, "No matching images found")
    if source_dataset_id == body.target_dataset_id:
        raise HTTPException(400, "Source and target dataset must differ")

    target_ds_result = await db.execute(select(Dataset).where(Dataset.id == body.target_dataset_id))
    target_dataset = target_ds_result.scalar_one_or_none()
    if not target_dataset:
        raise HTTPException(404, "Target dataset not found")

    target_images_dir = Path(target_dataset.folder_path) / "images"
    target_images_dir.mkdir(parents=True, exist_ok=True)
    (Path(target_dataset.folder_path) / "thumbnails").mkdir(parents=True, exist_ok=True)

    existing = await db.execute(select(Image.filename).where(Image.dataset_id == body.target_dataset_id))
    db_names: set[str] = {r[0] for r in existing.all()}

    target_stem = slugify_filename(target.replace("/", "_")) if target else "image"

    # (old_path, new_path, old_thumb, new_thumb, img_id, new_fn)
    plan: list[tuple[Path, Path, Path, Path, str, str]] = []
    for row in rows:
        old_path = Path(row.file_path)
        suf = old_path.suffix.lower()
        new_fn = unique_filename(target_images_dir, target_stem, suf, db_names)
        new_path = target_images_dir / new_fn
        old_thumb = Path(thumbnail_path_for(str(old_path)))
        new_thumb = Path(thumbnail_path_for(str(new_path)))
        db_names.add(new_fn)
        plan.append((old_path, new_path, old_thumb, new_thumb, row.id, new_fn))

    for old_path, new_path, old_thumb, new_thumb, img_id, new_fn in plan:
        await db.execute(
            sa_update(Image).where(Image.id == img_id).values(
                dataset_id=body.target_dataset_id,
                subfolder=target,
                filename=new_fn,
                file_path=str(new_path),
                thumbnail_path=str(new_thumb),
                is_auto_named=True,
            )
        )

    for old_path, new_path, old_thumb, new_thumb, *_ in plan:
        rename_with_sidecar(old_path, new_path)
        if old_thumb.exists():
            shutil.copy2(old_thumb, new_thumb)
            old_thumb.unlink()

    await db.commit()
    await refresh_stats(db, source_dataset_id)
    await refresh_stats(db, body.target_dataset_id)
    return {"moved": len(rows), "target_dataset_id": body.target_dataset_id}


@router.post("/batch/copy-dataset", response_model=BatchCopyDatasetResult)
async def batch_copy_dataset(body: BatchMoveDatasetRequest, db: AsyncSession = Depends(get_db)):
    target = normalize_subfolder(body.subfolder)

    cols = (
        Image.id, Image.filename, Image.file_path, Image.dataset_id, Image.thumbnail_path,
        Image.original_filename, Image.width, Image.height, Image.file_size_bytes, Image.format,
        Image.phash, Image.caption_text, Image.caption_style, Image.captioned_by, Image.captioned_at,
        Image.tags_json, Image.quality_flags, Image.aesthetic_score, Image.blur_score,
        Image.noise_score, Image.uniformity_score, Image.watermark_score, Image.color_score,
        Image.saturation_score, Image.style_similarity_score, Image.dino_layer_scores,
        Image.generation_metadata,
    )

    if body.image_ids:
        result = await db.execute(select(*cols).where(Image.id.in_(body.image_ids)))
        rows = result.all()
        source_dataset_id = rows[0].dataset_id if rows else None
    elif body.source_dataset_id is not None and body.source_subfolder is not None:
        source_subfolder = normalize_subfolder(body.source_subfolder)
        result = await db.execute(
            select(*cols)
            .where(Image.dataset_id == body.source_dataset_id)
            .where(Image.subfolder == source_subfolder)
        )
        rows = result.all()
        source_dataset_id = body.source_dataset_id
    else:
        raise HTTPException(400, "Provide image_ids or source_dataset_id+source_subfolder")

    if not rows:
        raise HTTPException(404, "No matching images found")
    if source_dataset_id == body.target_dataset_id:
        raise HTTPException(400, "Source and target dataset must differ")

    target_ds_result = await db.execute(select(Dataset).where(Dataset.id == body.target_dataset_id))
    target_dataset = target_ds_result.scalar_one_or_none()
    if not target_dataset:
        raise HTTPException(404, "Target dataset not found")

    target_images_dir = Path(target_dataset.folder_path) / "images"
    target_images_dir.mkdir(parents=True, exist_ok=True)
    (Path(target_dataset.folder_path) / "thumbnails").mkdir(parents=True, exist_ok=True)

    existing = await db.execute(select(Image.filename).where(Image.dataset_id == body.target_dataset_id))
    db_names: set[str] = {r[0] for r in existing.all()}

    target_stem = slugify_filename(target.replace("/", "_")) if target else "image"

    plan: list[tuple[Path, Path, Path, Path, Any]] = []
    for row in rows:
        old_path = Path(row.file_path)
        suf = old_path.suffix.lower()
        new_fn = unique_filename(target_images_dir, target_stem, suf, db_names)
        new_path = target_images_dir / new_fn
        old_thumb = Path(thumbnail_path_for(str(old_path)))
        new_thumb = Path(thumbnail_path_for(str(new_path)))
        db_names.add(new_fn)
        plan.append((old_path, new_path, old_thumb, new_thumb, row))

    for old_path, new_path, old_thumb, new_thumb, row in plan:
        db.add(Image(
            id=str(uuid4()),
            dataset_id=body.target_dataset_id,
            subfolder=target,
            filename=new_path.name,
            original_filename=row.original_filename,
            file_path=str(new_path),
            thumbnail_path=str(new_thumb),
            is_auto_named=True,
            width=row.width,
            height=row.height,
            file_size_bytes=row.file_size_bytes,
            format=row.format,
            phash=row.phash,
            caption_text=row.caption_text,
            caption_style=row.caption_style,
            captioned_by=row.captioned_by,
            captioned_at=row.captioned_at,
            tags_json=row.tags_json,
            quality_flags=row.quality_flags,
            aesthetic_score=row.aesthetic_score,
            blur_score=row.blur_score,
            noise_score=row.noise_score,
            uniformity_score=row.uniformity_score,
            watermark_score=row.watermark_score,
            color_score=row.color_score,
            saturation_score=row.saturation_score,
            style_similarity_score=row.style_similarity_score,
            dino_layer_scores=row.dino_layer_scores,
            generation_metadata=row.generation_metadata,
        ))

    for old_path, new_path, old_thumb, new_thumb, *_ in plan:
        copy_with_sidecar(old_path, new_path)
        if old_thumb.exists():
            shutil.copy2(old_thumb, new_thumb)

    await db.commit()
    await refresh_stats(db, body.target_dataset_id)
    return {"copied": len(rows), "target_dataset_id": body.target_dataset_id}

