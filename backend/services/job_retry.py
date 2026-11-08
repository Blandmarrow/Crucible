"""Retry a stopped background job by resubmitting its stored config (M9 stage 8).

A job type joins `_HANDLERS` only once its stored `BackgroundJob.config` has been checked to
rebuild its request completely. `quality_score` stores `body.model_dump()`, so it was first.
There is no checkpoint: since M9 stage 3 a scoring job commits chunk by chunk, so the rows
themselves record what is done, and a retry that turns on `only_unscored` resumes from them
(docs/dev/backend-infrastructure.md § Retrying a stopped job).
"""

from collections.abc import Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import BackgroundJob, Dataset

# Interrupted is stored as `failed` with `job_queue.INTERRUPTED_MSG`, so `failed` covers it.
RETRYABLE_STATUSES = ("failed", "cancelled")

# Keys a retry must not carry forward: the run tags (a run-owned job is refused anyway) and
# the previous retry's back-link, which the new job replaces with its own.
_DROPPED_KEYS = ("run_id", "run_stage", "retry_of")


class RetryRefused(Exception):
    """A retry that cannot go ahead. The router maps it to an HTTP status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def _retry_quality_score(db: AsyncSession, job: BackgroundJob) -> dict:
    # Imported here: a service must not import a router at module load.
    from backend.routers.quality import ScoreRequest, start_score_job

    config = {k: v for k, v in (job.config or {}).items() if k not in _DROPPED_KEYS}
    try:
        body = ScoreRequest.model_validate(config)
    except ValidationError as exc:
        raise RetryRefused(409, "This job's settings can no longer be replayed.") from exc
    if await db.get(Dataset, body.dataset_id) is None:
        raise RetryRefused(404, "This job's dataset no longer exists.")
    if not body.only_mismatched:
        # Resume rather than redo (the user's ruling, 2026-11-08). `only_mismatched` cannot
        # take `only_unscored` (a 422), and needs no help: rows the first run finished now
        # match the model, so a re-run skips them anyway.
        body = body.model_copy(update={"only_unscored": True})
    return await start_score_job(
        db, body, label_prefix="Retry: ", extra_config={"retry_of": job.id}
    )


_HANDLERS: dict[str, Callable[[AsyncSession, BackgroundJob], Awaitable[dict]]] = {
    "quality_score": _retry_quality_score,
}


def _refusal(job: BackgroundJob) -> RetryRefused | None:
    if job.status not in RETRYABLE_STATUSES:
        return RetryRefused(
            409, f"Only a failed or cancelled job can be retried; this one is {job.status}."
        )
    if job.job_type not in _HANDLERS:
        return RetryRefused(409, f"Retry is not available for {job.job_type} jobs.")
    if (job.config or {}).get("run_id"):
        # The run's own Resume is the way back, so a retry never steps a run twice.
        return RetryRefused(409, "This job belongs to an automated run; resume the run instead.")
    if job.dataset_id is None:
        # Every type in `_HANDLERS` is dataset-scoped, and the FK is `SET NULL`, so a
        # NULL here means the dataset was deleted: hide the button rather than offer a
        # 404. The handler still checks, for a delete that lands after the list loads.
        return RetryRefused(404, "This job's dataset no longer exists.")
    return None


def is_retryable(job: BackgroundJob) -> bool:
    """Whether the Logs page shows Retry. Pure: no DB read, so the list stays one query.

    The one condition it cannot see, a retry of this job still queued or running, is
    refused by `retry_job` instead.
    """
    return _refusal(job) is None


async def retry_job(db: AsyncSession, job: BackgroundJob) -> dict:
    """Resubmit `job`. Answers what the job type's start function answers:
    `{"job_id", "total"}`, or `{"job_id": None, "message"}` when nothing is left.

    The in-flight check and the new job's commit are two steps, so two POSTs at the same
    moment can both pass it. That is accepted rather than locked: the worker is serial,
    and under `only_unscored` each chunk re-reads its filter, so the second job finds
    nothing left to visit.
    """
    refusal = _refusal(job)
    if refusal:
        raise refusal
    in_flight = await db.scalar(
        select(BackgroundJob.id)
        .where(
            func.json_extract(BackgroundJob.config, "$.retry_of") == job.id,
            BackgroundJob.status.in_(("pending", "running")),
        )
        .limit(1)
    )
    if in_flight:
        raise RetryRefused(409, "A retry of this job is already queued or running.")
    return await _HANDLERS[job.job_type](db, job)
