"""ComfyUI generation queue: plans, prompt rows, and the generation job.

A plan stores an API-format ComfyUI workflow plus pinned parameters; rows hold
per-run values. The run endpoint queues a background job that submits each row
to the ComfyUI server sequentially, downloads the outputs, and ingests them
into the dataset. See docs/dev/comfyui.md.
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import BackgroundJob, Dataset, Image
from backend.models.comfy import ComfyPlan, ComfyRow
from backend.services.comfy_service import (
    MAX_CONSECUTIVE_CONNECT_ERRORS,
    PER_ROW_TIMEOUT_S,
    ComfyClient,
    ComfyRowError,
    check_history_error,
    effective_prompt,
    extract_output_images,
    patch_workflow,
    workflow_format,
)
from backend.services.threshold_service import get_thresholds
from backend.utils import (
    normalize_subfolder,
    sanitize_abs_path,
    slugify_filename,
    thumbnail_path_for,
    unique_filename_with_thumb,
)
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/comfy", tags=["comfy"])
logger = logging.getLogger(__name__)

INT_MODES = ("fixed", "random", "increment")
ROW_STATUSES = ("pending", "running", "completed", "failed")


# ── Schemas ───────────────────────────────────────────────────────────────────

class PinnedParam(BaseModel):
    node_id: str
    input: str
    alias: str = Field(min_length=1, max_length=100)
    is_prompt: bool = False
    # per_row=True → queue-table column; False → run default only (no column).
    per_row: bool = True
    # Run-default override applied to every row without a row value; None/"" = template.
    value: str | int | float | bool | None = None
    # Integer params only: fixed (default) | random | increment, applied when no row value.
    int_mode: str | None = None


class PlanCreate(BaseModel):
    dataset_id: str
    name: str = Field(min_length=1, max_length=255)
    workflow_json: dict = Field(default_factory=dict)
    pinned_params: list[PinnedParam] = Field(default_factory=list)
    output_node_ids: list[str] = Field(default_factory=list)


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    workflow_json: dict | None = None
    pinned_params: list[PinnedParam] | None = None
    # [] clears back to auto (import all SaveImage outputs); absent = unchanged.
    output_node_ids: list[str] | None = None


class RowCreate(BaseModel):
    values: dict = Field(default_factory=dict)


class RowUpdate(BaseModel):
    values: dict | None = None
    sort_order: int | None = None


class BulkLinesRequest(BaseModel):
    lines: list[str]


class RowIdsRequest(BaseModel):
    row_ids: list[str]


class RowResetRequest(BaseModel):
    row_ids: list[str] | None = None


class SetValueRequest(BaseModel):
    alias: str
    # None / "" clears the override on every row (back to the template value).
    value: str | int | float | bool | None = None


class GeneratePromptsRequest(BaseModel):
    provider_id: str
    model_name: str = ""  # empty → provider default_model
    # Standing rules for HOW prompts are written (style/format) — system message.
    system_instructions: str = Field(default="", max_length=4000)
    # The per-call ask: WHAT to generate now.
    instruction: str = Field(min_length=1, max_length=4000)
    batch_size: int = Field(default=5, ge=1, le=10)
    # Prompts that already exist (queue rows + prior batches) — the LLM is told
    # to diverge from them; this is the diversity mechanism.
    existing: list[str] = Field(default_factory=list)
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)


BULK_EDIT_OPS = ("prepend", "append", "remove", "find_replace")


class RowsBulkEditRequest(BaseModel):
    operation: str
    text: str = Field(min_length=1)
    replacement: str = ""
    use_regex: bool = False
    row_ids: list[str] | None = None  # None = all rows of the plan


class RunRequest(BaseModel):
    plan_id: str
    row_ids: list[str] | None = None
    subfolder: str = ""
    set_caption: bool = True
    label: str | None = None


def _plan_out(plan: ComfyPlan) -> dict:
    return {
        "id": plan.id,
        "dataset_id": plan.dataset_id,
        "name": plan.name,
        "workflow_json": plan.workflow_json or {},
        "pinned_params": plan.pinned_params or [],
        "output_node_ids": plan.output_node_ids or [],
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def _row_out(row: ComfyRow) -> dict:
    return {
        "id": row.id,
        "plan_id": row.plan_id,
        "sort_order": row.sort_order,
        "values": row.values or {},
        "status": row.status,
        "error_msg": row.error_msg,
        "image_id": row.image_id,
        "image_ids": row.image_ids or [],
        "prompt_id": row.prompt_id,
    }


async def _get_plan(db: AsyncSession, plan_id: str) -> ComfyPlan:
    plan = await db.get(ComfyPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return plan


def _validate_pins(pins: list[PinnedParam]) -> list[dict]:
    aliases = [p.alias for p in pins]
    if len(set(aliases)) != len(aliases):
        raise HTTPException(400, "Pinned parameter aliases must be unique")
    if sum(1 for p in pins if p.is_prompt) > 1:
        raise HTTPException(400, "Only one pinned parameter can be marked as the prompt")
    for p in pins:
        if p.int_mode is not None and p.int_mode not in INT_MODES:
            raise HTTPException(400, f"int_mode must be one of {INT_MODES}")
        if p.is_prompt and not p.per_row:
            raise HTTPException(400, "The prompt parameter must be per-row (it is the queue's prompt column)")
    return [p.model_dump() for p in pins]


# ── Connection test ───────────────────────────────────────────────────────────

@router.get("/ping")
async def ping(url: str | None = None, db: AsyncSession = Depends(get_db)):
    base_url = (url or "").strip()
    if not base_url:
        thresholds = await get_thresholds(db)
        base_url = (thresholds.comfyui_url or "").strip()
    if not base_url:
        return {"ok": False, "error": "No ComfyUI URL configured"}
    try:
        await ComfyClient(base_url).ping()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Workflow folder scan ──────────────────────────────────────────────────────

_WORKFLOW_SCAN_MAX_FILES = 500
_WORKFLOW_SNIFF_MAX_BYTES = 5 * 1024 * 1024


@router.get("/workflow-files")
async def list_workflow_files(dir: str | None = None, db: AsyncSession = Depends(get_db)):
    """Scan a folder (recursively) for ComfyUI workflow .json files.

    `dir` falls back to the comfy_workflow_dir setting. Each file is sniffed as
    api / ui / invalid so the client can offer only loadable (API-format) ones.
    """
    scan_dir = (dir or "").strip()
    if not scan_dir:
        thresholds = await get_thresholds(db)
        scan_dir = (thresholds.comfy_workflow_dir or "").strip()
    if not scan_dir:
        raise HTTPException(400, "No folder given — set a workflow folder in Settings → ComfyUI or pick one")
    p = sanitize_abs_path(scan_dir)
    if not p.is_dir():
        raise HTTPException(404, "Folder not found")

    def _scan() -> list[dict]:
        files: list[dict] = []
        for f in sorted(p.rglob("*.json"), key=lambda x: x.name.lower()):
            if not f.is_file():
                continue
            if len(files) >= _WORKFLOW_SCAN_MAX_FILES:
                break
            try:
                stat = f.stat()
            except OSError:
                continue
            try:
                if stat.st_size > _WORKFLOW_SNIFF_MAX_BYTES:
                    fmt = "invalid"
                else:
                    fmt = workflow_format(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                fmt = "invalid"
            files.append({
                "name": str(f.relative_to(p)),
                "path": str(f),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "format": fmt,
            })
        return files

    try:
        files = await asyncio.get_running_loop().run_in_executor(None, _scan)
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return {"dir": str(p), "files": files}


@router.get("/workflow-file")
async def load_workflow_file(path: str):
    """Read one .json workflow file; returns the parsed API-format workflow."""
    p = sanitize_abs_path(path)
    if not p.is_file():
        raise HTTPException(404, "File not found")
    if p.stat().st_size > _WORKFLOW_SNIFF_MAX_BYTES:
        raise HTTPException(400, "File too large to be a workflow export")
    try:
        parsed = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(400, f"Could not parse JSON: {e}")
    fmt = workflow_format(parsed)
    if fmt == "ui":
        raise HTTPException(
            400,
            'UI-format export — in ComfyUI use "Workflow → Export (API)" and save that file instead',
        )
    if fmt != "api":
        raise HTTPException(400, "Not an API-format workflow ({node_id: {class_type, inputs}})")
    return {"workflow": parsed}


# ── Plan CRUD ─────────────────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans(dataset_id: str, db: AsyncSession = Depends(get_db)):
    plans = (
        await db.execute(
            select(ComfyPlan).where(ComfyPlan.dataset_id == dataset_id).order_by(ComfyPlan.created_at)
        )
    ).scalars().all()
    counts = (
        await db.execute(
            select(ComfyRow.plan_id, ComfyRow.status, func.count(ComfyRow.id))
            .join(ComfyPlan, ComfyRow.plan_id == ComfyPlan.id)
            .where(ComfyPlan.dataset_id == dataset_id)
            .group_by(ComfyRow.plan_id, ComfyRow.status)
        )
    ).all()
    status_counts: dict[str, dict[str, int]] = {}
    for plan_id, status, n in counts:
        status_counts.setdefault(plan_id, {})[status] = n
    return [
        {
            "id": p.id,
            "dataset_id": p.dataset_id,
            "name": p.name,
            "row_count": sum(status_counts.get(p.id, {}).values()),
            "status_counts": status_counts.get(p.id, {}),
        }
        for p in plans
    ]


@router.post("/plans")
async def create_plan(body: PlanCreate, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, body.dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    dup = await db.execute(
        select(ComfyPlan.id).where(ComfyPlan.dataset_id == body.dataset_id, ComfyPlan.name == body.name)
    )
    if dup.first():
        raise HTTPException(400, f"Plan '{body.name}' already exists in this dataset")
    plan = ComfyPlan(
        dataset_id=body.dataset_id,
        name=body.name,
        workflow_json=body.workflow_json,
        pinned_params=_validate_pins(body.pinned_params),
        output_node_ids=body.output_node_ids,
    )
    db.add(plan)
    await db.commit()
    return _plan_out(plan)


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    return _plan_out(await _get_plan(db, plan_id))


@router.patch("/plans/{plan_id}")
async def update_plan(plan_id: str, body: PlanUpdate, db: AsyncSession = Depends(get_db)):
    plan = await _get_plan(db, plan_id)
    if body.name is not None and body.name != plan.name:
        dup = await db.execute(
            select(ComfyPlan.id).where(
                ComfyPlan.dataset_id == plan.dataset_id,
                ComfyPlan.name == body.name,
                ComfyPlan.id != plan_id,
            )
        )
        if dup.first():
            raise HTTPException(400, f"Plan '{body.name}' already exists in this dataset")
        plan.name = body.name
    if body.workflow_json is not None:
        plan.workflow_json = body.workflow_json
    if body.pinned_params is not None:
        plan.pinned_params = _validate_pins(body.pinned_params)
    if body.output_node_ids is not None:
        plan.output_node_ids = list(body.output_node_ids)
    await db.commit()
    return _plan_out(plan)


@router.delete("/plans/{plan_id}", status_code=204)
async def delete_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    plan = await _get_plan(db, plan_id)
    await db.delete(plan)
    await db.commit()


# ── Row CRUD ──────────────────────────────────────────────────────────────────

@router.get("/plans/{plan_id}/rows")
async def list_rows(plan_id: str, db: AsyncSession = Depends(get_db)):
    await _get_plan(db, plan_id)
    rows = (
        await db.execute(
            select(ComfyRow).where(ComfyRow.plan_id == plan_id).order_by(ComfyRow.sort_order, ComfyRow.created_at)
        )
    ).scalars().all()
    return [_row_out(r) for r in rows]


@router.get("/plans/{plan_id}/prompts")
async def list_plan_prompts(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Effective prompt text of each row — used to reuse prompts across plans/datasets.

    Returns the row → run-default → template prompt for every row (the same chain
    the run uses), so prompts can be browsed and imported into another plan even
    when the two plans have different pinned parameters. Rows whose effective
    prompt is empty are omitted.
    """
    plan = await _get_plan(db, plan_id)
    workflow = plan.workflow_json or {}
    pinned = plan.pinned_params or []
    rows = (
        await db.execute(
            select(ComfyRow).where(ComfyRow.plan_id == plan_id).order_by(ComfyRow.sort_order, ComfyRow.created_at)
        )
    ).scalars().all()
    out = []
    for r in rows:
        prompt = effective_prompt(workflow, pinned, r.values or {}) or ""
        if prompt.strip():
            out.append({"row_id": r.id, "prompt": prompt, "status": r.status})
    return {"prompts": out}


async def _next_sort_order(db: AsyncSession, plan_id: str) -> int:
    max_order = (
        await db.execute(select(func.max(ComfyRow.sort_order)).where(ComfyRow.plan_id == plan_id))
    ).scalar()
    return (max_order + 1) if max_order is not None else 0


@router.post("/plans/{plan_id}/rows")
async def create_row(plan_id: str, body: RowCreate, db: AsyncSession = Depends(get_db)):
    await _get_plan(db, plan_id)
    row = ComfyRow(plan_id=plan_id, sort_order=await _next_sort_order(db, plan_id), values=body.values)
    db.add(row)
    await db.commit()
    return _row_out(row)


@router.post("/plans/{plan_id}/rows/bulk")
async def bulk_add_rows(plan_id: str, body: BulkLinesRequest, db: AsyncSession = Depends(get_db)):
    plan = await _get_plan(db, plan_id)
    prompt_alias = next((p["alias"] for p in (plan.pinned_params or []) if p.get("is_prompt")), None)
    if not prompt_alias:
        raise HTTPException(400, "No pinned parameter is marked as the prompt — pin one first")
    lines = [ln.strip() for ln in body.lines if ln.strip()]
    order = await _next_sort_order(db, plan_id)
    for i, line in enumerate(lines):
        db.add(ComfyRow(plan_id=plan_id, sort_order=order + i, values={prompt_alias: line}))
    await db.commit()
    return {"created": len(lines)}


@router.patch("/rows/{row_id}")
async def update_row(row_id: str, body: RowUpdate, db: AsyncSession = Depends(get_db)):
    row = await db.get(ComfyRow, row_id)
    if not row:
        raise HTTPException(404, "Row not found")
    if body.values is not None:
        row.values = dict(body.values)
        # Edited values invalidate a previous result; keep image links for reference.
        if row.status in ("completed", "failed"):
            row.status = "pending"
            row.error_msg = None
            row.prompt_id = None
    if body.sort_order is not None:
        row.sort_order = body.sort_order
    await db.commit()
    return _row_out(row)


@router.post("/plans/{plan_id}/rows/delete")
async def delete_rows(plan_id: str, body: RowIdsRequest, db: AsyncSession = Depends(get_db)):
    await _get_plan(db, plan_id)
    rows = (
        await db.execute(
            select(ComfyRow).where(ComfyRow.plan_id == plan_id, ComfyRow.id.in_(body.row_ids))
        )
    ).scalars().all()
    for r in rows:
        await db.delete(r)
    await db.commit()
    return {"deleted": len(rows)}


@router.post("/plans/{plan_id}/rows/reorder", status_code=204)
async def reorder_rows(plan_id: str, body: RowIdsRequest, db: AsyncSession = Depends(get_db)):
    await _get_plan(db, plan_id)
    rows = (
        await db.execute(select(ComfyRow).where(ComfyRow.plan_id == plan_id))
    ).scalars().all()
    by_id = {r.id: r for r in rows}
    if set(body.row_ids) != set(by_id):
        raise HTTPException(400, "row_ids must contain exactly the plan's row ids")
    for i, rid in enumerate(body.row_ids):
        by_id[rid].sort_order = i
    await db.commit()


@router.post("/plans/{plan_id}/rows/set-value")
async def set_value_all_rows(plan_id: str, body: SetValueRequest, db: AsyncSession = Depends(get_db)):
    """Set (or clear) one pinned parameter's value on every row of the plan.

    Same semantics as editing a cell via PATCH /rows/{id}: completed/failed rows
    whose values change reset to pending (image links are kept).
    """
    plan = await _get_plan(db, plan_id)
    aliases = {p["alias"] for p in (plan.pinned_params or [])}
    if body.alias not in aliases:
        raise HTTPException(400, f"No pinned parameter with alias {body.alias!r}")
    clear = body.value in (None, "")
    rows = (
        await db.execute(select(ComfyRow).where(ComfyRow.plan_id == plan_id))
    ).scalars().all()
    updated = 0
    for r in rows:
        vals = dict(r.values or {})
        if clear:
            if body.alias not in vals:
                continue
            del vals[body.alias]
        else:
            if vals.get(body.alias) == body.value:
                continue
            vals[body.alias] = body.value
        r.values = vals
        if r.status in ("completed", "failed"):
            r.status = "pending"
            r.error_msg = None
            r.prompt_id = None
        updated += 1
    await db.commit()
    return {"updated": updated}


@router.post("/generate-prompts")
async def generate_prompts_endpoint(body: GeneratePromptsRequest, db: AsyncSession = Depends(get_db)):
    """One LLM batch call → {prompts}. The client loops for 'generate until N'."""
    from backend.ml.prompt_generator import generate_prompts
    from backend.models.openai_provider import OpenAIProvider

    provider = await db.get(OpenAIProvider, body.provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")
    model_name = body.model_name.strip() or provider.default_model
    if not model_name:
        raise HTTPException(400, "No model given and the provider has no default model")
    try:
        prompts = await generate_prompts(
            base_url=provider.base_url,
            api_key=provider.api_key,
            model_name=model_name,
            instruction=body.instruction,
            system_instructions=body.system_instructions,
            batch_size=body.batch_size,
            existing=body.existing,
            temperature=body.temperature,
            max_tokens=provider.max_tokens,
        )
    except Exception as e:
        raise HTTPException(502, f"Prompt generation failed: {e}")
    return {"prompts": prompts, "model": model_name}


@router.post("/plans/{plan_id}/rows/bulk-edit")
async def bulk_edit_rows(plan_id: str, body: RowsBulkEditRequest, db: AsyncSession = Depends(get_db)):
    """Bulk text operation on the prompt column (mirrors caption bulk-edit semantics).

    The base text per row is its effective prompt (row value → run default →
    template); the result is written into the row's values, and changed
    completed/failed rows reset to pending. Returns {affected, skipped}.
    """
    import re as _re

    if body.operation not in BULK_EDIT_OPS:
        raise HTTPException(400, f"operation must be one of {BULK_EDIT_OPS}")
    plan = await _get_plan(db, plan_id)
    pinned = plan.pinned_params or []
    prompt_alias = next((p["alias"] for p in pinned if p.get("is_prompt")), None)
    if not prompt_alias:
        raise HTTPException(400, "No pinned parameter is marked as the prompt — pin one first")
    workflow = plan.workflow_json or {}

    stmt = select(ComfyRow).where(ComfyRow.plan_id == plan_id)
    if body.row_ids is not None:
        stmt = stmt.where(ComfyRow.id.in_(body.row_ids))
    rows = (await db.execute(stmt)).scalars().all()

    if body.use_regex:
        try:
            pattern = _re.compile(body.text)
        except _re.error as e:
            raise HTTPException(400, f"Invalid regex: {e}")

    bases = [(row, effective_prompt(workflow, pinned, row.values or {}) or "") for row in rows]

    def _transform() -> list[tuple[ComfyRow, str, str]]:
        out = []
        for row, base in bases:
            if body.operation == "prepend":
                new = body.text + base
            elif body.operation == "append":
                new = base + body.text
            elif not base:
                continue  # remove/find_replace skip empty prompts
            elif body.use_regex:
                repl = "" if body.operation == "remove" else body.replacement
                new = pattern.sub(repl, base)
            else:
                repl = "" if body.operation == "remove" else body.replacement
                new = base.replace(body.text, repl)
            out.append((row, base, new))
        return out

    # Regex on user input can backtrack catastrophically — bound it like caption bulk-edit.
    try:
        transformed = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _transform), timeout=30.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(408, "Regex evaluation timed out")

    affected = 0
    for row, base, new in transformed:
        if new == base:
            continue  # no-op (e.g. find/replace with no match) — don't touch the row
        vals = dict(row.values or {})
        vals[prompt_alias] = new
        row.values = vals
        if row.status in ("completed", "failed"):
            row.status = "pending"
            row.error_msg = None
            row.prompt_id = None
        affected += 1
    await db.commit()
    return {"affected": affected, "skipped": len(rows) - affected}


@router.post("/plans/{plan_id}/rows/reset")
async def reset_rows(plan_id: str, body: RowResetRequest, db: AsyncSession = Depends(get_db)):
    await _get_plan(db, plan_id)
    stmt = select(ComfyRow).where(ComfyRow.plan_id == plan_id, ComfyRow.status == "failed")
    if body.row_ids:
        stmt = stmt.where(ComfyRow.id.in_(body.row_ids))
    rows = (await db.execute(stmt)).scalars().all()
    for r in rows:
        r.status = "pending"
        r.error_msg = None
        r.prompt_id = None
    await db.commit()
    return {"reset": len(rows)}


# ── Run ───────────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_plan(body: RunRequest, db: AsyncSession = Depends(get_db)):
    plan = await _get_plan(db, body.plan_id)
    if not plan.workflow_json:
        raise HTTPException(400, "Plan has no workflow — paste an API-format workflow JSON first")

    thresholds = await get_thresholds(db)
    comfy_url = (thresholds.comfyui_url or "").strip()
    if not comfy_url:
        raise HTTPException(400, "No ComfyUI URL configured — set it in Settings")

    dataset = await db.get(Dataset, plan.dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    # One run at a time per plan.
    active = (
        await db.execute(
            select(BackgroundJob).where(
                BackgroundJob.job_type == "comfy_generate",
                BackgroundJob.status.in_(("pending", "running")),
            )
        )
    ).scalars().all()
    if any((j.config or {}).get("plan_id") == body.plan_id for j in active):
        raise HTTPException(409, "A generation run for this plan is already queued or running")

    if body.row_ids:
        stmt = select(ComfyRow).where(ComfyRow.plan_id == body.plan_id, ComfyRow.id.in_(body.row_ids))
    else:
        stmt = select(ComfyRow).where(ComfyRow.plan_id == body.plan_id, ComfyRow.status == "pending")
    rows = (await db.execute(stmt.order_by(ComfyRow.sort_order, ComfyRow.created_at))).scalars().all()
    if not rows:
        raise HTTPException(400, "No rows to run")
    row_ids = [r.id for r in rows]

    n = len(row_ids)
    auto_label = f"ComfyUI: {plan.name} — {n} row{'s' if n != 1 else ''}"
    job = BackgroundJob(
        job_type="comfy_generate",
        label=body.label or auto_label,
        dataset_id=plan.dataset_id,
        total_items=n,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    plan_id = body.plan_id
    subfolder = normalize_subfolder(body.subfolder)
    set_caption = body.set_caption

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.services.caption_service import _write_txt_sidecar
        from backend.services.dataset_service import _register_file_sync, refresh_stats
        from backend.workers.progress import broadcaster

        async with AsyncSessionLocal() as session:
            plan_row = await session.get(ComfyPlan, plan_id)
            ds = await session.get(Dataset, plan_row.dataset_id) if plan_row else None
            if not plan_row or not ds:
                return  # plan/dataset deleted after the job was enqueued
            workflow = plan_row.workflow_json or {}
            pinned = plan_row.pinned_params or []
            output_node_ids = plan_row.output_node_ids or []

            run_rows = (
                await session.execute(
                    select(ComfyRow)
                    .where(ComfyRow.id.in_(row_ids))
                    .order_by(ComfyRow.sort_order, ComfyRow.created_at)
                )
            ).scalars().all()
            total = len(run_rows)

            images_dir = Path(ds.folder_path) / "images"
            thumbs_dir = Path(ds.folder_path) / "thumbnails"
            images_dir.mkdir(parents=True, exist_ok=True)
            thumbs_dir.mkdir(parents=True, exist_ok=True)
            existing = await session.execute(select(Image.filename).where(Image.dataset_id == ds.id))
            db_filenames: set[str] = {r[0] for r in existing.all()}
            occupied_thumb_stems: set[str] = {p.stem for p in thumbs_dir.glob("*.webp")}
            planned_thumb_stems: set[str] = set()
            stem_slug = slugify_filename(plan_row.name) or "comfy"

            client = ComfyClient(comfy_url)
            loop = asyncio.get_running_loop()
            created_image_ids: list[str] = []
            failed_row_ids: list[str] = []
            consecutive_connect_errors = 0

            def _write_and_register(data: bytes, dest: Path, thumb: str) -> tuple[dict, dict | None]:
                dest.write_bytes(data)
                return _register_file_sync(dest, thumb)

            async def _emit(done: int, message: str, current: str = "") -> None:
                await broadcaster.emit(job_id, {
                    "type": "progress",
                    "job_id": job_id,
                    "job_type": "comfy_generate",
                    "status": "running",
                    "done": done,
                    "total": total,
                    "percent": round(done / total * 100, 1) if total else 100.0,
                    "current_item": current,
                    "message": message,
                    "dataset_id": ds.id,
                })

            for i, row in enumerate(run_rows):
                job_queue.raise_if_cancelled(job_id)
                prompt_preview = (effective_prompt(workflow, pinned, row.values or {}) or "")[:80]
                row.status = "running"
                row.error_msg = None
                await session.commit()
                await _emit(i, f"Generating {i + 1}/{total}", prompt_preview)

                # Populated as the row's outputs are imported; used to roll back files +
                # Image rows if the row fails partway through (see the except handler).
                row_images: list[Image] = []
                row_files: list[Path] = []
                try:
                    wf = patch_workflow(workflow, pinned, row.values or {}, i)
                    prompt_id = await client.submit(wf, client_id=job_id)
                    consecutive_connect_errors = 0
                    row.prompt_id = prompt_id
                    await session.commit()

                    deadline = time.monotonic() + PER_ROW_TIMEOUT_S
                    while True:
                        if job_queue.cancel_requested(job_id):
                            await client.interrupt()
                            row.status = "pending"
                            await session.commit()
                            raise asyncio.CancelledError()
                        entry = await client.poll_history(prompt_id)
                        if entry is not None:
                            break
                        if time.monotonic() > deadline:
                            raise ComfyRowError(
                                f"timed out after {int(PER_ROW_TIMEOUT_S / 60)} min waiting for ComfyUI"
                            )
                        await asyncio.sleep(1.0)

                    err = check_history_error(entry)
                    if err:
                        raise ComfyRowError(err)
                    image_refs = extract_output_images(entry, output_node_ids)
                    if not image_refs:
                        raise ComfyRowError(
                            f"selected output node(s) {', '.join(output_node_ids)} produced no "
                            "images — review the workflow and the plan's output-node selection"
                            if output_node_ids
                            else "workflow produced no output images — add a SaveImage node, or "
                            "select an output node (e.g. a PreviewImage) in Workflow & Pins"
                        )

                    # Per-row atomicity: row_files / row_images (declared above) track every
                    # file + Image created for this row so a failure partway through a
                    # multi-output row leaves no stray files on disk and no orphan Image rows
                    # (which would otherwise surface in the gallery under a "failed" row).
                    row_image_ids: list[str] = []
                    for ref in image_refs:
                        data = await client.fetch_image(
                            ref["filename"], ref.get("subfolder", ""), ref.get("type", "output")
                        )
                        suffix = Path(ref["filename"]).suffix.lower() or ".png"
                        new_name = unique_filename_with_thumb(
                            images_dir, stem_slug, suffix, db_filenames,
                            occupied_thumb_stems, planned_thumb_stems,
                        )
                        dest = images_dir / new_name
                        thumb_path = thumbnail_path_for(dest)
                        # Track BEFORE writing: _write_and_register can raise after
                        # dest is on disk (corrupt bytes → PIL error during register),
                        # and unlink(missing_ok=True) makes early tracking harmless.
                        row_files.extend((dest, Path(thumb_path)))
                        info, gen_meta = await loop.run_in_executor(
                            None, _write_and_register, data, dest, thumb_path
                        )
                        img = Image(
                            dataset_id=ds.id,
                            filename=new_name,
                            original_filename=ref["filename"],
                            subfolder=subfolder,
                            file_path=str(dest),
                            thumbnail_path=thumb_path,
                            generation_metadata=gen_meta,
                            **info,
                        )
                        if set_caption:
                            caption = effective_prompt(workflow, pinned, row.values or {})
                            if caption:
                                img.caption_text = caption
                                img.captioned_by = "comfyui"
                                img.captioned_at = datetime.utcnow()
                                row_files.append(dest.with_suffix(".txt"))
                                _write_txt_sidecar(str(dest), caption)
                        session.add(img)
                        await session.flush()
                        row_images.append(img)
                        row_image_ids.append(img.id)

                    row.image_id = row_image_ids[0]
                    row.image_ids = row_image_ids
                    row.status = "completed"
                    row.error_msg = None
                    created_image_ids.extend(row_image_ids)
                except asyncio.CancelledError:
                    # Hard task cancellation (server shutdown) mid-import: remove the
                    # files already produced for this row (sync — guaranteed even if
                    # the loop refuses further awaits), then best-effort DB cleanup so
                    # the row doesn't stay committed as "running" with orphan images.
                    # The cooperative-cancel path above commits "pending" before any
                    # files are written, so this is idempotent with it.
                    for f in row_files:
                        try:
                            f.unlink(missing_ok=True)
                        except OSError:
                            pass
                    try:
                        for img_obj in row_images:
                            await session.delete(img_obj)
                        row.image_id = None
                        row.image_ids = []
                        row.status = "pending"
                        row.error_msg = None
                        await session.commit()
                    except Exception:
                        pass  # best-effort; a second CancelledError propagates below
                    raise
                except (ComfyRowError, httpx.HTTPError, OSError) as e:
                    # Discard any images/files this row produced before it failed.
                    for img_obj in row_images:
                        await session.delete(img_obj)
                    for f in row_files:
                        try:
                            f.unlink(missing_ok=True)
                        except OSError:
                            pass
                    row.image_id = None
                    row.image_ids = []
                    row.status = "failed"
                    row.error_msg = str(e)[:2000]
                    failed_row_ids.append(row.id)
                    logger.warning("comfy_generate: row %s failed", row.id, exc_info=True)
                    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
                        consecutive_connect_errors += 1
                        if consecutive_connect_errors >= MAX_CONSECUTIVE_CONNECT_ERRORS:
                            await session.commit()
                            raise RuntimeError(
                                f"ComfyUI unreachable at {comfy_url} "
                                f"({consecutive_connect_errors} consecutive connection failures) — "
                                f"aborting; remaining rows left pending"
                            )
                    else:
                        consecutive_connect_errors = 0

                await session.commit()
                await _emit(i + 1, f"Completed {i + 1}/{total}", prompt_preview)

            await refresh_stats(session, ds.id)
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = {
                    "created_image_ids": created_image_ids,
                    "failed_row_ids": failed_row_ids,
                    "completed": total - len(failed_row_ids),
                    "failed": len(failed_row_ids),
                }
            await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": n}
