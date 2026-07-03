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


async def _backup_and_record_hash(
    image_id: str, file_path: str, dataset_folder: str, db: AsyncSession
) -> str | None:
    """Hash file, copy to object store, backfill NULL-hash state rows. Returns sha256 or None."""
    null_check = await db.execute(
        select(VersionImageState.id).where(
            VersionImageState.image_id == image_id,
            VersionImageState.file_hash.is_(None),
        ).limit(1)
    )
    if null_check.first() is None or not Path(file_path).exists():
        return None
    loop = asyncio.get_running_loop()
    sha256 = await loop.run_in_executor(None, _compute_sha256, file_path)
    await loop.run_in_executor(None, _copy_to_object_store_if_absent, dataset_folder, file_path, sha256)
    await db.execute(
        update(VersionImageState)
        .where(VersionImageState.image_id == image_id, VersionImageState.file_hash.is_(None))
        .values(file_hash=sha256)
    )
    await db.flush()
    return sha256


# ---------------------------------------------------------------------------
# Copy-on-write hooks
# ---------------------------------------------------------------------------

async def protect_file_before_overwrite(
    image_id: str, file_path: str, db: AsyncSession
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
    return await _backup_and_record_hash(image_id, file_path, dataset.folder_path, db)


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
    VersionImageState.style_similarity_score,
    VersionImageState.sort_order,
    VersionImageState.processing_history,
)


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
                      "watermark_score", "style_similarity_score", "processing_history",
                      "sort_order"):
            va, vb = getattr(sa, field), getattr(sb, field)
            if va != vb:
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
    from backend.utils import thumbnail_path_for
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

    total = len(target_states)
    for i, state in enumerate(target_states):
        if job_id and (i % 5 == 0 or i == total - 1):
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "restore_snapshot",
                "status": "running",
                "done": i + 1, "total": total,
                "percent": round((i + 1) / max(total, 1) * 100, 1),
                "message": f"Restoring {state.filename}",
            })

        img = current_images.get(state.image_id) if state.image_id else None

        # Restore file content if hash recorded
        file_restored = False
        if state.file_hash is not None:
            object_path = _object_store_path(dataset.folder_path, state.file_hash)
            current_file = Path(state.file_path)

            needs_restore = True
            if current_file.exists():
                try:
                    current_hash = await loop.run_in_executor(None, _compute_sha256, str(current_file))
                    if current_hash == state.file_hash:
                        needs_restore = False
                except Exception:
                    pass

            if needs_restore:
                if object_path.exists():
                    current_file.parent.mkdir(parents=True, exist_ok=True)
                    await loop.run_in_executor(None, shutil.copy2, str(object_path), str(current_file))
                    file_restored = True
                    files_restored += 1
                else:
                    files_unavailable += 1

        # Re-create Image row if it was deleted
        if img is None and state.image_id is not None:
            img = Image(
                id=state.image_id,
                dataset_id=dataset_id,
                filename=state.filename,
                original_filename=state.original_filename,
                subfolder=state.subfolder,
                file_path=state.file_path,
                thumbnail_path=thumbnail_path_for(state.file_path),
                width=state.width,
                height=state.height,
                file_size_bytes=state.file_size_bytes,
                format=state.format,
            )
            db.add(img)
            await db.flush()
            images_re_created += 1
            current_images[img.id] = img
            file_restored = True

        # Restore metadata
        if img is not None:
            img.caption_text = state.caption_text
            img.quality_flags = state.quality_flags or {}
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

            # Regenerate thumbnail if file was restored
            if file_restored and Path(state.file_path).exists():
                thumb = thumbnail_path_for(state.file_path)
                try:
                    await loop.run_in_executor(None, generate_thumbnail, state.file_path, thumb)
                    img.thumbnail_path = thumb
                except Exception as exc:
                    logger.warning("Thumbnail regen failed for %s: %s", state.filename, exc)
                # Force updated_at to change so the frontend's ?v= cache-bust param gets a new
                # value. Without this, if all other restored metadata values match what's already
                # in the DB, SQLAlchemy may skip the UPDATE entirely, leaving the browser serving
                # the pre-restore thumbnail from cache.
                img.updated_at = datetime.utcnow()

    # Handle extra images not in the snapshot
    if handle_extra_images == "remove":
        extra_ids = set(current_images.keys()) - target_image_ids
        for extra_id in extra_ids:
            extra_img = current_images[extra_id]
            # Fire the COW hook so the pre-restore snapshot (created moments ago with a
            # NULL hash for this image) backs the file up before it is unlinked, making the
            # restore itself undoable in auto mode.
            await mark_image_deleted_in_versions(extra_img.id, extra_img.file_path, db)
            try:
                p = Path(extra_img.file_path)
                if p.exists():
                    p.unlink()
                sidecar = p.with_suffix(".txt")
                if sidecar.exists():
                    sidecar.unlink()
                if extra_img.thumbnail_path:
                    thumb = Path(extra_img.thumbnail_path)
                    if thumb.exists():
                        thumb.unlink()
            except Exception:
                pass
            await db.delete(extra_img)
            images_removed += 1

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
