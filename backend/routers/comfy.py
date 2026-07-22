"""ComfyUI generation queue: plans, prompt rows, and the generation job.

A plan stores an API-format ComfyUI workflow plus pinned parameters; rows hold
per-run values. The run endpoint queues a background job that submits each row
to the ComfyUI server sequentially, downloads the outputs, and ingests them
into the dataset. See docs/dev/comfyui.md.
"""
import asyncio
import json
import logging
import math
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.licenses import PROVENANCE_FIELDS
from backend.models import BackgroundJob, Dataset, Image
from backend.models.comfy import ComfyLibraryPrompt, ComfyPlan, ComfyRow
from backend.services.comfy_service import (
    MAX_CONSECUTIVE_CONNECT_ERRORS,
    PER_ROW_TIMEOUT_S,
    ComfyClient,
    ComfyRowError,
    check_history_error,
    effective_prompt,
    extract_output_images,
    history_entry_workflow,
    patch_workflow,
    workflow_format,
)
from backend.services.threshold_service import get_thresholds
from backend.utils import (
    REGEX_TIMEOUT_SECONDS,
    compile_user_regex,
    normalize_subfolder,
    regex_error,
    regex_sub_deadline,
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
NO_PROMPT_PIN_MSG = "No pinned parameter is marked as the prompt — pin one first"


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


class LibraryAddRequest(BaseModel):
    category: str = Field(max_length=100)
    prompts: list[str]


class LibraryMoveRequest(BaseModel):
    ids: list[str]
    category: str = Field(max_length=100)


class LibraryIdsRequest(BaseModel):
    ids: list[str]


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


class GeneratePromptsJobRequest(BaseModel):
    """Body of the background 'generate until N' job.

    Mirrors GeneratePromptsRequest's bounds, but deliberately has **no
    `existing` field**: the server derives the anti-similarity context from the
    plan itself. Every field here is bounded, which is what makes persisting
    `model_dump()` into `BackgroundJob.config` safe — `JobOut.config` is
    returned to the client verbatim, so the provider's api_key must never be
    able to reach it.
    """

    provider_id: str
    model_name: str = ""  # empty → provider default_model
    system_instructions: str = Field(default="", max_length=4000)
    instruction: str = Field(min_length=1, max_length=4000)
    batch_size: int = Field(default=5, ge=1, le=10)
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    # Absolute ("until the plan holds N prompts"), not "N more" — so resuming
    # after a stop or a partial run is idempotent.
    target_count: int = Field(ge=1, le=200)
    # Controls only what the LLM is *shown*. Dedupe against existing rows is
    # unconditional (see the job's `seen` set).
    use_existing_context: bool = True
    label: str | None = Field(default=None, max_length=200)


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


def _workflow_checkpoints(workflow: dict | None) -> list[str]:
    """Checkpoint names loaded by an API-format workflow, in node order.

    Read from the workflow itself rather than from the PNG's embedded metadata:
    ComfyUI's own output carries the whole prompt graph under `prompt`/`workflow`
    and never a flat `checkpoint` key, so scanning `generation_metadata` for one
    finds nothing for genuine ComfyUI images.
    """
    out: list[str] = []
    for node in (workflow or {}).values():
        if not isinstance(node, dict):
            continue
        if "checkpoint" not in str(node.get("class_type", "")).lower():
            continue
        name = (node.get("inputs") or {}).get("ckpt_name")
        if isinstance(name, str) and name and name not in out:
            out.append(name)
    return out


def _comfy_source_meta(plan: ComfyPlan, row: ComfyRow, workflow: dict | None) -> dict:
    """Provenance `source_meta` for an image imported from a ComfyUI run.

    Records which plan/row produced it and the checkpoint(s) it came from, so a
    synthetic image can be traced back to its generator. Deliberately compact —
    the full workflow already lives in `generation_metadata`.
    """
    meta: dict = {"generator": "ComfyUI", "plan_id": plan.id, "plan_name": plan.name, "row_id": row.id}
    checkpoints = _workflow_checkpoints(workflow)
    if checkpoints:
        meta["checkpoint"] = checkpoints[0] if len(checkpoints) == 1 else checkpoints
    return meta


def _comfy_output_provenance(ds) -> dict:
    """Provenance columns to stamp on images imported from a ComfyUI run.

    All-or-nothing on purpose. ComfyUI output is self-created, but an img2img plan
    over a licensed source dataset is *derived* from that source — so a dataset
    that records *any* provenance default keeps ownership of the whole story and
    the output inherits every field (returning `{}` leaves them NULL). Stamping
    only `license`/`source_name` would instead mix a "synthetic" license onto an
    inherited real-photographer credit, and would hide a CC-BY-NC source from the
    commercial-use export filter.

    The gate is *any* dataset provenance field, not just `license`: a dataset that
    records only an `attribution` still owns the credit line, and there is no way
    to opt one field out of inheritance — `resolve_provenance` treats "" and NULL
    alike as "inherit", so stamping `source_url=""`/`attribution=""` would not
    stop them falling through to the dataset.

    Only when the dataset asserts nothing at all do we record the run's own
    provenance, leaving `source_url`/`attribution` NULL — the honest value for a
    synthetic image with no URL and nobody to credit.
    """
    if any((getattr(ds, f, None) or "").strip() for f in PROVENANCE_FIELDS):
        return {}
    return {"license": "synthetic", "source_name": "ComfyUI"}


async def _get_plan(db: AsyncSession, plan_id: str) -> ComfyPlan:
    plan = await db.get(ComfyPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return plan


def _prompt_alias(plan: ComfyPlan) -> str | None:
    """The alias of the pin marked as the prompt, if any (at most one exists)."""
    return next((p["alias"] for p in (plan.pinned_params or []) if p.get("is_prompt")), None)


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


# ── Workflow sync (pull from ComfyUI) ─────────────────────────────────────────

@router.get("/canvas-workflow")
async def canvas_workflow(db: AsyncSession = Depends(get_db)):
    """Pull the current workflow from ComfyUI.

    Tries the ComfyUI-CrucibleBridge snapshot (the live canvas, pushed by the
    browser extension) first; falls back to the last-queued history entry when
    the bridge is not installed or has no snapshot. See docs/dev/comfyui-sync.md.
    """
    thresholds = await get_thresholds(db)
    base_url = (thresholds.comfyui_url or "").strip()
    if not base_url:
        raise HTTPException(400, "No ComfyUI URL configured — set it in Settings → ComfyUI")
    client = ComfyClient(base_url)

    try:
        snap = await client.bridge_snapshot()
        if snap:
            wf = snap.get("workflow")
            if workflow_format(wf) == "api":
                return {
                    "workflow": wf,
                    "source": "bridge",
                    "name": snap.get("name"),
                    "node_count": len(wf),
                    "age_seconds": snap.get("age_seconds"),
                }
            # Bridge installed but no (valid) snapshot yet → try history.
        entry = await client.last_history_entry()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"ComfyUI unreachable at {base_url}: {e}")

    wf = history_entry_workflow(entry) if entry else None
    if wf is not None:
        return {
            "workflow": wf,
            "source": "history",
            "name": None,
            "node_count": len(wf),
            "age_seconds": None,
        }
    raise HTTPException(
        404,
        "No workflow available from ComfyUI — install the ComfyUI-CrucibleBridge "
        "extension (extras/ComfyUI-CrucibleBridge) and keep a ComfyUI browser tab "
        "open, or queue the workflow once in ComfyUI and retry",
    )


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


# ── Prompt library ────────────────────────────────────────────────────────────
# Global (no dataset/plan FK): saved prompt texts grouped by free-text category,
# reusable across plans and datasets.

def _dedupe_key(text: str) -> str:
    return " ".join(text.split()).lower()


@router.get("/library")
async def list_library_prompts(db: AsyncSession = Depends(get_db)):
    prompts = (
        await db.execute(
            select(ComfyLibraryPrompt).order_by(
                ComfyLibraryPrompt.category, ComfyLibraryPrompt.created_at
            )
        )
    ).scalars().all()
    return {
        # created_at is naive UTC (repo-wide convention) — currently ordering-only;
        # if it is ever displayed, treat it as UTC at the display point.
        "prompts": [
            {"id": p.id, "category": p.category, "text": p.text, "created_at": p.created_at.isoformat()}
            for p in prompts
        ]
    }


@router.post("/library")
async def add_library_prompts(body: LibraryAddRequest, db: AsyncSession = Depends(get_db)):
    """Add prompts to a category; case/whitespace-insensitive duplicates are skipped."""
    category = body.category.strip()
    if not category:
        raise HTTPException(400, "Category must not be empty")
    existing = (
        await db.execute(
            select(ComfyLibraryPrompt.text).where(ComfyLibraryPrompt.category == category)
        )
    ).scalars().all()
    seen = {_dedupe_key(t) for t in existing}
    created = skipped = 0
    for raw in body.prompts:
        text = raw.strip()
        if not text:
            continue
        key = _dedupe_key(text)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        db.add(ComfyLibraryPrompt(category=category, text=text))
        created += 1
    await db.commit()
    return {"created": created, "skipped": skipped}


@router.post("/library/move")
async def move_library_prompts(body: LibraryMoveRequest, db: AsyncSession = Depends(get_db)):
    """Re-categorize prompts, upholding the same per-category uniqueness as the add
    path: a moved prompt whose text already exists in the target category (or arrives
    twice in one batch) is deleted instead — merged, since it exists there either way."""
    category = body.category.strip()
    if not category:
        raise HTTPException(400, "Category must not be empty")
    prompts = (
        await db.execute(select(ComfyLibraryPrompt).where(ComfyLibraryPrompt.id.in_(body.ids)))
    ).scalars().all()
    target_texts = (
        await db.execute(
            select(ComfyLibraryPrompt.text).where(
                ComfyLibraryPrompt.category == category,
                ComfyLibraryPrompt.id.notin_(body.ids),
            )
        )
    ).scalars().all()
    seen = {_dedupe_key(t) for t in target_texts}
    # Selected prompts already in the target stay put and occupy their keys.
    for p in prompts:
        if p.category == category:
            seen.add(_dedupe_key(p.text))
    moved = merged = 0
    for p in prompts:
        if p.category == category:
            continue
        key = _dedupe_key(p.text)
        if key in seen:
            await db.delete(p)
            merged += 1
        else:
            p.category = category
            seen.add(key)
            moved += 1
    await db.commit()
    return {"moved": moved, "merged": merged}


@router.post("/library/delete")
async def delete_library_prompts(body: LibraryIdsRequest, db: AsyncSession = Depends(get_db)):
    prompts = (
        await db.execute(select(ComfyLibraryPrompt).where(ComfyLibraryPrompt.id.in_(body.ids)))
    ).scalars().all()
    for p in prompts:
        await db.delete(p)
    await db.commit()
    return {"deleted": len(prompts)}


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


def _plan_prompt_texts(plan: ComfyPlan, rows: Iterable[ComfyRow]) -> list[tuple[ComfyRow, str]]:
    """(row, effective prompt) for every row whose prompt is non-empty.

    The single definition of "what prompt does this row run with" outside the
    run itself: row value → run default → template, the same chain
    `patch_workflow` resolves. Used by the prompts listing and by the
    prompt-generation job's existing-context/dedupe seed.
    """
    workflow = plan.workflow_json or {}
    pinned = plan.pinned_params or []
    out: list[tuple[ComfyRow, str]] = []
    for r in rows:
        prompt = effective_prompt(workflow, pinned, r.values or {}) or ""
        if prompt.strip():
            out.append((r, prompt))
    return out


async def _plan_rows(db: AsyncSession, plan_id: str) -> list[ComfyRow]:
    return list(
        (
            await db.execute(
                select(ComfyRow)
                .where(ComfyRow.plan_id == plan_id)
                .order_by(ComfyRow.sort_order, ComfyRow.created_at)
            )
        ).scalars().all()
    )


@router.get("/plans/{plan_id}/prompts")
async def list_plan_prompts(plan_id: str, db: AsyncSession = Depends(get_db)):
    """Effective prompt text of each row — used to reuse prompts across plans/datasets.

    Returns the row → run-default → template prompt for every row (the same chain
    the run uses), so prompts can be browsed and imported into another plan even
    when the two plans have different pinned parameters. Rows whose effective
    prompt is empty are omitted.
    """
    plan = await _get_plan(db, plan_id)
    rows = await _plan_rows(db, plan_id)
    return {
        "prompts": [
            {"row_id": r.id, "prompt": prompt, "status": r.status}
            for r, prompt in _plan_prompt_texts(plan, rows)
        ]
    }


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
    prompt_alias = _prompt_alias(plan)
    if not prompt_alias:
        raise HTTPException(400, NO_PROMPT_PIN_MSG)
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
        parsed = await generate_prompts(
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
    return {"prompts": parsed.prompts, "model": model_name}


@router.post("/plans/{plan_id}/generate-prompts")
async def generate_prompts_job(
    plan_id: str, body: GeneratePromptsJobRequest, db: AsyncSession = Depends(get_db)
):
    """Background 'generate until N': loop LLM batches, inserting rows per batch.

    Durable counterpart of POST /comfy/generate-prompts. Because rows are
    committed as each batch lands, closing the modal (or navigating away, or a
    Stop) never discards LLM calls already paid for. Runs on the shared
    job_queue, so it serialises behind captioning/ComfyUI runs — see
    docs/dev/comfyui.md.

    Everything that could fail predictably is checked here rather than minutes
    later inside the job.
    """
    from backend.models.openai_provider import OpenAIProvider

    plan = await _get_plan(db, plan_id)
    if not _prompt_alias(plan):
        # Without this you could burn a full generation run and then be unable
        # to insert a single row.
        raise HTTPException(400, NO_PROMPT_PIN_MSG)

    provider = await db.get(OpenAIProvider, body.provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")
    if not (body.model_name.strip() or provider.default_model):
        raise HTTPException(400, "No model given and the provider has no default model")

    # One prompt-generation job at a time per plan (mirrors the run guard).
    active = (
        await db.execute(
            select(BackgroundJob).where(
                BackgroundJob.job_type == "comfy_prompts",
                BackgroundJob.status.in_(("pending", "running")),
            )
        )
    ).scalars().all()
    if any((j.config or {}).get("plan_id") == plan_id for j in active):
        raise HTTPException(409, "A prompt-generation job for this plan is already queued or running")

    existing_count = len(_plan_prompt_texts(plan, await _plan_rows(db, plan_id)))
    if existing_count >= body.target_count:
        raise HTTPException(
            400,
            f"The plan already holds {existing_count} prompt{'s' if existing_count != 1 else ''} — "
            f"raise the target above that to generate more",
        )
    to_generate = body.target_count - existing_count

    # plan_id is a path param, so model_dump() omits it — but the 409 guard above
    # reads config["plan_id"], so inject it explicitly.
    config = body.model_dump()
    config["plan_id"] = plan_id
    auto_label = f"Prompts: {plan.name} — {to_generate} more (to {body.target_count})"
    job = BackgroundJob(
        job_type="comfy_prompts",
        label=body.label or auto_label,
        dataset_id=plan.dataset_id,
        total_items=to_generate,
        config=config,
    )
    db.add(job)
    await db.commit()

    dataset_id = plan.dataset_id
    provider_id = body.provider_id
    requested_model = body.model_name.strip()
    instruction = body.instruction
    system_instructions = body.system_instructions
    batch_size = body.batch_size
    temperature = body.temperature
    target_count = body.target_count
    use_existing_context = body.use_existing_context

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.ml.prompt_generator import generate_prompts
        from backend.models.openai_provider import OpenAIProvider as Provider
        from backend.workers.progress import broadcaster

        async with AsyncSessionLocal() as session:
            # Re-check on dequeue: the plan, its prompt pin and the provider can
            # all change while the job waits behind others in the queue. Raise
            # rather than return — a silent return is reported as success.
            plan_row = await session.get(ComfyPlan, plan_id)
            if not plan_row:
                raise RuntimeError("The plan was deleted before this job started")
            alias = _prompt_alias(plan_row)
            if not alias:
                raise RuntimeError("The plan's prompt pin was removed before this job started")
            prov = await session.get(Provider, provider_id)
            if not prov:
                raise RuntimeError("The LLM provider was deleted before this job started")
            model_name = requested_model or prov.default_model
            if not model_name:
                raise RuntimeError("The provider no longer has a default model")

            existing = [t for _, t in _plan_prompt_texts(plan_row, await _plan_rows(session, plan_id))]
            total = target_count - len(existing)
            if total <= 0:
                # Rows were added while the job waited in the queue. Fail loudly
                # rather than "complete" having created nothing.
                raise RuntimeError(
                    f"The plan already holds {len(existing)} prompts (target was {target_count}) — "
                    f"nothing to generate"
                )
            # Dedupe covers existing rows AND everything generated this run — the
            # old client-side loop only deduped against the review textarea.
            seen = {t.strip().lower() for t in existing}
            generated: list[str] = []
            created = 0
            filtered_total = 0
            calls = 0
            stop_reason = "target"
            error_note = ""
            # Every iteration is a paid call and duplicates are dropped, so a
            # model trickling near-duplicates must not loop forever.
            max_calls = max(12, math.ceil(total / batch_size) * 3)

            async def _emit(done: int, message: str) -> None:
                await broadcaster.emit(job_id, {
                    "type": "progress",
                    "job_id": job_id,
                    "job_type": "comfy_prompts",
                    "status": "running",
                    "done": done,
                    "total": total,
                    "percent": round(done / total * 100, 1) if total else 100.0,
                    "message": message,
                    "dataset_id": dataset_id,
                    # plan_id/requested ride the running events only; the
                    # worker's terminal event carries neither, but jobStore
                    # merges by job id so they persist client-side.
                    "plan_id": plan_id,
                    "requested": total,
                })

            # Emitted BEFORE the first call, which can take 120 s: this is the
            # first event carrying plan_id, and TopBar's invalidation needs it.
            await _emit(0, f"Generating prompts (0/{total})")

            while created < total and calls < max_calls:
                if job_queue.cancel_requested(job_id):
                    stop_reason = "cancelled"
                    break
                calls += 1
                context = (existing if use_existing_context else []) + generated
                try:
                    parsed = await generate_prompts(
                        base_url=prov.base_url,
                        api_key=prov.api_key,
                        model_name=model_name,
                        instruction=instruction,
                        system_instructions=system_instructions,
                        batch_size=batch_size,
                        existing=context,
                        temperature=temperature,
                        max_tokens=prov.max_tokens,
                    )
                except Exception as e:
                    # Rows already committed are real. Discarding them because
                    # call 8 of 10 timed out is the worse error.
                    if created:
                        logger.warning("comfy_prompts: stopping after provider error", exc_info=True)
                        stop_reason = "provider_error"
                        error_note = str(e)
                        break
                    raise RuntimeError(f"Prompt generation failed: {e}")

                filtered_total += parsed.filtered
                new: list[str] = []
                for p in parsed.prompts:
                    text = p.strip()
                    key = text.lower()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    new.append(text)
                    if created + len(new) >= total:
                        break  # don't overshoot the target within a batch
                if not new:
                    stop_reason = "no_new"
                    break

                order = await _next_sort_order(session, plan_id)
                for i, text in enumerate(new):
                    session.add(ComfyRow(plan_id=plan_id, sort_order=order + i, values={alias: text}))
                generated.extend(new)
                created += len(new)
                await session.commit()
                await _emit(created, f"Generated {created}/{total} prompts")

                # A Stop arriving during an LLM call still yields prompts that
                # were paid for, so check again after committing them. Guarded on
                # created < total because only a cancel that actually cut the run
                # short is a cancel: reaching the target on the same iteration a
                # Stop lands is a completed job, and the loop condition ends it
                # either way — this keeps the reported outcome honest.
                if created < total and job_queue.cancel_requested(job_id):
                    stop_reason = "cancelled"
                    break

            if created < total and stop_reason == "target":
                stop_reason = "call_cap"

            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = {
                    "created": created,
                    "requested": total,
                    "filtered": filtered_total,
                    "calls": calls,
                    "stop_reason": stop_reason,
                    "plan_id": plan_id,
                }
                # The worker re-reads total_items after _run and emits it as both
                # done and total; leaving it at the request would render 20/20
                # for a run that made 7. The shortfall lives in result_data.
                job_row.total_items = created
            await session.commit()

            # Commit first, THEN raise: a plain return here would let the worker
            # overwrite the user's cancel with "completed".
            if stop_reason == "cancelled":
                job_queue.raise_if_cancelled(job_id)

            if created == 0:
                # PM-004: "_run returned without raising" is a proxy for
                # "prompts were generated" — generate_prompts returns [] without
                # raising when the model emits unparseable prose. Report the
                # actual state, and keep the prefix identical to the sync path's
                # 502 so both read the same.
                reasons = {
                    "no_new": "the model returned no new prompts — try rephrasing the request "
                              "or lowering the temperature",
                    "call_cap": f"the model kept returning duplicates ({calls} calls, no new prompts)",
                    "provider_error": error_note,
                }
                raise RuntimeError(
                    f"Prompt generation failed: {reasons.get(stop_reason) or 'no prompts were generated'}"
                )

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": to_generate}


@router.post("/plans/{plan_id}/rows/bulk-edit")
async def bulk_edit_rows(plan_id: str, body: RowsBulkEditRequest, db: AsyncSession = Depends(get_db)):
    """Bulk text operation on the prompt column (mirrors caption bulk-edit semantics).

    The base text per row is its effective prompt (row value → run default →
    template); the result is written into the row's values, and changed
    completed/failed rows reset to pending. Returns {affected, skipped}.
    """
    if body.operation not in BULK_EDIT_OPS:
        raise HTTPException(400, f"operation must be one of {BULK_EDIT_OPS}")
    plan = await _get_plan(db, plan_id)
    pinned = plan.pinned_params or []
    prompt_alias = _prompt_alias(plan)
    if not prompt_alias:
        raise HTTPException(400, NO_PROMPT_PIN_MSG)
    workflow = plan.workflow_json or {}

    stmt = select(ComfyRow).where(ComfyRow.plan_id == plan_id)
    if body.row_ids is not None:
        stmt = stmt.where(ComfyRow.id.in_(body.row_ids))
    rows = (await db.execute(stmt)).scalars().all()

    if body.use_regex:
        try:
            pattern = compile_user_regex(body.text)
        except regex_error as e:
            raise HTTPException(400, f"Invalid regex: {e}")

    bases = [(row, effective_prompt(workflow, pinned, row.values or {}) or "") for row in rows]

    def _transform() -> list[tuple[ComfyRow, str, str]]:
        # One deadline for the whole batch, enforced inside the regex engine.
        deadline = time.monotonic() + REGEX_TIMEOUT_SECONDS
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
                new = regex_sub_deadline(pattern, repl, base, deadline)
            else:
                repl = "" if body.operation == "remove" else body.replacement
                new = base.replace(body.text, repl)
            out.append((row, base, new))
        return out

    # Offload so a long batch doesn't stall the event loop; `regex` releases the GIL
    # during matching, so this genuinely runs concurrently and the deadline is real.
    try:
        transformed = await asyncio.get_running_loop().run_in_executor(None, _transform)
    except TimeoutError:
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
        from backend.services.dataset_service import (
            RegisteredFile,
            _register_file_sync,
            refresh_stats,
        )
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

            output_provenance = _comfy_output_provenance(ds)

            client = ComfyClient(comfy_url)
            loop = asyncio.get_running_loop()
            created_image_ids: list[str] = []
            failed_row_ids: list[str] = []
            consecutive_connect_errors = 0

            def _write_and_register(data: bytes, dest: Path, thumb: str) -> RegisteredFile:
                dest.write_bytes(data)
                # `.provenance` is deliberately ignored: ComfyUI output has no
                # sidecar/EXIF provenance worth capturing and sets its own below.
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
                        reg = await loop.run_in_executor(
                            None, _write_and_register, data, dest, thumb_path
                        )
                        gen_meta = reg.gen_meta
                        img = Image(
                            dataset_id=ds.id,
                            filename=new_name,
                            original_filename=ref["filename"],
                            subfolder=subfolder,
                            file_path=str(dest),
                            thumbnail_path=thumb_path,
                            generation_metadata=gen_meta,
                            source_meta=_comfy_source_meta(plan_row, row, workflow),
                            **output_provenance,
                            **reg.info,
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
                except Exception as e:
                    # Deliberately broad: an unexpected error (a refactor breaking a
                    # helper's signature, a PIL failure, a DB error) must still run the
                    # per-row cleanup below rather than orphan the files and Image rows
                    # this row already wrote and leave the row wedged at "running".
                    # CancelledError is handled above and re-raised; it is a
                    # BaseException on 3.8+ so it never reaches here.
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
