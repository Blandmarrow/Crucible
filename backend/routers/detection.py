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

_ALLOWED_MODELS = frozenset({"florence2_large", "florence2_promptgen"})
_ALLOWED_TASKS = frozenset({"<OD>", "<CAPTION_TO_PHRASE_GROUNDING>"})


@router.post("/run")
async def run_detection(body: DetectionJobRequest, db: AsyncSession = Depends(get_db)):
    if body.model not in _ALLOWED_MODELS:
        raise HTTPException(400, f"model must be one of: {sorted(_ALLOWED_MODELS)}")
    if body.task not in _ALLOWED_TASKS:
        raise HTTPException(400, f"task must be one of: {sorted(_ALLOWED_TASKS)}")
    if (
        body.task == "<CAPTION_TO_PHRASE_GROUNDING>"
        and not body.use_caption_as_prompt
        and not body.custom_prompt.strip()
    ):
        raise HTTPException(400, "custom_prompt is required for CAPTION_TO_PHRASE_GROUNDING (or enable use_caption_as_prompt)")

    query = select(Image.id, Image.file_path, Image.caption_text).where(Image.dataset_id == body.dataset_id)
    if body.image_ids:
        query = query.where(Image.id.in_(body.image_ids))
    if body.use_caption_as_prompt:
        query = query.where(Image.caption_text.isnot(None), Image.caption_text != "")
    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return {"job_id": None, "message": "No images found"}

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
        from backend.ml.florence_captioner import detect_image
        from backend.workers.progress import broadcaster

        variant = "promptgen" if "promptgen" in body.model else "large"
        model_entry = await model_manager.load_florence2(variant)

        total = len(image_data)
        start_time = time.monotonic()

        for i, (img_id, file_path, caption_text) in enumerate(image_data):
            async with AsyncSessionLocal() as session:
                job_row = await session.get(BackgroundJob, job_id)
                if job_row and job_row.status == "cancelled":
                    raise asyncio.CancelledError()

                prompt = caption_text if body.use_caption_as_prompt else body.custom_prompt
                detections: list[dict] = []
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
                import torch
                vram_mb = int(torch.cuda.memory_reserved() / 1024 / 1024) if i % 10 == 0 and torch.cuda.is_available() else 0
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
