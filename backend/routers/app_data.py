"""User-written data shared by every browser that opens this install: caption prompt
presets, declared empty categories and the captioning workflow, plus the one-time
import of what each browser held before this data moved here.

See docs/dev/persistence.md § Data stored on the server.
"""
import asyncio
import json
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.app_data import CaptionPromptPreset, DeclaredCategory
from backend.models.comfy import ComfyPlan
from backend.models.threshold_settings import ThresholdSettings
from backend.schemas.app_data import (
    CaptioningWorkflowPut,
    CategoryCreate,
    CategoryName,
    CategoryRename,
    ComfyGenSettings,
    LegacyImportRequest,
    PresetCreate,
)
from backend.services.threshold_service import DEFAULTS

router = APIRouter(prefix="/app-data", tags=["app-data"])

MAX_WORKFLOW_BYTES = 1_000_000

_import_lock = asyncio.Lock()


def _preset_out(preset: CaptionPromptPreset) -> dict:
    return {
        "id": preset.id,
        "name": preset.name,
        "model": preset.model,
        "style": preset.style,
        "prompt": preset.prompt,
    }


def _workflow_fits(value: dict) -> bool:
    return len(json.dumps(value)) <= MAX_WORKFLOW_BYTES


async def _ensure_settings_row(db: AsyncSession) -> None:
    """The singleton row doesn't exist until something first writes it."""
    await db.execute(
        sqlite_insert(ThresholdSettings).values(**DEFAULTS).on_conflict_do_nothing(index_elements=["id"])
    )


# ── Presets ───────────────────────────────────────────────────────────────────

@router.get("/presets")
async def list_presets(db: AsyncSession = Depends(get_db)):
    presets = (
        await db.execute(select(CaptionPromptPreset).order_by(CaptionPromptPreset.created_at))
    ).scalars().all()
    return [_preset_out(p) for p in presets]


@router.post("/presets")
async def create_preset(body: PresetCreate, db: AsyncSession = Depends(get_db)):
    preset = CaptionPromptPreset(**body.model_dump())
    db.add(preset)
    await db.commit()
    return _preset_out(preset)


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: str, db: AsyncSession = Depends(get_db)):
    preset = await db.get(CaptionPromptPreset, preset_id)
    if not preset:
        raise HTTPException(404, "Preset not found")
    await db.delete(preset)
    await db.commit()
    return {"deleted": 1}


# ── Declared categories ───────────────────────────────────────────────────────
# Names travel in the body or query, never the path: a category name can contain "/".

async def _insert_category(db: AsyncSession, name: str) -> bool:
    result = await db.execute(
        sqlite_insert(DeclaredCategory).values(name=name).on_conflict_do_nothing(index_elements=["name"])
    )
    return bool(result.rowcount)


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)):
    names = (
        await db.execute(select(DeclaredCategory.name).order_by(DeclaredCategory.name))
    ).scalars().all()
    return list(names)


@router.post("/categories")
async def create_category(body: CategoryCreate, db: AsyncSession = Depends(get_db)):
    created = await _insert_category(db, body.name)
    await db.commit()
    return {"name": body.name, "created": created}


@router.post("/categories/rename")
async def rename_category(body: CategoryRename, db: AsyncSession = Depends(get_db)):
    """Follows a category rename. A name that was never declared belongs to datasets
    alone, so there is nothing to do and the new name is not declared either."""
    declared = await db.get(DeclaredCategory, body.old_name)
    if not declared:
        return {"renamed": False}
    if body.new_name != body.old_name:
        await db.delete(declared)
        await db.flush()
        await _insert_category(db, body.new_name)
        await db.commit()
    return {"renamed": True}


@router.delete("/categories")
async def delete_category(name: CategoryName, db: AsyncSession = Depends(get_db)):
    declared = await db.get(DeclaredCategory, name)
    if declared:
        await db.delete(declared)
        await db.commit()
    return {"deleted": 1 if declared else 0}


# ── Captioning workflow ───────────────────────────────────────────────────────

@router.get("/captioning-workflow")
async def get_captioning_workflow(db: AsyncSession = Depends(get_db)):
    value = (
        await db.execute(select(ThresholdSettings.captioning_workflow).where(ThresholdSettings.id == 1))
    ).scalar_one_or_none()
    return {"value": value}


@router.put("/captioning-workflow")
async def put_captioning_workflow(body: CaptioningWorkflowPut, db: AsyncSession = Depends(get_db)):
    if not _workflow_fits(body.value):
        raise HTTPException(413, "Captioning workflow is too large")
    await _ensure_settings_row(db)
    await db.execute(
        update(ThresholdSettings).where(ThresholdSettings.id == 1).values(captioning_workflow=body.value)
    )
    await db.commit()
    return {"value": body.value}


@router.delete("/captioning-workflow")
async def delete_captioning_workflow(db: AsyncSession = Depends(get_db)):
    await db.execute(
        update(ThresholdSettings).where(ThresholdSettings.id == 1).values(captioning_workflow=sa.null())
    )
    await db.commit()
    return {"value": None}


# ── One-time import from browser storage ──────────────────────────────────────
# Re-running an import changes nothing, which is what makes a crash before the browser
# records its marker safe.

async def _import_presets(db: AsyncSession, entries: list[Any]) -> dict:
    rows = await db.execute(
        select(
            CaptionPromptPreset.name,
            CaptionPromptPreset.model,
            CaptionPromptPreset.style,
            CaptionPromptPreset.prompt,
        )
    )
    seen = {tuple(row) for row in rows.all()}
    created = skipped = 0
    rejected: list[int] = []
    for index, entry in enumerate(entries):
        try:
            preset = PresetCreate.model_validate(entry)
        except ValidationError:
            rejected.append(index)
            continue
        key = (preset.name, preset.model, preset.style, preset.prompt)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        db.add(CaptionPromptPreset(**preset.model_dump()))
        created += 1
    return {"created": created, "skipped": skipped, "rejected": rejected}


async def _import_categories(db: AsyncSession, entries: list[Any]) -> dict:
    created = 0
    rejected: list[int] = []
    for index, entry in enumerate(entries):
        try:
            category = CategoryCreate(name=entry)
        except ValidationError:
            rejected.append(index)
            continue
        created += await _insert_category(db, category.name)
    return {"created": created, "rejected": rejected}


async def _import_workflow(db: AsyncSession, value: Any) -> str:
    if not isinstance(value, dict) or not _workflow_fits(value):
        return "rejected"
    await _ensure_settings_row(db)
    result = await db.execute(
        update(ThresholdSettings)
        .where(ThresholdSettings.id == 1, ThresholdSettings.captioning_workflow.is_(sa.null()))
        .values(captioning_workflow=value)
    )
    if result.rowcount:
        return "stored"
    current = (
        await db.execute(select(ThresholdSettings.captioning_workflow).where(ThresholdSettings.id == 1))
    ).scalar_one()
    return "same" if current == value else "conflict"


async def _import_comfy_settings(db: AsyncSession, entries: dict[str, Any]) -> dict[str, str]:
    known = set(
        (await db.execute(select(ComfyPlan.id).where(ComfyPlan.id.in_(entries)))).scalars().all()
    )
    report: dict[str, str] = {}
    for plan_id, entry in entries.items():
        if plan_id not in known:
            report[plan_id] = "discarded"
            continue
        try:
            value = ComfyGenSettings.model_validate(entry).model_dump()
        except ValidationError:
            report[plan_id] = "rejected"
            continue
        result = await db.execute(
            update(ComfyPlan)
            .where(ComfyPlan.id == plan_id, ComfyPlan.gen_settings.is_(sa.null()))
            # Pinned to itself to suppress `onupdate`: see put_gen_settings in routers/comfy.py.
            .values(gen_settings=value, updated_at=ComfyPlan.updated_at)
        )
        if result.rowcount:
            report[plan_id] = "stored"
            continue
        current = (
            await db.execute(select(ComfyPlan.gen_settings).where(ComfyPlan.id == plan_id))
        ).scalar_one()
        report[plan_id] = "same" if current == value else "conflict"
    return report


@router.post("/legacy-import")
async def legacy_import(body: LegacyImportRequest, db: AsyncSession = Depends(get_db)):
    """One transaction: the browser records a section as done only after this commits,
    so a section is either fully here or retried on its next load.

    Serialized because presets have no unique constraint to fall back on: two tabs
    loading together would each read "no match" and insert the same preset."""
    report: dict[str, Any] = {}
    async with _import_lock:
        if body.presets is not None:
            report["presets"] = await _import_presets(db, body.presets)
        if body.categories is not None:
            report["categories"] = await _import_categories(db, body.categories)
        if body.captioning_workflow is not None:
            report["captioning_workflow"] = await _import_workflow(db, body.captioning_workflow)
        if body.comfy_gen_settings is not None:
            report["comfy_gen_settings"] = await _import_comfy_settings(db, body.comfy_gen_settings)
        await db.commit()
    return report
