from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.config import settings
from backend.services.booru_service import search_gelbooru, search_safebooru

router = APIRouter(prefix="/booru", tags=["booru"])


class AutocompleteRequest(BaseModel):
    prefix: str
    source: str = "safebooru"
    limit: int = 10


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    source: str = Query("safebooru", pattern="^(safebooru|gelbooru)$"),
    limit: int = Query(20, ge=1, le=100),
):
    if source == "safebooru":
        return await search_safebooru(q, limit)
    else:
        return await search_gelbooru(
            q, limit,
            api_key=settings.gelbooru_api_key,
            user_id=settings.gelbooru_user_id,
        )


@router.post("/autocomplete")
async def autocomplete(body: AutocompleteRequest):
    if body.source == "safebooru":
        return await search_safebooru(body.prefix, body.limit)
    else:
        return await search_gelbooru(
            body.prefix, body.limit,
            api_key=settings.gelbooru_api_key,
            user_id=settings.gelbooru_user_id,
        )
