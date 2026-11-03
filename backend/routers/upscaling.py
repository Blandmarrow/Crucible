import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import BackgroundJob
from backend.ml.upscaler import scan_upscale_models, upscale_image_sync
from backend.schemas.upscale import UpscaleModelInfo, UpscaleRunRequest
from backend.services.image_edit_job import EditSpec, run_image_edit_job
from backend.services.image_scope import resolve_scope_ids
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upscaling", tags=["upscaling"])


@router.get("/models", response_model=list[UpscaleModelInfo])
async def list_upscale_models():
    return scan_upscale_models(settings.upscale_models_dir)


def _scale_suffix(model_path: str) -> str:
    """The copy's stem suffix, from the scale the model's filename claims."""
    # Imported here so a test's patch of `upscaler._detect_scale` reaches it.
    from backend.ml.upscaler import _detect_scale
    raw_scale = _detect_scale(Path(model_path).stem)
    # Scale 1 is a restoration model (denoise / deblur / JPEG artifacts), not
    # an upscaler, so the suffix says so rather than claiming "_up1x".
    return (
        "_1x" if raw_scale == 1
        else f"_up{raw_scale}x" if raw_scale
        else "_upscale"
    )


@router.post("/run")
async def run_upscale(body: UpscaleRunRequest, db: AsyncSession = Depends(get_db)):
    image_ids = await resolve_scope_ids(
        db, dataset_id=body.dataset_id, image_ids=body.image_ids,
        subfolder=body.subfolder, quality_flags=body.quality_flags,
    )

    total = len(image_ids)
    auto_label = f"Upscale — {Path(body.model_path).stem}"
    job = BackgroundJob(
        job_type="batch_upscale",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=total,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    model_path, replace = body.model_path, body.replace
    target_w, target_h = body.target_width, body.target_height

    def _process(src: str, dest: str) -> dict:
        # Looked up at call time, so a test's patch of this module's name holds.
        return upscale_image_sync(src, dest, model_path, replace, target_w, target_h)

    spec = EditSpec(
        job_type="batch_upscale",
        context="Upscale",
        start_verb="Upscaling",
        action="could not be upscaled",
        suffix=lambda: _scale_suffix(model_path),
        process=_process,
        history_op="upscale",
        history_params={"model": Path(model_path).name},
    )
    await job_queue.enqueue(job, run_image_edit_job, image_ids=image_ids, replace=replace, spec=spec)
    return {"job_id": job.id, "total": total}
