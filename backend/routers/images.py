import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, case, delete, func, or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from backend.config import settings
from backend.utils import ALLOWED_FLAG_KEYS, InsufficientDiskSpaceError, chunked, contained_path, copy_with_sidecar, normalize_image_format, normalize_subfolder, parse_license_filter_param, poster_path_for, record_in_place, rename_with_sidecar, require_free_space, safe_dataset_path, slugify_filename, thumbnail_path_for, unique_filename_with_thumb
from backend.database import get_db
from backend.media_types import media_kind_for
from backend.licenses import (
    PROVENANCE_FIELDS,
    copy_provenance,
    materialize_by_source,
    merge_provenance,
    resolve_provenance,
)
from backend.models import BackgroundJob, Dataset, Image, Video
from backend.models.detection import Detection
from backend.schemas.detection import DetectionOut
from backend.schemas.image import (
    BatchCopyDatasetResult,
    BatchCropRequest,
    BatchMoveDatasetRequest,
    BatchMoveSubfolderRequest,
    BatchResizeRequest,
    BatchReorderRequest,
    BulkCountRequest,
    BulkDeleteRequest,
    BulkProvenanceRequest,
    BulkProvenanceResult,
    BulkRenameRequest,
    BulkThumbnailRequest,
    ImageCropRequest,
    ImageFilterParams,
    ImageIdsParams,
    ImageListItem,
    ImageListParams,
    ImageOut,
    ImageProvenanceUpdate,
    ImageResizeRequest,
    RenameImageRequest,
)
from backend.services.dataset_service import refresh_stats
from backend.services import version_service
from backend.services.dataset_busy import ensure_not_busy
from backend.services.detection_service import remap_detections_for_crop
from backend.services.image_service import (
    crop_image_to_dest,
    crop_to_aspect,
    extract_generation_metadata,
    extract_embedded_provenance,
    generate_thumbnail,
    get_image_info,
    resize_image,
)
from backend.services.video_service import UnreadableVideoError, claimed_poster_stems, probe_and_poster
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])


def _write_upload_sync(src_fileobj, dest: Path, thumb_path: str) -> tuple[dict, dict | None, dict]:
    """Persist one upload and derive its metadata + thumbnail + provenance off the event loop.

    `src_fileobj` is an UploadFile's SpooledTemporaryFile; sync reads of it are safe in a thread.
    A browser upload carries no scraper sidecar, so only EXIF is available here.
    """
    with open(dest, "wb") as f:
        shutil.copyfileobj(src_fileobj, f)
    info = get_image_info(str(dest))
    gen_meta = extract_generation_metadata(str(dest))
    generate_thumbnail(str(dest), thumb_path)
    return info, gen_meta, extract_embedded_provenance(str(dest)) or {}


def _write_video_upload_sync(src_fileobj, dest: Path, poster_path: Path) -> tuple[dict, str | None]:
    """Persist one uploaded video, read its header and cut its poster off the event loop.

    The probe doubles as the ingest gate — cv2 cannot open a truncated or
    zero-byte file — so a rejected upload is removed again rather than left as
    an orphan in videos/ that no row points at. The poster is not a gate: a
    video whose frames will not decode still ingests with `poster_path` NULL.
    """
    with open(dest, "wb") as f:
        shutil.copyfileobj(src_fileobj, f)
    try:
        return probe_and_poster(dest, poster_path)
    except BaseException:
        # Every failure removes the bytes, not just the gate's. `probe_and_poster`
        # shields `generate_poster` alone, so `probe_video` still raises cv2's
        # lazy `ImportError` (a headless host with no libGL), a raw `cv2.error`
        # or a `MemoryError` straight through — and those left the file behind in
        # videos/ with no row for the next folder sync to adopt.
        dest.unlink(missing_ok=True)
        raise

_ALLOWED_SCORE_FIELDS = frozenset({
    "aesthetic_score", "blur_score", "noise_score", "uniformity_score",
    "watermark_score", "color_score", "saturation_score", "luminance_score",
    "style_similarity_score",
})

# Every value `GET /images/`'s `sort` param may take. An unknown one is coerced
# to `created_at`, never rejected — a stale `sortIdx` persisted in
# `gallery-state-${datasetId}` must degrade to a working gallery, not a 400.
#
# The allowlist exists because the ordering block reaches `Image` by `getattr`,
# and `Image` has attributes that are not columns: `?sort=metadata`,
# `?sort=dataset` (the relationship) and `?sort=has_dino_layer_embeddings` (the
# property) each returned something SQLAlchemy cannot order by and raised a
# **500**. The fallback in the `getattr` call only ever caught names that do not
# exist at all.
_ALLOWED_SORT_FIELDS = _ALLOWED_SCORE_FIELDS | {
    "created_at", "updated_at", "filename", "file_size_bytes",
    "width", "height", "caption_token_count", "sort_order",
    # `_ALLOWED_SCORE_FIELDS` omits NSFW for *filter* reasons — that set also
    # drives the score filters, the Stats histograms and `score_filters`, all of
    # which exclude it deliberately. Sorting is a separate question, and
    # inheriting the filter set silently removed an ordering that worked before
    # this allowlist existed. Do not add it to `_ALLOWED_SCORE_FIELDS`.
    "nsfw_score",
    # Frame lineage. Both get an explicit nulls-last ordering below.
    "source_timestamp_ms", "source_shot_index",
}

# Lineage sorts need nulls-last plus a stable tiebreak; the generic branch has
# neither, so an ASC sort would float every non-frame image to the top and leave
# equal-timestamp frames in whatever order SQLite scanned them.
_NULLS_LAST_SORT_FIELDS = frozenset({"source_timestamp_ms", "source_shot_index"})

def _apply_bulk_filters(query, image_ids, subfolder, quality_flags, include_flagged: bool = False):
    if image_ids is not None:
        query = query.where(Image.id.in_(image_ids))
    elif subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(subfolder))
    if quality_flags:
        valid_flags = [f for f in quality_flags if f in ALLOWED_FLAG_KEYS]
        if valid_flags:
            if include_flagged:
                query = query.where(or_(*[Image.quality_flags[f].as_boolean().is_(True) for f in valid_flags]))
            else:
                query = query.where(and_(*[Image.quality_flags[f].as_boolean().is_not(True) for f in valid_flags]))
    return query



# Cap on `GET /images/ids`. It bounds the response *and* every batch request
# body that follows from the selection it hands back — a 200k-id POST is a
# different problem from a 200k-id GET, and this is the one place both are
# bounded. The six endpoints that consume such a selection still bind every id
# in one statement — an unchunked `Image.id.in_(...)` over the whole list, in
# `batch_delete` (695, 718), `batch/resize` (1266), `batch/crop` (1367),
# `batch/move-subfolder` (1900), `batch/move-dataset` (1974) and
# `batch/copy-dataset` (2134) — so the cap also has to stay under SQLite's
# 32,766 bind-parameter ceiling. 20,000 is twice `utils.chunked`'s default with
# a 1.6× margin to that ceiling; raising it means chunking those six first.
SELECT_ALL_ID_CAP = 20_000


def _apply_image_filters(q, f: ImageFilterParams):
    """Narrow `q` by `dataset_id` plus every filter in `f`.

    Shared verbatim by the three endpoints that must describe the same view —
    `GET /images/`, `GET /images/count` and `GET /images/ids` — so a filter
    reaches all three or none. Takes any select shape: the license block's
    `join(Dataset, …)` carries a `select(func.count(Image.id))` as happily as a
    `select(Image)`, and no clause here depends on the columns selected.

    The two 400s live here rather than in the routes so bad input is rejected
    identically by all three; a count that silently ignored an invalid
    `score_field` the grid rejected would offer a number for a view that does
    not exist.
    """
    if f.score_field and f.score_field not in _ALLOWED_SCORE_FIELDS:
        raise HTTPException(400, f"Invalid score_field: {f.score_field}")
    if f.quality_flag and f.quality_flag not in ALLOWED_FLAG_KEYS:
        raise HTTPException(400, f"Invalid quality_flag: {f.quality_flag}")

    q = q.where(Image.dataset_id == f.dataset_id)

    if f.captioned is True:
        q = q.where(Image.caption_text != "")
    elif f.captioned is False:
        q = q.where(Image.caption_text == "")

    if f.search:
        term = f"%{f.search}%"
        q = q.where(or_(Image.original_filename.ilike(term), Image.caption_text.ilike(term)))

    # Score filtering — score_field selects the column; defaults to aesthetic_score
    score_col = getattr(Image, f.score_field) if f.score_field else Image.aesthetic_score
    if f.score_is_null is True:
        q = q.where(score_col.is_(None))
    else:
        if f.min_score is not None:
            q = q.where(score_col >= f.min_score)
        if f.max_score is not None:
            q = q.where(score_col <= f.max_score)

    if f.quality_flag:
        q = q.where(Image.quality_flags[f.quality_flag].as_boolean() == True)  # noqa: E712

    if f.file_size_min is not None:
        q = q.where(Image.file_size_bytes >= f.file_size_min)
    if f.file_size_max is not None:
        q = q.where(Image.file_size_bytes <= f.file_size_max)

    if f.mp_min is not None:
        q = q.where(Image.width * Image.height >= int(f.mp_min * 1_000_000))
    if f.mp_max is not None:
        q = q.where(Image.width * Image.height < int(f.mp_max * 1_000_000))

    if f.ar_min is not None:
        q = q.where(Image.width >= f.ar_min * Image.height)
    if f.ar_max is not None:
        q = q.where(Image.width < f.ar_max * Image.height)

    if f.format_filter:
        q = q.where(Image.format == f.format_filter)

    if f.subfolder is not None:
        escaped_subfolder = f.subfolder.replace("%", r"\%").replace("_", r"\_")
        q = q.where(
            (Image.subfolder == f.subfolder)
            | Image.subfolder.like(escaped_subfolder + "/%", escape="\\")
        )

    # Frame lineage — every frame this video produced, wherever curation has since
    # filed it. The column is indexed, so this is a plain equality: no join, no
    # EXISTS. Truthiness rather than `is not None` (unlike `subfolder`, "" carries
    # no meaning here), and no allowlist — the value is an opaque uuid and an
    # unknown one correctly returns zero rows.
    if f.source_video_id:
        q = q.where(Image.source_video_id == f.source_video_id)

    if f.detection_label:
        q = q.where(
            select(Detection.id)
            .where(Detection.image_id == Image.id, Detection.label.ilike(f"%{f.detection_label}%"))
            .exists()
        )

    # Detection-driven filters for Stats "Detections & Masks" click-through.
    # Exact label + score conditions combine into ONE EXISTS subquery so they all
    # apply to the *same* detection row (a row scoring low on one label must not be
    # matched via a different high-scoring label).
    if (
        f.detection_label_exact is not None
        or f.detection_score_min is not None
        or f.detection_score_max is not None
        or f.detection_score_null is True
    ):
        det_conds = [Detection.image_id == Image.id]
        if f.detection_label_exact is not None:
            det_conds.append(Detection.label == f.detection_label_exact)
        if f.detection_score_null is True:
            det_conds.append(Detection.score.is_(None))
        else:
            if f.detection_score_min is not None:
                det_conds.append(Detection.score >= f.detection_score_min)
            if f.detection_score_max is not None:
                det_conds.append(Detection.score < f.detection_score_max)
        q = q.where(select(Detection.id).where(*det_conds).exists())

    # Mask coverage — per-image SUM(mask_area) clamped to 1.0; min inclusive, max
    # exclusive (matching caption filters). Requires ≥1 detection (EXISTS) so the
    # click-through population matches the coverage histogram.
    if f.mask_coverage_min is not None or f.mask_coverage_max is not None:
        cov_subq = (
            select(func.coalesce(func.sum(Detection.mask_area), 0.0))
            .where(Detection.image_id == Image.id)
            .scalar_subquery()
        )
        cov_expr = case((cov_subq > 1.0, 1.0), else_=cov_subq)
        q = q.where(
            select(Detection.id).where(Detection.image_id == Image.id).exists()
        )
        if f.mask_coverage_min is not None:
            q = q.where(cov_expr >= f.mask_coverage_min)
        if f.mask_coverage_max is not None:
            q = q.where(cov_expr < f.mask_coverage_max)

    # Detections-per-image count — correlated COUNT coalesced to 0 so the "0"
    # bucket (images with no detections) works naturally.
    if f.detection_count_min is not None or f.detection_count_max is not None:
        count_subq = (
            select(func.count(Detection.id))
            .where(Detection.image_id == Image.id)
            .scalar_subquery()
        )
        count_expr = func.coalesce(count_subq, 0)
        if f.detection_count_min is not None:
            q = q.where(count_expr >= f.detection_count_min)
        if f.detection_count_max is not None:
            q = q.where(count_expr <= f.detection_count_max)

    if f.score_filters:
        try:
            # `sf`, not `f` — `f` is the filter model in this scope now.
            for sf in json.loads(f.score_filters):
                field = sf.get("field", "")
                if field not in _ALLOWED_SCORE_FIELDS:
                    continue
                col = getattr(Image, field)
                mn = sf.get("min")
                mx = sf.get("max")
                if mn is not None:
                    q = q.where(col >= float(mn))
                if mx is not None:
                    q = q.where(col <= float(mx))
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass

    if f.caption_words_min is not None or f.caption_words_max is not None:
        wc_expr = case(
            (
                (Image.caption_text.is_(None)) | (func.trim(Image.caption_text) == ""),
                0,
            ),
            else_=(
                func.length(func.trim(Image.caption_text))
                - func.length(func.replace(func.trim(Image.caption_text), " ", ""))
                + 1
            ),
        )
        if f.caption_words_min is not None:
            q = q.where(wc_expr >= f.caption_words_min)
        if f.caption_words_max is not None:
            q = q.where(wc_expr < f.caption_words_max)

    if f.caption_tokens_min is not None or f.caption_tokens_max is not None:
        # Token counts are persisted in Image.caption_token_count (kept in sync by the
        # caption_text listener), so filtering is plain SQL — min inclusive, max exclusive,
        # empty caption = 0. No fetch cap, no in-Python BPE pass.
        tc_expr = func.coalesce(Image.caption_token_count, 0)
        if f.caption_tokens_min is not None:
            q = q.where(tc_expr >= f.caption_tokens_min)
        if f.caption_tokens_max is not None:
            q = q.where(tc_expr < f.caption_tokens_max)

    # License filter operates on the *effective* value — an image with NULL
    # license carries its dataset's default, so the filter must join and
    # coalesce rather than test Image.license alone.
    effective_license = func.coalesce(func.nullif(Image.license, ""), Dataset.license, "")
    if f.license_filter or f.license_missing is not None:
        q = q.join(Dataset, Dataset.id == Image.dataset_id)
        if f.license_missing is True:
            q = q.where(effective_license == "")
        elif f.license_missing is False:
            q = q.where(effective_license != "")
        if f.license_filter:
            # JSON array, not comma-separated: an `other:<free text>` id may
            # contain commas. Same encoding as the export preview.
            parsed = parse_license_filter_param(f.license_filter) or []
            if parsed and any(not v for v in parsed):
                # `""` is a meaningful entry for the *export* filters ("no license
                # recorded"), so a client can reasonably send it here too — but
                # this endpoint expresses that through `license_missing`. Dropping
                # the blank silently narrows a mixed list to the non-blank ids
                # (returning fewer images than asked for), and an all-blank list
                # to no filter at all (returning every image). Both are silent
                # lies, so any blank entry is a 400.
                raise HTTPException(
                    400,
                    "license_filter contains an empty entry; use license_missing=true "
                    "to select images with no license recorded",
                )
            if parsed:
                q = q.where(effective_license.in_(parsed))

    return q


def _apply_image_ordering(q, sort: str, order: str):
    """Order `q` the way the gallery grid is ordered.

    Split out of the filter chain so `GET /images/ids` hands back the *first N
    in the order the user is looking at* rather than an arbitrary slice. Safe to
    apply after every filter even though this block used to sit between two of
    them: `where` clauses accumulate order-independently.
    """
    # Coerce rather than 400: an unknown name already fell back today, and a
    # stale persisted `sortIdx` must degrade to a working gallery.
    if sort not in _ALLOWED_SORT_FIELDS:
        sort = "created_at"
    sort_col = getattr(Image, sort)
    if sort == "sort_order":
        # Manual ordering is ascending by definition; unpositioned images last.
        # `order` is ignored on purpose — "custom order, descending" is not
        # something the drag-and-drop grid can mean. This branch matches first,
        # which is why `sort_order` is absent from `_NULLS_LAST_SORT_FIELDS`
        # below: an entry there could never be reached.
        return q.order_by(Image.sort_order.asc().nulls_last(), Image.created_at.asc())
    if sort in _NULLS_LAST_SORT_FIELDS:
        # NULL here means "not a video frame", which belongs at the end whichever
        # direction the frames run in, with `created_at` breaking the ties that
        # two frames at the same timestamp or in the same shot would otherwise
        # leave to SQLite's scan order.
        direction = sort_col.desc() if order == "desc" else sort_col.asc()
        return q.order_by(direction.nulls_last(), Image.created_at.asc())
    return q.order_by(sort_col.desc() if order == "desc" else sort_col.asc())


@router.get("/", response_model=list[ImageListItem])
async def list_images(
    f: Annotated[ImageListParams, Query()],
    db: AsyncSession = Depends(get_db),
):
    q = _apply_image_ordering(_apply_image_filters(select(Image), f), f.sort, f.order)
    q = q.offset((f.page - 1) * f.limit).limit(f.limit)
    result = await db.execute(q)
    images = result.scalars().all()

    # Stamp the effective license onto each row for the gallery badge. One
    # extra lookup for the whole page, not per image.
    ds_license = ""
    if images:
        ds_license = (await db.execute(
            select(Dataset.license).where(Dataset.id == f.dataset_id)
        )).scalar_one_or_none() or ""
    out = []
    for img in images:
        item = ImageListItem.model_validate(img)
        item.license = img.license or ds_license
        out.append(item)
    return out


# Both of these must stay **above** `GET /{image_id}`, or FastAPI matches
# `count`/`ids` as an image id and answers 404.
@router.get("/count")
async def count_images(
    f: Annotated[ImageFilterParams, Query()],
    db: AsyncSession = Depends(get_db),
):
    """How many images the current filters match — the number behind the
    pagination row and the select-all offer. No ordering, no paging."""
    q = _apply_image_filters(select(func.count(Image.id)), f)
    return {"count": (await db.execute(q)).scalar_one()}


@router.get("/ids")
async def list_image_ids(
    f: Annotated[ImageIdsParams, Query()],
    db: AsyncSession = Depends(get_db),
):
    """Every id the current filters match, for "select all matching filters".

    Takes `sort`/`order` as well as the filters, so a truncated result is the
    first `SELECT_ALL_ID_CAP` *in the order the user is looking at*. Over the
    cap, `count` is the true total (a second query) rather than the trimmed
    length, so the UI can say "the first 20,000 of 84,113".
    """
    q = _apply_image_ordering(_apply_image_filters(select(Image.id), f), f.sort, f.order)
    rows = (await db.execute(q.limit(SELECT_ALL_ID_CAP + 1))).scalars().all()
    if len(rows) > SELECT_ALL_ID_CAP:
        total = (await db.execute(
            _apply_image_filters(select(func.count(Image.id)), f)
        )).scalar_one()
        return {"ids": list(rows[:SELECT_ALL_ID_CAP]), "count": total, "truncated": True}
    return {"ids": list(rows), "count": len(rows), "truncated": False}


@router.post("/upload", status_code=201)
async def upload_images(
    dataset_id: str,
    subfolder: str = Query(""),
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    ensure_not_busy(dataset_id)

    dest_images = Path(ds.folder_path) / "images"
    dest_thumbs = Path(ds.folder_path) / "thumbnails"
    dest_videos = Path(ds.folder_path) / "videos"
    dest_posters = dest_videos / "thumbnails"
    added = []
    videos_added: list[str] = []
    # Files we would not or could not ingest. Reported rather than silently
    # dropped: before this the loop just `continue`d and the response counted
    # only successes, so a rejected upload looked exactly like a successful one.
    skipped: list[dict] = []
    norm_subfolder = normalize_subfolder(subfolder)

    existing_result = await db.execute(select(Image.filename).where(Image.dataset_id == dataset_id))
    db_names: set[str] = {r[0] for r in existing_result.all()}
    occupied_thumb_stems: set[str] = {p.stem for p in dest_thumbs.glob("*.webp")} if dest_thumbs.exists() else set()
    planned_thumb_stems: set[str] = set()

    existing_videos = await db.execute(
        select(Video.id, Video.filename, Video.poster_path).where(Video.dataset_id == dataset_id)
    )
    existing_video_rows = [(r.id, r.filename, r.poster_path) for r in existing_videos.all()]
    video_db_names: set[str] = {fn for _, fn, _ in existing_video_rows}
    # Seeded from the *rows*, not only from posters on disk: a row whose poster
    # could not be cut, or one whose stem was disambiguated by rescan, is not
    # findable by globbing alone. See video_service.claimed_poster_stems.
    occupied_poster_stems = claimed_poster_stems(existing_video_rows, dest_posters)
    planned_poster_stems: set[str] = set()

    # Append new uploads at the end of the custom order only when EVERY existing image in this
    # subfolder already has a sort_order assigned. A partial assignment (some NULLs) means the
    # custom order was never fully initialized, so we leave new uploads unordered too.
    order_stats_result = await db.execute(
        select(func.count(Image.id), func.count(Image.sort_order), func.max(Image.sort_order)).where(
            Image.dataset_id == dataset_id,
            Image.subfolder == norm_subfolder,
        )
    )
    total_count, ordered_count, max_order = order_stats_result.one()
    next_sort_order: int | None = (max_order + 1) if (total_count > 0 and total_count == ordered_count) else None

    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        kind = media_kind_for(suffix)
        if kind is None:
            skipped.append({"file": upload.filename or "", "reason": f"Unsupported file type: {suffix or 'no extension'}"})
            continue
        if kind == "video":
            # Videos are sources, not gallery images — they get a Video row and
            # live flat in videos/. videos/ is created lazily so an image-only
            # dataset never grows an empty directory.
            dest_videos.mkdir(parents=True, exist_ok=True)
            slug = slugify_filename(Path(upload.filename or "").stem) or "video"
            # Poster thumbnails are .webp keyed by stem, exactly like image
            # thumbnails, so `a.mp4` and `a.mkv` would collide on one poster
            # path. Same helper, different directory pair.
            filename = unique_filename_with_thumb(
                dest_videos, slug, suffix, video_db_names, occupied_poster_stems, planned_poster_stems
            )
            dest = dest_videos / filename
            try:
                info, poster_path = await asyncio.get_running_loop().run_in_executor(
                    None, _write_video_upload_sync, upload.file, dest, Path(poster_path_for(dest))
                )
            except UnreadableVideoError as exc:
                skipped.append({"file": upload.filename or "", "reason": str(exc)})
                video_db_names.discard(filename)
                planned_poster_stems.discard(dest.stem)
                continue
            except Exception as exc:
                # One bad file must not take the request down. The only commit is
                # after this loop, so an escaping exception discards the rows for
                # every file already copied in this request while leaving those
                # files (and their thumbnails) on disk — the user sees "nothing
                # uploaded", re-uploads into `_001` duplicates, and a later folder
                # sync adopts the orphans at `subfolder=""` as a second copy of
                # everything. The other two ingest paths (`import_images_from_folder`
                # and `_rescan_videos`) both guard per file for exactly this.
                logger.exception("upload: probing %s failed", upload.filename)
                skipped.append({
                    "file": upload.filename or "",
                    "reason": f"Could not read video: {exc.__class__.__name__}",
                })
                video_db_names.discard(filename)
                planned_poster_stems.discard(dest.stem)
                continue
            db.add(Video(
                dataset_id=dataset_id,
                filename=filename,
                original_filename=upload.filename or filename,
                file_path=str(dest),
                poster_path=poster_path,
                # A browser upload carries no provenance for a video (no EXIF
                # equivalent we read), so everything inherits the dataset default.
                **info,
            ))
            videos_added.append(filename)
            continue

        raw_stem = Path(upload.filename or "").stem
        slug = slugify_filename(raw_stem) or "image"
        filename = unique_filename_with_thumb(dest_images, slug, suffix, db_names, occupied_thumb_stems, planned_thumb_stems)
        dest = dest_images / filename
        thumb_path = str(dest_thumbs / (dest.stem + ".webp"))
        try:
            info, gen_meta, captured = await asyncio.get_running_loop().run_in_executor(
                None, _write_upload_sync, upload.file, dest, thumb_path
            )
        except Exception as exc:
            # Same per-file containment as the video branch above, and for the
            # same reason: `_write_upload_sync` copies the file *first*, then runs
            # `get_image_info`, `extract_generation_metadata` and
            # `generate_thumbnail`, any of which can raise on a corrupt or
            # truncated image — and the single commit sits after this loop.
            logger.exception("upload: processing %s failed", upload.filename)
            dest.unlink(missing_ok=True)
            Path(thumb_path).unlink(missing_ok=True)
            skipped.append({
                "file": upload.filename or "",
                "reason": f"Could not read image: {exc.__class__.__name__}",
            })
            db_names.discard(filename)
            planned_thumb_stems.discard(dest.stem)
            continue

        img = Image(
            dataset_id=dataset_id,
            filename=filename,
            original_filename=upload.filename or filename,
            subfolder=norm_subfolder,
            file_path=str(dest),
            thumbnail_path=thumb_path,
            generation_metadata=gen_meta,
            sort_order=next_sort_order,
            # Only EXIF is available here (no scraper sidecar on a browser
            # upload); anything unset inherits the dataset default.
            **merge_provenance(captured),
            **info,
        )
        if next_sort_order is not None:
            next_sort_order += 1
        db.add(img)
        added.append(filename)

    await db.commit()
    await refresh_stats(db, dataset_id)
    return {
        "added": len(added),
        "files": added,
        "videos_added": len(videos_added),
        "videos": videos_added,
        "skipped": skipped,
    }


@router.get("/{image_id}", response_model=ImageOut)
async def get_image(image_id: str, db: AsyncSession = Depends(get_db)):
    img = await db.get(
        Image, image_id,
        options=[undefer(Image.dino_layer_embeddings), undefer(Image.source_meta)],
    )
    if not img:
        raise HTTPException(404, "Image not found")
    if img.generation_metadata is None and img.file_path and Path(img.file_path).exists():
        gen_meta = await asyncio.get_running_loop().run_in_executor(
            None, extract_generation_metadata, img.file_path
        )
        if gen_meta:
            img.generation_metadata = gen_meta
            await db.commit()
    det_result = await db.execute(
        select(Detection).where(Detection.image_id == image_id).order_by(Detection.id)
    )
    detections = det_result.scalars().all()
    img_out = ImageOut.model_validate(img)
    img_out.detections = [DetectionOut.model_validate(d) for d in detections]
    ds = await db.get(Dataset, img.dataset_id)
    img_out.provenance = resolve_provenance(img, ds)
    return img_out


@router.delete("/{image_id}", status_code=204)
async def delete_image(image_id: str, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    dataset_id = img.dataset_id
    ensure_not_busy(dataset_id)
    # The *resolved* paths the guard hands back are what gets unlinked — validating
    # one path and deleting another would defeat the check entirely.
    p = contained_path(img.file_path, settings.datasets_dir, context="delete_image", ident=img.id)
    t = contained_path(
        img.thumbnail_path, settings.datasets_dir, context="delete_image", ident=img.id
    )
    txt = p.with_suffix(".txt") if p is not None else None
    if p is not None:
        await version_service.mark_image_deleted_in_versions(img.id, str(p), db)
    await db.delete(img)
    await db.commit()
    for f in [p, t, txt]:
        if f and f.exists():
            f.unlink(missing_ok=True)
    await refresh_stats(db, dataset_id)


@router.delete("/batch/delete", status_code=204)
async def batch_delete(image_ids: list[str], db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Image.id, Image.dataset_id, Image.file_path, Image.thumbnail_path)
        .where(Image.id.in_(image_ids))
    )
    rows = result.all()
    if not rows:
        return

    dataset_ids = {r.dataset_id for r in rows}
    for did in dataset_ids:
        ensure_not_busy(did)
    files_to_delete: list[Path] = []
    for r in rows:
        # Per row, not per request: one escaped path must not stop its neighbours
        # from being deleted properly.
        p = contained_path(r.file_path, settings.datasets_dir, context="batch_delete", ident=r.id)
        if p is not None:
            await version_service.mark_image_deleted_in_versions(r.id, str(p), db)
            files_to_delete.extend([p, p.with_suffix(".txt")])
        t = contained_path(
            r.thumbnail_path, settings.datasets_dir, context="batch_delete", ident=r.id
        )
        if t is not None:
            files_to_delete.append(t)

    await db.execute(delete(Image).where(Image.id.in_(image_ids)))
    await db.commit()

    for f in files_to_delete:
        f.unlink(missing_ok=True)

    for did in dataset_ids:
        await refresh_stats(db, did)


@router.post("/bulk-count")
async def bulk_count(body: BulkCountRequest, db: AsyncSession = Depends(get_db)):
    query = _apply_bulk_filters(
        select(func.count(Image.id)).where(Image.dataset_id == body.dataset_id),
        body.image_ids, body.subfolder, body.quality_flags,
        include_flagged=body.include_flagged,
    )
    count = (await db.execute(query)).scalar_one()
    return {"count": count}


def _provenance_values(body) -> dict:
    """Turn a provenance edit request into SQL values.

    None → the field is absent from the result (left unchanged).
    "" (or whitespace) → NULL (clear the override so the dataset default applies).
    Anything else → that value; `license` has already been normalized to a known
    id or `other:<free text>` by the schema validator.
    """
    values: dict = {}
    for field in PROVENANCE_FIELDS:
        raw = getattr(body, field)
        if raw is None:
            continue
        values[field] = raw.strip() or None
    return values


@router.post("/bulk-provenance", response_model=BulkProvenanceResult)
async def bulk_provenance(body: BulkProvenanceRequest, db: AsyncSession = Depends(get_db)):
    """Set source/license on a selection — how an existing library gets labeled."""
    values = _provenance_values(body)
    if not values:
        ensure_not_busy(body.dataset_id)
        return BulkProvenanceResult(updated=0)

    subq = _apply_bulk_filters(
        select(Image.id, Image.dataset_id),
        body.image_ids, body.subfolder, body.quality_flags,
        include_flagged=body.include_flagged,
    )
    # An explicit image_ids selection can span datasets — the gallery toolbar shows
    # a per-dataset breakdown precisely because of that — so scope by dataset_id
    # only when the selection is a whole-dataset/subfolder one, and guard every
    # dataset actually touched rather than just body.dataset_id.
    if not body.image_ids:
        subq = subq.where(Image.dataset_id == body.dataset_id)
    rows = (await db.execute(subq)).all()
    touched = {r.dataset_id for r in rows} or {body.dataset_id}
    for ds_id in touched:
        ensure_not_busy(ds_id)

    ids = [r.id for r in rows]
    updated = 0
    # Chunked so the bind-parameter count stays under SQLite's ceiling (utils.chunked).
    for batch in chunked(ids):
        result = await db.execute(
            sa_update(Image).where(Image.id.in_(list(batch))).values(**values)
        )
        updated += result.rowcount or 0
    await db.commit()
    # No refresh_stats: it recomputes counts/sizes, none of which provenance
    # touches. The license breakdown is computed live by GET /datasets/{id}/stats.
    return BulkProvenanceResult(updated=updated)


@router.patch("/{image_id}/provenance", response_model=ImageOut)
async def update_image_provenance(
    image_id: str, body: ImageProvenanceUpdate, db: AsyncSession = Depends(get_db)
):
    # undefer as in get_image: ImageOut reads has_dino_layer_embeddings, and a
    # deferred column would lazy-load mid-serialization (MissingGreenlet).
    # source_meta is deferred too and is part of the provenance response.
    img = await db.get(
        Image, image_id,
        options=[undefer(Image.dino_layer_embeddings), undefer(Image.source_meta)],
    )
    if not img:
        raise HTTPException(404, "Image not found")
    # Guards like every sibling write: restore_snapshot writes provenance, so an
    # unguarded save would race a running restore.
    ensure_not_busy(img.dataset_id)
    for field, value in _provenance_values(body).items():
        setattr(img, field, value)
    # No refresh: the session is expire_on_commit=False, so the instance keeps
    # its loaded state (and a refresh would re-defer the undeferred column).
    await db.commit()
    ds = await db.get(Dataset, img.dataset_id)
    img_out = ImageOut.model_validate(img)
    img_out.provenance = resolve_provenance(img, ds)
    # Populated as in get_image, so the client can seed its per-image cache from
    # this response instead of showing an image that has lost its detections.
    dets = (await db.execute(
        select(Detection).where(Detection.image_id == image_id).order_by(Detection.id)
    )).scalars().all()
    img_out.detections = [DetectionOut.model_validate(d) for d in dets]
    return img_out


@router.post("/bulk-rename")
async def bulk_rename(body: BulkRenameRequest, db: AsyncSession = Depends(get_db)):
    ensure_not_busy(body.dataset_id)
    raw = body.new_stem.strip()
    if not raw:
        raise HTTPException(400, "new_stem cannot be empty")
    stem = slugify_filename(raw)
    if not stem:
        raise HTTPException(400, "new_stem produces empty slug")

    order_clause = (
        (Image.sort_order.asc().nulls_last(), Image.created_at.asc())
        if body.sort_by_sort_order
        else (Image.created_at,)
    )
    query = _apply_bulk_filters(
        select(Image.id, Image.file_path, Image.filename).where(Image.dataset_id == body.dataset_id),
        body.image_ids, body.subfolder, body.quality_flags,
    ).order_by(*order_clause)

    rows = (await db.execute(query)).all()
    if not rows:
        return {"affected": 0}

    images_dir = Path(rows[0].file_path).parent
    thumb_dir = images_dir.parent / "thumbnails"

    batch_ids = [r.id for r in rows]
    existing = await db.execute(
        select(Image.filename).where(
            Image.dataset_id == body.dataset_id,
            ~Image.id.in_(batch_ids),
        )
    )
    db_names: set[str] = {r[0] for r in existing.all()}
    # The **sanctioned exception** to `unique_filename_with_thumb`'s "never exclude
    # the stems of images being renamed" contract: the batch's own thumbnails and
    # image files are excluded from the occupancy checks, because without that a
    # second Renumber of image.jpg … image_007.jpg sees all eight .webp stems and
    # all eight files as occupied and starts the counter at image_008 instead of
    # restarting at 001. What pays for it is the two-phase rename below, which
    # defers any rename whose target collides with a batch member's *current*
    # image, thumbnail or sidecar path (PM-017).
    batch_thumb_stems: set[str] = {Path(thumbnail_path_for(r.file_path)).stem for r in rows}
    occupied_thumb_stems: set[str] = (
        {p.stem for p in thumb_dir.glob("*.webp") if p.stem not in batch_thumb_stems}
        if thumb_dir.exists() else set()
    )
    # A non-batch row whose .webp is missing (never generated, or hand-deleted)
    # still owns its stem — the thumbnail this rename creates for it would be
    # regenerated onto that row's path by `serve_thumbnail`. Occupancy is the union
    # of the two, exactly as `file-browser.md` defines it.
    occupied_thumb_stems |= {Path(n).stem for n in db_names}
    # Image files currently on disk that belong to this batch — they will be renamed away,
    # so the counter should not treat them as occupied during planning.
    batch_current_filenames: set[str] = {Path(r.file_path).name for r in rows}
    planned_thumb_stems: set[str] = set()

    plan: list[tuple[Path, Path, Path, Path, str, str]] = []  # (old, new, old_thumb, new_thumb, id, new_fn)
    for row in rows:
        old_path = Path(row.file_path)
        new_filename = unique_filename_with_thumb(
            images_dir, stem, old_path.suffix.lower(), db_names, occupied_thumb_stems, planned_thumb_stems,
            disk_exclude=batch_current_filenames,
        )
        new_path = images_dir / new_filename
        old_thumb = Path(thumbnail_path_for(str(old_path)))
        new_thumb = Path(thumbnail_path_for(str(new_path)))
        plan.append((old_path, new_path, old_thumb, new_thumb, row.id, new_filename))

    # Two-phase DB update: first move all batch filenames to guaranteed-unique temp names,
    # then set the final names. A single executemany updating filename to a permutation of
    # the current values would hit transient UNIQUE(dataset_id, filename) violations because
    # SQLite checks the constraint immediately per row (not deferred).
    await db.execute(
        sa_update(Image),
        [{"id": img_id, "filename": f"__renaming__{img_id}{Path(old_path).suffix.lower()}"}
         for old_path, _, _, _, img_id, _ in plan],
    )
    await db.execute(
        sa_update(Image),
        [{"id": img_id, "filename": new_fn, "file_path": str(new_path),
          "thumbnail_path": str(new_thumb), "is_auto_named": True}
         for _, new_path, _, new_thumb, img_id, new_fn in plan],
    )

    # Commit DB before touching the filesystem so the DB is always the authoritative
    # record of where files should live. If a filesystem rename fails partway through,
    # the DB already reflects the intended final state; only the not-yet-renamed files
    # are temporarily inaccessible (vs. the previous order where a mid-batch FS failure
    # left the DB rolled back to old paths while some files had already moved).
    await db.commit()

    # Two-phase rename to avoid clobbering batch files whose current name is another
    # batch file's target name (e.g. after drag-reordering: image_003 → image_001
    # while image_001 → image_003 in the same batch).
    # Phase 1: rename everything whose target is still occupied to a unique temp name;
    #          rename the rest directly.
    # Phase 2: move temp files to their final names.
    # A rename moves three things, and the deferral has to cover all three. The
    # image path alone is not enough: the thumbnail ({stem}.webp) and the caption
    # sidecar ({stem}.txt) are keyed on the **stem**, so a cross-extension
    # collision — shot.png taking shot.jpg's stem — is invisible to a path test,
    # takes the direct branch, and `replace`s a live sibling's thumbnail while
    # `rename_with_sidecar` overwrites its caption. Nothing repairs either: the
    # filenames are all correct, so `serve_thumbnail` regenerates only when the
    # file is *missing*, never when it is another image (PM-017).
    batch_old_paths: set[str] = {str(e[0]) for e in plan}
    batch_old_thumbs: set[str] = {str(e[2]) for e in plan}
    batch_old_sidecars: set[str] = {str(e[0].with_suffix(".txt")) for e in plan}
    deferred: list[tuple[Path, Path, Path, Path]] = []

    for old_path, new_path, old_thumb, new_thumb, img_id, _ in plan:
        if new_path == old_path:
            continue
        if (
            str(new_path) in batch_old_paths
            or str(new_thumb) in batch_old_thumbs
            or str(new_path.with_suffix(".txt")) in batch_old_sidecars
        ):
            # Keyed on the row id, like the DB half's temp names two blocks above:
            # a user renaming to the stem "__renaming__image" would otherwise pick
            # a name that collides with a live temp file.
            tmp_path = old_path.with_name(f"__renaming__{img_id}{old_path.suffix}")
            tmp_thumb = old_thumb.parent / f"__renaming__{img_id}{old_thumb.suffix}"
            rename_with_sidecar(old_path, tmp_path)
            if old_thumb.exists():
                old_thumb.replace(tmp_thumb)
            deferred.append((tmp_path, new_path, tmp_thumb, new_thumb))
        else:
            rename_with_sidecar(old_path, new_path)
            if old_thumb.exists():
                old_thumb.replace(new_thumb)

    for tmp_path, new_path, tmp_thumb, new_thumb in deferred:
        rename_with_sidecar(tmp_path, new_path)
        if tmp_thumb.exists():
            tmp_thumb.replace(new_thumb)

    return {"affected": len(plan)}


@router.patch("/batch/reorder")
async def batch_reorder(body: BatchReorderRequest, db: AsyncSession = Depends(get_db)):
    if not body.updates:
        return {"updated": 0}
    ids = [u.id for u in body.updates]
    count_result = await db.execute(
        select(func.count()).where(Image.id.in_(ids), Image.dataset_id == body.dataset_id)
    )
    if count_result.scalar() != len(ids):
        raise HTTPException(400, "Some image IDs do not belong to the specified dataset")
    await db.execute(
        sa_update(Image),
        [{"id": u.id, "sort_order": u.sort_order} for u in body.updates],
    )
    await db.commit()
    return {"updated": len(body.updates)}


@router.post("/bulk-delete")
async def bulk_delete_filtered(body: BulkDeleteRequest, db: AsyncSession = Depends(get_db)):
    ensure_not_busy(body.dataset_id)
    query = _apply_bulk_filters(
        select(Image.id, Image.dataset_id, Image.file_path, Image.thumbnail_path).where(
            Image.dataset_id == body.dataset_id
        ),
        body.image_ids, body.subfolder, body.quality_flags,
        include_flagged=body.include_flagged,
    )

    result = await db.execute(query)
    rows = result.all()
    if not rows:
        return {"deleted": 0}

    image_ids = [r.id for r in rows]
    files_to_delete: list[Path] = []
    for r in rows:
        p = contained_path(
            r.file_path, settings.datasets_dir, context="bulk_delete_filtered", ident=r.id
        )
        if p is not None:
            await version_service.mark_image_deleted_in_versions(r.id, str(p), db)
            files_to_delete.extend([p, p.with_suffix(".txt")])
        t = contained_path(
            r.thumbnail_path, settings.datasets_dir, context="bulk_delete_filtered", ident=r.id
        )
        if t is not None:
            files_to_delete.append(t)

    await db.execute(delete(Image).where(Image.id.in_(image_ids)))
    await db.commit()

    for f in files_to_delete:
        f.unlink(missing_ok=True)

    await refresh_stats(db, body.dataset_id)
    return {"deleted": len(image_ids)}


@router.post("/bulk-thumbnails")
async def bulk_thumbnails(body: BulkThumbnailRequest, db: AsyncSession = Depends(get_db)):
    """Re-cut the thumbnails for a scope — the repair for a stale-preview run.

    Four jobs regenerate an image thumbnail as a best-effort post-commit epilogue
    (`batch_lut`, `batch_upscale`, `crop_upscale`, `video_reextract`): the image
    is correct and committed, but the tile the gallery renders is the old one and
    nothing self-heals it — `GET /images/{id}/thumbnail` only regenerates when the
    file is *missing*, and a stale file exists.
    """
    query = _apply_bulk_filters(
        select(Image.id, Image.dataset_id),
        body.image_ids, body.subfolder, body.quality_flags,
        include_flagged=body.include_flagged,
    )
    # An explicit image_ids selection can span datasets — SelectionToolbar shows a
    # per-dataset breakdown precisely because of that — so scope by dataset_id
    # only when the selection is a whole-dataset/subfolder one, as bulk_provenance
    # does, and guard every dataset actually touched.
    if not body.image_ids:
        query = query.where(Image.dataset_id == body.dataset_id)
    rows = (await db.execute(query)).all()
    for ds_id in {r.dataset_id for r in rows} or {body.dataset_id}:
        ensure_not_busy(ds_id)

    image_ids = [r.id for r in rows]
    total = len(image_ids)

    # This is the repair you run *because* the volume filled up, so a 507 is the
    # right answer rather than 400 more "no space left on device" log lines.
    # Request-path, before the job row exists: an HTTPException raised inside the
    # job coroutine would fail the job instead of reaching the client.
    try:
        require_free_space(settings.datasets_dir)
    except InsufficientDiskSpaceError as e:
        raise HTTPException(507, str(e)) from None

    auto_label = f"Regenerate thumbnails — {total} image{'s' if total != 1 else ''}"
    job = BackgroundJob(
        job_type="regenerate_thumbnails",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=total,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.workers.progress import broadcaster

        counts = {"regenerated": 0, "failed": 0, "skipped": 0}
        async with AsyncSessionLocal() as session:
            # Chunked so the bind-parameter count stays under SQLite's ceiling
            # (utils.chunked) — `_apply_bulk_filters` above builds one query and
            # does not chunk, but it never binds the ids: only this loader does.
            image_rows: list[Image] = []
            for chunk in chunked(image_ids):
                image_rows.extend(
                    (await session.execute(select(Image).where(Image.id.in_(chunk)))).scalars().all()
                )
            loop = asyncio.get_running_loop()
            cancelled = False
            uncommitted = 0

            for i, img in enumerate(image_rows):
                if job_queue.cancel_requested(job_id):
                    cancelled = True
                    break
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "regenerate_thumbnails",
                    "status": "running", "done": i, "total": len(image_rows),
                    "percent": round(i / max(len(image_rows), 1) * 100, 1),
                    "current_item": img.filename,
                    "message": f"Re-cutting thumbnail for {img.filename}…",
                })

                # Both stored paths are gated, and the *resolved* paths are what
                # get read and written. `generate_thumbnail` mkdir(parents=True)s
                # its destination's parent, so an ungated `thumbnail_path` is an
                # arbitrary-file-write primitive, not merely a stale tile.
                src = contained_path(
                    img.file_path, settings.datasets_dir, context="bulk_thumbnails", ident=img.id
                )
                if src is None or not src.exists():
                    counts["skipped"] += 1
                    continue
                if img.thumbnail_path:
                    dest = contained_path(
                        img.thumbnail_path, settings.datasets_dir,
                        context="bulk_thumbnails", ident=img.id,
                    )
                    stored = img.thumbnail_path
                else:
                    # Derived from the already-guarded source, so it is inside the
                    # tree by construction. A row with no thumbnail at all is
                    # exactly what this repair should give one.
                    dest = Path(thumbnail_path_for(str(src)))
                    # The *stored* form is derived from the stored file_path, not
                    # from the resolved one the write uses: `contained_path`
                    # hands back a `.resolve()`d path, and writing one of those
                    # into the column changes the stored-path form mid-table
                    # (visible on a symlinked temp dir such as macOS /private/var).
                    stored = thumbnail_path_for(img.file_path)
                if dest is None:
                    counts["skipped"] += 1
                    continue

                try:
                    await loop.run_in_executor(None, generate_thumbnail, str(src), str(dest))
                except Exception as exc:
                    # Per row, so the repair does not inherit the failure mode it
                    # exists to fix: one unreadable source must not cost the other
                    # 4,999 images their working previews.
                    counts["failed"] += 1
                    logger.warning(
                        "regenerate_thumbnails: %s could not be re-cut: %s", img.filename, exc
                    )
                    continue

                img.thumbnail_path = stored
                # Mandatory, not bookkeeping: `imagesApi.thumbnailUrlVersioned`
                # builds `?v=${Date.parse(updated_at)}`, so a repair that rewrites
                # the .webp without moving the row leaves the <img src> unchanged
                # and the browser serves the stale tile straight from cache — the
                # repair would visibly do nothing.
                img.updated_at = datetime.now(timezone.utc)
                counts["regenerated"] += 1
                uncommitted += 1
                if uncommitted >= 50:
                    uncommitted = 0
                    await session.commit()

            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = counts
            # Above `raise_if_cancelled`, so a cancelled run keeps the counts for
            # the thumbnails it did repair.
            await session.commit()
            if cancelled:
                job_queue.raise_if_cancelled(job_id)
            # No refresh_stats: a thumbnail is not counted in `total_size_bytes`
            # (dataset_service builds it from Image.file_size_bytes), and nothing
            # else this job writes appears in a dataset rollup.

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": total}


@router.get("/{image_id}/file")
async def serve_file(image_id: str, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    p = safe_dataset_path(img.file_path, settings.datasets_dir)
    if not p.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(str(p))


@router.get("/{image_id}/thumbnail")
async def serve_thumbnail(image_id: str, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    if img.thumbnail_path:
        # Guarded like the file route above, and like the video poster twin: a
        # thumbnail_path is as much a stored path as file_path is.
        t = safe_dataset_path(img.thumbnail_path, settings.datasets_dir)
        if t.exists():
            return FileResponse(str(t))
    # Fallback: generate on demand
    p = safe_dataset_path(img.file_path, settings.datasets_dir)
    thumb = thumbnail_path_for(p)
    await asyncio.get_running_loop().run_in_executor(None, generate_thumbnail, str(p), thumb)
    img.thumbnail_path = thumb
    await db.commit()
    return FileResponse(thumb)


# The in-place-overwrite recorder lives in `backend/utils.py` so that the four
# other routers that overwrite pixels (lut, upscaling, detection, videos) share
# one writer of `processing_history` *and* `scores_stale`. This alias keeps the
# six call sites below — and PM-010's prose, which names it — valid.
_record_in_place = record_in_place


async def _guard_batch_datasets(db: AsyncSession, image_ids: list[str]) -> set[str]:
    """`ensure_not_busy` every dataset a bare id list touches; return the set.

    The two `/batch/*` overwrite endpoints below take `image_ids` with no dataset
    constraint, so one selection can span datasets — each needs its own guard,
    the way `batch_move_dataset` and `bulk_provenance` do it. Chunked so the bind
    parameter count stays under SQLite's ceiling (`utils.chunked`).

    The returned set also names the job: `BackgroundJob.dataset_id` is a single
    column, so it is only meaningful when the selection resolved to one dataset.
    """
    dataset_ids: set[str] = set()
    for batch in chunked(image_ids):
        rows = await db.execute(
            select(Image.dataset_id).where(Image.id.in_(list(batch))).distinct()
        )
        dataset_ids.update(r[0] for r in rows.all())
    for ds_id in dataset_ids:
        ensure_not_busy(ds_id)
    return dataset_ids


# ── Batch geometry ────────────────────────────────────────────────────────────
# Declaration order *is* the routing table. FastAPI matches routes in the order
# they are declared and `image_id: str` accepts the literal segment "batch", so
# these two have to stay **above** `/{image_id}/resize` and `/{image_id}/crop`.
# Declared after them they were simply unreachable — a POST to /batch/resize was
# answered by the single-image handler, which 404'd out of `db.get(Image,
# "batch")`, and /batch/crop 422'd on the single-crop body model (PM-018). Any
# future `/batch/*` route on this router belongs in this block too.


@router.post("/batch/resize")
async def batch_resize(body: BatchResizeRequest, db: AsyncSession = Depends(get_db)):
    dataset_ids = await _guard_batch_datasets(db, body.image_ids)
    auto_label = f"Batch resize — {len(body.image_ids)} images"
    job = BackgroundJob(
        job_type="batch_resize",
        label=body.label or auto_label,
        dataset_id=next(iter(dataset_ids)) if len(dataset_ids) == 1 else None,
        total_items=len(body.image_ids),
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.workers.progress import broadcaster
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Image).where(Image.id.in_(body.image_ids)))
            images = result.scalars().all()
            loop = asyncio.get_running_loop()

            # The whole outcome of the run, seeded like the LUT/upscale twins.
            # `skipped` starts at the rows that no longer exist (deleted between
            # enqueue and run); `thumbnails_stale` counts resized images whose
            # *preview* could not be recut — the image itself is correct and
            # committed, the gallery just keeps rendering the old tile.
            counts = {
                "processed": 0,
                "skipped": len(body.image_ids) - len(images),
                "failed": 0,
                "thumbnails_stale": 0,
            }

            cancelled = False
            for i, img in enumerate(images):
                if job_queue.cancel_requested(job_id):
                    cancelled = True
                    break
                await version_service.protect_file_before_overwrite(img.id, img.file_path, session)
                await session.commit()  # persist the COW hash backfill before the overwrite
                try:
                    new_w, new_h = await loop.run_in_executor(
                        None, resize_image, img.file_path, body.width, body.height,
                        body.scale, body.maintain_ar,
                    )
                except Exception as exc:
                    logger.error("Batch resize failed for %s: %s", img.filename, exc)
                    counts["failed"] += 1
                    await broadcaster.emit(job_id, {
                        "type": "progress", "job_id": job_id, "job_type": "batch_resize",
                        "status": "running", "done": i + 1, "total": len(images),
                        "percent": round((i + 1) / len(images) * 100, 1),
                        "current_item": img.filename,
                        "message": f"Failed: {exc}",
                    })
                    continue
                img.width, img.height = new_w, new_h
                _record_in_place(img, "resize", width=new_w, height=new_h)
                # Per image, with nothing fallible between the (already-done)
                # overwrite and it. The commit used to sit outside the loop, so a
                # raise on image N rolled back the geometry and processing_history
                # of images 1..N — every one of them already rewritten on disk
                # (PM-013).
                await session.commit()

                # --- epilogue: best-effort, cannot undo the resize ---
                # `expire_on_commit=False` (backend/database.py) keeps `img`
                # readable after the commit without a refresh.
                if img.thumbnail_path:
                    try:
                        await loop.run_in_executor(
                            None, generate_thumbnail, img.file_path, img.thumbnail_path
                        )
                    except Exception as exc:
                        counts["thumbnails_stale"] += 1
                        logger.warning(
                            "Batch resize: thumbnail for %s could not be regenerated: %s",
                            img.filename, exc,
                        )
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_resize",
                    "status": "running", "done": i + 1, "total": len(images),
                    "percent": round((i + 1) / len(images) * 100, 1),
                    "current_item": img.filename,
                })
                counts["processed"] += 1

            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = counts
            # Above `raise_if_cancelled`, so a cancelled run keeps the counts for
            # everything it did manage — including any stale thumbnail.
            await session.commit()
            if cancelled:
                job_queue.raise_if_cancelled(job_id)

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/batch/crop")
async def batch_crop(body: BatchCropRequest, db: AsyncSession = Depends(get_db)):
    dataset_ids = await _guard_batch_datasets(db, body.image_ids)
    auto_label = f"Batch crop — {body.target_ar:g} AR, {len(body.image_ids)} images"
    job = BackgroundJob(
        job_type="batch_crop",
        label=body.label or auto_label,
        dataset_id=next(iter(dataset_ids)) if len(dataset_ids) == 1 else None,
        total_items=len(body.image_ids),
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.workers.progress import broadcaster
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Image).where(Image.id.in_(body.image_ids)))
            images = result.scalars().all()
            loop = asyncio.get_running_loop()

            # Seeded and incremented exactly like the resize twin above.
            counts = {
                "processed": 0,
                "skipped": len(body.image_ids) - len(images),
                "failed": 0,
                "thumbnails_stale": 0,
            }

            cancelled = False
            for i, img in enumerate(images):
                if job_queue.cancel_requested(job_id):
                    cancelled = True
                    break
                await version_service.protect_file_before_overwrite(img.id, img.file_path, session)
                await session.commit()  # persist the COW hash backfill before the overwrite
                try:
                    new_w, new_h, rect, old_size = await loop.run_in_executor(
                        None, crop_to_aspect, img.file_path, body.target_ar, body.strategy
                    )
                except Exception as exc:
                    logger.error("Batch crop failed for %s: %s", img.filename, exc)
                    counts["failed"] += 1
                    await broadcaster.emit(job_id, {
                        "type": "progress", "job_id": job_id, "job_type": "batch_crop",
                        "status": "running", "done": i + 1, "total": len(images),
                        "percent": round((i + 1) / len(images) * 100, 1),
                        "current_item": img.filename,
                        "message": f"Failed: {exc}",
                    })
                    continue
                img.width, img.height = new_w, new_h
                _record_in_place(img, "crop_aspect", target_ar=body.target_ar, rect=list(rect))
                # Aspect crop changed geometry: remap this image's detections.
                # DB-only work, so it stays *above* the commit — the same place
                # `_run_crop_upscale_replace` puts it. Below this line the row and
                # the file on disk agree, durably.
                await remap_detections_for_crop(session, img.id, rect, old_size)
                await session.commit()

                # --- epilogue: best-effort, cannot undo the crop ---
                if img.thumbnail_path:
                    try:
                        await loop.run_in_executor(
                            None, generate_thumbnail, img.file_path, img.thumbnail_path
                        )
                    except Exception as exc:
                        counts["thumbnails_stale"] += 1
                        logger.warning(
                            "Batch crop: thumbnail for %s could not be regenerated: %s",
                            img.filename, exc,
                        )
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_crop",
                    "status": "running", "done": i + 1, "total": len(images),
                    "percent": round((i + 1) / len(images) * 100, 1),
                    "current_item": img.filename,
                })
                counts["processed"] += 1

            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = counts
            await session.commit()
            if cancelled:
                job_queue.raise_if_cancelled(job_id)

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/{image_id}/resize")
async def resize(image_id: str, body: ImageResizeRequest, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    ensure_not_busy(img.dataset_id)
    await version_service.protect_file_before_overwrite(image_id, img.file_path, db)
    await db.commit()  # persist the COW hash backfill before the overwrite
    new_w, new_h = await asyncio.get_running_loop().run_in_executor(
        None, resize_image, img.file_path, body.width, body.height, body.scale, body.maintain_ar, body.resample
    )
    img.width, img.height = new_w, new_h
    _record_in_place(img, "resize", width=new_w, height=new_h)
    # `resize_image` saved over `img.file_path` in place; nothing fallible may sit
    # between that irreversible write and this commit, or a raise rolls the row
    # back onto pixels that no longer match it (PM-013).
    await db.commit()

    # --- epilogue: best-effort, cannot undo the resize ---
    # `expire_on_commit=False` (backend/database.py) keeps `img` readable here.
    if img.thumbnail_path:
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, generate_thumbnail, img.file_path, img.thumbnail_path
            )
        except Exception as exc:
            # A stale thumbnail is cosmetic; the resized image is committed and
            # serves. This endpoint returns a dict rather than a job, so there is
            # no `thumbnails_stale` counter to raise — TopBar reads that off
            # `job.result_data`, and a new response field would be a second
            # reporting channel for the same fact.
            logger.warning(
                "Resize: thumbnail for %s could not be regenerated: %s", img.filename, exc
            )
    return {"width": new_w, "height": new_h}


@router.post("/{image_id}/crop")
async def crop(image_id: str, body: ImageCropRequest, db: AsyncSession = Depends(get_db)):
    # undefer: the new-file path copies this image's provenance onto the crop, and
    # source_meta is a deferred column (a lazy load here would be a MissingGreenlet).
    img = await db.get(Image, image_id, options=[undefer(Image.source_meta)])
    if not img:
        raise HTTPException(404, "Image not found")
    ensure_not_busy(img.dataset_id)

    src_path = Path(img.file_path)
    dest_images = src_path.parent
    dest_thumbs = src_path.parent.parent / "thumbnails"
    loop = asyncio.get_running_loop()

    # --- Replace mode: overwrite the original image ---
    if body.replace:
        if body.upscale_model:
            # The upscaler writes through `normalize_image_format`, which falls
            # back to PNG for .bmp/.gif/.tiff/.avif — so a replace of one of
            # those lands at a *different* path, and an unregistered file already
            # sitting there has no DB row guarding it. Unlike the LUT and upscale
            # batch jobs, which skip the image and keep going, this endpoint
            # handles exactly one image and can refuse before touching anything.
            _fmt, planned_out = normalize_image_format(src_path.suffix, str(src_path))
            if planned_out != str(src_path) and Path(planned_out).exists():
                raise HTTPException(
                    409,
                    f"Upscaling {src_path.name} writes {Path(planned_out).name}, "
                    "which already exists on disk. Rename or remove it first.",
                )
        await version_service.protect_file_before_overwrite(img.id, img.file_path, db)
        await db.commit()  # persist the COW hash backfill before the overwrite
        tmp_path = src_path.with_name(src_path.stem + "_croptmp" + src_path.suffix)

        if not body.upscale_model:
            # Synchronous replace crop
            old_size = (img.width, img.height)
            info = await loop.run_in_executor(
                None, crop_image_to_dest,
                str(src_path), str(tmp_path),
                body.x, body.y, body.width, body.height,
                body.output_width, body.output_height,
            )
            tmp_path.replace(src_path)
            img.width = info["width"]
            img.height = info["height"]
            img.file_size_bytes = info["file_size_bytes"]
            img.format = info["format"]
            img.phash = info["phash"]
            _record_in_place(img, "crop", rect=[body.x, body.y, body.width, body.height])
            # Replace crop changed geometry: remap this image's detections.
            await remap_detections_for_crop(
                db, img.id, (body.x, body.y, body.width, body.height), old_size
            )
            # `tmp_path.replace(src_path)` above is irreversible; nothing fallible
            # may precede this commit or the row rolls back onto the cropped file
            # (PM-013). The thumbnail regenerates in the epilogue below.
            await db.commit()

            # --- epilogue: best-effort, cannot undo the crop ---
            if img.thumbnail_path:
                try:
                    await loop.run_in_executor(
                        None, generate_thumbnail, str(src_path), img.thumbnail_path
                    )
                except Exception as exc:
                    # Cosmetic; the crop is committed and serves. No
                    # `thumbnails_stale` counter here — this endpoint returns a
                    # dict, not a job (see the resize endpoint above).
                    logger.warning(
                        "Crop: thumbnail for %s could not be regenerated: %s", img.filename, exc
                    )
            return {"id": img.id, "filename": img.filename, "width": img.width, "height": img.height}

        # Replace + upscale: crop to temp, enqueue job that upscales to original path
        old_size = (img.width, img.height)
        await loop.run_in_executor(
            None, crop_image_to_dest,
            str(src_path), str(tmp_path),
            body.x, body.y, body.width, body.height,
            body.output_width, body.output_height,
        )
        replace_cfg = {
            "tmp_path": str(tmp_path),
            "dest_path": str(src_path),
            "thumb_path": img.thumbnail_path or thumbnail_path_for(src_path),
            "image_id": img.id,
            "upscale_model": body.upscale_model,
            "upscale_target_width": body.upscale_target_width,
            "upscale_target_height": body.upscale_target_height,
            # For detection remap after a successful upscale (crop frame = old dims).
            "crop_rect": [body.x, body.y, body.width, body.height],
            "old_size": [old_size[0], old_size[1]],
        }
        job = BackgroundJob(job_type="crop_upscale", label="Crop + upscale", dataset_id=img.dataset_id, total_items=1, config=replace_cfg)
        db.add(job)
        await db.commit()

        async def _run_crop_upscale_replace(job_id: str) -> None:
            import os
            from backend.database import AsyncSessionLocal
            from backend.ml.upscaler import upscale_image_sync
            from backend.workers.progress import broadcaster

            loop2 = asyncio.get_running_loop()
            try:
                info = await loop2.run_in_executor(
                    None, upscale_image_sync,
                    replace_cfg["tmp_path"], replace_cfg["dest_path"], replace_cfg["upscale_model"], False,
                    replace_cfg["upscale_target_width"], replace_cfg["upscale_target_height"],
                )
            finally:
                try:
                    os.remove(replace_cfg["tmp_path"])
                except OSError:
                    pass

            # The PNG fallback may have written a different path than the one
            # asked for; the thumbnail has to be cut from the file that exists.
            # It is cut in the epilogue, after the row is committed: the crop has
            # already overwritten the original, so a raise here would leave the
            # row describing a file that is gone (PM-013).
            actual_out_path = info.get("out_path", replace_cfg["dest_path"])
            superseded: Path | None = None
            stale = 0

            async with AsyncSessionLocal() as session:
                updated = await session.get(Image, replace_cfg["image_id"])
                if updated:
                    updated.width = info["width"]
                    updated.height = info["height"]
                    updated.file_size_bytes = info["file_size_bytes"]
                    if actual_out_path != replace_cfg["dest_path"]:
                        # A .bmp read back as .png: the row has to follow the
                        # written file and the original has to go, or the dataset
                        # ends up with two files and one row (PM-009). The COW
                        # copy already exists — `protect_file_before_overwrite`
                        # ran before the crop — so the unlink is safe. The stem is
                        # unchanged, so the thumbnail and sidecar stay put. It
                        # happens after the commit, since `unlink` is fallible.
                        superseded = Path(replace_cfg["dest_path"])
                        updated.filename = Path(actual_out_path).name
                        updated.file_path = actual_out_path
                        updated.format = info["format"]
                    _record_in_place(
                        updated, "crop_upscale",
                        rect=replace_cfg["crop_rect"],
                        model=Path(replace_cfg["upscale_model"]).name,
                    )
                    # Remap detections only now that the upscale succeeded (a
                    # failed upscale raises above and never reaches here). The
                    # crop rect is in the OLD (pre-crop) transposed frame.
                    rect = replace_cfg["crop_rect"]
                    old_dims = replace_cfg["old_size"]
                    await remap_detections_for_crop(
                        session, replace_cfg["image_id"],
                        (rect[0], rect[1], rect[2], rect[3]),
                        (old_dims[0], old_dims[1]),
                    )
                    # DB-only work above may still roll back safely; below this
                    # line the row and the file on disk agree, durably.
                    await session.commit()

                    # --- epilogue: best-effort, cannot undo the crop+upscale ---
                    # `expire_on_commit=False` (backend/database.py) keeps
                    # `updated` readable after the commit without a refresh.
                    if superseded is not None:
                        try:
                            superseded.unlink(missing_ok=True)
                        except OSError as exc:
                            logger.warning(
                                "Crop+upscale: could not remove superseded %s: %s",
                                superseded.name, exc,
                            )
                    try:
                        await loop2.run_in_executor(
                            None, generate_thumbnail, actual_out_path, replace_cfg["thumb_path"]
                        )
                    except Exception as exc:
                        # A stale thumbnail is cosmetic; the image is committed
                        # and serves. Counted rather than merely logged so the
                        # run can say so: TopBar reads this count and points at
                        # Bulk Edit → Thumbnails.
                        stale = 1
                        logger.warning(
                            "Crop+upscale: thumbnail for %s could not be regenerated: %s",
                            Path(actual_out_path).name, exc,
                        )

                # Written and committed **inside** this `async with`, and
                # deliberately dedented out of `if updated:` so a vanished row
                # still reports. The emit below is this job's own terminal
                # `completed` event — TopBar's completion branch fires on it and
                # immediately fetches the job row, so anything a completion
                # handler will read has to be durable before the emit runs.
                # `job_queue` marks the row later, from its own session.
                job_row = await session.get(BackgroundJob, job_id)
                if job_row:
                    job_row.result_data = {
                        "processed": 1 if updated else 0,
                        "thumbnails_stale": stale,
                    }
                await session.commit()

            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "crop_upscale",
                "status": "completed", "done": 1, "total": 1, "percent": 100.0,
                "replace": True,
            })

        await job_queue.enqueue(job, _run_crop_upscale_replace)
        return {"job_id": job.id}

    # --- New-file mode (original behaviour) ---
    crop_stem = slugify_filename(src_path.stem + "_crop")
    existing = await db.execute(
        select(Image.filename).where(
            Image.dataset_id == img.dataset_id,
            Image.filename.like(f"{crop_stem}%"),
        )
    )
    db_names: set[str] = {r[0] for r in existing.all()}
    occupied_thumb_stems: set[str] = {p.stem for p in dest_thumbs.glob("*.webp")} if dest_thumbs.exists() else set()
    # A plain crop keeps the source extension — `crop_image_to_dest` saves by the
    # destination suffix and never falls back — but a crop+upscale is written by
    # `upscale_image_sync`, whose PNG fallback changes it. Reserve the name under
    # the extension that will actually be written, so both the db_names and the
    # on-disk check apply to the real path.
    dest_suffix = src_path.suffix
    if body.upscale_model:
        _fmt, planned_out = normalize_image_format(src_path.suffix, str(src_path))
        dest_suffix = Path(planned_out).suffix
    new_filename = unique_filename_with_thumb(
        dest_images, crop_stem, dest_suffix, db_names, occupied_thumb_stems, set()
    )
    dest_path = dest_images / new_filename

    # Crop + upscale path (async background job)
    if body.upscale_model:
        tmp_path = dest_images / (dest_path.stem + "_tmp" + dest_path.suffix)
        await loop.run_in_executor(
            None, crop_image_to_dest,
            str(src_path), str(tmp_path),
            body.x, body.y, body.width, body.height,
            body.output_width, body.output_height,
        )

        job_cfg = {
            "tmp_path": str(tmp_path),
            "dest_path": str(dest_path),
            "dest_thumbs": str(dest_thumbs),
            "new_filename": new_filename,
            "dataset_id": img.dataset_id,
            "original_filename": img.original_filename,
            "subfolder": img.subfolder,
            "upscale_model": body.upscale_model,
            "upscale_target_width": body.upscale_target_width,
            "upscale_target_height": body.upscale_target_height,
            # A derivative of a CC-BY-SA image is still CC-BY-SA — carry the
            # parent's raw provenance (same dataset, so inheritance still holds).
            "provenance": copy_provenance(img),
        }
        job = BackgroundJob(job_type="crop_upscale", label="Crop + upscale", dataset_id=img.dataset_id, total_items=1, config=job_cfg)
        db.add(job)
        await db.commit()

        async def _run_crop_upscale(job_id: str) -> None:
            import os
            from backend.database import AsyncSessionLocal
            from backend.ml.upscaler import upscale_image_sync
            from backend.workers.progress import broadcaster

            cfg = job_cfg
            tmp = cfg["tmp_path"]
            dst = cfg["dest_path"]
            loop2 = asyncio.get_running_loop()
            try:
                info = await loop2.run_in_executor(
                    None, upscale_image_sync,
                    tmp, dst, cfg["upscale_model"], False,
                    cfg["upscale_target_width"], cfg["upscale_target_height"],
                )
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

            # Everything downstream names the file that was written, not the one
            # requested: `generate_thumbnail` on a path the PNG fallback moved
            # would raise and fail the job, leaving an orphan and no row.
            actual_out_path = info.get("out_path", dst)
            thumb_path = str(Path(cfg["dest_thumbs"]) / (Path(actual_out_path).stem + ".webp"))
            await loop2.run_in_executor(None, generate_thumbnail, actual_out_path, thumb_path)

            async with AsyncSessionLocal() as session:
                new_img = Image(
                    dataset_id=cfg["dataset_id"],
                    filename=Path(actual_out_path).name,
                    original_filename=cfg["original_filename"],
                    subfolder=cfg["subfolder"],
                    file_path=actual_out_path,
                    thumbnail_path=thumb_path,
                    width=info["width"],
                    height=info["height"],
                    file_size_bytes=info["file_size_bytes"],
                    format=info["format"],
                    **cfg["provenance"],
                )
                session.add(new_img)
                await session.commit()
                await session.refresh(new_img)

            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "crop_upscale",
                "status": "completed", "done": 1, "total": 1, "percent": 100.0,
                "image_id": new_img.id, "filename": new_img.filename,
            })

        await job_queue.enqueue(job, _run_crop_upscale)
        return {"job_id": job.id}

    # Synchronous crop-only path
    info = await loop.run_in_executor(
        None, crop_image_to_dest,
        str(src_path), str(dest_path),
        body.x, body.y, body.width, body.height,
        body.output_width, body.output_height,
    )
    thumb_path = str(dest_thumbs / (Path(new_filename).stem + ".webp"))
    await loop.run_in_executor(None, generate_thumbnail, str(dest_path), thumb_path)

    new_img = Image(
        dataset_id=img.dataset_id,
        filename=new_filename,
        original_filename=img.original_filename,
        subfolder=img.subfolder,
        file_path=str(dest_path),
        thumbnail_path=thumb_path,
        **copy_provenance(img),
        **info,
    )
    db.add(new_img)
    await db.commit()
    await db.refresh(new_img)
    return {"id": new_img.id, "filename": new_img.filename, "width": new_img.width, "height": new_img.height}


@router.patch("/{image_id}/rename")
async def rename_image(image_id: str, body: RenameImageRequest, db: AsyncSession = Depends(get_db)):
    img = await db.get(Image, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    ensure_not_busy(img.dataset_id)

    raw = body.new_stem.strip()
    if not raw or "/" in raw or "\\" in raw or len(raw) > 200:
        raise HTTPException(400, "Invalid new_stem")
    slug = slugify_filename(raw)
    if not slug:
        raise HTTPException(400, "Stem produces empty slug")

    old_path = Path(img.file_path)
    # A row whose file was deleted underneath it used to run all the way to
    # `rename_with_sidecar`, which surfaced FileNotFoundError as a 500 with the
    # row already carrying the new name. Same answer `rename_video` gives.
    if not old_path.exists():
        raise HTTPException(404, "File not found on disk")
    existing = await db.execute(
        select(Image.filename).where(Image.dataset_id == img.dataset_id, Image.id != image_id)
    )
    db_names: set[str] = {r[0] for r in existing.all()}

    # Build occupied thumbnail stems, excluding the image's own current stem so a
    # rename that keeps the same stem (different extension is impossible here, but
    # guards against stale thumbnails from a prior rename) doesn't block itself.
    thumb_dir = old_path.parent.parent / "thumbnails"
    occupied_thumb_stems: set[str] = {p.stem for p in thumb_dir.glob("*.webp")} if thumb_dir.exists() else set()
    occupied_thumb_stems.discard(old_path.stem)
    planned_thumb_stems: set[str] = set()

    new_filename = unique_filename_with_thumb(
        old_path.parent, slug, old_path.suffix.lower(), db_names, occupied_thumb_stems, planned_thumb_stems
    )
    new_path = old_path.parent / new_filename
    old_thumb = Path(thumbnail_path_for(str(old_path)))
    new_thumb = Path(thumbnail_path_for(str(new_path)))

    img.filename = new_filename
    img.file_path = str(new_path)
    img.thumbnail_path = str(new_thumb)
    img.is_auto_named = False
    try:
        rename_with_sidecar(old_path, new_path)  # FS last — if this raises, commit never runs
    except FileNotFoundError:
        # Lost the TOCTOU race against the check above. Same answer either way.
        raise HTTPException(404, "File not found on disk") from None
    await db.commit()

    # Post-commit epilogue, per CLAUDE.md § *Nothing fallible between an
    # irreversible filesystem mutation and the `commit()`*. The thumbnail move
    # used to sit between the rename and the commit, so an OSError there threw
    # away a rename that had already happened on disk. `img.thumbnail_path` still
    # names `new_thumb` even when this fails — `serve_thumbnail` regenerates a
    # missing one, so the row states intent and the next view heals it.
    if old_thumb != new_thumb:
        try:
            if old_thumb.exists():  # inside the try — an lstat can raise too
                old_thumb.replace(new_thumb)
        except OSError:
            logger.warning(
                "rename_image %s: thumbnail move failed; it will be regenerated", image_id,
                exc_info=True,
            )
    return {"filename": new_filename}


@router.post("/batch/move-subfolder")
async def batch_move_subfolder(body: BatchMoveSubfolderRequest, db: AsyncSession = Depends(get_db)):
    target = normalize_subfolder(body.subfolder)
    result = await db.execute(
        select(Image.id, Image.filename, Image.file_path, Image.dataset_id)
        .where(Image.id.in_(body.image_ids))
    )
    rows = result.all()
    if not rows:
        raise HTTPException(404, "No matching images found")

    dataset_id = rows[0].dataset_id
    ensure_not_busy(dataset_id)
    images_dir = Path(rows[0].file_path).parent

    if body.rename_on_move:
        existing = await db.execute(select(Image.filename).where(Image.dataset_id == dataset_id))
        db_names: set[str] = {r[0] for r in existing.all()}

        target_stem = slugify_filename(target.replace("/", "_")) if target else "image"

        # All existing thumbnails are occupied — including those of images being moved,
        # because allowing a new assignment to reuse a moving image's stem would let
        # image A's new thumbnail overwrite image B's current thumbnail mid-execution.
        thumb_dir = images_dir.parent / "thumbnails"
        occupied_thumb_stems: set[str] = {p.stem for p in thumb_dir.glob("*.webp")} if thumb_dir.exists() else set()
        planned_thumb_stems: set[str] = set()

        # Build the full rename plan before touching anything.
        renames: list[tuple[Path, Path, Path, Path, str, str, str]] = []  # (old, new, old_thumb, new_thumb, id, new_fn, new_fp)
        for row in rows:
            old_path = Path(row.file_path)
            suf = old_path.suffix.lower()
            db_names.discard(row.filename)
            new_filename = unique_filename_with_thumb(
                images_dir, target_stem, suf, db_names, occupied_thumb_stems, planned_thumb_stems
            )
            new_path = images_dir / new_filename
            old_thumb = Path(thumbnail_path_for(str(old_path)))
            new_thumb = Path(thumbnail_path_for(str(new_path)))
            renames.append((old_path, new_path, old_thumb, new_thumb, row.id, new_filename, str(new_path)))

        # Apply all DB mutations in-memory (no commit yet).
        for old_path, new_path, _ot, new_thumb, img_id, new_fn, new_fp in renames:
            values: dict = dict(subfolder=target, filename=new_fn, file_path=new_fp, thumbnail_path=str(new_thumb))
            if new_path != old_path:
                values["is_auto_named"] = True
            await db.execute(sa_update(Image).where(Image.id == img_id).values(**values))

        # Perform filesystem renames — if any raise, the exception propagates and
        # db.commit() is never reached, so all DB mutations are rolled back.
        for old_path, new_path, old_thumb, new_thumb, *_ in renames:
            if new_path != old_path:
                rename_with_sidecar(old_path, new_path)
                if old_thumb.exists():
                    old_thumb.replace(new_thumb)
    else:
        # Just update the subfolder field; filenames stay unchanged.
        for row in rows:
            await db.execute(
                sa_update(Image).where(Image.id == row.id).values(subfolder=target)
            )

    await db.commit()
    return {"moved": len(rows), "subfolder": target}


@router.post("/batch/move-dataset")
async def batch_move_dataset(body: BatchMoveDatasetRequest, db: AsyncSession = Depends(get_db)):
    target = normalize_subfolder(body.subfolder)

    _move_cols = (Image.id, Image.filename, Image.file_path, Image.dataset_id, Image.thumbnail_path,
                  Image.sort_order, Image.created_at,
                  Image.source_name, Image.source_url, Image.license, Image.attribution)
    # Deliberately no `Image.source_meta`: a move does not change it, and the
    # materialize step below strips it back out — selecting it would load a
    # scraper's full raw payload per row only to discard it.
    if body.image_ids:
        result = await db.execute(
            select(*_move_cols).where(Image.id.in_(body.image_ids))
        )
        rows = result.all()
    elif body.source_dataset_id is not None and body.source_subfolder is not None:
        source_subfolder = normalize_subfolder(body.source_subfolder)
        result = await db.execute(
            select(*_move_cols)
            .where(Image.dataset_id == body.source_dataset_id)
            .where(Image.subfolder == source_subfolder)
        )
        rows = result.all()
    else:
        raise HTTPException(400, "Provide image_ids or source_dataset_id+source_subfolder")

    if not rows:
        raise HTTPException(404, "No matching images found")
    # A selection can span datasets, so every source is handled individually —
    # for the busy guard, the provenance below, and the stats refresh at the end.
    source_dataset_ids = {row.dataset_id for row in rows}
    if body.target_dataset_id in source_dataset_ids:
        raise HTTPException(400, "Source and target dataset must differ")
    for src_id in source_dataset_ids:
        ensure_not_busy(src_id)
    ensure_not_busy(body.target_dataset_id)

    target_ds_result = await db.execute(select(Dataset).where(Dataset.id == body.target_dataset_id))
    target_dataset = target_ds_result.scalar_one_or_none()
    if not target_dataset:
        raise HTTPException(404, "Target dataset not found")

    # An image whose provenance was inherited from the source dataset must have
    # it written out as concrete values here, or it silently re-inherits the
    # target dataset's unrelated default.
    source_datasets = {
        ds.id: ds for ds in (await db.execute(
            select(Dataset).where(Dataset.id.in_(source_dataset_ids))
        )).scalars()
    }

    target_images_dir = Path(target_dataset.folder_path) / "images"
    target_thumb_dir = Path(target_dataset.folder_path) / "thumbnails"
    target_images_dir.mkdir(parents=True, exist_ok=True)
    target_thumb_dir.mkdir(parents=True, exist_ok=True)

    existing = await db.execute(select(Image.filename).where(Image.dataset_id == body.target_dataset_id))
    db_names: set[str] = {r[0] for r in existing.all()}

    # Determine target sort_order assignment: preserve moved images' relative sequence.
    # Query target order state before the move rows arrive.
    target_order_result = await db.execute(
        select(func.count(Image.id), func.count(Image.sort_order), func.max(Image.sort_order))
        .where(Image.dataset_id == body.target_dataset_id)
    )
    target_total, target_ordered, target_max = target_order_result.one()
    if target_total == 0:
        # Empty target: start a fresh custom order for the arriving images.
        next_sort_order: int | None = 0
    elif target_total == target_ordered:
        # Target is fully ordered: append moved images after the last position.
        next_sort_order = (target_max or 0) + 1
    else:
        # Target has mixed ordering; don't introduce more partial order.
        next_sort_order = None

    # Sort moved images by their source relative order so they arrive in the same sequence.
    rows = sorted(rows, key=lambda r: (r.sort_order is None, r.sort_order or 0, r.created_at))

    target_stem = slugify_filename(target.replace("/", "_")) if target else "image"

    occupied_thumb_stems: set[str] = {p.stem for p in target_thumb_dir.glob("*.webp")}
    planned_thumb_stems: set[str] = set()

    # Resolved against each row's *own* source dataset, so a move preserves the
    # license the image actually had. source_meta is untouched — a move doesn't
    # change it.
    materialized: dict[str, dict] = {
        img_id: {f: v for f, v in values.items() if f != "source_meta"}
        for img_id, values in materialize_by_source(rows, source_datasets).items()
    }

    # (old_path, new_path, old_thumb, new_thumb, img_id, new_fn, assigned_sort_order)
    plan: list[tuple[Path, Path, Path, Path, str, str, int | None]] = []
    for idx, row in enumerate(rows):
        old_path = Path(row.file_path)
        suf = old_path.suffix.lower()
        new_fn = unique_filename_with_thumb(
            target_images_dir, target_stem, suf, db_names, occupied_thumb_stems, planned_thumb_stems
        )
        new_path = target_images_dir / new_fn
        old_thumb = Path(thumbnail_path_for(str(old_path)))
        new_thumb = Path(thumbnail_path_for(str(new_path)))
        assigned_order = (next_sort_order + idx) if next_sort_order is not None else None
        plan.append((old_path, new_path, old_thumb, new_thumb, row.id, new_fn, assigned_order))

    # Moving an image out of its dataset removes it from that dataset's history —
    # back its content into the source object store first so pre-move snapshots
    # can still restore it. Must run while dataset_id still points at the source.
    for old_path, _new_path, _old_thumb, _new_thumb, img_id, _new_fn, _order in plan:
        await version_service.mark_image_deleted_in_versions(img_id, str(old_path), db)

    for old_path, new_path, old_thumb, new_thumb, img_id, new_fn, assigned_order in plan:
        await db.execute(
            sa_update(Image).where(Image.id == img_id).values(
                dataset_id=body.target_dataset_id,
                subfolder=target,
                filename=new_fn,
                file_path=str(new_path),
                thumbnail_path=str(new_thumb),
                is_auto_named=True,
                sort_order=assigned_order,
                # A move is an UPDATE in place, so lineage survives unless it is
                # explicitly cleared: the row would land in the target dataset
                # still pointing at a video the target does not contain. The
                # timestamp and shot index stay — they are facts about the frame,
                # not about which dataset holds it.
                source_video_id=None,
                **materialized[img_id],
            )
        )

    # Commit DB before filesystem operations so the DB always reflects the intended
    # final state. If a rename fails mid-batch the images that were already moved
    # remain accessible; only not-yet-moved files are temporarily at the wrong path.
    await db.commit()

    for old_path, new_path, old_thumb, new_thumb, *_ in plan:
        rename_with_sidecar(old_path, new_path)
        if old_thumb.exists():
            shutil.copy2(old_thumb, new_thumb)
            old_thumb.unlink()

    for src_id in source_dataset_ids:
        await refresh_stats(db, src_id)
    await refresh_stats(db, body.target_dataset_id)
    return {"moved": len(rows), "target_dataset_id": body.target_dataset_id}


@router.post("/batch/copy-dataset", response_model=BatchCopyDatasetResult)
async def batch_copy_dataset(body: BatchMoveDatasetRequest, db: AsyncSession = Depends(get_db)):
    target = normalize_subfolder(body.subfolder)

    cols = (
        Image.id, Image.filename, Image.file_path, Image.dataset_id, Image.thumbnail_path,
        Image.original_filename, Image.width, Image.height, Image.file_size_bytes, Image.format,
        Image.phash, Image.caption_text, Image.caption_style, Image.captioned_by, Image.captioned_at,
        Image.quality_flags, Image.nsfw_score, Image.aesthetic_score, Image.blur_score,
        Image.noise_score, Image.uniformity_score, Image.watermark_score, Image.color_score,
        Image.saturation_score, Image.luminance_score, Image.style_similarity_score,
        Image.scores_stale, Image.dino_layer_scores,
        Image.generation_metadata, Image.processing_history, Image.sort_order, Image.created_at,
        Image.source_name, Image.source_url, Image.license, Image.attribution,
        Image.source_meta,
        # Frame lineage. Deliberately no `Image.source_video_id`: the copy lands
        # in another dataset, where that id would point at a video the target
        # does not contain — the copies get NULL. The timestamp and shot index
        # are facts about the frame and travel with it.
        Image.source_timestamp_ms, Image.source_shot_index,
    )

    if body.image_ids:
        result = await db.execute(select(*cols).where(Image.id.in_(body.image_ids)))
        rows = result.all()
    elif body.source_dataset_id is not None and body.source_subfolder is not None:
        source_subfolder = normalize_subfolder(body.source_subfolder)
        result = await db.execute(
            select(*cols)
            .where(Image.dataset_id == body.source_dataset_id)
            .where(Image.subfolder == source_subfolder)
        )
        rows = result.all()
    else:
        raise HTTPException(400, "Provide image_ids or source_dataset_id+source_subfolder")

    if not rows:
        raise HTTPException(404, "No matching images found")
    # Same as batch_move_dataset: the selection may span datasets.
    source_dataset_ids = {row.dataset_id for row in rows}
    if body.target_dataset_id in source_dataset_ids:
        raise HTTPException(400, "Source and target dataset must differ")
    for src_id in source_dataset_ids:
        ensure_not_busy(src_id)
    ensure_not_busy(body.target_dataset_id)

    target_ds_result = await db.execute(select(Dataset).where(Dataset.id == body.target_dataset_id))
    target_dataset = target_ds_result.scalar_one_or_none()
    if not target_dataset:
        raise HTTPException(404, "Target dataset not found")

    # Same rule as batch_move_dataset: inherited provenance is materialized
    # against each row's own source dataset so the copy keeps its real license.
    source_datasets = {
        ds.id: ds for ds in (await db.execute(
            select(Dataset).where(Dataset.id.in_(source_dataset_ids))
        )).scalars()
    }
    materialized = materialize_by_source(rows, source_datasets)

    target_images_dir = Path(target_dataset.folder_path) / "images"
    target_thumb_dir = Path(target_dataset.folder_path) / "thumbnails"
    target_images_dir.mkdir(parents=True, exist_ok=True)
    target_thumb_dir.mkdir(parents=True, exist_ok=True)

    existing = await db.execute(select(Image.filename).where(Image.dataset_id == body.target_dataset_id))
    db_names: set[str] = {r[0] for r in existing.all()}

    # Sort copied images by their source relative order so filenames are assigned in sequence.
    rows = sorted(rows, key=lambda r: (r.sort_order is None, r.sort_order or 0, r.created_at))

    # Determine target sort_order assignment: append after target's existing order rather than
    # copying raw source values, which would interleave copies with target's existing images.
    target_order_result = await db.execute(
        select(func.count(Image.id), func.count(Image.sort_order), func.max(Image.sort_order))
        .where(Image.dataset_id == body.target_dataset_id)
    )
    target_total, target_ordered, target_max = target_order_result.one()
    if target_total == 0:
        next_sort_order: int | None = 0
    elif target_total == target_ordered:
        next_sort_order = (target_max or 0) + 1
    else:
        next_sort_order = None

    target_stem = slugify_filename(target.replace("/", "_")) if target else "image"

    occupied_thumb_stems: set[str] = {p.stem for p in target_thumb_dir.glob("*.webp")}
    planned_thumb_stems: set[str] = set()

    plan: list[tuple[Path, Path, Path, Path, Any, int | None]] = []
    for idx, row in enumerate(rows):
        old_path = Path(row.file_path)
        suf = old_path.suffix.lower()
        new_fn = unique_filename_with_thumb(
            target_images_dir, target_stem, suf, db_names, occupied_thumb_stems, planned_thumb_stems
        )
        new_path = target_images_dir / new_fn
        old_thumb = Path(thumbnail_path_for(str(old_path)))
        new_thumb = Path(thumbnail_path_for(str(new_path)))
        assigned_order = (next_sort_order + idx) if next_sort_order is not None else None
        plan.append((old_path, new_path, old_thumb, new_thumb, row, assigned_order))

    for old_path, new_path, old_thumb, new_thumb, row, assigned_order in plan:
        db.add(Image(
            id=str(uuid4()),
            dataset_id=body.target_dataset_id,
            subfolder=target,
            filename=new_path.name,
            original_filename=row.original_filename,
            file_path=str(new_path),
            thumbnail_path=str(new_thumb),
            is_auto_named=True,
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
            nsfw_score=row.nsfw_score,
            aesthetic_score=row.aesthetic_score,
            blur_score=row.blur_score,
            noise_score=row.noise_score,
            uniformity_score=row.uniformity_score,
            watermark_score=row.watermark_score,
            color_score=row.color_score,
            saturation_score=row.saturation_score,
            luminance_score=row.luminance_score,
            style_similarity_score=row.style_similarity_score,
            scores_stale=row.scores_stale,
            dino_layer_scores=row.dino_layer_scores,
            generation_metadata=row.generation_metadata,
            processing_history=row.processing_history,
            sort_order=assigned_order,
            source_video_id=None,
            source_timestamp_ms=row.source_timestamp_ms,
            source_shot_index=row.source_shot_index,
            **materialized[row.id],
        ))

    for old_path, new_path, old_thumb, new_thumb, *_ in plan:
        copy_with_sidecar(old_path, new_path)
        if old_thumb.exists():
            shutil.copy2(old_thumb, new_thumb)

    await db.commit()
    await refresh_stats(db, body.target_dataset_id)
    return {"copied": len(rows), "target_dataset_id": body.target_dataset_id}

