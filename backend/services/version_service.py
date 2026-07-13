"""
Dataset versioning service.

Object store layout:
  {dataset.folder_path}/.versions/objects/{sha256[:2]}/{sha256[2:]}

Copy-on-write (COW) strategy:
  - "off":    all hooks are no-ops
  - "manual": deletion hook fires; snapshot copies all files eagerly (full point-in-time backup)
  - "auto":   deletion hook fires; overwrite hook backs up files before overwrite (lazy COW)

is_present semantics:
  A VersionImageState row always has is_present=True — it records that the image was present
  in the dataset at snapshot time. Post-deletion snapshots simply have no row for the deleted
  image. This means restoring a pre-deletion snapshot correctly re-creates the image from the
  object store (the deletion hook backs up the file before it is unlinked).
"""
import asyncio
import hashlib
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.dataset import Dataset
from backend.models.image import Image
from backend.models.versioning import DatasetBranch, DatasetVersion, VersionImageState
from backend.services.threshold_service import get_thresholds

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


def _copy_to_object_store_if_absent(dataset_folder: str, src_path: str, sha256: str) -> None:
    dest = _object_store_path(dataset_folder, sha256)
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest)


def _remove_stale_files(file_path: str | None, sidecar: bool, thumbnail_path: str | None) -> None:
    """Best-effort removal of an image file, its .txt sidecar, and/or its thumbnail.

    Used by restore when an image was renamed/moved after the snapshot: the file left
    behind at its old on-disk location is an orphan and must be cleaned up.
    Sync — always call via ``loop.run_in_executor``.
    """
    try:
        if file_path:
            p = Path(file_path)
            if p.exists():
                p.unlink()
            if sidecar:
                txt = p.with_suffix(".txt")
                if txt.exists():
                    txt.unlink()
        if thumbnail_path:
            thumb = Path(thumbnail_path)
            if thumb.exists():
                thumb.unlink()
    except Exception:
        pass


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
    except OSError as exc:
        logger.warning("Caption sidecar sync failed for %s: %s", image_path, exc)


async def _backup_and_record_hash(
    image_id: str, file_path: str, dataset_folder: str, db: AsyncSession,
    precomputed_sha256: str | None = None,
) -> str | None:
    """Hash file, copy to object store, backfill NULL-hash state rows. Returns sha256 or None.

    ``precomputed_sha256`` skips the hashing pass when the caller already hashed the
    file (restore hashes once per image and reuses the result).
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
    if sha256 is None:
        sha256 = await loop.run_in_executor(None, _compute_sha256, file_path)
    await loop.run_in_executor(None, _copy_to_object_store_if_absent, dataset_folder, file_path, sha256)
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


# ---------------------------------------------------------------------------
# Copy-on-write hooks
# ---------------------------------------------------------------------------

async def protect_file_before_overwrite(
    image_id: str, file_path: str, db: AsyncSession,
    precomputed_sha256: str | None = None,
) -> str | None:
    """Back up the file to the object store before an in-place overwrite.

    Only fires in "auto" mode. The is_present=True filter is intentional: it avoids
    loading img/dataset when no active snapshot references this image with a null hash.
    Returns the SHA-256 hash, or None if no-op.
    """
    settings = await get_thresholds(db)
    if settings.versioning_mode != "auto":
        return None
    result = await db.execute(
        select(VersionImageState.id).where(
            VersionImageState.image_id == image_id,
            VersionImageState.file_hash.is_(None),
            VersionImageState.is_present.is_(True),
        ).limit(1)
    )
    if result.first() is None:
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

    Fires in both "manual" and "auto" modes. Does NOT touch is_present — existing
    snapshot rows stay True so restoring a pre-deletion snapshot can re-create the image.
    Post-deletion snapshots simply won't include a row for the image at all.
    """
    settings = await get_thresholds(db)
    if settings.versioning_mode == "off":
        return
    result = await db.execute(
        select(VersionImageState.id).where(VersionImageState.image_id == image_id).limit(1)
    )
    if result.first() is None:
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
) -> DatasetVersion:
    from backend.workers.progress import broadcaster

    settings = await get_thresholds(db)
    mode = settings.versioning_mode

    # Ensure main branch exists and resolve branch
    if branch_id is None:
        branch = await _ensure_main_branch(db, dataset_id)
        branch_id = branch.id
    else:
        branch = await db.get(DatasetBranch, branch_id)

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

    # Load all images for this dataset
    result = await db.execute(
        select(Image).where(Image.dataset_id == dataset_id)
    )
    images = result.scalars().all()

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

    dataset = await db.get(Dataset, dataset_id)
    loop = asyncio.get_running_loop()
    states = []

    for i, img in enumerate(images):
        file_hash: str | None = None

        if mode == "manual" and Path(img.file_path).exists():
            try:
                file_hash = await loop.run_in_executor(None, _compute_sha256, img.file_path)
                await loop.run_in_executor(
                    None, _copy_to_object_store_if_absent,
                    dataset.folder_path, img.file_path, file_hash
                )
            except Exception as exc:
                logger.warning("Could not back up %s: %s", img.file_path, exc)
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
            aesthetic_score=img.aesthetic_score,
            blur_score=img.blur_score,
            noise_score=img.noise_score,
            uniformity_score=img.uniformity_score,
            watermark_score=img.watermark_score,
            color_score=img.color_score,
            style_similarity_score=img.style_similarity_score,
            dino_layer_scores=img.dino_layer_scores,
            generation_metadata=img.generation_metadata,
            processing_history=img.processing_history,
            sort_order=img.sort_order,
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

    db.add_all(states)

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
    VersionImageState.aesthetic_score,
    VersionImageState.blur_score,
    VersionImageState.noise_score,
    VersionImageState.uniformity_score,
    VersionImageState.watermark_score,
    VersionImageState.color_score,
    VersionImageState.style_similarity_score,
    VersionImageState.dino_layer_scores,
    VersionImageState.generation_metadata,
    VersionImageState.sort_order,
    VersionImageState.processing_history,
)

# Diffed but reported only as {"changed": true} — the values can be tens of KB
# per image (full ComfyUI workflow JSON / per-layer score dicts).
_HEAVY_DIFF_FIELDS = frozenset({"dino_layer_scores", "generation_metadata"})


async def diff_versions(
    db: AsyncSession,
    dataset_id: str,
    version_id_a: str,
    version_id_b: str,
) -> dict:
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

        for field in ("caption_text", "quality_flags", "subfolder",
                      "aesthetic_score", "blur_score", "noise_score", "uniformity_score",
                      "watermark_score", "color_score", "style_similarity_score",
                      "dino_layer_scores", "generation_metadata", "processing_history",
                      "sort_order"):
            va, vb = getattr(sa, field), getattr(sb, field)
            if va != vb:
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
    from backend.utils import rename_with_sidecar, thumbnail_path_for
    from backend.workers.progress import broadcaster

    # Auto-snapshot current state before restoring
    pre_restore_version_id: str | None = None
    if pre_restore_snapshot:
        try:
            pre = await create_snapshot(
                db, dataset_id,
                name=None,
                description="Pre-restore auto-snapshot",
                job_id=None,
                source="pre_restore",
            )
            pre_restore_version_id = pre.id
        except Exception as exc:
            logger.warning("Pre-restore snapshot failed: %s", exc)

    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset {dataset_id} not found")

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
    images_re_created = 0
    images_removed = 0

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
        ))
    all_targets = {p.target_path for p in plans}
    extra_imgs = [im for iid, im in current_images.items() if iid not in target_image_ids]

    # ── Pass 1: gated COW protection, hash once (no dataset-file mutation) ──
    # Backs the current on-disk content into the pre-restore snapshot's NULL-hash
    # rows (auto-mode COW) before it can be overwritten, moved, or deleted — this
    # is what makes the restore undoable. Only fires for files the restore will
    # actually touch; a pure rename is moved, never overwritten, so it needs no
    # backup.
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
                if p.needs_restore and cur_hash is not None:
                    await protect_file_before_overwrite(
                        p.state.image_id, p.current_path, db, precomputed_sha256=cur_hash
                    )
            else:
                # Renamed with recorded content: the current file will be removed
                # (or moved) after the restore — protect it. needs_restore is
                # resolved in Pass 3 (the target may change during staging).
                await protect_file_before_overwrite(p.state.image_id, p.current_path, db)
        # Pure rename (file_hash is None): moved, not overwritten — no protection.

    # Extras occupying a restore target would be clobbered by Pass 3 — back them
    # up first. In "remove" mode every extra is deleted anyway, so fire the
    # deletion hook for all of them here (before any FS write), making the
    # removal undoable via the pre-restore snapshot.
    if handle_extra_images == "remove":
        for extra_img in extra_imgs:
            await mark_image_deleted_in_versions(extra_img.id, extra_img.file_path, db)
    else:
        for extra_img in extra_imgs:
            if extra_img.file_path in all_targets:
                await protect_file_before_overwrite(extra_img.id, extra_img.file_path, db)

    # ── Pass 2: all DB updates, then commit (DB is authoritative before FS) ──
    for p in plans:
        img = p.img
        if img is None and p.state.image_id is not None:
            img = Image(
                id=p.state.image_id,
                dataset_id=dataset_id,
                filename=p.state.filename,
                original_filename=p.state.original_filename,
                subfolder=p.state.subfolder,
                file_path=p.state.file_path,
                thumbnail_path=thumbnail_path_for(p.state.file_path),
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
        img.aesthetic_score = state.aesthetic_score
        img.blur_score = state.blur_score
        img.noise_score = state.noise_score
        img.uniformity_score = state.uniformity_score
        img.watermark_score = state.watermark_score
        img.color_score = state.color_score
        img.style_similarity_score = state.style_similarity_score
        img.dino_layer_scores = state.dino_layer_scores
        img.generation_metadata = state.generation_metadata
        img.processing_history = state.processing_history
        img.sort_order = state.sort_order
        if state.width:
            img.width = state.width
        if state.height:
            img.height = state.height
        if state.file_size_bytes:
            img.file_size_bytes = state.file_size_bytes

    if handle_extra_images == "remove":
        for extra_img in extra_imgs:
            await db.delete(extra_img)
            images_removed += 1

    await db.commit()

    # ── Pass 3a: stage rename sources that another image's restore targets ──
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
                    logger.warning("Staging failed for %s: %s", p.state.filename, exc)

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

        if state.file_hash is not None:
            needs_restore = p.needs_restore
            if needs_restore is None:  # renamed: decide now, post-staging
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

            object_path = _object_store_path(dataset.folder_path, state.file_hash)
            if needs_restore and object_path.exists():
                target_file.parent.mkdir(parents=True, exist_ok=True)
                await loop.run_in_executor(None, shutil.copy2, str(object_path), str(target_file))
                p.file_restored = True
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
                files_unavailable += 1
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
                    _compensate_paths(p)
            elif target_file.exists():
                # Source gone but the target already holds a file (e.g. an earlier
                # partially-applied restore) — keep the committed target paths.
                p.file_restored = True
            else:
                files_unavailable += 1
                _compensate_paths(p)

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

    # ── Pass 3c: remove extra images' files (rows deleted + backed up above) ──
    if handle_extra_images == "remove":
        for extra_img in extra_imgs:
            await loop.run_in_executor(
                None, _remove_stale_files,
                extra_img.file_path, True, extra_img.thumbnail_path,
            )

    # Move branch head to the restored version so the UI marks it as Current
    ver = await db.get(DatasetVersion, version_id)
    if ver and ver.branch_id:
        branch = await db.get(DatasetBranch, ver.branch_id)
        if branch:
            branch.head_version_id = version_id

    await db.commit()
    await refresh_stats(db, dataset_id)
    await db.commit()

    return {
        "files_restored": files_restored,
        "files_unavailable": files_unavailable,
        "images_re_created": images_re_created,
        "images_removed": images_removed,
        "pre_restore_version_id": pre_restore_version_id,
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
    # Validate name uniqueness
    existing = await db.execute(
        select(DatasetBranch).where(
            DatasetBranch.dataset_id == dataset_id,
            DatasetBranch.name == branch_name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"Branch '{branch_name}' already exists")

    # Resolve source version
    if from_version_id is None:
        main_branch = await _ensure_main_branch(db, dataset_id)
        from_version_id = main_branch.head_version_id

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
