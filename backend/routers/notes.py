"""Dataset notes and app-wide notes (M12 phase 1). See docs/dev/analyst/notes.md.

Removal is a soft delete, so `DELETE` returns the item and `/restore` brings it back.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Dataset
from backend.models.notes import AppNoteItem, DatasetNoteItem
from backend.schemas.health import AppNoteCreate, DatasetNoteCreate, NoteItemOut, NotesOut, NoteUpdate
from backend.services import notes_service

router = APIRouter(tags=["notes"])


async def _require_dataset(db: AsyncSession, dataset_id: str) -> None:
    if await db.get(Dataset, dataset_id) is None:
        raise HTTPException(404, "Dataset not found")


async def _notes_out(db: AsyncSession, model, dataset_id: str | None, include_deleted: bool) -> NotesOut:
    items = await notes_service.list_items(db, model, dataset_id, include_deleted=include_deleted)
    return NotesOut(
        items=[NoteItemOut.model_validate(i) for i in items],
        used_chars=await notes_service.used_chars(db, model, dataset_id),
        cap_chars=notes_service.cap_for(model),
    )


async def _apply_update(db: AsyncSession, item, body: NoteUpdate):
    return await notes_service.edit_item(
        db,
        item,
        text=body.text,
        position=body.position,
        decided_at=body.decided_at,
        set_decided_at="decided_at" in body.model_fields_set,
    )


# --- Dataset notes -------------------------------------------------------------


@router.get("/datasets/{dataset_id}/notes", response_model=NotesOut)
async def list_dataset_notes(
    dataset_id: str,
    include_deleted: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    await _require_dataset(db, dataset_id)
    return await _notes_out(db, DatasetNoteItem, dataset_id, include_deleted)


@router.post("/datasets/{dataset_id}/notes", response_model=NoteItemOut, status_code=201)
async def add_dataset_note(dataset_id: str, body: DatasetNoteCreate, db: AsyncSession = Depends(get_db)):
    await _require_dataset(db, dataset_id)
    return await notes_service.add_item(
        db, DatasetNoteItem, section=body.section, text=body.text,
        dataset_id=dataset_id, decided_at=body.decided_at,
    )


@router.patch("/datasets/{dataset_id}/notes/{item_id}", response_model=NoteItemOut)
async def edit_dataset_note(dataset_id: str, item_id: str, body: NoteUpdate, db: AsyncSession = Depends(get_db)):
    item = await notes_service.get_item(db, DatasetNoteItem, item_id, dataset_id)
    return await _apply_update(db, item, body)


@router.delete("/datasets/{dataset_id}/notes/{item_id}", response_model=NoteItemOut)
async def remove_dataset_note(dataset_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    item = await notes_service.get_item(db, DatasetNoteItem, item_id, dataset_id)
    return await notes_service.remove_item(db, item)


@router.post("/datasets/{dataset_id}/notes/{item_id}/restore", response_model=NoteItemOut)
async def restore_dataset_note(dataset_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    item = await notes_service.get_item(db, DatasetNoteItem, item_id, dataset_id)
    return await notes_service.restore_item(db, item)


# --- App-wide notes ("About me") -------------------------------------------------


@router.get("/app-notes", response_model=NotesOut)
async def list_app_notes(include_deleted: bool = Query(False), db: AsyncSession = Depends(get_db)):
    return await _notes_out(db, AppNoteItem, None, include_deleted)


@router.post("/app-notes", response_model=NoteItemOut, status_code=201)
async def add_app_note(body: AppNoteCreate, db: AsyncSession = Depends(get_db)):
    return await notes_service.add_item(db, AppNoteItem, section=body.section, text=body.text)


@router.patch("/app-notes/{item_id}", response_model=NoteItemOut)
async def edit_app_note(item_id: str, body: NoteUpdate, db: AsyncSession = Depends(get_db)):
    item = await notes_service.get_item(db, AppNoteItem, item_id)
    return await _apply_update(db, item, body)


@router.delete("/app-notes/{item_id}", response_model=NoteItemOut)
async def remove_app_note(item_id: str, db: AsyncSession = Depends(get_db)):
    item = await notes_service.get_item(db, AppNoteItem, item_id)
    return await notes_service.remove_item(db, item)


@router.post("/app-notes/{item_id}/restore", response_model=NoteItemOut)
async def restore_app_note(item_id: str, db: AsyncSession = Depends(get_db)):
    item = await notes_service.get_item(db, AppNoteItem, item_id)
    return await notes_service.restore_item(db, item)
