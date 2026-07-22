"""Serves the curated license vocabulary so the frontend list can't drift.

`frontend/src/constants/licenses.ts` carries labels and badge colors for the UI;
this endpoint is the authority on which ids exist and what each permits.
"""
from fastapi import APIRouter

from backend.licenses import LICENSES
from backend.schemas.image import LicenseOut

router = APIRouter(prefix="/licenses", tags=["licenses"])


@router.get("/", response_model=list[LicenseOut])
async def list_licenses():
    return [LicenseOut(**vars(info)) for info in LICENSES.values()]
