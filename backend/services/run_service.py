"""Automated dataset runs (M6 §4): a `Run` row stepped forward on job completion.

Decision 9 is the ruling. Nothing here waits for a job. Starting a run queues its
first stage's child job, tagged with `config["run_id"]` and `config["run_stage"]`.
When that job reaches a terminal state, `JobQueue._after_terminal` calls
`advance_run`, after the job's own commit and outside its outcome: it records the
stage and queues the next one, or ends the run. See docs/dev/comfyui/automation.md.

Rules this module keeps:
- **One lock per run** (`_run_lock`). Start, resume, cancel, pause and `advance_run`
  all take it, so a child the API has just queued cannot finish and step the run
  before the API has committed the run row.
- **A stage with nothing in scope is skipped, never started with `[]`.** Score,
  caption and export read an empty id list as "the whole dataset".
- **JSON columns are replaced, never mutated in place**, or SQLAlchemy skips the
  UPDATE (CLAUDE.md).
- `AsyncSessionLocal` is imported inside each function, because the test harness
  rebinds it.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import BackgroundJob, ComfyPlan, ComfyRow, Run
from backend.schemas.run import STAGE_LABELS, ExportSettings, RunRecipe, RunStartRequest
from backend.utils import chunked, normalize_subfolder
from backend.workers.job_queue import job_queue, run_job_config, run_job_label

logger = logging.getLogger(__name__)

LIVE_STATUSES = ("running", "paused")
FINISHED_STATUSES = ("completed", "failed", "cancelled")
# The stages that talk to ComfyUI, so need its URL and the plan's workflow.
_COMFY_STAGES = ("prompts", "generate", "handoff_to_local")

_locks: dict[str, asyncio.Lock] = {}
_dataset_locks: dict[str, asyncio.Lock] = {}


def _run_lock(run_id: str) -> asyncio.Lock:
    lock = _locks.get(run_id)
    if lock is None:
        lock = _locks[run_id] = asyncio.Lock()
    return lock


def _dataset_lock(dataset_id: str) -> asyncio.Lock:
    """Holds "one live run per dataset" from its check to the commit that makes a run
    live, so two presses (two panes, or a start beside a resume) cannot both pass.
    Taken after a run lock where both are held, never before one."""
    lock = _dataset_locks.get(dataset_id)
    if lock is None:
        lock = _dataset_locks[dataset_id] = asyncio.Lock()
    return lock


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def stage_tag(index: int, key: str) -> str:
    """What a child job's `config["run_stage"]` holds: the stage's position and key.

    The position makes a job from an earlier pass of the same stage stale once the
    run has moved on (and lets §5's loop repeat a stage key)."""
    return f"{index}:{key}"


def _prefix(run: Run, key: str) -> str:
    return f'Run "{run.name}" · {STAGE_LABELS.get(key, key)} — '


class _Outcome:
    """What starting a stage did: queued a job, finished inline, skipped, or paused."""

    def __init__(self, kind: str, *, job_id: str | None = None, note: str | None = None,
                 summary: str | None = None):
        self.kind = kind
        self.job_id = job_id
        self.note = note
        self.summary = summary


# ── Starting ─────────────────────────────────────────────────────────────────

def _export_body(dataset_id: str, s: ExportSettings):
    from backend.routers import export as export_router

    common: dict[str, Any] = {
        "dataset_id": dataset_id,
        "output_dir": s.output_dir,
        "captioned_only": s.captioned_only,
        "exclude_flags": "is_duplicate" if s.exclude_duplicates else "",
    }
    if s.kind == "kohya":
        return export_router.KohyaExportRequest(**common, n_repeats=s.n_repeats, concept_token=s.concept_token)
    if s.kind == "aitoolkit":
        return export_router.AIToolkitExportRequest(**common, concept_name=s.concept_name)
    return export_router.PlainExportRequest(**common)


def _caption_body(dataset_id: str, recipe: RunRecipe, image_ids: list[str]):
    from backend.routers.captioning import CaptionJobRequest

    c = recipe.caption
    assert c is not None
    return CaptionJobRequest(
        dataset_id=dataset_id, image_ids=image_ids, model=c.model, style=c.style,
        custom_prompt=c.custom_prompt, overwrite=c.overwrite,
    )


async def _preflight(db: AsyncSession, plan: ComfyPlan, recipe: RunRecipe) -> None:
    """Every check that can fail cheaply, at the press rather than at stage five."""
    from backend.routers import comfy as comfy_router
    from backend.routers import export as export_router
    from backend.services import blueprint_service

    if any(s in recipe.stages for s in _COMFY_STAGES):
        await comfy_router._run_preflight(db, plan)
    normalize_subfolder(recipe.generate.subfolder)
    if "prompts" in recipe.stages and await blueprint_service.load_blueprint(db, plan.id) is not None:
        if not comfy_router._prompt_alias(plan):
            raise HTTPException(400, comfy_router.NO_PROMPT_PIN_MSG)
    if "caption" in recipe.stages:
        try:
            _caption_body(plan.dataset_id, recipe, ["preflight"])
        except ValidationError as e:
            raise HTTPException(400, f"Caption settings: {e.errors()[0].get('msg', e)}")
    if "export" in recipe.stages:
        assert recipe.export is not None
        export_router.check_export_request(_export_body(plan.dataset_id, recipe.export))


async def start_run(db: AsyncSession, body: RunStartRequest) -> Run:
    plan = await db.get(ComfyPlan, body.plan_id)
    if plan is None:
        raise HTTPException(404, "Plan not found")
    if plan.dataset_id != body.dataset_id:
        raise HTTPException(400, "That plan belongs to another dataset")
    recipe = body.recipe
    await _preflight(db, plan, recipe)

    run = Run(
        dataset_id=body.dataset_id,
        plan_id=plan.id,
        name=(body.name.strip() or f"{plan.name} — {datetime.now():%Y-%m-%d %H:%M}")[:200],
        recipe=recipe.model_dump(),
        stages=[{"key": k, "status": "pending", "job_id": None, "note": None, "summary": None,
                 "started_at": None, "finished_at": None} for k in recipe.stages],
        context={},
        status="running",
        started_at=datetime.utcnow(),
    )
    async with _dataset_lock(body.dataset_id):
        await _refuse_second_live_run(db, body.dataset_id)
        db.add(run)
        await db.commit()
    async with _run_lock(run.id):
        await _start_next(db, run)
    return run


async def _refuse_second_live_run(db: AsyncSession, dataset_id: str, *, except_id: str | None = None) -> None:
    stmt = select(Run.id).where(Run.dataset_id == dataset_id, Run.status.in_(LIVE_STATUSES))
    if except_id is not None:
        stmt = stmt.where(Run.id != except_id)
    if (await db.execute(stmt.limit(1))).first() is not None:
        raise HTTPException(409, "This dataset already has a run that is running or paused — finish or cancel it first")


async def _start_next(db: AsyncSession, run: Run) -> None:
    """Start the first pending stage; skip, finish or pause through to the next job.

    Commits. A stage that raises fails the stage and the run with the reason; it
    never propagates, because this runs inside the worker's post-terminal hook too.
    """
    while True:
        stages = copy.deepcopy(run.stages)
        idx = next((i for i, s in enumerate(stages) if s["status"] == "pending"), None)
        if idx is None:
            run.status = "completed"
            run.current_job_id = None
            run.finished_at = datetime.utcnow()
            await db.commit()
            return
        key = stages[idx]["key"]
        # A stage starting again (after a resume, a restart or a pause) drops what its
        # last attempt said, or a success would still read "· provider timeout".
        stages[idx].update(status="running", started_at=_now(), finished_at=None, job_id=None,
                           note=None, summary=None)
        run.stages = stages
        try:
            outcome = await _begin_stage(db, run, idx, key)
        except Exception as e:
            # A rollback expires every loaded attribute; reload the run before reading
            # it, or the next access lazy-loads on the async session and raises.
            await db.rollback()
            await db.refresh(run)
            reason = e.detail if isinstance(e, HTTPException) else str(e) or type(e).__name__
            if not isinstance(e, HTTPException):
                logger.exception("Run %s could not start its %s stage", run.id, key)
            await _end_stage(db, run, idx, "failed", note=str(reason), run_status="failed",
                             error=f"{STAGE_LABELS.get(key, key)}: {reason}")
            return

        stages = copy.deepcopy(run.stages)
        if outcome.kind == "job":
            stages[idx]["job_id"] = outcome.job_id
            run.stages = stages
            run.current_job_id = outcome.job_id
            await db.commit()
            return
        if outcome.kind == "pause":
            stages[idx].update(status="pending", note=outcome.note, started_at=None)
            run.stages = stages
            run.status = "paused"
            run.current_job_id = None
            await db.commit()
            return
        stages[idx].update(
            status="done" if outcome.kind == "done" else "skipped",
            note=outcome.note, summary=outcome.summary, finished_at=_now(),
        )
        run.stages = stages
        await db.commit()


async def _end_stage(db: AsyncSession, run: Run, idx: int, status: str, *, note: str | None = None,
                     run_status: str | None = None, error: str | None = None) -> None:
    stages = copy.deepcopy(run.stages)
    stages[idx].update(status=status, finished_at=_now())
    if note is not None:
        stages[idx]["note"] = note
    run.stages = stages
    run.current_job_id = None
    if run_status is not None:
        run.status = run_status
        run.error_msg = error
        run.pause_requested = False
        if run_status in FINISHED_STATUSES:
            run.finished_at = datetime.utcnow()
    await db.commit()


async def _begin_stage(db: AsyncSession, run: Run, idx: int, key: str) -> _Outcome:
    plan = await db.get(ComfyPlan, run.plan_id) if run.plan_id else None
    recipe = RunRecipe.model_validate(run.recipe)
    tag = stage_tag(idx, key)
    if key in _COMFY_STAGES and plan is None:
        raise HTTPException(404, "The run's plan was deleted")
    if key == "prompts":
        return await _stage_prompts(db, run, plan)
    if key in ("handoff_to_comfy", "handoff_to_local"):
        return await _stage_handoff(db, run, plan, key, tag)
    if key == "generate":
        return await _stage_generate(db, run, plan, recipe, tag)
    if key in ("score", "caption"):
        return await _stage_images(db, run, recipe, key, tag)
    if key == "export":
        from backend.routers import export as export_router

        assert recipe.export is not None
        res = await export_router.start_export_job(
            db, recipe.export.kind, _export_body(run.dataset_id, recipe.export),
            run_id=run.id, run_stage=tag, label_prefix=_prefix(run, key),
        )
        return _Outcome("job", job_id=res["job_id"])
    raise HTTPException(400, f"Unknown stage {key!r}")


async def _stage_prompts(db: AsyncSession, run: Run, plan: ComfyPlan) -> _Outcome:
    """Top up's fill on the stored blueprint: Apply, reset failed rows, list pending ones."""
    from backend.routers import comfy as comfy_router
    from backend.schemas.comfy_blueprint import BlueprintSpec
    from backend.services import blueprint_service

    bp = await blueprint_service.load_blueprint(db, plan.id)
    if bp is None:
        return _Outcome("skip", note="The plan has no blueprint, so the run generates its pending rows")
    alias = comfy_router._prompt_alias(plan)
    if not alias:
        raise HTTPException(400, comfy_router.NO_PROMPT_PIN_MSG)
    spec = BlueprintSpec.model_validate(bp.spec or {})
    seed = bp.seed
    async with comfy_router._plan_lock(plan.id):
        if await comfy_router._plan_job_active(db, plan.id, comfy_router._ROW_JOB_TYPES):
            raise HTTPException(409, "A run, prompt-generation or rewrite job for this plan is queued or running")
        # No preview to confirm against: the lock holds from Apply's count to its write.
        out = await comfy_router.fill_blueprint(
            db, plan, spec, seed, prompt_alias=alias, confirm_total=None,
            allow_over_cap=False, delete_orphan_pending=False,
        )
    if out["awaiting_rewrite"]:
        n = out["awaiting_rewrite"]
        # An unattended run cannot review which scenarios to rewrite, so it stops here.
        return _Outcome(
            "pause",
            note=f"{n} row{'s' if n != 1 else ''} wait for a rewrite. Run it on the ComfyUI page, then resume.",
        )
    run.context = {**(run.context or {}), "row_ids": out["pending"]}
    n = len(out["pending"])
    return _Outcome(
        "done",
        summary=f"{out['created']} rows added, {out['reset']} reset, {n} to generate",
    )


async def _stage_handoff(db: AsyncSession, run: Run, plan: ComfyPlan | None, key: str, tag: str) -> _Outcome:
    from backend.routers import comfy as comfy_router

    direction = "to_comfy" if key == "handoff_to_comfy" else "to_local"
    comfy_url = None
    if direction == "to_local":
        assert plan is not None
        comfy_url, _ds = await comfy_router._run_preflight(db, plan)
    job = BackgroundJob(
        job_type="run_handoff",
        label=run_job_label(_prefix(run, key), "VRAM handoff"),
        dataset_id=run.dataset_id,
        total_items=1,
        config=run_job_config({"direction": direction}, run.id, tag),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        note = await _handoff(direction, comfy_url)
        from backend.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            row = await session.get(BackgroundJob, job_id)
            if row is not None:
                row.result_data = {"note": note}
                row.done_items = 1
                await session.commit()

    await job_queue.enqueue(job, _run)
    return _Outcome("job", job_id=job.id)


async def _handoff(direction: str, comfy_url: str | None) -> str:
    """Free the GPU for the other side. Best effort: a failure is a note, never an error."""
    if direction == "to_comfy":
        from backend.ml.model_manager import model_manager

        # `evict_all` returns every registered id, resident or not, so count the
        # resident ones first to report what was really freed.
        resident = [m["id"] for m in model_manager.list_models() if m.get("loaded")]
        try:
            await model_manager.evict_all()
        except Exception as e:
            # `evict_all` ends with a torch cache flush, which raises on a torch-free
            # install. Nothing torch-backed was loaded there, so nothing is lost.
            logger.info("evict_all during a run's handoff: %s", e)
        leased = model_manager.leased_ids()
        freed = [m for m in resident if m not in leased]
        note = f"Unloaded {len(freed)} local model{'s' if len(freed) != 1 else ''}"
        if leased:
            note += f"; still in use: {', '.join(leased)}"
        return note
    from backend.routers import comfy as comfy_router

    try:
        # Through the router module's name, which the tests patch onto the fake.
        await comfy_router.ComfyClient(comfy_url or "").free()
        return "Asked ComfyUI to unload its models"
    except Exception as e:
        logger.warning("ComfyUI /free failed during a run's handoff: %s", e)
        return f"ComfyUI did not free its models ({type(e).__name__}); continuing"


async def _stage_generate(db: AsyncSession, run: Run, plan: ComfyPlan, recipe: RunRecipe, tag: str) -> _Outcome:
    """Run this run's rows that are not generated yet.

    The rows are the `prompts` stage's pending ones, or with no blueprint the plan's
    pending rows when the stage first starts. On a resume, rows a failed or
    interrupted pass left `failed` or `running` go back to pending; completed rows
    are never run twice.
    """
    from backend.routers import comfy as comfy_router

    async with comfy_router._plan_lock(plan.id):
        if await comfy_router._plan_job_active(db, plan.id, ("comfy_generate", "comfy_rewrite")):
            raise HTTPException(409, "A generation run or prompt rewrite for this plan is queued or running")
        row_ids = (run.context or {}).get("row_ids")
        if row_ids is None:
            row_ids = list((await db.execute(
                select(ComfyRow.id)
                .where(ComfyRow.plan_id == plan.id, ComfyRow.status == "pending")
                .order_by(ComfyRow.sort_order, ComfyRow.created_at)
            )).scalars())
            run.context = {**(run.context or {}), "row_ids": row_ids}
        to_run: list[tuple[int, Any, str]] = []
        for chunk in chunked(row_ids):
            rows = (await db.execute(
                select(ComfyRow).where(
                    ComfyRow.plan_id == plan.id,
                    ComfyRow.id.in_(chunk),
                    ComfyRow.status.in_(("pending", "failed", "running")),
                )
            )).scalars().all()
            for r in rows:
                if r.status != "pending":
                    r.status = "pending"
                    r.error_msg = None
                    r.prompt_id = None
                to_run.append((r.sort_order, r.created_at, r.id))
        if not to_run:
            await db.commit()
            return _Outcome("skip", note="No rows to generate")
        ids = [rid for *_, rid in sorted(to_run)]
        res = await comfy_router._start_run(
            db,
            comfy_router.RunRequest(
                plan_id=plan.id, row_ids=ids, subfolder=recipe.generate.subfolder,
                set_caption=recipe.generate.set_caption,
            ),
            hold_lock=False, run_id=run.id, run_stage=tag, label_prefix=_prefix(run, "generate"),
        )
    return _Outcome("job", job_id=res["job_id"])


async def run_image_ids(db: AsyncSession, row_ids: list[str]) -> list[str]:
    """The images this run's completed rows produced, in row order, without repeats."""
    by_row: dict[str, list[str]] = {}
    for chunk in chunked(row_ids):
        for rid, image_ids in (await db.execute(
            select(ComfyRow.id, ComfyRow.image_ids).where(ComfyRow.id.in_(chunk), ComfyRow.status == "completed")
        )).all():
            by_row[rid] = list(image_ids or [])
    seen: set[str] = set()
    out: list[str] = []
    for rid in row_ids:
        for iid in by_row.get(rid, []):
            if iid not in seen:
                seen.add(iid)
                out.append(iid)
    return out


async def _stage_images(db: AsyncSession, run: Run, recipe: RunRecipe, key: str, tag: str) -> _Outcome:
    ids = await run_image_ids(db, (run.context or {}).get("row_ids") or [])
    # Never `[]`: score and caption read an empty list as the whole dataset.
    if not ids:
        return _Outcome("skip", note="This run produced no images to " + ("score" if key == "score" else "caption"))
    if key == "score":
        from backend.routers.quality import ScoreRequest, start_score_job

        res = await start_score_job(
            db,
            ScoreRequest(
                dataset_id=run.dataset_id, image_ids=ids, run_technical=True,
                run_aesthetic=recipe.score.run_aesthetic, aesthetic_model=recipe.score.aesthetic_model,
            ),
            run_id=run.id, run_stage=tag, label_prefix=_prefix(run, key),
        )
    else:
        from backend.routers.captioning import start_caption_job

        res = await start_caption_job(
            db, _caption_body(run.dataset_id, recipe, ids),
            run_id=run.id, run_stage=tag, label_prefix=_prefix(run, key),
        )
    if res.get("job_id") is None:
        return _Outcome("skip", note=res.get("message") or "Nothing in scope")
    return _Outcome("job", job_id=res["job_id"])


# ── Stepping ─────────────────────────────────────────────────────────────────

def _summary(job: BackgroundJob) -> str | None:
    rd = job.result_data or {}
    if job.job_type == "comfy_generate":
        done, failed = rd.get("completed", 0), rd.get("failed", 0)
        return f"{done} row{'s' if done != 1 else ''} generated" + (f", {failed} failed" if failed else "")
    if job.job_type == "quality_score":
        n = job.total_items or 0
        return f"{n} image{'s' if n != 1 else ''} scored"
    if job.job_type == "caption":
        failed = rd.get("failed_count", 0)
        n = (job.total_items or 0) - failed
        return f"{n} image{'s' if n != 1 else ''} captioned" + (f", {failed} failed" if failed else "")
    if job.job_type == "export":
        n = rd.get("exported", 0)
        return f"{n} image{'s' if n != 1 else ''} exported to {rd.get('output_dir', '')}".rstrip()
    if job.job_type == "run_handoff":
        return rd.get("note")
    return None


async def advance_run(run_id: str, job_id: str) -> None:
    """Called by the worker after a tagged child job's terminal commit. Records the
    stage and starts the next one, or ends the run.

    Acts only when the job belongs to the stage now `running` (the stale guard), so
    a job the run has already moved past — cancelled, then finishing late — changes
    nothing. The caller logs and swallows anything this raises.
    """
    from backend.database import AsyncSessionLocal

    async with _run_lock(run_id):
        async with AsyncSessionLocal() as db:
            run = await db.get(Run, run_id)
            job = await db.get(BackgroundJob, job_id)
            if run is None or job is None or run.status != "running":
                return
            tag = (job.config or {}).get("run_stage")
            idx = next(
                (i for i, s in enumerate(run.stages)
                 if s["status"] == "running" and stage_tag(i, s["key"]) == tag),
                None,
            )
            if idx is None:
                return
            key = run.stages[idx]["key"]
            stages = copy.deepcopy(run.stages)
            stages[idx]["summary"] = _summary(job)
            run.stages = stages
            label = STAGE_LABELS.get(key, key)
            if job.status == "completed":
                await _end_stage(db, run, idx, "done")
            elif job.status == "cancelled":
                await _end_stage(db, run, idx, "cancelled", run_status="failed",
                                 error=f"{label}: its job was cancelled outside the run")
                return
            else:
                await _end_stage(db, run, idx, "failed", note=job.error_msg, run_status="failed",
                                 error=f"{label}: {job.error_msg or 'the job failed'}")
                return
            # A pause asked for during the last stage has nothing to stop before, so the
            # run completes rather than waiting paused with no stage left.
            if run.pause_requested and any(s["status"] == "pending" for s in run.stages):
                run.status = "paused"
                run.pause_requested = False
                await db.commit()
                return
            run.pause_requested = False
            await _start_next(db, run)


# ── Controls ─────────────────────────────────────────────────────────────────

async def _get(db: AsyncSession, run_id: str) -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


async def pause_run(db: AsyncSession, run_id: str) -> Run:
    """Stop at the next stage boundary; the running child finishes."""
    async with _run_lock(run_id):
        run = await _get(db, run_id)
        if run.status != "running":
            raise HTTPException(409, f"Only a running run can be paused (this one is {run.status})")
        run.pause_requested = True
        await db.commit()
        return run


async def resume_run(db: AsyncSession, run_id: str) -> Run:
    """Go on from the first stage not done. Always explicit, never automatic."""
    async with _run_lock(run_id):
        run = await _get(db, run_id)
        if run.status == "running":
            # A pause asked for but not yet reached: take it back.
            run.pause_requested = False
            await db.commit()
            return run
        if run.status not in ("paused", "failed"):
            raise HTTPException(409, f"A {run.status} run cannot be resumed")
        async with _dataset_lock(run.dataset_id):
            await _refuse_second_live_run(db, run.dataset_id, except_id=run.id)
            stages = copy.deepcopy(run.stages)
            for s in stages:
                if s["status"] in ("running", "failed", "cancelled"):
                    s.update(status="pending", job_id=None, note=None, summary=None,
                             started_at=None, finished_at=None)
            run.stages = stages
            run.status = "running"
            run.pause_requested = False
            run.error_msg = None
            run.finished_at = None
            await db.commit()
        await _start_next(db, run)
        return run


async def cancel_run(db: AsyncSession, run_id: str) -> Run:
    """Mark the run cancelled, then cancel its current child the way `DELETE /jobs` does."""
    async with _run_lock(run_id):
        run = await _get(db, run_id)
        if run.status not in LIVE_STATUSES:
            raise HTTPException(409, f"A {run.status} run cannot be cancelled")
        child_id = run.current_job_id
        stages = copy.deepcopy(run.stages)
        for s in stages:
            if s["status"] == "running":
                s.update(status="cancelled", finished_at=_now())
        run.stages = stages
        run.status = "cancelled"
        run.pause_requested = False
        run.current_job_id = None
        run.finished_at = datetime.utcnow()
        await db.commit()
        if child_id:
            job = await db.get(BackgroundJob, child_id)
            if job is not None and job.status in ("pending", "running"):
                if job.status == "pending":
                    job.status = "cancelled"
                    await db.commit()
                job_queue.request_cancel(child_id)
        return run


async def delete_run(db: AsyncSession, run_id: str) -> None:
    async with _run_lock(run_id):
        run = await _get(db, run_id)
        if run.status in LIVE_STATUSES:
            raise HTTPException(409, "Cancel the run before deleting it")
        await db.delete(run)
        await db.commit()
    _locks.pop(run_id, None)


async def reconcile_interrupted_runs() -> int:
    """At startup, right after `mark_interrupted_jobs`: every run left `running` is set
    `paused` for an explicit resume.

    Its running stage goes back to pending — **unless that stage's child completed**. A
    child that ends inside `stop()`'s grace period commits `completed`, but its hook is
    skipped while the queue is stopping, so the run never heard. Resetting that stage
    would make a resume export again, or caption again on a paid provider. It is
    recorded `done` with its summary instead; with no stage left, the run is completed.
    """
    from backend.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        runs = (await db.execute(select(Run).where(Run.status == "running"))).scalars().all()
        for run in runs:
            stages = copy.deepcopy(run.stages)
            for s in stages:
                if s["status"] != "running":
                    continue
                child = await db.get(BackgroundJob, s["job_id"]) if s.get("job_id") else None
                if child is not None and child.status == "completed":
                    s.update(status="done", summary=_summary(child), finished_at=_now())
                else:
                    s.update(status="pending", job_id=None, started_at=None, summary=None,
                             note="Crucible restarted during this stage. Resume to run it again.")
            run.stages = stages
            run.pause_requested = False
            run.current_job_id = None
            if any(s["status"] == "pending" for s in stages):
                run.status = "paused"
            else:
                run.status = "completed"
                run.finished_at = datetime.utcnow()
        await db.commit()
        return len(runs)
