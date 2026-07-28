import asyncio
import mimetypes
import os
import shutil
import string
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.media_types import IMAGE_EXTENSIONS, media_kind_for, video_mime
from backend.models import Dataset, Image, Video
from backend.services.image_service import extract_generation_metadata, get_image_info
from backend.utils import sanitize_abs_path

router = APIRouter(prefix="/filesystem", tags=["filesystem"])


async def _find_dataset_for_path(db: AsyncSession, file_path: Path) -> Dataset | None:
    """Return the Dataset whose folder contains file_path, or None."""
    path_str = str(file_path)
    # Use a LIKE pre-filter to avoid loading every dataset; relative_to is the authoritative check.
    result = await db.execute(
        select(Dataset)
        .where(literal(path_str).like(Dataset.folder_path + "%"))
        .order_by(func.length(Dataset.folder_path).desc())
    )
    for ds in result.scalars():
        try:
            file_path.relative_to(ds.folder_path)
            return ds
        except ValueError:
            pass
    return None


# ── Drive roots ──────────────────────────────────────────────────────────────

@router.get("/roots")
async def list_roots():
    drives = []
    for letter in string.ascii_uppercase:
        p = Path(f"{letter}:\\")
        if p.exists():
            drives.append({"path": str(p), "label": str(p)})
    return {"roots": drives, "datasets_dir": str(settings.datasets_dir)}


# ── Directory listing ─────────────────────────────────────────────────────────

@router.get("/list")
async def list_directory(path: str = Query(...)):
    p = sanitize_abs_path(path)
    if not p.exists():
        raise HTTPException(404, "Path not found")
    if not p.is_dir():
        raise HTTPException(400, "Path is not a directory")

    def _list_dir() -> list[dict]:
        result: list[dict] = []
        try:
            children = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            raise
        for child in children:
            try:
                stat = child.stat()
                result.append({
                    "name": child.name,
                    "path": str(child),
                    "type": "dir" if child.is_dir() else "file",
                    "size_bytes": stat.st_size if child.is_file() else None,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "media_kind": media_kind_for(child.suffix) if child.is_file() else None,
                    "extension": child.suffix.lstrip(".").upper() if child.is_file() else None,
                })
            except (PermissionError, OSError):
                pass
        return result

    try:
        entries = await asyncio.get_running_loop().run_in_executor(None, _list_dir)
    except PermissionError:
        raise HTTPException(403, "Access denied")

    return {"path": str(p), "entries": entries}


# ── Media preview ─────────────────────────────────────────────────────────────

@router.get("/preview")
async def preview_media(path: str = Query(...)):
    """Serve any ingestible media file for the browser's preview panel.

    Videos are served through the same route as images rather than a second
    endpoint: FileResponse supplies `accept-ranges` and 206 on its own, which is
    all a <video> needs to seek. The path is client-supplied and only
    `sanitize_abs_path`-checked — the same deliberate local-desktop posture this
    endpoint has always had for images (see docs/dev/workspace.md § Path
    safety); widening the extension allowlist adds no new exposure class.
    """
    p = sanitize_abs_path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "File not found")
    kind = media_kind_for(p.suffix)
    if kind is None:
        raise HTTPException(400, "Not a previewable media file")
    if kind == "video":
        return FileResponse(str(p), media_type=video_mime(p.suffix))
    mime, _ = mimetypes.guess_type(str(p))
    return FileResponse(str(p), media_type=mime or "image/png")


# ── Image metadata (without DB) ───────────────────────────────────────────────

@router.get("/image-meta")
async def image_meta(path: str = Query(...)):
    p = sanitize_abs_path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "File not found")
    if p.suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(400, "Not an image file")

    info = get_image_info(str(p))
    gen_meta = extract_generation_metadata(str(p))
    return {**info, "generation_metadata": gen_meta}


# ── Move ──────────────────────────────────────────────────────────────────────

class MoveRequest(BaseModel):
    src: str
    dst_dir: str


@router.post("/move")
async def move_path(req: MoveRequest, db: AsyncSession = Depends(get_db)):
    src = sanitize_abs_path(req.src)
    dst_dir = sanitize_abs_path(req.dst_dir)

    if not src.exists():
        raise HTTPException(404, "Source not found")
    if not dst_dir.is_dir():
        raise HTTPException(400, "Destination is not a directory")

    new_path = dst_dir / src.name
    if new_path.exists():
        raise HTTPException(409, "A file or folder with that name already exists at the destination")

    # Classify *before* the move. `src` no longer exists afterwards, so asking it
    # `is_file()` / `is_dir()` down there answers False for both and the entire
    # sync below becomes dead code — every moved file silently leaves a row
    # pointing at nothing.
    src_is_file = src.is_file()
    src_is_dir = src.is_dir()

    try:
        shutil.move(str(src), str(new_path))
    except PermissionError:
        raise HTTPException(403, "Access denied")

    # Sync DB records for any media within the moved path. Videos get the same
    # treatment as images: a moved file whose row still points at the old path is
    # a dangling record either way.
    kind = media_kind_for(src.suffix) if src_is_file else None
    if kind is not None:
        model = Image if kind == "image" else Video
        result = await db.execute(select(model).where(model.file_path == str(src)))
        row = result.scalar_one_or_none()
        if row:
            row.file_path = str(new_path)
            row.filename = new_path.name
            # Update dataset if destination is inside a different dataset folder
            new_ds = await _find_dataset_for_path(db, new_path)
            if new_ds and new_ds.id != row.dataset_id:
                row.dataset_id = new_ds.id
            await db.commit()
    elif src_is_dir:
        # Update every media row whose file_path started with the old dir path
        old_prefix = str(src) + os.sep
        touched = False
        for model in (Image, Video):
            result = await db.execute(select(model).where(model.file_path.startswith(old_prefix)))
            rows = result.scalars().all()
            for row in rows:
                rel = Path(row.file_path).relative_to(src)
                row.file_path = str(new_path / rel)
            touched = touched or bool(rows)
        if touched:
            await db.commit()

    return {"new_path": str(new_path)}


# ── Rename ────────────────────────────────────────────────────────────────────

class RenameRequest(BaseModel):
    path: str
    new_name: str


@router.post("/rename")
async def rename_path(req: RenameRequest, db: AsyncSession = Depends(get_db)):
    if "/" in req.new_name or "\\" in req.new_name or "\x00" in req.new_name:
        raise HTTPException(400, "new_name must not contain path separators")

    p = sanitize_abs_path(req.path)
    if not p.exists():
        raise HTTPException(404, "Path not found")

    new_path = p.parent / req.new_name
    if new_path.exists():
        raise HTTPException(409, "A file or folder with that name already exists")

    try:
        p.rename(new_path)
    except PermissionError:
        raise HTTPException(403, "Access denied")

    # Sync DB
    if new_path.is_file():
        for model in (Image, Video):
            result = await db.execute(select(model).where(model.file_path == str(p)))
            row = result.scalar_one_or_none()
            if row:
                row.file_path = str(new_path)
                row.filename = new_path.name
                await db.commit()
                break

    return {"new_path": str(new_path)}


# ── Delete ────────────────────────────────────────────────────────────────────

class DeleteRequest(BaseModel):
    path: str


@router.post("/delete")
async def delete_path(req: DeleteRequest, db: AsyncSession = Depends(get_db)):
    p = sanitize_abs_path(req.path)
    if not p.exists():
        raise HTTPException(404, "Path not found")

    # Capture type before any mutation; build dir prefix with separator to avoid
    # matching sibling paths that share a common prefix (e.g. /foo/bar vs /foo/bar_2).
    is_dir = p.is_dir()
    old_prefix = str(p) + os.sep if is_dir else None

    # Delete from filesystem first — if this fails, DB records are left intact.
    try:
        if is_dir:
            shutil.rmtree(str(p))
        else:
            p.unlink()
    except PermissionError:
        raise HTTPException(403, "Access denied")

    # Filesystem deletion succeeded; now remove DB records.
    if not is_dir:
        for model in (Image, Video):
            result = await db.execute(select(model).where(model.file_path == str(p)))
            row = result.scalar_one_or_none()
            if row:
                await db.delete(row)
                break
    else:
        for model in (Image, Video):
            await db.execute(delete(model).where(model.file_path.startswith(old_prefix)))
    await db.commit()

    return {"ok": True}


# ── Mkdir ─────────────────────────────────────────────────────────────────────

class MkdirRequest(BaseModel):
    parent: str
    name: str


@router.post("/mkdir")
async def make_directory(req: MkdirRequest):
    if "/" in req.name or "\\" in req.name or "\x00" in req.name:
        raise HTTPException(400, "name must not contain path separators")

    parent = sanitize_abs_path(req.parent)
    if not parent.is_dir():
        raise HTTPException(400, "Parent is not a directory")

    new_dir = parent / req.name
    if new_dir.exists():
        raise HTTPException(409, "Directory already exists")

    try:
        new_dir.mkdir(parents=False)
    except PermissionError:
        raise HTTPException(403, "Access denied")

    return {"path": str(new_dir)}
