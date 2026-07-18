import asyncio
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob, Image
from backend.models.detection import Detection
from backend.schemas.detection import DetectionJobRequest, DetectionOut
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
