import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image as PilImage, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Image

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


def _write_image(
    src: Path,
    dest_img: Path,
    output_format: str,
    jpeg_quality: int,
    resize_to: int | None,
    strip_metadata: bool = False,
) -> None:
    if resize_to is None and output_format == "original" and not strip_metadata:
        shutil.copy2(src, dest_img)
        return

    img = PilImage.open(src)
    try:
        img = ImageOps.exif_transpose(img)

        if resize_to and max(img.size) > resize_to:
            ratio = resize_to / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), PilImage.Resampling.LANCZOS)

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
) -> dict:
    """
    Shared export loop. Returns a dict with 'exported', 'output_dir', and optionally
    'jsonl_entries' and 'csv_rows' when accumulate_plain=True.
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

    jsonl_entries: list[dict] = []
    exported = 0

    for i, img in enumerate(images):
        src = Path(img.file_path)
        if not captions_only and not src.exists():
            continue
        if _is_excluded(img, aesthetic_min, captioned_only, exclude_flags, style_sim_min):
            continue

        if captions_only:
            # Don't read or write image files; use original filename for any manifest entries.
            dest_img = dest_dir / img.filename
        else:
            dest_img = _dest_img_path(dest_dir, img, output_format)
            _write_image(src, dest_img, output_format, jpeg_quality, resize_to, strip_metadata)

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

    return {
        "exported": exported,
        "jsonl_entries": jsonl_entries,
    }


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
    job_id: str | None = None,
) -> dict:
    exclude_flags = exclude_flags or []
    dest = Path(output_dir) / f"{n_repeats}_{concept_token}"
    dest.mkdir(parents=True, exist_ok=True)

    loop_result = await _run_export_loop(
        db, dataset_id, image_ids, dest, output_format, jpeg_quality,
        resize_to, aesthetic_min, captioned_only, exclude_flags, style_sim_min,
        job_id, "export", caption_format, subfolders=subfolders, strip_metadata=strip_metadata,
        captions_only=captions_only,
    )

    if caption_format == "jsonl" and loop_result["jsonl_entries"]:
        out = Path(output_dir) / "captions.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for entry in loop_result["jsonl_entries"]:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {"exported": loop_result["exported"], "output_dir": str(dest)}


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
    job_id: str | None = None,
) -> dict:
    exclude_flags = exclude_flags or []
    dest = Path(output_dir) / concept_name
    dest.mkdir(parents=True, exist_ok=True)

    loop_result = await _run_export_loop(
        db, dataset_id, image_ids, dest, output_format, jpeg_quality,
        resize_to, aesthetic_min, captioned_only, exclude_flags, style_sim_min,
        job_id, "export", caption_format, subfolders=subfolders, strip_metadata=strip_metadata,
        captions_only=captions_only,
    )

    if caption_format == "jsonl" and loop_result["jsonl_entries"]:
        out = Path(output_dir) / "captions.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for entry in loop_result["jsonl_entries"]:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {"exported": loop_result["exported"], "output_dir": str(dest)}


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
    job_id: str | None = None,
) -> dict:
    exclude_flags = exclude_flags or []
    out = Path(output_dir)
    if captions_only:
        dest_dir = out
    else:
        dest_dir = out / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)

    loop_result = await _run_export_loop(
        db, dataset_id, image_ids, dest_dir, output_format, jpeg_quality,
        resize_to, aesthetic_min, captioned_only, exclude_flags, style_sim_min,
        job_id, "export", None, accumulate_plain=True, subfolders=subfolders,
        strip_metadata=strip_metadata, captions_only=captions_only,
    )

    jsonl_path = Path(output_dir) / "captions.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for entry in loop_result["jsonl_entries"]:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {"exported": loop_result["exported"], "output_dir": output_dir}


async def preview_export(
    db: AsyncSession,
    dataset_id: str,
    aesthetic_min: float | None = None,
    captioned_only: bool = False,
    exclude_flags: list[str] | None = None,
    style_sim_min: float | None = None,
    subfolders: list[str] | None = None,
) -> dict:
    exclude_flags = exclude_flags or []

    query = select(
        Image.filename, Image.caption_text,
        Image.aesthetic_score, Image.quality_flags, Image.style_similarity_score,
    ).where(Image.dataset_id == dataset_id)
    if subfolders is not None:
        query = query.where(Image.subfolder.in_(subfolders))
    result = await db.execute(query)
    rows = result.all()

    total = len(rows)
    will_export = 0
    excl_aesthetic = 0
    excl_uncaptioned = 0
    excl_flagged = 0
    excl_style_sim = 0
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
            will_export += 1
            if len(sample_files) < 5:
                caption = r.caption_text or ""
                sample_files.append({"image": r.filename, "caption_preview": caption[:80]})

    return {
        "image_count": total,
        "will_export": will_export,
        "captioned_count": sum(1 for r in rows if r.caption_text),
        "excluded_low_aesthetic": excl_aesthetic,
        "excluded_uncaptioned": excl_uncaptioned,
        "excluded_flagged": excl_flagged,
        "excluded_style_sim": excl_style_sim,
        "sample_files": sample_files,
    }
