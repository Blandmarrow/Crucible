from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.chat import Chat
from backend.models.openai_provider import OpenAIProvider
from backend.schemas.openai_provider import (
    OpenAIProviderCreate,
    OpenAIProviderOut,
    OpenAIProviderUpdate,
    provider_is_billed,
)

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/", response_model=list[OpenAIProviderOut])
async def list_providers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OpenAIProvider).order_by(OpenAIProvider.created_at))
    rows = result.scalars().all()
    return [OpenAIProviderOut.from_orm_row(r) for r in rows]


@router.post("/", response_model=OpenAIProviderOut, status_code=201)
async def create_provider(body: OpenAIProviderCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(OpenAIProvider).where(OpenAIProvider.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Provider named '{body.name}' already exists")
    # Splatted, not an explicit field list: the old list omitted `max_tokens`, so a
    # POST carrying one silently stored the default and needed a follow-up PATCH.
    # OpenAIProviderCreate's fields map 1:1 onto the model's non-generated columns.
    row = OpenAIProvider(**body.model_dump())
    row.analyst_privacy = row.analyst_privacy or None  # {} is no override
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return OpenAIProviderOut.from_orm_row(row)


@router.patch("/{provider_id}", response_model=OpenAIProviderOut)
async def update_provider(provider_id: str, body: OpenAIProviderUpdate, db: AsyncSession = Depends(get_db)):
    row = await db.get(OpenAIProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    data = body.model_dump(exclude_none=True)
    if data.pop("analyst_privacy_reset", False) or data.get("analyst_privacy") == {}:
        row.analyst_privacy = None
        data.pop("analyst_privacy", None)
    was = (row.base_url, provider_is_billed(row))
    for field, value in data.items():
        setattr(row, field, value)
    if (row.base_url, provider_is_billed(row)) != was:
        # A chat's own privacy toggles were chosen for this provider as it was. A new
        # server, or a change in whether it bills, must not inherit "send everything".
        await db.execute(update(Chat).where(Chat.provider_id == row.id).values(privacy=None))
    await db.commit()
    await db.refresh(row)
    return OpenAIProviderOut.from_orm_row(row)


@router.get("/{provider_id}/models")
async def fetch_provider_models(provider_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(OpenAIProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    import asyncio

    def _list_models(base_url: str, api_key: str) -> list[str]:
        try:
            from openai import OpenAI
            # max_retries=0 so this never-raises probe cannot spend 15 s on a 5 s timeout.
            client = OpenAI(base_url=base_url, api_key=api_key or "no-key", timeout=5.0, max_retries=0)
            result = client.models.list()
            return sorted(m.id for m in result.data)
        except Exception:
            return []

    loop = asyncio.get_running_loop()
    ids = await loop.run_in_executor(None, _list_models, row.base_url, row.api_key)
    return {"models": ids}


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(OpenAIProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    await db.delete(row)
    await db.commit()
