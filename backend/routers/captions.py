from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.caption import (
    BulkEditRequest,
    BulkEditResponse,
    CaptionOut,
    CaptionUpdate,
    FindReplaceRequest,
    TagStatItem,
)
from backend.services.caption_service import (
    bulk_edit_captions,
    find_replace_captions,
    get_caption,
    get_tag_stats,
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



@router.get("/dataset/{dataset_id}/tag-stats", response_model=list[TagStatItem])
async def tag_stats(dataset_id: str, subfolder: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    return await get_tag_stats(db, dataset_id, subfolder=subfolder)


@router.post("/dataset/{dataset_id}/find-replace")
async def find_replace(dataset_id: str, body: FindReplaceRequest, db: AsyncSession = Depends(get_db)):
    count = await find_replace_captions(
        db, dataset_id, body.find, body.replace, body.use_regex, body.image_ids
    )
    return {"updated": count}


@router.post("/dataset/{dataset_id}/bulk-edit", response_model=BulkEditResponse)
async def bulk_edit(dataset_id: str, body: BulkEditRequest, db: AsyncSession = Depends(get_db)):
    return await bulk_edit_captions(
        db,
        dataset_id,
        operation=body.operation,
        text=body.text,
        replacement=body.replacement,
        use_regex=body.use_regex,
        image_ids=body.image_ids,
        quality_flags=body.quality_flags,
    )
