import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import BackgroundJob, Dataset
from backend.models.versioning import DatasetBranch, DatasetVersion
from backend.schemas.versioning import (
    BranchCreate, BranchOut,
    DiffOut,
    RestoreRequest, RestoreSummary,
    SnapshotCreate, VersionOut,
)
from backend.services import version_service
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/datasets", tags=["versioning"])

_SNAPSHOT_INLINE_LIMIT = 100


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------

@router.get("/{dataset_id}/versions/branches", response_model=list[BranchOut])
async def list_branches(dataset_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DatasetBranch).where(DatasetBranch.dataset_id == dataset_id)
    )
    return result.scalars().all()


@router.post("/{dataset_id}/versions/branches", response_model=BranchOut)
async def create_branch(
    dataset_id: str, body: BranchCreate, db: AsyncSession = Depends(get_db)
):
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Dataset not found")

    # Count images to decide sync vs async
    from sqlalchemy import func
    from backend.models.image import Image
    count_result = await db.execute(
        select(func.count(Image.id)).where(Image.dataset_id == dataset_id)
    )
    image_count = count_result.scalar() or 0

    if image_count <= _SNAPSHOT_INLINE_LIMIT:
        try:
            branch, _ = await version_service.create_branch(
                db, dataset_id, body.name, body.from_version_id
            )
            return branch
        except ValueError as e:
            raise HTTPException(400, str(e))

    # Background job for large datasets
    job = BackgroundJob(job_type="create_branch", dataset_id=dataset_id, total_items=image_count)
    db.add(job)
    await db.commit()

    branch_name = body.name
    from_vid = body.from_version_id

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            try:
                await version_service.create_branch(session, dataset_id, branch_name, from_vid)
            except Exception as exc:
                logger.error("create_branch job failed: %s", exc)
                raise

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/{dataset_id}/versions/branches/{branch_id}/checkout")
async def checkout_branch(
    dataset_id: str, branch_id: str, db: AsyncSession = Depends(get_db)
):
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Dataset not found")

    branch = await db.get(DatasetBranch, branch_id)
    if branch is None or branch.dataset_id != dataset_id:
        raise HTTPException(404, "Branch not found")

    job = BackgroundJob(job_type="checkout_branch", dataset_id=dataset_id, total_items=1)
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            await version_service.checkout_branch(session, dataset_id, branch_id, job_id)

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
    db: AsyncSession = Depends(get_db),
):
    q = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    if branch_id is not None:
        q = q.where(DatasetVersion.branch_id == branch_id)
    q = q.order_by(DatasetVersion.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/{dataset_id}/versions")
async def create_snapshot(
    dataset_id: str, body: SnapshotCreate, db: AsyncSession = Depends(get_db)
):
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Dataset not found")

    from sqlalchemy import func
    from backend.models.image import Image
    count_result = await db.execute(
        select(func.count(Image.id)).where(Image.dataset_id == dataset_id)
    )
    image_count = count_result.scalar() or 0

    from backend.services.threshold_service import get_thresholds
    settings = await get_thresholds(db)
    mode = settings.versioning_mode

    if mode == "off":
        raise HTTPException(400, "Versioning is disabled. Enable it in Settings first.")

    # Manual mode always runs as background job; auto mode inline if small
    if mode == "manual" or image_count > _SNAPSHOT_INLINE_LIMIT:
        job = BackgroundJob(
            job_type="create_snapshot", dataset_id=dataset_id, total_items=image_count
        )
        db.add(job)
        await db.commit()

        snap_name = body.name
        snap_desc = body.description
        snap_branch_id = body.branch_id

        async def _run(job_id: str) -> None:
            from backend.database import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                await version_service.create_snapshot(
                    session, dataset_id,
                    name=snap_name, description=snap_desc,
                    branch_id=snap_branch_id, job_id=job_id,
                )

        await job_queue.enqueue(job, _run)
        return {"job_id": job.id}

    version = await version_service.create_snapshot(
        db, dataset_id,
        name=body.name, description=body.description,
        branch_id=body.branch_id,
    )
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


@router.get("/{dataset_id}/versions/{version_id}", response_model=VersionOut)
async def get_version(
    dataset_id: str, version_id: str, db: AsyncSession = Depends(get_db)
):
    ver = await db.get(DatasetVersion, version_id)
    if ver is None or ver.dataset_id != dataset_id:
        raise HTTPException(404, "Version not found")
    return ver


@router.delete("/{dataset_id}/versions/{version_id}", status_code=204)
async def delete_version(
    dataset_id: str, version_id: str, db: AsyncSession = Depends(get_db)
):
    ver = await db.get(DatasetVersion, version_id)
    if ver is None or ver.dataset_id != dataset_id:
        raise HTTPException(404, "Version not found")

    # Prevent deleting the only version on a branch
    if ver.branch_id is not None:
        branch = await db.get(DatasetBranch, ver.branch_id)
        if branch and branch.head_version_id == version_id:
            # Check if there are other versions on this branch
            other = await db.execute(
                select(DatasetVersion.id).where(
                    DatasetVersion.branch_id == ver.branch_id,
                    DatasetVersion.id != version_id,
                ).limit(1)
            )
            if other.first() is None:
                raise HTTPException(400, "Cannot delete the only version on a branch")
            branch.head_version_id = ver.parent_id

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

    if body.handle_extra_images not in ("keep", "remove"):
        raise HTTPException(400, "handle_extra_images must be 'keep' or 'remove'")

    job = BackgroundJob(
        job_type="restore_snapshot", dataset_id=dataset_id, total_items=ver.image_count
    )
    db.add(job)
    await db.commit()

    h_extra = body.handle_extra_images
    pre_restore = body.pre_restore_snapshot

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            await version_service.restore_snapshot(
                session, dataset_id, version_id,
                handle_extra_images=h_extra,
                pre_restore_snapshot=pre_restore,
                job_id=job_id,
            )

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}
