from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.services.booru_service import (
    BooruUnavailable,
    check_tags,
    search_gelbooru,
    search_safebooru,
)
from backend.services.secrets_service import resolve_secret
from backend.services.threshold_service import get_thresholds

router = APIRouter(prefix="/booru", tags=["booru"])


class AutocompleteRequest(BaseModel):
    prefix: str
    source: str = "safebooru"
    limit: int = 10


class CheckRequest(BaseModel):
    # A caption's comma parts, as written. 200 covers any real tag caption, and bounds the
    # suggestion work; each part is capped so one pasted paragraph cannot become a URL.
    tags: list[Annotated[str, Field(max_length=200)]] = Field(..., min_length=1, max_length=200)


async def _gelbooru_credentials(db: AsyncSession) -> tuple[str, str]:
    """The effective Gelbooru credentials: the Settings -> API Keys values, else .env.

    Resolved per request rather than read off the settings singleton at import, so saving a
    key in the UI takes effect on the next lookup with no restart.
    """
    row = await get_thresholds(db)
    return resolve_secret(row, "gelbooru_api_key"), resolve_secret(row, "gelbooru_user_id")


async def _lookup(source: str, q: str, limit: int, db: AsyncSession) -> list[dict]:
    """Dispatch a tag search. An unreachable booru is a 502, never an empty list."""
    try:
        if source == "safebooru":
            return await search_safebooru(q, limit)
        api_key, user_id = await _gelbooru_credentials(db)
        return await search_gelbooru(q, limit, api_key=api_key, user_id=user_id)
    except BooruUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    source: str = Query("safebooru", pattern="^(safebooru|gelbooru)$"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await _lookup(source, q, limit, db)


@router.post("/autocomplete")
async def autocomplete(body: AutocompleteRequest, db: AsyncSession = Depends(get_db)):
    return await _lookup(body.source, body.prefix, body.limit, db)


@router.post("/check")
async def check(body: CheckRequest):
    """Check a caption's tags against Danbooru (#50). Runs only on the user's button press."""
    try:
        return await check_tags(body.tags)
    except BooruUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
