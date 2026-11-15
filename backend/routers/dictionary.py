"""The offline synonym dictionary (#52). See `services/dictionary_service.py` and
`docs/dev/captioning/dictionary.md`."""

import asyncio

from fastapi import APIRouter, HTTPException, Query

from backend.services import dictionary_service as dictionary

router = APIRouter(prefix="/dictionary", tags=["dictionary"])


@router.get("/status")
async def status():
    return dictionary.status()


@router.post("/install")
async def install():
    """Start the one-time download and conversion, or join the one running."""
    try:
        return dictionary.start_install()
    except dictionary.Busy as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("")
async def remove():
    # A file Windows will not let go of raises FileInUseError, which main.py answers
    # with a 409 that says so.
    try:
        await dictionary.remove()
    except dictionary.Busy as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return dictionary.status()


@router.get("/synonyms")
async def synonyms(word: str = Query(..., min_length=1, max_length=200)):
    try:
        return await asyncio.to_thread(dictionary.synonyms, word)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except dictionary.NotInstalled as e:
        raise HTTPException(
            status_code=409,
            detail="The dictionary is not installed. Turn on Synonym suggestions in Settings.",
        ) from e
