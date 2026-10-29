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

# The `error_msg` of a job the server stopped: both the one `stop()` interrupts and
# every leftover row `mark_interrupted_jobs` fails at the next startup, so the two
# read identically on the Logs page.
INTERRUPTED_MSG = "Interrupted by server shutdown or restart"

# How long `stop()` waits for the running job to honour the cooperative cancel flag
# before it cancels the worker task outright, and how long it then waits for that
# cancel to unwind before giving up on the task (GPT-012).
STOP_GRACE_SECONDS = 10.0
STOP_UNWIND_SECONDS = 5.0

# Job types that never poll the cancel flag, so their grace is pure waiting for them
# to finish on their own — and a forced stop mid-run leaves a half-written snapshot or
# a half-applied restore. They get longer before `stop()` forces them. `create_branch`
# is here because its job form always snapshots (a branch without one runs inline).
NON_COOPERATIVE_GRACE: dict[str, float] = {
    "create_snapshot": 120.0,
    "create_branch": 120.0,
    "restore_snapshot": 120.0,
    "checkout_branch": 120.0,
}


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
                error_msg=INTERRUPTED_MSG,
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
    """The single in-process worker that runs every background job, one at a time.

    Two kinds of stop exist and must never be confused (GPT-012, PM-027):

    - **Job cancellation** is cooperative. `request_cancel` sets a flag, the job's
      loop sees it and raises `CancelledError` itself via `raise_if_cancelled`, and
      the worker records `cancelled` and carries on with the next job.
    - **Queue shutdown** is `stop()`. It sets `_stopping` first, so nothing new is
      dequeued, asks the running job to stop through that same flag, and only after
      a grace period cancels the worker *task*. The per-job handler tells the two
      apart by `asyncio.current_task().cancelling()`: a task cancel is re-raised and
      ends the worker, where a cooperative one is absorbed.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._current_job_id: str | None = None
        self._current_job_type: str | None = None
        self._cancel_requested: set[str] = set()
        self._stopping = False
        # The job `stop()` itself asked to stop, as opposed to one the user had
        # already cancelled: it is recorded `failed`/INTERRUPTED_MSG, not `cancelled`.
        self._stop_interrupted: str | None = None

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
        self._stopping = False
        self._stop_interrupted = None
        task = asyncio.create_task(self._worker(), name="job-queue-worker")
        task.add_done_callback(self._on_worker_done)
        self._worker_task = task

    def _on_worker_done(self, task: asyncio.Task) -> None:
        # Retrieve the exception unconditionally, so asyncio does not also log
        # "Task exception was never retrieved".
        exc = None if task.cancelled() else task.exception()
        if self._stopping or task is not self._worker_task:
            return
        # Every job's lifecycle is wrapped, so a worker that ends while nobody asked
        # it to has hit something restarting the task would not cure (a broken
        # queue, a cancel from outside `stop()`). No auto-restart: say so loudly.
        logger.critical(
            "Job queue worker exited unexpectedly; no further background jobs will "
            "run until the server restarts",
            exc_info=exc,
        )

    async def stop(
        self,
        grace: float | None = None,
        unwind: float = STOP_UNWIND_SECONDS,
    ) -> None:
        """Stop the worker. Always returns, within about ``grace + unwind`` seconds.

        Queued jobs never start; they stay `pending` for `mark_interrupted_jobs` to
        fail at the next startup. A running job is first asked to stop through the
        cooperative flag and given ``grace`` seconds — by default its
        `NON_COOPERATIVE_GRACE` entry, else `STOP_GRACE_SECONDS`. Past that, the
        worker task gets exactly one `cancel()` (a second would interrupt the
        handler's own status write) and ``unwind`` seconds to finish. A task still
        running after that — a job that swallows `CancelledError` — is logged and
        abandoned. An executor thread cannot be stopped from here at all.
        """
        task = self._worker_task
        if task is None or task.done():
            return
        self._stopping = True
        current = self._current_job_id
        if current is None:
            # Idle in `get()`: the cancel propagates straight out of the wait, and
            # every queued item stays in the queue.
            task.cancel()
            await asyncio.wait({task}, timeout=unwind)
        else:
            if grace is None:
                grace = NON_COOPERATIVE_GRACE.get(self._current_job_type or "", STOP_GRACE_SECONDS)
            if not self.cancel_requested(current):
                self._stop_interrupted = current
                self.request_cancel(current)
            # `asyncio.wait`, not `wait_for`: on timeout `wait_for` cancels what it
            # awaits, which would be a second, uncounted cancel of the worker.
            done, _ = await asyncio.wait({task}, timeout=grace)
            if not done:
                logger.warning(
                    "Job %s did not stop within %.0f s of shutdown; cancelling it", current, grace
                )
                task.cancel()
                await asyncio.wait({task}, timeout=unwind)
        if not task.done():
            logger.error(
                "Job queue worker did not exit within %.0f s of being cancelled; abandoning it",
                unwind,
            )

    async def enqueue(
        self,
        job: BackgroundJob,
        fn: Callable[..., Coroutine[Any, Any, None]],
        **kwargs: Any,
    ) -> str:
        await self._queue.put((job, fn, kwargs))
        await broadcaster.emit(job.id, {
            **self._base_event(job),
            "status": "pending",
            "message": "Queued",
        })
        return job.id

    @property
    def current_job_id(self) -> str | None:
        return self._current_job_id

    async def _worker(self) -> None:
        while not self._stopping:
            job, fn, kwargs = await self._queue.get()
            try:
                # Dequeued in the instant `stop()` began: leave it unstarted, and
                # `pending` for the next startup's sweep.
                if self._stopping:
                    break
                await self._run_item(job, fn, kwargs)
            except Exception:
                # `_run_item` reports its own failures; this is the backstop that
                # keeps the only worker alive whatever escapes it (GPT-013).
                logger.exception("Job worker: unhandled error around job %s", job.id)
            finally:
                self._queue.task_done()

    async def _run_item(
        self,
        job: BackgroundJob,
        fn: Callable[..., Coroutine[Any, Any, None]],
        kwargs: dict[str, Any],
    ) -> None:
        # Set before the first await, so `stop()` never sees a dequeued job as idle.
        self._current_job_id = job.id
        self._current_job_type = job.job_type
        try:
            try:
                started = await self._mark_started(job)
            except Exception as e:
                logger.exception("Job %s could not be started", job.id)
                await self._finish(job, "failed", f"Could not start job: {e}")
                return
            if not started:
                # Cancelled while waiting in the queue — skip it. The row already
                # says `cancelled`: the cancel route wrote it.
                await broadcaster.emit(job.id, {
                    **self._base_event(job),
                    "status": "cancelled",
                    "message": "Cancelled before starting.",
                })
                return
            if self._stopping:
                await self._finish(job, "failed", INTERRUPTED_MSG)
                return

            try:
                await broadcaster.emit(job.id, {
                    **self._base_event(job),
                    "status": "running",
                    "done": 0,
                    "total": job.total_items,
                    "percent": 0.0,
                    "message": f"Starting {job.label or job.job_type}...",
                })
                await fn(job_id=job.id, **kwargs)
            except asyncio.CancelledError:
                # A cooperative cancel (`raise_if_cancelled`) leaves the task's
                # cancel count at zero; `stop()`'s forced `task.cancel()` does not.
                # Only the second must propagate: swallowing it sent the worker back
                # to `get()` and wedged shutdown forever (GPT-012, PM-027).
                task = asyncio.current_task()
                hard = task is not None and task.cancelling() > 0
                if job.id == self._stop_interrupted:
                    try:
                        await self._finish(job, "failed", INTERRUPTED_MSG)
                    except Exception:
                        logger.exception("Could not record job %s as interrupted", job.id)
                else:
                    await self._finish(job, "cancelled")
                if hard:
                    raise
            except Exception as e:
                logger.exception("Job %s failed", job.id)
                await self._finish(job, "failed", str(e))
            else:
                await self._finish(job, "completed")
        finally:
            self._current_job_id = None
            self._current_job_type = None
            self._cancel_requested.discard(job.id)

    async def _mark_started(self, job: BackgroundJob) -> bool:
        """Write `running`; False if the job was cancelled while it was queued."""
        async with AsyncSessionLocal() as db:
            job_row = await db.get(BackgroundJob, job.id)
            if job_row and job_row.status == "cancelled":
                return False
            if job_row:
                job_row.status = "running"
                job_row.started_at = datetime.utcnow()
                await db.commit()
        return True

    @staticmethod
    def _base_event(job: BackgroundJob) -> dict[str, Any]:
        return {
            "type": "progress",
            "job_id": job.id,
            "job_type": job.job_type,
            "label": job.label,
            "dataset_id": job.dataset_id,
        }

    @staticmethod
    async def _write_final(job_id: str, status: str, error: str | None) -> int:
        async with AsyncSessionLocal() as db:
            job_row = await db.get(BackgroundJob, job_id)
            if job_row is None:
                return 0
            job_row.status = status
            job_row.finished_at = datetime.utcnow()
            if error is not None:
                job_row.error_msg = error
            await db.commit()
            return job_row.total_items or 0

    async def _finish(self, job: BackgroundJob, status: str, error: str | None = None) -> None:
        """Record a job's terminal status from a fresh session, then emit it.

        A failed write gets one fallback attempt at `failed`, and the event always
        goes out, carrying whichever status was attempted last. `CancelledError` is
        not caught: a forced stop landing here leaves the row `running`, for the next
        startup's `mark_interrupted_jobs` to fail.
        """
        final_total = 0
        try:
            final_total = await self._write_final(job.id, status, error)
        except Exception as e:
            logger.exception("Could not record job %s as %s", job.id, status)
            status = "failed"
            error = f"Job finished but its status could not be recorded: {e}"
            try:
                await self._write_final(job.id, status, error)
            except Exception:
                logger.exception("Could not record job %s as failed either", job.id)
        event: dict[str, Any] = {**self._base_event(job), "status": status}
        if status == "completed":
            event.update({
                "done": final_total,
                "total": final_total,
                "percent": 100.0,
                "message": "Done.",
            })
        elif status == "failed":
            event["message"] = error
        # Through the module attribute every time: tests monkeypatch `emit`.
        await broadcaster.emit(job.id, event)


job_queue = JobQueue()
