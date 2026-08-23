from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.threshold_settings import ThresholdSettings
from backend.schemas import mask_secret
from backend.services.secrets_service import (
    SECRET_FIELDS,
    resolve_secret,
    secret_source,
    sync_env,
)
from backend.services.threshold_service import DEFAULTS, get_thresholds

router = APIRouter(prefix="/settings", tags=["settings"])

_VALID_VERSIONING_MODES = {"off", "manual", "auto"}


class ThresholdsOut(BaseModel):
    blur_threshold: float
    noise_threshold: float
    uniformity_threshold: float
    duplicate_threshold: float
    watermark_threshold: float
    nsfw_threshold: float
    gdino_threshold: float
    sam3_threshold: float
    versioning_mode: str = "off"
    auto_rescan_on_open: bool = False
    auto_unload_after_scoring: bool = True
    comfyui_url: str = ""
    comfy_workflow_dir: str = ""

    model_config = {"from_attributes": True}


class ThresholdsUpdate(BaseModel):
    blur_threshold: float | None = Field(default=None, gt=0)
    noise_threshold: float | None = Field(default=None, gt=0)
    uniformity_threshold: float | None = Field(default=None, gt=0)
    duplicate_threshold: float | None = Field(default=None, ge=1)
    watermark_threshold: float | None = Field(default=None, gt=0, le=1.0)
    nsfw_threshold: float | None = Field(default=None, gt=0, le=1.0)
    gdino_threshold: float | None = Field(default=None, gt=0, le=1.0)
    sam3_threshold: float | None = Field(default=None, gt=0, le=1.0)
    versioning_mode: str | None = Field(default=None)
    auto_rescan_on_open: bool | None = Field(default=None)
    auto_unload_after_scoring: bool | None = Field(default=None)
    comfyui_url: str | None = Field(default=None, max_length=500)
    comfy_workflow_dir: str | None = Field(default=None, max_length=1000)

    @field_validator("versioning_mode")
    @classmethod
    def validate_versioning_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_VERSIONING_MODES:
            raise ValueError(f"versioning_mode must be one of {_VALID_VERSIONING_MODES}")
        return v


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


# --- Secrets (Settings -> API Keys) ------------------------------------------------
#
# A separate endpoint pair rather than three more fields on /thresholds, for three reasons:
# GET /thresholds returns the ORM row directly under from_attributes, so a derived
# `hf_token_masked` field would look for a nonexistent row attribute and fail validation;
# that PATCH's blind setattr loop is the wrong tool for a value with a process-level side
# effect (sync_env); and the frontend caches ["settings","thresholds"] across six screens,
# none of which should be pulling secrets over the wire. ThresholdsOut itself needs no
# change — from_attributes ignores the three new ORM attributes.


class SecretOut(BaseModel):
    masked: str
    source: Literal["db", "env", "unset"]


class SecretsOut(BaseModel):
    """One nested object per secret, so the response is structurally not a valid update.

    The nesting is the mask-echo defence. A flat `hf_token: str | None` cannot distinguish a
    mask from a real token that happens to be asterisks, so `PATCH(GET().json())` would
    quietly save `****abcd` as the key. Nested, that same call is a 422 — an assertable
    runtime property rather than a naming convention. See test_settings_secrets.py.
    """

    hf_token: SecretOut
    gelbooru_api_key: SecretOut
    gelbooru_user_id: SecretOut


class SecretsUpdate(BaseModel):
    """Absent/null leaves a secret unchanged; "" clears the override; non-empty sets it."""

    hf_token: str | None = Field(default=None, max_length=500)
    gelbooru_api_key: str | None = Field(default=None, max_length=500)
    gelbooru_user_id: str | None = Field(default=None, max_length=500)

    @field_validator("*")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        # Pasted tokens routinely carry a trailing newline. Whitespace-only therefore
        # strips to "", i.e. clear the override.
        return v.strip() if v is not None else v


def _secrets_out(row: ThresholdSettings | None) -> SecretsOut:
    # The masked value is the *effective* one, so the UI can show `****abcd` beside
    # "inherited from .env". That exposes four characters of a token that only ever lived
    # in .env — accepted deliberately: this is an unauthenticated LAN surface where
    # GET /filesystem/list already reads .env outright.
    return SecretsOut(
        **{
            field: SecretOut(
                masked=mask_secret(resolve_secret(row, field)),
                source=secret_source(row, field),
            )
            for field in SECRET_FIELDS
        }
    )


@router.get("/secrets", response_model=SecretsOut)
async def get_secrets(db: AsyncSession = Depends(get_db)):
    return _secrets_out(await get_thresholds(db))


@router.patch("/secrets", response_model=SecretsOut)
async def update_secrets(body: SecretsUpdate, db: AsyncSession = Depends(get_db)):
    row = await db.get(ThresholdSettings, 1)
    if row is None:
        row = ThresholdSettings(**DEFAULTS)
        db.add(row)

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(row, field, value)

    await db.commit()
    await db.refresh(row)
    # Unconditional, even when only a gelbooru field changed: idempotent, and branching on
    # which field moved is how the projection drifts out of sync with the row.
    sync_env(row)
    return _secrets_out(row)
