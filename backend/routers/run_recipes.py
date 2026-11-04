"""The run recipe library (M6 §6). Its own prefix, not under `/runs`, whose
`/{run_id}` routes would otherwise sit beside it. The logic is
`services/run_recipe_service.py`."""
import json

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.run_recipe import SavedRunRecipe
from backend.schemas.run import RecipeImportOut, RecipeOut, RecipeRename, RecipeSave
from backend.services import run_recipe_service as svc

router = APIRouter(prefix="/run-recipes", tags=["runs"])


@router.get("/", response_model=list[RecipeOut])
async def list_recipes(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(SavedRunRecipe).order_by(SavedRunRecipe.name_key))).scalars().all()
    return [svc.recipe_out(r) for r in rows]


@router.post("/", response_model=RecipeOut)
async def save_recipe(body: RecipeSave, db: AsyncSession = Depends(get_db)):
    return svc.recipe_out(await svc.save(db, body.name, body.recipe, replace=body.replace))


@router.patch("/{recipe_id}", response_model=RecipeOut)
async def rename_recipe(recipe_id: str, body: RecipeRename, db: AsyncSession = Depends(get_db)):
    return svc.recipe_out(await svc.rename(db, recipe_id, body.name))


@router.delete("/{recipe_id}", status_code=204)
async def delete_recipe(recipe_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(SavedRunRecipe, recipe_id)
    if row is None:
        raise HTTPException(404, "Recipe not found")
    await db.delete(row)
    await db.commit()


@router.get("/{recipe_id}/export")
async def export_recipe(recipe_id: str, db: AsyncSession = Depends(get_db)):
    """The recipe as a file to share: no export folder, providers by name."""
    row = await db.get(SavedRunRecipe, recipe_id)
    if row is None:
        raise HTTPException(404, "Recipe not found")
    return JSONResponse(await svc.to_document(db, row))


@router.post("/import", response_model=RecipeImportOut)
async def import_recipe(doc: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """Save a recipe file under its own name, numbered if that name is taken. The notes
    say what it could not carry over: the export folder, a provider not set up here."""
    if len(json.dumps(doc)) > svc.MAX_DOCUMENT_BYTES:
        raise HTTPException(400, "That file is too large to be a run recipe")
    name, draft, notes = await svc.from_document(db, doc)
    row = await svc.save(db, await svc.free_name(db, name), draft, replace=False)
    return RecipeImportOut(recipe=svc.recipe_out(row), notes=notes)
