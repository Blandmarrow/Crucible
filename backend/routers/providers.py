from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.openai_provider import OpenAIProvider
from backend.schemas.openai_provider import OpenAIProviderCreate, OpenAIProviderOut, OpenAIProviderUpdate

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
    row = OpenAIProvider(
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        default_model=body.default_model,
        max_image_px=body.max_image_px,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return OpenAIProviderOut.from_orm_row(row)


@router.patch("/{provider_id}", response_model=OpenAIProviderOut)
async def update_provider(provider_id: str, body: OpenAIProviderUpdate, db: AsyncSession = Depends(get_db)):
    row = await db.get(OpenAIProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(row, field, value)
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
            client = OpenAI(base_url=base_url, api_key=api_key or "no-key", timeout=5.0)
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
