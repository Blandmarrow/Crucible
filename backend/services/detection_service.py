"""Detection geometry orchestration shared across crop paths.

Pure geometry math lives in ``backend/ml/mask_utils.py``; this module wires it to
the DB. Coordinates in ``Detection`` rows are normalized to the image's
EXIF-transposed frame, so remap callers must pass the crop rect and old size in
that same frame (see ``open_rgb`` / ``image_service._open_safe``).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ml.mask_utils import remap_detection_geometry
from backend.models.detection import Detection


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
