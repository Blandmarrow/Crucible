from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.caption import (
    BatchTagRemove,
    BatchTagSet,
    CaptionOut,
    CaptionUpdate,
    FindReplaceRequest,
    TagPatch,
    TagStatItem,
)
from backend.services.caption_service import (
    batch_remove_tags,
    batch_set_tags,
    find_replace_captions,
    get_caption,
    get_tag_stats,
    patch_tags,
    set_caption,
)

router = APIRouter(prefix="/captions", tags=["captions"])


@router.get("/image/{image_id}", response_model=CaptionOut)
async def get(image_id: str, db: AsyncSession = Depends(get_db)):
    data = await get_caption(db, image_id)
    if not data:
        raise HTTPException(404, "Image not found")
    return data


@router.put("/image/{image_id}", response_model=CaptionOut)
async def update(image_id: str, body: CaptionUpdate, db: AsyncSession = Depends(get_db)):
    await set_caption(db, image_id, body.caption_text, body.tags, body.caption_style, "manual")
    return await get_caption(db, image_id)


@router.patch("/image/{image_id}/tags", response_model=CaptionOut)
async def patch(image_id: str, body: TagPatch, db: AsyncSession = Depends(get_db)):
    await patch_tags(db, image_id, body.add, body.remove)
    return await get_caption(db, image_id)


@router.post("/batch/set-tags", status_code=204)
async def batch_set(body: BatchTagSet, db: AsyncSession = Depends(get_db)):
    await batch_set_tags(db, body.image_ids, body.tags, body.mode)


@router.post("/batch/remove-tags", status_code=204)
async def batch_remove(body: BatchTagRemove, db: AsyncSession = Depends(get_db)):
    await batch_remove_tags(db, body.image_ids, body.tags)


@router.get("/dataset/{dataset_id}/tag-stats", response_model=list[TagStatItem])
async def tag_stats(dataset_id: str, subfolder: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    return await get_tag_stats(db, dataset_id, subfolder=subfolder)


@router.post("/dataset/{dataset_id}/find-replace")
async def find_replace(dataset_id: str, body: FindReplaceRequest, db: AsyncSession = Depends(get_db)):
    count = await find_replace_captions(
        db, dataset_id, body.find, body.replace, body.use_regex, body.image_ids
    )
    return {"updated": count}
