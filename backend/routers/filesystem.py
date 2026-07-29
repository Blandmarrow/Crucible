import asyncio
import logging
import mimetypes
import os
import shutil
import string
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, literal, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.media_types import IMAGE_EXTENSIONS, media_kind_for, video_mime
from backend.models import Dataset, Image, Video
from backend.services import version_service
from backend.services.dataset_busy import ensure_not_busy
from backend.services.dataset_service import refresh_stats
from backend.services.image_service import extract_generation_metadata, get_image_info
from backend.utils import chunked, sanitize_abs_path, within_datasets_dir

logger = logging.getLogger(__name__)

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
    # The directory branch's prefix is derived from `src` for the same reason:
    # it has to be captured while `src` still means something.
    old_prefix = str(src) + os.sep

    # Look the row up before the move too, so the 409 below lands with the
    # filesystem still untouched — the convention every other guard in this file
    # follows. Videos get the same treatment as images: a moved file whose row
    # still points at the old path is a dangling record either way.
    kind = media_kind_for(src.suffix) if src_is_file else None
    row = None
    if kind is not None:
        model = Image if kind == "image" else Video
        row = (
            await db.execute(select(model).where(model.file_path == str(src)))
        ).scalar_one_or_none()
        if row is not None:
            # Refuse to re-home a registered file. This endpoint knows how to
            # rewrite a path and nothing else, while re-homing means everything
            # `batch_move_dataset` does — materialize_provenance, refresh_stats,
            # NULLing `Image.source_video_id`, and moving `Video.poster_path` /
            # `Image.thumbnail_path` — none of which happens here.
            #
            # The check is broader than "another dataset" on purpose: moving a
            # registered file out of the datasets tree entirely is equally
            # broken, since `utils.safe_dataset_path` then 403s every request for
            # its bytes. Unregistered files still move anywhere — that is the
            # file browser's actual job.
            new_ds = await _find_dataset_for_path(db, new_path)
            if new_ds is None or new_ds.id != row.dataset_id:
                raise HTTPException(
                    409,
                    "This file belongs to a dataset. Use the gallery's "
                    "'Move to dataset' action to move it.",
                )

            # Same dataset is not enough: a registered file has to stay directly
            # in its dataset's canonical media folder. Two failure modes, both
            # silent:
            #
            # - `_rescan_videos` globs `videos_dir.glob("*")` non-recursively, so
            #   a video parked in `{ds}/images/` or `{ds}/videos/sub/` is reported
            #   under `videos_missing` forever while the file is perfectly fine.
            # - images are stored *flat* — `Image.subfolder` is a purely logical
            #   column — so `images/sub/a.jpg` makes `thumbnail_path_for`'s
            #   `parent.parent` resolve to `images/thumbnails/`, at all eleven
            #   sites that re-derive a thumbnail path from a filename.
            #
            # This sits under `row is not None` on purpose: unregistered files
            # still move anywhere, which is the file browser's actual job.
            canonical = "images" if kind == "image" else "videos"
            if new_path.parent.relative_to(new_ds.folder_path) != Path(canonical):
                raise HTTPException(
                    409,
                    f"A registered {kind} must stay directly in its dataset's "
                    f"{canonical}/ folder.",
                )

    # The directory branch's rows are loaded before the move as well — not for
    # convenience but because the guard below has to be able to refuse with the
    # filesystem untouched, which is the convention every guard in this file
    # follows. The original code queried only *after* `shutil.move`, which is
    # why it had nowhere to put a containment check.
    dir_rows: list[tuple[Image | Video, str]] = []
    if src_is_dir:
        for model, derived in ((Image, "thumbnail_path"), (Video, "poster_path")):
            # autoescape=True: `startswith` compiles to a SQL LIKE, where `_` is a
            # single-character wildcard — and `_` is the *normal* case in a dataset
            # folder path, since `_name_to_slug` collapses whitespace to it and the
            # collision fallback appends `_{ds_id[:8]}`. Unescaped, `{ds}/my_dataset`
            # matches `{ds}/myXdataset`, and the over-matched row then makes
            # `Path(media.file_path).relative_to(src)` raise *after* `shutil.move`
            # has run — files moved, no commit, 500 (a PM-013 shape).
            result = await db.execute(
                select(model).where(model.file_path.startswith(old_prefix, autoescape=True))
            )
            dir_rows.extend((media, derived) for media in result.scalars().all())
        if dir_rows:
            # The directory twin of the file guard above, and refused for the
            # same reason: this endpoint rewrites paths and nothing else, so a
            # folder of registered media leaving its dataset strands every row —
            # `utils.safe_dataset_path` 403s each one's bytes and the gallery
            # goes blank with nothing in the DB saying why.
            #
            # Set equality, not "some dataset was found": a moved tree can span
            # two datasets, and then no single destination is the same dataset
            # for all of them, so `!=` refuses a move that would re-home half the
            # rows and strand the rest.
            new_ds = await _find_dataset_for_path(db, new_path)
            if new_ds is None or {media.dataset_id for media, _ in dir_rows} != {new_ds.id}:
                raise HTTPException(
                    409,
                    "This folder holds files that belong to a dataset. Use the "
                    "gallery's 'Move to dataset' action to move them.",
                )

        # A folder of *derived* artifacts — `{ds}/thumbnails` or
        # `{ds}/videos/thumbnails` — holds no `file_path` at all, so the guard
        # above sees nothing and the rewrite below has nothing to rewrite: every
        # stored `thumbnail_path`/`poster_path` in it is stranded and the move
        # returns 200. The predicate is "derived under the prefix while
        # `file_path` is NOT", which is what distinguishes it from an ordinary
        # media folder whose derived files travel along:
        #
        #   {ds}/videos            → poster under the prefix, but so is file_path
        #                            → second conjunct False, permitted.
        #   {ds}/images            → thumbnails are in the *sibling*
        #                            {ds}/thumbnails/ → first conjunct False.
        #   {ds}/thumbnails        → both True → refused, here.
        #   {ds}                   → both under the prefix → falls through to the
        #                            dir_rows guard above, which already refuses it.
        for model, derived in ((Image, "thumbnail_path"), (Video, "poster_path")):
            col = getattr(model, derived)
            stranded = (await db.execute(
                select(model.id).where(
                    col.startswith(old_prefix, autoescape=True),
                    ~model.file_path.startswith(old_prefix, autoescape=True),
                ).limit(1)
            )).first()
            if stranded is not None:
                raise HTTPException(
                    409,
                    "This folder holds thumbnails or posters belonging to a "
                    "dataset whose files are elsewhere. Moving it would strand them.",
                )

    try:
        shutil.move(str(src), str(new_path))
    except PermissionError:
        raise HTTPException(403, "Access denied")

    if row is not None:
        row.file_path = str(new_path)
        row.filename = new_path.name
        await db.commit()
    elif dir_rows:
        # Update every media row whose file_path started with the old dir path,
        # plus the derived artifact each model keys off a path of its own. A
        # poster or thumbnail *inside* the moved tree travelled with it and its
        # stored path is now wrong; one outside did not move and its path is
        # still right — an image's thumbnails sit in `{ds}/thumbnails/`, beside
        # `images/` rather than under it, so moving `images/` alone must leave
        # them alone. Hence the per-column prefix test, not a blanket rewrite.
        #
        # No `refresh_stats`: it recomputes counts and sizes only — never a path,
        # never `dataset_id` — and the guard above makes every permitted move a
        # same-dataset one, so none of those numbers can change here.
        for media, derived in dir_rows:
            media.file_path = str(new_path / Path(media.file_path).relative_to(src))
            old_derived = getattr(media, derived)
            if old_derived and str(old_derived).startswith(old_prefix):
                setattr(media, derived, str(new_path / Path(old_derived).relative_to(src)))
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
    """Delete a file or folder, with the same care the router deletes take.

    Order: **gather → guard → hook → stage + flush → filesystem → commit →
    epilogue.** That reconciles two rules which look opposed. The original
    comment here ("delete from the filesystem first — a failed FS deletion leaves
    DB records intact") is about *commit* ordering and survives: if `rmtree`
    raises we return before `commit()` and `get_db`'s session discards
    everything staged. CLAUDE.md's PM-013 invariant is about *what may fail
    between the mutation and the commit* — satisfied by issuing every fallible
    statement before the irreversible step and closing them with an explicit
    `flush()`, leaving only the `commit()` after it.
    """
    p = sanitize_abs_path(req.path)
    if not p.exists():
        raise HTTPException(404, "Path not found")

    # Capture type before any mutation; build dir prefix with separator to avoid
    # matching sibling paths that share a common prefix (e.g. /foo/bar vs /foo/bar_2).
    is_dir = p.is_dir()
    old_prefix = str(p) + os.sep if is_dir else None

    def _scope(model):
        # autoescape=True for the same reason as in `/move`: `_` is a LIKE
        # wildcard and the normal case in a dataset folder name, so an unescaped
        # prefix let this DELETE reach a *different* dataset's rows.
        if is_dir:
            return model.file_path.startswith(old_prefix, autoescape=True)
        return model.file_path == str(p)

    # ORM entities, loaded before anything is touched: the guards below have to be
    # able to refuse with the filesystem intact, and the versioning hook needs the
    # rows to still exist.
    img_rows = (await db.execute(select(Image).where(_scope(Image)))).scalars().all()
    vid_rows = (await db.execute(select(Video).where(_scope(Video)))).scalars().all()
    dataset_ids = {r.dataset_id for r in img_rows} | {r.dataset_id for r in vid_rows}

    for ds_id in dataset_ids:
        ensure_not_busy(ds_id)

    # Derived artifacts that will *not* be taken by the delete about to run, and
    # would therefore be left behind pointing at nothing.
    #
    # - Directory branch: an orphan is anything that does not start with
    #   `old_prefix`. An image's thumbnail is in `{ds}/thumbnails/`, beside
    #   `images/`, so an rmtree of `images/` never reaches it; a video's poster is
    #   in `videos/thumbnails/` and travels with `videos/`, so it is not an orphan.
    # - File branch: everything except `p` itself — the thumbnail or poster, and
    #   the `.txt` sidecar, which `p.unlink()` really does strand. All three
    #   router deletes unlink theirs; this reaches parity.
    orphans: list[Path] = []

    def _add_orphan(path_str: str | None) -> None:
        if not path_str or (is_dir and path_str.startswith(old_prefix)):
            return
        # A stored path is not a client-supplied one: every dataset folder lives
        # under `settings.datasets_dir`, so a derived path outside it was never
        # written by this app and this endpoint will not unlink it on the strength
        # of a request that named a different directory.
        safe = within_datasets_dir(path_str, settings.datasets_dir)
        if safe is None:
            logger.warning("delete_path: refusing to unlink out-of-tree artifact %s", path_str)
            return
        orphans.append(safe)

    for img in img_rows:
        _add_orphan(img.thumbnail_path)
        if not is_dir:
            _add_orphan(str(Path(img.file_path).with_suffix(".txt")))
    for vid in vid_rows:
        _add_orphan(vid.poster_path)

    # PM-003's hook, and PM-014's recurrence of it: this is a delete endpoint like
    # any other, so the bytes go to the object store before they go anywhere. It
    # must run *before* the row deletes — it does `db.get(Image, image_id)`
    # internally, which after a staged delete autoflushes and returns None, and the
    # hook then no-ops via its `dataset is None` early return — and before the
    # unlink, since `_backup_and_record_hash` early-returns on a file that is gone.
    for img in img_rows:
        await version_service.mark_image_deleted_in_versions(img.id, img.file_path, db)

    # Frames extracted from a deleted video are ordinary Image rows and survive
    # with their lineage cut, exactly as `DELETE /videos/{id}` leaves them.
    for batch in chunked([v.id for v in vid_rows]):
        await db.execute(
            sa_update(Image).where(Image.source_video_id.in_(batch)).values(source_video_id=None)
        )
    for batch in chunked([i.id for i in img_rows]):
        await db.execute(delete(Image).where(Image.id.in_(batch)))
    for batch in chunked([v.id for v in vid_rows]):
        await db.execute(delete(Video).where(Video.id.in_(batch)))
    await db.flush()

    try:
        if is_dir:
            shutil.rmtree(str(p))
        else:
            p.unlink()
    except PermissionError:
        raise HTTPException(403, "Access denied")

    await db.commit()

    # Epilogue — fallible, and unable to change the outcome of anything above.
    for f in orphans:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            logger.warning("delete_path: could not remove orphaned artifact %s", f, exc_info=True)
    for ds_id in dataset_ids:
        await refresh_stats(db, ds_id)

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
