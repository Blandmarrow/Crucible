import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from backend.database import get_db
from backend.models import BackgroundJob
from backend.schemas.job import JobOut
from backend.services.job_retry import RetryRefused, is_retryable, retry_job
from backend.workers.job_queue import job_queue
from backend.workers.progress import broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


JOB_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
MAX_IDS_FILTER = 500


def _csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _out(job: BackgroundJob) -> JobOut:
    return JobOut.model_validate(job).model_copy(update={"retryable": is_retryable(job)})


@router.get("/", response_model=list[JobOut])
async def list_jobs(
    limit: int = Query(50, ge=1, le=500),
    status: str | None = Query(
        None, description="Comma-separated subset of: " + ", ".join(JOB_STATUSES)
    ),
    ids: str | None = Query(None, description=f"Comma-separated job ids, at most {MAX_IDS_FILTER}"),
    db: AsyncSession = Depends(get_db),
):
    """Newest first. `status` and `ids` are ANDed; both feed the client's reconcile
    (`frontend/src/utils/jobReconcile.ts`), which catches up on terminal events an
    SSE gap lost."""
    query = select(BackgroundJob)
    statuses = _csv(status)
    if statuses:
        unknown = sorted(set(statuses) - set(JOB_STATUSES))
        if unknown:
            raise HTTPException(422, f"Unknown job status: {', '.join(unknown)}")
        query = query.where(BackgroundJob.status.in_(statuses))
    id_list = _csv(ids)
    if ids is not None:
        # An `ids` filter naming nothing matches nothing — never "no filter".
        if not id_list:
            return []
        if len(id_list) > MAX_IDS_FILTER:
            raise HTTPException(422, f"At most {MAX_IDS_FILTER} ids per request")
        query = query.where(BackgroundJob.id.in_(id_list))
    result = await db.execute(query.order_by(BackgroundJob.created_at.desc()).limit(limit))
    return [_out(job) for job in result.scalars().all()]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(BackgroundJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _out(job)


@router.post("/{job_id}/retry")
async def retry(job_id: str, db: AsyncSession = Depends(get_db)):
    """Resubmit a failed or cancelled job's stored config (`services/job_retry.py`).

    Answers `{"job_id", "total"}`, or `{"job_id": None, "message"}` when the stopped job
    left nothing to do. 409 when the job cannot be retried, or a retry of it is still
    queued or running; 404 when the job or its dataset is gone.
    """
    job = await db.get(BackgroundJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    try:
        return await retry_job(db, job)
    except RetryRefused as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.delete("/{job_id}", status_code=204)
async def cancel_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(BackgroundJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status not in ("running", "pending"):
        return
    if job.status == "pending":
        # Only a queued job is terminal the moment it is cancelled: the worker's
        # dequeue check reads this status to skip it, and nothing is running to
        # report a different outcome.
        job.status = "cancelled"
        await db.commit()
        run_id = (job.config or {}).get("run_id")
        if run_id:
            # An automated run's child, cancelled before it started: step the run now.
            # The worker would only do it on reaching the job, so until the job ahead
            # finished the run read `running` with nothing in flight (found 2026-11-07).
            # Its later call finds the stage already ended and does nothing.
            from backend.services import run_service

            try:
                await run_service.advance_run(run_id, job_id)
            except Exception:
                logger.exception("Could not step run %s after its queued job was cancelled", run_id)
    # A running job stops cooperatively — loops poll the flag between items, so
    # the job keeps working until its next check (for comfy_prompts that is a
    # whole LLM call, up to 120 s). Writing "cancelled" here would put a terminal
    # badge on the Logs page for a job still doing work, and would be wrong
    # outright when the job goes on to finish what was asked. The worker writes
    # the real final status in its CancelledError and completion paths.
    job_queue.request_cancel(job_id)


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
