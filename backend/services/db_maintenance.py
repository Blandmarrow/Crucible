"""Startup database maintenance: integrity check, then a rotating backup.

The whole app is one SQLite file. Nothing else in the stack notices when it starts
to rot, and there is no copy of it anywhere — a corrupted page or a mistaken bulk
operation loses every caption, score and version snapshot in the install.

This runs once per boot, in a thread, off the startup path (see
`backend.main._startup_db_maintenance`), and is never fatal: a failure here logs and
leaves the app running.

Order matters. `PRAGMA quick_check` runs first and a failure **skips the backup** —
rotating a corrupt copy in would push the newest good backup one slot closer to
deletion, which is the opposite of what a backup is for. The copy itself goes
through sqlite3's backup API rather than a file copy, because the live database has
a WAL sidecar: copying the `.db` alone captures a torn state.
"""

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from backend.config import settings

logger = logging.getLogger(__name__)

# How many timestamped copies to keep. Small on purpose — each is a full copy of
# the database, sitting on the same volume as the original.
BACKUP_KEEP = 5
BACKUP_DIR_NAME = "backups"

# Skip the whole run when a backup this recent already exists. `manage.sh dev` runs
# uvicorn with --reload, and uvicorn re-runs the lifespan on every reload, so without
# this a backend file save would integrity-check and copy the entire database — and
# five saves would flush every pre-session backup out of a BACKUP_KEEP-slot rotation,
# discarding the history exactly when the work has gone wrong. The restart button
# (see docs/dev/backend-infrastructure.md § restart loop) has the same shape, slower.
BACKUP_MIN_AGE_SECONDS = 15 * 60


def database_file_path() -> Path | None:
    """The filesystem path behind `settings.database_url`, or None if there is none.

    Returns None for a non-SQLite URL and for `:memory:` — both mean "nothing to
    back up" rather than an error.
    """
    url = str(settings.database_url)
    if not url.startswith("sqlite"):
        return None
    _, sep, tail = url.partition(":///")
    if not sep or not tail or tail.startswith(":memory:"):
        return None
    return Path(tail)


def _newest_backup_age(path: Path) -> float | None:
    """Seconds since the newest existing backup was written, or None when there is none.

    Read from mtime, not the filename stamp: the question is whether a copy was taken
    recently, which is about when the file landed rather than what it is called.
    """
    backup_dir = path.parent / BACKUP_DIR_NAME
    try:
        mtimes = [p.stat().st_mtime for p in backup_dir.glob(f"{path.stem}-*.db")]
    except OSError:
        return None  # unreadable backup dir is not a reason to skip the backup
    if not mtimes:
        return None
    return time.time() - max(mtimes)


def _quick_check(path: Path) -> bool:
    """Run `PRAGMA quick_check`. True when the database reports 'ok'.

    A file too damaged to open at all raises instead of reporting problems; that is
    the same answer for our purposes — do not back it up.
    """
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        logger.warning("Database at %s could not be integrity-checked (%s); skipping backup", path, exc)
        return False
    problems = [r[0] for r in rows if r and r[0] != "ok"]
    if problems:
        logger.warning(
            "Database integrity check FAILED for %s (%d problem(s)); skipping backup. First: %s",
            path, len(problems), problems[0],
        )
        return False
    return True


def _write_backup(path: Path) -> Path:
    """Copy the live database into `<db dir>/backups/<stem>-<timestamp>.db`."""
    backup_dir = path.parent / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    # Rotation orders copies by filename, so the name has to sort chronologically
    # and never collide: milliseconds (two boots inside one second is a Restart
    # click away) plus an always-present counter. The counter is not conditional on
    # purpose — a bare `-{stamp}.db` would sort *after* its own `-{stamp}-001.db`,
    # since '-' precedes '.', and rotation would then delete the newest copy.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    n = 0
    dest = backup_dir / f"{path.stem}-{stamp}-{n:03d}.db"
    while dest.exists():
        n += 1
        dest = backup_dir / f"{path.stem}-{stamp}-{n:03d}.db"
    src = sqlite3.connect(str(path), timeout=30.0)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def _prune_backups(path: Path, keep: int = BACKUP_KEEP) -> int:
    """Delete all but the newest `keep` backups. Returns the number removed.

    Sorted by filename, not mtime: the name carries a zero-padded timestamp, so it
    sorts chronologically and stays correct if a copy is moved around.
    """
    backup_dir = path.parent / BACKUP_DIR_NAME
    existing = sorted(backup_dir.glob(f"{path.stem}-*.db"))
    removed = 0
    for old in existing[:-keep] if keep > 0 else existing:
        try:
            old.unlink()
            removed += 1
        except OSError:
            logger.warning("Could not remove old database backup %s", old, exc_info=True)
    return removed


def run_startup_maintenance_sync() -> None:
    """Integrity-check and back up the database. Synchronous — call in an executor."""
    path = database_file_path()
    if path is None:
        return
    if not path.exists():
        logger.debug("No database file at %s yet; skipping startup maintenance", path)
        return

    # Before the integrity check, not after: quick_check is a full page scan and is most
    # of the per-reload cost this guard exists to avoid. A negative age means a copy is
    # stamped in the future (clock skew), so fall through and back up — the safe way to
    # be wrong is an extra copy, not a permanently skipped one.
    age = _newest_backup_age(path)
    if age is not None and 0 <= age < BACKUP_MIN_AGE_SECONDS:
        logger.debug(
            "Database backup skipped: newest copy is %.1f min old (minimum age %.0f min)",
            age / 60, BACKUP_MIN_AGE_SECONDS / 60,
        )
        return

    started = time.monotonic()
    try:
        if not _quick_check(path):
            return
        dest = _write_backup(path)
        size_mb = dest.stat().st_size / 2 ** 20  # before pruning — with keep=0 dest itself goes
        removed = _prune_backups(path)
    except (sqlite3.Error, OSError):
        logger.warning("Startup database backup failed for %s", path, exc_info=True)
        return

    logger.info(
        "Database backup written: %s (%.1f MB) in %.1fs%s",
        dest, size_mb, time.monotonic() - started,
        f", pruned {removed} old backup(s)" if removed else "",
    )
