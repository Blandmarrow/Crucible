import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine

from backend.database import AsyncSessionLocal
from backend.models import BackgroundJob
from backend.workers.progress import broadcaster

logger = logging.getLogger(__name__)


class JobQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._current_job_id: str | None = None

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
                if job_row:
                    job_row.status = "running"
                    job_row.started_at = datetime.utcnow()
                    await db.commit()

            await broadcaster.emit(job.id, {
                "type": "progress",
                "job_id": job.id,
                "job_type": job.job_type,
                "status": "running",
                "done": 0,
                "total": job.total_items,
                "percent": 0.0,
                "message": f"Starting {job.job_type}...",
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
                await broadcaster.emit(job.id, {"type": "progress", "job_id": job.id, "status": "cancelled"})
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
                    "status": "failed",
                    "message": str(e),
                })
            finally:
                self._current_job_id = None
                self._queue.task_done()


job_queue = JobQueue()
