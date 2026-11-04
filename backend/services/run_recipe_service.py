"""The run recipe library (M6 §6): named recipes saved from the Runs page, and the JSON
document they export to and import from.

A saved recipe keeps what only this machine can use — a provider id, an absolute export
folder — so loading it here fills in everything. The **document** is what may travel,
so it never carries either: the export folder is blanked and each provider id becomes
the provider's name, which an import resolves to a provider of that name here or
leaves blank with a note. Neither ever holds an API key or a plan id: the recipe
models have no such field, and every recipe — saved or imported — goes through
`RunRecipeDraft`, which drops any key it does not know.

See docs/dev/comfyui/recipes.md.
"""
from __future__ import annotations

import copy
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.openai_provider import OpenAIProvider
from backend.models.run_recipe import SavedRunRecipe
from backend.schemas.run import RecipeOut, RunRecipeDraft
from backend.utils import normalize_subfolder, sanitize_abs_path

DOCUMENT_FORMAT = "crucible.run-recipe"
DOCUMENT_VERSION = 1
# A recipe is a few kilobytes; anything near this is not one.
MAX_DOCUMENT_BYTES = 256 * 1024
_COMPAT = "openai_compat:"


def name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def recipe_out(row: SavedRunRecipe) -> RecipeOut:
    return RecipeOut(id=row.id, name=row.name, recipe=row.recipe or {},
                     created_at=row.created_at, updated_at=row.updated_at)


def checked(draft: RunRecipeDraft) -> dict:
    """The draft as stored, after the checks every save runs: an export folder must be
    absolute (this machine's, so the start can use it) and the base folder may not
    climb with `..`. Raises the 400s those helpers raise."""
    if draft.export is not None and draft.export.output_dir.strip():
        sanitize_abs_path(draft.export.output_dir.strip())
    normalize_subfolder(draft.generate.subfolder)
    return draft.model_dump()


async def _by_key(db: AsyncSession, key: str) -> SavedRunRecipe | None:
    return (await db.execute(select(SavedRunRecipe).where(SavedRunRecipe.name_key == key))).scalar_one_or_none()


async def save(db: AsyncSession, name: str, draft: RunRecipeDraft, *, replace: bool) -> SavedRunRecipe:
    """Save under `name`. A recipe that already has the name is replaced in place (its
    id kept) when `replace`, else refused with a 409 the page turns into a question."""
    recipe = checked(draft)
    row = await _by_key(db, name_key(name))
    if row is not None and not replace:
        raise HTTPException(409, f'A recipe named "{row.name}" already exists')
    if row is None:
        row = SavedRunRecipe(name=name, name_key=name_key(name), recipe=recipe)
        db.add(row)
    else:
        row.name = name
        row.recipe = recipe
    await db.commit()
    await db.refresh(row)
    return row


async def rename(db: AsyncSession, recipe_id: str, name: str) -> SavedRunRecipe:
    row = await db.get(SavedRunRecipe, recipe_id)
    if row is None:
        raise HTTPException(404, "Recipe not found")
    other = await _by_key(db, name_key(name))
    if other is not None and other.id != row.id:
        raise HTTPException(409, f'A recipe named "{other.name}" already exists')
    row.name = name
    row.name_key = name_key(name)
    await db.commit()
    await db.refresh(row)
    return row


# ── The document ─────────────────────────────────────────────────────────────

async def _provider_name(db: AsyncSession, provider_id: str) -> str | None:
    p = await db.get(OpenAIProvider, provider_id) if provider_id else None
    return p.name if p is not None else None


async def to_document(db: AsyncSession, row: SavedRunRecipe) -> dict:
    """The recipe as a file another machine can import: no export folder, and each
    provider named rather than given by this machine's id."""
    recipe = copy.deepcopy(row.recipe or {})
    providers: dict[str, str | None] = {"caption": None, "rewrite": None}
    if isinstance(recipe.get("export"), dict):
        recipe["export"]["output_dir"] = ""
    caption = recipe.get("caption")
    if isinstance(caption, dict) and str(caption.get("model", "")).startswith(_COMPAT):
        _, pid, *rest = str(caption["model"]).split(":", 2)
        providers["caption"] = await _provider_name(db, pid)
        caption["model"] = f"{_COMPAT}:{rest[0] if rest else ''}"
    rewrite = recipe.get("rewrite")
    if isinstance(rewrite, dict):
        providers["rewrite"] = await _provider_name(db, str(rewrite.get("provider_id") or ""))
        rewrite["provider_id"] = ""
    return {
        "format": DOCUMENT_FORMAT,
        "version": DOCUMENT_VERSION,
        "name": row.name,
        "recipe": recipe,
        "providers": providers,
    }


async def _provider_by_name(db: AsyncSession, name: Any) -> OpenAIProvider | None:
    if not isinstance(name, str) or not name.strip():
        return None
    return (await db.execute(
        select(OpenAIProvider).where(func.lower(OpenAIProvider.name) == name.strip().lower())
    )).scalars().first()


async def from_document(db: AsyncSession, doc: Any) -> tuple[str, RunRecipeDraft, list[str]]:
    """A document back into a draft, with notes for what it could not carry over.

    Refuses (400) anything that is not a recipe document of a version this build
    reads. An export folder is dropped whatever it holds — it names a folder on the
    machine that made the file. Providers resolve by name, or are left blank.
    """
    if not isinstance(doc, dict) or doc.get("format") != DOCUMENT_FORMAT:
        raise HTTPException(400, "That file is not a Crucible run recipe")
    version = doc.get("version")
    if not isinstance(version, int) or version > DOCUMENT_VERSION or version < 1:
        raise HTTPException(400, f"That recipe is version {version}; this Crucible reads version {DOCUMENT_VERSION}")
    recipe = doc.get("recipe")
    if not isinstance(recipe, dict):
        raise HTTPException(400, "The recipe file has no recipe in it")
    recipe = copy.deepcopy(recipe)
    providers = doc.get("providers") if isinstance(doc.get("providers"), dict) else {}
    notes: list[str] = []
    stages = recipe.get("stages") if isinstance(recipe.get("stages"), list) else []

    export = recipe.get("export")
    if isinstance(export, dict):
        if str(export.get("output_dir") or "").strip():
            notes.append("The export folder was left out: it names a folder on the machine that made the file.")
        export["output_dir"] = ""
        if "export" in stages:
            notes.append("Choose an export folder before starting.")

    caption = recipe.get("caption")
    if isinstance(caption, dict) and str(caption.get("model", "")).startswith(_COMPAT):
        model_name = str(caption["model"]).split(":", 2)[2] if str(caption["model"]).count(":") >= 2 else ""
        provider = await _provider_by_name(db, providers.get("caption"))
        if provider is not None:
            caption["model"] = f"{_COMPAT}{provider.id}:{model_name}" if model_name else f"{_COMPAT}{provider.id}"
        else:
            caption["model"] = ""
            if "caption" in stages:
                notes.append(f'No LLM provider here is named "{providers.get("caption") or "?"}": choose a caption model.')

    rewrite = recipe.get("rewrite")
    if isinstance(rewrite, dict):
        provider = await _provider_by_name(db, providers.get("rewrite"))
        rewrite["provider_id"] = provider.id if provider is not None else ""
        if provider is None and "rewrite" in stages:
            notes.append(f'No LLM provider here is named "{providers.get("rewrite") or "?"}": choose one under Rewrite.')

    try:
        draft = RunRecipeDraft.model_validate(recipe)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise HTTPException(400, f"The recipe is not valid{f' at {where}' if where else ''}: {first.get('msg', e)}")
    name = doc.get("name") if isinstance(doc.get("name"), str) and doc["name"].strip() else "Imported recipe"
    return " ".join(name.split())[:120], draft, notes


async def free_name(db: AsyncSession, name: str) -> str:
    """`name`, or `name (2)`, `name (3)` … whichever no recipe has yet."""
    if await _by_key(db, name_key(name)) is None:
        return name
    n = 2
    while True:
        candidate = f"{name[:110]} ({n})"
        if await _by_key(db, name_key(candidate)) is None:
            return candidate
        n += 1
