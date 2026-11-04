"""The gate's reject flag, and the rule that it goes when the image leaves `rejects/`.

`run_gate` flags a failed image `gate_rejected` (with `gate_reasons`) and files it under
`<base>/rejects/...`. The flag is what keeps a reject out of a folder's kept count and
the export, so it must not outlive the filing: an image moved out of `rejects/` by
any writer of `Image.subfolder` loses it (the user's ruling, 2026-11-04). Every such
writer calls `clear_gate_flags_outside_rejects` after its subfolder write. Version
restore does not: it restores the flags and the subfolder together.

This is its own module because `run_gate` imports `image_move`, which needs the helper.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Image

REJECTS_FOLDER = "rejects"


async def clear_gate_flags_outside_rejects(db: AsyncSession, dataset_id: str) -> int:
    """Drop `gate_rejected` / `gate_reasons` from every flagged image of `dataset_id`
    whose subfolder has no `rejects` segment. Returns how many it cleared.

    Does not commit; the caller owns the transaction. Call it after the subfolder write
    (the query autoflushes pending changes first). Reads the whole dataset's flagged
    rows rather than the moved ids, so a caller need not know which images moved.

    `populate_existing` refreshes `Image` objects the session already holds (a bulk
    `sa_update` left their `subfolder` stale). It also drops their loaded deferred
    columns (`Image.source_meta`), so a caller that reads one afterwards on the same
    session must `undefer` it again, or the lazy load raises `MissingGreenlet`.
    """
    imgs = (await db.execute(
        select(Image).where(
            Image.dataset_id == dataset_id,
            Image.quality_flags["gate_rejected"].as_boolean() == True,  # noqa: E712
        ).execution_options(populate_existing=True)
    )).scalars().all()
    cleared = 0
    for img in imgs:
        if REJECTS_FOLDER in (img.subfolder or "").split("/"):
            continue
        # Copied and reassigned: an in-place edit of a loaded JSON value is not seen as
        # a change (CLAUDE.md).
        img.quality_flags = {k: v for k, v in (img.quality_flags or {}).items()
                             if k not in ("gate_rejected", "gate_reasons")}
        cleared += 1
    return cleared
