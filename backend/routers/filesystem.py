import asyncio
import mimetypes
import os
import shutil
import string
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import Dataset, Image
from backend.services.image_service import extract_generation_metadata, get_image_info

router = APIRouter(prefix="/filesystem", tags=["filesystem"])

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".avif"}


def _sanitize_path(path: str) -> Path:
    """Validate path string and return a Path object. Rejects null bytes."""
    if "\x00" in path:
        raise HTTPException(400, "Invalid path")
    p = Path(path)
    if not p.is_absolute():
        raise HTTPException(400, "Path must be absolute")
    return p


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
    p = _sanitize_path(path)
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
                    "is_image": child.suffix.lower() in IMAGE_EXTENSIONS,
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


# ── Native OS folder picker ────────────────────────────────────────────────────

_TK_SCRIPT = (
    "import tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
    "p = filedialog.askdirectory(title='Select a folder to import into Crucible')\n"
    "print(p or '')\n"
)


def _tk_pick_folder() -> str | None:
    """Try a tkinter folder dialog in a subprocess (widens Linux coverage where zenity/
    kdialog are absent but python3-tk is present).

    Runs in a fresh process so Tk never touches the async server's threads. Returns the
    chosen path ("" if the user cancels), or None if tkinter is unavailable or cannot
    open a window (no python3-tk, no display, etc.) so the caller can fall through.
    """
    try:
        proc = subprocess.run([sys.executable, "-c", _TK_SCRIPT], capture_output=True, text=True, timeout=600)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _pick_folder_sync() -> str:
    """Open the host OS's native folder-selection dialog and return the chosen path.

    Crucible is a local-first app (server == the user's machine), so the dialog appears
    on the user's desktop. Returns "" if the user cancels. Raises RuntimeError if no
    native dialog is available (e.g. a headless server).
    """
    plat = sys.platform
    if plat == "win32":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$f.Description = 'Select a folder to import into Crucible';"
            "$f.ShowDialog() | Out-Null;"
            "Write-Output $f.SelectedPath"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, timeout=600,
        )
        return proc.stdout.strip()
    if plat == "darwin":
        script = 'POSIX path of (choose folder with prompt "Select a folder to import into Crucible")'
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
        return proc.stdout.strip()  # empty on cancel (osascript exits non-zero, no output)
    # Linux / other: try zenity, then kdialog, then a tkinter dialog
    for cmd in (
        ["zenity", "--file-selection", "--directory", "--title=Select a folder to import into Crucible"],
        ["kdialog", "--getexistingdirectory", "."],
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return proc.stdout.strip()
        return ""  # non-zero from a present tool means the user cancelled
    tk_path = _tk_pick_folder()
    if tk_path is not None:
        return tk_path
    raise RuntimeError(
        "No native folder dialog available on this machine "
        "(install zenity, kdialog, or the python3-tk package on Linux). "
        "You can also type or paste the folder path manually."
    )


@router.post("/pick-folder")
async def pick_folder():
    try:
        path = await asyncio.get_running_loop().run_in_executor(None, _pick_folder_sync)
    except subprocess.TimeoutExpired:
        path = ""
    except (RuntimeError, FileNotFoundError) as e:
        raise HTTPException(501, str(e) or "No native folder dialog available on this machine.")
    return {"path": path or None}


# ── Image preview ─────────────────────────────────────────────────────────────

@router.get("/preview")
async def preview_image(path: str = Query(...)):
    p = _sanitize_path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "File not found")
    if p.suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(400, "Not an image file")
    mime, _ = mimetypes.guess_type(str(p))
    return FileResponse(str(p), media_type=mime or "image/png")


# ── Image metadata (without DB) ───────────────────────────────────────────────

@router.get("/image-meta")
async def image_meta(path: str = Query(...)):
    p = _sanitize_path(path)
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
    src = _sanitize_path(req.src)
    dst_dir = _sanitize_path(req.dst_dir)

    if not src.exists():
        raise HTTPException(404, "Source not found")
    if not dst_dir.is_dir():
        raise HTTPException(400, "Destination is not a directory")

    new_path = dst_dir / src.name
    if new_path.exists():
        raise HTTPException(409, "A file or folder with that name already exists at the destination")

    try:
        shutil.move(str(src), str(new_path))
    except PermissionError:
        raise HTTPException(403, "Access denied")

    # Sync DB records for any images within the moved path
    if src.is_file() and src.suffix.lower() in IMAGE_EXTENSIONS:
        result = await db.execute(select(Image).where(Image.file_path == str(src)))
        img = result.scalar_one_or_none()
        if img:
            img.file_path = str(new_path)
            img.filename = new_path.name
            # Update dataset if destination is inside a different dataset folder
            new_ds = await _find_dataset_for_path(db, new_path)
            if new_ds and new_ds.id != img.dataset_id:
                img.dataset_id = new_ds.id
            await db.commit()
    elif src.is_dir():
        # Update all images whose file_path started with old dir path
        old_prefix = str(src) + os.sep
        result = await db.execute(select(Image).where(Image.file_path.startswith(old_prefix)))
        imgs = result.scalars().all()
        for img in imgs:
            rel = Path(img.file_path).relative_to(src)
            img.file_path = str(new_path / rel)
        if imgs:
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

    p = _sanitize_path(req.path)
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
        result = await db.execute(select(Image).where(Image.file_path == str(p)))
        img = result.scalar_one_or_none()
        if img:
            img.file_path = str(new_path)
            img.filename = new_path.name
            await db.commit()

    return {"new_path": str(new_path)}


# ── Delete ────────────────────────────────────────────────────────────────────

class DeleteRequest(BaseModel):
    path: str


@router.post("/delete")
async def delete_path(req: DeleteRequest, db: AsyncSession = Depends(get_db)):
    p = _sanitize_path(req.path)
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
        result = await db.execute(select(Image).where(Image.file_path == str(p)))
        img = result.scalar_one_or_none()
        if img:
            await db.delete(img)
    else:
        await db.execute(delete(Image).where(Image.file_path.startswith(old_prefix)))
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

    parent = _sanitize_path(req.parent)
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
