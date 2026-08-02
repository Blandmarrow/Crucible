from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.services.booru_service import search_gelbooru, search_safebooru
from backend.services.secrets_service import resolve_secret
from backend.services.threshold_service import get_thresholds

router = APIRouter(prefix="/booru", tags=["booru"])


class AutocompleteRequest(BaseModel):
    prefix: str
    source: str = "safebooru"
    limit: int = 10


async def _gelbooru_credentials(db: AsyncSession) -> tuple[str, str]:
    """The effective Gelbooru credentials: the Settings -> API Keys values, else .env.

    Resolved per request rather than read off the settings singleton at import, so saving a
    key in the UI takes effect on the next lookup with no restart.
    """
    row = await get_thresholds(db)
    return resolve_secret(row, "gelbooru_api_key"), resolve_secret(row, "gelbooru_user_id")


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    source: str = Query("safebooru", pattern="^(safebooru|gelbooru)$"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    if source == "safebooru":
        return await search_safebooru(q, limit)
    else:
        api_key, user_id = await _gelbooru_credentials(db)
        return await search_gelbooru(q, limit, api_key=api_key, user_id=user_id)


@router.post("/autocomplete")
async def autocomplete(body: AutocompleteRequest, db: AsyncSession = Depends(get_db)):
    if body.source == "safebooru":
        return await search_safebooru(body.prefix, body.limit)
    else:
        api_key, user_id = await _gelbooru_credentials(db)
        return await search_gelbooru(body.prefix, body.limit, api_key=api_key, user_id=user_id)
