from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import BackgroundJob
from backend.services.export_service import (
    export_aitoolkit,
    export_kohya,
    export_plain,
    preview_export,
)
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/export", tags=["export"])


class KohyaExportRequest(BaseModel):
    dataset_id: str
    output_dir: str
    n_repeats: int = 10
    concept_token: str = "concept"
    image_ids: list[str] | None = None
    output_format: str = "original"
    jpeg_quality: int = 95
    caption_format: str = "txt"
    resize_to: int | None = None
    aesthetic_min: float | None = None
    captioned_only: bool = False
    exclude_flags: str = ""   # comma-separated flag names
    style_sim_min: float | None = None
    subfolders: list[str] | None = None
    strip_metadata: bool = False


class AIToolkitExportRequest(BaseModel):
    dataset_id: str
    output_dir: str
    concept_name: str = "concept"
    image_ids: list[str] | None = None
    output_format: str = "original"
    jpeg_quality: int = 95
    caption_format: str = "txt"
    resize_to: int | None = None
    aesthetic_min: float | None = None
    captioned_only: bool = False
    exclude_flags: str = ""
    style_sim_min: float | None = None
    subfolders: list[str] | None = None
    strip_metadata: bool = False


class PlainExportRequest(BaseModel):
    dataset_id: str
    output_dir: str
    image_ids: list[str] | None = None
    output_format: str = "original"
    jpeg_quality: int = 95
    resize_to: int | None = None
    aesthetic_min: float | None = None
    captioned_only: bool = False
    exclude_flags: str = ""
    style_sim_min: float | None = None
    subfolders: list[str] | None = None
    strip_metadata: bool = False


def _parse_flags(s: str) -> list[str]:
    return [f.strip() for f in s.split(",") if f.strip()]


@router.post("/kohya")
async def export_kohya_endpoint(body: KohyaExportRequest, db: AsyncSession = Depends(get_db)):
    job = BackgroundJob(
        job_type="export",
        dataset_id=body.dataset_id,
        total_items=0,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await export_kohya(
                session,
                body.dataset_id,
                body.output_dir,
                body.n_repeats,
                body.concept_token,
                body.image_ids,
                body.output_format,
                body.jpeg_quality,
                body.caption_format,
                body.resize_to,
                body.aesthetic_min,
                body.captioned_only,
                _parse_flags(body.exclude_flags),
                body.style_sim_min,
                body.subfolders,
                body.strip_metadata,
                job_id=job_id,
            )
        async with AsyncSessionLocal() as session:
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = result
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/aitoolkit")
async def export_aitoolkit_endpoint(body: AIToolkitExportRequest, db: AsyncSession = Depends(get_db)):
    job = BackgroundJob(
        job_type="export",
        dataset_id=body.dataset_id,
        total_items=0,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await export_aitoolkit(
                session,
                body.dataset_id,
                body.output_dir,
                body.concept_name,
                body.image_ids,
                body.output_format,
                body.jpeg_quality,
                body.caption_format,
                body.resize_to,
                body.aesthetic_min,
                body.captioned_only,
                _parse_flags(body.exclude_flags),
                body.style_sim_min,
                body.subfolders,
                body.strip_metadata,
                job_id=job_id,
            )
        async with AsyncSessionLocal() as session:
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = result
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/plain")
async def export_plain_endpoint(body: PlainExportRequest, db: AsyncSession = Depends(get_db)):
    job = BackgroundJob(
        job_type="export",
        dataset_id=body.dataset_id,
        total_items=0,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await export_plain(
                session,
                body.dataset_id,
                body.output_dir,
                body.image_ids,
                body.output_format,
                body.jpeg_quality,
                body.resize_to,
                body.aesthetic_min,
                body.captioned_only,
                _parse_flags(body.exclude_flags),
                body.style_sim_min,
                body.subfolders,
                body.strip_metadata,
                job_id=job_id,
            )
        async with AsyncSessionLocal() as session:
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = result
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.get("/preview/{dataset_id}")
async def preview(
    dataset_id: str,
    aesthetic_min: float | None = Query(default=None),
    captioned_only: bool = Query(default=False),
    exclude_flags: str = Query(default=""),
    style_sim_min: float | None = Query(default=None),
    subfolders: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    subfolder_list = [s.strip() for s in subfolders.split(",") if s.strip()] or None
    return await preview_export(
        db,
        dataset_id,
        aesthetic_min,
        captioned_only,
        _parse_flags(exclude_flags),
        style_sim_min,
        subfolder_list,
    )
