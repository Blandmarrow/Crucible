import asyncio
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from backend.utils import normalize_subfolder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import BackgroundJob, Dataset, Image
from backend.schemas.dataset import CaptionImportRequest, DatasetCreate, DatasetDuplicateRequest, DatasetImport, DatasetImportWithOptions, DatasetOut, DatasetRescanRequest, DatasetStats, DatasetUpdate, SubfolderCreate, SubfolderInfo, TagCooccurrence
from backend.services.dataset_service import (
    create_dataset,
    declare_subfolder,
    delete_subfolder,
    duplicate_dataset,
    get_dataset_stats,
    get_score_values,
    get_tag_cooccurrence,
    import_captions_from_folder,
    import_images_from_folder,
    list_subfolders,
    refresh_stats,
    rename_dataset,
    rescan_dataset,
)
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("/", response_model=list[DatasetOut])
async def list_datasets(
    skip: int = Query(0, ge=0),
    limit: int = Query(0, ge=0, description="Maximum number of datasets to return; 0 means no limit"),
    db: AsyncSession = Depends(get_db),
):
    q = select(Dataset).order_by(Dataset.created_at.desc())
    if limit > 0:
        q = q.offset(skip).limit(limit)
    result = await db.execute(q)
    datasets = result.scalars().all()

    previews: defaultdict[str, list[str]] = defaultdict(list)
    if datasets:
        ds_ids = [ds.id for ds in datasets]
        # Window function: rank images within each dataset by created_at and keep only
        # the first 8 per dataset, so cost is O(datasets) instead of O(total library).
        rn = func.row_number().over(
            partition_by=Image.dataset_id, order_by=Image.created_at
        ).label("rn")
        ranked = (
            select(Image.dataset_id, Image.id, rn)
            .where(Image.dataset_id.in_(ds_ids))
            .subquery()
        )
        rows = (await db.execute(
            select(ranked.c.dataset_id, ranked.c.id)
            .where(ranked.c.rn <= 8)
            .order_by(ranked.c.dataset_id, ranked.c.rn)
        )).all()
        for row in rows:
            previews[row[0]].append(row[1])

    return [
        DatasetOut(
            id=ds.id,
            name=ds.name,
            description=ds.description,
            category=ds.category,
            folder_path=ds.folder_path,
            created_at=ds.created_at,
            updated_at=ds.updated_at,
            image_count=ds.image_count,
            captioned_count=ds.captioned_count,
            total_size_bytes=ds.total_size_bytes,
            preview_image_ids=previews[ds.id],
            current_branch_id=ds.current_branch_id,
        )
        for ds in datasets
    ]


@router.post("/", response_model=DatasetOut, status_code=201)
async def create(body: DatasetCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Dataset).where(Dataset.name == body.name))
    if existing.scalar():
        raise HTTPException(400, f"Dataset '{body.name}' already exists")
    return await create_dataset(db, body.name, body.description, body.category)


@router.get("/{dataset_id}", response_model=DatasetOut)
async def get_dataset(dataset_id: str, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return ds


@router.patch("/{dataset_id}", response_model=DatasetOut)
async def update_dataset(dataset_id: str, body: DatasetUpdate, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    if body.name is not None and body.name != ds.name:
        conflict = await db.execute(select(Dataset).where(Dataset.name == body.name))
        if conflict.scalar():
            raise HTTPException(400, f"Dataset '{body.name}' already exists")
        ds = await rename_dataset(db, ds, body.name, body.description)
        # Apply category update after rename (rename already committed)
        if body.category is not None:
            ds.category = body.category
            await db.commit()
            await db.refresh(ds)
        return ds

    if body.description is not None:
        ds.description = body.description
    if body.category is not None:
        ds.category = body.category
    await db.commit()
    await db.refresh(ds)
    return ds


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(dataset_id: str, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    folder = Path(ds.folder_path)
    await db.delete(ds)
    await db.commit()
    if folder.exists():
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


@router.post("/{dataset_id}/duplicate")
async def duplicate_dataset_endpoint(
    dataset_id: str, body: DatasetDuplicateRequest, db: AsyncSession = Depends(get_db)
):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    conflict = await db.execute(select(Dataset).where(Dataset.name == body.new_name))
    if conflict.scalar():
        raise HTTPException(400, f"Dataset '{body.new_name}' already exists")

    if body.source_version_id is not None:
        from backend.models.versioning import DatasetVersion
        ver = await db.get(DatasetVersion, body.source_version_id)
        if not ver or ver.dataset_id != dataset_id:
            raise HTTPException(404, "Snapshot not found for this dataset")
        total = ver.image_count
    else:
        total = ds.image_count

    job = BackgroundJob(
        job_type="duplicate",
        label=f"Duplicate - {body.new_name}",
        dataset_id=dataset_id,
        total_items=total,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            src = await session.get(Dataset, dataset_id)
            if src is None:
                return  # dataset deleted after job was enqueued
            await duplicate_dataset(
                session, src, body.new_name, job_id,
                source_version_id=body.source_version_id,
            )

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/{dataset_id}/import")
async def import_folder(dataset_id: str, body: DatasetImportWithOptions, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    from pathlib import Path as _Path
    job = BackgroundJob(
        job_type="import",
        label=f"Import - {_Path(body.folder_path).name}",
        dataset_id=dataset_id,
        total_items=0,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            ds2 = await session.get(Dataset, dataset_id)
            summary = await import_images_from_folder(
                session, ds2, body.folder_path,
                job_id=job_id,
                subfolder=body.subfolder,
                preserve_structure=body.preserve_structure,
                import_captions=body.import_captions,
            )
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = summary
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/{dataset_id}/rescan")
async def rescan_folder(dataset_id: str, body: DatasetRescanRequest, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    # Dedupe: a rescan for this dataset already waiting or running covers this
    # request too (auto-rescan-on-open can fire on every gallery mount while a
    # long job holds the serial queue — without this, duplicates pile up).
    existing = (await db.execute(
        select(BackgroundJob.id)
        .where(
            BackgroundJob.job_type == "rescan",
            BackgroundJob.dataset_id == dataset_id,
            BackgroundJob.status.in_(("pending", "running")),
        )
        .limit(1)
    )).scalar_one_or_none()
    if existing:
        return {"job_id": existing}

    job = BackgroundJob(
        job_type="rescan",
        label=f"Rescan - {ds.name}",
        dataset_id=dataset_id,
        total_items=0,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            ds2 = await session.get(Dataset, dataset_id)
            summary = await rescan_dataset(
                session, ds2, job_id=job_id, import_captions=body.import_captions,
            )
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = summary
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/{dataset_id}/import-captions")
async def import_captions(dataset_id: str, body: CaptionImportRequest, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    job = BackgroundJob(
        job_type="import_captions",
        label=f"Import captions - {Path(body.folder_path).name}",
        dataset_id=dataset_id,
        total_items=0,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            ds2 = await session.get(Dataset, dataset_id)
            summary = await import_captions_from_folder(
                session, ds2, body.folder_path, job_id=job_id,
            )
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = summary
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.get("/{dataset_id}/subfolders", response_model=list[SubfolderInfo])
async def get_subfolders(dataset_id: str, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return await list_subfolders(db, dataset_id)


@router.post("/{dataset_id}/subfolders", response_model=SubfolderInfo, status_code=201)
async def create_subfolder(dataset_id: str, body: SubfolderCreate, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    path = normalize_subfolder(body.path)
    if not path:
        raise HTTPException(400, "Subfolder path must not be empty")
    await declare_subfolder(db, dataset_id, path)
    return {"path": path, "image_count": 0}


@router.delete("/{dataset_id}/subfolders")
async def remove_subfolder(
    dataset_id: str,
    path: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    moved = await delete_subfolder(db, dataset_id, path)
    return {"deleted": path, "images_moved_to_root": moved}


@router.post("/{dataset_id}/refresh-stats", status_code=204)
async def do_refresh_stats(dataset_id: str, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    await refresh_stats(db, dataset_id)


@router.get("/{dataset_id}/stats", response_model=DatasetStats)
async def get_stats(dataset_id: str, subfolder: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    stats = await get_dataset_stats(db, dataset_id, subfolder=subfolder)
    if not stats:
        raise HTTPException(404, "Dataset not found")
    return stats


@router.get("/{dataset_id}/score-values")
async def get_score_values_endpoint(dataset_id: str, subfolder: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return await get_score_values(db, dataset_id, subfolder=subfolder)


@router.get("/{dataset_id}/tag-cooccurrence", response_model=TagCooccurrence)
async def tag_cooccurrence(dataset_id: str, limit: int = 15, subfolder: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return await get_tag_cooccurrence(db, dataset_id, limit, subfolder=subfolder)
