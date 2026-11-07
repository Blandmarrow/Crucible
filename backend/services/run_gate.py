"""The gate an automated run's pass ends with (M6 §5).

It judges each of the pass's images against the recipe's `GateSettings`. A failure is
flagged `gate_rejected` (with its `gate_reasons`) and filed under
`<base>/rejects/<leaf path>`, one tree for every reject (the user's ruling,
2026-11-04). Nothing is deleted, and subfolders are DB labels, so no file moves.
The flag is what keeps a reject out of a folder's kept count and the export. It goes
when the image leaves `rejects/` by any move, copy or subfolder rename or delete
(`gate_flags.clear_gate_flags_outside_rejects`, the user's ruling, 2026-11-04); the
gate's own move into `rejects/` keeps it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Image
from backend.models.comfy import ComfyRow
from backend.schemas.run import GateSettings
from backend.services.gate_flags import REJECTS_FOLDER
from backend.services.image_move import move_to_subfolder
from backend.utils import chunked, join_subfolder

__all__ = ["REJECTS_FOLDER", "apply_gate", "gate_reasons"]

# `REJECTS_FOLDER` lives in `gate_flags` (re-exported here) so `image_move` can use it
# without an import cycle.


def gate_reasons(img: Image, gate: GateSettings) -> list[str]:
    """Why `img` fails the gate; empty when it passes. Pure.

    A flag fails when its value is truthy. A NULL aesthetic score fails a minimum (an
    unscored image has not shown it passes), and a stale score always fails: the
    pixels changed since it was measured (`utils.record_in_place`).
    """
    flags = img.quality_flags or {}
    out = [f for f in gate.exclude_flags if flags.get(f)]
    if gate.aesthetic_min is not None and (img.aesthetic_score is None or img.aesthetic_score < gate.aesthetic_min):
        out.append("aesthetic_below_min")
    if img.scores_stale:
        out.append("scores_stale")
    return out


async def apply_gate(
    db: AsyncSession, row_ids: list[str], base: str, gate: GateSettings, *, dataset_id: str,
) -> dict:
    """Judge the images of `row_ids`' completed rows in `dataset_id`; flag and file the
    failures.

    Does not commit: the caller commits once, with its own job row. An image already
    rejected is left alone, and so is one the user moved to another dataset. Returns
    `{checked, rejected, reasons: {reason: count}}`.
    """
    folder_of: dict[str, str] = {}
    for chunk in chunked(row_ids):
        for subfolder, image_ids in (await db.execute(
            select(ComfyRow.subfolder, ComfyRow.image_ids).where(
                ComfyRow.id.in_(chunk), ComfyRow.status == "completed",
            )
        )).all():
            for iid in image_ids or []:
                folder_of.setdefault(iid, subfolder or "")
    by_target: dict[str, list[str]] = {}
    reasons: dict[str, int] = {}
    checked = 0
    for chunk in chunked(list(folder_of)):
        images = (await db.execute(
            select(Image).where(Image.id.in_(chunk), Image.dataset_id == dataset_id)
        )).scalars().all()
        for img in images:
            flags = img.quality_flags or {}
            if flags.get("gate_rejected"):
                continue
            checked += 1
            why = gate_reasons(img, gate)
            if not why:
                continue
            for r in why:
                reasons[r] = reasons.get(r, 0) + 1
            # Copied and reassigned: an in-place edit of a loaded JSON value is not
            # seen as a change, and the UPDATE would be skipped (CLAUDE.md).
            img.quality_flags = {**flags, "gate_rejected": True, "gate_reasons": why}
            by_target.setdefault(join_subfolder(base, REJECTS_FOLDER, folder_of[img.id]), []).append(img.id)
    for target, ids in by_target.items():
        await move_to_subfolder(db, ids, target, rename_on_move=False, commit=False, clear_gate_flags=False)
    return {
        "checked": checked,
        "rejected": sum(len(v) for v in by_target.values()),
        "reasons": reasons,
    }
