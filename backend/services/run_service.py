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
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import BackgroundJob, ComfyPlan, ComfyRow, Image, Run
from backend.schemas.run import (
    BODY_STAGES, STAGE_LABELS, STAGE_ORDER, ExportSettings, RunRecipe, RunStartRequest,
)
from backend.utils import chunked, normalize_subfolder
from backend.workers.job_queue import job_queue, run_job_config, run_job_label

logger = logging.getLogger(__name__)

LIVE_STATUSES = ("running", "paused")
FINISHED_STATUSES = ("completed", "failed", "cancelled")
# The stages that talk to ComfyUI, so need its URL and the plan's workflow.
_COMFY_STAGES = ("prompts", "generate", "handoff_to_local")
# Workflow inputs a repeating run needs random, or every pass repeats the last one's
# pictures and the duplicate scan rejects them all (M6 §5).
SEED_INPUTS = ("seed", "noise_seed")

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


def _entry(key: str, pass_no: int | None) -> dict:
    return {"key": key, "status": "pending", "job_id": None, "note": None, "summary": None,
            "started_at": None, "finished_at": None, "pass_no": pass_no}


def _initial_stages(recipe: RunRecipe) -> list[dict]:
    """The recipe's stages in `STAGE_ORDER`, as pass 1, plus `next_pass` when it loops."""
    keys = [k for k in STAGE_ORDER if k in recipe.stages or (k == "next_pass" and recipe.loop is not None)]
    return [_entry(k, 1 if k in BODY_STAGES else None) for k in keys]


def _pass_of(run: Run, idx: int) -> int:
    return run.stages[idx].get("pass_no") or 1


def _pass_rows(run: Run, pass_no: int) -> list[str] | None:
    """The plan rows a pass generates, or None before its `prompts` (or `generate`) has
    chosen them. A run started before §5 kept one flat `row_ids`, which is its pass 1."""
    ctx = run.context or {}
    passes = ctx.get("passes")
    if passes is None:
        return ctx.get("row_ids") if pass_no == 1 else None
    return passes[pass_no - 1] if len(passes) >= pass_no else None


def _set_pass_rows(run: Run, pass_no: int, row_ids: list[str]) -> None:
    """Replace (never append) the pass's rows, so a resumed `prompts` writes its slot
    again, and keep `row_ids` the union, which the run's row count and caption read."""
    ctx = run.context or {}
    passes = [list(p) for p in (ctx.get("passes") or [])]
    while len(passes) < pass_no:
        passes.append([])
    passes[pass_no - 1] = list(row_ids)
    union = list(dict.fromkeys(i for p in passes for i in p))
    run.context = {**ctx, "passes": passes, "row_ids": union}


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
        "output_format": s.output_format,
        "jpeg_quality": s.jpeg_quality,
        "resize_to": None if s.captions_only else s.resize_to,
        "aesthetic_min": s.aesthetic_min,
        "captioned_only": s.captioned_only,
        "exclude_flags": ",".join(s.exclude_flags),
        "style_sim_min": s.style_sim_min,
        "strip_metadata": s.strip_metadata and not s.captions_only,
        "captions_only": s.captions_only,
    }
    if s.kind == "kohya":
        return export_router.KohyaExportRequest(
            **common, caption_format=s.caption_format, n_repeats=s.n_repeats, concept_token=s.concept_token,
        )
    if s.kind == "aitoolkit":
        return export_router.AIToolkitExportRequest(
            **common, caption_format=s.caption_format, concept_name=s.concept_name,
        )
    return export_router.PlainExportRequest(**common)


def _caption_body(dataset_id: str, recipe: RunRecipe, image_ids: list[str]):
    from backend.routers.captioning import CaptionJobRequest

    c = recipe.caption
    assert c is not None
    # Field for field: CaptionSettings is named after CaptionJobRequest, so its
    # validators (the model id, the flag names) judge the run's settings too.
    return CaptionJobRequest(dataset_id=dataset_id, image_ids=image_ids, **c.model_dump())


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
    if "rewrite" in recipe.stages:
        await _preflight_rewrite(db, plan, recipe)
    if recipe.loop is not None:
        if await blueprint_service.load_blueprint(db, plan.id) is None:
            raise HTTPException(400, "Repeating fills the plan's blueprint, and this plan has none")
        check_seeds(plan)
        await _preflight_paid_cap(db, recipe)


async def _preflight_rewrite(db: AsyncSession, plan: ComfyPlan, recipe: RunRecipe) -> None:
    from backend.models.openai_provider import OpenAIProvider
    from backend.schemas.comfy_blueprint import BlueprintSpec
    from backend.services import blueprint_service
    from backend.services.blueprint_rewrite import rewrite_scenarios

    rw = recipe.rewrite
    assert rw is not None
    provider = await db.get(OpenAIProvider, rw.provider_id)
    if provider is None:
        raise HTTPException(404, "The rewrite provider was not found")
    if not (rw.model_name.strip() or provider.default_model):
        raise HTTPException(400, "No rewrite model given and the provider has no default model")
    bp = await blueprint_service.load_blueprint(db, plan.id)
    on = rewrite_scenarios(BlueprintSpec.model_validate(bp.spec or {})) if bp else set()
    off = sorted(set(rw.scenarios) - on)
    if off:
        raise HTTPException(400, f"Rewrite is not on for {', '.join(off)} in the saved blueprint")


async def _preflight_paid_cap(db: AsyncSession, recipe: RunRecipe) -> None:
    """A billed provider needs a paid-call cap, and when captioning is billed the cap
    must cover captioning every image the loop may make (that reserves the tail)."""
    loop = recipe.loop
    assert loop is not None
    rewrite_billed = "rewrite" in recipe.stages and await _rewrite_billed(db, recipe)
    caption_billed = "caption" in recipe.stages and await _caption_billed(db, recipe)
    if not (rewrite_billed or caption_billed):
        return
    cap = loop.max_paid_calls
    if cap is None:
        raise HTTPException(400, "A billed LLM provider needs a paid-call cap")
    if caption_billed and cap < loop.max_images:
        raise HTTPException(
            400, f"The paid-call cap ({cap}) must cover captioning up to {loop.max_images} images"
        )
    if rewrite_billed and caption_billed and cap <= loop.max_images:
        raise HTTPException(
            400, f"Captioning {loop.max_images} images takes the whole paid-call cap ({cap}), "
            "leaving none for rewriting",
        )


def check_seeds(plan: ComfyPlan) -> None:
    """Refuse a repeating run whose workflow seed is not pinned random.

    Every input named `seed` or `noise_seed` that holds a value (not a link) must have
    a pin with `int_mode: random`. An unpinned seed keeps the template's value on every
    row, and `increment` restarts at its base each run, so a later pass would repeat the
    earlier one's pictures and the duplicate scan would reject them all.
    """
    pins = {(str(p.get("node_id")), p.get("input")): p for p in (plan.pinned_params or [])}
    bad: list[str] = []
    for node_id, node in (plan.workflow_json or {}).items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        for name in SEED_INPUTS:
            if name not in inputs or isinstance(inputs[name], list):
                continue
            pin = pins.get((str(node_id), name))
            if pin is None or pin.get("int_mode") != "random":
                bad.append(f"{node.get('class_type') or 'node'} #{node_id} ({name})")
    if bad:
        raise HTTPException(
            400, "A repeating run needs a random seed, or each pass repeats the last one's images. "
            f"Pin these with Random: {', '.join(bad)}",
        )


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
        stages=_initial_stages(recipe),
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
            if outcome.note:
                stages[idx]["note"] = outcome.note
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
    if key in ("rewrite", "gate", "next_pass") and plan is None:
        raise HTTPException(404, "The run's plan was deleted")
    if key == "prompts":
        return await _stage_prompts(db, run, plan, recipe, idx)
    if key == "rewrite":
        return await _stage_rewrite(db, run, plan, recipe, idx, tag)
    if key in ("handoff_to_comfy", "handoff_to_local"):
        return await _stage_handoff(db, run, plan, key, tag)
    if key == "free_rewrite_model":
        if recipe.rewrite is None:
            return _Outcome("skip", note="No rewrite stage to unload after")
        rw = recipe.rewrite
        return await _stage_handoff(
            db, run, plan, key, tag, llm_model=f"openai_compat:{rw.provider_id}:{rw.model_name.strip()}",
        )
    if key == "free_caption_model":
        if recipe.caption is None:
            return _Outcome("skip", note="No caption stage to unload after")
        return await _stage_handoff(db, run, plan, key, tag, llm_model=recipe.caption.model)
    if key == "generate":
        return await _stage_generate(db, run, plan, recipe, idx, tag)
    if key in ("score", "caption"):
        return await _stage_images(db, run, recipe, key, idx, tag)
    if key == "gate":
        return await _stage_gate(db, run, recipe, idx, tag)
    if key == "next_pass":
        return await _stage_next_pass(db, run, plan, recipe, idx)
    if key == "export":
        from backend.routers import export as export_router

        assert recipe.export is not None
        settings = recipe.export
        if "gate" in recipe.stages and "gate_rejected" not in settings.exclude_flags:
            # A form filled from the Export page can carry a saved filter older than the
            # flag; what this run's own gate rejected never goes out.
            settings = settings.model_copy(update={"exclude_flags": [*settings.exclude_flags, "gate_rejected"]})
        res = await export_router.start_export_job(
            db, settings.kind, _export_body(run.dataset_id, settings),
            run_id=run.id, run_stage=tag, label_prefix=_prefix(run, key),
        )
        return _Outcome("job", job_id=res["job_id"])
    raise HTTPException(400, f"Unknown stage {key!r}")


async def _stage_prompts(db: AsyncSession, run: Run, plan: ComfyPlan, recipe: RunRecipe, idx: int) -> _Outcome:
    """Top up's fill on the stored blueprint: Apply, reset failed rows, list pending ones.

    In a loop, leaves the run abandoned get nothing, the scenarios its rewrite stage
    handles do not pause it, pass 1 records each leaf's starting kept count, and the
    pass's rows are clamped to the image budget left.
    """
    from backend.routers import comfy as comfy_router
    from backend.schemas.comfy_blueprint import BlueprintSpec
    from backend.services import blueprint_service

    p = _pass_of(run, idx)
    bp = await blueprint_service.load_blueprint(db, plan.id)
    if bp is None:
        return _Outcome("skip", note="The plan has no blueprint, so the run generates its pending rows")
    alias = comfy_router._prompt_alias(plan)
    if not alias:
        raise HTTPException(400, comfy_router.NO_PROMPT_PIN_MSG)
    spec = BlueprintSpec.model_validate(bp.spec or {})
    seed = bp.seed
    leaves = (run.context or {}).get("leaves") or {}
    # Folders the run gave up on get nothing more, and neither do folders already full:
    # rows a clamp held back would otherwise run past their target in a later pass.
    excluded = frozenset(k for k, v in leaves.items() if v.get("state") in ("abandoned", "met"))
    run_rewrites = (
        frozenset(recipe.rewrite.scenarios) if recipe.rewrite and "rewrite" in recipe.stages else frozenset()
    )
    async with comfy_router._plan_lock(plan.id):
        if await comfy_router._plan_job_active(db, plan.id, comfy_router._ROW_JOB_TYPES):
            raise HTTPException(409, "A run, prompt-generation or rewrite job for this plan is queued or running")
        # No preview to confirm against: the lock holds from Apply's count to its write.
        # From pass 2 a looping fill may plan past the default row cap: the yield floor
        # can ask for 4x a folder's shortfall, and the image cap's clamp below bounds
        # what runs. Pass 1 keeps the cap a manual Apply has (the user's ruling,
        # 2026-11-04): it is the fill Preview showed, so a blueprint too big for that
        # is a mistake to refuse at the start, not growth the run made itself.
        out = await comfy_router.fill_blueprint(
            db, plan, spec, seed, prompt_alias=alias, confirm_total=None,
            allow_over_cap=recipe.loop is not None and p > 1, delete_orphan_pending=False,
            exclude_leaf_ids=excluded, run_rewrites=run_rewrites,
        )
    if out["awaiting_rewrite"]:
        n = out["awaiting_rewrite"]
        # An unattended run cannot review which scenarios to rewrite, so it stops here.
        return _Outcome(
            "pause",
            note=f"{n} row{'s' if n != 1 else ''} wait for a rewrite. Run it on the ComfyUI page, then resume.",
        )
    pending = out["pending"]
    clamped_from = None
    if recipe.loop is not None:
        if p == 1 and not leaves:
            progress = await blueprint_service.leaf_progress(db, plan)
            run.context = {**(run.context or {}), "leaves": {
                lid: {"path": v["path"], "target": v["target"], "kept": [v["kept"]],
                      "state": "met" if v["kept"] >= v["target"] else "open", "reason": None, "stalls": 0}
                for lid, v in progress.items()
            }}
        stop = await _budget_stop(db, run, recipe)
        if stop:
            _set_pass_rows(run, p, [])
            return _stop_loop(run, idx, stop)
        spent = await _spent(db, run, recipe)
        kept_rows = await _clamp_rows(db, plan, pending, recipe.loop.max_images - spent.images)
        if len(kept_rows) < len(pending):
            clamped_from = len(pending)
        pending = kept_rows
    _set_pass_rows(run, p, pending)
    n = len(pending)
    summary = f"{out['created']} rows added, {out['reset']} reset, {n} to generate"
    if clamped_from is not None:
        summary += f" (the image cap leaves room for {n} of {clamped_from})"
    return _Outcome("done", summary=summary)


async def _images_per_row(db: AsyncSession, plan: ComfyPlan) -> float:
    """Images a completed blueprint row of this plan produced, on average (1 if none)."""
    done = (await db.execute(
        select(ComfyRow.image_ids).where(
            ComfyRow.plan_id == plan.id, ComfyRow.status == "completed", ComfyRow.leaf_id.is_not(None),
        )
    )).scalars().all()
    produced = sum(len(i or []) for i in done)
    return produced / len(done) if done and produced else 1.0


async def _clamp_rows(db: AsyncSession, plan: ComfyPlan, pending: list[str], images_left: int) -> list[str]:
    """At most enough rows to make `images_left` images, taken round-robin across leaves so
    no folder is starved while another takes the whole budget. Queue order is kept. Rows
    left out stay pending, count as future images in the next fill, and come back then."""
    if not pending:
        return pending
    max_rows = max(1, math.ceil(images_left / await _images_per_row(db, plan)))
    if len(pending) <= max_rows:
        return pending
    leaf_of: dict[str, str | None] = {}
    for chunk in chunked(pending):
        leaf_of.update((await db.execute(
            select(ComfyRow.id, ComfyRow.leaf_id).where(ComfyRow.id.in_(chunk))
        )).all())
    groups: dict[str | None, list[str]] = {}
    for rid in pending:
        groups.setdefault(leaf_of.get(rid), []).append(rid)
    chosen: set[str] = set()
    queues = [list(g) for g in groups.values()]
    while len(chosen) < max_rows:
        for q in queues:
            if q and len(chosen) < max_rows:
                chosen.add(q.pop(0))
        queues = [q for q in queues if q]
        if not queues:
            break
    return [rid for rid in pending if rid in chosen]


async def _stage_rewrite(
    db: AsyncSession, run: Run, plan: ComfyPlan, recipe: RunRecipe, idx: int, tag: str,
) -> _Outcome:
    """The §3 rewrite, over exactly this pass's rows, in the scenarios the start form
    reviewed. Capped by the paid-call cap when the provider is billed."""
    from backend.models.openai_provider import OpenAIProvider
    from backend.routers import comfy as comfy_router

    rw = recipe.rewrite
    assert rw is not None
    rows = _pass_rows(run, _pass_of(run, idx)) or []
    if not rows:
        return _Outcome("skip", note="This pass has no rows to rewrite")
    max_requests = None
    if recipe.loop is not None:
        stop = await _budget_stop(db, run, recipe)
        if stop:
            return _stop_loop(run, idx, stop)
        if recipe.loop.max_paid_calls is not None and await _rewrite_billed(db, recipe):
            spent = await _spent(db, run, recipe)
            reserve = recipe.loop.max_images if await _caption_billed(db, recipe) else 0
            max_requests = recipe.loop.max_paid_calls - spent.paid_calls - reserve
            if max_requests <= 0:
                return _stop_loop(run, idx, "the paid-call cap is spent")
    if await db.get(OpenAIProvider, rw.provider_id) is None:
        raise HTTPException(404, "The rewrite provider was deleted")
    async with comfy_router._plan_lock(plan.id):
        if await comfy_router._plan_job_active(db, plan.id, comfy_router._ROW_JOB_TYPES):
            raise HTTPException(409, "A run, prompt-generation or rewrite job for this plan is queued or running")
        res = await comfy_router.start_rewrite_job(
            db, plan, provider_id=rw.provider_id, model_name=rw.model_name, batch_size=rw.batch_size,
            temperature=rw.temperature, scenarios=list(rw.scenarios), row_ids=rows,
            max_requests=max_requests, run_id=run.id, run_stage=tag, label_prefix=_prefix(run, "rewrite"),
            sit_out_failures=recipe.loop is not None,
        )
    if res["job_id"] is None:
        return _Outcome("skip", note="No row of this pass waits for a rewrite")
    return _Outcome("job", job_id=res["job_id"])


async def _stage_handoff(
    db: AsyncSession, run: Run, plan: ComfyPlan | None, key: str, tag: str, *, llm_model: str | None = None,
) -> _Outcome:
    from backend.routers import comfy as comfy_router

    direction = {
        "handoff_to_comfy": "to_comfy", "handoff_to_local": "to_local", "free_rewrite_model": "after_rewrite",
    }.get(key, "after_caption")
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
        if direction in ("after_caption", "after_rewrite"):
            note = await _free_llm(llm_model or "")
        else:
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


# The transport every LLM-server call here goes through; tests set a MockTransport.
_LLM_TRANSPORT: httpx.AsyncBaseTransport | None = None


async def _free_llm(model: str) -> str:
    """After the caption or rewrite stage: unload the model it used. Best effort — any
    failure is a note, never a failed run.

    - An OpenAI-compatible provider on this machine: LM Studio's native API (0.4 and
      later), `GET /api/v1/models` for the loaded instances, then
      `POST /api/v1/models/unload` with each one's `instance_id`. A provider without
      that API answers 404, and the note says so. A remote provider is left alone:
      its models are not on this GPU.
    - Ollama: a request with `keep_alive: 0`, its own way to unload now.
    - A local captioner (Florence-2, JoyCaption, PaliGemma, WD14): `evict_all`.
    """
    if model.startswith("openai_compat:"):
        parts = model.split(":", 2)
        provider_id = parts[1] if len(parts) > 1 else ""
        name = parts[2] if len(parts) > 2 else ""
        from backend.database import AsyncSessionLocal
        from backend.models.openai_provider import OpenAIProvider
        from backend.schemas.openai_provider import _is_remote

        async with AsyncSessionLocal() as db:
            provider = await db.get(OpenAIProvider, provider_id)
        if provider is None:
            return "The provider was deleted; nothing to unload"
        if _is_remote(provider.base_url):
            return f"{provider.name} is a remote provider; its models are not on this GPU"
        return await _unload_lmstudio(provider.base_url, provider.api_key, name or provider.default_model)
    if model.startswith("ollama:"):
        from backend.config import settings

        name = model.removeprefix("ollama:")
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=_LLM_TRANSPORT) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/generate", json={"model": name, "keep_alive": 0}
                )
                resp.raise_for_status()
            return f"Unloaded {name} from Ollama"
        except Exception as e:
            logger.warning("Ollama unload during a run failed: %s", e)
            return f"Ollama did not unload {name} ({type(e).__name__}); continuing"
    from backend.ml.model_manager import model_manager

    resident = [m["id"] for m in model_manager.list_models() if m.get("loaded")]
    try:
        await model_manager.evict_all()
    except Exception as e:
        logger.info("evict_all after a run's LLM stage: %s", e)
    freed = [m for m in resident if m not in model_manager.leased_ids()]
    return f"Unloaded {len(freed)} local model{'s' if len(freed) != 1 else ''}"


async def _unload_lmstudio(base_url: str, api_key: str, name: str) -> str:
    """Unload every loaded instance of `name` through LM Studio's native REST API."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=_LLM_TRANSPORT) as client:
            resp = await client.get(f"{root}/api/v1/models", headers=headers)
            if resp.status_code == 404:
                return "This provider has no unload API (LM Studio 0.4 or later has one); the model stays loaded"
            resp.raise_for_status()
            # An instance's id is the model's key, or the key plus ":N" for a second copy.
            ids = [
                inst["id"]
                for m in resp.json().get("models", [])
                for inst in (m.get("loaded_instances") or [])
                if inst.get("id") and (
                    m.get("key") == name or inst["id"] == name or inst["id"].startswith(f"{name}:")
                )
            ]
            if not ids:
                return f"{name} was not loaded in LM Studio"
            for instance_id in ids:
                (await client.post(
                    f"{root}/api/v1/models/unload", json={"instance_id": instance_id}, headers=headers
                )).raise_for_status()
        return f"Unloaded {name} from LM Studio"
    except Exception as e:
        logger.warning("LM Studio unload during a run failed: %s", e)
        return f"Could not unload {name} ({type(e).__name__}); continuing"


# The name §4 shipped; the tests and any caller from then keep working.
_free_caption_model = _free_llm


async def _stage_generate(
    db: AsyncSession, run: Run, plan: ComfyPlan, recipe: RunRecipe, idx: int, tag: str,
) -> _Outcome:
    """Run this pass's rows that are not generated yet.

    The rows are the `prompts` stage's pending ones, or with no blueprint the plan's
    pending rows when the stage first starts. On a resume, rows a failed or
    interrupted pass left `failed` or `running` go back to pending; completed rows
    are never run twice. Rows still waiting for a rewrite (one the rewrite stage
    could not do) sit the pass out as template text never runs.
    """
    from backend.routers import comfy as comfy_router
    from backend.services import blueprint_rewrite

    p = _pass_of(run, idx)
    if recipe.loop is not None:
        stop = await _budget_stop(db, run, recipe)
        if stop:
            return _stop_loop(run, idx, stop)
    async with comfy_router._plan_lock(plan.id):
        if await comfy_router._plan_job_active(db, plan.id, ("comfy_generate", "comfy_rewrite")):
            raise HTTPException(409, "A generation run or prompt rewrite for this plan is queued or running")
        row_ids = _pass_rows(run, p)
        if row_ids is None:
            row_ids = list((await db.execute(
                select(ComfyRow.id)
                .where(ComfyRow.plan_id == plan.id, ComfyRow.status == "pending")
                .order_by(ComfyRow.sort_order, ComfyRow.created_at)
            )).scalars())
            _set_pass_rows(run, p, row_ids)
        waiting: set[str] = set()
        alias = comfy_router._prompt_alias(plan)
        if alias and row_ids:
            # Before the reset below, and with no scenario filter: no row of any
            # rewrite-enabled scenario may run as template text.
            waiting = {
                rid for t in await blueprint_rewrite.select_targets(db, plan.id, alias, row_ids=set(row_ids))
                for rid, _text in t.rows
            }
        to_run: list[tuple[int, Any, str]] = []
        for chunk in chunked([r for r in row_ids if r not in waiting]):
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
            note = "No rows to generate"
            if waiting:
                note += f" ({len(waiting)} wait for a rewrite that failed)"
            return _Outcome("skip", note=note)
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


async def in_dataset(db: AsyncSession, image_ids: list[str], dataset_id: str, *, kept_only: bool = False) -> list[str]:
    """The ids still in `dataset_id`, in order; with `kept_only`, only those passing the
    fill's kept rule (no `NOT_KEPT_FLAGS`). A row's `image_ids` still names an image the
    user moved to another dataset while the run was paused, and a stage given it would
    touch that dataset, or 400 on a mixed list."""
    from backend.services.blueprint_service import NOT_KEPT_FLAGS
    from backend.services.image_scope import flag_exclusions

    where = flag_exclusions(NOT_KEPT_FLAGS) if kept_only else []
    keep: set[str] = set()
    for chunk in chunked(image_ids):
        keep.update((await db.execute(
            select(Image.id).where(Image.id.in_(chunk), Image.dataset_id == dataset_id, *where)
        )).scalars())
    return [i for i in image_ids if i in keep]


async def _stage_images(db: AsyncSession, run: Run, recipe: RunRecipe, key: str, idx: int, tag: str) -> _Outcome:
    if key == "score":
        # One pass's images: earlier passes' are scored already.
        ids = await in_dataset(db, await run_image_ids(db, _pass_rows(run, _pass_of(run, idx)) or []), run.dataset_id)
    else:
        # Caption what counts, in a run that loops or gates: not what a gate rejected,
        # nor a duplicate.
        ids = await in_dataset(
            db, await run_image_ids(db, (run.context or {}).get("row_ids") or []), run.dataset_id,
            kept_only=recipe.loop is not None or "gate" in recipe.stages,
        )
    # Never `[]`: score and caption read an empty list as the whole dataset.
    if not ids:
        return _Outcome("skip", note="This run produced no images to " + ("score" if key == "score" else "caption"))
    trimmed = None
    if key == "caption" and recipe.loop is not None and recipe.loop.max_paid_calls is not None \
            and await _caption_billed(db, recipe):
        # Multi-image rows can make more images than the clamp planned for, and a
        # resumed caption stage has already spent calls. Caption what the cap allows,
        # and say so, rather than failing a loop whose GPU work is done.
        left = recipe.loop.max_paid_calls - (await _spent(db, run, recipe)).paid_calls
        if left <= 0:
            return _Outcome("skip", note="The paid-call cap is spent; nothing was captioned")
        if len(ids) > left:
            trimmed = f"The paid-call cap leaves {left} calls, so {len(ids) - left} of {len(ids)} images are not captioned"
            ids = ids[:left]
    if key == "score":
        from backend.routers.quality import ScoreRequest, start_score_job

        flags = recipe.gate_flags
        res = await start_score_job(
            db,
            ScoreRequest(
                dataset_id=run.dataset_id, image_ids=ids, run_technical=True,
                run_aesthetic=recipe.score.run_aesthetic, aesthetic_model=recipe.score.aesthetic_model,
                # A gate testing these flags needs the checks that set them.
                run_watermark="has_watermark" in flags, run_nsfw="is_nsfw" in flags,
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
    return _Outcome("job", job_id=res["job_id"], note=trimmed)


# ── The gate (M6 §5) ─────────────────────────────────────────────────────────

async def _stage_gate(db: AsyncSession, run: Run, recipe: RunRecipe, idx: int, tag: str) -> _Outcome:
    from backend.services import run_gate

    assert recipe.gate is not None
    rows = _pass_rows(run, _pass_of(run, idx)) or []
    ids = await in_dataset(db, await run_image_ids(db, rows), run.dataset_id)
    if not ids:
        return _Outcome("skip", note="This pass produced no images to gate")
    job = BackgroundJob(
        job_type="run_gate",
        label=run_job_label(_prefix(run, "gate"), f"Gate — {len(ids)} image{'s' if len(ids) != 1 else ''}"),
        dataset_id=run.dataset_id,
        total_items=len(ids),
        config=run_job_config({"pass_no": _pass_of(run, idx)}, run.id, tag),
    )
    db.add(job)
    await db.commit()
    gate = recipe.gate
    base = recipe.generate.subfolder
    dataset_id = run.dataset_id

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            result = await run_gate.apply_gate(session, rows, base, gate, dataset_id=dataset_id)
            row = await session.get(BackgroundJob, job_id)
            if row is not None:
                row.result_data = result
                row.done_items = result["checked"]
            # One commit: the flags, the moves and the job's numbers together.
            await session.commit()

    await job_queue.enqueue(job, _run)
    return _Outcome("job", job_id=job.id)


# ── The loop (M6 §5) ─────────────────────────────────────────────────────────

# A handoff that undoes one already done this pass: kept when the loop stops, so a
# stopped pass does not leave ComfyUI (or the rewrite model) holding the GPU for the tail.
_UNDOES = {"handoff_to_local": "handoff_to_comfy", "free_rewrite_model": "rewrite"}


def _stop_loop(run: Run, idx: int, reason: str) -> _Outcome:
    """End the loop at this stage: the pass's later body stages are skipped with the
    reason, and the tail still runs. Records `{outcome: "short", reason}`. A handoff
    whose partner ran this pass still runs (`_UNDOES`)."""
    p = _pass_of(run, idx)
    stages = copy.deepcopy(run.stages)
    done_here = {s["key"] for s in stages if s.get("pass_no") == p and s["status"] == "done"}
    for i, s in enumerate(stages):
        if i > idx and s["status"] == "pending" and s.get("pass_no") == p and s["key"] in BODY_STAGES:
            if _UNDOES.get(s["key"]) in done_here:
                continue
            s.update(status="skipped", note=f"Stopped: {reason}", finished_at=_now())
    run.stages = stages
    run.context = {**(run.context or {}), "loop": {"outcome": "short", "reason": reason}}
    return _Outcome("skip", note=f"Stopped: {reason}")


async def _stage_next_pass(db: AsyncSession, run: Run, plan: ComfyPlan, recipe: RunRecipe, idx: int) -> _Outcome:
    """End a pass: recount each folder, mark it met or abandoned, check the caps, and
    insert the next pass's stages before the tail when a folder is still short.

    Inline and commit-free, and idempotent: a restart or resume that runs it again for
    the same pass neither recounts the folders' history nor inserts the pass twice.
    The insert lands after this entry and before any unstarted one, so every started
    stage keeps its index and `stage_tag` stays unique.
    """
    from backend.services import blueprint_service

    loop = recipe.loop
    assert loop is not None
    p = _pass_of(run, idx)
    ctx = run.context or {}
    leaves = copy.deepcopy(ctx.get("leaves") or {})
    if ctx.get("leaves_pass") != p:
        progress = await blueprint_service.leaf_progress(db, plan)
        submitted: set[str] = set()
        for chunk in chunked(_pass_rows(run, p) or []):
            submitted.update(
                lid for lid in (await db.execute(
                    select(ComfyRow.leaf_id).where(ComfyRow.id.in_(chunk))
                )).scalars() if lid
            )
        for lid, v in progress.items():
            e = leaves.get(lid)
            prev = None
            if e is None:
                e = leaves[lid] = {"path": v["path"], "target": v["target"], "kept": [v["kept"]],
                                   "state": "open", "reason": None, "stalls": 0}
            else:
                prev = (e.get("kept") or [None])[-1]
                e.update(path=v["path"], target=v["target"], kept=((e.get("kept") or []) + [v["kept"]])[-12:])
            if e["state"] == "abandoned":
                continue
            if v["kept"] >= v["target"]:
                e.update(state="met", stalls=0)
                continue
            e["state"] = "open"
            # Only a pass that gave the folder rows can show it stuck: one the image
            # cap's round-robin left out gained nothing through no fault of its own.
            if lid in submitted and prev is not None:
                e["stalls"] = e.get("stalls", 0) + 1 if v["kept"] <= prev else 0
                if e["stalls"] >= 2:
                    e.update(state="abandoned", reason="no gain in two passes")
        for lid in [k for k in leaves if k not in progress]:
            del leaves[lid]
        run.context = {**ctx, "leaves": leaves, "leaves_pass": p}

    total = len(leaves)
    met = sum(1 for e in leaves.values() if e["state"] == "met")
    open_ = sum(1 for e in leaves.values() if e["state"] == "open")
    abandoned = sum(1 for e in leaves.values() if e["state"] == "abandoned")
    if not open_:
        if abandoned:
            reason = f"{abandoned} folder{'s' if abandoned != 1 else ''} gave up after two passes with no gain"
            run.context = {**(run.context or {}), "loop": {"outcome": "short", "reason": reason}}
            return _Outcome("done", summary=f"Pass {p}: {met} of {total} folders full; {reason}")
        run.context = {**(run.context or {}), "loop": {"outcome": "met", "reason": None}}
        return _Outcome("done", summary=f"Pass {p}: every folder is full ({total})")
    stop = f"the pass cap ({loop.max_passes}) is reached" if p >= loop.max_passes else await _budget_stop(db, run, recipe)
    if stop:
        run.context = {**(run.context or {}), "loop": {"outcome": "short", "reason": stop}}
        return _Outcome("done", summary=f"Pass {p}: {met} of {total} folders full. Stopped: {stop}")
    if not any(s.get("pass_no") == p + 1 for s in run.stages):
        stages = copy.deepcopy(run.stages)
        keys = [s["key"] for s in stages if s.get("pass_no") == p]
        stages[idx + 1:idx + 1] = [_entry(k, p + 1) for k in keys]
        run.stages = stages
    return _Outcome(
        "done", summary=f"Pass {p}: {met} of {total} folders full, {open_} short — starting pass {p + 1}",
    )


# ── Budgets: recomputed from the run's child jobs, never accumulated ─────────

@dataclass
class _Spent:
    minutes: float
    paid_calls: int
    images: int


async def _provider_billed(db: AsyncSession, provider_id: str) -> bool:
    from backend.models.openai_provider import OpenAIProvider
    from backend.schemas.openai_provider import provider_is_billed

    provider = await db.get(OpenAIProvider, provider_id)
    return provider is not None and provider_is_billed(provider)


async def _rewrite_billed(db: AsyncSession, recipe: RunRecipe) -> bool:
    return recipe.rewrite is not None and await _provider_billed(db, recipe.rewrite.provider_id)


async def _caption_billed(db: AsyncSession, recipe: RunRecipe) -> bool:
    """Only an OpenAI-compatible provider can bill; Ollama and local captioners never do."""
    model = recipe.caption.model if recipe.caption else ""
    if not model.startswith("openai_compat:"):
        return False
    return await _provider_billed(db, model.split(":", 2)[1])


async def _spent(db: AsyncSession, run: Run, recipe: RunRecipe) -> _Spent:
    """What the run has used, read from its child jobs every time. Stage entries are not
    the record: a resume and the restart reconcile wipe a failed stage's numbers, and a
    failed rewrite's calls were still made."""
    jobs = (await db.execute(
        select(BackgroundJob).where(BackgroundJob.config["run_id"].as_string() == run.id)
    )).scalars().all()
    from backend.workers.job_queue import INTERRUPTED_MSG

    now = datetime.utcnow()
    # A job the restart marked interrupted has `finished_at` at the restart, so its
    # span includes the downtime (a crash, a sleep). It counts as no time at all:
    # undercounting a crashed job is the safe side of a cap, and blocking every
    # resume with "the time cap is spent" is not.
    seconds = sum(
        ((j.finished_at or now) - j.started_at).total_seconds()
        for j in jobs if j.started_at and j.error_msg != INTERRUPTED_MSG
    )
    calls = 0
    if await _rewrite_billed(db, recipe):
        calls += sum(int((j.result_data or {}).get("calls") or 0) for j in jobs if j.job_type == "comfy_rewrite")
    if await _caption_billed(db, recipe):
        calls += sum(int(j.done_items or 0) for j in jobs if j.job_type == "caption")
    images = len(await run_image_ids(db, (run.context or {}).get("row_ids") or []))
    return _Spent(minutes=max(0.0, seconds) / 60, paid_calls=calls, images=images)


async def _budget_stop(db: AsyncSession, run: Run, recipe: RunRecipe) -> str | None:
    """Why the loop must stop before its next expensive stage, or None."""
    loop = recipe.loop
    if loop is None:
        return None
    spent = await _spent(db, run, recipe)
    if spent.minutes >= loop.max_minutes:
        return f"the time cap ({loop.max_minutes} min) is spent"
    if spent.images >= loop.max_images:
        return f"the image cap ({loop.max_images}) is reached"
    return None


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
    if job.job_type == "run_gate":
        n, bad = rd.get("checked", 0), rd.get("rejected", 0)
        return f"{bad} of {n} image{'s' if n != 1 else ''} rejected"
    if job.job_type == "comfy_rewrite":
        n, failed, calls = rd.get("rewritten", 0), rd.get("leaves_failed", 0), rd.get("calls", 0)
        out = f"{n} prompt{'s' if n != 1 else ''} rewritten"
        if failed:
            out += f", {failed} folder{'s' if failed != 1 else ''} failed"
        return out + f" · {calls} call{'s' if calls != 1 else ''}"
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
