import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import BackgroundJob, Image
from backend.ml.upscaler import scan_upscale_models, upscale_image_sync
from backend.schemas.upscale import UpscaleModelInfo, UpscaleRunRequest
from backend.services.image_service import generate_thumbnail
from backend.services import version_service
from backend.utils import slugify_filename, unique_filename, thumbnail_path_for
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upscaling", tags=["upscaling"])


@router.get("/models", response_model=list[UpscaleModelInfo])
async def list_upscale_models():
    return scan_upscale_models(settings.upscale_models_dir)


@router.post("/run")
async def run_upscale(body: UpscaleRunRequest, db: AsyncSession = Depends(get_db)):
    # Resolve image list
    if body.image_ids is not None:
        result = await db.execute(
            select(Image.id).where(Image.id.in_(body.image_ids))
        )
        image_ids = [r[0] for r in result.all()]
    else:
        result = await db.execute(
            select(Image.id).where(Image.dataset_id == body.dataset_id)
        )
        image_ids = [r[0] for r in result.all()]

    total = len(image_ids)
    job = BackgroundJob(
        job_type="batch_upscale",
        dataset_id=body.dataset_id,
        total_items=total,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    cfg = body.model_dump()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.workers.progress import broadcaster

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Image).where(Image.id.in_(image_ids))
            )
            images = result.scalars().all()
            loop = asyncio.get_running_loop()

            model_path = cfg["model_path"]
            replace = cfg["replace"]
            target_w = cfg["target_width"]
            target_h = cfg["target_height"]

            # Detect model scale for naming (heuristic from filename)
            from backend.ml.upscaler import _detect_scale
            raw_scale = _detect_scale(Path(model_path).stem)
            scale_suffix = f"_up{raw_scale}x" if raw_scale else "_upscale"

            last_image_id: str | None = None

            for i, img in enumerate(images):
                src_path = Path(img.file_path)
                dest_path_str: str

                if replace:
                    dest_path_str = str(src_path)
                else:
                    dest_images = src_path.parent
                    dest_stem = slugify_filename(src_path.stem + scale_suffix)
                    existing = await session.execute(
                        select(Image.filename).where(
                            Image.dataset_id == img.dataset_id,
                            Image.filename.like(f"{dest_stem}%"),
                        )
                    )
                    db_names: set[str] = {r[0] for r in existing.all()}
                    new_filename = unique_filename(dest_images, dest_stem, src_path.suffix, db_names)
                    dest_path_str = str(dest_images / new_filename)

                if replace:
                    await version_service.protect_file_before_overwrite(img.id, img.file_path, session)

                try:
                    info = await loop.run_in_executor(
                        None, upscale_image_sync,
                        str(src_path), dest_path_str, model_path, replace, target_w, target_h,
                    )
                except Exception as exc:
                    logger.error("Upscale failed for %s: %s", img.filename, exc)
                    await broadcaster.emit(job_id, {
                        "type": "progress", "job_id": job_id, "job_type": "batch_upscale",
                        "status": "running", "done": i + 1, "total": len(images),
                        "percent": round((i + 1) / len(images) * 100, 1),
                        "current_item": img.filename,
                        "message": f"Failed: {exc}",
                    })
                    continue

                if replace:
                    img.width = info["width"]
                    img.height = info["height"]
                    img.file_size_bytes = info["file_size_bytes"]
                    img.updated_at = datetime.now(timezone.utc)
                    if img.thumbnail_path:
                        await loop.run_in_executor(
                            None, generate_thumbnail, str(src_path), img.thumbnail_path
                        )
                    last_image_id = img.id
                else:
                    dest_path = Path(dest_path_str)
                    thumb_path = thumbnail_path_for(dest_path_str)
                    await loop.run_in_executor(
                        None, generate_thumbnail, dest_path_str, thumb_path
                    )
                    new_img = Image(
                        dataset_id=img.dataset_id,
                        filename=dest_path.name,
                        original_filename=img.original_filename,
                        subfolder=img.subfolder,
                        file_path=dest_path_str,
                        thumbnail_path=thumb_path,
                        width=info["width"],
                        height=info["height"],
                        file_size_bytes=info["file_size_bytes"],
                        format=info["format"],
                    )
                    session.add(new_img)
                    await session.flush()
                    last_image_id = new_img.id

                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_upscale",
                    "status": "running", "done": i + 1, "total": len(images),
                    "percent": round((i + 1) / len(images) * 100, 1),
                    "current_item": img.filename,
                    "image_id": last_image_id,
                })

            await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": total}
