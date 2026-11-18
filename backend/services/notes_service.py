"""Dataset notes and app-wide notes (M12): add, edit, soft delete, restore, and the
dismissal of a health finding, which is a note item too.

Every change to an item's text or presence (add, edit, remove, restore) records a
`NoteItemHistory` row in the same commit: soft delete plus that table is the undo. A
reorder or a new `decided_at` changes no text and records none. Each scope has a size cap, counted
over its live items, so the notes always fit beside the findings in a small model's
context. See docs/dev/analyst/notes.md.
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.notes import AppNoteItem, DatasetNoteItem, NoteItemHistory

# Characters of live note text per scope, enforced here (a 422 past it).
DATASET_NOTES_CAP_CHARS = 4000
APP_NOTES_CAP_CHARS = 2000

NoteItem = DatasetNoteItem | AppNoteItem


def _naive_utc(value: datetime | None) -> datetime | None:
    """Stored columns are naive UTC; a client may send an offset."""
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _scope_filter(model, dataset_id: str | None):
    conds = [model.deleted_at.is_(None)]
    if model is DatasetNoteItem:
        conds.append(DatasetNoteItem.dataset_id == dataset_id)
    return conds


async def used_chars(db: AsyncSession, model, dataset_id: str | None = None) -> int:
    total = await db.scalar(
        select(func.coalesce(func.sum(func.length(model.text)), 0)).where(*_scope_filter(model, dataset_id))
    )
    return int(total or 0)


def cap_for(model) -> int:
    return DATASET_NOTES_CAP_CHARS if model is DatasetNoteItem else APP_NOTES_CAP_CHARS


async def _ensure_fits(db: AsyncSession, model, dataset_id: str | None, adding: int, removing: int = 0) -> None:
    cap = cap_for(model)
    after = await used_chars(db, model, dataset_id) - removing + adding
    if after > cap:
        raise HTTPException(
            422,
            f"Notes are limited to {cap} characters for this scope; this change would make "
            f"them {after}. Remove or shorten an item first.",
        )


async def list_items(db: AsyncSession, model, dataset_id: str | None = None, *, include_deleted: bool = False):
    q = select(model)
    if model is DatasetNoteItem:
        q = q.where(DatasetNoteItem.dataset_id == dataset_id)
    if not include_deleted:
        q = q.where(model.deleted_at.is_(None))
    q = q.order_by(model.section, model.position, model.created_at)
    return list((await db.execute(q)).scalars().all())


def _history(item: NoteItem, op: str, before: str | None, after: str | None, actor: str) -> NoteItemHistory:
    is_dataset = isinstance(item, DatasetNoteItem)
    return NoteItemHistory(
        dataset_item_id=item.id if is_dataset else None,
        app_item_id=None if is_dataset else item.id,
        op=op,
        before_text=before,
        after_text=after,
        actor=actor,
    )


async def _next_position(db: AsyncSession, model, section: str, dataset_id: str | None) -> int:
    q = select(func.max(model.position)).where(model.section == section, *_scope_filter(model, dataset_id))
    top = await db.scalar(q)
    return 0 if top is None else top + 1


async def add_item(
    db: AsyncSession,
    model,
    *,
    section: str,
    text: str,
    dataset_id: str | None = None,
    decided_at: datetime | None = None,
    finding_id: str | None = None,
    fingerprint: dict | None = None,
    permanent: bool = False,
    actor: str = "user",
    commit: bool = True,
) -> NoteItem:
    text = text.strip()
    if not text:
        raise HTTPException(422, "A note cannot be empty")
    await _ensure_fits(db, model, dataset_id, len(text))
    fields = dict(
        section=section,
        text=text,
        position=await _next_position(db, model, section, dataset_id),
    )
    if model is DatasetNoteItem:
        fields.update(
            dataset_id=dataset_id,
            decided_at=_naive_utc(decided_at) if section == "decision" else None,
            finding_id=finding_id,
            fingerprint=fingerprint,
            permanent=permanent,
        )
    item = model(**fields)
    db.add(item)
    await db.flush()
    db.add(_history(item, "add", None, text, actor))
    if commit:
        await db.commit()
        await db.refresh(item)
    return item


async def get_item(db: AsyncSession, model, item_id: str, dataset_id: str | None = None) -> NoteItem:
    item = await db.get(model, item_id)
    if item is None or (model is DatasetNoteItem and item.dataset_id != dataset_id):
        raise HTTPException(404, "Note not found")
    return item


async def edit_item(
    db: AsyncSession,
    item: NoteItem,
    *,
    text: str | None = None,
    position: int | None = None,
    decided_at: datetime | None = None,
    set_decided_at: bool = False,
    actor: str = "user",
) -> NoteItem:
    if item.deleted_at is not None:
        raise HTTPException(409, "Restore the note before editing it")
    model = type(item)
    dataset_id = getattr(item, "dataset_id", None)
    if text is not None:
        text = text.strip()
        if not text:
            raise HTTPException(422, "A note cannot be empty")
        if text != item.text:
            await _ensure_fits(db, model, dataset_id, len(text), removing=len(item.text))
            db.add(_history(item, "edit", item.text, text, actor))
            item.text = text
    if position is not None and position != item.position:
        await _move(db, item, position)
    if set_decided_at and isinstance(item, DatasetNoteItem) and item.section == "decision":
        item.decided_at = _naive_utc(decided_at)
    await db.commit()
    await db.refresh(item)
    return item


async def _move(db: AsyncSession, item: NoteItem, position: int) -> None:
    """Put `item` at `position` within its section, renumbering the others 0..n-1."""
    model = type(item)
    dataset_id = getattr(item, "dataset_id", None)
    siblings = [
        s for s in await list_items(db, model, dataset_id)
        if s.section == item.section and s.id != item.id
    ]
    position = min(position, len(siblings))
    siblings.insert(position, item)
    for i, s in enumerate(siblings):
        if s.position != i:
            s.position = i


async def remove_item(db: AsyncSession, item: NoteItem, *, actor: str = "user", commit: bool = True) -> NoteItem:
    if item.deleted_at is None:
        item.deleted_at = datetime.utcnow()
        db.add(_history(item, "remove", item.text, None, actor))
    if commit:
        await db.commit()
        await db.refresh(item)
    return item


async def restore_item(db: AsyncSession, item: NoteItem, *, actor: str = "user") -> NoteItem:
    if item.deleted_at is not None:
        model = type(item)
        if isinstance(item, DatasetNoteItem) and item.finding_id:
            newer = await db.scalar(select(func.count()).where(
                DatasetNoteItem.dataset_id == item.dataset_id,
                DatasetNoteItem.finding_id == item.finding_id,
                DatasetNoteItem.deleted_at.is_(None),
            ))
            if newer:
                raise HTTPException(
                    409, "This finding has a newer dismissal; remove it before restoring this one"
                )
        await _ensure_fits(db, model, getattr(item, "dataset_id", None), len(item.text))
        item.deleted_at = None
        item.position = await _next_position(db, model, item.section, getattr(item, "dataset_id", None))
        db.add(_history(item, "restore", None, item.text, actor))
    await db.commit()
    await db.refresh(item)
    return item


# --- Dismissals ----------------------------------------------------------------


async def live_dismissals(db: AsyncSession, dataset_id: str) -> dict[str, DatasetNoteItem]:
    """finding id -> its live dismissal (the newest, if several)."""
    rows = (await db.execute(
        select(DatasetNoteItem)
        .where(
            DatasetNoteItem.dataset_id == dataset_id,
            DatasetNoteItem.finding_id.isnot(None),
            DatasetNoteItem.deleted_at.is_(None),
        )
        .order_by(DatasetNoteItem.created_at)
    )).scalars().all()
    return {r.finding_id: r for r in rows}


async def dismissal_validator(db: AsyncSession, dataset_id: str) -> tuple:
    """Changes whenever a dismissal is added, edited, removed or restored: part of the
    health report's cache key. Every such write goes through the ORM, which bumps
    `updated_at`, and deleted rows are counted too, so a removal changes it."""
    row = (await db.execute(
        select(func.count(), func.max(DatasetNoteItem.updated_at)).where(
            DatasetNoteItem.dataset_id == dataset_id,
            DatasetNoteItem.finding_id.isnot(None),
        )
    )).one()
    return (row[0], row[1])


async def dismiss(
    db: AsyncSession,
    dataset_id: str,
    *,
    finding_id: str,
    title: str,
    reason: str,
    permanent: bool,
    fingerprint: dict,
) -> DatasetNoteItem:
    """Write the dismissal as a `preference` item, replacing any live one for the same
    finding, so a finding never has two reasons."""
    for old in (await db.execute(
        select(DatasetNoteItem).where(
            DatasetNoteItem.dataset_id == dataset_id,
            DatasetNoteItem.finding_id == finding_id,
            DatasetNoteItem.deleted_at.is_(None),
        )
    )).scalars().all():
        await remove_item(db, old, commit=False)
    await db.flush()
    text = reason.strip() or f"Dismissed the health finding “{title}”."
    return await add_item(
        db,
        DatasetNoteItem,
        section="preference",
        text=text,
        dataset_id=dataset_id,
        finding_id=finding_id,
        fingerprint=fingerprint,
        permanent=permanent,
    )

