from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.threshold_settings import ThresholdSettings
from backend.services.threshold_service import DEFAULTS, get_thresholds

router = APIRouter(prefix="/settings", tags=["settings"])


class ThresholdsOut(BaseModel):
    blur_threshold: float
    noise_threshold: float
    uniformity_threshold: float
    duplicate_threshold: float
    watermark_threshold: float

    model_config = {"from_attributes": True}


class ThresholdsUpdate(BaseModel):
    blur_threshold: float | None = Field(default=None, gt=0)
    noise_threshold: float | None = Field(default=None, gt=0)
    uniformity_threshold: float | None = Field(default=None, gt=0)
    duplicate_threshold: float | None = Field(default=None, ge=1)
    watermark_threshold: float | None = Field(default=None, gt=0, le=1.0)


@router.get("/thresholds", response_model=ThresholdsOut)
async def get_thresholds_endpoint(db: AsyncSession = Depends(get_db)):
    return await get_thresholds(db)


@router.patch("/thresholds", response_model=ThresholdsOut)
async def update_thresholds(body: ThresholdsUpdate, db: AsyncSession = Depends(get_db)):
    row = await db.get(ThresholdSettings, 1)
    if row is None:
        row = ThresholdSettings(**DEFAULTS)
        db.add(row)

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(row, field, value)

    await db.commit()
    await db.refresh(row)
    return row
