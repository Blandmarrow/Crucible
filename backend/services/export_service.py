import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image as PilImage, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Image
from backend.models.detection import Detection
from backend.utils import chunked
from backend.workers.job_queue import job_queue

# Only the columns the export loop actually reads — avoids loading multi-MB blob fields
_EXPORT_COLS = (
    Image.id,
    Image.file_path,
    Image.filename,
    Image.subfolder,
    Image.caption_text,
    Image.aesthetic_score,
    Image.quality_flags,
    Image.style_similarity_score,
)


def _caption_text(img: Any) -> str:
    return img.caption_text or ""


def _is_excluded(
    img: Any,
    aesthetic_min: float | None,
    captioned_only: bool,
    exclude_flags: list[str],
    style_sim_min: float | None,
) -> bool:
    if aesthetic_min is not None and (img.aesthetic_score is None or img.aesthetic_score < aesthetic_min):
        return True
    if captioned_only and not img.caption_text:
        return True
    if exclude_flags:
        flags = img.quality_flags or {}
        if any(flags.get(f) for f in exclude_flags):
            return True
    if style_sim_min is not None and (img.style_similarity_score is None or img.style_similarity_score < style_sim_min):
        return True
    return False


def _unique_stem(stem: str, used: set[str]) -> str:
    """Return a stem not already in ``used``, adding ``_001``, ``_002``, … on collision.

    Mutates ``used`` (adds the chosen stem). Deliberately **not**
    ``utils.unique_filename``: that also probes the filesystem, which would rename
    every file on a re-export into the same directory. This guards only against
    collisions *within one export run* so an image, its caption sidecar, and its
    mask stay a consistent triple even when two source images share a stem (e.g.
    ``same.png`` and ``same.jpg``).
    """
    if stem not in used:
        used.add(stem)
        return stem
    i = 1
    while True:
        cand = f"{stem}_{i:03d}"
        if cand not in used:
            used.add(cand)
            return cand
        i += 1


def _write_image(
    src: Path,
    dest_img: Path,
    output_format: str,
    jpeg_quality: int,
    resize_to: int | None,
    strip_metadata: bool = False,
    need_size: bool = True,
) -> tuple[int, int] | None:
    """Write the exported image and return its final (width, height).

    Returns ``None`` when ``need_size`` is False and the fast-copy branch is taken
    — the caller only needs the size to rasterize a loss mask, so when masks are
    off we skip the PIL probe entirely (a corrupt-but-copyable file still exports).
    """
    if resize_to is None and output_format == "original" and not strip_metadata:
        if not need_size:
            # No mask to size — copy the bytes without opening the file in PIL.
            shutil.copy2(src, dest_img)
            return None
        # Metadata-only size read: exif_transpose would decode the pixels, so
        # swap dimensions from the EXIF orientation tag instead.
        with PilImage.open(src) as probe:
            w, h = probe.size
            try:
                orientation = probe.getexif().get(0x0112)
            except Exception:
                orientation = None
        if orientation in (5, 6, 7, 8):
            w, h = h, w
        shutil.copy2(src, dest_img)
        return (w, h)

    img = PilImage.open(src)
    try:
        img = ImageOps.exif_transpose(img)

        if resize_to and max(img.size) > resize_to:
            ratio = resize_to / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), PilImage.Resampling.LANCZOS)
        final_size = img.size

        if output_format == "jpeg":
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(dest_img, "JPEG", quality=jpeg_quality)
        elif output_format == "png":
            img.save(dest_img, "PNG")
        else:
            fmt = src.suffix.lstrip(".").upper()
            if fmt == "JPG":
                fmt = "JPEG"
            if fmt == "JPEG" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(dest_img, fmt, quality=jpeg_quality)
        return final_size
    finally:
        img.close()


def _dest_img_path(dest_dir: Path, img: Any, output_format: str) -> Path:
    src = Path(img.file_path)
    if output_format == "png":
        return dest_dir / (src.stem + ".png")
    if output_format == "jpeg":
        return dest_dir / (src.stem + ".jpg")
    return dest_dir / img.filename


def _write_sidecar(dest_dir: Path, stem: str, caption: str, caption_format: str) -> None:
    ext = ".caption" if caption_format == "caption" else ".txt"
    (dest_dir / f"{stem}{ext}").write_text(caption, encoding="utf-8")


def _write_mask(
    mask_path: Path,
    include: list[tuple[str | None, list[float] | None]],
    exclude: list[tuple[str | None, list[float] | None]],
    size: tuple[int, int],
    invert: bool,
) -> None:
    """Write the grayscale loss mask for one exported image.

    Thin wrapper over :func:`compose_loss_mask`: the include detections define
    the trainable region (full-white when empty, so the image trains unmasked),
    and the excluded regions are punched black after any invert.
    """
    from backend.ml.mask_utils import compose_loss_mask

    compose_loss_mask(include, exclude, size[0], size[1], invert).save(mask_path, "PNG")


async def _fetch_detections_by_image(
    db: AsyncSession,
    image_ids: list[str],
    mask_labels: list[str] | None,
    mask_exclude_labels: list[str] | None = None,
) -> tuple[
    dict[str, list[tuple[str | None, list[float] | None]]],
    dict[str, list[tuple[str | None, list[float] | None]]],
]:
    """Batch-fetch (mask_json, bbox) pairs keyed by image id, split into
    (include_by_image, exclude_by_image).

    One query per 10k chunk. When ``mask_labels`` is set the WHERE filter is
    ``label IN (include ∪ exclude)``; when it is None no label filter is applied
    (every detection is a potential include) and only ``mask_exclude_labels``
    are peeled off into the exclude map. Exclusion wins: a row whose label is in
    the exclude set goes to ``exclude_by_image`` and never to the include map.
    """
    exclude_set = set(mask_exclude_labels or [])
    include_by_image: dict[str, list[tuple[str | None, list[float] | None]]] = {}
    exclude_by_image: dict[str, list[tuple[str | None, list[float] | None]]] = {}
    for chunk in chunked(image_ids):
        query = select(
            Detection.image_id, Detection.label, Detection.mask, Detection.bbox
        ).where(Detection.image_id.in_(chunk))
        if mask_labels:
            query = query.where(Detection.label.in_(set(mask_labels) | exclude_set))
        result = await db.execute(query)
        for row in result.all():
            geom = (row.mask, row.bbox)
            if row.label in exclude_set:
                exclude_by_image.setdefault(row.image_id, []).append(geom)
            else:
                include_by_image.setdefault(row.image_id, []).append(geom)
    return include_by_image, exclude_by_image


async def _run_export_loop(
    db: AsyncSession,
    dataset_id: str,
    image_ids: list[str] | None,
    dest_dir: Path,
    output_format: str,
    jpeg_quality: int,
    resize_to: int | None,
    aesthetic_min: float | None,
    captioned_only: bool,
    exclude_flags: list[str],
    style_sim_min: float | None,
    job_id: str | None,
    job_type: str,
    caption_format: str | None,
    accumulate_plain: bool = False,
    subfolders: list[str] | None = None,
    strip_metadata: bool = False,
    captions_only: bool = False,
    mask_dir: Path | None = None,
    mask_labels: list[str] | None = None,
    mask_exclude_labels: list[str] | None = None,
    mask_invert: bool = False,
    mask_missing: str = "white",
) -> dict:
    """
    Shared export loop. Returns a dict with 'exported', 'output_dir', and optionally
    'jsonl_entries' and 'csv_rows' when accumulate_plain=True. When mask_dir is set
    (and captions_only is not), a grayscale loss-mask PNG is written per exported
    image and the mask counters are included in the result.
    """
    from backend.workers.progress import broadcaster

    query = select(*_EXPORT_COLS).where(Image.dataset_id == dataset_id)
    if image_ids:
        query = query.where(Image.id.in_(image_ids))
    if subfolders is not None:
        query = query.where(Image.subfolder.in_(subfolders))
    query = query.order_by(Image.sort_order.asc().nulls_last(), Image.created_at.asc())
    result = await db.execute(query)
    images = result.all()

    export_masks = mask_dir is not None and not captions_only
    detections_by_image: dict[str, list[tuple[str | None, list[float] | None]]] = {}
    exclude_by_image: dict[str, list[tuple[str | None, list[float] | None]]] = {}
    if export_masks:
        detections_by_image, exclude_by_image = await _fetch_detections_by_image(
            db, [img.id for img in images], mask_labels, mask_exclude_labels
        )

    jsonl_entries: list[dict] = []
    used_stems: set[str] = set()
    exported = 0
    masks_written = 0
    masks_full_white = 0
    excluded_no_mask = 0

    for i, img in enumerate(images):
        if job_id:
            job_queue.raise_if_cancelled(job_id)
        src = Path(img.file_path)
        if not captions_only and not src.exists():
            continue
        if _is_excluded(img, aesthetic_min, captioned_only, exclude_flags, style_sim_min):
            continue
        if export_masks and mask_missing == "skip" and not detections_by_image.get(img.id):
            excluded_no_mask += 1
            continue

        if captions_only:
            # Don't read or write image files; use original filename for any manifest entries.
            dest_img = dest_dir / img.filename
            dest_img = dest_img.with_stem(_unique_stem(dest_img.stem, used_stems))
        else:
            dest_img = _dest_img_path(dest_dir, img, output_format)
            # Uniquify within this run so two source images sharing a stem don't
            # clobber each other's image/caption/mask (all derive from dest_img).
            dest_img = dest_img.with_stem(_unique_stem(dest_img.stem, used_stems))
            final_size = await asyncio.get_event_loop().run_in_executor(
                None, _write_image, src, dest_img, output_format, jpeg_quality, resize_to, strip_metadata, export_masks
            )
            if export_masks:
                dets = detections_by_image.get(img.id) or []
                excl = exclude_by_image.get(img.id) or []
                await asyncio.get_event_loop().run_in_executor(
                    None, _write_mask, mask_dir / (dest_img.stem + ".png"), dets, excl, final_size, mask_invert
                )
                masks_written += 1
                if not dets:
                    masks_full_white += 1

        caption = _caption_text(img)

        if accumulate_plain:
            jsonl_entries.append({"file": dest_img.name, "caption": caption})
        elif caption_format == "jsonl":
            jsonl_entries.append({"file": dest_img.name, "caption": caption})
        else:
            _write_sidecar(dest_dir, dest_img.stem, caption, caption_format or "txt")

        exported += 1

        if job_id and i % 5 == 0:
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": job_type,
                "status": "running", "done": exported, "total": len(images),
                "percent": round((i + 1) / len(images) * 100, 1),
                "current_item": img.filename, "message": f"Exporting {img.filename}",
            })

    loop_result: dict = {
        "exported": exported,
        "jsonl_entries": jsonl_entries,
    }
    if export_masks:
        loop_result["masks_written"] = masks_written
        loop_result["masks_full_white"] = masks_full_white
        if mask_missing == "skip":
            loop_result["excluded_no_mask"] = excluded_no_mask
    return loop_result


_MASK_RESULT_KEYS = ("masks_written", "masks_full_white", "excluded_no_mask")


def _mask_dir_for(
    base: Path, export_masks: bool, captions_only: bool
) -> Path | None:
    if not export_masks or captions_only:
        return None
    base.mkdir(parents=True, exist_ok=True)
    return base


async def export_kohya(
    db: AsyncSession,
    dataset_id: str,
    output_dir: str,
    n_repeats: int = 10,
    concept_token: str = "concept",
    image_ids: list[str] | None = None,
    output_format: str = "original",
    jpeg_quality: int = 95,
    caption_format: str = "txt",
    resize_to: int | None = None,
    aesthetic_min: float | None = None,
    captioned_only: bool = False,
    exclude_flags: list[str] | None = None,
    style_sim_min: float | None = None,
    subfolders: list[str] | None = None,
    strip_metadata: bool = False,
    captions_only: bool = False,
    export_masks: bool = False,
    mask_labels: list[str] | None = None,
    mask_exclude_labels: list[str] | None = None,
    mask_invert: bool = False,
    mask_missing: str = "white",
    job_id: str | None = None,
) -> dict:
    exclude_flags = exclude_flags or []
    dest = Path(output_dir) / f"{n_repeats}_{concept_token}"
    dest.mkdir(parents=True, exist_ok=True)
    # Sibling conditioning_data_dir, mirroring the kohya masked-loss docs layout
    mask_dir = _mask_dir_for(
        Path(output_dir) / f"{n_repeats}_{concept_token}_mask", export_masks, captions_only
    )

    loop_result = await _run_export_loop(
        db, dataset_id, image_ids, dest, output_format, jpeg_quality,
        resize_to, aesthetic_min, captioned_only, exclude_flags, style_sim_min,
        job_id, "export", caption_format, subfolders=subfolders, strip_metadata=strip_metadata,
        captions_only=captions_only, mask_dir=mask_dir, mask_labels=mask_labels,
        mask_exclude_labels=mask_exclude_labels,
        mask_invert=mask_invert, mask_missing=mask_missing,
    )

    if caption_format == "jsonl" and loop_result["jsonl_entries"]:
        out = Path(output_dir) / "captions.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for entry in loop_result["jsonl_entries"]:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    result = {"exported": loop_result["exported"], "output_dir": str(dest)}
    if mask_dir is not None:
        result["mask_dir"] = str(mask_dir)
    result.update({k: loop_result[k] for k in _MASK_RESULT_KEYS if k in loop_result})
    return result


async def export_aitoolkit(
    db: AsyncSession,
    dataset_id: str,
    output_dir: str,
    concept_name: str = "concept",
    image_ids: list[str] | None = None,
    output_format: str = "original",
    jpeg_quality: int = 95,
    caption_format: str = "txt",
    resize_to: int | None = None,
    aesthetic_min: float | None = None,
    captioned_only: bool = False,
    exclude_flags: list[str] | None = None,
    style_sim_min: float | None = None,
    subfolders: list[str] | None = None,
    strip_metadata: bool = False,
    captions_only: bool = False,
    export_masks: bool = False,
    mask_labels: list[str] | None = None,
    mask_exclude_labels: list[str] | None = None,
    mask_invert: bool = False,
    mask_missing: str = "white",
    job_id: str | None = None,
) -> dict:
    exclude_flags = exclude_flags or []
    dest = Path(output_dir) / concept_name
    dest.mkdir(parents=True, exist_ok=True)
    # Sibling folder for the ai-toolkit dataset config's mask_path
    mask_dir = _mask_dir_for(
        Path(output_dir) / f"{concept_name}_mask", export_masks, captions_only
    )

    loop_result = await _run_export_loop(
        db, dataset_id, image_ids, dest, output_format, jpeg_quality,
        resize_to, aesthetic_min, captioned_only, exclude_flags, style_sim_min,
        job_id, "export", caption_format, subfolders=subfolders, strip_metadata=strip_metadata,
        captions_only=captions_only, mask_dir=mask_dir, mask_labels=mask_labels,
        mask_exclude_labels=mask_exclude_labels,
        mask_invert=mask_invert, mask_missing=mask_missing,
    )

    if caption_format == "jsonl" and loop_result["jsonl_entries"]:
        out = Path(output_dir) / "captions.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for entry in loop_result["jsonl_entries"]:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    result = {"exported": loop_result["exported"], "output_dir": str(dest)}
    if mask_dir is not None:
        result["mask_dir"] = str(mask_dir)
    result.update({k: loop_result[k] for k in _MASK_RESULT_KEYS if k in loop_result})
    return result


async def export_plain(
    db: AsyncSession,
    dataset_id: str,
    output_dir: str,
    image_ids: list[str] | None = None,
    output_format: str = "original",
    jpeg_quality: int = 95,
    resize_to: int | None = None,
    aesthetic_min: float | None = None,
    captioned_only: bool = False,
    exclude_flags: list[str] | None = None,
    style_sim_min: float | None = None,
    subfolders: list[str] | None = None,
    strip_metadata: bool = False,
    captions_only: bool = False,
    export_masks: bool = False,
    mask_labels: list[str] | None = None,
    mask_exclude_labels: list[str] | None = None,
    mask_invert: bool = False,
    mask_missing: str = "white",
    job_id: str | None = None,
) -> dict:
    exclude_flags = exclude_flags or []
    out = Path(output_dir)
    if captions_only:
        dest_dir = out
    else:
        dest_dir = out / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    mask_dir = _mask_dir_for(out / "masks", export_masks, captions_only)

    loop_result = await _run_export_loop(
        db, dataset_id, image_ids, dest_dir, output_format, jpeg_quality,
        resize_to, aesthetic_min, captioned_only, exclude_flags, style_sim_min,
        job_id, "export", None, accumulate_plain=True, subfolders=subfolders,
        strip_metadata=strip_metadata, captions_only=captions_only,
        mask_dir=mask_dir, mask_labels=mask_labels,
        mask_exclude_labels=mask_exclude_labels,
        mask_invert=mask_invert, mask_missing=mask_missing,
    )

    jsonl_path = Path(output_dir) / "captions.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for entry in loop_result["jsonl_entries"]:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    result = {"exported": loop_result["exported"], "output_dir": output_dir}
    if mask_dir is not None:
        result["mask_dir"] = str(mask_dir)
    result.update({k: loop_result[k] for k in _MASK_RESULT_KEYS if k in loop_result})
    return result


async def preview_export(
    db: AsyncSession,
    dataset_id: str,
    aesthetic_min: float | None = None,
    captioned_only: bool = False,
    exclude_flags: list[str] | None = None,
    style_sim_min: float | None = None,
    subfolders: list[str] | None = None,
    export_masks: bool = False,
    mask_labels: list[str] | None = None,
    mask_exclude_labels: list[str] | None = None,
    mask_missing: str = "white",
) -> dict:
    exclude_flags = exclude_flags or []

    query = select(
        Image.id, Image.filename, Image.caption_text,
        Image.aesthetic_score, Image.quality_flags, Image.style_similarity_score,
    ).where(Image.dataset_id == dataset_id)
    if subfolders is not None:
        query = query.where(Image.subfolder.in_(subfolders))
    result = await db.execute(query)
    rows = result.all()

    exclude_set = set(mask_exclude_labels or [])
    ids_with_detections: set[str] = set()
    if export_masks:
        # Effective include: an image counts as "with detections" only if it has
        # an include-label detection. Exclusion wins, so exclude-only images are
        # "without detections" (mirrors _fetch_detections_by_image in the export).
        run_det_query = True
        det_query = (
            select(Detection.image_id)
            .join(Image, Detection.image_id == Image.id)
            .where(Image.dataset_id == dataset_id)
            .distinct()
        )
        if mask_labels:
            effective = [l for l in mask_labels if l not in exclude_set]
            if effective:
                det_query = det_query.where(Detection.label.in_(effective))
            else:
                run_det_query = False  # every include label is also excluded
        elif exclude_set:
            det_query = det_query.where(Detection.label.notin_(list(exclude_set)))
        if run_det_query:
            det_result = await db.execute(det_query)
            ids_with_detections = {r.image_id for r in det_result.all()}

    total = len(rows)
    will_export = 0
    excl_aesthetic = 0
    excl_uncaptioned = 0
    excl_flagged = 0
    excl_style_sim = 0
    without_detections = 0
    sample_files: list[dict] = []

    for r in rows:
        low_aes = aesthetic_min is not None and (r.aesthetic_score is None or r.aesthetic_score < aesthetic_min)
        no_cap = captioned_only and not r.caption_text
        flagged = bool(exclude_flags) and any((r.quality_flags or {}).get(f) for f in exclude_flags)
        low_sim = style_sim_min is not None and (r.style_similarity_score is None or r.style_similarity_score < style_sim_min)

        if low_aes:
            excl_aesthetic += 1
        if no_cap:
            excl_uncaptioned += 1
        if flagged:
            excl_flagged += 1
        if low_sim:
            excl_style_sim += 1

        if not (low_aes or no_cap or flagged or low_sim):
            no_det = export_masks and r.id not in ids_with_detections
            if no_det:
                without_detections += 1
                # skip policy: these images are excluded from the export entirely
                if mask_missing == "skip":
                    continue
            will_export += 1
            if len(sample_files) < 5:
                caption = r.caption_text or ""
                sample_files.append({"image": r.filename, "caption_preview": caption[:80]})

    preview: dict = {
        "image_count": total,
        "will_export": will_export,
        "captioned_count": sum(1 for r in rows if r.caption_text),
        "excluded_low_aesthetic": excl_aesthetic,
        "excluded_uncaptioned": excl_uncaptioned,
        "excluded_flagged": excl_flagged,
        "excluded_style_sim": excl_style_sim,
        "sample_files": sample_files,
    }
    if export_masks:
        preview["images_without_detections"] = without_detections
    return preview
