"""Dataset-wide semantic tag consolidation endpoints (issue #44, Stage B).

Two background jobs: ``analyze`` proposes synonym clusters (result stored in the
job's ``result_data``, read back via GET /jobs/{id}); ``apply`` rewrites captions
from a confirmed ``{variant -> canonical}`` map. See
backend/services/tag_consolidation_service.py and docs/dev/tag-consolidation.md.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import BackgroundJob, Image
from backend.utils import normalize_subfolder
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/tag-consolidation", tags=["tag-consolidation"])


class AnalyzeRequest(BaseModel):
    threshold: float = Field(0.85, ge=0.5, le=1.0)
    subfolder: str | None = None
    label: str | None = None


class ApplyRequest(BaseModel):
    mapping: dict[str, str]
    subfolder: str | None = None
    label: str | None = None


class SubsumeRequest(BaseModel):
    subfolder: str | None = None
    dry_run: bool = False
    image_ids: list[str] | None = None


async def _count_images(db: AsyncSession, dataset_id: str, subfolder: str | None) -> int:
    q = select(func.count()).select_from(Image).where(
        Image.dataset_id == dataset_id,
        Image.caption_text != "",
    )
    if subfolder is not None:
        q = q.where(Image.subfolder == normalize_subfolder(subfolder))
    return int((await db.execute(q)).scalar() or 0)


@router.post("/dataset/{dataset_id}/analyze")
async def analyze_tags(dataset_id: str, body: AnalyzeRequest, db: AsyncSession = Depends(get_db)):
    count = await _count_images(db, dataset_id, body.subfolder)
    if count == 0:
        return {"job_id": None, "message": "No captioned images found"}

    auto_label = f"Analyze tags — {count} image{'s' if count != 1 else ''}"
    job = BackgroundJob(
        job_type="tag_consolidate_analyze",
        label=body.label or auto_label,
        dataset_id=dataset_id,
        total_items=count,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    threshold = body.threshold
    subfolder = body.subfolder

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.services.tag_consolidation_service import analyze

        async with AsyncSessionLocal() as session:
            result = await analyze(session, dataset_id, threshold, subfolder, job_id)
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = result
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": count}


@router.post("/dataset/{dataset_id}/subsume")
async def subsume_tags_endpoint(dataset_id: str, body: SubsumeRequest, db: AsyncSession = Depends(get_db)):
    """Synchronous per-caption subsumption cleanup (no model, no job). dry_run only counts."""
    from backend.services.tag_consolidation_service import subsume

    return await subsume(db, dataset_id, body.subfolder, body.dry_run, body.image_ids)


@router.post("/dataset/{dataset_id}/apply")
async def apply_consolidation(dataset_id: str, body: ApplyRequest, db: AsyncSession = Depends(get_db)):
    count = await _count_images(db, dataset_id, body.subfolder)
    if count == 0:
        return {"job_id": None, "message": "No captioned images found"}

    pairs = sum(1 for k, v in body.mapping.items() if k != v)
    auto_label = f"Consolidate tags — {pairs} mapping{'s' if pairs != 1 else ''}, {count} image{'s' if count != 1 else ''}"
    job = BackgroundJob(
        job_type="tag_consolidate_apply",
        label=body.label or auto_label,
        dataset_id=dataset_id,
        total_items=count,
        config={"mapping": body.mapping, "subfolder": body.subfolder},
    )
    db.add(job)
    await db.commit()

    mapping = body.mapping
    subfolder = body.subfolder

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.services.tag_consolidation_service import apply

        async with AsyncSessionLocal() as session:
            result = await apply(session, dataset_id, mapping, subfolder, job_id)
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = result
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": count}
