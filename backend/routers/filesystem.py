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
from sqlalchemy import delete, func, literal, or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.media_types import IMAGE_EXTENSIONS, media_kind_for, video_mime
from backend.models import Dataset, Image, Video
from backend.services import version_service
from backend.services.dataset_busy import ensure_not_busy
from backend.services.dataset_service import refresh_stats
from backend.services.image_service import extract_generation_metadata, get_image_info
from backend.utils import (
    chunked,
    rename_with_sidecar,
    sanitize_abs_path,
    thumbnail_path_for,
    within_datasets_dir,
)

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


_DATASET_LAYOUT_DIRS = frozenset({"images", "videos", "thumbnails", ".versions"})
"""The folder names a dataset's own layout owns, as rules rather than constructions.

Two sources of truth: `dataset_service.create_dataset` mkdirs `images/` and
`thumbnails/` (and `video_service` adds `videos/` on the first video ingest), and
`version_service`'s module docstring pins `.versions/objects/{sha[:2]}/{sha[2:]}`.

This is the first place the layout is stated as a *rule*; the ~20 sites that
inline `Path(folder_path) / "images"` are unaffected and stay that way — no
repo-wide layout-constant refactor rides along with this guard.
"""


async def _guard_directory_rename(db: AsyncSession, p: Path) -> None:
    """Refuse a directory rename that would strand rows, a `Dataset` or the object store.

    Renaming is a same-parent move, so — unlike `/move` — there is no prefix
    rewrite to be had: the only directories that can hold registered media are
    structural ones, and rewriting their paths leaves the DB agreeing with disk
    while the *dataset* stays broken (a renamed `images/` makes `rescan_dataset`
    take its no-`images_dir` early return and the next upload recreate the
    folder; a renamed `videos/` makes `_rescan_videos` report every row missing
    forever; a renamed `{ds}` leaves `Dataset.folder_path` stale, which no path
    rewrite touches). There is also nothing to validate a rewrite against —
    `/move`'s rewrite is coherent only because `_find_dataset_for_path(db,
    new_path)` gives it a destination dataset to check containment against, and
    a rename's new name is arbitrary. So this only ever refuses.

    Guards run **C → D → A → B**: most specific and most actionable first, each
    next one the more general fallback. `{ds}` holding rows should get C's
    "rename the dataset instead", not A's generic message.

    Comparisons are unresolved strings throughout, deliberately.
    `sanitize_abs_path` does not resolve — it rejects null bytes and relative
    paths and hands back a bare `Path`; `Dataset.folder_path` is stored as
    `str(settings.datasets_dir / slug)`, equally unresolved; and the File
    Browser only ever sends paths it got back from `GET /list`. Resolving here
    would harden one guard against a request shape `/move`'s two and `/delete`'s
    scope predicate all still let through.
    """
    old_prefix = str(p) + os.sep

    # C — a dataset's own folder, or any folder above one. The prefix arm is not
    # redundant with A: `p` can be `settings.datasets_dir` itself, which A only
    # catches when some dataset under it is non-empty. This is the guard `/move`
    # lacks entirely.
    ds_row = (await db.execute(
        select(Dataset.folder_path).where(
            or_(
                Dataset.folder_path == str(p),
                Dataset.folder_path.startswith(old_prefix, autoescape=True),
            )
        )
        # Shortest first, so an exact match beats a descendant to the one row we
        # keep: only then does a folder that is both a dataset's own and a
        # dataset's parent get the more actionable of the two messages.
        .order_by(func.length(Dataset.folder_path))
        .limit(1)
    )).first()
    if ds_row is not None:
        if ds_row[0] == str(p):
            raise HTTPException(
                409,
                "This is a dataset's own folder. Use the Datasets page's edit "
                "action to rename the dataset instead.",
            )
        raise HTTPException(
            409,
            "This folder contains a dataset's own folder. Renaming it would "
            "strand every dataset under it.",
        )

    # D — structural names, empty or not. A, B and C are all content-based, so a
    # brand-new dataset's `images/` passes every one of them. Checked only when
    # the name is in the set (an ordinary folder costs zero extra queries) and
    # only for a **direct** child of a dataset folder.
    #
    # `.versions` is the strongest member of the set, not the marginal one: no
    # row anywhere stores a path under it, so A, B and C are blind to it
    # *permanently*, even for a full dataset. Rename it and `restore_version`
    # takes its `elif needs_restore:` branch — `files_unavailable += 1`, every
    # snapshot silently restoring nothing — `_prune_objects_sync` returns zeros
    # forever, and the next `mark_image_deleted_in_versions` mkdirs a fresh
    # `.versions/objects`, splitting the store across two folders.
    #
    # An empty `{ds}/thumbnails` is in the set for legibility — one rule, "a
    # dataset's structural folders are not renameable" — rather than because it
    # is dangerous. `{ds}/videos/thumbnails` is deliberately *not* covered: not a
    # direct child, caught by B the moment a poster exists, and inert when empty
    # (`get_video_poster` backfills into a re-derived path).
    if p.name in _DATASET_LAYOUT_DIRS:
        parent_ds = (await db.execute(
            select(Dataset.id).where(Dataset.folder_path == str(p.parent)).limit(1)
        )).first()
        if parent_ds is not None:
            raise HTTPException(
                409,
                f"{p.name}/ is part of a dataset's folder layout. Renaming it "
                "would split the dataset across two folders.",
            )

    # A — rows under the prefix. `autoescape=True` for the reason `/move` and
    # `/delete` both document at length: `_` is a LIKE wildcard *and* the
    # character `_name_to_slug` puts in every multi-word dataset folder, so
    # unescaped, `{ds}/my_dataset/…` matches `{ds}/myxdataset/…`. Here an
    # over-match is an over-*refusal* rather than a delete, but it is still a
    # refusal of somebody else's perfectly good rename.
    for model in (Image, Video):
        hit = (await db.execute(
            select(model.id).where(
                model.file_path.startswith(old_prefix, autoescape=True)
            ).limit(1)
        )).first()
        if hit is not None:
            raise HTTPException(
                409,
                "This folder holds files that belong to a dataset. Renaming it "
                "would strand every one of their rows.",
            )

    # B — derived artifacts under the prefix while `file_path` is not. Lifted
    # from `/move`'s guard of the same shape, same two conjuncts: for `{ds}/videos`
    # the poster is under the prefix but so is `file_path` (second conjunct
    # False), for `{ds}/images` the thumbnails are in the sibling
    # `{ds}/thumbnails/` (first conjunct False), and for a folder holding only
    # derived files both hold.
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
                "This folder holds thumbnails or posters belonging to a dataset "
                "whose files are elsewhere. Renaming it would strand them.",
            )


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
    endpoint has always had for images (see docs/dev/file-browser.md § Path
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

    # The directory branch's guards run before the move, so each can refuse with
    # the filesystem untouched — the convention every guard in this file follows.
    # The original code queried only *after* `shutil.move`, which is why it had
    # nowhere to put a containment check.
    if src_is_dir:
        for model in (Image, Video):
            # autoescape=True: `startswith` compiles to a SQL LIKE, where `_` is a
            # single-character wildcard — and `_` is the *normal* case in a dataset
            # folder path, since `_name_to_slug` collapses whitespace to it and the
            # collision fallback appends `_{ds_id[:8]}`. Unescaped, `{ds}/my_dataset`
            # matches `{ds}/myXdataset`; the over-match is an over-*refusal* rather
            # than a wrong rewrite, but it is still a refusal of somebody else's
            # perfectly good move. Same reasoning as `_guard_directory_rename`'s A.
            hit = (await db.execute(
                select(model.id).where(
                    model.file_path.startswith(old_prefix, autoescape=True)
                ).limit(1)
            )).first()
            if hit is not None:
                # Refused wherever it goes, not only across a dataset boundary.
                # A registered file has exactly one supported home — directly in
                # its dataset's `images/` or `videos/` — which is what the file
                # branch above enforces per row; a folder move can only ever land
                # its contents one level *deeper* than that (`new_path.exists()`
                # already refuses a move back onto the original), so there is no
                # destination this branch could permit that the file branch would
                # not refuse file by file.
                #
                # Same-dataset was permitted here until V-21, together with a
                # prefix rewrite that kept every row consistent with the new
                # location. The rows were right and the app was still wrong:
                # `rescan_dataset`/`_rescan_videos` glob the canonical folders
                # non-recursively, so every moved row is reported missing forever
                # while its file is fine, and `thumbnail_path_for`'s `parent.parent`
                # resolves into a `thumbnails/` beside the *new* parent, orphaning
                # the real thumbnail at the next rename. Supporting the layout
                # instead would mean a recursive rescan and stored-not-derived
                # thumbnail paths at eleven sites, against CLAUDE.md's flat-storage
                # invariant. So this endpoint refuses to create a layout the rest
                # of the app cannot read, and the rewrite went with the permission.
                raise HTTPException(
                    409,
                    "This folder holds files that belong to a dataset. Use the "
                    "gallery's 'Move to dataset' action to move them.",
                )

        # A folder of *derived* artifacts — `{ds}/thumbnails` or
        # `{ds}/videos/thumbnails` — holds no `file_path` at all, so the guard
        # above sees nothing: every stored `thumbnail_path`/`poster_path` in it
        # would be stranded and the move would return 200. The predicate is
        # "derived under the prefix while `file_path` is NOT", which is what
        # distinguishes it from a media folder whose derived files travel along:
        #
        #   {ds}/videos            → poster under the prefix, but so is file_path
        #                            → second conjunct False; refused above instead.
        #   {ds}/images            → thumbnails are in the *sibling*
        #                            {ds}/thumbnails/ → first conjunct False; also
        #                            refused above.
        #   {ds}/thumbnails        → both True → refused, here. This is the only
        #                            arm that still refuses anything on its own,
        #                            since a folder holding `file_path`s never
        #                            reaches it.
        #   {ds}                   → both under the prefix → refused above.
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

    # Only the file branch has a row to sync. The directory branch has none by
    # construction: any folder holding a registered `file_path` was refused above,
    # so every directory that reaches this point is unregistered and there is
    # nothing to rewrite. No `refresh_stats` either — no count, size or
    # `dataset_id` can change on a move this endpoint permits.
    if row is not None:
        row.file_path = str(new_path)
        row.filename = new_path.name
        await db.commit()

    return {"new_path": str(new_path)}


# ── Rename ────────────────────────────────────────────────────────────────────

class RenameRequest(BaseModel):
    path: str
    new_name: str


@router.post("/rename")
async def rename_path(req: RenameRequest, db: AsyncSession = Depends(get_db)):
    """Rename a file or folder, carrying a registered media row's derived artifacts.

    Order: **gather → guard → mutate → commit**, the shape `/move` and `/delete`
    already use. Rows are loaded *before* `p.rename(...)` so every 409 below can
    still refuse with the filesystem intact — the old code synced the DB after
    the rename, which left no point at which a guard could say no.

    The file browser is one of the three paths that **adopt** a name rather than
    pick one (rescan's two halves are the others), so it cannot dodge a stem
    collision through `unique_filename_with_thumb`: a name the user typed must
    either be taken as typed or refused. It refuses, matching the endpoint's
    other conflicts.

    A **directory** takes neither arm — `_guard_directory_rename` refuses it
    outright whenever it touches a dataset, since a rename has no coherent
    prefix rewrite to offer the way `/move` does.
    """
    if "/" in req.new_name or "\\" in req.new_name or "\x00" in req.new_name:
        raise HTTPException(400, "new_name must not contain path separators")

    p = sanitize_abs_path(req.path)
    if not p.exists():
        raise HTTPException(404, "Path not found")

    new_path = p.parent / req.new_name
    if new_path.exists():
        raise HTTPException(409, "A file or folder with that name already exists")

    if p.is_dir():
        # A directory matches neither arm below (both key on `file_path ==
        # str(p)`), and a rename has no coherent prefix rewrite to offer in
        # their place — so every directory that touches a dataset is refused
        # here instead. No `ensure_not_busy`: every dataset-touching case is a
        # refusal, and a permitted rename touches no dataset row at all.
        await _guard_directory_rename(db, p)

    # Gather. Only a file can match; a directory has already been through the
    # guard above and holds nothing registered.
    img: Image | None = None
    vid: Video | None = None
    if p.is_file():
        img = (await db.execute(select(Image).where(Image.file_path == str(p)))).scalar_one_or_none()
        if img is None:
            vid = (await db.execute(select(Video).where(Video.file_path == str(p)))).scalar_one_or_none()

    if img is not None:
        ensure_not_busy(img.dataset_id)
        siblings = (await db.execute(
            select(Image.filename)
            .where(Image.dataset_id == img.dataset_id, Image.id != img.id)
        )).all()
        db_names = {r[0] for r in siblings}
        if req.new_name in db_names:
            # Would otherwise surface as a raw uq_dataset_filename IntegrityError 500.
            raise HTTPException(409, f"Another image in this dataset is already named {req.new_name}")

        # Thumbnails are .webp keyed by stem, so `a.jpg` and `a.png` share one
        # derived path: taking a sibling's stem means the next bulk_rename /
        # batch_move / crop / restore recomputes `thumbnail_path_for` and moves or
        # overwrites *that sibling's* thumbnail. Same two terms rescan uses — the
        # on-disk glob, plus the rows' own stems for a thumbnail not yet cut.
        thumb_dir = p.parent.parent / "thumbnails"
        occupied = {q.stem for q in thumb_dir.glob("*.webp")} if thumb_dir.exists() else set()
        occupied |= {Path(fn).stem for fn in db_names}
        # This row's own stem does not block it, which is also what lets a **pure
        # extension change** (`a.jpg` → `a.png`) through: the stem is unchanged,
        # so the thumbnail and the .txt sidecar stay exactly where they are.
        occupied.discard(p.stem)
        if new_path.stem in occupied:
            clash = next((fn for fn in db_names if Path(fn).stem == new_path.stem), None)
            raise HTTPException(
                409,
                f"The thumbnail name '{new_path.stem}.webp' is already taken"
                + (f" by {clash}" if clash else "")
                + " — pick a different name.",
            )

        # A stored path is not a client-supplied one, and the same rule
        # `delete_path._add_orphan` follows applies: a hand-edited pointer outside
        # the datasets tree is left alone with a warning, never `.replace()`d into
        # the tree. The column keeps naming the file that did not move.
        old_thumb: Path | None = None
        if img.thumbnail_path:
            if within_datasets_dir(img.thumbnail_path, settings.datasets_dir) is None:
                logger.warning(
                    "rename_path: leaving out-of-tree thumbnail_path %s alone", img.thumbnail_path
                )
            else:
                old_thumb = Path(img.thumbnail_path)
        new_thumb = Path(thumbnail_path_for(str(new_path)))

        # PM-013: assign every field, then the filesystem, then the commit, with
        # nothing fallible in between. `rename_image` mutates in this exact order.
        img.filename = new_path.name
        img.file_path = str(new_path)
        if old_thumb is not None:
            img.thumbnail_path = str(new_thumb)
        img.is_auto_named = False  # a name the user typed is not auto-generated
        try:
            rename_with_sidecar(p, new_path)  # FS last — if this raises, commit never runs
        except PermissionError:
            raise HTTPException(403, "Access denied")
        if old_thumb is not None and old_thumb.exists() and old_thumb != new_thumb:
            old_thumb.replace(new_thumb)
        await db.commit()
        return {"new_path": str(new_path)}

    if vid is not None:
        ensure_not_busy(vid.dataset_id)
        siblings = (await db.execute(
            select(Video.filename)
            .where(Video.dataset_id == vid.dataset_id, Video.id != vid.id)
        )).all()
        if req.new_name in {r[0] for r in siblings}:
            # Cheap pre-check for uq_dataset_video_filename: a 409 like the
            # image arm's, rather than an IntegrityError 500.
            raise HTTPException(409, f"Another video in this dataset is already named {req.new_name}")

        # `poster_path` is deliberately untouched, and there is no stem guard:
        # nothing re-derives a poster path — every consumer reads the column — so
        # a poster whose stem no longer matches its video is the normal state
        # (rescan produces it on purpose) and cannot be clobbered by a later
        # operation. Compare the image arm above, where the derived path *is*
        # recomputed from the filename by eleven sites.
        vid.filename = new_path.name
        vid.file_path = str(new_path)
        try:
            p.rename(new_path)  # FS last — if this raises, commit never runs
        except PermissionError:
            raise HTTPException(403, "Access denied")
        await db.commit()
        return {"new_path": str(new_path)}

    # No row: a plain rename, which is the file browser's actual job.
    try:
        p.rename(new_path)
    except PermissionError:
        raise HTTPException(403, "Access denied")
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
