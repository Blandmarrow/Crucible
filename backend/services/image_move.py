"""Move images into a subfolder of their own dataset.

Lifted out of ``routers/images.py::batch_move_subfolder`` so the route and an
automated run's stage share one implementation. It raises the route's
``HTTPException``s itself (404 / 400 / 409), as the other lifted start
functions do, so the route stays a thin wrapper.
"""
import logging
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Image
from backend.services.dataset_busy import ensure_not_busy
from backend.utils import (
    chunked,
    normalize_subfolder,
    rename_with_sidecar,
    slugify_filename,
    thumbnail_path_for,
    unique_filename_with_thumb,
)

logger = logging.getLogger(__name__)


async def move_to_subfolder(
    db: AsyncSession,
    image_ids: list[str],
    subfolder: str,
    *,
    rename_on_move: bool,
    commit: bool = True,
) -> dict:
    """Move ``image_ids`` into ``subfolder``; returns ``{"moved", "failed", "subfolder"}``.

    ``image_ids`` must be non-empty: an empty list is refused with ``ValueError``
    so a caller can never turn "nothing selected" into a silent no-op or a
    whole-dataset move. All ids must belong to one dataset (400 otherwise), and
    that dataset must not be busy (409).

    ``commit=False`` (only with ``rename_on_move=False``) stages the UPDATE and
    leaves the commit to the caller. With ``rename_on_move=True`` each image is
    committed right after its rename (PM-013), so ``commit=False`` is refused.
    """
    if not image_ids:
        raise ValueError("move_to_subfolder needs at least one image id")
    if rename_on_move and not commit:
        raise ValueError("commit=False cannot be combined with rename_on_move=True")

    target = normalize_subfolder(subfolder)
    ids = list(dict.fromkeys(image_ids))
    rows: list = []
    for chunk in chunked(ids):
        result = await db.execute(
            select(Image.id, Image.filename, Image.file_path, Image.dataset_id)
            .where(Image.id.in_(chunk))
        )
        rows.extend(result.all())
    if not rows:
        raise HTTPException(404, "No matching images found")
    # Everything below takes one dataset from rows[0]: its images folder, its
    # filename set and its busy guard. A gallery selection can span split-view
    # panes, and with rename_on_move a second dataset's files were renamed into
    # the first one's folder while their rows kept their own dataset_id. A
    # subfolder is a per-dataset label, so a mixed request has no single meaning:
    # refuse it before touching anything. The toolbar sends one request per dataset.
    if len({row.dataset_id for row in rows}) > 1:
        raise HTTPException(
            400, "These images belong to more than one dataset — move each dataset's images separately",
        )

    dataset_id = rows[0].dataset_id
    ensure_not_busy(dataset_id)
    images_dir = Path(rows[0].file_path).parent

    if rename_on_move:
        existing = await db.execute(select(Image.filename).where(Image.dataset_id == dataset_id))
        db_names: set[str] = {r[0] for r in existing.all()}

        target_stem = slugify_filename(target.replace("/", "_")) if target else "image"

        # All existing thumbnails are occupied — including those of images being moved,
        # because allowing a new assignment to reuse a moving image's stem would let
        # image A's new thumbnail overwrite image B's current thumbnail mid-execution.
        thumb_dir = images_dir.parent / "thumbnails"
        occupied_thumb_stems: set[str] = {p.stem for p in thumb_dir.glob("*.webp")} if thumb_dir.exists() else set()
        planned_thumb_stems: set[str] = set()

        # Build the full rename plan before touching anything.
        renames: list[tuple[Path, Path, Path, Path, str, str, str]] = []  # (old, new, old_thumb, new_thumb, id, new_fn, new_fp)
        for row in rows:
            old_path = Path(row.file_path)
            suf = old_path.suffix.lower()
            db_names.discard(row.filename)
            new_filename = unique_filename_with_thumb(
                images_dir, target_stem, suf, db_names, occupied_thumb_stems, planned_thumb_stems
            )
            new_path = images_dir / new_filename
            old_thumb = Path(thumbnail_path_for(str(old_path)))
            new_thumb = Path(thumbnail_path_for(str(new_path)))
            renames.append((old_path, new_path, old_thumb, new_thumb, row.id, new_filename, str(new_path)))

        # One commit per image, right after its rename (PM-013): the rename is
        # the item's only fallible step, so a failure rolls back that one row
        # with its file still at the old name — and every image before it is
        # already moved *and* committed. A single commit after the loop instead
        # rolled back the rows of every file the loop had already renamed.
        moved = failed = 0
        for old_path, new_path, old_thumb, new_thumb, img_id, new_fn, new_fp in renames:
            values: dict = dict(subfolder=target, filename=new_fn, file_path=new_fp, thumbnail_path=str(new_thumb))
            if new_path != old_path:
                values["is_auto_named"] = True
            await db.execute(sa_update(Image).where(Image.id == img_id).values(**values))
            if new_path != old_path:
                try:
                    rename_with_sidecar(old_path, new_path)
                except OSError:
                    logger.warning("batch_move_subfolder %s: rename %s failed", img_id, old_path.name, exc_info=True)
                    await db.rollback()
                    failed += 1
                    continue
            await db.commit()
            moved += 1
            # --- epilogue: best-effort, cannot undo the committed rename ---
            if old_thumb != new_thumb:
                try:
                    if old_thumb.exists():
                        old_thumb.replace(new_thumb)
                except OSError:
                    logger.warning(
                        "batch_move_subfolder %s: thumbnail move failed; it will be regenerated",
                        img_id, exc_info=True,
                    )
        return {"moved": moved, "failed": failed, "subfolder": target}

    # Just update the subfolder field; filenames stay unchanged.
    for chunk in chunked([row.id for row in rows]):
        await db.execute(sa_update(Image).where(Image.id.in_(chunk)).values(subfolder=target))
    if commit:
        await db.commit()
    return {"moved": len(rows), "failed": 0, "subfolder": target}
