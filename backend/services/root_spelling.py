"""One stored spelling for the datasets root (PM-034).

On a case-folding filesystem (`utils.PATHS_FOLD_CASE`: Windows, macOS) the root's
letter case used to follow however the launch folder was typed — `c:\\crucible` one
launch, `C:\\Crucible` the next — or a `.env` edit, and every absolute path a row
stores begins with it. So one database could hold rows under two spellings of the
same folder, and any *exact* match against a freshly built path missed the older
ones: `/filesystem/delete`'s file arm deleted the bytes and left the rows.

`settle_root_spelling`, from `main.py`'s lifespan, fixes both halves:

1. It works out the root's on-disk case (`utils.on_disk_spelling`, which follows
   no link).
2. `repair_root_spelling` rewrites every stored path whose root is a case variant
   of that spelling **and the same folder on disk**, in one transaction, after an
   integrity-checked backup.
3. Only if the repair went ahead, or had nothing to do, does `settings.datasets_dir`
   take the new spelling — a declined repair must not leave the setting disagreeing
   with every row.

Rows under a genuinely different root (a database copied from another machine,
`DATASETS_DIR` pointed elsewhere, or a case-variant folder that really is another
folder on a case-sensitive volume) are left alone, so the orphan sweep's refusal
still fires for them. On Linux a case variant is a different folder, and nothing
here runs.
"""
import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend import utils
from backend.config import settings
from backend.models import Dataset, Image, Video
from backend.models.versioning import VersionImageState
from backend.utils import chunked, sql_has_prefix

logger = logging.getLogger(__name__)

# Every column that stores an absolute path under `settings.datasets_dir`. JSON
# columns hold none (`source_meta`, `processing_history`, job config/result and the
# ComfyUI tables carry ids or folder labels), and the version object store is
# derived from `folder_path` at read time, so it follows this rewrite.
PATH_COLUMNS: tuple[tuple[type, str], ...] = (
    (Dataset, "folder_path"),
    (Image, "file_path"),
    (Image, "thumbnail_path"),
    (Video, "file_path"),
    (Video, "poster_path"),
    (VersionImageState, "file_path"),
)

BACKUP_TAG = "root-spelling"


def _separators() -> tuple[str, ...]:
    return (os.sep, os.altsep) if os.altsep else (os.sep,)


def respelled(value: str | None, root: str) -> str | None:
    """`value` with its root rewritten to `root`, or None if it is not a candidate.

    A candidate's first `len(root)` characters equal `root` case-blind but not
    exactly, and a separator follows (so `datasetsX\\...` is not under `datasets`).
    This is the string half only; `repair_root_spelling` also asks the filesystem
    whether the variant is the same folder (`_same_folder`). String compare rather
    than `Path.is_relative_to`, which is case-sensitive on macOS.
    """
    if not value or len(value) <= len(root):
        return None
    head = value[: len(root)]
    if head == root or head.casefold() != root.casefold():
        return None
    if not root.endswith(_separators()) and value[len(root)] not in _separators():
        return None
    return root + value[len(root):]


def _same_folder(a: str, b: str) -> bool:
    """`os.path.samefile`, False when either side cannot be stat'ed. A seam for tests.

    The decision that a case variant of the root may be rewritten. A variant that
    does not exist, or is another folder (a case-sensitive APFS volume, a Windows
    folder with per-directory case sensitivity, both under `PATHS_FOLD_CASE`), is
    not this root, and re-pointing its rows would also defeat the orphan sweep's
    "outside the root" refusal.
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _take_backup() -> Path:
    from backend.services.db_maintenance import write_named_backup_sync

    return write_named_backup_sync(BACKUP_TAG)


async def repair_root_spelling(db: AsyncSession, root: Path | None = None) -> int | None:
    """Rewrite stored paths onto `root`'s spelling (default `settings.datasets_dir`).

    Returns the rows changed (0 when nothing needed it), or **None when it
    declined**: rows needed rewriting but the backup failed. A no-op returning 0
    unless `utils.PATHS_FOLD_CASE`.

    The candidate query is the exact `sql_has_prefix` negated, so a healthy
    database reads next to nothing. Each distinct variant root among the
    candidates is rewritten only if it is the same folder on disk. Nothing is
    written unless an integrity-checked backup succeeds first; if it fails this
    logs and changes nothing, and the file browser's `sql_path_equals` /
    `sql_has_path_prefix` still tolerate the drift. `updated_at` is carried
    through, so the rewrite re-stamps no row. Ends any transaction already open
    on `db` (it rolls back after reading): pass a fresh session.
    """
    if not utils.PATHS_FOLD_CASE:
        return 0
    root_str = str(root if root is not None else settings.datasets_dir)
    exact_prefix = root_str if root_str.endswith(_separators()) else root_str + os.sep

    candidates: list[tuple[type, str, list[tuple[str, dict]]]] = []
    for model, attr in PATH_COLUMNS:
        col = getattr(model, attr)
        keep_stamp = hasattr(model, "updated_at")
        cols = [model.id, col] + ([model.updated_at] if keep_stamp else [])
        rows = (
            await db.execute(select(*cols).where(col.is_not(None), ~sql_has_prefix(col, exact_prefix)))
        ).all()
        found = []
        for row in rows:
            new = respelled(row[1], root_str)
            if new is None:
                continue
            values = {"id": row[0], attr: new}
            if keep_stamp:
                values["updated_at"] = row[2]
            found.append((row[1][: len(root_str)], values))
        if found:
            candidates.append((model, attr, found))
    # Close the read transaction before another connection copies the file.
    await db.rollback()

    heads = {head for _m, _a, found in candidates for head, _v in found}
    same = {h for h in heads if _same_folder(h, root_str)}
    for other in sorted(heads - same):
        logger.warning(
            "Stored paths under %s were left as they are: it differs from the datasets "
            "root %s only in case, but is not the same folder on disk",
            other, root_str,
        )
    plan = [
        (model, attr, [v for head, v in found if head in same])
        for model, attr, found in candidates
    ]
    plan = [(m, a, u) for m, a, u in plan if u]
    if not plan:
        return 0

    try:
        backup = await asyncio.get_running_loop().run_in_executor(None, _take_backup)
    except Exception:
        logger.warning(
            "Stored paths use another spelling of the datasets root (%s), but the "
            "database backup failed, so they were left as they are",
            root_str, exc_info=True,
        )
        return None

    for model, _attr, updates in plan:
        for chunk in chunked(updates, 1000):
            # ORM bulk UPDATE by primary key (executemany), as `rename_dataset` does.
            await db.execute(sa_update(model), list(chunk))
    await db.commit()

    total = sum(len(u) for _m, _a, u in plan)
    logger.warning(
        "Rewrote %d stored path(s) onto the datasets root's on-disk spelling %s (%s). "
        "Backup taken first: %s",
        total, root_str,
        ", ".join(f"{m.__tablename__}.{a}: {len(u)}" for m, a, u in plan),
        backup,
    )
    return total


async def settle_root_spelling(db: AsyncSession) -> None:
    """Give the datasets root one spelling: respell it, repair the rows, then adopt it.

    The startup entry point (`main._settle_root_spelling`, after `init_db()` and
    before anything reads a stored path). The new spelling is adopted only when the
    repair went ahead or had nothing to do: after a declined repair the rows still
    carry the old spelling, and on macOS `within_datasets_dir` compares case
    sensitively, so a setting that disagreed with them would 403 every serve route.
    """
    if not utils.PATHS_FOLD_CASE:
        return
    current = settings.datasets_dir
    spelled = utils.on_disk_spelling(current)
    done = await repair_root_spelling(db, spelled)
    if done is not None and str(spelled) != str(current):
        logger.info("Datasets root %s respelled to its on-disk case %s", current, spelled)
        settings.datasets_dir = spelled
