"""Non-CRUD label logic, shared by the router, the image filters, export and versioning.

CRUD stays inline in `routers/labels.py` (following `routers/providers.py`, the
closest precedent for a small global vocabulary table). What lives here is
everything with more than one call site — above all `label_filter_clause`, so the
gallery filter and the export filter cannot drift into meaning different things.
"""
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.image import Image
from backend.models.label import ImageLabel, Label

# 3 bind parameters per inserted row against SQLite's 32,766 ceiling. Chunking is
# on the **row** count (images x labels), not the image count: 20,000 images x 3
# labels is 60,000 rows from a request the cap lets through.
ROWS_PER_STATEMENT = 8_000


def label_filter_clause(
    label_ids: list[str] | None,
    match: str,
    missing: bool | None,
) -> list:
    """Build the WHERE clauses for a label filter.

    **Correlated EXISTS only, never a join.** `GET /images/count` runs the shared
    filter builder over `select(func.count(Image.id))` and `GET /images/` selects
    whole rows, so a join to `image_labels` would count a two-label image twice
    and duplicate its row in the grid. Each EXISTS is index-backed on
    `ix_image_labels_image_id`.

    `match` is checked rather than falling through to "any", so a direct service
    call with a typo raises instead of quietly answering the wrong question. HTTP
    callers never reach it — `utils.validate_label_filter_params` turns the same
    mistake into a 400 in the request path.
    """
    if match not in ("any", "all"):
        raise ValueError(f"label match must be 'any' or 'all', got {match!r}")
    clauses: list = []
    any_label = exists().where(ImageLabel.image_id == Image.id)

    if missing is True:
        clauses.append(~any_label)
    elif missing is False:
        clauses.append(any_label)

    if label_ids:
        if match == "all":
            # One EXISTS per id, rather than `GROUP BY ... HAVING COUNT(...)`,
            # which would need a join and a grouping this shared builder must not
            # introduce. Nobody selects fifty chips.
            for lid in label_ids:
                clauses.append(
                    exists().where(ImageLabel.image_id == Image.id, ImageLabel.label_id == lid)
                )
        else:
            clauses.append(
                exists().where(
                    ImageLabel.image_id == Image.id, ImageLabel.label_id.in_(label_ids)
                )
            )
    return clauses


async def labels_by_image(db: AsyncSession, image_ids: list[str]) -> dict[str, list[str]]:
    """`{image_id: sorted label ids}` for the given images, in one query.

    Sorted because `VersionImageState.label_ids` is diffed with `!=` — an
    unsorted list would report a reorder as a change.
    """
    if not image_ids:
        return {}
    out: dict[str, list[str]] = {}
    ids = list(image_ids)
    for start in range(0, len(ids), ROWS_PER_STATEMENT):
        chunk = ids[start:start + ROWS_PER_STATEMENT]
        rows = (await db.execute(
            select(ImageLabel.image_id, ImageLabel.label_id).where(ImageLabel.image_id.in_(chunk))
        )).all()
        for image_id, label_id in rows:
            out.setdefault(image_id, []).append(label_id)
    for v in out.values():
        v.sort()
    return out


async def live_label_ids(db: AsyncSession, candidate_ids) -> set[str]:
    """Which of `candidate_ids` still name a row in `labels`.

    There is no FK from `VersionImageState.label_ids` (it is a JSON blob on a
    snapshot, which must survive the deletion of what it names), so every restore
    path resolves through here rather than trusting the mirror.
    """
    ids = list({c for c in candidate_ids if c})
    if not ids:
        return set()
    live: set[str] = set()
    for start in range(0, len(ids), ROWS_PER_STATEMENT):
        chunk = ids[start:start + ROWS_PER_STATEMENT]
        rows = (await db.execute(select(Label.id).where(Label.id.in_(chunk)))).scalars().all()
        live.update(rows)
    return live


async def copy_labels(db: AsyncSession, id_map: dict[str, str]) -> int:
    """Attach the source images' labels to their copies. Returns the row count.

    ORM `add_all`, not a Core insert: SQLAlchemy's unit of work orders inserts by
    table dependency, so the pending `images` rows land before these join rows
    without an explicit flush. A brand-new image cannot already carry the
    assignment, so `ON CONFLICT` would buy nothing.

    Labels travel on a cross-dataset copy while **detections deliberately do
    not** — "this image is a reject" is a fact about the image, and the global
    vocabulary means the destination dataset needs no name remapping. Surprising
    enough that someone will otherwise "fix" it.
    """
    if not id_map:
        return 0
    by_source = await labels_by_image(db, list(id_map.keys()))
    rows = [
        ImageLabel(image_id=id_map[src], label_id=lid)
        for src, lids in by_source.items()
        for lid in lids
    ]
    db.add_all(rows)
    return len(rows)


async def set_labels(db: AsyncSession, wanted: dict[str, list[str]]) -> int:
    """Authoritative replace: each image in `wanted` ends up with exactly those labels.

    Replace, not merge — a restore means "the dataset looked like this", so a
    label added after the snapshot disappears, exactly as a caption edit does. It
    deletes only for image ids present in `wanted`, so `handle_extra_images="keep"`
    images keep theirs.

    Callers resolve the ids through `live_label_ids` first; this function does not
    (there is no FK to fall back on, and an unknown id would be an IntegrityError).
    """
    image_ids = list(wanted.keys())
    if not image_ids:
        return 0
    for start in range(0, len(image_ids), ROWS_PER_STATEMENT):
        chunk = image_ids[start:start + ROWS_PER_STATEMENT]
        await db.execute(delete(ImageLabel).where(ImageLabel.image_id.in_(chunk)))
    rows = [
        ImageLabel(image_id=iid, label_id=lid)
        for iid, lids in wanted.items()
        for lid in lids
    ]
    db.add_all(rows)
    return len(rows)
