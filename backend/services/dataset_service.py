import asyncio
import logging
import re
import shutil
import statistics
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

from sqlalchemy import case, func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.licenses import PROVENANCE_FIELDS, copy_provenance, merge_provenance
from backend.media_types import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from backend.models import Dataset, Image, Video
from backend.services.caption_service import _write_txt_sidecar
from backend.services.image_service import (
    extract_generation_metadata,
    extract_embedded_provenance,
    generate_thumbnail,
    get_image_info,
    read_provenance_sidecar,
)
from backend.services.video_service import UnreadableVideoError, claimed_poster_stems, probe_and_poster
from backend.utils import (
    copy_with_sidecar,
    poster_path_for,
    read_caption_sidecar,
    require_free_space,
    thumbnail_path_for,
    unique_filename_with_thumb,
    unique_poster_path,
)

logger = logging.getLogger(__name__)

# Cap on how many per-file error details we retain in a job summary (failed_count keeps the full tally).
_MAX_FAILED_DETAILS = 50

# Largest license buckets returned by get_dataset_stats; the remainder collapses
# into LICENSE_BREAKDOWN_OTHER_KEY so the counts still sum to the dataset total.
# Comfortably above the curated vocabulary (13 ids) — the cap only ever bites on
# `other:<free text>` values, which are unbounded.
LICENSE_BREAKDOWN_LIMIT = 20
LICENSE_BREAKDOWN_OTHER_KEY = "__other_licenses__"

# Cap on the distinct licenses get_licenses_in_use reports. Deliberately looser
# than the breakdown cap: that one only drops rows from a summary panel, while
# this list *is* the picker — a value that falls off the end cannot be selected
# or filtered on at all.
LICENSES_IN_USE_LIMIT = 100

# --- Stats cache -------------------------------------------------------------
# `get_dataset_stats` and `get_score_values` both pull every image row in the
# dataset into Python, and StatsPage polls them live — so an idle Stats tab
# re-reads the whole table every few seconds. Each call first runs a cheap
# validator query (row count + newest `updated_at`, plus the dataset's own
# `updated_at` for stats, whose license breakdown depends on the dataset
# default); an unchanged validator serves the previous payload.
#
# Staleness is bounded to edits that leave the (count, max updated_at) tuple
# identical — impossible for any ORM/Core write, since `Image.updated_at` has an
# `onupdate`. Callers must treat the returned dict as read-only: it is the cached
# object, not a copy.
_STATS_CACHE_MAX = 64
_stats_cache: dict[tuple[str, str | None, str], tuple[tuple, dict]] = {}


def _stats_cache_get(key: tuple[str, str | None, str], validator: tuple) -> dict | None:
    entry = _stats_cache.get(key)
    return entry[1] if entry is not None and entry[0] == validator else None


def _stats_cache_put(key: tuple[str, str | None, str], validator: tuple, payload: dict) -> None:
    _stats_cache[key] = (validator, payload)
    while len(_stats_cache) > _STATS_CACHE_MAX:
        _stats_cache.pop(next(iter(_stats_cache)))  # drop oldest insertion


async def _image_validator(db: AsyncSession, dataset_id: str, subfolder: str | None) -> tuple:
    """(row count, newest updated_at) for one dataset/subfolder scope."""
    where = [Image.dataset_id == dataset_id]
    if subfolder is not None:
        where.append(Image.subfolder == subfolder)
    row = (await db.execute(
        select(func.count(Image.id), func.max(Image.updated_at)).where(*where)
    )).one()
    return (row[0], row[1])


def remove_dataset_dir(folder: Path) -> None:
    """Recursively remove a dataset's on-disk folder, logging (not swallowing) any failure.

    Unlike ``shutil.rmtree(..., ignore_errors=True)``, partial/failed removals are surfaced
    as warnings so an orphaned directory left behind by a locked file or permission mismatch
    is visible in the logs instead of silently persisting. Use this everywhere a dataset
    folder is deleted (the delete endpoint and the startup orphan sweep).
    """
    if not folder.exists():
        return

    failures: list[str] = []

    def _onerror(func, path, exc_info):  # signature covers both onexc (3.12+) and onerror
        exc = exc_info[1] if isinstance(exc_info, tuple) else exc_info
        failures.append(f"{path}: {exc}")

    # Python 3.12 renamed the callback kwarg from onerror to onexc; pass whichever exists.
    try:
        shutil.rmtree(folder, onexc=_onerror)  # type: ignore[call-arg]
    except TypeError:
        shutil.rmtree(folder, onerror=_onerror)

    if failures:
        logger.warning(
            "Failed to fully remove dataset folder %s (%d error(s)): %s",
            folder, len(failures), "; ".join(failures[:10]),
        )


async def sweep_orphan_dataset_folders(db: AsyncSession) -> list[str]:
    """Remove child directories of datasets_dir that no dataset row references.

    Dataset deletion removes the DB row and then the folder, but a folder can be left
    behind if the row disappeared by another route (DB reset/migration/swap) so the
    delete endpoint never ran for it. This reconciles disk against the DB at startup.
    Returns the names of the folders removed.
    """
    datasets_dir = settings.datasets_dir
    if not datasets_dir.exists():
        return []

    result = await db.execute(select(Dataset.folder_path))
    known = {Path(p).resolve() for (p,) in result.all()}

    removed: list[str] = []
    for child in datasets_dir.iterdir():
        if not child.is_dir():
            continue
        if child.resolve() in known:
            continue
        remove_dataset_dir(child)
        removed.append(child.name)

    if removed:
        logger.warning(
            "Removed %d orphan dataset folder(s) with no DB row: %s",
            len(removed), ", ".join(removed),
        )
    return removed


def _capture_provenance(src_file: Path, dest_file: Path) -> dict:
    """Provenance captured from a file being ingested: sidecar JSON, then embedded.

    Sidecar wins over embedded (EXIF / PNG text) per field. The caller layers
    request-supplied values on top of this, and leaves anything still unset NULL
    so it inherits the dataset default (see backend/licenses.py).
    """
    captured = read_provenance_sidecar(src_file) or {}
    embedded = extract_embedded_provenance(str(dest_file)) or {}
    for field, value in embedded.items():
        if not captured.get(field):
            captured[field] = value
    return captured


def _ingest_file_sync(
    src_file: Path,
    dest_file: Path,
    thumb_path: str,
    read_caption: bool,
) -> tuple[dict, dict | None, str | None, dict]:
    """Copy an imported file and derive its metadata/thumbnail/caption/provenance.

    Pure filesystem + CPU work (no DB session) so it can run in a single executor hop,
    keeping the blocking copy/decode/phash/thumbnail off the event loop.
    """
    shutil.copy2(src_file, dest_file)
    info = get_image_info(str(dest_file))
    gen_meta = extract_generation_metadata(str(dest_file))
    generate_thumbnail(str(dest_file), thumb_path)
    caption = read_caption_sidecar(src_file) if read_caption else None
    if caption:
        # Written here rather than by the caller so the import loop pays no extra
        # executor hop per file; the caller still does the ORM assignment (the
        # caption_token_count listener only fires on attribute assignment).
        _write_txt_sidecar(str(dest_file), caption)
    provenance = _capture_provenance(src_file, dest_file)
    return info, gen_meta, caption, provenance


class RegisteredFile(NamedTuple):
    """Result of `_register_file_sync`.

    A NamedTuple rather than a plain tuple so callers use attribute access: this
    return value grew a third field once already and silently broke a caller that
    still unpacked two (the ComfyUI import crash). Adding a fourth must not be
    able to do that again.
    """

    info: dict
    gen_meta: dict | None
    provenance: dict


def _register_file_sync(f: Path, thumb_path: str) -> RegisteredFile:
    """Derive metadata + thumbnail + provenance for a file already on disk (rescan)."""
    info = get_image_info(str(f))
    gen_meta = extract_generation_metadata(str(f))
    generate_thumbnail(str(f), thumb_path)
    return RegisteredFile(info, gen_meta, _capture_provenance(f, f))


def _copy_caption_file_sync(txt_file: Path, image_path: str) -> str:
    """Read a standalone caption file and write it as the image's sidecar.

    Both halves of the round trip in one executor hop; returns the text so the
    caller can do the ORM assignment on the event loop.
    """
    text = txt_file.read_text(encoding="utf-8").strip()
    _write_txt_sidecar(image_path, text)
    return text


def _copy_image_sync(old_path: Path, new_path: Path, old_thumb: Path, new_thumb: Path) -> None:
    """Copy an image (+ sidecar) and its existing thumbnail. Runs in an executor."""
    copy_with_sidecar(old_path, new_path)
    if old_thumb.exists():
        shutil.copy2(old_thumb, new_thumb)


def _copy_snapshot_image_sync(src_file: Path, new_path: Path, new_thumb: Path, caption_text: str) -> None:
    """Copy a snapshot object into a new dataset, writing its sidecar + regenerating a thumbnail."""
    shutil.copy2(src_file, new_path)
    if caption_text:
        new_path.with_suffix(".txt").write_text(caption_text, encoding="utf-8")
    generate_thumbnail(str(new_path), str(new_thumb))


def _name_to_slug(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s]+", "_", slug)
    slug = re.sub(r"_+", "_", slug)
    slug = slug.strip("_-")
    return slug[:80] or "dataset"


def _bucket(val: float, edges: list[float], labels: list[str]) -> str:
    for i, edge in enumerate(edges):
        if val < edge:
            return labels[i]
    return labels[-1]


def _ar_fine_bucket(ratio: float) -> str:
    if ratio <= 0.5:
        return "9:16+"
    if ratio <= 0.67:
        return "2:3"
    if ratio <= 0.85:
        return "3:4"
    if ratio <= 1.15:
        return "1:1"
    if ratio <= 1.4:
        return "4:3"
    if ratio <= 1.6:
        return "3:2"
    if ratio <= 1.95:
        return "16:9"
    return "21:9+"


def _watermark_bucket(val: float) -> str:
    idx = min(int(val * 10), 9)
    lo = idx / 10
    hi = lo + 0.1
    return f"{lo:.1f}–{hi:.1f}"


def _p95(sorted_vals: list[float]) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(len(sorted_vals) * 0.95)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


async def create_dataset(
    db: AsyncSession,
    name: str,
    description: str = "",
    category: str = "",
    provenance: dict | None = None,
) -> Dataset:
    """Create a dataset row + its on-disk folders in one commit.

    `provenance` carries the dataset-level defaults (source_name/source_url/
    license/attribution). They are set before the single commit on purpose: with a
    second commit for them, a failure in between left a committed dataset with
    empty provenance and returned a 500, and the client's retry then 400'd on the
    duplicate name.
    """
    ds_id = str(uuid4())
    slug = _name_to_slug(name)
    folder = settings.datasets_dir / slug
    if folder.exists():
        folder = settings.datasets_dir / f"{slug}_{ds_id[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)
    (folder / "thumbnails").mkdir(exist_ok=True)

    ds = Dataset(
        id=ds_id, name=name, description=description, category=category,
        folder_path=str(folder),
        **{f: v for f, v in (provenance or {}).items() if f in PROVENANCE_FIELDS},
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


async def rename_dataset(
    db: AsyncSession,
    ds: Dataset,
    new_name: str,
    new_description: str | None = None,
) -> Dataset:
    """Rename the dataset, carrying its folder and **every** stored path in it.

    Videos as well as images: `Video.file_path`/`poster_path` used to be left
    behind, and the damage was invisible rather than loud — `_rescan_videos`
    keys `by_filename` on the row's `filename`, and the files in the *new*
    folder carry those same names, so each hits `continue` and `videos_missing`
    comes back empty while every `file_path` names a folder that no longer
    exists. `GET /videos/{id}/file` then 404s on a row nothing reports as broken.

    Both row sets are loaded **before** `old_folder.rename(...)` (PM-013): the
    query is fallible and the rename is not, so running it afterwards meant a
    failed `SELECT` left the folder moved with nothing committed to describe it.

    `.versions` needs nothing — `version_service._object_store_path` derives the
    object store from `dataset.folder_path` at read time, so it travels as soon
    as that column is updated.
    """
    old_folder = Path(ds.folder_path)
    new_slug = _name_to_slug(new_name)
    new_folder = settings.datasets_dir / new_slug

    if old_folder.exists() and old_folder.resolve() != new_folder.resolve():
        if new_folder.exists():
            new_folder = settings.datasets_dir / f"{new_slug}_{ds.id[:8]}"

        images = (
            await db.execute(select(Image).where(Image.dataset_id == ds.id))
        ).scalars().all()
        videos = (
            await db.execute(select(Video).where(Video.dataset_id == ds.id))
        ).scalars().all()

        old_folder.rename(new_folder)

        def _rebase(value: str | None) -> str | None:
            # `/move`'s house shape (routers/filesystem.py) rather than a bare
            # `startswith(old_str)`: one place for every column either model
            # keeps a path in, and no dependence on the query's dataset scoping
            # to keep a prefix from over-matching a sibling folder.
            if not value:
                return value
            p = Path(value)
            if not p.is_relative_to(old_folder):
                return value
            return str(new_folder / p.relative_to(old_folder))

        for img in images:
            img.file_path = _rebase(img.file_path)
            img.thumbnail_path = _rebase(img.thumbnail_path)
        for vid in videos:
            vid.file_path = _rebase(vid.file_path)
            vid.poster_path = _rebase(vid.poster_path)
        ds.folder_path = str(new_folder)

    ds.name = new_name
    if new_description is not None:
        ds.description = new_description
    await db.commit()
    await db.refresh(ds)
    return ds


async def list_subfolders(db: AsyncSession, dataset_id: str) -> list[dict]:
    ds = await db.get(Dataset, dataset_id)
    declared: list[str] = ds.declared_subfolders if ds else []

    result = await db.execute(
        select(Image.subfolder, func.count(Image.id).label("cnt"))
        .where(Image.dataset_id == dataset_id)
        .group_by(Image.subfolder)
        .order_by(Image.subfolder)
    )
    image_rows = {r.subfolder: r.cnt for r in result.all()}

    # Merge: start with image-derived, add any declared paths that have no images yet
    merged: dict[str, int] = dict(image_rows)
    for path in declared:
        if path not in merged:
            merged[path] = 0

    return [{"path": p, "image_count": c} for p, c in sorted(merged.items())]


async def declare_subfolder(db: AsyncSession, dataset_id: str, path: str) -> None:
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        return
    current: list[str] = list(ds.declared_subfolders or [])
    parts = path.split("/")
    changed = False
    for i in range(1, len(parts) + 1):
        ancestor = "/".join(parts[:i])
        if ancestor not in current:
            current.append(ancestor)
            changed = True
    if changed:
        ds.declared_subfolders = current
        await db.commit()


async def delete_subfolder(db: AsyncSession, dataset_id: str, path: str) -> int:
    """Move all images in the subfolder and its children to root, remove all from declared list."""
    escaped = path.replace("%", r"\%").replace("_", r"\_")
    result = await db.execute(
        sa_update(Image)
        .where(Image.dataset_id == dataset_id)
        .where((Image.subfolder == path) | Image.subfolder.like(escaped + "/%", escape="\\"))
        .values(subfolder="")
    )
    moved = result.rowcount

    ds = await db.get(Dataset, dataset_id)
    if ds:
        current = [p for p in (ds.declared_subfolders or [])
                   if p != path and not p.startswith(path + "/")]
        ds.declared_subfolders = current

    await db.commit()
    return moved


def _file_size(path: Path) -> int:
    """st_size, or 0 for a file that vanished between the scan and this stat."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _scan_source_files(
    src: Path, preserve_structure: bool, include_videos: bool = False
) -> tuple[list[Path], list[Path], int]:
    """Importable files under `src`, split by kind, plus their combined size.

    Blocking — call in an executor. One traversal for all three: `is_file()`
    already stats every entry, so summing sizes in the same pass costs nothing
    beyond the stat the filter performs anyway. The combined size is what feeds
    `require_free_space`, so video bytes are covered automatically whenever
    they are being imported.
    """
    it = src.rglob("*") if preserve_structure else src.iterdir()
    images: list[Path] = []
    videos: list[Path] = []
    for f in it:
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            images.append(f)
        elif include_videos and suffix in VIDEO_EXTENSIONS:
            videos.append(f)
    return images, videos, sum(_file_size(f) for f in images + videos)


def _ingest_video_sync(src_file: Path, dest_file: Path, poster_file: Path) -> tuple[dict, str | None]:
    """Copy one video into the dataset, read its header and cut its poster. Blocking.

    `shutil.copy2` rather than `copy_with_sidecar`: a video carries no `.txt`
    caption companion — captions belong to the frames extracted from it.

    A file that fails the probe is removed again. The copy has to happen first
    (probing the destination is what proves the copy itself is readable), so
    without this an undecodable source would leave an orphan in videos/ that no
    DB row points at — and rescan would then try to register it on every run.
    A failed *poster* is not a failed import; it just leaves the path NULL.
    """
    shutil.copy2(src_file, dest_file)
    try:
        return probe_and_poster(dest_file, poster_file)
    except UnreadableVideoError:
        dest_file.unlink(missing_ok=True)
        raise


async def import_images_from_folder(
    db: AsyncSession,
    dataset: Dataset,
    folder_path: str,
    job_id: str | None = None,
    subfolder: str = "",
    preserve_structure: bool = False,
    import_captions: bool = True,
    provenance: dict | None = None,
    include_videos: bool = False,
) -> dict:
    from backend.workers.progress import broadcaster
    from backend.workers.job_queue import job_queue

    src = Path(folder_path)
    if not src.exists() or not src.is_dir():
        raise ValueError(f"Folder not found: {folder_path}")

    # Walking the tree and stat-ing every file is unbounded blocking I/O — slow enough
    # on a large or network-mounted folder to stall SSE progress and every other
    # request. It is also self-contained, so the whole scan goes to a thread at once.
    image_files, video_files, source_bytes = await asyncio.get_running_loop().run_in_executor(
        None, _scan_source_files, src, preserve_structure, include_videos
    )
    total = len(image_files) + len(video_files)

    # Every one of these files is copied into the dataset, plus a thumbnail each.
    # Check before the first copy so a too-small disk fails the job with a readable
    # message rather than leaving a partially imported folder behind. `source_bytes`
    # spans both kinds, so an import that includes videos is preflighted for them.
    require_free_space(dataset.folder_path, source_bytes)
    added = 0
    videos_added = 0
    failed_count = 0
    failed: list[dict] = []

    from backend.utils import slugify_filename

    dest_images = Path(dataset.folder_path) / "images"
    dest_thumbs = Path(dataset.folder_path) / "thumbnails"

    existing_result = await db.execute(select(Image.filename).where(Image.dataset_id == dataset.id))
    db_filenames: set[str] = {r[0] for r in existing_result.all()}

    occupied_thumb_stems: set[str] = {p.stem for p in dest_thumbs.glob("*.webp")} if dest_thumbs.exists() else set()
    planned_thumb_stems: set[str] = set()

    for i, src_file in enumerate(image_files):
        if job_id and job_queue.cancel_requested(job_id):
            break
        try:
            if preserve_structure:
                rel_subfolder = str(src_file.parent.relative_to(src)).replace("\\", "/")
                if rel_subfolder == ".":
                    rel_subfolder = ""
            else:
                rel_subfolder = subfolder

            slug = slugify_filename(src_file.stem) or "image"
            new_name = unique_filename_with_thumb(
                dest_images, slug, src_file.suffix.lower(), db_filenames,
                occupied_thumb_stems, planned_thumb_stems,
            )
            dest_file = dest_images / new_name
            thumb_path = str(dest_thumbs / (dest_file.stem + ".webp"))

            info, gen_meta, caption, captured = await asyncio.get_running_loop().run_in_executor(
                None, _ingest_file_sync, src_file, dest_file, thumb_path, import_captions
            )

            img = Image(
                dataset_id=dataset.id,
                filename=new_name,
                original_filename=src_file.name,
                subfolder=rel_subfolder,
                file_path=str(dest_file),
                thumbnail_path=thumb_path,
                generation_metadata=gen_meta,
                # Request-supplied provenance wins over the sidecar, which wins
                # over EXIF; anything still unset stays NULL and inherits.
                **merge_provenance(provenance, captured),
                **info,
            )
            if caption:
                # The sidecar itself was already written inside _ingest_file_sync.
                img.caption_text = caption
                img.captioned_by = "import"
                img.captioned_at = datetime.utcnow()
            db.add(img)
            added += 1
        except Exception as exc:  # skip broken files, continue import
            failed_count += 1
            if len(failed) < _MAX_FAILED_DETAILS:
                failed.append({"file": src_file.name, "error": str(exc)})
            logger.warning("import: failed for %s", src_file, exc_info=True)

        # Commit periodically so completed work survives a crash/cancel mid-import and the
        # gallery populates live. expire_on_commit=False (see database.py) means `dataset`
        # attributes stay valid across these commits.
        if (i + 1) % 200 == 0:
            await db.commit()

        if job_id and i % 10 == 0:
            pct = round((i + 1) / total * 100, 1)
            await broadcaster.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "job_type": "import",
                "status": "running",
                "done": i + 1,
                "total": total,
                "percent": pct,
                "current_item": src_file.name,
                "message": f"Importing {src_file.name}",
            })

    # Videos, after the images. They land flat in videos/ regardless of
    # preserve_structure — subfolders are an image-side concept, and a video's
    # extracted frames get their own subfolder at extraction time instead.
    if video_files and not (job_id and job_queue.cancel_requested(job_id)):
        # Created lazily, so an image-only dataset never grows an empty videos/.
        dest_videos = Path(dataset.folder_path) / "videos"
        dest_posters = dest_videos / "thumbnails"
        dest_videos.mkdir(parents=True, exist_ok=True)

        existing_videos = await db.execute(
            select(Video.id, Video.filename, Video.poster_path).where(Video.dataset_id == dataset.id)
        )
        existing_video_rows = existing_videos.all()
        video_db_names: set[str] = {r.filename for r in existing_video_rows}
        occupied_poster_stems = claimed_poster_stems(
            [(r.id, r.filename, r.poster_path) for r in existing_video_rows], dest_posters
        )
        planned_poster_stems: set[str] = set()

        for j, src_file in enumerate(video_files):
            if job_id and job_queue.cancel_requested(job_id):
                break
            try:
                slug = slugify_filename(src_file.stem) or "video"
                new_name = unique_filename_with_thumb(
                    dest_videos, slug, src_file.suffix.lower(), video_db_names,
                    occupied_poster_stems, planned_poster_stems,
                )
                dest_file = dest_videos / new_name
                info, poster_path = await asyncio.get_running_loop().run_in_executor(
                    None, _ingest_video_sync, src_file, dest_file, Path(poster_path_for(dest_file))
                )
                db.add(Video(
                    dataset_id=dataset.id,
                    filename=new_name,
                    original_filename=src_file.name,
                    file_path=str(dest_file),
                    poster_path=poster_path,
                    # PROVENANCE_FIELDS, not the Image set: Video has no source_meta.
                    **merge_provenance(provenance, fields=PROVENANCE_FIELDS),
                    **info,
                ))
                videos_added += 1
            except Exception as exc:  # skip broken files, continue import
                failed_count += 1
                if len(failed) < _MAX_FAILED_DETAILS:
                    failed.append({"file": src_file.name, "error": str(exc)})
                logger.warning("import: failed for %s", src_file, exc_info=True)

            if (j + 1) % 200 == 0:
                await db.commit()

            if job_id and j % 10 == 0:
                done = len(image_files) + j + 1
                await broadcaster.emit(job_id, {
                    "type": "progress",
                    "job_id": job_id,
                    "job_type": "import",
                    "status": "running",
                    "done": done,
                    "total": total,
                    "percent": round(done / total * 100, 1),
                    "current_item": src_file.name,
                    "message": f"Importing {src_file.name}",
                })

    await db.commit()
    await refresh_stats(db, dataset.id)
    if job_id:
        job_queue.raise_if_cancelled(job_id)
    return {"added": added, "videos_added": videos_added, "failed_count": failed_count, "failed": failed}


def _fold_video_failures(vids: dict, failed: list[dict], failed_count: int) -> int:
    """Merge `_rescan_videos`' failure tally into the image pass's shared one.

    `_rescan_videos` reports under `videos_failed`/`videos_failed_count` because
    `rescan_dataset` splats its result into a dict that already carries
    `failed`/`failed_count` from the image loop — same names would silently
    discard one pass's tally. The two become one number here, and
    `_MAX_FAILED_DETAILS` stays a cap on the *combined* detail list, so the
    public response shape never grows a video-specific key.
    """
    failed.extend(vids.pop("videos_failed")[: max(0, _MAX_FAILED_DETAILS - len(failed))])
    return failed_count + vids.pop("videos_failed_count")


async def _rescan_videos(db: AsyncSession, dataset: Dataset) -> dict:
    """Reconcile videos/ with the `videos` table.

    Returns {videos_added, videos_missing, videos_failed, videos_failed_count};
    callers fold the last two into their own tally via `_fold_video_failures`.

    Its own pass because the image rescan walks `images_dir.rglob("*")`, which
    cannot see videos/ at all. The walk here is a **flat** glob: videos are never
    nested, and flat conveniently skips the videos/thumbnails/ child directory
    that would otherwise be scanned for video files on every run.

    Callers commit and refresh stats — this only stages rows.
    """
    videos_dir = Path(dataset.folder_path) / "videos"
    if not videos_dir.exists():
        return {"videos_added": 0, "videos_missing": [], "videos_failed": [], "videos_failed_count": 0}

    disk_videos = [f for f in videos_dir.glob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS]

    existing = await db.execute(select(Video).where(Video.dataset_id == dataset.id))
    rows = existing.scalars().all()
    by_filename: dict[str, Video] = {v.filename: v for v in rows}

    # Rescan *adopts* the filenames it finds — it cannot rename a file the user
    # dropped in here — so unlike upload/import/rename it cannot dodge a stem
    # collision by picking a different name. `clip.mp4` and `clip.mkv` are two
    # legitimate videos that would both want `thumbnails/clip.webp`, and the
    # second poster written would clobber the first, leaving two rows pointing at
    # one file. So the *poster* moves instead: the second gets `clip_001.webp`.
    #
    # This is the opposite choice from the image walk below, which does rename
    # the file. The asymmetry is deliberate: eleven sites re-derive an image's
    # thumbnail path from its filename, so an image's thumbnail stem must stay
    # equal to its own; nothing re-derives a poster path — every consumer reads
    # `Video.poster_path`.
    poster_dir = videos_dir / "thumbnails"
    claimed = claimed_poster_stems([(v.id, v.filename, v.poster_path) for v in rows], poster_dir)

    seen: set[str] = set()
    videos_added = 0
    videos_failed_count = 0
    videos_failed: list[dict] = []
    for f in disk_videos:
        seen.add(f.name)
        if f.name in by_filename:
            continue
        poster_target = unique_poster_path(poster_dir, f.stem, claimed)
        claimed.add(poster_target.stem)  # keep the set honest across this run
        try:
            info, poster_path = await asyncio.get_running_loop().run_in_executor(
                None, probe_and_poster, f, poster_target
            )
        except UnreadableVideoError:
            # Not a failure worth reporting: an undecodable file in videos/ is
            # simply not a video we can register. Leave it on disk untouched.
            logger.info("rescan: skipping undecodable video %s", f)
            continue
        except Exception as exc:
            # Everything the probe can raise that *isn't* the ingest gate: an
            # ImportError from cv2's lazy import, a raw cv2.error, a MemoryError
            # on a huge frame. Without this arm one such file aborts the whole
            # rescan — and the image pass's collision renames and thumbnails are
            # already permanent on disk by then. Mirrors the image loop's
            # handler, including its detail cap.
            videos_failed_count += 1
            if len(videos_failed) < _MAX_FAILED_DETAILS:
                videos_failed.append({"file": f.name, "error": str(exc)})
            logger.warning("rescan: video failed for %s", f, exc_info=True)
            continue
        db.add(Video(
            dataset_id=dataset.id,
            filename=f.name,
            original_filename=f.name,
            file_path=str(f),
            poster_path=poster_path,
            **info,
        ))
        videos_added += 1

    videos_missing = [fn for fn in by_filename if fn not in seen]
    return {
        "videos_added": videos_added,
        "videos_missing": videos_missing,
        "videos_failed": videos_failed,
        "videos_failed_count": videos_failed_count,
    }


async def rescan_dataset(
    db: AsyncSession,
    dataset: Dataset,
    job_id: str | None = None,
    import_captions: bool = True,
) -> dict:
    """Reconcile a dataset's DB records with the files on disk under images/ and videos/.

    - Files on disk not in the DB are registered (thumbnail + sidecar caption).
    - DB records whose file is missing on disk are reported (never removed).
    - Existing records pick up changed/added .txt sidecars when import_captions is set.
    Returns {added, videos_added, captions_updated, missing, videos_missing, total_on_disk}.
    """
    from backend.workers.progress import broadcaster
    from backend.workers.job_queue import job_queue

    images_dir = Path(dataset.folder_path) / "images"
    if not images_dir.exists():
        # videos/ can exist without images/ — reconcile it either way.
        vids = await _rescan_videos(db, dataset)
        failed: list[dict] = []
        failed_count = _fold_video_failures(vids, failed, 0)
        # Explicit, rather than relying on `refresh_stats` to commit the staged
        # Video rows on this branch's behalf.
        await db.commit()
        await refresh_stats(db, dataset.id)
        return {
            "added": 0, "renamed": 0, "captions_updated": 0, "missing": [], "total_on_disk": 0,
            "failed_count": failed_count, "failed": failed, **vids,
        }

    disk_files = [
        f for f in images_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ]
    total = len(disk_files)

    # Image files are stored flat in images/ — subfolder is a purely logical DB column,
    # never reflected in the physical path. So key reconciliation on filename alone
    # (unique per dataset via uq_dataset_filename), not on the on-disk directory.
    existing = await db.execute(select(Image).where(Image.dataset_id == dataset.id))
    by_filename: dict[str, Image] = {}
    for img in existing.scalars().all():
        by_filename[img.filename] = img

    # Thumbnails are .webp keyed by stem, so `a.png` and `a.jpg` dropped into
    # images/ by hand both resolve to `thumbnails/a.webp` — the second written
    # clobbers the first and two rows end up sharing one picture. Every other
    # creation path in the app avoids this by picking a free name through
    # `unique_filename_with_thumb`; rescan is one of only two that adopt a name
    # off disk instead, so it has to disambiguate here.
    #
    # It resolves the clash by renaming the *file*, where video rescan renames
    # the *poster*. The asymmetry is deliberate: eleven sites re-derive an
    # image's thumbnail path from its filename (rename, move, copy, crop,
    # extension change, captioning's rename-on-caption, duplicate_dataset and
    # four versioning paths), so an image whose thumbnail stem drifted from its
    # own would have that thumbnail silently orphaned by the next such
    # operation. Nothing re-derives a poster path — every consumer reads
    # `Video.poster_path`.
    #
    # Two terms, for the same reason `claimed_poster_stems` carries three (read
    # its docstring — it is the model for this):
    #
    # - **registered rows' filename stems** — the conservative reservation for a
    #   thumbnail that is not on disk. Delete `{ds}/thumbnails/` from the file
    #   browser and the Image rows survive (their `file_path` is not under that
    #   prefix), so a glob alone sees nothing occupied and hands a hand-dropped
    #   `a.jpg` the very path registered `a.png` will regenerate into. PM-007,
    #   silently.
    # - **on-disk `*.webp`** — covers a thumbnail whose row is gone, and the
    #   stem drift a row's own filename cannot express.
    thumbs_dir = Path(dataset.folder_path) / "thumbnails"
    occupied_thumb_stems: set[str] = {Path(fn).stem for fn in by_filename}
    if thumbs_dir.exists():
        occupied_thumb_stems |= {p.stem for p in thumbs_dir.glob("*.webp")}
    planned_thumb_stems: set[str] = set()
    db_names: set[str] = set(by_filename)

    seen_filenames: set[str] = set()
    added = 0
    renamed = 0
    captions_updated = 0
    failed_count = 0
    failed: list[dict] = []

    cancelled = False
    for i, f in enumerate(disk_files):
        if job_id and job_queue.cancel_requested(job_id):
            cancelled = True
            break
        try:
            # Before the collision rename below rebinds `f`. This is the name the
            # user gave the file, and `original_filename` is what
            # `import_captions_from_folder` matches a later `a.txt` against — so
            # recording the *new* name would lose both the record and the pairing.
            original_name = f.name
            seen_filenames.add(f.name)
            caption = (
                await asyncio.get_running_loop().run_in_executor(None, read_caption_sidecar, f)
                if import_captions else None
            )

            existing_img = by_filename.get(f.name)
            if existing_img is None:
                # Flat images/ only. A nested file carries two separate defects
                # that predate this guard — the reconcile key above is the bare
                # filename, so images/sub/a.png is treated as already-existing
                # whenever images/a.png is registered, and thumbnail_path_for
                # resolves it to images/thumbnails/ rather than the dataset's
                # own, with subfolder="" hardcoded below. Disambiguating against
                # the wrong thumbnail directory would not help, so nested files
                # keep behaving exactly as they did.
                if f.parent == images_dir:
                    # disk_exclude is what keeps this from renaming *every* file
                    # it finds: unlike an import, the file is already sitting in
                    # images/, so without it `unique_filename` sees its own name
                    # occupied and steps straight to `_001`. Its own stem is
                    # likewise absent from occupied_thumb_stems: nothing has
                    # generated its thumbnail yet, and this arm only runs when
                    # the file has no row, so the set's row term cannot hold it
                    # either.
                    new_name = unique_filename_with_thumb(
                        images_dir, f.stem, f.suffix.lower(),
                        db_names, occupied_thumb_stems, planned_thumb_stems,
                        disk_exclude={f.name},
                    )
                    if new_name != f.name:
                        # The file only, not its .txt sidecar — the deliberate
                        # exception to the rename_with_sidecar rule. The two
                        # files share a stem, so a single `a.txt` belongs to
                        # both equally and moving it would take it away from the
                        # image that kept the name. `caption` above was read
                        # before this, so nothing is lost either way.
                        f = f.rename(images_dir / new_name)
                        seen_filenames.add(f.name)
                        renamed += 1
                thumb_path = thumbnail_path_for(f)
                reg = await asyncio.get_running_loop().run_in_executor(
                    None, _register_file_sync, f, thumb_path
                )
                img = Image(
                    dataset_id=dataset.id,
                    filename=f.name,
                    original_filename=original_name,
                    subfolder="",
                    file_path=str(f),
                    thumbnail_path=thumb_path,
                    generation_metadata=reg.gen_meta,
                    **merge_provenance(reg.provenance),
                    **reg.info,
                )
                if caption:
                    img.caption_text = caption
                    img.captioned_by = "import"
                    img.captioned_at = datetime.utcnow()
                db.add(img)
                added += 1
            elif caption is not None and caption != (existing_img.caption_text or "").strip():
                existing_img.caption_text = caption
                existing_img.captioned_by = "import"
                existing_img.captioned_at = datetime.utcnow()
                captions_updated += 1
        except Exception as exc:  # skip broken files, continue rescan
            failed_count += 1
            if len(failed) < _MAX_FAILED_DETAILS:
                failed.append({"file": f.name, "error": str(exc)})
            logger.warning("rescan: failed for %s", f, exc_info=True)

        if job_id and i % 10 == 0:
            pct = round((i + 1) / total * 100, 1) if total else 100.0
            await broadcaster.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "job_type": "rescan",
                "status": "running",
                "done": i + 1,
                "total": total,
                "percent": pct,
                "current_item": f.name,
                "message": f"Scanning {f.name}",
            })

    # When cancelled mid-scan, unscanned files would falsely appear "missing" — suppress
    # the report in that case (partial add/caption results are still committed).
    missing = [] if cancelled else [
        {"subfolder": img.subfolder or "", "filename": fn}
        for fn, img in by_filename.items()
        if fn not in seen_filenames
    ]

    # Commit the image pass *before* the video pass runs. Everything the image
    # loop did to the filesystem — collision renames, thumbnails — is already
    # permanent, so a video-pass failure that reached the caller would discard
    # only the rows describing it, leaving renamed files with nothing pointing at
    # them. The per-file guard in `_rescan_videos` is not enough on its own: its
    # pre-loop `select(Video)` and `videos_dir.glob` can still raise. Safe with
    # `AsyncSessionLocal`'s `expire_on_commit=False` — the `by_filename` Image
    # instances stay usable below, and the trailing commit still lands the Video
    # rows.
    await db.commit()

    # Videos are reported under their own keys rather than folded into `missing`,
    # whose entries are image-shaped ({subfolder, filename}).
    vids = (
        {"videos_added": 0, "videos_missing": [], "videos_failed": [], "videos_failed_count": 0}
        if cancelled else await _rescan_videos(db, dataset)
    )
    failed_count = _fold_video_failures(vids, failed, failed_count)

    await db.commit()
    await refresh_stats(db, dataset.id)
    if cancelled:
        job_queue.raise_if_cancelled(job_id)
    return {
        "added": added,
        # Files rescan had to rename because another image already owned their
        # stem. Reported rather than silent: rescan otherwise never touches a
        # file, so a name changing under the user needs to say so.
        "renamed": renamed,
        "captions_updated": captions_updated,
        "missing": missing,
        "total_on_disk": total,
        "failed_count": failed_count,
        "failed": failed,
        **vids,
    }


async def import_captions_from_folder(
    db: AsyncSession,
    dataset: Dataset,
    folder_path: str,
    job_id: str | None = None,
) -> dict:
    """Match .txt files in folder_path to existing dataset images by filename stem and apply them.

    Matches each .txt stem against image original_filename stem first, then filename stem.
    Existing captions are overwritten. Returns {matched, unmatched}.
    """
    from backend.workers.progress import broadcaster
    from backend.workers.job_queue import job_queue

    src = Path(folder_path)
    if not src.exists() or not src.is_dir():
        raise ValueError(f"Folder not found: {folder_path}")

    txt_files = [f for f in src.rglob("*.txt") if f.is_file()]
    total = len(txt_files)

    rows = await db.execute(select(Image).where(Image.dataset_id == dataset.id))
    by_original: dict[str, Image] = {}
    by_filename: dict[str, Image] = {}
    for img in rows.scalars().all():
        by_original.setdefault(Path(img.original_filename or "").stem, img)
        by_filename.setdefault(Path(img.filename).stem, img)

    matched = 0
    unmatched: list[str] = []

    cancelled = False
    for i, txt in enumerate(txt_files):
        if job_id and job_queue.cancel_requested(job_id):
            cancelled = True
            break
        try:
            stem = txt.stem
            img = by_original.get(stem) or by_filename.get(stem)
            if img is None:
                unmatched.append(txt.name)
            else:
                # Read the source .txt and write the dataset-side sidecar in one
                # executor hop; the ORM assignment stays on the event loop so the
                # caption_token_count listener still fires.
                text = await asyncio.get_running_loop().run_in_executor(
                    None, _copy_caption_file_sync, txt, img.file_path
                )
                img.caption_text = text
                img.captioned_by = "import"
                img.captioned_at = datetime.utcnow()
                matched += 1
        except Exception:
            unmatched.append(txt.name)

        if job_id and i % 10 == 0:
            pct = round((i + 1) / total * 100, 1) if total else 100.0
            await broadcaster.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "job_type": "import_captions",
                "status": "running",
                "done": i + 1,
                "total": total,
                "percent": pct,
                "current_item": txt.name,
                "message": f"Importing caption {txt.name}",
            })

    await db.commit()
    await refresh_stats(db, dataset.id)
    if cancelled:
        job_queue.raise_if_cancelled(job_id)
    return {"matched": matched, "unmatched": unmatched}


async def refresh_stats(db: AsyncSession, dataset_id: str) -> None:
    result = await db.execute(
        select(
            func.count(Image.id),
            func.sum(Image.file_size_bytes),
        ).where(Image.dataset_id == dataset_id)
    )
    row = result.one()
    image_count = row[0] or 0
    total_size = row[1] or 0

    captioned = await db.execute(
        select(func.count(Image.id)).where(
            Image.dataset_id == dataset_id,
            Image.caption_text != "",
        )
    )
    captioned_count = captioned.scalar() or 0

    # Videos are counted into their own columns, never folded into image_count
    # or total_size_bytes: a video is ~100x the size of the frames it yields, so
    # folding it in would make every dataset card read as bloated, and
    # image_count is what a user compares against an export manifest. Extracted
    # frames need no special-casing — they arrive as ordinary Image rows above.
    vid = await db.execute(
        select(func.count(Video.id), func.sum(Video.file_size_bytes)).where(
            Video.dataset_id == dataset_id
        )
    )
    vid_row = vid.one()

    ds = await db.get(Dataset, dataset_id)
    if ds:
        ds.image_count = image_count
        ds.captioned_count = captioned_count
        ds.total_size_bytes = total_size
        ds.video_count = vid_row[0] or 0
        ds.video_size_bytes = vid_row[1] or 0
        ds.updated_at = datetime.utcnow()
        await db.commit()


def _aggregate_dataset_stats(rows, ds, subfolder, score_cov, flag_counts) -> dict:
    """Pure-Python bucketing/aggregation for get_dataset_stats.

    Runs in a thread executor so the whole per-row loop doesn't block the event loop.
    All DB work (row fetch, coverage/flag/embedding counts) happens in the async caller;
    this function only touches the already-materialized `rows`. Token counts come from the
    persisted Image.caption_token_count column — no tokenization here.
    """
    # Bucket edge/label definitions
    blur_edges =       [20, 40, 80, 150, 300]
    blur_labels =      ["0–20", "20–40", "40–80", "80–150", "150–300", "300+"]
    noise_edges =      [5, 10, 15, 20, 30]
    noise_labels =     ["0–5", "5–10", "10–15", "15–20", "20–30", "30+"]
    uni_edges =        [5, 10, 20, 40]
    uni_labels =       ["0–5", "5–10", "10–20", "20–40", "40+"]
    color_edges =      [10, 20, 40, 60]
    color_labels =     ["0–10", "10–20", "20–40", "40–60", "60+"]
    sat_edges =        [10, 20, 40, 60]
    sat_labels =       ["0–10", "10–20", "20–40", "40–60", "60+"]
    # Brightness is the 0–1 mean grayscale, so its edges are fractions. They must
    # stay numerically identical to DEFAULT_EDGES.luminance on the frontend, which
    # rebuckets client-side from the raw score-values array once a user edits them.
    lum_edges =        [0.15, 0.3, 0.5, 0.7]
    lum_labels =       ["<0.15", "0.15–0.3", "0.3–0.5", "0.5–0.7", "0.7+"]
    mp_edges =         [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
    mp_labels =        ["<0.25", "0.25–0.5", "0.5–1", "1–2", "2–4", "4–8", "8+"]
    fs_edges =         [0.1, 0.5, 1.0, 2.0, 5.0]
    fs_labels =        ["<0.1 MB", "0.1–0.5 MB", "0.5–1 MB", "1–2 MB", "2–5 MB", "5+ MB"]
    wc_edges =         [1, 6, 11, 21, 51]
    wc_labels =        ["No caption", "1–5 words", "6–10 words", "11–20 words", "21–50 words", "50+ words"]
    tc_edges =         [1, 20, 40, 60, 77]
    tc_labels =        ["No caption", "1–19", "20–39", "40–59", "60–76", "77+"]

    widths: list[int] = []
    heights: list[int] = []
    file_sizes_mb: list[float] = []

    formats: dict[str, int] = {}
    ar_coarse: dict[str, int] = {"portrait": 0, "landscape": 0, "square": 0}
    ar_fine: dict[str, int] = {}
    score_buckets = {"low (0-4)": 0, "mid (4-6)": 0, "high (6-10)": 0, "unscored": 0}
    blur_dist: dict[str, int] = {}
    noise_dist: dict[str, int] = {}
    uni_dist: dict[str, int] = {}
    wm_dist: dict[str, int] = {}
    color_dist: dict[str, int] = {}
    sat_dist: dict[str, int] = {}
    lum_dist: dict[str, int] = {}
    mp_dist: dict[str, int] = {}
    fs_dist: dict[str, int] = {}
    wc_dist: dict[str, int] = {}
    tc_dist: dict[str, int] = {}

    ssim_dist: dict[str, int] = {}

    captioned = 0

    for r in rows:
        # Formats
        fmt = (r.format or "unknown").upper()
        formats[fmt] = formats.get(fmt, 0) + 1

        # Dimensions
        if r.width and r.height:
            widths.append(r.width)
            heights.append(r.height)
            ar = r.width / r.height
            # Coarse AR
            if ar < 0.8:
                ar_coarse["portrait"] += 1
            elif ar > 1.2:
                ar_coarse["landscape"] += 1
            else:
                ar_coarse["square"] += 1
            # Fine AR
            b = _ar_fine_bucket(ar)
            ar_fine[b] = ar_fine.get(b, 0) + 1
            # Megapixels
            mp = (r.width * r.height) / 1_000_000
            b = _bucket(mp, mp_edges, mp_labels)
            mp_dist[b] = mp_dist.get(b, 0) + 1

        # File size
        if r.file_size_bytes:
            mb = r.file_size_bytes / 1_048_576
            file_sizes_mb.append(mb)
            b = _bucket(mb, fs_edges, fs_labels)
            fs_dist[b] = fs_dist.get(b, 0) + 1

        # Aesthetic
        if r.aesthetic_score is None:
            score_buckets["unscored"] += 1
        else:
            if r.aesthetic_score < 4:
                score_buckets["low (0-4)"] += 1
            elif r.aesthetic_score < 6:
                score_buckets["mid (4-6)"] += 1
            else:
                score_buckets["high (6-10)"] += 1

        # Technical scores
        if r.blur_score is not None:
            b = _bucket(r.blur_score, blur_edges, blur_labels)
            blur_dist[b] = blur_dist.get(b, 0) + 1
        if r.noise_score is not None:
            b = _bucket(r.noise_score, noise_edges, noise_labels)
            noise_dist[b] = noise_dist.get(b, 0) + 1
        if r.uniformity_score is not None:
            b = _bucket(r.uniformity_score, uni_edges, uni_labels)
            uni_dist[b] = uni_dist.get(b, 0) + 1
        if r.color_score is not None:
            b = _bucket(r.color_score, color_edges, color_labels)
            color_dist[b] = color_dist.get(b, 0) + 1
        if r.saturation_score is not None:
            b = _bucket(r.saturation_score, sat_edges, sat_labels)
            sat_dist[b] = sat_dist.get(b, 0) + 1
        if r.luminance_score is not None:
            b = _bucket(r.luminance_score, lum_edges, lum_labels)
            lum_dist[b] = lum_dist.get(b, 0) + 1

        # Watermark
        if r.watermark_score is not None:
            b = _watermark_bucket(r.watermark_score)
            wm_dist[b] = wm_dist.get(b, 0) + 1

        # Style similarity
        if r.style_similarity_score is not None:
            b = _watermark_bucket(r.style_similarity_score)
            ssim_dist[b] = ssim_dist.get(b, 0) + 1

        trimmed = (r.caption_text or "").strip()
        if trimmed:
            captioned += 1
            wc = len(trimmed.split())
        else:
            wc = 0
        tc = r.caption_token_count or 0
        b = _bucket(wc, wc_edges, wc_labels)
        wc_dist[b] = wc_dist.get(b, 0) + 1
        b = _bucket(tc, tc_edges, tc_labels)
        tc_dist[b] = tc_dist.get(b, 0) + 1

    total = len(rows)
    coverage = round(captioned / total * 100, 1) if total else 0.0

    # File size summary
    fs_sorted = sorted(file_sizes_mb)
    fs_summary: dict[str, float] = {}
    if fs_sorted:
        fs_summary = {
            "min_mb": round(fs_sorted[0], 3),
            "median_mb": round(statistics.median(fs_sorted), 3),
            "p95_mb": round(_p95(fs_sorted), 3),
            "max_mb": round(fs_sorted[-1], 3),
        }

    # Sort ordered distributions to preserve bucket order in JSON
    def _ordered(dist: dict[str, int], labels: list[str]) -> dict[str, int]:
        return {lbl: dist[lbl] for lbl in labels if lbl in dist}

    ar_fine_order = ["9:16+", "2:3", "3:4", "1:1", "4:3", "3:2", "16:9", "21:9+"]

    return {
        "id": ds.id,
        "name": ds.name,
        "image_count": total,
        "captioned_count": captioned,
        "caption_coverage_pct": coverage,
        "total_size_bytes": sum(r.file_size_bytes or 0 for r in rows) if subfolder is not None else ds.total_size_bytes,
        "total_size_mb": round(sum(file_sizes_mb), 2) if subfolder is not None else round(ds.total_size_bytes / 1_048_576, 2),
        "avg_width": round(sum(widths) / len(widths), 1) if widths else None,
        "avg_height": round(sum(heights) / len(heights), 1) if heights else None,
        "aspect_ratio_distribution": {k: v for k, v in ar_coarse.items() if v},
        "format_distribution": formats,
        "score_distribution": score_buckets,
        "blur_distribution": _ordered(blur_dist, blur_labels),
        "noise_distribution": _ordered(noise_dist, noise_labels),
        "uniformity_distribution": _ordered(uni_dist, uni_labels),
        "watermark_distribution": dict(sorted(wm_dist.items())),
        "color_distribution": _ordered(color_dist, color_labels),
        "saturation_distribution": _ordered(sat_dist, sat_labels),
        "luminance_distribution": _ordered(lum_dist, lum_labels),
        "megapixel_distribution": _ordered(mp_dist, mp_labels),
        "file_size_distribution": _ordered(fs_dist, fs_labels),
        "file_size_summary": fs_summary,
        "aspect_ratio_fine": _ordered(ar_fine, ar_fine_order),
        "caption_length_distribution": _ordered(wc_dist, wc_labels),
        "caption_token_distribution": _ordered(tc_dist, tc_labels),
        "style_similarity_distribution": dict(sorted(ssim_dist.items())),
        "quality_flag_counts": flag_counts,
        "score_coverage": score_cov,
    }


async def get_dataset_stats(db: AsyncSession, dataset_id: str, subfolder: str | None = None) -> dict:
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        return {}

    cache_key = (dataset_id, subfolder, "stats")
    validator = (*await _image_validator(db, dataset_id, subfolder), ds.updated_at)
    cached = _stats_cache_get(cache_key, validator)
    if cached is not None:
        return cached

    q = select(
        Image.width, Image.height, Image.format,
        Image.aesthetic_score, Image.caption_text, Image.caption_token_count,
        Image.blur_score, Image.noise_score, Image.uniformity_score,
        Image.watermark_score, Image.color_score, Image.saturation_score,
        Image.luminance_score,
        Image.file_size_bytes,
        Image.style_similarity_score,
    ).where(Image.dataset_id == dataset_id)
    if subfolder is not None:
        q = q.where(Image.subfolder == subfolder)
    result = await db.execute(q)
    rows = result.all()

    # Score coverage: count rows where each score column is non-null.
    _base_where = [Image.dataset_id == dataset_id]
    if subfolder is not None:
        _base_where.append(Image.subfolder == subfolder)
    cov_row = (await db.execute(
        select(
            func.count(Image.aesthetic_score).label("aesthetic"),
            func.count(Image.blur_score).label("technical"),
            func.count(Image.watermark_score).label("watermark"),
        ).where(*_base_where)
    )).one()
    score_cov = {
        "aesthetic": cov_row.aesthetic,
        "technical": cov_row.technical,
        "watermark": cov_row.watermark,
    }

    # Quality flag counts via SQL json_extract — avoids loading quality_flags into Python.
    def _flag_sum(json_key: str):
        return func.coalesce(
            func.sum(case((func.json_extract(Image.quality_flags, f"$.{json_key}") == 1, 1), else_=0)), 0
        )
    flag_row = (await db.execute(
        select(
            _flag_sum("is_blurry").label("blurry"),
            _flag_sum("is_noisy").label("noisy"),
            _flag_sum("is_uniform").label("uniform"),
            _flag_sum("has_watermark").label("watermarked"),
            _flag_sum("is_duplicate").label("duplicate"),
            _flag_sum("is_nsfw").label("nsfw"),
            _flag_sum("has_ai_artifacts").label("ai_artifacts"),
        ).where(*_base_where)
    )).one()
    flag_counts = {
        "blurry": flag_row.blurry,
        "noisy": flag_row.noisy,
        "uniform": flag_row.uniform,
        "watermarked": flag_row.watermarked,
        "duplicate": flag_row.duplicate,
        "nsfw": flag_row.nsfw,
        "ai_artifacts": flag_row.ai_artifacts,
    }

    # Embedding coverage — separate count query to avoid loading blobs (async, done before
    # the CPU-bound aggregation below so that aggregation can run entirely off the event loop).
    embed_q = select(func.count(Image.id)).where(
        Image.dataset_id == dataset_id,
        Image.clip_embedding.isnot(None),
    )
    if subfolder is not None:
        embed_q = embed_q.where(Image.subfolder == subfolder)
    embed_count = await db.scalar(embed_q)
    score_cov["embeddings"] = embed_count or 0

    # License breakdown over the *effective* license (image value coalesced over
    # the dataset default). Aggregated in SQL alongside flag_counts rather than
    # in _aggregate_dataset_stats — no reason to pull the rows into Python.
    #
    # Bounded, like get_tag_cooccurrence: the curated vocabulary is a dozen ids,
    # but `other:<free text>` is unbounded and comes from scrapers, so a dataset
    # of scraped images can produce one bucket per image — an unbounded response
    # body and an unbounded list in the Stats panel. Everything past the cap
    # collapses into one bucket so the counts still sum to the dataset total.
    effective_license = func.coalesce(func.nullif(Image.license, ""), ds.license or "", "")
    license_rows = (await db.execute(
        select(effective_license.label("lic"), func.count(Image.id).label("n"))
        .where(*_base_where)
        .group_by(effective_license)
        .order_by(func.count(Image.id).desc())
    )).all()
    license_breakdown = {(r.lic or ""): r.n for r in license_rows[:LICENSE_BREAKDOWN_LIMIT]}
    rest = license_rows[LICENSE_BREAKDOWN_LIMIT:]
    if rest:
        license_breakdown[LICENSE_BREAKDOWN_OTHER_KEY] = sum(r.n for r in rest)

    stats = await asyncio.get_running_loop().run_in_executor(
        None, _aggregate_dataset_stats, rows, ds, subfolder, score_cov, flag_counts
    )
    stats["license_breakdown"] = license_breakdown
    _stats_cache_put(cache_key, validator, stats)
    return stats


async def get_licenses_in_use(db: AsyncSession, dataset_id: str) -> list[dict]:
    """Distinct *effective* licenses recorded in one dataset, most-used first.

    Exists so an `other:<free text>` license can be *picked* rather than retyped.
    The curated vocabulary is compiled into the frontend, but a free-text license
    is data — the only way a dropdown can offer one is to ask which ones exist.
    Feeds the gallery license filter, the export filter and every license editor
    scoped to a single dataset.

    The dataset's own default is always included — exempt from the cap below, and
    carrying its real count whether or not any image resolves to it. Otherwise the
    license you just typed into the dataset defaults is absent from every picker
    until some image happens to carry it, which is the gap this endpoint closes.

    Bounded like the stats breakdown, and for the same reason — `other:` is
    unbounded and comes from scrapers, so a scrape folder can produce one value
    per image. Unlike the breakdown the tail is *dropped*, not collapsed into a
    synthetic bucket: these counts are never summed to a dataset total, and a
    collapsed bucket is not a selectable license.

    Note the cap is applied in Python, not as SQL `LIMIT` — same as the breakdown.
    Capping in SQL hides the tail from the default's own lookup too, so a default
    that ranks past the cap gets re-added advertising a count of 0 while real
    images carry it.
    """
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        return []

    effective_license = func.coalesce(func.nullif(Image.license, ""), ds.license or "", "")
    rows = (await db.execute(
        select(effective_license.label("lic"), func.count(Image.id).label("n"))
        .where(Image.dataset_id == dataset_id)
        .group_by(effective_license)
        .order_by(func.count(Image.id).desc())
    )).all()

    by_license = {(r.lic or ""): r.n for r in rows}
    out = [{"license": r.lic or "", "count": r.n} for r in rows[:LICENSES_IN_USE_LIMIT]]
    if ds.license and not any(e["license"] == ds.license for e in out):
        out.append({"license": ds.license, "count": by_license.get(ds.license, 0)})
    return out


async def get_score_values(db: AsyncSession, dataset_id: str, subfolder: str | None = None) -> dict:
    # No dataset-level input here (unlike get_dataset_stats' license breakdown),
    # so the image validator alone keys the cache.
    cache_key = (dataset_id, subfolder, "scores")
    validator = await _image_validator(db, dataset_id, subfolder)
    cached = _stats_cache_get(cache_key, validator)
    if cached is not None:
        return cached

    q = select(
        Image.aesthetic_score,
        Image.blur_score,
        Image.noise_score,
        Image.uniformity_score,
        Image.watermark_score,
        Image.color_score,
        Image.saturation_score,
        Image.luminance_score,
        Image.style_similarity_score,
        Image.width,
        Image.height,
        Image.file_size_bytes,
        Image.caption_text,
        Image.caption_token_count,
    ).where(Image.dataset_id == dataset_id)
    if subfolder is not None:
        q = q.where(Image.subfolder == subfolder)
    result = await db.execute(q)
    rows = result.all()

    # Pure-Python aggregation runs off the event loop; token counts come from the
    # persisted column (no tokenization).
    def _collect() -> dict:
        score_fields = [
            "aesthetic_score", "blur_score", "noise_score", "uniformity_score",
            "watermark_score", "color_score", "saturation_score", "luminance_score",
            "style_similarity_score",
        ]
        out: dict[str, list[float]] = {f: [] for f in score_fields}
        out["megapixels"] = []
        out["file_size_mb"] = []
        out["caption_words"] = []
        out["caption_tokens"] = []

        for row in rows:
            for field in score_fields:
                val = getattr(row, field)
                if val is not None:
                    out[field].append(float(val))
            if row.width and row.height:
                out["megapixels"].append(row.width * row.height / 1_000_000)
            if row.file_size_bytes:
                out["file_size_mb"].append(row.file_size_bytes / 1_048_576)
            trimmed = (row.caption_text or "").strip()
            out["caption_words"].append(len(trimmed.split()) if trimmed else 0)
            out["caption_tokens"].append(row.caption_token_count or 0)
        return out

    values = await asyncio.get_running_loop().run_in_executor(None, _collect)
    _stats_cache_put(cache_key, validator, values)
    return values


async def duplicate_dataset(
    db: AsyncSession,
    source_dataset: Dataset,
    new_name: str,
    job_id: str,
    source_version_id: str | None = None,
) -> str:
    """Deep-clone a dataset into a new one.  Returns the new dataset's id."""
    from backend.workers.progress import broadcaster
    from backend.workers.job_queue import job_queue

    log = logger
    cancelled = False
    loop = asyncio.get_running_loop()

    # --- Step 1: create fresh destination dataset ---
    new_ds = await create_dataset(db, new_name, source_dataset.description, source_dataset.category)
    new_ds.declared_subfolders = list(source_dataset.declared_subfolders or [])
    # Carry the provenance defaults across so images copied with raw (still
    # inherited) values resolve to the same license they had in the source.
    for _field in PROVENANCE_FIELDS:
        setattr(new_ds, _field, getattr(source_dataset, _field) or "")
    await db.flush()

    dest_images = Path(new_ds.folder_path) / "images"
    dest_thumbs = Path(new_ds.folder_path) / "thumbnails"

    # --- Step 2A: copy from current on-disk state ---
    if source_version_id is None:
        cols = (
            Image.id, Image.filename, Image.file_path, Image.thumbnail_path,
            Image.original_filename, Image.subfolder, Image.is_auto_named,
            Image.width, Image.height, Image.file_size_bytes, Image.format,
            Image.phash, Image.caption_text, Image.caption_style, Image.captioned_by, Image.captioned_at,
            Image.quality_flags, Image.aesthetic_score, Image.blur_score,
            Image.noise_score, Image.uniformity_score, Image.watermark_score, Image.color_score,
            Image.saturation_score, Image.luminance_score, Image.style_similarity_score, Image.dino_layer_scores,
            Image.generation_metadata, Image.processing_history, Image.sort_order,
            Image.source_name, Image.source_url, Image.license, Image.attribution,
            Image.source_meta,
            Image.source_timestamp_ms, Image.source_shot_index,
        )
        result = await db.execute(select(*cols).where(Image.dataset_id == source_dataset.id))
        rows = result.all()
        total = len(rows)

        # Build copy plan (path mappings)
        plan: list = []
        for row in rows:
            old_path = Path(row.file_path)
            new_path = dest_images / row.filename  # fresh folder — no collision
            old_thumb = Path(thumbnail_path_for(str(old_path)))
            new_thumb = dest_thumbs / old_thumb.name
            plan.append((old_path, new_path, old_thumb, new_thumb, row))

        # File copies + DB inserts — add() only after successful copy so no ghost records
        for i, (old_path, new_path, old_thumb, new_thumb, row) in enumerate(plan):
            if job_queue.cancel_requested(job_id):
                cancelled = True
                break
            try:
                await loop.run_in_executor(
                    None, _copy_image_sync, old_path, new_path, old_thumb, new_thumb
                )
                db.add(Image(
                    id=str(uuid4()),
                    dataset_id=new_ds.id,
                    filename=row.filename,
                    original_filename=row.original_filename,
                    subfolder=row.subfolder,
                    file_path=str(new_path),
                    thumbnail_path=str(new_thumb),
                    is_auto_named=row.is_auto_named,
                    width=row.width,
                    height=row.height,
                    file_size_bytes=row.file_size_bytes,
                    format=row.format,
                    phash=row.phash,
                    caption_text=row.caption_text,
                    caption_style=row.caption_style,
                    captioned_by=row.captioned_by,
                    captioned_at=row.captioned_at,
                    quality_flags=row.quality_flags,
                    aesthetic_score=row.aesthetic_score,
                    blur_score=row.blur_score,
                    noise_score=row.noise_score,
                    uniformity_score=row.uniformity_score,
                    watermark_score=row.watermark_score,
                    color_score=row.color_score,
                    saturation_score=row.saturation_score,
                    luminance_score=row.luminance_score,
                    style_similarity_score=row.style_similarity_score,
                    dino_layer_scores=row.dino_layer_scores,
                    generation_metadata=row.generation_metadata,
                    processing_history=row.processing_history,
                    sort_order=row.sort_order,
                    # Frame lineage: the timestamp and shot index travel, but
                    # source_video_id does not — duplicate_dataset copies only
                    # Image rows, so the new dataset has no videos and the id
                    # would point across a dataset boundary at a source the
                    # duplicate does not contain. Same rule as a cross-dataset
                    # copy. Not part of copy_provenance, which is exactly the
                    # five provenance keys.
                    source_video_id=None,
                    source_timestamp_ms=row.source_timestamp_ms,
                    source_shot_index=row.source_shot_index,
                    # Raw, not resolved: the new dataset carries the same
                    # provenance defaults, so inheritance stays equivalent.
                    **copy_provenance(row),
                ))
            except Exception as exc:
                log.warning("duplicate_dataset: failed to copy %s: %s", old_path, exc)

            if i % 10 == 0:
                pct = round((i + 1) / total * 100, 1) if total else 100.0
                await broadcaster.emit(job_id, {
                    "type": "progress",
                    "job_id": job_id,
                    "job_type": "duplicate",
                    "dataset_id": source_dataset.id,
                    "status": "running",
                    "done": i + 1,
                    "total": total,
                    "percent": pct,
                    "message": f"Copying {row.filename}",
                })

    # --- Step 2B: copy from snapshot ---
    else:
        from backend.models.versioning import VersionImageState

        result = await db.execute(
            select(VersionImageState).where(
                VersionImageState.version_id == source_version_id,
                VersionImageState.is_present.is_(True),
            )
        )
        states = result.scalars().all()
        total = len(states)
        skipped = 0

        for i, state in enumerate(states):
            if job_queue.cancel_requested(job_id):
                cancelled = True
                break
            # Resolve source file: object store or current on-disk path
            if state.file_hash:
                src_file = (
                    Path(source_dataset.folder_path)
                    / ".versions" / "objects"
                    / state.file_hash[:2]
                    / state.file_hash[2:]
                )
            else:
                src_file = Path(state.file_path)

            if not src_file.exists():
                log.warning("duplicate_dataset: source file missing for %s, skipping", state.filename)
                skipped += 1
                continue

            new_path = dest_images / state.filename
            new_thumb = dest_thumbs / (Path(state.filename).stem + ".webp")

            try:
                await loop.run_in_executor(
                    None, _copy_snapshot_image_sync, src_file, new_path, new_thumb, state.caption_text or "",
                )

                db.add(Image(
                    id=str(uuid4()),
                    dataset_id=new_ds.id,
                    filename=state.filename,
                    original_filename=state.original_filename,
                    subfolder=state.subfolder,
                    file_path=str(new_path),
                    thumbnail_path=str(new_thumb),
                    caption_text=state.caption_text,
                    quality_flags=state.quality_flags or {},
                    width=state.width,
                    height=state.height,
                    file_size_bytes=state.file_size_bytes,
                    format=state.format,
                    aesthetic_score=state.aesthetic_score,
                    blur_score=state.blur_score,
                    noise_score=state.noise_score,
                    uniformity_score=state.uniformity_score,
                    watermark_score=state.watermark_score,
                    color_score=state.color_score,
                    style_similarity_score=state.style_similarity_score,
                    dino_layer_scores=state.dino_layer_scores,
                    generation_metadata=state.generation_metadata,
                    processing_history=state.processing_history,
                    sort_order=state.sort_order,
                    # Same rule as the on-disk branch above: lineage timestamps
                    # travel, the video id does not.
                    source_video_id=None,
                    source_timestamp_ms=state.source_timestamp_ms,
                    source_shot_index=state.source_shot_index,
                    **copy_provenance(state),
                ))
            except Exception as exc:
                log.warning("duplicate_dataset (snapshot): failed to copy %s: %s", state.filename, exc)
                skipped += 1

            if i % 10 == 0:
                pct = round((i + 1) / total * 100, 1) if total else 100.0
                await broadcaster.emit(job_id, {
                    "type": "progress",
                    "job_id": job_id,
                    "job_type": "duplicate",
                    "dataset_id": source_dataset.id,
                    "status": "running",
                    "done": i + 1,
                    "total": total,
                    "percent": pct,
                    "message": f"Copying {state.filename}",
                })

        if skipped:
            log.warning("duplicate_dataset: skipped %d/%d images (missing files)", skipped, total)

    # --- Step 3: commit and refresh stats ---
    await db.commit()
    await refresh_stats(db, new_ds.id)
    if cancelled:
        job_queue.raise_if_cancelled(job_id)
    return new_ds.id


async def get_tag_cooccurrence(db: AsyncSession, dataset_id: str, limit: int = 15, subfolder: str | None = None) -> dict:
    q = select(Image.caption_text).where(Image.dataset_id == dataset_id, Image.caption_text != "")
    if subfolder is not None:
        q = q.where(Image.subfolder == subfolder)
    result = await db.stream(q)
    all_tag_lists = []
    async for (caption_text,) in result:
        all_tag_lists.append([t.strip() for t in caption_text.split(",") if t.strip()])

    # Count tag frequencies, pick top N
    freq: dict[str, int] = {}
    for tags in all_tag_lists:
        for t in tags:
            freq[t] = freq.get(t, 0) + 1

    top_tags = [t for t, _ in sorted(freq.items(), key=lambda x: -x[1])[:limit]]
    if not top_tags:
        return {"tags": [], "matrix": []}

    tag_idx = {t: i for i, t in enumerate(top_tags)}
    n = len(top_tags)
    matrix = [[0] * n for _ in range(n)]

    for tags in all_tag_lists:
        present = [tag_idx[t] for t in tags if t in tag_idx]
        for i in present:
            for j in present:
                matrix[i][j] += 1

    return {"tags": top_tags, "matrix": matrix}
