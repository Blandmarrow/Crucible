import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from backend.database import get_db
from backend.models import BackgroundJob
from backend.schemas.job import JobOut
from backend.workers.progress import broadcaster

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/", response_model=list[JobOut])
async def list_jobs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BackgroundJob).order_by(BackgroundJob.created_at.desc()).limit(50)
    )
    return result.scalars().all()


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(BackgroundJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.delete("/{job_id}", status_code=204)
async def cancel_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(BackgroundJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status in ("running", "pending"):
        job.status = "cancelled"
        await db.commit()


@router.get("/stream/{job_id}")
async def stream_job(job_id: str, request: Request):
    async def generate():
        async for event in broadcaster.stream(job_id):
            if await request.is_disconnected():
                break
            yield {"data": json.dumps(event)}

    return EventSourceResponse(generate())


@router.get("/stream/all/events")
async def stream_all(request: Request):
    async def generate():
        async for event in broadcaster.stream("all", stop_on_terminal=False):
            if await request.is_disconnected():
                break
            yield {"data": json.dumps(event)}

    return EventSourceResponse(generate())
