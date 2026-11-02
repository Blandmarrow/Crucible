import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import BackgroundJob, Dataset, Image
from backend.models.versioning import DatasetBranch, DatasetVersion, VersionImageState
from backend.schemas.versioning import (
    BranchCreate, BranchOut,
    CheckoutRequest,
    DiffOut,
    RatingImpact,
    RestoreRequest, SnapshotCreate, VersionOut, VersionUpdate,
)
from backend.services import version_service
from backend.services.dataset_busy import busy
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/datasets", tags=["versioning"])

_SNAPSHOT_INLINE_LIMIT = 100


async def _require_versioning_enabled(db: AsyncSession) -> str:
    """Refuse a versioning *write* when the mode is "off", and return the mode.

    Off means the user turned the feature off, so nothing here may create,
    restore, delete or prune versions. The UI hides the page, which is not a
    guard: the routes are reachable directly. (The guard used to carry a second
    reason — both copy-on-write hooks no-op'd in Off, so a restore here
    overwrote files with nothing kept. They now fire in every mode, gated on
    lazy snapshot rows rather than the mode (GPT-022), so existing snapshots
    stay protected; the refusal is about the feature being off.)

    The four read routes are deliberately *not* gated. `Sidebar` and
    `DatasetsPage` call `listBranches`/`listVersions` whatever the mode is, so
    gating them would turn a disabled feature into a wall of 400s.
    """
    from backend.services.threshold_service import get_thresholds
    settings = await get_thresholds(db)
    if settings.versioning_mode == "off":
        raise HTTPException(400, "Versioning is disabled. Enable it in Settings first.")
    return settings.versioning_mode


async def _store_result(session: AsyncSession, job_id: str, result: dict) -> None:
    """Write a restore/checkout outcome to the job row's `result_data`.

    A restore that could not restore some files still ends `completed`, so the
    counts and `failure_summary` are the only record of it once the live
    progress is gone (PM-023's `failed_count` convention, GPT-025). Called
    inside the `busy` block, before the job queue marks the row completed.
    """
    job_row = await session.get(BackgroundJob, job_id)
    if job_row is not None:
        job_row.result_data = result
        await session.commit()


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------

@router.get("/{dataset_id}/versions/branches", response_model=list[BranchOut])
async def list_branches(dataset_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DatasetBranch).where(DatasetBranch.dataset_id == dataset_id)
    )
    branches = result.scalars().all()

    head_ids = [b.head_version_id for b in branches if b.head_version_id]
    version_names: dict[str, str | None] = {}
    if head_ids:
        vr = await db.execute(
            select(DatasetVersion.id, DatasetVersion.name).where(
                DatasetVersion.id.in_(head_ids)
            )
        )
        for vid, vname in vr.all():
            version_names[vid] = vname

    return [
        BranchOut.model_validate({
            "id": b.id,
            "dataset_id": b.dataset_id,
            "name": b.name,
            "head_version_id": b.head_version_id,
            "head_version_name": version_names.get(b.head_version_id) if b.head_version_id else None,
            "created_at": b.created_at,
        })
        for b in branches
    ]


@router.post("/{dataset_id}/versions/branches", response_model=BranchOut)
async def create_branch(
    dataset_id: str, body: BranchCreate, db: AsyncSession = Depends(get_db)
):
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Dataset not found")
    # Before the job row, so a foreign or bogus id is a 404 rather than a failed
    # job; the service checks again (GPT-024).
    if body.from_version_id is not None:
        src_ver = await db.get(DatasetVersion, body.from_version_id)
        if src_ver is None or src_ver.dataset_id != dataset_id:
            raise HTTPException(404, "Version not found")
    await _require_versioning_enabled(db)

    # Count images to decide sync vs async
    from sqlalchemy import func
    from backend.models.image import Image
    count_result = await db.execute(
        select(func.count(Image.id)).where(Image.dataset_id == dataset_id)
    )
    image_count = count_result.scalar() or 0

    if image_count <= _SNAPSHOT_INLINE_LIMIT or not body.include_snapshot:
        try:
            branch, _ = await version_service.create_branch(
                db, dataset_id, body.name, body.from_version_id, body.include_snapshot
            )
            return branch
        except ValueError as e:
            raise HTTPException(400, str(e))

    # Background job for large datasets
    job = BackgroundJob(job_type="create_branch", label=f"Branch: {body.name}", dataset_id=dataset_id, total_items=image_count)
    db.add(job)
    await db.commit()

    branch_name = body.name
    from_vid = body.from_version_id
    inc_snap = body.include_snapshot

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session, busy(dataset_id, "branch snapshot"):
            try:
                await version_service.create_branch(session, dataset_id, branch_name, from_vid, inc_snap)
            except Exception as exc:
                logger.error("create_branch job failed: %s", exc)
                raise

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.delete("/{dataset_id}/versions/branches/{branch_id}", status_code=204)
async def delete_branch(
    dataset_id: str, branch_id: str, db: AsyncSession = Depends(get_db)
):
    branch = await db.get(DatasetBranch, branch_id)
    if branch is None or branch.dataset_id != dataset_id:
        raise HTTPException(404, "Branch not found")
    await _require_versioning_enabled(db)

    count_result = await db.execute(
        select(func.count(DatasetBranch.id)).where(DatasetBranch.dataset_id == dataset_id)
    )
    if count_result.scalar() <= 1:
        raise HTTPException(400, "Cannot delete the only branch")

    dataset = await db.get(Dataset, dataset_id)
    if dataset.current_branch_id == branch_id:
        raise HTTPException(400, "Cannot delete the active branch. Switch to another branch first.")

    await db.delete(branch)
    await db.commit()


@router.post("/{dataset_id}/versions/branches/{branch_id}/checkout")
async def checkout_branch(
    dataset_id: str, branch_id: str,
    body: CheckoutRequest = CheckoutRequest(),
    db: AsyncSession = Depends(get_db),
):
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Dataset not found")

    branch = await db.get(DatasetBranch, branch_id)
    if branch is None or branch.dataset_id != dataset_id:
        raise HTTPException(404, "Branch not found")
    await _require_versioning_enabled(db)

    job = BackgroundJob(job_type="checkout_branch", label=f"Checkout: {branch.name}", dataset_id=dataset_id, total_items=1)
    db.add(job)
    await db.commit()

    pre_restore = body.pre_restore_snapshot

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session, busy(dataset_id, "branch checkout"):
            result = await version_service.checkout_branch(
                session, dataset_id, branch_id,
                pre_restore_snapshot=pre_restore,
                job_id=job_id,
            )
            await _store_result(session, job_id, result)

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

@router.get("/{dataset_id}/versions", response_model=list[VersionOut])
async def list_versions(
    dataset_id: str,
    branch_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
    created_after: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    q = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    if branch_id is not None:
        q = q.where(DatasetVersion.branch_id == branch_id)
    if search:
        term = f"%{search}%"
        q = q.where(or_(
            DatasetVersion.name.ilike(term),
            DatasetVersion.description.ilike(term),
        ))
    if created_after:
        try:
            q = q.where(DatasetVersion.created_at >= datetime.fromisoformat(created_after))
        except ValueError:
            pass
    if created_before:
        try:
            q = q.where(DatasetVersion.created_at < datetime.fromisoformat(created_before) + timedelta(days=1))
        except ValueError:
            pass
    q = q.order_by(
        DatasetVersion.is_pinned.desc(),
        DatasetVersion.created_at.desc(),
    ).offset(offset).limit(limit)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/{dataset_id}/versions")
async def create_snapshot(
    dataset_id: str, body: SnapshotCreate, db: AsyncSession = Depends(get_db)
):
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Dataset not found")
    # A foreign branch would take this dataset's version and move *its* head
    # (GPT-024). Checked before the job row, matching the checkout route; the
    # service refuses it too.
    if body.branch_id is not None:
        branch = await db.get(DatasetBranch, body.branch_id)
        if branch is None or branch.dataset_id != dataset_id:
            raise HTTPException(404, "Branch not found")

    from sqlalchemy import func
    from backend.models.image import Image
    count_result = await db.execute(
        select(func.count(Image.id)).where(Image.dataset_id == dataset_id)
    )
    image_count = count_result.scalar() or 0

    mode = await _require_versioning_enabled(db)

    # Manual mode always runs as background job; auto mode inline if small
    if mode == "manual" or image_count > _SNAPSHOT_INLINE_LIMIT:
        job = BackgroundJob(
            job_type="create_snapshot",
            label=f"Snapshot: {body.name or 'auto'}",
            dataset_id=dataset_id,
            total_items=image_count,
        )
        db.add(job)
        await db.commit()

        snap_name = body.name
        snap_desc = body.description
        snap_branch_id = body.branch_id

        async def _run(job_id: str) -> None:
            from backend.database import AsyncSessionLocal
            async with AsyncSessionLocal() as session, busy(dataset_id, "snapshot"):
                await version_service.create_snapshot(
                    session, dataset_id,
                    name=snap_name, description=snap_desc,
                    branch_id=snap_branch_id, job_id=job_id,
                )

        await job_queue.enqueue(job, _run)
        return {"job_id": job.id}

    try:
        version = await version_service.create_snapshot(
            db, dataset_id,
            name=body.name, description=body.description,
            branch_id=body.branch_id,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return VersionOut.model_validate(version)


# IMPORTANT: declare /diff BEFORE /{version_id} to prevent FastAPI path collision
@router.get("/{dataset_id}/versions/diff", response_model=DiffOut)
async def diff_versions(
    dataset_id: str,
    v1: str = Query(...),
    v2: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    for vid in (v1, v2):
        ver = await db.get(DatasetVersion, vid)
        if ver is None or ver.dataset_id != dataset_id:
            raise HTTPException(404, f"Version {vid} not found")

    result = await version_service.diff_versions(db, dataset_id, v1, v2)
    return result


@router.get("/{dataset_id}/versions/{version_id}/rating-impact", response_model=RatingImpact)
async def rating_impact(
    dataset_id: str, version_id: str, db: AsyncSession = Depends(get_db)
):
    """How many keep/cut ratings a restore of this version would change.

    A rating is hand-made work that nothing can recompute, and a restore reverts
    it like every other mirrored column — so `RestoreConfirmModal` states the
    number before the user commits. Read-only, and ungated on versioning mode
    like the other read routes.

    The snapshot side already stores unrated as `0`, and NULL there means "not
    recorded" — a row from before the rating existed, whose restore leaves the
    image's rating alone — so those rows are left out rather than counted as a
    clear. The live side is `func.coalesce(col, 0)` into the same encoding,
    rather than `IS DISTINCT FROM`, which SQLite does not support. `0` is safe
    as the stand-in precisely because it is outside the stored 1–4 domain — the
    same fact the gallery's `rating_filter` sentinel rests on.
    """
    ver = await db.get(DatasetVersion, version_id)
    if ver is None or ver.dataset_id != dataset_id:
        raise HTTPException(404, "Version not found")

    snap_rating = VersionImageState.aesthetic_rating
    live_rating = func.coalesce(Image.aesthetic_rating, 0)

    changed_q = (
        select(
            func.count().label("will_change"),
            func.coalesce(func.sum(case((snap_rating == 0, 1), else_=0)), 0).label("will_clear"),
        )
        .select_from(VersionImageState)
        .join(Image, Image.id == VersionImageState.image_id)
        .where(
            VersionImageState.version_id == version_id,
            VersionImageState.is_present.is_(True),
            # The restore only writes rows that are still in this dataset.
            Image.dataset_id == dataset_id,
            snap_rating.isnot(None),
            snap_rating != live_rating,
        )
    )
    row = (await db.execute(changed_q)).one()

    # Rated images the version does not contain. Deleted rather than reverted
    # under handle_extra_images="remove", so the modal reports them separately —
    # the mode decides which sentence is true.
    in_version = (
        select(VersionImageState.image_id)
        .where(
            VersionImageState.version_id == version_id,
            VersionImageState.is_present.is_(True),
            VersionImageState.image_id.isnot(None),
        )
    )
    extras_rated = (await db.execute(
        select(func.count())
        .select_from(Image)
        .where(
            Image.dataset_id == dataset_id,
            Image.aesthetic_rating.isnot(None),
            Image.id.notin_(in_version),
        )
    )).scalar_one()

    return RatingImpact(
        will_change=row.will_change or 0,
        will_clear=int(row.will_clear or 0),
        extras_rated=extras_rated or 0,
    )


@router.get("/{dataset_id}/versions/{version_id}", response_model=VersionOut)
async def get_version(
    dataset_id: str, version_id: str, db: AsyncSession = Depends(get_db)
):
    ver = await db.get(DatasetVersion, version_id)
    if ver is None or ver.dataset_id != dataset_id:
        raise HTTPException(404, "Version not found")
    return ver


@router.patch("/{dataset_id}/versions/{version_id}", response_model=VersionOut)
async def update_version(
    dataset_id: str,
    version_id: str,
    body: VersionUpdate,
    db: AsyncSession = Depends(get_db),
):
    ver = await db.get(DatasetVersion, version_id)
    if ver is None or ver.dataset_id != dataset_id:
        raise HTTPException(404, "Version not found")
    await _require_versioning_enabled(db)
    if body.is_pinned is not None:
        ver.is_pinned = body.is_pinned
    await db.commit()
    await db.refresh(ver)
    return ver


@router.delete("/{dataset_id}/versions/{version_id}", status_code=204)
async def delete_version(
    dataset_id: str, version_id: str, db: AsyncSession = Depends(get_db)
):
    ver = await db.get(DatasetVersion, version_id)
    if ver is None or ver.dataset_id != dataset_id:
        raise HTTPException(404, "Version not found")
    await _require_versioning_enabled(db)

    # Prevent deleting the only version on a branch; otherwise move the branch head to
    # the deleted version's PARENT when it lives on the same branch (git-like: head
    # returns to where it came from — after restore→delete-head this correctly skips
    # the just-created pre_restore auto-snapshot, which is always the newest by
    # created_at). Fall back to the newest remaining version on the branch when the
    # parent is NULL (broken chain) or points at another branch (branch-init snapshots).
    if ver.branch_id is not None:
        branch = await db.get(DatasetBranch, ver.branch_id)
        if branch and branch.head_version_id == version_id:
            newest_other = await db.execute(
                select(DatasetVersion.id).where(
                    DatasetVersion.branch_id == ver.branch_id,
                    DatasetVersion.id != version_id,
                ).order_by(
                    DatasetVersion.created_at.desc(), DatasetVersion.id
                ).limit(1)
            )
            fallback = newest_other.scalar_one_or_none()
            if fallback is None:
                raise HTTPException(400, "Cannot delete the only version on a branch")
            new_head = None
            if ver.parent_id:
                parent = await db.get(DatasetVersion, ver.parent_id)
                if parent is not None and parent.branch_id == ver.branch_id:
                    new_head = parent.id
            branch.head_version_id = new_head or fallback

    await db.delete(ver)
    await db.commit()


@router.post("/{dataset_id}/versions/{version_id}/restore")
async def restore_version(
    dataset_id: str,
    version_id: str,
    body: RestoreRequest,
    db: AsyncSession = Depends(get_db),
):
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Dataset not found")

    ver = await db.get(DatasetVersion, version_id)
    if ver is None or ver.dataset_id != dataset_id:
        raise HTTPException(404, "Version not found")
    await _require_versioning_enabled(db)

    if body.handle_extra_images not in ("keep", "remove"):
        raise HTTPException(400, "handle_extra_images must be 'keep' or 'remove'")

    job = BackgroundJob(
        job_type="restore_snapshot",
        label=f"Restore: {ver.name or version_id[:8]}",
        dataset_id=dataset_id,
        total_items=ver.image_count,
    )
    db.add(job)
    await db.commit()

    h_extra = body.handle_extra_images
    pre_restore = body.pre_restore_snapshot

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session, busy(dataset_id, "restore"):
            result = await version_service.restore_snapshot(
                session, dataset_id, version_id,
                handle_extra_images=h_extra,
                pre_restore_snapshot=pre_restore,
                job_id=job_id,
            )
            await _store_result(session, job_id, result)

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/{dataset_id}/versions/prune")
async def prune_versions(dataset_id: str, db: AsyncSession = Depends(get_db)):
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Dataset not found")

    await _require_versioning_enabled(db)

    job = BackgroundJob(
        job_type="prune_versions",
        label="Prune version storage",
        dataset_id=dataset_id,
        total_items=1,
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session, busy(dataset_id, "prune"):
            summary = await version_service.prune_object_store(
                session, dataset_id, job_id=job_id
            )
        async with AsyncSessionLocal() as session:
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = summary
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}
