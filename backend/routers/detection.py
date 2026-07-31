import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, and_, cast, delete, func, select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.licenses import copy_provenance
from backend.ml.mask_utils import detection_crop_rect, merge_detection_geometry
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob, Image
from backend.models.detection import Detection
from backend.schemas.detection import (
    DetectionBulkDeleteRequest,
    DetectionCropRequest,
    DetectionJobRequest,
    DetectionMergeRequest,
    DetectionOut,
    DetectionRefineRequest,
    DetectionUpdate,
    ManualDetectionRequest,
)
from backend.services import version_service
from backend.services.detection_service import remap_detections_for_crop
from backend.services.image_service import crop_image_to_dest, generate_thumbnail
from backend.utils import (
    ALLOWED_FLAG_KEYS,
    chunked,
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

# Tasks whose located regions are watermark phrases we can sync to Image.has_watermark.
_WATERMARK_SYNC_TASKS = frozenset({"text_prompt", "<CAPTION_TO_PHRASE_GROUNDING>"})


async def _apply_watermark_flag(session: AsyncSession, img_id: str, found: bool) -> None:
    """Set/clear ``Image.has_watermark`` for one scanned image (copy-then-reassign)."""
    img = await session.get(Image, img_id)
    if img is None:
        return
    flags = dict(img.quality_flags or {})     # copy-then-reassign invariant
    flags["has_watermark"] = found
    img.quality_flags = flags


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
    if body.sync_watermark_flag and body.task not in _WATERMARK_SYNC_TASKS:
        raise HTTPException(
            400,
            "sync_watermark_flag requires a text-prompt grounding task "
            "(text_prompt on sam2/sam3, or <CAPTION_TO_PHRASE_GROUNDING>)",
        )

    query = select(Image.id, Image.file_path, Image.caption_text).where(Image.dataset_id == body.dataset_id)
    if body.image_ids is not None:
        # `is not None`, not truthiness (same convention as bulk-delete/crop):
        # an empty selection must match nothing, never widen to the dataset.
        query = query.where(Image.id.in_(body.image_ids))
    else:
        # Dataset-scope filters (mirror bulk-delete / crop). Explicit ids win and
        # bypass these; subfolder/flag scoping is only for whole-dataset runs.
        if body.subfolder is not None:
            query = query.where(Image.subfolder == normalize_subfolder(body.subfolder))
        if body.quality_flags:
            valid_flags = [f for f in body.quality_flags if f in ALLOWED_FLAG_KEYS]
            if valid_flags:
                query = query.where(and_(*[Image.quality_flags[f].as_boolean().is_not(True) for f in valid_flags]))
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
        loop = asyncio.get_running_loop()

        async def _finish_watermark_sync() -> None:
            """Refresh dataset stats after a watermark-sync run so the flag counts
            update. No-op unless the run synced flags. Cancellation skips it."""
            if not body.sync_watermark_flag:
                return
            from backend.services.dataset_service import refresh_stats
            async with AsyncSessionLocal() as _stats_session:
                await refresh_stats(_stats_session, body.dataset_id)

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
                            delete(Detection).where(
                                Detection.image_id == img_id,
                                Detection.model == "nudenet",
                            )
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
                    inference_ok = False
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
                        inference_ok = True
                    except Exception:
                        logger.error("SAM2 prediction failed for %s", file_path, exc_info=True)

                    if body.overwrite:
                        await session.execute(
                            delete(Detection).where(
                                Detection.image_id == img_id,
                                Detection.model == "sam2",
                            )
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
                    if body.sync_watermark_flag and inference_ok:
                        await _apply_watermark_flag(session, img_id, bool(detections))
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
            await _finish_watermark_sync()
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
                    inference_ok = False
                    try:
                        fn = functools.partial(
                            sam3_predict_sync,
                            file_path,
                            sam3_entry.model,
                            body.custom_prompt,
                            sam3_threshold,
                        )
                        detections = await loop.run_in_executor(None, fn)
                        inference_ok = True
                    except Exception:
                        logger.error("SAM3 prediction failed for %s", file_path, exc_info=True)

                    if body.overwrite:
                        await session.execute(
                            delete(Detection).where(
                                Detection.image_id == img_id,
                                Detection.model == "sam3",
                            )
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
                    if body.sync_watermark_flag and inference_ok:
                        await _apply_watermark_flag(session, img_id, bool(detections))
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
            await _finish_watermark_sync()
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
                inference_ok = False
                try:
                    detections = await detect_image(
                        file_path, model_entry, body.task, prompt
                    )
                    inference_ok = True
                except Exception:
                    logger.error("Detection failed for %s", file_path, exc_info=True)

                if body.overwrite:
                    await session.execute(
                        delete(Detection).where(
                            Detection.image_id == img_id,
                            Detection.model == body.model,
                        )
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
                if (
                    body.sync_watermark_flag
                    and inference_ok
                    and body.task == "<CAPTION_TO_PHRASE_GROUNDING>"
                ):
                    await _apply_watermark_flag(session, img_id, bool(detections))
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

        await _finish_watermark_sync()

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


@router.get("/models/{dataset_id}")
async def get_dataset_models(dataset_id: str, db: AsyncSession = Depends(get_db)):
    """Distinct detection models in a dataset with the number of images each covers."""
    image_count = func.count(func.distinct(Detection.image_id))
    result = await db.execute(
        select(Detection.model, image_count.label("image_count"))
        .join(Image, Detection.image_id == Image.id)
        .where(Image.dataset_id == dataset_id)
        .group_by(Detection.model)
        .order_by(image_count.desc(), Detection.model.asc())
    )
    return [{"model": r.model, "image_count": r.image_count} for r in result.all()]


# Coverage histogram bucket edges (fraction of image area) + labels.
_COVERAGE_EDGES = [0.02, 0.10, 0.25, 0.50, 0.75, 0.95]
_COVERAGE_LABELS = ["<2%", "2–10%", "10–25%", "25–50%", "50–75%", "75–95%", ">95%"]


@router.get("/stats/{dataset_id}")
async def get_detection_stats(
    dataset_id: str,
    subfolder: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate detection/mask stats for the Stats page "Detections & Masks" section.

    All aggregates join ``Detection.image_id == Image.id`` scoped to ``dataset_id``
    and, when ``subfolder`` is given, ``Image.subfolder == subfolder`` (exact
    equality — the ``DatasetStats`` subfolder invariant). Coverage is the per-image
    ``SUM(mask_area)`` clamped to 1.0 (overlaps overcount — an approximation of the
    exported union mask, not a rasterized union), bucketed over images with ≥1
    detection. The score histogram gets an explicit "unscored" bucket for NULL
    (manual) scores.
    """
    img_filter = [Image.dataset_id == dataset_id]
    if subfolder is not None:
        img_filter.append(Image.subfolder == subfolder)

    def _det_query(*cols):
        return (
            select(*cols)
            .select_from(Detection)
            .join(Image, Detection.image_id == Image.id)
            .where(*img_filter)
        )

    total_images = (
        await db.execute(select(func.count()).select_from(Image).where(*img_filter))
    ).scalar_one()

    total_detections = (await db.execute(_det_query(func.count()))).scalar_one()
    images_with_detections = (
        await db.execute(_det_query(func.count(func.distinct(Detection.image_id))))
    ).scalar_one()
    distinct_labels = (
        await db.execute(_det_query(func.count(func.distinct(Detection.label))))
    ).scalar_one()
    bbox_only_count = (
        await db.execute(_det_query(func.count()).where(Detection.mask.is_(None)))
    ).scalar_one()

    # Label distribution — top 30 labels by detection count.
    label_rows = (
        await db.execute(
            _det_query(Detection.label, func.count().label("n"))
            .group_by(Detection.label)
            .order_by(func.count().desc(), Detection.label.asc())
            .limit(30)
        )
    ).all()
    label_distribution = {r.label: r.n for r in label_rows}

    # Per-model breakdown.
    model_rows = (
        await db.execute(
            _det_query(Detection.model, func.count().label("n"))
            .group_by(Detection.model)
            .order_by(func.count().desc(), Detection.model.asc())
        )
    ).all()
    model_distribution = {r.model: r.n for r in model_rows}

    # Score histogram — 10 bins (0.0–0.1 … 0.9–1.0) + an "unscored" bucket for NULLs.
    score_histogram = {
        f"{i / 10:.1f}–{(i + 1) / 10:.1f}": 0 for i in range(10)
    }
    score_bucket = cast(Detection.score * 10, Integer)
    score_rows = (
        await db.execute(
            _det_query(score_bucket.label("b"), func.count().label("n"))
            .where(Detection.score.isnot(None))
            .group_by(score_bucket)
        )
    ).all()
    bin_keys = list(score_histogram.keys())
    for r in score_rows:
        idx = min(max(int(r.b), 0), 9)  # score == 1.0 → bucket 10 → clamp into last bin
        score_histogram[bin_keys[idx]] += r.n
    score_histogram["unscored"] = (
        await db.execute(_det_query(func.count()).where(Detection.score.is_(None)))
    ).scalar_one()

    # One GROUP BY image_id pass drives both the coverage histogram (images with
    # ≥1 detection) and the detections-per-image histogram.
    per_image_rows = (
        await db.execute(
            _det_query(
                func.count().label("n"),
                func.coalesce(func.sum(Detection.mask_area), 0.0).label("cov"),
            ).group_by(Detection.image_id)
        )
    ).all()

    coverage_histogram = {lbl: 0 for lbl in _COVERAGE_LABELS}
    per_image_counts = {"1": 0, "2": 0, "3–5": 0, "6+": 0}
    for r in per_image_rows:
        cov = min(max(r.cov, 0.0), 1.0)
        placed = False
        for i, edge in enumerate(_COVERAGE_EDGES):
            if cov < edge:
                coverage_histogram[_COVERAGE_LABELS[i]] += 1
                placed = True
                break
        if not placed:
            coverage_histogram[_COVERAGE_LABELS[-1]] += 1

        if r.n >= 6:
            per_image_counts["6+"] += 1
        elif r.n >= 3:
            per_image_counts["3–5"] += 1
        elif r.n == 2:
            per_image_counts["2"] += 1
        elif r.n == 1:
            per_image_counts["1"] += 1

    images_without_detections = total_images - images_with_detections
    detections_per_image = {"0": images_without_detections, **per_image_counts}

    return {
        "total_detections": total_detections,
        "images_with_detections": images_with_detections,
        "images_without_detections": images_without_detections,
        "total_images": total_images,
        "distinct_labels": distinct_labels,
        "bbox_only_count": bbox_only_count,
        "label_distribution": label_distribution,
        "model_distribution": model_distribution,
        "score_histogram": score_histogram,
        "coverage_histogram": coverage_histogram,
        "detections_per_image": detections_per_image,
    }


@router.post("/bulk-delete")
async def bulk_delete_detections(
    body: DetectionBulkDeleteRequest, db: AsyncSession = Depends(get_db)
):
    """Delete detections matching a scope + optional label/model/score filters.

    Scope resolution mirrors ``crop_to_detection``: explicit ``image_ids`` win,
    otherwise ``dataset_id`` + ``subfolder`` + ``quality_flags`` exclusions.
    ``score_below`` uses SQL ``score < x`` which never matches NULL scores, so
    manual/unscored rows are immune (intended). ``dry_run`` returns the count
    that would be deleted without deleting.
    """
    # Resolve the image scope into an IN-condition for Detection.image_id.
    if body.image_ids is not None:
        # Explicit ids are bounded by the request body, so a literal IN is safe —
        # no need to round-trip through Image to re-materialize the same list
        # (a non-existent id simply matches no detections via the FK).
        if not body.image_ids:
            return {"deleted": 0, "dry_run": body.dry_run}
        scope_condition = Detection.image_id.in_(body.image_ids)
    else:
        q = select(Image.id).where(Image.dataset_id == body.dataset_id)
        if body.subfolder is not None:
            q = q.where(Image.subfolder == normalize_subfolder(body.subfolder))
        if body.quality_flags:
            valid_flags = [f for f in body.quality_flags if f in ALLOWED_FLAG_KEYS]
            if valid_flags:
                q = q.where(and_(*[Image.quality_flags[f].as_boolean().is_not(True) for f in valid_flags]))
        # Correlated subquery — never materialize the id list, which on a large
        # dataset can exceed SQLite's bind-parameter ceiling (see utils.chunked).
        scope_condition = Detection.image_id.in_(q)

    conditions = [scope_condition]
    if body.labels:
        conditions.append(Detection.label.in_(body.labels))
    if body.models:
        conditions.append(Detection.model.in_(body.models))
    if body.score_below is not None:
        conditions.append(Detection.score < body.score_below)

    if body.dry_run:
        count = await db.execute(
            select(func.count()).select_from(Detection).where(and_(*conditions))
        )
        return {"deleted": count.scalar_one(), "dry_run": True}

    result = await db.execute(delete(Detection).where(and_(*conditions)))
    await db.commit()
    return {"deleted": result.rowcount or 0, "dry_run": False}


@router.post("/merge", response_model=DetectionOut)
async def merge_detections(body: DetectionMergeRequest, db: AsyncSession = Depends(get_db)):
    """Merge ≥2 detections on the same image into one ``model="manual"`` row."""
    result = await db.execute(
        select(Detection).where(Detection.id.in_(body.detection_ids))
    )
    found = {d.id: d for d in result.scalars().all()}
    missing = [i for i in body.detection_ids if i not in found]
    if missing:
        raise HTTPException(404, f"Detection(s) not found: {missing}")

    dets = [found[i] for i in body.detection_ids]   # preserve request order
    if len({d.image_id for d in dets}) != 1:
        raise HTTPException(400, "All detections to merge must belong to the same image")

    mask_json, bbox = merge_detection_geometry([(d.mask, d.bbox) for d in dets])
    scores = [d.score for d in dets if d.score is not None]
    merged = Detection(
        image_id=dets[0].image_id,
        label=dets[0].label,
        bbox=bbox,
        score=max(scores) if scores else None,
        mask=mask_json,
        model="manual",
        task="merge",
        detected_at=datetime.utcnow(),
    )
    db.add(merged)
    for d in dets:
        await db.delete(d)
    await db.commit()
    await db.refresh(merged)
    return merged


def _sanitize_bbox(bbox: list[float]) -> list[float]:
    """Order + clamp a normalized bbox; raise 400 if either extent < 0.002."""
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        raise HTTPException(400, "bbox must be four numbers")
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    x1, y1 = max(x1, 0.0), max(y1, 0.0)
    x2, y2 = min(x2, 1.0), min(y2, 1.0)
    if (x2 - x1) < 0.002 or (y2 - y1) < 0.002:
        raise HTTPException(400, "bbox is too small")
    return [x1, y1, x2, y2]


@router.post("/manual")
async def create_manual_detection(
    body: ManualDetectionRequest, db: AsyncSession = Depends(get_db)
):
    """Create a hand-drawn detection, optionally segmenting the box with SAM2.

    Without SAM: inserts a plain bbox row synchronously and returns a
    ``DetectionOut`` dict. With SAM (``refine_with_sam=True``): enqueues a
    detection job that segments the box; on success stores a ``task="box_prompt"``
    row, on SAM failure falls back to a plain ``task="manual"`` bbox row so the
    drawing is never lost. Returns ``{job_id}``. (Sync-or-job union response has
    precedent in ``POST /images/{id}/crop``.)
    """
    image = await db.get(Image, body.image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    bbox = _sanitize_bbox(body.bbox)

    if not body.refine_with_sam:
        det = Detection(
            image_id=body.image_id,
            label=body.label,
            bbox=bbox,
            score=None,
            mask=None,
            model="manual",
            task="manual",
            detected_at=datetime.utcnow(),
        )
        db.add(det)
        await db.commit()
        await db.refresh(det)
        return DetectionOut.model_validate(det).model_dump(mode="json")

    filename = image.filename
    file_path = image.file_path
    dataset_id = image.dataset_id
    image_id = body.image_id
    label = body.label

    job = BackgroundJob(
        job_type="detection",
        label=f"Manual box + SAM — {filename}",
        dataset_id=dataset_id,
        total_items=1,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        import functools

        from backend.database import AsyncSessionLocal
        from backend.ml.sam2_predictor import predict_sync
        from backend.workers.progress import broadcaster

        loop = asyncio.get_running_loop()
        sam2_entry = await model_manager.load_sam2(job_id=job_id, loop=loop, dataset_id=dataset_id)

        detections: list[dict] = []
        try:
            fn = functools.partial(
                predict_sync, file_path, sam2_entry.model, "box", "", None, None, 0.35, bbox,
            )
            detections = await loop.run_in_executor(None, fn)
        except Exception:
            logger.error("Manual-box SAM2 prediction failed for %s", file_path, exc_info=True)

        async with AsyncSessionLocal() as session:
            now = datetime.utcnow()
            if detections:
                det = detections[0]
                session.add(Detection(
                    image_id=image_id,
                    label=label,
                    bbox=det["bbox"],
                    score=det.get("score"),
                    mask=det.get("mask"),
                    model="manual",
                    task="box_prompt",
                    detected_at=now,
                ))
                message = "Segmented drawn box"
            else:
                # SAM produced nothing — keep the drawn box as a plain manual row.
                session.add(Detection(
                    image_id=image_id,
                    label=label,
                    bbox=bbox,
                    score=None,
                    mask=None,
                    model="manual",
                    task="manual",
                    detected_at=now,
                ))
                message = "SAM found no mask — kept plain box"
            await session.commit()

        await broadcaster.emit(job_id, {
            "type": "progress", "job_id": job_id, "job_type": "detection",
            "status": "running", "done": 1, "total": 1, "percent": 100.0,
            "current_item": filename, "message": message,
        })

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


async def _fetch_bboxes_by_image(
    db: AsyncSession, image_ids: list[str], labels: list[str] | None
) -> dict[str, list[list[float]]]:
    """Batch-fetch detection bboxes keyed by image id, chunked to keep IN() bounded."""
    by_image: dict[str, list[list[float]]] = {}
    for chunk in chunked(image_ids):
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

    # Normalize the destination subfolder once, up front, so a bad path 400s
    # immediately instead of failing inside the background job.
    if body.dest_subfolder is not None:
        body.dest_subfolder = normalize_subfolder(body.dest_subfolder)

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
            images = []
            for chunk in chunked(matched_ids):
                # undefer: each crop copies its parent's provenance, source_meta
                # included, and a deferred lazy load on an async session raises.
                result = await session.execute(
                    select(Image).where(Image.id.in_(chunk)).options(undefer(Image.source_meta))
                )
                images.extend(result.scalars().all())
            bboxes_by_image = await _fetch_bboxes_by_image(session, matched_ids, cfg["labels"])
            loop = asyncio.get_running_loop()

            replace = cfg["replace"]
            dest_subfolder = cfg["dest_subfolder"]  # None = inherit source subfolder
            # `thumbnails_stale` counts replace-mode crops whose *preview* could not
            # be recut in the post-commit epilogue. The image itself is correct and
            # committed; only the gallery tile is stale, and the realistic trigger
            # (a full volume, a read-only thumbnails/) hits every image in the run.
            counts = {
                "cropped": 0, "skipped_no_detection": 0, "skipped_noop": 0,
                "failed": 0, "thumbnails_stale": 0,
            }

            # Occupied/planned thumbnail stems for the non-replace path, keyed by
            # thumbnail directory: matched images can span multiple datasets (each
            # with its own thumbnails/ dir), so a single flat set would false-share
            # stems across datasets. Built lazily per dir inside the loop;
            # planned_by_dir accumulates across iterations (mutated by
            # unique_filename_with_thumb per its contract).
            occupied_by_dir: dict[Path, set[str]] = {}
            planned_by_dir: dict[Path, set[str]] = {}

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
                # Capture the OLD (pre-crop) transposed dims for detection remap —
                # rect is in this frame; img.width/height get overwritten below.
                old_size = (img.width, img.height)

                if replace:
                    await version_service.protect_file_before_overwrite(img.id, img.file_path, session)
                    await session.commit()  # persist the COW hash backfill before the overwrite
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
                    # Replace-mode crop changed the image geometry: remap the
                    # image's detections into the crop frame (drop ones outside).
                    # In the same transaction as the geometry it describes.
                    await remap_detections_for_crop(session, img.id, rect, old_size)
                    # Nothing fallible between the (already-done) overwrite and this
                    # commit — a raise before here would roll back the geometry,
                    # processing_history and detection remaps of *every* image the
                    # loop has already overwritten on disk (PM-013). Committing per
                    # image is what bounds the damage to the one that failed.
                    await session.commit()

                    # --- epilogue: best-effort, cannot undo the crop ---
                    # `expire_on_commit=False` (backend/database.py) keeps `img`
                    # readable after the commit without a refresh.
                    if img.thumbnail_path:
                        try:
                            await loop.run_in_executor(
                                None, generate_thumbnail, str(src_path), img.thumbnail_path
                            )
                        except Exception as exc:
                            # A stale thumbnail is cosmetic; the cropped image is
                            # committed and serves. Counted rather than merely
                            # logged so the run can say so: TopBar reads this count
                            # and points at Bulk Edit → Thumbnails.
                            counts["thumbnails_stale"] += 1
                            logger.warning(
                                "Detection crop: thumbnail for %s could not be "
                                "regenerated: %s", img.filename, exc,
                            )
                    last_image_id = img.id
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
                        subfolder=dest_subfolder if dest_subfolder is not None else img.subfolder,
                        file_path=dest_path_str,
                        thumbnail_path=thumb_path,
                        width=info["width"],
                        height=info["height"],
                        file_size_bytes=info["file_size_bytes"],
                        format=info["format"],
                        phash=info["phash"],
                        # A crop inherits its parent's source and license.
                        **copy_provenance(img),
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


# ---------------------------------------------------------------------------
# Every route below takes `{detection_id}`; keep new literal-segment routes above
# this line. FastAPI matches in declaration order, so a literal route declared
# after a parameterized one is shadowed the moment their methods collide — the
# PM-018 shape. `POST /crop` was declared down here and was safe only
# incidentally: there is no `POST /{detection_id}` today, and `detection_id: int`
# would have 422'd on the segment "crop" rather than routing it correctly.
# ---------------------------------------------------------------------------

@router.patch("/{detection_id}", response_model=DetectionOut)
async def update_detection(
    detection_id: int, body: DetectionUpdate, db: AsyncSession = Depends(get_db)
):
    det = await db.get(Detection, detection_id)
    if not det:
        raise HTTPException(404, "Detection not found")
    det.label = body.label
    await db.commit()
    await db.refresh(det)
    return det


@router.delete("/{detection_id}", status_code=204)
async def delete_detection(detection_id: int, db: AsyncSession = Depends(get_db)):
    det = await db.get(Detection, detection_id)
    if not det:
        raise HTTPException(404, "Detection not found")
    await db.delete(det)
    await db.commit()


@router.post("/{detection_id}/refine")
async def refine_detection(
    detection_id: int, body: DetectionRefineRequest, db: AsyncSession = Depends(get_db)
):
    """Refine an existing mask with point prompts (SAM2), in place.

    Enqueues a detection job seeded by the detection's current mask logits. On
    success the row is updated in place (``model="manual"``, ``task="refine"``);
    if the row was deleted meanwhile the worker no-ops gracefully. Returns
    ``{job_id}``. 400 when the detection has no mask to refine.
    """
    det = await db.get(Detection, detection_id)
    if not det:
        raise HTTPException(404, "Detection not found")
    if det.mask is None:
        raise HTTPException(400, "Detection has no mask to refine")

    image = await db.get(Image, det.image_id)
    if not image:
        raise HTTPException(404, "Image not found")

    # Capture into locals before the worker closure.
    mask_json = det.mask
    bbox = det.bbox
    file_path = image.file_path
    filename = image.filename
    dataset_id = image.dataset_id
    point_prompts = body.point_prompts
    point_labels = body.point_labels

    job = BackgroundJob(
        job_type="detection",
        label=f"Refine mask — {filename}",
        dataset_id=dataset_id,
        total_items=1,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        import functools

        from backend.database import AsyncSessionLocal
        from backend.ml.sam2_predictor import refine_sync
        from backend.workers.progress import broadcaster

        loop = asyncio.get_running_loop()
        sam2_entry = await model_manager.load_sam2(job_id=job_id, loop=loop, dataset_id=dataset_id)

        result: dict | None = None
        try:
            fn = functools.partial(
                refine_sync, file_path, sam2_entry.model, mask_json, bbox,
                point_prompts, point_labels,
            )
            result = await loop.run_in_executor(None, fn)
        except Exception:
            logger.error("Mask refine failed for %s", file_path, exc_info=True)

        async with AsyncSessionLocal() as session:
            row = await session.get(Detection, detection_id)
            if row is None:
                message = "Detection was removed before refine completed"
            elif result is None:
                message = "Refine produced no mask — unchanged"
            else:
                row.mask = result["mask"]
                row.bbox = result["bbox"]
                row.score = result.get("score")
                row.model = "manual"
                row.task = "refine"
                row.detected_at = datetime.utcnow()
                await session.commit()
                message = "Mask refined"

        await broadcaster.emit(job_id, {
            "type": "progress", "job_id": job_id, "job_type": "detection",
            "status": "running", "done": 1, "total": 1, "percent": 100.0,
            "current_item": filename, "message": message,
        })

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}
