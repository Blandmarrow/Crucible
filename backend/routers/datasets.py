import asyncio
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from backend.utils import normalize_subfolder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import BackgroundJob, Dataset, Image
from backend.schemas.dataset import DatasetCreate, DatasetImport, DatasetImportWithOptions, DatasetOut, DatasetStats, DatasetUpdate, SubfolderCreate, SubfolderInfo, TagCooccurrence
from backend.services.dataset_service import (
    create_dataset,
    declare_subfolder,
    delete_subfolder,
    get_dataset_stats,
    get_score_values,
    get_tag_cooccurrence,
    import_images_from_folder,
    list_subfolders,
    refresh_stats,
    rename_dataset,
)
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("/", response_model=list[DatasetOut])
async def list_datasets(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Dataset).order_by(Dataset.created_at.desc()))
    datasets = result.scalars().all()

    previews: defaultdict[str, list[str]] = defaultdict(list)
    if datasets:
        ds_ids = [ds.id for ds in datasets]
        rows = (await db.execute(
            select(Image.dataset_id, Image.id)
            .where(Image.dataset_id.in_(ds_ids))
            .order_by(Image.dataset_id, Image.created_at)
        )).all()
        for row in rows:
            did, iid = row[0], row[1]
            if len(previews[did]) < 8:
                previews[did].append(iid)

    return [
        DatasetOut(
            id=ds.id,
            name=ds.name,
            description=ds.description,
            folder_path=ds.folder_path,
            created_at=ds.created_at,
            updated_at=ds.updated_at,
            image_count=ds.image_count,
            captioned_count=ds.captioned_count,
            total_size_bytes=ds.total_size_bytes,
            preview_image_ids=previews[ds.id],
        )
        for ds in datasets
    ]


@router.post("/", response_model=DatasetOut, status_code=201)
async def create(body: DatasetCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Dataset).where(Dataset.name == body.name))
    if existing.scalar():
        raise HTTPException(400, f"Dataset '{body.name}' already exists")
    return await create_dataset(db, body.name, body.description)


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
        return await rename_dataset(db, ds, body.name, body.description)

    if body.description is not None:
        ds.description = body.description
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


@router.post("/{dataset_id}/import")
async def import_folder(dataset_id: str, body: DatasetImportWithOptions, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    job = BackgroundJob(
        job_type="import",
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
            await import_images_from_folder(
                session, ds2, body.folder_path,
                job_id=job_id,
                subfolder=body.subfolder,
                preserve_structure=body.preserve_structure,
            )

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
async def get_stats(dataset_id: str, db: AsyncSession = Depends(get_db)):
    stats = await get_dataset_stats(db, dataset_id)
    if not stats:
        raise HTTPException(404, "Dataset not found")
    return stats


@router.get("/{dataset_id}/score-values")
async def get_score_values_endpoint(dataset_id: str, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return await get_score_values(db, dataset_id)


@router.get("/{dataset_id}/tag-cooccurrence", response_model=TagCooccurrence)
async def tag_cooccurrence(dataset_id: str, limit: int = 15, db: AsyncSession = Depends(get_db)):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return await get_tag_cooccurrence(db, dataset_id, limit)
