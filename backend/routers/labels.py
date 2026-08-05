"""The global label vocabulary and the single attach/detach endpoint.

CRUD is inline here rather than in a service, following `routers/providers.py` —
the closest precedent, a small global vocabulary table. Everything with a second
call site (the filter clause, the bulk reads and writes) lives in
`services/label_service.py` instead, so the gallery and export filters cannot
drift apart.

Naming: `label` is already taken by `Detection.label` and `BackgroundJob.label`,
and every export request body already carries `label: str | None`. So no bare
`labels` field appears on any request body or query param here — the assign body
uses `add`/`remove` and the filters are `label_filter`/`label_match`/
`label_missing`.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.image import Image
from backend.models.label import ImageLabel, Label
from backend.routers import images as images_router
from backend.schemas.label import (
    LabelAssignRequest,
    LabelCreate,
    LabelOut,
    LabelReorderRequest,
    LabelUpdate,
)
from backend.services.label_service import ROWS_PER_STATEMENT

router = APIRouter(prefix="/labels", tags=["labels"])

_HOTKEY_RE = re.compile(r"^[a-z0-9]$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_NAME_MAX = 64
_DEFAULT_COLOR = "#6b7280"


def _clean_hotkey(value: str | None) -> str | None:
    """Normalize a hotkey to a single lowercase [a-z0-9], or None. 400 otherwise.

    The charset is what makes hotkey conflicts *structural*: it cannot express
    Escape, Space, ArrowLeft/Right or Delete — the keys `ImageDetailPage`
    already binds — so no reserved-key blocklist is needed and none can go stale
    when a sixth binding is added.
    """
    if value is None:
        return None
    v = value.strip().lower()
    if v == "":
        return None
    if not _HOTKEY_RE.match(v):
        raise HTTPException(400, "hotkey must be a single character in [a-z0-9]")
    return v


def _clean_color(value: str | None) -> str:
    """Normalize a swatch to a `#rgb`/`#rrggbb`/`#rrggbbaa` hex string. 400 otherwise.

    Validated rather than trusted because the value is interpolated straight into
    inline CSS on every chip and card: `Label.color` is a `String(16)` SQLite does
    not enforce, and an unchecked `url(http://…)` would become one remote fetch
    per rendered card.
    """
    v = (value or "").strip() or _DEFAULT_COLOR
    if not _COLOR_RE.match(v):
        raise HTTPException(400, "color must be a hex value like #6b7280")
    return v


def _clean_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise HTTPException(400, "Label name cannot be blank")
    if len(name) > _NAME_MAX:
        raise HTTPException(400, f"Label name cannot exceed {_NAME_MAX} characters")
    return name


async def _assert_name_free(db: AsyncSession, name: str, exclude_id: str | None = None) -> None:
    """Case-insensitive uniqueness — SQLite's default collation is case-sensitive,
    so the column's `unique=True` alone would let "Reject" and "reject" coexist."""
    q = select(Label.id).where(func.lower(Label.name) == name.lower())
    if exclude_id:
        q = q.where(Label.id != exclude_id)
    if (await db.execute(q)).scalar_one_or_none() is not None:
        raise HTTPException(409, f"A label named '{name}' already exists")


async def _assert_hotkey_free(db: AsyncSession, hotkey: str, exclude_id: str | None = None) -> None:
    q = select(Label.name).where(Label.hotkey == hotkey)
    if exclude_id:
        q = q.where(Label.id != exclude_id)
    owner = (await db.execute(q)).scalar_one_or_none()
    if owner is not None:
        raise HTTPException(409, f"Hotkey '{hotkey}' is already used by '{owner}'")


async def _commit_unique(db: AsyncSession, name: str, hotkey: str | None) -> None:
    """Commit, turning a lost uniqueness race into the same 409 the pre-check gives.

    `_assert_name_free`/`_assert_hotkey_free` read then write, so two concurrent
    creates can both pass the check and one then hits the unique index. No router
    in the repo catches `IntegrityError` and `main.py` registers no handler for it,
    so without this the loser gets a 500 for something the caller can act on. An
    app-wide handler is the tempting alternative and changes behaviour everywhere;
    this stays local to the two writers that have a pre-check to be raced.
    """
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Which constraint fired is not worth a second round-trip: the caller is
        # re-submitting a form that names both.
        detail = f"A label named '{name}' already exists"
        if hotkey:
            detail += f", or hotkey '{hotkey}' is already taken"
        raise HTTPException(409, detail)


async def _usage_counts(db: AsyncSession) -> dict[str, int]:
    rows = (await db.execute(
        select(ImageLabel.label_id, func.count(ImageLabel.image_id)).group_by(ImageLabel.label_id)
    )).all()
    return {label_id: count for label_id, count in rows}


def _out(row: Label, counts: dict[str, int]) -> LabelOut:
    item = LabelOut.model_validate(row)
    item.usage_count = counts.get(row.id, 0)
    return item


# ---------------------------------------------------------------------------
# Collection routes. All of these must stay **above** `/{label_id}`, or FastAPI
# matches "counts"/"assign"/"reorder" as a label id.
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[LabelOut])
async def list_labels(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Label).order_by(Label.sort_order, Label.name)
    )).scalars().all()
    counts = await _usage_counts(db)
    return [_out(r, counts) for r in rows]


@router.get("/counts")
async def label_counts(dataset_id: str, db: AsyncSession = Depends(get_db)):
    """`{label_id: image count}` within one dataset — the gallery chip badges.

    Scoped through `images.dataset_id`; `image_labels` deliberately carries no
    `dataset_id` of its own (see the model docstring).
    """
    rows = (await db.execute(
        select(ImageLabel.label_id, func.count(ImageLabel.image_id))
        .join(Image, Image.id == ImageLabel.image_id)
        .where(Image.dataset_id == dataset_id)
        .group_by(ImageLabel.label_id)
    )).all()
    return {"counts": {label_id: count for label_id, count in rows}}


@router.post("/assign")
async def assign_labels(body: LabelAssignRequest, db: AsyncSession = Depends(get_db)):
    """Attach and/or detach labels across a set of images.

    Idempotency comes from the `uq_image_label` unique constraint via
    `ON CONFLICT DO NOTHING`, not from a read-then-write (which races), and the
    statement `rowcount` gives an honest "newly added" count for the toast.
    """
    if not body.image_ids:
        raise HTTPException(400, "image_ids cannot be empty")
    if not body.add and not body.remove:
        raise HTTPException(400, "Nothing to do: both add and remove are empty")
    # The same bound `GET /images/ids` hands out, so the select-all toolbar can
    # never build a body this refuses.
    cap = images_router.SELECT_ALL_ID_CAP
    if len(body.image_ids) > cap:
        raise HTTPException(400, f"Cannot label more than {cap} images at once")
    overlap = set(body.add) & set(body.remove)
    if overlap:
        raise HTTPException(400, f"Label ids appear in both add and remove: {sorted(overlap)}")

    # De-duped here, not only for validation: the execution block below divides
    # ROWS_PER_STATEMENT by `len(add)` to chunk on the *row* count, so a body
    # repeating one id 20,000 times floors the chunk to a single image and builds
    # one INSERT carrying 60,000 bind parameters — past the 32,766 a stock Windows
    # sqlite3.dll enforces. After the unknown-id 400 below, both lists are bounded
    # by the size of the vocabulary.
    add = list(dict.fromkeys(body.add))
    remove = list(dict.fromkeys(body.remove))

    wanted_labels = list(dict.fromkeys([*add, *remove]))
    if wanted_labels:
        # FK enforcement is ON in the app (backend/database.py), so an unvalidated
        # bad label id would surface as an IntegrityError -> 500 rather than a 400.
        known = set((await db.execute(
            select(Label.id).where(Label.id.in_(wanted_labels))
        )).scalars().all())
        missing = [lid for lid in wanted_labels if lid not in known]
        if missing:
            raise HTTPException(400, f"Unknown label ids: {missing}")

    # Unknown *image* ids are skipped silently and the response reports what
    # matched — a selection is a client-side set that can go stale, and
    # `useSelectionStore` spans datasets.
    image_ids = list(dict.fromkeys(body.image_ids))
    live_images: list[str] = []
    for start in range(0, len(image_ids), ROWS_PER_STATEMENT):
        chunk = image_ids[start:start + ROWS_PER_STATEMENT]
        live_images.extend((await db.execute(
            select(Image.id).where(Image.id.in_(chunk))
        )).scalars().all())

    # Every dataset the live selection touches must be idle, the way the two
    # `/images/batch/*` overwrites guard themselves: a restore's Pass 2c is an
    # authoritative replace, so an assign landing mid-restore is discarded without
    # a word. A 409 says so instead.
    await images_router.guard_batch_datasets(db, live_images)

    removed = 0
    added = 0
    if live_images:
        # Chunk on the **row** count (images x labels), not the image count:
        # 3 binds per row against SQLite's 32,766 ceiling.
        if remove:
            per_stmt = max(1, ROWS_PER_STATEMENT // max(1, len(remove)))
            for start in range(0, len(live_images), per_stmt):
                chunk = live_images[start:start + per_stmt]
                result = await db.execute(
                    delete(ImageLabel).where(
                        ImageLabel.image_id.in_(chunk),
                        ImageLabel.label_id.in_(remove),
                    )
                )
                removed += result.rowcount or 0
        if add:
            per_stmt = max(1, ROWS_PER_STATEMENT // len(add))
            for start in range(0, len(live_images), per_stmt):
                chunk = live_images[start:start + per_stmt]
                values = [
                    {"image_id": iid, "label_id": lid}
                    for iid in chunk
                    for lid in add
                ]
                stmt = sqlite_insert(ImageLabel).values(values).on_conflict_do_nothing(
                    index_elements=["image_id", "label_id"]
                )
                result = await db.execute(stmt)
                added += result.rowcount or 0

    await db.commit()
    return {"images": len(live_images), "added": added, "removed": removed}


@router.post("/reorder")
async def reorder_labels(body: LabelReorderRequest, db: AsyncSession = Depends(get_db)):
    """Rewrite `sort_order` from the given order. The id set must be the whole
    vocabulary — a partial reorder would leave two labels sharing an index."""
    rows = (await db.execute(select(Label))).scalars().all()
    if set(body.ordered_ids) != {r.id for r in rows} or len(body.ordered_ids) != len(rows):
        raise HTTPException(400, "ordered_ids must list every label exactly once")
    position = {lid: i for i, lid in enumerate(body.ordered_ids)}
    for row in rows:
        row.sort_order = position[row.id]
    await db.commit()
    return {"reordered": len(rows)}


@router.post("/", response_model=LabelOut, status_code=201)
async def create_label(body: LabelCreate, db: AsyncSession = Depends(get_db)):
    name = _clean_name(body.name)
    hotkey = _clean_hotkey(body.hotkey)
    color = _clean_color(body.color)
    await _assert_name_free(db, name)
    if hotkey:
        await _assert_hotkey_free(db, hotkey)
    next_order = ((await db.execute(select(func.max(Label.sort_order)))).scalar_one_or_none() or 0)
    row = Label(
        name=name,
        color=color,
        hotkey=hotkey,
        sort_order=next_order + 1,
    )
    db.add(row)
    await _commit_unique(db, name, hotkey)
    await db.refresh(row)
    return _out(row, {})


# ---------------------------------------------------------------------------
# Item routes.
# ---------------------------------------------------------------------------


@router.patch("/{label_id}", response_model=LabelOut)
async def update_label(label_id: str, body: LabelUpdate, db: AsyncSession = Depends(get_db)):
    row = await db.get(Label, label_id)
    if row is None:
        raise HTTPException(404, "Label not found")
    # `exclude_unset`, deliberately **not** the `exclude_none=True` that
    # `routers/providers.py` uses: `hotkey` has to be clearable with an explicit
    # `{"hotkey": null}`, which `exclude_none` would silently drop.
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields:
        name = _clean_name(fields["name"])
        await _assert_name_free(db, name, exclude_id=label_id)
        row.name = name
    if "color" in fields and fields["color"]:
        row.color = _clean_color(fields["color"])
    if "hotkey" in fields:
        hotkey = _clean_hotkey(fields["hotkey"])
        if hotkey:
            await _assert_hotkey_free(db, hotkey, exclude_id=label_id)
        row.hotkey = hotkey
    await _commit_unique(db, row.name, row.hotkey)
    await db.refresh(row)
    counts = await _usage_counts(db)
    return _out(row, counts)


@router.delete("/{label_id}", status_code=204)
async def delete_label(label_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a label. Its `image_labels` rows go via the DB cascade — which is
    load-bearing, not a nicety, since nothing here loads those rows."""
    row = await db.get(Label, label_id)
    if row is None:
        raise HTTPException(404, "Label not found")
    await db.delete(row)
    await db.commit()
