import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import BackgroundJob
from backend.ml.lut_processor import scan_lut_models, apply_lut_sync
from backend.schemas.lut import LutModelInfo, LutRunRequest
from backend.services.image_edit_job import EditSpec, run_image_edit_job
from backend.services.image_scope import resolve_scope_ids
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lut", tags=["lut"])


@router.get("/models", response_model=list[LutModelInfo])
async def list_lut_models():
    return scan_lut_models(settings.lut_models_dir)


@router.post("/run")
async def run_lut(body: LutRunRequest, db: AsyncSession = Depends(get_db)):
    image_ids = await resolve_scope_ids(
        db, dataset_id=body.dataset_id, image_ids=body.image_ids,
        subfolder=body.subfolder, quality_flags=body.quality_flags,
    )

    total = len(image_ids)
    auto_label = f"LUT — {Path(body.lut_path).name}"
    job = BackgroundJob(
        job_type="batch_lut",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=total,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    lut_path, intensity, replace = body.lut_path, body.intensity, body.replace

    def _process(src: str, dest: str) -> dict:
        # Looked up at call time, so a test's patch of this module's name holds.
        return apply_lut_sync(src, dest, lut_path, intensity, replace)

    spec = EditSpec(
        job_type="batch_lut",
        context="LUT",
        start_verb="Applying LUT to",
        action="could not be graded",
        suffix=lambda: "_lut",
        process=_process,
        history_op="lut",
        history_params={"lut": Path(lut_path).name, "intensity": intensity},
        gpu=False,  # numpy grading: stays on the default pool
    )
    await job_queue.enqueue(job, run_image_edit_job, image_ids=image_ids, replace=replace, spec=spec)
    return {"job_id": job.id, "total": total}
