"""A job's image scope: an id list, or a dataset narrowed by subfolder and flags.

Upscale, LUT, inpaint and detection crop all take the same four request fields
(``dataset_id``, ``image_ids``, ``subfolder``, ``quality_flags``) and used to
resolve them with the same block, copied four times. The rules live here once:

- **Explicit ids win.** When ``image_ids`` is given, subfolder and flags are
  ignored, and the ids are *not* constrained to ``dataset_id``: a selection can
  span datasets (PM-029), and the job refreshes every dataset it touched.
- **Flags exclude.** Each named flag drops the images that carry it, AND-ed
  together. Unknown names are dropped silently, never a 400.
- **Subfolder is an exact match**, not a prefix: child folders are not included.

``flag_exclusions`` is also the flag filter for the sites whose scope differs
in other ways (detection run, caption bulk edit, captioning's ``exclude_flags``).
``images._apply_bulk_filters`` stays separate on purpose: it applies flags even
with explicit ids and has an ``include_flagged`` polarity (M8 stage 8 moves it).
"""
from collections.abc import Iterable

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.models import Image
from backend.utils import ALLOWED_FLAG_KEYS, chunked, normalize_subfolder


def flag_exclusions(quality_flags: Iterable[str] | None) -> list[ColumnElement[bool]]:
    """One ``flag IS NOT TRUE`` clause per known flag name; ``[]`` means no filter."""
    return [
        Image.quality_flags[f].as_boolean().is_not(True)
        for f in (quality_flags or ())
        if f in ALLOWED_FLAG_KEYS
    ]


def scope_query(
    dataset_id: str, subfolder: str | None, quality_flags: Iterable[str] | None
) -> Select:
    """``select(Image.id)`` over a dataset, narrowed by subfolder and flag exclusions.

    Unexecuted, so a caller can use it as a subquery. ``normalize_subfolder``
    raises its 400 here, at request time.
    """
    q = select(Image.id).where(Image.dataset_id == dataset_id)
    if subfolder is not None:
        q = q.where(Image.subfolder == normalize_subfolder(subfolder))
    exclusions = flag_exclusions(quality_flags)
    if exclusions:
        q = q.where(and_(*exclusions))
    return q


async def resolve_scope_ids(
    db: AsyncSession,
    *,
    dataset_id: str,
    image_ids: list[str] | None,
    subfolder: str | None,
    quality_flags: list[str] | None,
) -> list[str]:
    """The ids of the images a job will touch.

    With ``image_ids``: the ones that still exist, each once, in no promised
    order. Deduplicated first, because one ``IN`` collapsed a repeated id but
    two chunks would each return it and the job would edit the image twice;
    chunked, because a select-all-matching list can pass SQLite's bind limit.
    ``[]`` means nothing, not the whole dataset.
    """
    if image_ids is not None:
        found: list[str] = []
        for chunk in chunked(list(dict.fromkeys(image_ids))):
            result = await db.execute(select(Image.id).where(Image.id.in_(chunk)))
            found.extend(r[0] for r in result.all())
        return found
    result = await db.execute(scope_query(dataset_id, subfolder, quality_flags))
    return [r[0] for r in result.all()]
