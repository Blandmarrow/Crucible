"""Detection geometry orchestration shared across crop paths.

Pure geometry math lives in ``backend/ml/mask_utils.py``; this module wires it to
the DB. Coordinates in ``Detection`` rows are normalized to the image's
EXIF-transposed frame, so remap callers must pass the crop rect and old size in
that same frame (see ``open_rgb`` / ``image_service._open_safe``).
"""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ml.mask_utils import remap_detection_geometry
from backend.models.detection import Detection
from backend.utils import chunked


async def remap_detections_for_crop(
    session: AsyncSession,
    image_id: str,
    rect: tuple[int, int, int, int],
    old_size: tuple[int, int],
) -> tuple[int, int]:
    """Remap (or drop) all of an image's detections through a replace-mode crop.

    ``rect`` is the crop rectangle ``(x, y, w, h)`` in pixels of the OLD image's
    EXIF-transposed frame; ``old_size`` is ``(old_w, old_h)`` of that frame — the
    values must be captured *before* the ``Image.width/height`` columns are
    updated to the cropped dimensions.

    Each detection is remapped via ``remap_detection_geometry``: a ``None`` result
    means the detection fell outside the crop and its row is deleted; otherwise the
    new ``mask`` then ``bbox`` are written via ORM attribute assignment (bbox last
    so the final ``mask_area`` recompute sees both). Does **not** commit — the
    caller owns the transaction (matches the crop workers' single-commit
    discipline).

    Returns ``(remapped, dropped)`` counts.
    """
    result = await session.execute(
        select(Detection).where(Detection.image_id == image_id)
    )
    rows = result.scalars().all()

    remapped = 0
    dropped = 0
    for det in rows:
        out = remap_detection_geometry(det.mask, det.bbox, rect, old_size)
        if out is None:
            await session.delete(det)
            dropped += 1
            continue
        new_mask, new_bbox = out
        # Assign mask first, bbox last: the mask_area listeners fire on each
        # assignment; ordering bbox last means the final recompute sees both.
        det.mask = new_mask
        det.bbox = new_bbox
        remapped += 1

    return remapped, dropped


# ---------------------------------------------------------------------------
# Snapshot mirror (GPT-026)
# ---------------------------------------------------------------------------
# `VersionImageState.detections` holds one dict per row in the shape below, and
# both directions go through this one pair so a snapshot and its restore cannot
# drift apart. No `id`/`image_id` (a restore may write them under a forked id)
# and no `mask_area` (derived: the ORM listeners above recompute it when the
# restore constructs the row, from the `bbox` and `mask` passed here).


# A column list rather than whole entities: `create_snapshot` reads a whole
# dataset's rows, and loading them as ORM objects would park every mask in the
# session's identity map for nothing. A Row exposes the same attribute names, so
# `serialize_detection` takes either.
_SERIALIZED_COLUMNS = (
    Detection.image_id, Detection.label, Detection.bbox, Detection.score,
    Detection.model, Detection.task, Detection.mask, Detection.detected_at,
)


def serialize_detection(det) -> dict:
    """One `Detection` (an ORM row, or a Row of `_SERIALIZED_COLUMNS`) as the
    mirror stores it."""
    detected_at = det.detected_at
    return {
        "label": det.label,
        "bbox": list(det.bbox) if det.bbox is not None else None,
        "score": det.score,
        "model": det.model,
        "task": det.task,
        "mask": det.mask,
        "detected_at": detected_at.isoformat() if detected_at is not None else None,
    }


def _sort_key(d: dict) -> tuple:
    """Total order over serialized detections, every field None-safe.

    Sorted because the snapshot diff compares the stored lists with `!=` — an
    unsorted list would report a query-order difference as a change.
    """
    score = d.get("score")
    return (
        d.get("model") or "", d.get("task") or "", d.get("label") or "",
        [float(v) for v in (d.get("bbox") or [])],
        d.get("detected_at") or "",
        score is None, score if score is not None else 0.0,
        d.get("mask") or "",
    )


def canonical_detections(dets) -> list[dict]:
    """Sort serialized detections into the order the mirror stores them in."""
    return sorted(dets, key=_sort_key)


def deserialize_detection(image_id: str, d: dict) -> Detection:
    """Build an ORM row from a serialized detection.

    ORM construction, never a Core insert: `mask_area` is kept in sync only by
    the attribute listeners, which fire on constructor assignment. `mask` is
    passed before `bbox`, matching `remap_detections_for_crop`'s ordering, so the
    last recompute sees both.
    """
    raw_at = d.get("detected_at")
    try:
        detected_at = datetime.fromisoformat(raw_at) if raw_at else datetime.utcnow()
    except (TypeError, ValueError):
        detected_at = datetime.utcnow()
    return Detection(
        image_id=image_id,
        label=d.get("label") or "",
        mask=d.get("mask"),
        bbox=list(d.get("bbox") or [0.0, 0.0, 0.0, 0.0]),
        score=d.get("score"),
        model=d.get("model") or "",
        task=d.get("task") or "",
        detected_at=detected_at,
    )


async def detections_by_image(db: AsyncSession, image_ids) -> dict[str, list[dict]]:
    """`{image_id: canonical serialized detections}` for the given images.

    One chunked select for the whole set — `create_snapshot` calls this for a
    whole dataset, where a per-image lookup would be N+1. An image with no
    detections is absent from the result; callers default it to `[]`.
    """
    ids = list(image_ids)
    if not ids:
        return {}
    out: dict[str, list[dict]] = {}
    for chunk in chunked(ids):
        rows = (await db.execute(
            select(*_SERIALIZED_COLUMNS).where(Detection.image_id.in_(chunk))
        )).all()
        for det in rows:
            out.setdefault(det.image_id, []).append(serialize_detection(det))
    return {iid: canonical_detections(v) for iid, v in out.items()}


async def set_detections(db: AsyncSession, wanted: dict[str, list[dict]]) -> int:
    """Authoritative replace: each image in `wanted` ends up with exactly those rows.

    Replace, not merge — a detection added after the snapshot is geometry over
    pixels the restore just took away. Only image ids present in `wanted` are
    touched, so keep-mode extras keep theirs. Does **not** commit. Returns the
    number of rows written.
    """
    image_ids = list(wanted.keys())
    if not image_ids:
        return 0
    for chunk in chunked(image_ids):
        await db.execute(delete(Detection).where(Detection.image_id.in_(chunk)))
    rows = [
        deserialize_detection(iid, d)
        for iid, dets in wanted.items()
        for d in dets
    ]
    db.add_all(rows)
    return len(rows)
