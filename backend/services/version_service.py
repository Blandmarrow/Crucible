"""
Dataset versioning service.

Object store layout:
  {dataset.folder_path}/.versions/objects/{sha256[:2]}/{sha256[2:]}

Copy-on-write (COW) strategy — the mode decides how a *new* snapshot is taken:
  - "off":    no new snapshots (the versioning write routes refuse)
  - "manual": a snapshot copies all files eagerly (full point-in-time backup)
  - "auto":   a snapshot records rows only (`file_hash=NULL`, lazy COW)

Protection is gated on the data, never on the mode (GPT-022): both hooks fire in
every mode whenever the image has a lazy (NULL-hash) snapshot row, and back the
current bytes up before an overwrite or a delete. A NULL hash means "unchanged
since the snapshot", which only stays true while every overwrite fills it first —
gating on the current mode left Auto-era rows unprotected after a switch to
Manual or Off, and a restore then read the overwritten file as the snapshot's.

is_present semantics:
  A VersionImageState row always has is_present=True — it records that the image was present
  in the dataset at snapshot time. Post-deletion snapshots simply have no row for the deleted
  image. This means restoring a pre-deletion snapshot correctly re-creates the image from the
  object store (the deletion hook backs up the file before it is unlinked).
"""
import asyncio
import copy
import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

# `settings as app_settings`: several functions here bind `settings = await
# get_thresholds(db)` as a local, so the plain name is a shadowing trap.
from backend.config import settings as app_settings
from backend.models.dataset import Dataset
from backend.models.detection import Detection
from backend.models.image import Image
from backend.models.label import ImageLabel, Label
from backend.models.versioning import DatasetBranch, DatasetVersion, VersionImageState
from backend.models.video import Video
from backend.services.detection_service import (
    canonical_detections, detections_by_image, set_detections,
)
from backend.services.label_service import labels_by_image, live_label_ids, set_labels
from backend.services.threshold_service import get_thresholds
from backend.utils import (
    chunked, contained_path, replace_retrying, score_columns, thumbnail_path_for,
    within_datasets_dir,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Object store helpers (sync — called via run_in_executor)
# ---------------------------------------------------------------------------

def _compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _object_store_path(dataset_folder: str, sha256: str) -> Path:
    return Path(dataset_folder) / ".versions" / "objects" / sha256[:2] / sha256[2:]


def _store_object(dataset_folder: str, src_path: str) -> str:
    """Stream a file into the object store atomically, returning the hash of the
    bytes actually stored.

    Hashing and writing happen in the same read pass to a temp file in objects/
    (same filesystem, so the final os.replace is atomic) — the stored content and
    its address can never disagree, even if the source file is overwritten
    mid-copy (the hash-then-copy TOCTOU that could permanently poison a
    content-addressed entry).
    """
    objects_dir = Path(dataset_folder) / ".versions" / "objects"
    objects_dir.mkdir(parents=True, exist_ok=True)
    tmp = objects_dir / f".tmp-{uuid4().hex}"
    h = hashlib.sha256()
    try:
        with open(src_path, "rb") as src, open(tmp, "wb") as out:
            for chunk in iter(lambda: src.read(65536), b""):
                h.update(chunk)
                out.write(chunk)
        sha256 = h.hexdigest()
        dest = _object_store_path(dataset_folder, sha256)
        if dest.exists():
            tmp.unlink()
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dest)
        return sha256
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _store_row_bytes(dataset_folder: str, file_path: str) -> str | None:
    """Store a row's file in the object store — the **single** place a dataset
    file's bytes enter ``{ds}/.versions/objects/``. Sync — call via executor.

    Returns the stored hash, or ``None`` when the path does not resolve inside
    ``dataset_folder`` (a resolve error counts as out of tree). A stored object
    is retrievable through a snapshot restore, so an out-of-tree ``file_path``
    would be an arbitrary-file **read** primitive even where nothing is
    unlinked. ``within_datasets_dir`` resolves, which is what catches the one
    live vector: a symlink in ``{ds}/images/`` that rescan registers under its
    in-tree path while ``open()`` follows it out of tree. The base is the row's
    own dataset folder rather than ``settings.datasets_dir`` — stricter, and it
    also catches a row pointing into a *different* dataset.

    Both callers route through here: ``_backup_and_record_hash`` (and so both
    copy-on-write hooks) and Manual ``create_snapshot``'s eager copy, which went
    ungated until the second caller was noticed. Copy errors propagate.
    """
    try:
        inside = within_datasets_dir(file_path, Path(dataset_folder)) is not None
    except (OSError, RuntimeError, ValueError):
        inside = False
    if not inside:
        return None
    return _store_object(dataset_folder, file_path)


def _remove_stale_files(file_path: str | None, sidecar: bool, thumbnail_path: str | None) -> None:
    """Best-effort removal of an image file, its .txt sidecar, and/or its thumbnail.

    Used by restore when an image was renamed/moved after the snapshot: the file left
    behind at its old on-disk location is an orphan and must be cleaned up.
    Sync — always call via ``loop.run_in_executor``.

    Containment is gated **here** rather than at the five call sites (V-83): each
    path resolves through `contained_path` and only the resolved form is unlinked,
    so a hand-edited `file_path` cannot make a restore delete outside
    `settings.datasets_dir`. The riskiest caller is
    ``handle_extra_images="remove"``, which passes an arbitrary row's stored
    columns straight through. `contained_path` never raises, which matters: the
    bare ``except Exception: pass`` below would swallow it silently.
    """
    try:
        p = contained_path(file_path, app_settings.datasets_dir, context="_remove_stale_files")
        if p is not None:
            if p.exists():
                p.unlink()
            if sidecar:
                txt = p.with_suffix(".txt")
                if txt.exists():
                    txt.unlink()
        thumb = contained_path(
            thumbnail_path, app_settings.datasets_dir, context="_remove_stale_files"
        )
        if thumb is not None and thumb.exists():
            thumb.unlink()
    except Exception:
        pass


def _copy_object_to_tmp(dataset_folder: str, object_path: Path) -> Path:
    """Copy a stored object to a fresh ``.tmp-{uuid}`` in ``objects/``. Sync.

    The temp lives where ``_store_object``'s do: prune already sweeps
    ``.tmp-*`` there, and no rescan ever looks under ``.versions``. Same
    filesystem as the dataset's files, so the ``replace`` that follows is atomic.
    A copy that fails leaves no temp behind.
    """
    objects_dir = Path(dataset_folder) / ".versions" / "objects"
    objects_dir.mkdir(parents=True, exist_ok=True)
    tmp = objects_dir / f".tmp-{uuid4().hex}"
    try:
        shutil.copy2(str(object_path), str(tmp))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


async def _write_object_atomically(
    dataset_folder: str, object_path: Path, target: Path
) -> None:
    """Put a stored object's bytes at ``target`` all at once, or not at all (GPT-025).

    ``shutil.copy2`` straight onto the target truncated it first, so a copy
    that failed half way left neither the old pixels nor the new ones. The
    bytes go to a temp file first and ``replace_retrying`` swaps it in — the
    overwriting form, which on Windows retries a file another handle holds and
    then raises ``FileInUseError``; either way the old file is intact until the
    swap. The temp never outlives the call.
    """
    loop = asyncio.get_running_loop()
    tmp = await loop.run_in_executor(None, _copy_object_to_tmp, dataset_folder, object_path)
    try:
        await replace_retrying(tmp, target)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove restore temp %s: %s", tmp, exc)


# Columns describing a row's *pixels* rather than its curation, captured before
# a restore writes the snapshot's values so that a file whose content could not
# be restored gets them back (GPT-025). The ten scores and what qualifies them
# were measured on the surviving pixels; the geometry is re-measured on top.
# Never `source_meta`: it is deferred, and it is provenance, not pixels.
_PIXEL_QUALIFIERS = (
    "aesthetic_model", "scores_stale", "quality_flags", "dino_layer_scores",
    "processing_history", "width", "height", "file_size_bytes", "format", "phash",
)


def _capture_pre_pixel(img: Image) -> dict:
    cols = sorted(score_columns(Image)) + list(_PIXEL_QUALIFIERS)
    # Deep copies: the JSON columns are reassigned later, and SQLAlchemy only
    # sees a change on a different object (CLAUDE.md, loaded JSON columns).
    return {c: copy.deepcopy(getattr(img, c)) for c in cols}


async def _measure_into(img: Image) -> None:
    """Measure the row's file and assign what could be measured. Never raises.

    Geometry, size, format and phash come from the file on disk rather than a
    snapshot mirror. ``get_image_info`` returns ``{}`` on any failure, so an
    unreadable file keeps whatever the row already holds.
    """
    from backend.services.image_service import get_image_info

    try:
        info = await asyncio.get_running_loop().run_in_executor(
            None, get_image_info, img.file_path
        )
    except Exception as exc:
        logger.warning("Could not measure %s: %s", img.file_path, exc)
        return
    for key in ("width", "height", "file_size_bytes", "format", "phash"):
        if key in info:
            setattr(img, key, info[key])


_FAILURE_DETAIL_CAP = 50


def _failure_summary(failed_count: int, failures: list[dict]) -> str | None:
    """One line for the job's ``result_data`` (PM-023's convention), ≤ 300 chars."""
    if not failed_count:
        return None
    names = ", ".join(f["file"] for f in failures[:3])
    more = failed_count - min(3, len(failures))
    text = f"{failed_count} file(s) could not be restored: {names}"
    if more > 0:
        text += f" and {more} more"
    if failures:
        text += f" ({failures[0]['error']})"
    return text if len(text) <= 300 else text[:297] + "..."


def _sync_caption_sidecar(image_path: str, caption: str | None) -> None:
    """Make the on-disk .txt sidecar match the restored caption.

    Restore writes ``caption_text`` to the DB, but rescan_dataset treats a
    differing sidecar as a caption edit and imports it back — so with
    auto_rescan_on_open enabled, a stale sidecar silently reverts the caption
    the restore just wrote seconds later. An empty restored caption removes the
    sidecar (the overwritten caption stays recoverable via the pre-restore
    auto-snapshot). Sync — always call via ``loop.run_in_executor``.
    """
    txt = Path(image_path).with_suffix(".txt")
    try:
        text = (caption or "").strip()
        if text:
            existing = txt.read_text(encoding="utf-8").strip() if txt.exists() else None
            if existing != text:
                txt.write_text(text, encoding="utf-8")
        elif txt.exists():
            txt.unlink()
    except (OSError, ValueError) as exc:
        # ValueError covers UnicodeDecodeError: a non-UTF-8 sidecar used to
        # escape Pass 3b and skip restore's final commit, losing every
        # compensating fixup with it.
        logger.warning("Caption sidecar sync failed for %s: %s", image_path, exc)


async def _backup_and_record_hash(
    image_id: str, file_path: str, dataset_folder: str, db: AsyncSession,
    precomputed_sha256: str | None = None,
) -> str | None:
    """Store file in the object store, backfill NULL-hash state rows. Returns sha256 or None.

    ``precomputed_sha256`` skips all I/O when its object already exists (the content
    is safely stored — correct even if the file changed since the caller hashed it).
    Otherwise the file is re-stored via ``_store_object`` and the backfilled hash is
    the hash of the bytes actually stored, so a file mutating between the caller's
    hash and the copy can never poison the store or the state rows.

    The copy goes through ``_store_row_bytes``, whose containment gate covers
    every call site of both hooks (``protect_file_before_overwrite``,
    ``mark_image_deleted_in_versions``) at once. Skip-and-log, per CLAUDE.md's
    destructive-site rule: an out-of-tree row still completes its overwrite or
    deletion instead of failing the whole job. The ``precomputed_sha256`` fast
    path stays in front of it because it copies nothing — it only records the
    hash of an object that is already stored.
    """
    null_check = await db.execute(
        select(VersionImageState.id).where(
            VersionImageState.image_id == image_id,
            VersionImageState.file_hash.is_(None),
        ).limit(1)
    )
    if null_check.first() is None or not Path(file_path).exists():
        return None
    loop = asyncio.get_running_loop()
    sha256 = precomputed_sha256
    if sha256 is None or not _object_store_path(dataset_folder, sha256).exists():
        sha256 = await loop.run_in_executor(
            None, _store_row_bytes, dataset_folder, file_path
        )
        if sha256 is None:
            logger.warning(
                "_backup_and_record_hash: refusing out-of-tree path for image %s: %s",
                image_id, file_path,
            )
            return None
    await db.execute(
        update(VersionImageState)
        .where(VersionImageState.image_id == image_id, VersionImageState.file_hash.is_(None))
        .values(file_hash=sha256)
    )
    await db.flush()
    return sha256


@dataclass
class _RestorePlan:
    """Per-image restore work item (see restore_snapshot's plan → protect →
    DB-commit → execute pass structure)."""
    state: VersionImageState
    img: Image | None
    target_path: str        # state.file_path — where the file must end up
    current_path: str       # img.file_path — where it is now (== target if not renamed)
    renamed: bool
    old_thumbnail: str | None   # img.thumbnail_path captured before metadata updates
    needs_restore: bool | None = None  # resolved in Pass 1 (in-place) or Pass 3 (renamed)
    staged: Path | None = None         # temp path when moved aside in Pass 3a
    file_restored: bool = False
    recreated: bool = False
    fork_new_id: str | None = None     # fresh ID when state.image_id now lives in another dataset
    skip_recreate: bool = False        # foreign ID + no recorded content — nothing to restore
    # GPT-025: the row's pixel-derived values before the restore (Pass 0, or
    # Pass 0b on adoption), put back when its content could not be written.
    pre_pixel: dict | None = None
    # GPT-026: the row's detections before the restore (canonical serialized
    # form), captured after Pass 0b so an adopted row's are the ones read.
    # Detection geometry is normalized to the pixels on disk, so it is a pixel
    # qualifier like `pre_pixel`: put back when the content could not be written.
    pre_detections: list[dict] | None = None
    detections_written: bool = False   # Pass 2c replaced this row's detections
    content_written: bool = False      # the snapshot's bytes are now at the target
    failure_recorded: bool = False     # counted once, however many steps then fail


class SnapshotIncomplete(RuntimeError):
    """A snapshot that had to be complete could not back up every file (GPT-023).

    Raised by ``create_snapshot(require_complete=True)`` — the pre-restore safety
    snapshot — before anything is committed, so no partial version is left and
    the branch head does not move. A ``RuntimeError`` so restore's wrapper turns
    it into the job's ``error_msg`` like any other abort.
    """


class RestoreRefused(RuntimeError):
    """A restore whose plan would write outside its dataset or onto another
    dataset's file (GPT-021, PM-025).

    Raised by Pass 0c, before the pre-restore snapshot and before any row or
    file is touched, so a refusal changes nothing. A `RuntimeError` so the job
    wrapper records it as the job's `error_msg` like any other abort.
    """


async def _refuse_unsafe_restore_paths(
    db: AsyncSession,
    dataset: Dataset,
    plans: list[_RestorePlan],
    extras_touched: list[Image],
) -> None:
    """Pass 0c — prove every path the restore will write or move is safe.

    A snapshot path is history: it names wherever the file lived when the
    snapshot was taken, which a dataset rename (or a pre-fix restore that
    followed one) can leave outside the dataset — and, once another dataset has
    taken the old name, on top of *that* dataset's files. Three rules, every
    violation collected and reported in one raise:

    1. **Targets** — the restored path of every plan that yields a row must
       resolve strictly inside the dataset folder, and not under `.versions`.
    2. **Renamed sources** — a file Pass 3 moves, stages or unlinks must be
       inside `settings.datasets_dir`. Looser than rule 1 on purpose, so a live
       row a pre-fix restore left under the old root can be moved back home.
    3. **Owned by another dataset** — no target, renamed source or touched extra
       may be another dataset's `Image.file_path`. Exact string match, which is
       sound because every writer builds paths from `datasets_dir / slug`.
    """
    def _resolve(path_str: str) -> Path | None:
        try:
            return Path(path_str).resolve() if path_str else None
        except (OSError, RuntimeError, ValueError):
            return None

    root = Path(dataset.folder_path).resolve()
    versions_dir = root / ".versions"
    violations: dict[str, str] = {}

    targets: set[str] = set()
    for p in plans:
        yields_row = p.img is not None or (
            p.state.image_id is not None and not p.skip_recreate
        )
        if not yields_row:
            continue
        targets.add(p.target_path)
        t = _resolve(p.target_path)
        # The derived thumbnail too: Pass 3 regenerates it at
        # `thumbnail_path_for(target)` (parent.parent), and `generate_thumbnail`
        # mkdirs its parent — a target directly in the root would write one
        # level above the dataset.
        thumb = _resolve(thumbnail_path_for(p.target_path)) if p.target_path else None
        if (
            t is None or thumb is None
            or t == root or not t.is_relative_to(root) or t.is_relative_to(versions_dir)
            or not thumb.is_relative_to(root)
        ):
            violations.setdefault(p.target_path, "outside the dataset folder")

    sources = {p.current_path for p in plans if p.img is not None and p.renamed}
    datasets_root = app_settings.datasets_dir
    for src in sources:
        try:
            inside = within_datasets_dir(src, datasets_root) is not None
        except (OSError, RuntimeError, ValueError):
            inside = False
        if not inside:
            violations.setdefault(src, "outside the datasets folder")

    checked = targets | sources | {e.file_path for e in extras_touched if e.file_path}
    for chunk in chunked(sorted(checked)):
        rows = await db.execute(
            select(Image.file_path, Dataset.name)
            .join(Dataset, Dataset.id == Image.dataset_id)
            .where(Image.file_path.in_(chunk), Image.dataset_id != dataset.id)
        )
        for fp, other_name in rows.all():
            violations.setdefault(fp, f"a file of dataset '{other_name}'")

    if violations:
        example_path, reason = next(iter(sorted(violations.items())))
        logger.error(
            "restore of dataset %s refused: %d unsafe path(s): %s",
            dataset.id, len(violations), sorted(violations.items())[:20],
        )
        raise RestoreRefused(
            f"Restore refused: {len(violations)} file path(s) in this snapshot are "
            f"unsafe to write — e.g. {example_path} is {reason}. Nothing was changed."
        )


# ---------------------------------------------------------------------------
# Copy-on-write hooks
# ---------------------------------------------------------------------------

async def _has_lazy_rows(db: AsyncSession, image_id: str) -> bool:
    """True when some snapshot row of this image still has `file_hash IS NULL`.

    The single gate for both hooks, and deliberately the only one: a lazy row
    claims "content unchanged since the snapshot", so whatever the current
    versioning mode, the bytes must be backed up before anything changes them.
    One indexed (`image_id`) `LIMIT 1` probe, so an image with no lazy rows —
    every image in a dataset that was never snapshotted in Auto mode — costs one
    cheap query per overwrite.
    """
    result = await db.execute(
        select(VersionImageState.id).where(
            VersionImageState.image_id == image_id,
            VersionImageState.file_hash.is_(None),
        ).limit(1)
    )
    return result.first() is not None


async def protect_file_before_overwrite(
    image_id: str, file_path: str, db: AsyncSession,
    precomputed_sha256: str | None = None,
) -> str | None:
    """Back up the file to the object store before an in-place overwrite.

    Fires in **every** versioning mode, gated only on `_has_lazy_rows` (GPT-022):
    an Auto snapshot's lazy rows still need protecting after the mode is switched
    to Manual or Off. A dataset with no lazy rows pays one indexed query and loads
    nothing else. Returns the SHA-256 hash, or None if no-op.

    The hash backfill is only flushed, not committed. Callers that overwrite the
    file before their own final commit must commit right after this call —
    otherwise a crash during the overwrite rolls back the backfill and the
    snapshot rows silently claim "content unchanged" (the post-overwrite file).
    """
    if not await _has_lazy_rows(db, image_id):
        return None
    img_row = await db.get(Image, image_id)
    dataset = await db.get(Dataset, img_row.dataset_id) if img_row else None
    if dataset is None:
        return None
    return await _backup_and_record_hash(
        image_id, file_path, dataset.folder_path, db, precomputed_sha256=precomputed_sha256
    )


async def mark_image_deleted_in_versions(
    image_id: str, file_path: str, db: AsyncSession
) -> None:
    """Back up a file to the object store just before it is deleted.

    Fires in **every** versioning mode, Off included, gated only on
    `_has_lazy_rows` (GPT-022) — a fully hashed snapshot already holds the bytes.
    Does NOT touch is_present — existing snapshot rows stay True so restoring a
    pre-deletion snapshot can re-create the image. Post-deletion snapshots simply
    won't include a row for the image at all.
    """
    if not await _has_lazy_rows(db, image_id):
        return
    img_row = await db.get(Image, image_id)
    dataset = await db.get(Dataset, img_row.dataset_id) if img_row else None
    if dataset is None:
        return
    await _backup_and_record_hash(image_id, file_path, dataset.folder_path, db)


# ---------------------------------------------------------------------------
# Branch helpers
# ---------------------------------------------------------------------------

async def _ensure_main_branch(db: AsyncSession, dataset_id: str) -> DatasetBranch:
    result = await db.execute(
        select(DatasetBranch).where(
            DatasetBranch.dataset_id == dataset_id,
            DatasetBranch.name == "main",
        )
    )
    branch = result.scalar_one_or_none()
    if branch is None:
        branch = DatasetBranch(dataset_id=dataset_id, name="main")
        db.add(branch)
        await db.flush()

        # Set as current branch on dataset
        await db.execute(
            update(Dataset).where(Dataset.id == dataset_id).values(current_branch_id=branch.id)
        )
        await db.flush()
    return branch


def _auto_snapshot_name(existing_names: set[str]) -> str:
    base = datetime.utcnow().strftime("Snapshot %Y-%m-%d %H:%M")
    if base not in existing_names:
        return base
    i = 2
    while f"{base} ({i})" in existing_names:
        i += 1
    return f"{base} ({i})"


# ---------------------------------------------------------------------------
# Snapshot creation
# ---------------------------------------------------------------------------

async def create_snapshot(
    db: AsyncSession,
    dataset_id: str,
    name: str | None,
    description: str,
    branch_id: str | None = None,
    parent_id: str | None = None,
    job_id: str | None = None,
    source: str = "manual",
    *,
    require_complete: bool = False,
) -> DatasetVersion:
    """Record a version of the dataset's current state.

    In Manual mode every file is copied into the object store eagerly. A
    per-file copy failure leaves that row's hash NULL — which a restore reads as
    "content unchanged" — so it is counted and surfaced as a ``[warning]`` line
    on the version. ``require_complete=True`` (the pre-restore safety snapshot)
    turns any such failure into ``SnapshotIncomplete`` instead, raised after a
    rollback and before the commit: a safety net with a hole is refused rather
    than recorded. A row whose path resolves outside the dataset is skipped
    (``_store_row_bytes`` refuses it) and reported on its own warning line; that
    is a refusal, not a failure, so it never aborts.
    """
    from backend.workers.progress import broadcaster

    settings = await get_thresholds(db)
    mode = settings.versioning_mode

    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset {dataset_id} not found")

    # Resolve branch: explicit branch_id wins; otherwise the dataset's current
    # branch (so auto-snapshots — pre-restore, checkout — land on the branch the
    # user is actually on); main is only a fallback for datasets that have never
    # branched.
    branch: DatasetBranch | None = None
    if branch_id is None:
        if dataset is not None and dataset.current_branch_id:
            branch = await db.get(DatasetBranch, dataset.current_branch_id)
            if branch is not None and branch.dataset_id != dataset_id:
                branch = None
        if branch is None:
            branch = await _ensure_main_branch(db, dataset_id)
        branch_id = branch.id
    else:
        # Ownership is checked here, not only in the router (GPT-024): a foreign
        # branch would record this dataset's version on it and move *that*
        # dataset's head. Raised before anything is added to the session.
        branch = await db.get(DatasetBranch, branch_id)
        if branch is None or branch.dataset_id != dataset_id:
            raise ValueError("Branch not found")

    # Resolve parent from branch head
    if parent_id is None and branch is not None:
        parent_id = branch.head_version_id

    # Auto-generate name if not supplied (scoped to the branch so names only need to be unique per branch)
    if name is None:
        existing = await db.execute(
            select(DatasetVersion.name).where(
                DatasetVersion.dataset_id == dataset_id,
                DatasetVersion.branch_id == branch_id,
            )
        )
        existing_names = {r[0] for r in existing.all() if r[0]}
        name = _auto_snapshot_name(existing_names)

    # Load all images for this dataset. undefer: VersionImageState mirrors every
    # provenance column including source_meta, which is deferred — without this the
    # snapshot build lazy-loads it on an async session (MissingGreenlet).
    result = await db.execute(
        select(Image).where(Image.dataset_id == dataset_id).options(undefer(Image.source_meta))
    )
    images = result.scalars().all()

    # Labels for the whole dataset in one query. A per-image lookup inside the
    # state loop below would be N+1 over a 20k-image dataset.
    labels_by_id = await labels_by_image(db, [img.id for img in images])
    # Detections the same way (GPT-026). Their geometry is in the frame of the
    # pixels being snapshotted, which is why it is recorded here at all.
    detections_by_id = await detections_by_image(db, [img.id for img in images])

    version = DatasetVersion(
        dataset_id=dataset_id,
        branch_id=branch_id,
        parent_id=parent_id,
        name=name,
        description=description,
        image_count=len(images),
        source=source,
    )
    db.add(version)
    await db.flush()

    loop = asyncio.get_running_loop()
    states = []
    backup_failures = 0
    first_failure: tuple[str, str] | None = None
    skipped_unsafe = 0

    for i, img in enumerate(images):
        file_hash: str | None = None

        if mode == "manual" and Path(img.file_path).exists():
            try:
                file_hash = await loop.run_in_executor(
                    None, _store_row_bytes, dataset.folder_path, img.file_path
                )
                if file_hash is None:
                    skipped_unsafe += 1
                    logger.warning(
                        "Snapshot: not backing up out-of-tree path for image %s: %s",
                        img.id, img.file_path,
                    )
            except Exception as exc:
                logger.warning("Could not back up %s: %s", img.file_path, exc)
                backup_failures += 1
                if first_failure is None:
                    first_failure = (img.filename, str(exc))
                file_hash = None

        states.append(VersionImageState(
            version_id=version.id,
            image_id=img.id,
            filename=img.filename,
            original_filename=img.original_filename,
            subfolder=img.subfolder,
            file_path=img.file_path,
            file_hash=file_hash,
            width=img.width,
            height=img.height,
            file_size_bytes=img.file_size_bytes,
            format=img.format,
            caption_text=img.caption_text or "",
            quality_flags=img.quality_flags,
            nsfw_score=img.nsfw_score,
            aesthetic_score=img.aesthetic_score,
            aesthetic_model=img.aesthetic_model,
            blur_score=img.blur_score,
            noise_score=img.noise_score,
            uniformity_score=img.uniformity_score,
            watermark_score=img.watermark_score,
            color_score=img.color_score,
            saturation_score=img.saturation_score,
            luminance_score=img.luminance_score,
            style_similarity_score=img.style_similarity_score,
            scores_stale=img.scores_stale,
            dino_layer_scores=img.dino_layer_scores,
            generation_metadata=img.generation_metadata,
            source_name=img.source_name,
            source_url=img.source_url,
            license=img.license,
            attribution=img.attribution,
            source_meta=img.source_meta,
            processing_history=img.processing_history,
            sort_order=img.sort_order,
            source_video_id=img.source_video_id,
            source_timestamp_ms=img.source_timestamp_ms,
            source_shot_index=img.source_shot_index,
            # Never `or None` — the column is NOT NULL, and an unlabelled image
            # snapshots as `[]`, which is what it means.
            label_ids=labels_by_id.get(img.id, []),
            # Always a list on a new snapshot — `[]` is "recorded: none". NULL is
            # reserved for rows that predate the column ("not recorded").
            detections=detections_by_id.get(img.id, []),
            is_present=True,
        ))

        if job_id and (i % 10 == 0 or i == len(images) - 1):
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "create_snapshot",
                "status": "running",
                "done": i + 1, "total": len(images),
                "percent": round((i + 1) / max(len(images), 1) * 100, 1),
                "message": f"Snapshotting {img.filename}",
            })

    if require_complete and backup_failures:
        # Nothing of this snapshot is committed: the version row, a lazily
        # created main branch and its current-branch update were only flushed.
        # Objects already copied stay as unreferenced files for prune to sweep.
        await db.rollback()
        fname, err = first_failure or ("?", "?")
        raise SnapshotIncomplete(
            f"{backup_failures} file(s) could not be backed up "
            f"(first: {fname}: {err})"
        )

    db.add_all(states)

    # A manual snapshot promises a full backup — a silent hole (file_hash=NULL
    # restores as "content unchanged") must be visible on the version card.
    warnings: list[str] = []
    if backup_failures:
        warnings.append(
            f"{backup_failures} file(s) could not be backed up to the object store"
        )
    if skipped_unsafe:
        warnings.append(
            f"{skipped_unsafe} file(s) outside the dataset folder were not backed up"
        )
    for warning in warnings:
        version.description = f"{version.description}\n[warning] {warning}".strip()
        logger.warning("Snapshot %s: %s", version.name, warning)
        if job_id:
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "create_snapshot",
                "status": "running",
                "done": len(images), "total": len(images), "percent": 100.0,
                "message": f"Warning: {warning}",
            })

    # Update branch head
    if branch is not None:
        branch.head_version_id = version.id

    await db.commit()
    return version


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

_DIFF_COLS = (
    VersionImageState.image_id,
    VersionImageState.file_path,
    VersionImageState.is_present,
    VersionImageState.filename,
    VersionImageState.subfolder,
    VersionImageState.file_hash,
    VersionImageState.caption_text,
    VersionImageState.quality_flags,
    VersionImageState.nsfw_score,
    VersionImageState.aesthetic_score,
    # Mutable like `scores_stale`, not immutable like lineage: a re-score with a
    # different model flips it, and which model produced a number is exactly the
    # kind of difference the diff exists to show.
    VersionImageState.aesthetic_model,
    VersionImageState.blur_score,
    VersionImageState.noise_score,
    VersionImageState.uniformity_score,
    VersionImageState.watermark_score,
    VersionImageState.color_score,
    VersionImageState.saturation_score,
    VersionImageState.luminance_score,
    VersionImageState.style_similarity_score,
    # Mutable, so diffed as well as mirrored — the carve-out below is for
    # *immutable* lineage columns only. An in-place pixel rewrite between two
    # snapshots flips this and nothing else, and that is exactly a difference
    # worth showing.
    VersionImageState.scores_stale,
    VersionImageState.dino_layer_scores,
    VersionImageState.generation_metadata,
    VersionImageState.source_name,
    VersionImageState.source_url,
    VersionImageState.license,
    VersionImageState.attribution,
    VersionImageState.source_meta,
    VersionImageState.sort_order,
    VersionImageState.processing_history,
    # Mutable state (attach/detach), so diffed as well as mirrored — the
    # immutable-lineage carve-out immediately below is not licence to skip it.
    # Because the *comparison* runs on ids, a label rename produces no diff at
    # all, which is the correct answer: the concept did not change. The ids are
    # resolved to names once, after the modified list is built, so `DiffModal`
    # shows names rather than uuids.
    VersionImageState.label_ids,
    # Mutable (a detection run, a crop's remap) and pixel-qualifying, so diffed.
    # Compared only when **both** sides recorded it: NULL is "not recorded" (a
    # row older than the column), and NULL vs a list says nothing about the
    # image. Rendered as a summary string, never the polygons — see
    # `_detections_change`.
    VersionImageState.detections,
    # Deliberately absent: source_video_id / source_timestamp_ms /
    # source_shot_index. Frame lineage is immutable for the life of an image —
    # extraction writes it once and nothing else ever changes it — so it can
    # never differ between two snapshots of the same image, and diffing it would
    # only add three columns to every state query for a guaranteed "unchanged".
    # It is still snapshotted and restored; it is just not *compared*.
)

# Diffed but reported only as {"changed": true} — the values can be tens of KB
# per image (full ComfyUI workflow JSON / per-layer score dicts / a scraper's raw
# sidecar payload).
_HEAVY_DIFF_FIELDS = frozenset({"dino_layer_scores", "generation_metadata", "source_meta"})

# The fields the comparison loop actually reads. A name here that is missing from
# `_DIFF_COLS` is not selected, so the attribute is absent and the diff silently
# reports "unchanged" for a value that did change — the two lists are hand-synced,
# and a test asserts this one is a subset of the columns above.
_DIFF_COMPARE_FIELDS = (
    "caption_text", "quality_flags", "subfolder",
    "nsfw_score", "aesthetic_score", "aesthetic_model", "blur_score", "noise_score", "uniformity_score",
    "watermark_score", "color_score", "saturation_score", "luminance_score",
    "style_similarity_score", "scores_stale",
    "dino_layer_scores", "generation_metadata", "processing_history",
    "source_name", "source_url", "license", "attribution",
    "source_meta", "sort_order", "label_ids", "detections",
)


def _detections_summary(dets: list[dict]) -> str:
    """`"3: face×2, hand"` — the count, then each label with its multiplicity."""
    if not dets:
        return "none"
    counts: dict[str, int] = {}
    for d in dets:
        label = d.get("label") or "?"
        counts[label] = counts.get(label, 0) + 1
    parts = [
        f"{label}×{n}" if n > 1 else label
        for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return f"{len(dets)}: " + ", ".join(parts)


def _detections_change(va: list[dict], vb: list[dict]) -> dict:
    """A detections diff entry as two summary strings, so `DiffModal` renders it
    like any scalar and the response never carries polygons.

    The lists already differ when this is called. When the summaries come out
    identical (a crop moves every box but keeps every label), the `to` side says
    what kind of change it was, so the row never reads "X → X": " (geometry
    changed)" when the boxes or masks differ, " (re-detected)" when only the
    score/model/task/time do.
    """
    frm, to = _detections_summary(va), _detections_summary(vb)
    if frm == to:
        def _geometry(dets):
            return sorted(
                (repr(d.get("bbox")), d.get("mask") or "") for d in dets
            )
        to += " (geometry changed)" if _geometry(va) != _geometry(vb) else " (re-detected)"
    return {"from": frm, "to": to}


async def diff_versions(
    db: AsyncSession,
    dataset_id: str,
    version_id_a: str,
    version_id_b: str,
) -> dict:
    for vid in (version_id_a, version_id_b):
        ver = await db.get(DatasetVersion, vid)
        if ver is None or ver.dataset_id != dataset_id:
            raise ValueError(f"Version {vid} not found")

    def _state_dict(rows: list) -> dict:
        out: dict = {}
        for s in rows:
            key = s.image_id or s.file_path
            out[key] = s
        return out

    result_a = await db.execute(
        select(*_DIFF_COLS).where(VersionImageState.version_id == version_id_a)
    )
    result_b = await db.execute(
        select(*_DIFF_COLS).where(VersionImageState.version_id == version_id_b)
    )
    states_a = _state_dict(result_a.all())
    states_b = _state_dict(result_b.all())

    keys_a = {k for k, v in states_a.items() if v.is_present}
    keys_b = {k for k, v in states_b.items() if v.is_present}

    added_keys = keys_b - keys_a
    removed_keys = keys_a - keys_b
    common_keys = keys_a & keys_b

    added = []
    for k in added_keys:
        s = states_b[k]
        added.append({"image_id": s.image_id, "filename": s.filename, "subfolder": s.subfolder, "caption": s.caption_text})

    removed = []
    for k in removed_keys:
        s = states_a[k]
        removed.append({"image_id": s.image_id, "filename": s.filename, "subfolder": s.subfolder, "caption": s.caption_text})

    modified = []
    unchanged_count = 0
    for k in common_keys:
        sa = states_a[k]
        sb = states_b[k]
        changes: dict[str, dict] = {}

        for field in _DIFF_COMPARE_FIELDS:
            va, vb = getattr(sa, field), getattr(sb, field)
            if va != vb:
                if field == "detections":
                    # NULL is "not recorded", not "none" — no row for a side
                    # that predates the column.
                    if va is not None and vb is not None:
                        changes[field] = _detections_change(va, vb)
                    continue
                # Heavy JSON columns (full ComfyUI workflow payloads, per-layer
                # score dicts) are compared but never embedded — a compact marker
                # keeps the diff response small.
                if field in _HEAVY_DIFF_FIELDS:
                    changes[field] = {"changed": True}
                else:
                    changes[field] = {"from": va, "to": vb}

        file_changed = (
            sa.file_hash is not None
            and sb.file_hash is not None
            and sa.file_hash != sb.file_hash
        )
        if file_changed:
            changes["file"] = {"from": sa.file_hash, "to": sb.file_hash}

        if changes:
            modified.append({
                "image_id": sb.image_id,
                "filename": sb.filename,
                "subfolder": sb.subfolder,
                "changes": changes,
            })
        else:
            unchanged_count += 1

    # Resolve label ids to names once for the whole diff, so `DiffModal` shows
    # "fx, reject" rather than two uuids. Joined server-side to a comma-separated
    # string because the modal renders `Object.entries(changes)` generically and a
    # raw array stringifies badly; an id with no row renders "(deleted)".
    referenced: set[str] = set()
    for entry in modified:
        for side in ("from", "to"):
            referenced.update(entry["changes"].get("label_ids", {}).get(side) or [])
    if referenced:
        names = dict((await db.execute(
            select(Label.id, Label.name).where(Label.id.in_(sorted(referenced)))
        )).all())
        for entry in modified:
            change = entry["changes"].get("label_ids")
            if not change:
                continue
            for side in ("from", "to"):
                ids = change.get(side) or []
                change[side] = ", ".join(names.get(i, "(deleted)") for i in ids)

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged_count": unchanged_count,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "unchanged": unchanged_count,
        },
    }


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

async def _discard_snapshot(db: AsyncSession, version_id: str) -> None:
    """Delete a just-committed pre-restore snapshot whose restore then aborted.

    Called from an abort path, so it must not raise over the error being
    reported: a failure here is logged and the version is left behind — a
    harmless extra snapshot of the unchanged dataset. The branch head moves
    back to the version's parent, which is where `create_snapshot` found it.
    """
    try:
        ver = await db.get(DatasetVersion, version_id)
        if ver is None:
            return
        if ver.branch_id:
            branch = await db.get(DatasetBranch, ver.branch_id)
            if branch is not None and branch.head_version_id == version_id:
                branch.head_version_id = ver.parent_id
        await db.delete(ver)
        await db.commit()
    except Exception as exc:
        logger.warning("Could not discard pre-restore snapshot %s: %s", version_id, exc)
        try:
            await db.rollback()
        except Exception:
            pass


async def restore_snapshot(
    db: AsyncSession,
    dataset_id: str,
    version_id: str,
    handle_extra_images: Literal["keep", "remove"] = "keep",
    pre_restore_snapshot: bool = True,
    job_id: str | None = None,
) -> dict:
    from backend.services.image_service import generate_thumbnail
    from backend.services.dataset_service import refresh_stats
    from backend.utils import rename_with_sidecar, unique_filename_with_thumb
    from backend.workers.progress import broadcaster

    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset {dataset_id} not found")
    # Refused before anything is read or written (GPT-024): another dataset's
    # version would plan that dataset's paths here and move its branch head.
    ver = await db.get(DatasetVersion, version_id)
    if ver is None or ver.dataset_id != dataset_id:
        raise ValueError("Version not found")

    # Load target version states
    result = await db.execute(
        select(VersionImageState).where(
            VersionImageState.version_id == version_id,
            VersionImageState.is_present.is_(True),
        )
    )
    target_states = result.scalars().all()

    # Current images in dataset
    cur_result = await db.execute(select(Image).where(Image.dataset_id == dataset_id))
    current_images = {img.id: img for img in cur_result.scalars().all()}
    target_image_ids = {s.image_id for s in target_states if s.image_id}

    loop = asyncio.get_running_loop()
    files_restored = 0
    files_unavailable = 0
    files_failed = 0
    extras_failed = 0
    thumbnails_stale = 0
    images_re_created = 0
    images_removed = 0
    # One entry per counted failure (capped), so the job's result_data can name
    # the files rather than only count them (GPT-025, PM-023).
    failures: list[dict] = []

    def _note_failure(name: str, error: object) -> None:
        if len(failures) < _FAILURE_DETAIL_CAP:
            failures.append({"file": name, "error": str(error)[:200]})

    def _record_failure(p: "_RestorePlan", kind: str, error: object) -> None:
        """Count a plan's failure once, as "failed" or "unavailable"."""
        nonlocal files_failed, files_unavailable
        if p.failure_recorded:
            return
        p.failure_recorded = True
        if kind == "unavailable":
            files_unavailable += 1
        else:
            files_failed += 1
        _note_failure(p.state.filename, error)

    # The restore runs in four passes so that (a) COW protection only fires for
    # files that will actually be overwritten/moved/deleted, hashing each file at
    # most once; (b) the DB is committed with the intended final paths BEFORE any
    # file moves (CLAUDE.md DB-before-filesystem invariant), with per-image
    # compensating fixups when a move fails; and (c) rename chains/swaps can't
    # clobber another image's current file (colliding sources are staged aside).

    # ── Pass 0: build a per-image plan (no I/O) ─────────────────────────────
    plans: list[_RestorePlan] = []
    for state in target_states:
        img = current_images.get(state.image_id) if state.image_id else None
        target_path = state.file_path
        current_path = img.file_path if img is not None else target_path
        plans.append(_RestorePlan(
            state=state,
            img=img,
            target_path=target_path,
            current_path=current_path,
            renamed=img is not None and current_path != target_path,
            old_thumbnail=img.thumbnail_path if img is not None else None,
            pre_pixel=_capture_pre_pixel(img) if img is not None else None,
        ))
    # ── Pass 0b: resolve snapshot images whose ID now lives in another dataset ──
    # (moved out via batch move — the ID cannot be re-inserted here). Adopt a
    # same-name current image whose content matches the snapshot (so repeat
    # restores don't duplicate), otherwise fork a fresh-ID row whose content
    # Pass 3 restores from the object store; with no recorded hash there is
    # nothing to materialize.
    current_by_filename = {im.filename: im for im in current_images.values()}
    adopted_ids: set[str] = set()
    for p in plans:
        if p.img is not None or p.state.image_id is None:
            continue
        foreign = await db.get(Image, p.state.image_id)
        if foreign is None:
            continue  # truly deleted — normal re-creation path
        candidate = current_by_filename.get(p.state.filename)
        if candidate is not None and candidate.id in target_image_ids:
            candidate = None  # belongs to another plan; never adopt it
        adopted = False
        if (
            handle_extra_images == "keep"
            and candidate is not None
            and p.state.file_hash is not None
            and Path(candidate.file_path).exists()
        ):
            try:
                cand_hash = await loop.run_in_executor(
                    None, _compute_sha256, candidate.file_path
                )
                adopted = cand_hash == p.state.file_hash
            except Exception:
                adopted = False
        if adopted:
            p.img = candidate
            p.current_path = candidate.file_path
            p.renamed = candidate.file_path != p.target_path
            p.old_thumbnail = candidate.thumbnail_path
            p.pre_pixel = _capture_pre_pixel(candidate)
            adopted_ids.add(candidate.id)
        elif p.state.file_hash is not None:
            p.fork_new_id = str(uuid4())
        else:
            p.skip_recreate = True

    # Detections of every row a plan will write, read once now that Pass 0b has
    # settled which row that is (an adopted plan's row is the candidate, not the
    # snapshot's id). Pass 2c compares against these to skip unchanged images,
    # and Pass 3b's reconcile puts them back.
    pre_dets = await detections_by_image(db, [p.img.id for p in plans if p.img is not None])
    for p in plans:
        if p.img is not None:
            p.pre_detections = pre_dets.get(p.img.id, [])

    all_targets = {p.target_path for p in plans}
    extra_imgs = [
        im for iid, im in current_images.items()
        if iid not in target_image_ids and iid not in adopted_ids
    ]

    # ── Pass 0c: path gate (no writes yet) ───────────────────────────────────
    # Everything above only read. The gate runs before the pre-restore snapshot
    # so that a refusal leaves no orphan safety version behind either: the
    # extras it checks are the ones Pass 2a/3 will move (keep mode: those on a
    # restored filename) or unlink (remove mode: all of them).
    if handle_extra_images == "remove":
        extras_touched = list(extra_imgs)
    else:
        restored_names = {p.state.filename for p in plans}
        extras_touched = [e for e in extra_imgs if e.filename in restored_names]
    await _refuse_unsafe_restore_paths(db, dataset, plans, extras_touched)

    # Auto-snapshot current state before restoring. A failed safety snapshot must
    # abort the restore — proceeding would silently make it non-undoable. The UI's
    # pre-restore checkbox is the explicit way to opt out. After planning (which
    # writes nothing) and the gate, before Pass 1 — whose COW backfill fills this
    # snapshot's NULL-hash rows.
    pre_restore_version_id: str | None = None
    if pre_restore_snapshot:
        try:
            # Complete or abort (GPT-023): a safety snapshot holding a NULL hash
            # for a file it failed to copy would let the restore overwrite the
            # only copy. Only Manual mode copies here; in Auto the backups are
            # Pass 1's, and a failure there aborts before any file changes.
            pre = await create_snapshot(
                db, dataset_id,
                name=None,
                description="Pre-restore auto-snapshot",
                job_id=None,
                source="pre_restore",
                require_complete=True,
            )
            pre_restore_version_id = pre.id
        except Exception as exc:
            logger.error("Pre-restore snapshot failed: %s", exc)
            raise RuntimeError(
                f"Pre-restore snapshot failed ({exc}); restore aborted before any "
                "changes were made. Disable the pre-restore snapshot option to "
                "restore anyway."
            ) from exc

    # ── Pass 1: gated COW protection, hash once (no dataset-file mutation) ──
    # Backs the current on-disk content into the pre-restore snapshot's NULL-hash
    # rows (auto-mode COW) before it can be overwritten, moved, or deleted — this
    # is what makes the restore undoable. Only fires for files the restore will
    # actually touch; a pure rename is moved, never overwritten, so it needs no
    # backup.
    #
    # All or nothing: a backup that raises here (an unreadable file, a full
    # disk) aborts the restore before any dataset file changes, and the
    # backfills already flushed roll back with the pre-restore snapshot
    # discarded — proceeding would overwrite a file nothing holds a copy of.
    try:
        for p in plans:
            if p.img is None or p.state.image_id is None:
                continue
            if p.state.file_hash is not None:
                if not p.renamed:
                    # In place: the target IS the current file — hash it once, decide
                    # needs_restore, and reuse the hash for the backup.
                    cur_hash: str | None = None
                    if Path(p.current_path).exists():
                        try:
                            cur_hash = await loop.run_in_executor(
                                None, _compute_sha256, p.current_path
                            )
                        except Exception:
                            pass
                    p.needs_restore = cur_hash != p.state.file_hash
                    if p.needs_restore and Path(p.current_path).exists():
                        # p.img.id, not state.image_id: an adopted plan's current row
                        # is a different image than the one the snapshot recorded.
                        # Protected even when hashing failed (cur_hash None): the
                        # hook then re-streams the file itself, and Pass 3 is about
                        # to overwrite it either way (new gap 6b).
                        await protect_file_before_overwrite(
                            p.img.id, p.current_path, db, precomputed_sha256=cur_hash
                        )
                else:
                    # Renamed with recorded content: the current file will be removed
                    # (or moved) after the restore — protect it. needs_restore is
                    # resolved in Pass 3 (the target may change during staging).
                    await protect_file_before_overwrite(p.img.id, p.current_path, db)
            # Pure rename (file_hash is None): moved, not overwritten — no protection.

        # Extras occupying a restore target would be clobbered by Pass 3 — back them
        # up first. In "remove" mode every extra is deleted anyway, so fire the
        # deletion hook for all of them here (before any FS write), making the
        # removal undoable via the pre-restore snapshot.
        #
        # The containment gate covers the **whole** loop, not just the "remove"
        # branch: both hooks end at `_store_object`, which copies the named file's
        # bytes into `{ds}/.versions/objects/` where a restore hands them back, so an
        # out-of-tree `file_path` is the same arbitrary-file read primitive either
        # way. Gated per row, and the restore carries on without the extra — the row
        # itself is still removed or renamed by Pass 2.
        for extra_img in extra_imgs:
            safe_extra = contained_path(
                extra_img.file_path, app_settings.datasets_dir,
                context="restore_snapshot extras", ident=extra_img.id,
            )
            if safe_extra is None:
                continue
            if handle_extra_images == "remove":
                await mark_image_deleted_in_versions(extra_img.id, str(safe_extra), db)
            elif extra_img.file_path in all_targets:
                await protect_file_before_overwrite(extra_img.id, str(safe_extra), db)
    except Exception as exc:
        logger.error("restore of dataset %s: Pass 1 backup failed: %s", dataset_id, exc)
        await db.rollback()
        if pre_restore_version_id is not None:
            await _discard_snapshot(db, pre_restore_version_id)
        raise RuntimeError(
            f"Could not back up a file before overwriting it ({exc}); restore "
            "aborted before any file was changed."
        ) from exc

    # ── Pass 2: all DB updates, then commit (DB is authoritative before FS) ──
    # Filename moves need DB-level staging too: SQLite checks uq_dataset_filename
    # per statement, so swaps/renumber chains and re-creations whose old name a
    # newer image now holds would abort the whole flush with an IntegrityError.
    # Order: clear extras off restored names (2a), park renamed images on unique
    # temp names (2b), then apply final values in one collision-free flush (2c).

    # Pass 2a: extras. "remove" deletes them now so the DELETEs are flushed
    # before any re-creation INSERT; "keep" renames extras occupying a restored
    # filename aside (uniquified, like import collisions) so both survive. The
    # matching file moves happen at the top of Pass 3a.
    extra_renames: list[tuple[Image, str, str | None]] = []  # (row, old_path, old_thumb)
    if handle_extra_images == "remove":
        # Deliberately **not** a `prune_orphaned_duplicate_flags` site, unlike every
        # router that deletes Image rows (PM-022): a restore rewrites `quality_flags`
        # wholesale from `VersionImageState`, so what this pass leaves behind is
        # snapshot-defined rather than delete-defined and a prune here would fight
        # the snapshot. A Technical re-scan is the repair.
        # Child rows go explicitly, as Pass 3b's reconcile does for a re-created
        # row, rather than trusting the FK cascade: a connection without
        # `PRAGMA foreign_keys` would orphan them, and orphaned detections still
        # count in the detection rollups.
        extra_ids = [e.id for e in extra_imgs]
        for chunk in chunked(extra_ids):
            await db.execute(delete(ImageLabel).where(ImageLabel.image_id.in_(chunk)))
            await db.execute(delete(Detection).where(Detection.image_id.in_(chunk)))
        for extra_img in extra_imgs:
            await db.delete(extra_img)
            images_removed += 1
        await db.flush()
    else:
        target_filenames = {p.state.filename for p in plans}
        colliding = [e for e in extra_imgs if e.filename in target_filenames]
        if colliding:
            db_names = {im.filename for im in current_images.values()} | target_filenames
            thumb_dir = Path(dataset.folder_path) / "thumbnails"
            occupied_thumb_stems = (
                {t.stem for t in thumb_dir.glob("*.webp")} if thumb_dir.exists() else set()
            )
            planned_thumb_stems: set[str] = set()
            for e in colliding:
                e_dir = Path(e.file_path).parent
                new_fn = unique_filename_with_thumb(
                    e_dir, Path(e.filename).stem, Path(e.filename).suffix,
                    db_names, occupied_thumb_stems, planned_thumb_stems,
                )
                extra_renames.append((e, e.file_path, e.thumbnail_path))
                e.filename = new_fn
                e.file_path = str(e_dir / new_fn)
                e.thumbnail_path = thumbnail_path_for(e.file_path)
            await db.flush()

    # Pass 2b: park renamed images on unique temp filenames.
    parked = [p for p in plans if p.img is not None and p.img.filename != p.state.filename]
    if parked:
        for p in parked:
            p.img.filename = (
                f"__dbtmp_{p.state.image_id[:8]}{Path(p.state.filename).suffix}"
            )
        await db.flush()

    # A snapshot outlives its video on purpose: `VersionImageState` carries no
    # FK, so deleting a video (which NULLs the live frames' lineage rather than
    # destroying curated data) leaves the stored id naming a row that is gone.
    # `Image.source_video_id` *is* a real FK, and `ondelete="SET NULL"` covers
    # only deleting the parent — an UPDATE to a missing parent key still raises
    # `FOREIGN KEY constraint failed`, aborting the whole restore permanently.
    # So resolve the ids that still exist and NULL the rest, the same fallback
    # `duplicate_dataset` uses when its old→new video map misses.
    snapshot_video_ids = {
        p.state.source_video_id for p in plans if p.state.source_video_id
    }
    live_video_ids: set[str] = set()
    for chunk in chunked(sorted(snapshot_video_ids)):
        rows = await db.execute(select(Video.id).where(Video.id.in_(chunk)))
        live_video_ids.update(rows.scalars().all())
    missing_video_ids = snapshot_video_ids - live_video_ids
    if missing_video_ids:
        logger.warning(
            "restore: %d snapshotted frame(s) name %d video(s) that no longer "
            "exist; restoring their lineage as NULL",
            sum(1 for p in plans if p.state.source_video_id in missing_video_ids),
            len(missing_video_ids),
        )

    # Pass 2c: final values; re-creation INSERTs are now collision-free.
    for p in plans:
        img = p.img
        if img is None and p.state.image_id is not None:
            if p.skip_recreate:
                _record_failure(
                    p, "unavailable",
                    "no recorded content (the image now lives in another dataset)",
                )
                continue
            img = Image(
                id=p.fork_new_id or p.state.image_id,
                dataset_id=dataset_id,
                filename=p.state.filename,
                original_filename=p.state.original_filename,
                subfolder=p.state.subfolder,
                file_path=p.target_path,  # the value Pass 0c gated
                thumbnail_path=thumbnail_path_for(p.target_path),
                width=p.state.width,
                height=p.state.height,
                file_size_bytes=p.state.file_size_bytes,
                format=p.state.format,
            )
            db.add(img)
            await db.flush()
            images_re_created += 1
            current_images[img.id] = img
            p.img = img
            p.recreated = True

        if img is None:
            continue
        state = p.state
        img.caption_text = state.caption_text
        img.quality_flags = state.quality_flags or {}
        img.filename = state.filename
        img.original_filename = state.original_filename
        img.file_path = p.target_path
        img.subfolder = state.subfolder
        img.nsfw_score = state.nsfw_score
        img.aesthetic_score = state.aesthetic_score
        img.aesthetic_model = state.aesthetic_model
        img.blur_score = state.blur_score
        img.noise_score = state.noise_score
        img.uniformity_score = state.uniformity_score
        img.watermark_score = state.watermark_score
        img.color_score = state.color_score
        img.saturation_score = state.saturation_score
        img.luminance_score = state.luminance_score
        img.style_similarity_score = state.style_similarity_score
        img.dino_layer_scores = state.dino_layer_scores
        img.generation_metadata = state.generation_metadata
        img.source_name = state.source_name
        img.source_url = state.source_url
        img.license = state.license
        img.attribution = state.attribution
        img.source_meta = state.source_meta
        img.processing_history = state.processing_history
        img.scores_stale = state.scores_stale
        img.sort_order = state.sort_order
        img.source_video_id = (
            state.source_video_id if state.source_video_id in live_video_ids else None
        )
        img.source_timestamp_ms = state.source_timestamp_ms
        img.source_shot_index = state.source_shot_index
        if state.width:
            img.width = state.width
        if state.height:
            img.height = state.height
        if state.file_size_bytes:
            img.file_size_bytes = state.file_size_bytes

    # Labels, at the end of Pass 2c and before the commit below — the DB→filesystem
    # boundary; everything past it is irreversible I/O.
    #
    # Keyed on `p.img.id`, **not** `p.state.image_id`: Pass 0b can fork a fresh
    # uuid or adopt a different row entirely, and writing the snapshot's id would
    # label an image in another dataset, or nothing at all. Plans with no `img`
    # (`skip_recreate`) are skipped.
    #
    # There is no FK behind `state.label_ids`, so ids are resolved against the
    # live vocabulary; a label deleted since the snapshot is dropped with one
    # aggregate warning rather than resurrected.
    #
    # A row Pass 0b forked (its image now lives in another dataset) needs no
    # label id remapping at all — the direct payoff of the vocabulary being
    # global rather than per-dataset.
    wanted_labels: dict[str, list[str]] = {}
    snapshot_label_ids: set[str] = set()
    for p in plans:
        if p.img is None:
            continue
        ids = list(p.state.label_ids or [])
        snapshot_label_ids.update(ids)
        wanted_labels[p.img.id] = ids
    if wanted_labels:
        live_labels = await live_label_ids(db, snapshot_label_ids)
        dropped = snapshot_label_ids - live_labels
        if dropped:
            logger.warning(
                "restore: %d snapshotted assignment(s) name %d label(s) deleted "
                "since the snapshot; dropping them",
                sum(1 for ids in wanted_labels.values() for i in ids if i in dropped),
                len(dropped),
            )
        # Authoritative replace, not merge: a restore means "the dataset looked
        # like this", so a label added after the snapshot disappears exactly as a
        # caption edit does. Only ids in `wanted_labels` are touched, so
        # `handle_extra_images="keep"` images keep theirs.
        await set_labels(db, {
            iid: [i for i in ids if i in live_labels] for iid, ids in wanted_labels.items()
        })

    # Detections (GPT-026), beside the labels and for the same reasons: keyed on
    # `p.img.id`, authoritative replace, only the ids named here are touched (so
    # keep-mode extras keep theirs), and all of it before the commit. Their
    # geometry is normalized to the snapshot's pixels, so restoring those pixels
    # without it left a later crop's boxes over the older picture.
    #
    # A state with `detections IS NULL` predates the mirror ("not recorded") and
    # leaves the image's current detections alone. An image whose detections
    # already equal the snapshot's is skipped, so a restore that changes nothing
    # does not churn detection ids.
    wanted_detections: dict[str, list[dict]] = {}
    for p in plans:
        if p.img is None or p.state.detections is None:
            continue
        snap_dets = canonical_detections(p.state.detections)
        if p.pre_detections is not None and p.pre_detections == snap_dets:
            continue
        wanted_detections[p.img.id] = snap_dets
        p.detections_written = True
    if wanted_detections:
        await set_detections(db, wanted_detections)

    await db.commit()

    # ── Pass 3a: move keep-mode extras off restored filenames, then stage
    # rename sources that another image's restore targets ──────────────────
    # Extras first: their old path is a restore target, so they must vacate it
    # before any restore write can land there.
    for e, old_path, old_thumb in extra_renames:
        try:
            src_e = Path(old_path)
            if src_e.exists():
                await loop.run_in_executor(
                    None, rename_with_sidecar, src_e, Path(e.file_path)
                )
            if old_thumb and old_thumb != e.thumbnail_path and Path(old_thumb).exists():
                await loop.run_in_executor(None, shutil.move, old_thumb, e.thumbnail_path)
        except Exception as exc:
            # Leave the row at its new name: the old path will be overwritten by
            # the restore; the extra's content was COW-protected in Pass 1.
            logger.warning("Rename-aside failed for extra %s: %s", e.filename, exc)
            extras_failed += 1
            _note_failure(e.filename, f"could not be moved aside: {exc}")

    # Handles swaps/renumber chains: moving each colliding source to a temp name
    # first means no restore write can land on top of a file that is still
    # someone's current content. The real suffix is preserved so
    # rename_with_sidecar keeps pairing the .txt sidecar.
    for p in plans:
        if p.renamed and p.current_path in all_targets:
            cur = Path(p.current_path)
            if cur.exists():
                tmp = cur.with_name(
                    f"{cur.stem}.__restore_tmp_{p.state.image_id[:8]}{cur.suffix}"
                )
                try:
                    await loop.run_in_executor(None, rename_with_sidecar, cur, tmp)
                    p.staged = tmp
                except Exception as exc:
                    # Counted, not made safe: the plan whose target this is may
                    # still write over the unstaged file (a known gap).
                    logger.warning("Staging failed for %s: %s", p.state.filename, exc)
                    _record_failure(p, "failed", f"could not be staged: {exc}")

    def _compensate_paths(p: _RestorePlan) -> None:
        """A move failed after Pass 2 committed the intended target paths — point
        the row back at wherever the file actually is (fixups land in the final
        commit). The thumbnail is left untouched."""
        actual = p.current_path
        if p.staged is not None:
            # Unstage only if the old spot is still free — another image's restore
            # may already have written its own file there.
            if Path(p.current_path).exists():
                actual = str(p.staged)
            else:
                try:
                    rename_with_sidecar(p.staged, Path(p.current_path))
                except Exception:
                    actual = str(p.staged)
        if p.img is not None:
            p.img.file_path = actual
            p.img.filename = Path(actual).name

    # ── Pass 3b: per-image filesystem execution ──────────────────────────────
    total = len(plans)
    for i, p in enumerate(plans):
        if job_id and (i % 5 == 0 or i == total - 1):
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "restore_snapshot",
                "status": "running",
                "done": i + 1, "total": total,
                "percent": round((i + 1) / max(total, 1) * 100, 1),
                "message": f"Restoring {p.state.filename}",
            })

        state = p.state
        src = p.staged if p.staged is not None else Path(p.current_path)
        target_file = Path(p.target_path)
        new_thumb = thumbnail_path_for(p.target_path)
        # Only remove the old thumbnail when the stem actually changed.
        stale_thumb = p.old_thumbnail if p.old_thumbnail and p.old_thumbnail != new_thumb else None

        # One image's failure (disk full, permissions) must not abort the batch:
        # compensate this row's paths and keep going, so no plan is left with a
        # committed target path and no fixup, and no staged file goes untracked.
        try:
            if state.file_hash is not None:
                needs_restore = p.needs_restore
                if needs_restore is None:  # renamed or re-created: decide now, post-staging
                    needs_restore = True
                    if target_file.exists():
                        try:
                            current_hash = await loop.run_in_executor(
                                None, _compute_sha256, str(target_file)
                            )
                            if current_hash == state.file_hash:
                                needs_restore = False
                        except Exception:
                            pass
                    p.needs_restore = needs_restore
                if not needs_restore:
                    p.content_written = True  # the target already holds those bytes

                object_path = _object_store_path(dataset.folder_path, state.file_hash)
                if needs_restore and object_path.exists():
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    await _write_object_atomically(
                        dataset.folder_path, object_path, target_file
                    )
                    p.file_restored = True
                    p.content_written = True
                    files_restored += 1
                    if p.renamed:
                        # The pre-rename copy (backed up in Pass 1) is now stale.
                        await loop.run_in_executor(
                            None, _remove_stale_files, str(src), True, stale_thumb
                        )
                elif needs_restore:
                    # Object store copy is missing. NEVER delete the current file —
                    # it may be the only remaining copy. If renamed, move it to the
                    # target so disk matches the committed DB paths.
                    _record_failure(
                        p, "unavailable", "its snapshot copy is missing from the object store"
                    )
                    if p.renamed and src.exists():
                        try:
                            target_file.parent.mkdir(parents=True, exist_ok=True)
                            await loop.run_in_executor(None, rename_with_sidecar, src, target_file)
                            p.file_restored = True  # name restored; content best-effort
                            if stale_thumb:
                                await loop.run_in_executor(
                                    None, _remove_stale_files, None, False, stale_thumb
                                )
                        except Exception as exc:
                            logger.warning("Rename restore failed for %s: %s", state.filename, exc)
                            _compensate_paths(p)
                elif p.renamed:
                    # Target already holds the snapshot content; the old copy is stale.
                    await loop.run_in_executor(
                        None, _remove_stale_files, str(src), True, stale_thumb
                    )

            elif p.renamed:
                # No recorded content change — the image was only renamed/moved since
                # the snapshot (auto mode leaves file_hash NULL for un-overwritten
                # files). Move the current file (and its .txt sidecar) back.
                if src.exists():
                    try:
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        await loop.run_in_executor(None, rename_with_sidecar, src, target_file)
                        p.file_restored = True
                        files_restored += 1
                        if stale_thumb:
                            # Only after a successful move; regenerated below.
                            await loop.run_in_executor(
                                None, _remove_stale_files, None, False, stale_thumb
                            )
                    except Exception as exc:
                        logger.warning("Rename restore failed for %s: %s", state.filename, exc)
                        _record_failure(p, "failed", f"could not be renamed back: {exc}")
                        _compensate_paths(p)
                elif target_file.exists():
                    # Source gone but the target already holds a file (e.g. an earlier
                    # partially-applied restore) — keep the committed target paths.
                    p.file_restored = True
                else:
                    _record_failure(p, "unavailable", "the file is missing")
                    _compensate_paths(p)

            elif p.recreated:
                # Re-created with no recorded content change: only a file
                # already at the target can back the row.
                if target_file.exists():
                    p.content_written = True
                else:
                    _record_failure(p, "unavailable", "no recorded content to re-create it from")
        except Exception as exc:
            logger.error("Restore failed for %s: %s", state.filename, exc)
            _record_failure(p, "failed", exc)
            _compensate_paths(p)

        # Reconcile a row whose content could not be written (GPT-025): Pass 2
        # committed the snapshot's metadata, but the pixels on disk are still
        # the pre-restore ones (the write is atomic, so they survived intact).
        # A re-created row has no pixels at all and goes; any other row gets
        # its pre-restore pixel values back and is re-measured — and its
        # pre-restore detections, whose geometry is in the frame of the pixels
        # still on disk (GPT-026). Caption, provenance, labels, sort order,
        # generation metadata and lineage keep the snapshot's values: the
        # metadata restore succeeded, only the pixels did not. Wrapped: Pass 3b
        # sits between two commits (PM-013).
        needed_content = p.recreated or (
            state.file_hash is not None and bool(p.needs_restore)
        )
        if p.img is not None and needed_content and not p.content_written:
            try:
                if p.recreated:
                    # Explicit, not left to the FK cascade: the harness and any
                    # connection without `PRAGMA foreign_keys` would orphan them.
                    await db.execute(delete(ImageLabel).where(ImageLabel.image_id == p.img.id))
                    await db.execute(delete(Detection).where(Detection.image_id == p.img.id))
                    await db.delete(p.img)
                    images_re_created -= 1
                    p.img = None
                else:
                    for key, value in (p.pre_pixel or {}).items():
                        setattr(p.img, key, value)
                    if p.detections_written:
                        await set_detections(db, {p.img.id: p.pre_detections or []})
                    await _measure_into(p.img)
            except Exception as exc:
                logger.warning("Could not reconcile %s after a failed restore: %s",
                               state.filename, exc)
        elif p.img is not None and needed_content:
            # The snapshot's bytes were written (or re-created): measure them
            # rather than trust a mirror (GPT-016). phash is not mirrored on
            # VersionImageState — a backfill would copy today's possibly stale
            # value into history — so without this an in-place restore kept the
            # edited image's phash and a re-created row got NULL, dropping out
            # of duplicate scans. Size, format and geometry come along.
            await _measure_into(p.img)

        # Regenerate the thumbnail whenever the file content changed or the stem
        # moved (a renamed image needs a thumbnail under its new stem, and its old
        # one was removed above).
        if p.img is not None and (p.file_restored or p.recreated or p.renamed) \
                and Path(p.img.file_path).exists():
            thumb = thumbnail_path_for(p.img.file_path)
            try:
                await loop.run_in_executor(None, generate_thumbnail, p.img.file_path, thumb)
                p.img.thumbnail_path = thumb
            except Exception as exc:
                logger.warning("Thumbnail regen failed for %s: %s", state.filename, exc)
                thumbnails_stale += 1
            # Force updated_at to change so the frontend's ?v= cache-bust param gets a new
            # value. Without this, if all other restored metadata values match what's already
            # in the DB, SQLAlchemy may skip the UPDATE entirely, leaving the browser serving
            # the pre-restore thumbnail from cache.
            p.img.updated_at = datetime.utcnow()

        # Sync the .txt sidecar with the restored caption even when the image file
        # itself was untouched (caption-only restores are the common case).
        if p.img is not None and Path(p.img.file_path).exists():
            await loop.run_in_executor(
                None, _sync_caption_sidecar, p.img.file_path, state.caption_text
            )

    # Sweep: no __restore_tmp file may survive untracked. A staged file whose
    # restore failed is recovered to its row's committed path when that spot is
    # free; otherwise the row is pointed at the staged file itself.
    for p in plans:
        if (
            p.staged is not None and p.staged.exists()
            and p.img is not None and not Path(p.img.file_path).exists()
        ):
            try:
                await loop.run_in_executor(
                    None, rename_with_sidecar, p.staged, Path(p.img.file_path)
                )
            except Exception:
                p.img.file_path = str(p.staged)
                p.img.filename = p.staged.name

    # ── Pass 3c: remove extra images' files (rows deleted + backed up above) ──
    if handle_extra_images == "remove":
        for extra_img in extra_imgs:
            if extra_img.file_path in all_targets:
                # A restored image now owns this path (its content was written
                # there in Pass 3b, or is pending as files_unavailable) —
                # unlinking would delete the restored file. Same stem means the
                # thumbnail is shared too; it gets regenerated above.
                continue
            await loop.run_in_executor(
                None, _remove_stale_files,
                extra_img.file_path, True, extra_img.thumbnail_path,
            )

    # Move branch head to the restored version so the UI marks it as Current.
    # The ownership check is belt-and-braces over the one at the top: a
    # version's branch is always its own dataset's, but a head move is the one
    # write here that reaches outside the dataset if that ever stops being true.
    ver = await db.get(DatasetVersion, version_id)
    if ver is not None and ver.branch_id:
        branch = await db.get(DatasetBranch, ver.branch_id)
        if branch is not None and branch.dataset_id == dataset_id:
            branch.head_version_id = version_id

    await db.commit()
    await refresh_stats(db, dataset_id)
    await db.commit()

    # The outcome travels in the return value, which the router writes to the
    # job's result_data: a restore that lost files still ends `completed`, so
    # this is the only place the loss is visible afterwards (PM-023, GPT-025).
    failed_count = files_failed + files_unavailable + extras_failed
    return {
        "files_restored": files_restored,
        "files_unavailable": files_unavailable,
        "files_failed": files_failed,
        "extras_failed": extras_failed,
        "thumbnails_stale": thumbnails_stale,
        "images_re_created": images_re_created,
        "images_removed": images_removed,
        "pre_restore_version_id": pre_restore_version_id,
        "failed_count": failed_count,
        "failure_summary": _failure_summary(failed_count, failures),
        "failed": failures,
    }


# ---------------------------------------------------------------------------
# Branch operations
# ---------------------------------------------------------------------------

async def create_branch(
    db: AsyncSession,
    dataset_id: str,
    branch_name: str,
    from_version_id: str | None = None,
    include_snapshot: bool = True,
) -> tuple[DatasetBranch, DatasetVersion | None]:
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError("Dataset not found")
    # A foreign `from_version_id` would record another dataset's version as this
    # branch-init snapshot's parent (GPT-024).
    if from_version_id is not None:
        src_ver = await db.get(DatasetVersion, from_version_id)
        if src_ver is None or src_ver.dataset_id != dataset_id:
            raise ValueError("Version not found")

    # Validate name uniqueness
    existing = await db.execute(
        select(DatasetBranch).where(
            DatasetBranch.dataset_id == dataset_id,
            DatasetBranch.name == branch_name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"Branch '{branch_name}' already exists")

    # Resolve source version: branch from the current branch's head (main only
    # as a fallback for never-branched datasets).
    if from_version_id is None:
        source_branch: DatasetBranch | None = None
        if dataset is not None and dataset.current_branch_id:
            source_branch = await db.get(DatasetBranch, dataset.current_branch_id)
            if source_branch is not None and source_branch.dataset_id != dataset_id:
                source_branch = None
        if source_branch is None:
            source_branch = await _ensure_main_branch(db, dataset_id)
        from_version_id = source_branch.head_version_id

    branch = DatasetBranch(dataset_id=dataset_id, name=branch_name)
    db.add(branch)
    await db.flush()

    if not include_snapshot:
        await db.commit()
        return branch, None

    version = await create_snapshot(
        db, dataset_id,
        name=f"Initial snapshot ({branch_name})",
        description=f"Branch created from version {from_version_id}",
        branch_id=branch.id,
        parent_id=from_version_id,
        source="branch_init",
    )
    return branch, version


async def checkout_branch(
    db: AsyncSession,
    dataset_id: str,
    target_branch_id: str,
    pre_restore_snapshot: bool = True,
    job_id: str | None = None,
) -> dict:
    branch = await db.get(DatasetBranch, target_branch_id)
    if branch is None or branch.dataset_id != dataset_id:
        raise ValueError("Branch not found")

    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError("Dataset not found")
    if branch.head_version_id is not None:
        head = await db.get(DatasetVersion, branch.head_version_id)
        if head is None or head.dataset_id != dataset_id:
            raise ValueError("Branch head version not found")

    result = {"branch_id": target_branch_id, "branch_name": branch.name}

    if branch.head_version_id is None:
        # Empty branch — just switch current_branch_id
        dataset.current_branch_id = target_branch_id
        await db.commit()
        result["restored"] = False
        return result

    restore_result = await restore_snapshot(
        db, dataset_id,
        version_id=branch.head_version_id,
        handle_extra_images="keep",
        pre_restore_snapshot=pre_restore_snapshot,
        job_id=job_id,
    )

    dataset = await db.get(Dataset, dataset_id)
    if dataset:
        dataset.current_branch_id = target_branch_id
        await db.commit()

    result["restored"] = True
    result.update(restore_result)
    return result


# ---------------------------------------------------------------------------
# Object-store GC
# ---------------------------------------------------------------------------

def _prune_objects_sync(
    dataset_folder: str, referenced: set[str], min_age_seconds: float
) -> dict:
    """Delete unreferenced objects and stale .tmp-* leftovers. Sync — call via executor.

    Files younger than ``min_age_seconds`` are kept regardless: a COW write racing
    the reference scan may have stored an object whose state row isn't committed
    yet (belt-and-suspenders on top of the dataset-busy flag).
    """
    objects_dir = Path(dataset_folder) / ".versions" / "objects"
    deleted = kept = 0
    bytes_freed = 0
    if not objects_dir.exists():
        return {"objects_deleted": 0, "objects_kept": 0, "bytes_freed": 0}
    cutoff = time.time() - min_age_seconds

    def _try_delete(path: Path) -> bool:
        nonlocal deleted, bytes_freed
        try:
            st = path.stat()
            if st.st_mtime > cutoff:
                return False
            path.unlink()
            deleted += 1
            bytes_freed += st.st_size
            return True
        except OSError:
            return False

    for entry in objects_dir.iterdir():
        if entry.is_file():
            if entry.name.startswith(".tmp-"):
                _try_delete(entry)
            continue
        if not entry.is_dir():
            continue
        for obj in entry.iterdir():
            if not obj.is_file():
                continue
            if entry.name + obj.name in referenced:
                kept += 1
            elif not _try_delete(obj):
                kept += 1
        try:
            entry.rmdir()  # only succeeds when empty
        except OSError:
            pass
    return {"objects_deleted": deleted, "objects_kept": kept, "bytes_freed": bytes_freed}


async def prune_object_store(
    db: AsyncSession,
    dataset_id: str,
    job_id: str | None = None,
    min_age_seconds: int = 3600,
) -> dict:
    """Delete object-store files no snapshot references. Returns a summary dict.

    Objects are per-dataset (the store lives under the dataset folder), so
    cross-dataset references are impossible — the reference set is exactly this
    dataset's non-NULL state hashes.
    """
    from backend.workers.progress import broadcaster

    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset {dataset_id} not found")

    result = await db.execute(
        select(VersionImageState.file_hash)
        .distinct()
        .join(DatasetVersion, VersionImageState.version_id == DatasetVersion.id)
        .where(
            DatasetVersion.dataset_id == dataset_id,
            VersionImageState.file_hash.is_not(None),
        )
    )
    referenced = {r[0] for r in result.all()}

    loop = asyncio.get_running_loop()
    summary = await loop.run_in_executor(
        None, _prune_objects_sync, dataset.folder_path, referenced, min_age_seconds
    )

    if job_id:
        mb = summary["bytes_freed"] / (1024 * 1024)
        await broadcaster.emit(job_id, {
            "type": "progress", "job_id": job_id, "job_type": "prune_versions",
            "status": "running", "done": 1, "total": 1, "percent": 100.0,
            "message": f"Freed {mb:.1f} MB ({summary['objects_deleted']} objects)",
        })
    return summary
