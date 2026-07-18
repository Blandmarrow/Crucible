import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ml.mask_utils import detection_crop_rect
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob, Image
from backend.models.detection import Detection
from backend.schemas.detection import DetectionCropRequest, DetectionJobRequest, DetectionOut
from backend.services import version_service
from backend.services.image_service import crop_image_to_dest, generate_thumbnail
from backend.utils import (
    ALLOWED_FLAG_KEYS,
    normalize_subfolder,
    slugify_filename,
    thumbnail_path_for,
    unique_filename_with_thumb,
)
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/detection", tags=["detection"])
logger = logging.getLogger(__name__)

_ALLOWED_MODELS = frozenset({"florence2_large", "florence2_promptgen", "nudenet", "sam2", "sam3"})
_ALLOWED_TASKS = frozenset({"<OD>", "<CAPTION_TO_PHRASE_GROUNDING>", "nudenet", "text_prompt", "points"})


@router.post("/run")
async def run_detection(body: DetectionJobRequest, db: AsyncSession = Depends(get_db)):
    if body.model not in _ALLOWED_MODELS:
        raise HTTPException(400, f"model must be one of: {sorted(_ALLOWED_MODELS)}")
    if body.task not in _ALLOWED_TASKS:
        raise HTTPException(400, f"task must be one of: {sorted(_ALLOWED_TASKS)}")
    if body.model == "nudenet" and body.task != "nudenet":
        raise HTTPException(400, "NudeNet model requires task='nudenet'")
    if body.model == "sam2" and body.task not in {"text_prompt", "points"}:
        raise HTTPException(400, "SAM2 model requires task one of: text_prompt, points")
    if body.model == "sam3" and body.task != "text_prompt":
        raise HTTPException(400, "SAM3 model requires task='text_prompt'")
    if (
        body.task == "<CAPTION_TO_PHRASE_GROUNDING>"
        and not body.use_caption_as_prompt
        and not body.custom_prompt.strip()
    ):
        raise HTTPException(400, "custom_prompt is required for CAPTION_TO_PHRASE_GROUNDING (or enable use_caption_as_prompt)")
    if body.task == "text_prompt" and not body.custom_prompt.strip():
        raise HTTPException(400, "custom_prompt is required for the text_prompt task")

    query = select(Image.id, Image.file_path, Image.caption_text).where(Image.dataset_id == body.dataset_id)
    if body.image_ids:
        query = query.where(Image.id.in_(body.image_ids))
    if body.use_caption_as_prompt:
        query = query.where(Image.caption_text.isnot(None), Image.caption_text != "")
    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return {"job_id": None, "message": "No images found"}

    if body.model == "nudenet":
        auto_label = f"NudeNet — {len(rows)} image{'s' if len(rows) != 1 else ''}"
    elif body.model == "sam2":
        auto_label = f"SAM2 ({body.task}) — {len(rows)} image{'s' if len(rows) != 1 else ''}"
    elif body.model == "sam3":
        auto_label = f"SAM3 (text_prompt) — {len(rows)} image{'s' if len(rows) != 1 else ''}"
    else:
        auto_label = f"Detect ({body.task}) — {body.model}"
    job = BackgroundJob(
        job_type="detection",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=len(rows),
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    image_data = [(r.id, r.file_path, r.caption_text or "") for r in rows]

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.workers.progress import broadcaster

        total = len(image_data)
        start_time = time.monotonic()
        loop = asyncio.get_event_loop()

        # --- NudeNet branch (CPU-only, ONNX) ---
        if body.model == "nudenet":
            import functools
            from backend.ml.nudenet_scorer import detect_sync

            for i, (img_id, file_path, _caption_text) in enumerate(image_data):
                job_queue.raise_if_cancelled(job_id)
                async with AsyncSessionLocal() as session:
                    detections: list[dict] = []
                    try:
                        fn = functools.partial(detect_sync, file_path, body.min_prob)
                        detections = await loop.run_in_executor(None, fn)
                    except Exception:
                        logger.error("NudeNet detection failed for %s", file_path, exc_info=True)

                    if body.overwrite:
                        await session.execute(
                            delete(Detection).where(Detection.image_id == img_id)
                        )
                    now = datetime.utcnow()
                    session.add_all([
                        Detection(
                            image_id=img_id,
                            label=det["label"],
                            bbox=det["bbox"],
                            score=det.get("score"),
                            model="nudenet",
                            task="nudenet",
                            detected_at=now,
                        )
                        for det in detections
                    ])
                    await session.commit()

                elapsed = time.monotonic() - start_time
                throughput = round((i + 1) / elapsed, 2) if elapsed > 0 else 0
                await broadcaster.emit(job_id, {
                    "type": "progress",
                    "job_id": job_id,
                    "job_type": "detection",
                    "status": "running",
                    "done": i + 1,
                    "total": total,
                    "percent": round((i + 1) / total * 100, 1),
                    "current_item": file_path.replace("\\", "/").split("/")[-1],
                    "message": f"Detected {len(detections)} region(s) — {i + 1}/{total}",
                    "throughput_ips": throughput,
                    "vram_used_mb": 0,
                })
            return

        # --- SAM2 / Grounded SAM2 branch ---
        if body.model == "sam2":
            import functools
            from backend.ml.sam2_predictor import predict_sync

            from backend.services.threshold_service import get_thresholds
            async with AsyncSessionLocal() as _ts:
                _thresholds = await get_thresholds(_ts)
            gdino_threshold = _thresholds.gdino_threshold

            sam2_entry = await model_manager.load_sam2(job_id=job_id, loop=loop, dataset_id=body.dataset_id)

            for i, (img_id, file_path, _caption_text) in enumerate(image_data):
                job_queue.raise_if_cancelled(job_id)
                async with AsyncSessionLocal() as session:
                    detections = []
                    try:
                        fn = functools.partial(
                            predict_sync,
                            file_path,
                            sam2_entry.model,
                            body.task,
                            body.custom_prompt,
                            body.point_prompts,
                            body.point_labels,
                            gdino_threshold,
                        )
                        detections = await loop.run_in_executor(None, fn)
                    except Exception:
                        logger.error("SAM2 prediction failed for %s", file_path, exc_info=True)

                    if body.overwrite:
                        await session.execute(
                            delete(Detection).where(Detection.image_id == img_id)
                        )
                    now = datetime.utcnow()
                    session.add_all([
                        Detection(
                            image_id=img_id,
                            label=det["label"],
                            bbox=det["bbox"],
                            score=det.get("score"),
                            mask=det.get("mask"),
                            model="sam2",
                            task=body.task,
                            detected_at=now,
                        )
                        for det in detections
                    ])
                    await session.commit()

                elapsed = time.monotonic() - start_time
                throughput = round((i + 1) / elapsed, 2) if elapsed > 0 else 0
                try:
                    from backend.ml.device import memory_reserved_mb as _vram_mb
                    vram_mb = _vram_mb() if i % 10 == 0 else 0
                except Exception:
                    vram_mb = 0
                await broadcaster.emit(job_id, {
                    "type": "progress",
                    "job_id": job_id,
                    "job_type": "detection",
                    "status": "running",
                    "done": i + 1,
                    "total": total,
                    "percent": round((i + 1) / total * 100, 1),
                    "current_item": file_path.replace("\\", "/").split("/")[-1],
                    "message": f"SAM2 segmented {len(detections)} mask(s) — {i + 1}/{total}",
                    "throughput_ips": throughput,
                    "vram_used_mb": vram_mb,
                })
            return

        # --- SAM3 branch (native text-prompt segmentation) ---
        if body.model == "sam3":
            import functools
            from backend.ml.sam3_predictor import predict_sync as sam3_predict_sync

            from backend.services.threshold_service import get_thresholds
            async with AsyncSessionLocal() as _ts:
                _thresholds = await get_thresholds(_ts)
            sam3_threshold = _thresholds.sam3_threshold

            sam3_entry = await model_manager.load_sam3(job_id=job_id, loop=loop, dataset_id=body.dataset_id)

            for i, (img_id, file_path, _caption_text) in enumerate(image_data):
                job_queue.raise_if_cancelled(job_id)
                async with AsyncSessionLocal() as session:
                    detections = []
                    try:
                        fn = functools.partial(
                            sam3_predict_sync,
                            file_path,
                            sam3_entry.model,
                            body.custom_prompt,
                            sam3_threshold,
                        )
                        detections = await loop.run_in_executor(None, fn)
                    except Exception:
                        logger.error("SAM3 prediction failed for %s", file_path, exc_info=True)

                    if body.overwrite:
                        await session.execute(
                            delete(Detection).where(Detection.image_id == img_id)
                        )
                    now = datetime.utcnow()
                    session.add_all([
                        Detection(
                            image_id=img_id,
                            label=det["label"],
                            bbox=det["bbox"],
                            score=det.get("score"),
                            mask=det.get("mask"),
                            model="sam3",
                            task="text_prompt",
                            detected_at=now,
                        )
                        for det in detections
                    ])
                    await session.commit()

                elapsed = time.monotonic() - start_time
                throughput = round((i + 1) / elapsed, 2) if elapsed > 0 else 0
                try:
                    from backend.ml.device import memory_reserved_mb as _vram_mb
                    vram_mb = _vram_mb() if i % 10 == 0 else 0
                except Exception:
                    vram_mb = 0
                await broadcaster.emit(job_id, {
                    "type": "progress",
                    "job_id": job_id,
                    "job_type": "detection",
                    "status": "running",
                    "done": i + 1,
                    "total": total,
                    "percent": round((i + 1) / total * 100, 1),
                    "current_item": file_path.replace("\\", "/").split("/")[-1],
                    "message": f"SAM3 segmented {len(detections)} mask(s) — {i + 1}/{total}",
                    "throughput_ips": throughput,
                    "vram_used_mb": vram_mb,
                })
            return

        # --- Florence-2 branch ---
        from backend.ml.florence_captioner import detect_image

        variant = "promptgen" if "promptgen" in body.model else "large"
        model_entry = await model_manager.load_florence2(variant)

        for i, (img_id, file_path, caption_text) in enumerate(image_data):
            job_queue.raise_if_cancelled(job_id)
            async with AsyncSessionLocal() as session:
                prompt = caption_text if body.use_caption_as_prompt else body.custom_prompt
                detections = []
                try:
                    detections = await detect_image(
                        file_path, model_entry, body.task, prompt
                    )
                except Exception:
                    logger.error("Detection failed for %s", file_path, exc_info=True)

                if body.overwrite:
                    await session.execute(
                        delete(Detection).where(Detection.image_id == img_id)
                    )
                now = datetime.utcnow()
                session.add_all([
                    Detection(
                        image_id=img_id,
                        label=det["label"],
                        bbox=det["bbox"],
                        score=det.get("score"),
                        model=body.model,
                        task=body.task,
                        detected_at=now,
                    )
                    for det in detections
                ])
                await session.commit()

            elapsed = time.monotonic() - start_time
            throughput = round((i + 1) / elapsed, 2) if elapsed > 0 else 0
            try:
                from backend.ml.device import memory_reserved_mb as _vram_mb
                vram_mb = _vram_mb() if i % 10 == 0 else 0
            except Exception:
                vram_mb = 0

            await broadcaster.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "job_type": "detection",
                "status": "running",
                "done": i + 1,
                "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "current_item": file_path.replace("\\", "/").split("/")[-1],
                "message": f"Detected {len(detections)} object(s) — {i + 1}/{total}",
                "throughput_ips": throughput,
                "vram_used_mb": vram_mb,
            })

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": len(image_data)}


@router.get("/image/{image_id}", response_model=list[DetectionOut])
async def get_image_detections(image_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Detection).where(Detection.image_id == image_id).order_by(Detection.id)
    )
    return result.scalars().all()


@router.get("/labels/{dataset_id}")
async def get_dataset_labels(dataset_id: str, db: AsyncSession = Depends(get_db)):
    """Distinct detection labels in a dataset with the number of images each covers."""
    image_count = func.count(func.distinct(Detection.image_id))
    result = await db.execute(
        select(Detection.label, image_count.label("image_count"))
        .join(Image, Detection.image_id == Image.id)
        .where(Image.dataset_id == dataset_id)
        .group_by(Detection.label)
        .order_by(image_count.desc(), Detection.label.asc())
    )
    return [{"label": r.label, "image_count": r.image_count} for r in result.all()]


async def _fetch_bboxes_by_image(
    db: AsyncSession, image_ids: list[str], labels: list[str] | None
) -> dict[str, list[list[float]]]:
    """Batch-fetch detection bboxes keyed by image id, chunked to keep IN() bounded."""
    by_image: dict[str, list[list[float]]] = {}
    for start in range(0, len(image_ids), 10_000):
        chunk = image_ids[start:start + 10_000]
        query = select(Detection.image_id, Detection.bbox).where(Detection.image_id.in_(chunk))
        if labels:
            query = query.where(Detection.label.in_(labels))
        result = await db.execute(query)
        for row in result.all():
            by_image.setdefault(row.image_id, []).append(row.bbox)
    return by_image


@router.post("/crop")
async def crop_to_detection(body: DetectionCropRequest, db: AsyncSession = Depends(get_db)):
    # Resolve image list (same triple as batch upscale: ids > dataset+subfolder+flags)
    if body.image_ids is not None:
        result = await db.execute(select(Image.id).where(Image.id.in_(body.image_ids)))
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

    by_image = await _fetch_bboxes_by_image(db, image_ids, body.labels)
    matched_ids = [i for i in image_ids if i in by_image]
    skipped = len(image_ids) - len(matched_ids)
    total = len(matched_ids)

    auto_label = f"Crop to detection — {total} image{'s' if total != 1 else ''}"
    job = BackgroundJob(
        job_type="crop_to_detection",
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
            result = await session.execute(select(Image).where(Image.id.in_(matched_ids)))
            images = result.scalars().all()
            bboxes_by_image = await _fetch_bboxes_by_image(session, matched_ids, cfg["labels"])
            loop = asyncio.get_running_loop()

            replace = cfg["replace"]
            counts = {"cropped": 0, "skipped_no_detection": 0, "skipped_noop": 0, "failed": 0}

            # Pre-build occupied thumbnail stems for the non-replace path so that
            # images with different extensions but the same derived stem don't
            # share a thumbnail. planned_thumb_stems accumulates across iterations.
            occupied_thumb_stems: set[str] = set()
            planned_thumb_stems: set[str] = set()
            if not replace and images:
                dest_thumb_dir = Path(images[0].file_path).parent.parent / "thumbnails"
                if dest_thumb_dir.exists():
                    occupied_thumb_stems = {p.stem for p in dest_thumb_dir.glob("*.webp")}

            last_image_id: str | None = None
            cancelled = False
            for i, img in enumerate(images):
                if job_queue.cancel_requested(job_id):
                    cancelled = True
                    break
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "crop_to_detection",
                    "status": "running", "done": i, "total": len(images),
                    "percent": round(i / len(images) * 100, 1),
                    "current_item": img.filename,
                    "message": f"Cropping {img.filename}…",
                })

                rect = detection_crop_rect(
                    bboxes_by_image.get(img.id, []), img.width, img.height,
                    mode=cfg["mode"], padding_pct=cfg["padding_pct"], target_ar=cfg["target_ar"],
                )
                if rect is None:
                    counts["skipped_no_detection"] += 1
                    continue
                if rect == (0, 0, img.width, img.height):
                    # Full-image rect: writing would only re-encode the file
                    counts["skipped_noop"] += 1
                    continue

                src_path = Path(img.file_path)

                if replace:
                    await version_service.protect_file_before_overwrite(img.id, img.file_path, session)
                    tmp_path = src_path.with_name(src_path.stem + "_croptmp" + src_path.suffix)
                    try:
                        info = await loop.run_in_executor(
                            None, crop_image_to_dest, str(src_path), str(tmp_path), *rect,
                        )
                        tmp_path.replace(src_path)
                    except Exception as exc:
                        logger.error("Detection crop failed for %s: %s", img.filename, exc)
                        tmp_path.unlink(missing_ok=True)
                        counts["failed"] += 1
                        await broadcaster.emit(job_id, {
                            "type": "progress", "job_id": job_id, "job_type": "crop_to_detection",
                            "status": "running", "done": i + 1, "total": len(images),
                            "percent": round((i + 1) / len(images) * 100, 1),
                            "current_item": img.filename,
                            "message": f"Failed: {exc}",
                        })
                        continue
                    if img.thumbnail_path:
                        await loop.run_in_executor(None, generate_thumbnail, str(src_path), img.thumbnail_path)
                    now = datetime.now(timezone.utc)
                    img.width = info["width"]
                    img.height = info["height"]
                    img.file_size_bytes = info["file_size_bytes"]
                    img.format = info["format"]
                    img.phash = info["phash"]
                    img.updated_at = now
                    img.processing_history = (img.processing_history or []) + [{
                        "op": "crop_to_detection",
                        "mode": cfg["mode"],
                        "labels": cfg["labels"],
                        "padding_pct": cfg["padding_pct"],
                        "target_ar": cfg["target_ar"],
                        "at": now.isoformat(),
                    }]
                    last_image_id = img.id
                else:
                    dest_images = src_path.parent
                    dest_stem = slugify_filename(src_path.stem + "_crop")
                    existing = await session.execute(
                        select(Image.filename).where(
                            Image.dataset_id == img.dataset_id,
                            Image.filename.like(f"{dest_stem}%"),
                        )
                    )
                    db_names: set[str] = {r[0] for r in existing.all()}
                    new_filename = unique_filename_with_thumb(
                        dest_images, dest_stem, src_path.suffix, db_names,
                        occupied_thumb_stems, planned_thumb_stems,
                    )
                    dest_path_str = str(dest_images / new_filename)
                    try:
                        info = await loop.run_in_executor(
                            None, crop_image_to_dest, str(src_path), dest_path_str, *rect,
                        )
                    except Exception as exc:
                        logger.error("Detection crop failed for %s: %s", img.filename, exc)
                        counts["failed"] += 1
                        await broadcaster.emit(job_id, {
                            "type": "progress", "job_id": job_id, "job_type": "crop_to_detection",
                            "status": "running", "done": i + 1, "total": len(images),
                            "percent": round((i + 1) / len(images) * 100, 1),
                            "current_item": img.filename,
                            "message": f"Failed: {exc}",
                        })
                        continue
                    thumb_path = thumbnail_path_for(dest_path_str)
                    await loop.run_in_executor(None, generate_thumbnail, dest_path_str, thumb_path)
                    new_img = Image(
                        dataset_id=img.dataset_id,
                        filename=new_filename,
                        original_filename=img.original_filename,
                        subfolder=img.subfolder,
                        file_path=dest_path_str,
                        thumbnail_path=thumb_path,
                        width=info["width"],
                        height=info["height"],
                        file_size_bytes=info["file_size_bytes"],
                        format=info["format"],
                        phash=info["phash"],
                    )
                    session.add(new_img)
                    await session.flush()
                    last_image_id = new_img.id

                counts["cropped"] += 1
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "crop_to_detection",
                    "status": "running", "done": i + 1, "total": len(images),
                    "percent": round((i + 1) / len(images) * 100, 1),
                    "current_item": img.filename,
                    "image_id": last_image_id,
                })

            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = counts
            await session.commit()
            if cancelled:
                job_queue.raise_if_cancelled(job_id)

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": total, "skipped": skipped}
