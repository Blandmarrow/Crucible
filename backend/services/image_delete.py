"""The files-first half of every image delete (GPT-008, PM-021).

An image delete used to commit the row removal and *then* unlink the file. On
Windows a file the app is still serving cannot be unlinked, so a lock there
answered 500 with the row already gone, the file still on disk, the dataset
counters stale (`refresh_stats` ran after the unlink) and a retry impossible —
the 404 had nothing left to delete. `routers/videos.py::delete_video` had
already solved this for videos: unlink first, through `unlink_retrying`, and let
`FileInUseError` raise *before* the row goes, so a lock is a 409 that leaves
everything intact.

These helpers give the four image-delete sites the same order:
`images.delete_image`, `images.batch_delete`, `images.bulk_delete_filtered` and
`quality.resolve_duplicates`. The caller deletes the rows (and prunes duplicate
flags) for the ids that came back clean, then commits, then refreshes stats.
"""

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.services import version_service
from backend.utils import FileInUseError, contained_path, unlink_retrying

logger = logging.getLogger(__name__)


async def unlink_image_files(
    db: AsyncSession,
    image_id: str,
    file_path: str | None,
    thumbnail_path: str | None,
    *,
    context: str,
) -> None:
    """Back up and unlink one image's file, then its sidecar and thumbnail.

    Raises — `FileInUseError` for a lock that outlasted the backoff, the original
    `OSError` otherwise — **only while the image file is still on disk**, so the
    caller must keep the row. The `.txt` sidecar and the thumbnail are best-effort,
    like `delete_video`'s poster: a stray one is re-derivable (a rescan adopts or
    ignores it; `serve_thumbnail` never asks for it again), and refusing a delete
    over one would make the file the user asked to remove outlive the request.

    Each path goes through `contained_path` and the *resolved* path is what gets
    unlinked and backed up (CLAUDE.md § Path traversal guard). An escaped
    `file_path` is skipped, not raised on: the row still goes, as it always has.
    The versioning hook runs before the unlink because it copies the bytes.
    """
    p = contained_path(file_path, settings.datasets_dir, context=context, ident=image_id)
    t = contained_path(thumbnail_path, settings.datasets_dir, context=context, ident=image_id)
    if p is not None:
        await version_service.mark_image_deleted_in_versions(image_id, str(p), db)
        await unlink_retrying(p)
    for extra in (p.with_suffix(".txt") if p is not None else None, t):
        if extra is None:
            continue
        try:
            await unlink_retrying(extra)
        except OSError:
            logger.warning("%s %s: could not remove %s; leaving it on disk", context, image_id, extra, exc_info=True)


async def unlink_files_for_rows(
    db: AsyncSession, rows: Iterable[Any], *, context: str
) -> tuple[list[str], int, int]:
    """`unlink_image_files` over a batch, keeping the rows whose file would not go.

    `rows` need `.id`, `.file_path` and `.thumbnail_path`. Returns
    `(deletable_ids, in_use, failed)`: only `deletable_ids` may have their rows
    deleted. A locked file costs its own row, never the batch — refusing the whole
    request would be wrong too, since the files before it are already gone.
    """
    deletable: list[str] = []
    in_use = failed = 0
    for r in rows:
        try:
            await unlink_image_files(db, r.id, r.file_path, r.thumbnail_path, context=context)
        except FileInUseError:
            in_use += 1
            continue
        except OSError:
            logger.warning("%s %s: could not delete %s; keeping its row", context, r.id, r.file_path, exc_info=True)
            failed += 1
            continue
        deletable.append(r.id)
    return deletable, in_use, failed
