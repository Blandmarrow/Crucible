import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import and_, select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.licenses import copy_provenance
from backend.models import BackgroundJob, Image
from backend.ml.upscaler import scan_upscale_models, upscale_image_sync
from backend.schemas.upscale import UpscaleModelInfo, UpscaleRunRequest
from backend.services.image_service import generate_thumbnail
from backend.services import version_service
from backend.utils import ALLOWED_FLAG_KEYS, normalize_image_format, normalize_subfolder, record_in_place, slugify_filename, unique_filename_with_thumb, thumbnail_path_for
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
    auto_label = f"Upscale — {_Path(body.model_path).stem}"
    job = BackgroundJob(
        job_type="batch_upscale",
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
            # undefer only where it is needed: a non-replace upscale copies the
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

            # The whole outcome of the run, written to `result_data` at the end —
            # the LUT twin, seeded and incremented identically. `skipped` starts
            # at the rows that no longer exist (deleted between enqueue and run);
            # the loop's two `continue` branches are invisible without it.
            #
            # `thumbnails_stale` counts upscaled images whose *preview* could not
            # be recut. The image itself is correct and committed, but the
            # gallery keeps rendering the old tile, and the realistic trigger (a
            # full volume, a read-only thumbnails/) hits every image in the run.
            counts = {
                "processed": 0,
                "skipped": len(image_ids) - len(images),
                "failed": 0,
                "thumbnails_stale": 0,
            }

            model_path = cfg["model_path"]
            replace = cfg["replace"]
            target_w = cfg["target_width"]
            target_h = cfg["target_height"]
            model_filename = Path(model_path).name

            # Detect model scale for naming (heuristic from filename)
            from backend.ml.upscaler import _detect_scale
            raw_scale = _detect_scale(Path(model_path).stem)
            # Scale 1 is a restoration model (denoise / deblur / JPEG artifacts),
            # not an upscaler, so the suffix says so rather than claiming "_up1x".
            scale_suffix = (
                "_1x" if raw_scale == 1
                else f"_up{raw_scale}x" if raw_scale
                else "_upscale"
            )

            last_image_id: str | None = None

            # Occupied/planned thumbnail stems for the non-replace path, keyed by
            # thumbnail directory: matched images can span multiple datasets (each
            # with its own thumbnails/ dir), so a single flat set would false-share
            # stems across datasets. Built lazily per dir inside the loop;
            # planned_by_dir accumulates across iterations (mutated by
            # unique_filename_with_thumb per its contract).
            occupied_by_dir: dict[Path, set[str]] = {}
            planned_by_dir: dict[Path, set[str]] = {}

            cancelled = False
            for i, img in enumerate(images):
                if job_queue.cancel_requested(job_id):
                    cancelled = True
                    break
                # Emit BEFORE the upscale runs: on CPU a single image can take
                # minutes, and without this a small job sits at "Starting…" the
                # whole time, looking hung.
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_upscale",
                    "status": "running", "done": i, "total": len(images),
                    "percent": round(i / len(images) * 100, 1),
                    "current_item": img.filename,
                    "message": f"Upscaling {img.filename}…",
                })
                src_path = Path(img.file_path)
                dest_path_str: str

                # Where `upscale_image_sync` will actually write: for .gif/.bmp/
                # .tiff/.avif that is a *different* suffix, PNG being the
                # fallback format. One call answers both modes — the replace
                # branch needs the full path to check for a squatter, the copy
                # branch needs the suffix so it reserves the name it will use.
                _fmt, planned_out = normalize_image_format(src_path.suffix, str(src_path))
                out_suffix = Path(planned_out).suffix

                if replace:
                    dest_path_str = str(src_path)
                else:
                    dest_images = src_path.parent
                    dest_thumb_dir = src_path.parent.parent / "thumbnails"
                    if dest_thumb_dir not in occupied_by_dir:
                        occupied_by_dir[dest_thumb_dir] = (
                            {p.stem for p in dest_thumb_dir.glob("*.webp")}
                            if dest_thumb_dir.exists() else set()
                        )
                    occupied_thumb_stems = occupied_by_dir[dest_thumb_dir]
                    planned_thumb_stems = planned_by_dir.setdefault(dest_thumb_dir, set())
                    dest_stem = slugify_filename(src_path.stem + scale_suffix)
                    existing = await session.execute(
                        select(Image.filename).where(
                            Image.dataset_id == img.dataset_id,
                            Image.filename.like(f"{dest_stem}%"),
                        )
                    )
                    db_names: set[str] = {r[0] for r in existing.all()}
                    new_filename = unique_filename_with_thumb(
                        dest_images, dest_stem, out_suffix, db_names,
                        occupied_thumb_stems, planned_thumb_stems,
                    )
                    dest_path_str = str(dest_images / new_filename)

                if replace:
                    # The collision has to be caught before the write, not after:
                    # an unregistered file hand-dropped into images/ has no DB row
                    # guarding it, and by the time the save has run it is gone.
                    if planned_out != str(src_path) and Path(planned_out).exists():
                        logger.warning(
                            "Upscale: %s would be written as %s, which already exists on disk — skipped",
                            img.filename, Path(planned_out).name,
                        )
                        await broadcaster.emit(job_id, {
                            "type": "progress", "job_id": job_id, "job_type": "batch_upscale",
                            "status": "running", "done": i + 1, "total": len(images),
                            "percent": round((i + 1) / len(images) * 100, 1),
                            "current_item": img.filename,
                            "message": f"Skipped: {Path(planned_out).name} already exists on disk",
                        })
                        counts["skipped"] += 1
                        continue
                    await version_service.protect_file_before_overwrite(img.id, img.file_path, session)
                    await session.commit()  # persist the COW hash backfill before the overwrite

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
                    counts["failed"] += 1
                    continue

                # upscale_image_sync may change out_path if format conversion occurred
                actual_out_path = info.get("out_path", dest_path_str)

                if replace:
                    superseded: Path | None = None
                    img.width = info["width"]
                    img.height = info["height"]
                    img.file_size_bytes = info["file_size_bytes"]
                    # `updated_at` is stamped by `record_in_place` below.
                    if Path(actual_out_path) != src_path:
                        # The PNG fallback wrote a *different* file. Without this
                        # the row keeps pointing at the stale original, which is
                        # also left on disk — PM-009, at its second call site.
                        #
                        # A pure extension change moves nothing derived: the stem
                        # is unchanged, so the thumbnail ({stem}.webp) and the
                        # caption sidecar ({stem}.txt) stay where they are.
                        #
                        # The original is unlinked in the epilogue below, after
                        # the commit: `unlink` is fallible, and a failure here
                        # would leave the write done and the row uncommitted.
                        superseded = src_path
                        img.filename = Path(actual_out_path).name
                        img.file_path = actual_out_path
                        img.format = info["format"]
                    # Writes `processing_history` *and* `scores_stale` — an upscale
                    # changes the resolution `blur_score` is measured against. Pure
                    # dict building, so it cannot raise between the overwrite above
                    # and the commit below (PM-013).
                    record_in_place(img, "upscale", model=model_filename)
                    # Nothing fallible between the (already-done) overwrite and
                    # this commit — a raise before here would roll the row back
                    # onto a file that no longer exists (PM-013).
                    await session.commit()

                    # --- epilogue: best-effort, cannot undo the upscale ---
                    # `expire_on_commit=False` (backend/database.py) keeps `img`
                    # readable after the commit without a refresh.
                    if superseded is not None:
                        try:
                            superseded.unlink(missing_ok=True)
                        except OSError as exc:
                            logger.warning(
                                "Upscale: could not remove superseded %s: %s",
                                superseded.name, exc,
                            )
                    if img.thumbnail_path:
                        try:
                            await loop.run_in_executor(
                                None, generate_thumbnail, actual_out_path, img.thumbnail_path
                            )
                        except Exception as exc:
                            # A stale thumbnail is cosmetic; the upscaled image is
                            # committed and serves. Counted rather than merely
                            # logged so the run can say so: TopBar reads this
                            # count and points at Bulk Edit → Thumbnails.
                            counts["thumbnails_stale"] += 1
                            logger.warning(
                                "Upscale: thumbnail for %s could not be regenerated: %s",
                                img.filename, exc,
                            )
                    last_image_id = img.id
                else:
                    dest_path = Path(actual_out_path)
                    thumb_path = thumbnail_path_for(actual_out_path)
                    await loop.run_in_executor(
                        None, generate_thumbnail, actual_out_path, thumb_path
                    )
                    new_img = Image(
                        dataset_id=img.dataset_id,
                        filename=dest_path.name,
                        original_filename=img.original_filename,
                        subfolder=img.subfolder,
                        file_path=actual_out_path,
                        thumbnail_path=thumb_path,
                        width=info["width"],
                        height=info["height"],
                        file_size_bytes=info["file_size_bytes"],
                        format=info["format"],
                        # An upscaled derivative keeps its parent's source/license.
                        **copy_provenance(img),
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
                counts["processed"] += 1

            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = counts
            # Above `raise_if_cancelled`, so a cancelled run keeps the counts for
            # everything it did manage — including any stale thumbnail.
            await session.commit()
            if cancelled:
                job_queue.raise_if_cancelled(job_id)

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": total}
