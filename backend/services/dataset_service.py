import asyncio
import re
import shutil
import statistics
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import tiktoken

from sqlalchemy import case, func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Dataset, Image, Tag
from backend.services.image_service import extract_generation_metadata, generate_thumbnail, get_image_info
from backend.utils import copy_with_sidecar, thumbnail_path_for

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}


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


async def create_dataset(db: AsyncSession, name: str, description: str = "", category: str = "") -> Dataset:
    ds_id = str(uuid4())
    slug = _name_to_slug(name)
    folder = settings.datasets_dir / slug
    if folder.exists():
        folder = settings.datasets_dir / f"{slug}_{ds_id[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)
    (folder / "thumbnails").mkdir(exist_ok=True)

    ds = Dataset(id=ds_id, name=name, description=description, category=category, folder_path=str(folder))
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
    old_folder = Path(ds.folder_path)
    new_slug = _name_to_slug(new_name)
    new_folder = settings.datasets_dir / new_slug

    if old_folder.exists() and old_folder.resolve() != new_folder.resolve():
        if new_folder.exists():
            new_folder = settings.datasets_dir / f"{new_slug}_{ds.id[:8]}"
        old_folder.rename(new_folder)

        old_str = str(old_folder)
        new_str = str(new_folder)
        result = await db.execute(select(Image).where(Image.dataset_id == ds.id))
        for img in result.scalars().all():
            if img.file_path and img.file_path.startswith(old_str):
                img.file_path = new_str + img.file_path[len(old_str):]
            if img.thumbnail_path and img.thumbnail_path.startswith(old_str):
                img.thumbnail_path = new_str + img.thumbnail_path[len(old_str):]
        ds.folder_path = new_str

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
                   if p != path and not p.startswith(prefix)]
        ds.declared_subfolders = current

    await db.commit()
    return moved


async def import_images_from_folder(
    db: AsyncSession,
    dataset: Dataset,
    folder_path: str,
    job_id: str | None = None,
    subfolder: str = "",
    preserve_structure: bool = False,
) -> int:
    from backend.workers.progress import broadcaster

    src = Path(folder_path)
    if not src.exists() or not src.is_dir():
        raise ValueError(f"Folder not found: {folder_path}")

    if preserve_structure:
        image_files = [f for f in src.rglob("*") if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]
    else:
        image_files = [f for f in src.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]
    total = len(image_files)
    added = 0

    from backend.utils import slugify_filename, unique_filename

    dest_images = Path(dataset.folder_path) / "images"
    dest_thumbs = Path(dataset.folder_path) / "thumbnails"

    existing_result = await db.execute(select(Image.filename).where(Image.dataset_id == dataset.id))
    db_filenames: set[str] = {r[0] for r in existing_result.all()}

    for i, src_file in enumerate(image_files):
        try:
            if preserve_structure:
                rel_subfolder = str(src_file.parent.relative_to(src)).replace("\\", "/")
                if rel_subfolder == ".":
                    rel_subfolder = ""
            else:
                rel_subfolder = subfolder

            slug = slugify_filename(src_file.stem) or "image"
            new_name = unique_filename(dest_images, slug, src_file.suffix.lower(), db_filenames)
            dest_file = dest_images / new_name
            db_filenames.add(new_name)

            shutil.copy2(src_file, dest_file)

            info = get_image_info(str(dest_file))
            gen_meta = extract_generation_metadata(str(dest_file))
            thumb_path = str(dest_thumbs / (dest_file.stem + ".webp"))
            await asyncio.get_event_loop().run_in_executor(
                None, generate_thumbnail, str(dest_file), thumb_path
            )

            img = Image(
                dataset_id=dataset.id,
                filename=new_name,
                original_filename=src_file.name,
                subfolder=rel_subfolder,
                file_path=str(dest_file),
                thumbnail_path=thumb_path,
                generation_metadata=gen_meta,
                **info,
            )
            db.add(img)
            added += 1
        except Exception:
            pass  # skip broken files, continue import

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

    await db.commit()
    await refresh_stats(db, dataset.id)
    return added


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

    ds = await db.get(Dataset, dataset_id)
    if ds:
        ds.image_count = image_count
        ds.captioned_count = captioned_count
        ds.total_size_bytes = total_size
        ds.updated_at = datetime.utcnow()
        await db.commit()


async def get_dataset_stats(db: AsyncSession, dataset_id: str, subfolder: str | None = None) -> dict:
    ds = await db.get(Dataset, dataset_id)
    if not ds:
        return {}

    q = select(
        Image.width, Image.height, Image.format,
        Image.aesthetic_score, Image.caption_text,
        Image.blur_score, Image.noise_score, Image.uniformity_score,
        Image.watermark_score, Image.color_score, Image.saturation_score,
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
        ).where(*_base_where)
    )).one()
    flag_counts = {
        "blurry": flag_row.blurry,
        "noisy": flag_row.noisy,
        "uniform": flag_row.uniform,
        "watermarked": flag_row.watermarked,
        "duplicate": flag_row.duplicate,
    }

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
    mp_dist: dict[str, int] = {}
    fs_dist: dict[str, int] = {}
    wc_dist: dict[str, int] = {}
    tc_dist: dict[str, int] = {}

    ssim_dist: dict[str, int] = {}

    captioned = 0
    enc = tiktoken.get_encoding("gpt2")

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

        # Watermark
        if r.watermark_score is not None:
            b = _watermark_bucket(r.watermark_score)
            wm_dist[b] = wm_dist.get(b, 0) + 1

        # Style similarity
        if r.style_similarity_score is not None:
            b = _watermark_bucket(r.style_similarity_score)
            ssim_dist[b] = ssim_dist.get(b, 0) + 1

        text = r.caption_text or ""
        trimmed = text.strip()
        if trimmed:
            captioned += 1
            wc = len(trimmed.split())
            tc = len(enc.encode_ordinary(trimmed))
        else:
            wc = tc = 0
        b = _bucket(wc, wc_edges, wc_labels)
        wc_dist[b] = wc_dist.get(b, 0) + 1
        b = _bucket(tc, tc_edges, tc_labels)
        tc_dist[b] = tc_dist.get(b, 0) + 1

    # Embedding coverage — separate count query to avoid loading blobs
    embed_q = select(func.count(Image.id)).where(
        Image.dataset_id == dataset_id,
        Image.clip_embedding.isnot(None),
    )
    if subfolder is not None:
        embed_q = embed_q.where(Image.subfolder == subfolder)
    embed_count = await db.scalar(embed_q)
    score_cov["embeddings"] = embed_count or 0

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


async def get_score_values(db: AsyncSession, dataset_id: str, subfolder: str | None = None) -> dict:
    q = select(
        Image.aesthetic_score,
        Image.blur_score,
        Image.noise_score,
        Image.uniformity_score,
        Image.watermark_score,
        Image.color_score,
        Image.saturation_score,
        Image.style_similarity_score,
        Image.width,
        Image.height,
        Image.file_size_bytes,
        Image.caption_text,
    ).where(Image.dataset_id == dataset_id)
    if subfolder is not None:
        q = q.where(Image.subfolder == subfolder)
    result = await db.execute(q)
    rows = result.all()

    score_fields = [
        "aesthetic_score", "blur_score", "noise_score", "uniformity_score",
        "watermark_score", "color_score", "saturation_score", "style_similarity_score",
    ]
    out: dict[str, list[float]] = {f: [] for f in score_fields}
    out["megapixels"] = []
    out["file_size_mb"] = []
    out["caption_words"] = []
    out["caption_tokens"] = []

    enc = tiktoken.get_encoding("gpt2")
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
        out["caption_tokens"].append(len(enc.encode_ordinary(trimmed)) if trimmed else 0)

    return out


async def duplicate_dataset(
    db: AsyncSession,
    source_dataset: Dataset,
    new_name: str,
    job_id: str,
    source_version_id: str | None = None,
) -> str:
    """Deep-clone a dataset into a new one.  Returns the new dataset's id."""
    import logging
    from backend.workers.progress import broadcaster

    log = logging.getLogger(__name__)

    # --- Step 1: create fresh destination dataset ---
    new_ds = await create_dataset(db, new_name, source_dataset.description, source_dataset.category)
    new_ds.declared_subfolders = list(source_dataset.declared_subfolders or [])
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
            Image.tags_json, Image.quality_flags, Image.aesthetic_score, Image.blur_score,
            Image.noise_score, Image.uniformity_score, Image.watermark_score, Image.color_score,
            Image.saturation_score, Image.style_similarity_score, Image.dino_layer_scores,
            Image.generation_metadata, Image.processing_history,
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
            try:
                copy_with_sidecar(old_path, new_path)
                if old_thumb.exists():
                    shutil.copy2(old_thumb, new_thumb)
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
                    tags_json=row.tags_json,
                    quality_flags=row.quality_flags,
                    aesthetic_score=row.aesthetic_score,
                    blur_score=row.blur_score,
                    noise_score=row.noise_score,
                    uniformity_score=row.uniformity_score,
                    watermark_score=row.watermark_score,
                    color_score=row.color_score,
                    saturation_score=row.saturation_score,
                    style_similarity_score=row.style_similarity_score,
                    dino_layer_scores=row.dino_layer_scores,
                    generation_metadata=row.generation_metadata,
                    processing_history=row.processing_history,
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
        loop = asyncio.get_event_loop()

        for i, state in enumerate(states):
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
                shutil.copy2(src_file, new_path)
                if state.caption_text:
                    new_path.with_suffix(".txt").write_text(state.caption_text, encoding="utf-8")
                await loop.run_in_executor(None, generate_thumbnail, str(new_path), str(new_thumb))

                db.add(Image(
                    id=str(uuid4()),
                    dataset_id=new_ds.id,
                    filename=state.filename,
                    original_filename=state.original_filename,
                    subfolder=state.subfolder,
                    file_path=str(new_path),
                    thumbnail_path=str(new_thumb),
                    caption_text=state.caption_text,
                    tags_json=state.tags_json or [],
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
    return new_ds.id


async def get_tag_cooccurrence(db: AsyncSession, dataset_id: str, limit: int = 15, subfolder: str | None = None) -> dict:
    q = select(Image.tags_json).where(Image.dataset_id == dataset_id, Image.tags_json.isnot(None))
    if subfolder is not None:
        q = q.where(Image.subfolder == subfolder)
    result = await db.execute(q)
    all_tags_json = [r[0] for r in result.all() if r[0]]

    # Count tag frequencies, pick top N
    freq: dict[str, int] = {}
    for tags in all_tags_json:
        for t in tags:
            freq[t] = freq.get(t, 0) + 1

    top_tags = [t for t, _ in sorted(freq.items(), key=lambda x: -x[1])[:limit]]
    if not top_tags:
        return {"tags": [], "matrix": []}

    tag_idx = {t: i for i, t in enumerate(top_tags)}
    n = len(top_tags)
    matrix = [[0] * n for _ in range(n)]

    for tags in all_tags_json:
        present = [tag_idx[t] for t in tags if t in tag_idx]
        for i in present:
            for j in present:
                matrix[i][j] += 1

    return {"tags": top_tags, "matrix": matrix}
