import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import and_, select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.licenses import copy_provenance
from backend.models import BackgroundJob, Image
from backend.ml.lut_processor import scan_lut_models, apply_lut_sync
from backend.schemas.lut import LutModelInfo, LutRunRequest
from backend.services.image_service import generate_thumbnail
from backend.services import version_service
from backend.utils import ALLOWED_FLAG_KEYS, normalize_image_format, normalize_subfolder, slugify_filename, unique_filename_with_thumb, thumbnail_path_for
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lut", tags=["lut"])


@router.get("/models", response_model=list[LutModelInfo])
async def list_lut_models():
    return scan_lut_models(settings.lut_models_dir)


@router.post("/run")
async def run_lut(body: LutRunRequest, db: AsyncSession = Depends(get_db)):
    if body.image_ids is not None:
        result = await db.execute(
            select(Image.id).where(Image.id.in_(body.image_ids))
        )
        image_ids = [r[0] for r in result.all()]
    else:
        q = select(Image.id).where(Image.dataset_id == body.dataset_id)
        if body.subfolder is not None:
            q = q.where(Image.subfolder == normalize_subfolder(body.subfolder))
        if body.quality_flags:
            valid_flags = [f for f in body.quality_flags if f in ALLOWED_FLAG_KEYS]
            if valid_flags:
                q = q.where(and_(*[Image.quality_flags[f].as_boolean().is_not(True) for f in valid_flags]))
        result = await db.execute(q)
        image_ids = [r[0] for r in result.all()]

    total = len(image_ids)
    from pathlib import Path as _Path
    auto_label = f"LUT — {_Path(body.lut_path).name}"
    job = BackgroundJob(
        job_type="batch_lut",
        label=body.label or auto_label,
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
            # undefer only where it is needed: a non-replace grade copies the
            # parent's provenance, source_meta included, and a deferred lazy load
            # would raise on this async session. The replace branch never calls
            # `copy_provenance`, so undeferring there would load a scraper's full
            # raw payload for the whole batch and discard it.
            query = select(Image).where(Image.id.in_(image_ids))
            if not cfg["replace"]:
                query = query.options(undefer(Image.source_meta))
            result = await session.execute(query)
            images = result.scalars().all()
            loop = asyncio.get_running_loop()

            lut_path = cfg["lut_path"]
            intensity = cfg["intensity"]
            replace = cfg["replace"]
            lut_filename = Path(lut_path).name

            last_image_id: str | None = None

            # Pre-build occupied thumbnail stems for the non-replace path so that
            # images with different extensions but the same derived stem don't share
            # a thumbnail. planned_thumb_stems accumulates across iterations.
            occupied_thumb_stems: set[str] = set()
            planned_thumb_stems: set[str] = set()
            if not replace and images:
                dest_thumb_dir = Path(images[0].file_path).parent.parent / "thumbnails"
                if dest_thumb_dir.exists():
                    occupied_thumb_stems = {p.stem for p in dest_thumb_dir.glob("*.webp")}

            cancelled = False
            for i, img in enumerate(images):
                if job_queue.cancel_requested(job_id):
                    cancelled = True
                    break
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_lut",
                    "status": "running", "done": i, "total": len(images),
                    "percent": round(i / len(images) * 100, 1),
                    "current_item": img.filename,
                    "message": f"Applying LUT to {img.filename}…",
                })

                src_path = Path(img.file_path)
                dest_path_str: str

                # Where `apply_lut_sync` will actually write. For .gif/.bmp/
                # .tiff/.avif that is a *different* path — PNG is the fallback
                # format. Computed once for both modes: replace needs the whole
                # path to check for a squatter, copy needs the suffix.
                _fmt, planned_out = normalize_image_format(src_path.suffix, str(src_path))
                out_suffix = Path(planned_out).suffix

                if replace:
                    dest_path_str = str(src_path)
                else:
                    dest_images = src_path.parent
                    dest_stem = slugify_filename(src_path.stem + "_lut")
                    existing = await session.execute(
                        select(Image.filename).where(
                            Image.dataset_id == img.dataset_id,
                            Image.filename.like(f"{dest_stem}%"),
                        )
                    )
                    db_names: set[str] = {r[0] for r in existing.all()}
                    # `out_suffix`, not the source's: reserving the name under the
                    # extension that will actually be written is what makes both
                    # the db_names and the on-disk check apply to the real path.
                    # `unique_filename` only stats the suffix it is handed, so a
                    # copy-mode grade of shot.bmp would otherwise overwrite an
                    # existing shot_lut.png, registered or not.
                    new_filename = unique_filename_with_thumb(
                        dest_images, dest_stem, out_suffix, db_names,
                        occupied_thumb_stems, planned_thumb_stems,
                    )
                    dest_path_str = str(dest_images / new_filename)

                if replace:
                    # The collision has to be caught before the write, not after:
                    # an unregistered file hand-dropped into images/ has no DB row
                    # guarding it, and by the time the save has run it is already
                    # gone.
                    if planned_out != str(src_path) and Path(planned_out).exists():
                        logger.warning(
                            "LUT: %s would be written as %s, which already exists on disk — skipped",
                            img.filename, Path(planned_out).name,
                        )
                        await broadcaster.emit(job_id, {
                            "type": "progress", "job_id": job_id, "job_type": "batch_lut",
                            "status": "running", "done": i + 1, "total": len(images),
                            "percent": round((i + 1) / len(images) * 100, 1),
                            "current_item": img.filename,
                            "message": f"Skipped: {Path(planned_out).name} already exists on disk",
                        })
                        continue
                    await version_service.protect_file_before_overwrite(img.id, img.file_path, session)
                    await session.commit()  # persist the COW hash backfill before the overwrite

                try:
                    info = await loop.run_in_executor(
                        None, apply_lut_sync,
                        str(src_path), dest_path_str, lut_path, intensity, replace,
                    )
                except Exception as exc:
                    logger.error("LUT failed for %s: %s", img.filename, exc)
                    await broadcaster.emit(job_id, {
                        "type": "progress", "job_id": job_id, "job_type": "batch_lut",
                        "status": "running", "done": i + 1, "total": len(images),
                        "percent": round((i + 1) / len(images) * 100, 1),
                        "current_item": img.filename,
                        "message": f"Failed: {exc}",
                    })
                    continue

                # apply_lut_sync may change out_path if format conversion occurred
                actual_out_path = info.get("out_path", dest_path_str)

                if replace:
                    now = datetime.now(timezone.utc)
                    img.file_size_bytes = info["file_size_bytes"]
                    img.updated_at = now
                    if Path(actual_out_path) != src_path:
                        # `normalize_image_format` falls back to PNG for .gif,
                        # .bmp, .tiff and .avif — all in IMAGE_EXTENSIONS — so a
                        # replace-mode grade of one of those writes a *different*
                        # file. Without this the row keeps pointing at the stale
                        # original, which is also left on disk.
                        #
                        # A pure extension change moves nothing derived: the stem
                        # is unchanged, so the thumbnail ({stem}.webp) and the
                        # caption sidecar ({stem}.txt) stay exactly where they
                        # are, and no other row can hold the name because every
                        # image-name-picking site rejects an occupied *stem* in
                        # any extension.
                        src_path.unlink(missing_ok=True)
                        img.filename = Path(actual_out_path).name
                        img.file_path = actual_out_path
                        img.format = info["format"]
                    img.processing_history = (img.processing_history or []) + [{
                        "op": "lut",
                        "lut": lut_filename,
                        "intensity": intensity,
                        "at": now.isoformat(),
                    }]
                    if img.thumbnail_path:
                        await loop.run_in_executor(
                            None, generate_thumbnail, actual_out_path, img.thumbnail_path
                        )
                    last_image_id = img.id
                else:
                    actual_dest = Path(actual_out_path)
                    thumb_path = thumbnail_path_for(actual_out_path)
                    await loop.run_in_executor(
                        None, generate_thumbnail, actual_out_path, thumb_path
                    )
                    new_img = Image(
                        dataset_id=img.dataset_id,
                        filename=actual_dest.name,
                        original_filename=img.original_filename,
                        subfolder=img.subfolder,
                        file_path=actual_out_path,
                        thumbnail_path=thumb_path,
                        width=info["width"],
                        height=info["height"],
                        file_size_bytes=info["file_size_bytes"],
                        format=info["format"],
                        # A graded derivative keeps its parent's source/license.
                        **copy_provenance(img),
                    )
                    session.add(new_img)
                    await session.flush()
                    last_image_id = new_img.id

                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_lut",
                    "status": "running", "done": i + 1, "total": len(images),
                    "percent": round((i + 1) / len(images) * 100, 1),
                    "current_item": img.filename,
                    "image_id": last_image_id,
                })

            await session.commit()
            if cancelled:
                job_queue.raise_if_cancelled(job_id)

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": total}
