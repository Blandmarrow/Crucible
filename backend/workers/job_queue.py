import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine

from sqlalchemy import delete, select, update

from backend.database import AsyncSessionLocal
from backend.models import BackgroundJob
from backend.workers.progress import broadcaster

logger = logging.getLogger(__name__)

# Retention policy for finished `background_jobs` rows, applied once per startup.
# The table only ever grows otherwise — every caption/export/detection run adds a
# row, and nothing deletes them. The floor exists so a long-idle install still
# keeps recent history: LogsPage asks for 200 rows and the jobs list caps at 500.
JOB_RETENTION_DAYS = 30
JOB_RETENTION_MIN_KEEP = 500


async def mark_interrupted_jobs() -> int:
    """Fail any job rows left 'running'/'pending' by a previous process.

    The queue lives in memory, so a shutdown/restart orphans those rows —
    nothing will ever resume them, and they render as stuck-forever in the UI.
    Called once at startup, before the worker starts. Returns the row count.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.status.in_(("running", "pending")))
            .values(
                status="failed",
                error_msg="Interrupted by server shutdown or restart",
                finished_at=datetime.utcnow(),
            )
        )
        await db.commit()
    count = result.rowcount or 0
    if count:
        logger.info("Marked %d interrupted job(s) from a previous run as failed", count)
    return count


async def sweep_old_jobs() -> int:
    """Delete finished job rows older than the retention window. Returns the count.

    Only `completed`/`failed`/`cancelled` rows are eligible — anything still
    pending/running belongs to this process (and `mark_interrupted_jobs`, which
    runs first at startup, has already resolved leftovers from the previous one).
    The newest ``JOB_RETENTION_MIN_KEEP`` rows overall are always kept, so an
    install that has been idle for a year still shows its last runs.
    """
    cutoff = datetime.utcnow() - timedelta(days=JOB_RETENTION_DAYS)
    async with AsyncSessionLocal() as db:
        keep_floor = await db.scalar(
            select(BackgroundJob.created_at)
            .order_by(BackgroundJob.created_at.desc())
            .offset(JOB_RETENTION_MIN_KEEP - 1)
            .limit(1)
        )
        if keep_floor is None:
            # Fewer than MIN_KEEP rows exist — nothing can be dropped.
            return 0
        result = await db.execute(
            delete(BackgroundJob).where(
                BackgroundJob.status.in_(("completed", "failed", "cancelled")),
                BackgroundJob.created_at < cutoff,
                BackgroundJob.created_at < keep_floor,
            )
        )
        await db.commit()
    count = result.rowcount or 0
    if count:
        logger.info("Swept %d job row(s) older than %d days", count, JOB_RETENTION_DAYS)
    return count


class JobQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._current_job_id: str | None = None
        self._cancel_requested: set[str] = set()

    def request_cancel(self, job_id: str) -> None:
        """Flag a running job for cooperative cancellation. Loops check this flag."""
        self._cancel_requested.add(job_id)

    def cancel_requested(self, job_id: str) -> bool:
        """Non-raising cancellation check for loops that keep partial results."""
        return job_id in self._cancel_requested

    def raise_if_cancelled(self, job_id: str) -> None:
        """Raise CancelledError if the job was cancelled — for loops with per-item
        commits (or additive file output) where nothing needs preserving on stop."""
        if job_id in self._cancel_requested:
            raise asyncio.CancelledError()

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def enqueue(
        self,
        job: BackgroundJob,
        fn: Callable[..., Coroutine[Any, Any, None]],
        **kwargs: Any,
    ) -> str:
        await self._queue.put((job, fn, kwargs))
        await broadcaster.emit(job.id, {
            "type": "progress",
            "job_id": job.id,
            "job_type": job.job_type,
            "label": job.label,
            "dataset_id": job.dataset_id,
            "status": "pending",
            "message": "Queued",
        })
        return job.id

    @property
    def current_job_id(self) -> str | None:
        return self._current_job_id

    async def _worker(self) -> None:
        while True:
            job, fn, kwargs = await self._queue.get()
            self._current_job_id = job.id
            async with AsyncSessionLocal() as db:
                job_row = await db.get(BackgroundJob, job.id)
                if job_row and job_row.status == "cancelled":
                    # Cancelled while waiting in the queue — skip it
                    self._current_job_id = None
                    self._cancel_requested.discard(job.id)
                    self._queue.task_done()
                    await broadcaster.emit(job.id, {
                        "type": "progress",
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "label": job.label,
                        "dataset_id": job.dataset_id,
                        "status": "cancelled",
                        "message": "Cancelled before starting.",
                    })
                    continue
                if job_row:
                    job_row.status = "running"
                    job_row.started_at = datetime.utcnow()
                    await db.commit()

            await broadcaster.emit(job.id, {
                "type": "progress",
                "job_id": job.id,
                "job_type": job.job_type,
                "label": job.label,
                "dataset_id": job.dataset_id,
                "status": "running",
                "done": 0,
                "total": job.total_items,
                "percent": 0.0,
                "message": f"Starting {job.label or job.job_type}...",
            })

            try:
                await fn(job_id=job.id, **kwargs)
                final_total = 0
                async with AsyncSessionLocal() as db:
                    job_row = await db.get(BackgroundJob, job.id)
                    if job_row:
                        job_row.status = "completed"
                        job_row.finished_at = datetime.utcnow()
                        await db.commit()
                        final_total = job_row.total_items
                await broadcaster.emit(job.id, {
                    "type": "progress",
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "label": job.label,
                    "dataset_id": job.dataset_id,
                    "status": "completed",
                    "done": final_total,
                    "total": final_total,
                    "percent": 100.0,
                    "message": "Done.",
                })
            except asyncio.CancelledError:
                async with AsyncSessionLocal() as db:
                    job_row = await db.get(BackgroundJob, job.id)
                    if job_row:
                        job_row.status = "cancelled"
                        job_row.finished_at = datetime.utcnow()
                        await db.commit()
                await broadcaster.emit(job.id, {"type": "progress", "job_id": job.id, "job_type": job.job_type, "label": job.label, "dataset_id": job.dataset_id, "status": "cancelled"})
            except Exception as e:
                logger.exception("Job %s failed", job.id)
                async with AsyncSessionLocal() as db:
                    job_row = await db.get(BackgroundJob, job.id)
                    if job_row:
                        job_row.status = "failed"
                        job_row.error_msg = str(e)
                        job_row.finished_at = datetime.utcnow()
                        await db.commit()
                await broadcaster.emit(job.id, {
                    "type": "progress",
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "label": job.label,
                    "dataset_id": job.dataset_id,
                    "status": "failed",
                    "message": str(e),
                })
            finally:
                self._current_job_id = None
                self._cancel_requested.discard(job.id)
                self._queue.task_done()


job_queue = JobQueue()
