import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image as PilImage, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.licenses import (
    OTHER_PREFIX,
    allows_commercial,
    license_info,
    license_label,
    resolve_provenance,
)
from backend.models import Dataset, Image
from backend.models.detection import Detection
from backend.utils import chunked, safe_external_url
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
    # Provenance: resolved against the parent Dataset (fetched once, by the
    # dataset_id the loop is called with) for the manifests and license filters.
    Image.source_name,
    Image.source_url,
    Image.license,
    Image.attribution,
)


def _caption_text(img: Any) -> str:
    return img.caption_text or ""


def _is_excluded(
    img: Any,
    aesthetic_min: float | None,
    captioned_only: bool,
    exclude_flags: list[str],
    style_sim_min: float | None,
    license_filter: list[str] | None = None,
    commercial_only: bool = False,
    exclude_unlicensed: bool = False,
    exclude_no_derivatives: bool = False,
    effective_license: str = "",
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
    # License filters run on the *effective* license (image over dataset default).
    # commercial_only is conservative: unknown rights are treated as "no".
    # exclude_unlicensed is separate from license_filter on purpose: the filter is
    # an allowlist of known ids, so using it to express "has any license" would
    # also drop every `other:<free text>` license, which *is* a recorded license.
    if exclude_unlicensed and not effective_license:
        return True
    if license_filter and effective_license not in license_filter:
        return True
    if commercial_only and not allows_commercial(effective_license):
        return True
    # An export ships resized/cropped/re-encoded copies, which is precisely what a
    # no-derivatives license forbids redistributing. Unlike commercial_only this is
    # *not* conservative about unknowns: only a license known to be ND is dropped,
    # so an `other:` free-text license is not silently excluded.
    if exclude_no_derivatives and license_info(effective_license).no_derivatives:
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


def _write_sidecar(dest_dir: Path, stem: str, caption: str, caption_format: str) -> Path:
    """Write a caption sidecar and return the path written (named by the manifests)."""
    ext = ".caption" if caption_format == "caption" else ".txt"
    path = dest_dir / f"{stem}{ext}"
    path.write_text(caption, encoding="utf-8")
    return path


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


# Bounded length for one value interpolated into CREDITS.md. The manifest is a
# human-readable summary; licenses.csv carries the untruncated strings.
_MD_MAX_LEN = 300

# Markdown specials that change document structure in an inline context, plus the
# angle brackets (autolinks / raw HTML) and the pipe (would forge a table cell).
_MD_ESCAPE = re.compile(r"([\\`*_\[\]<>#|])")

# Leading characters Excel/LibreOffice/Sheets interpret as the start of a formula.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# `CREDITS.2.md`, `CREDITS.3.md`, … before giving up on a differing manifest.
_MANIFEST_MAX_ALTERNATES = 99


def _md_inline(value: str | None) -> str:
    """Escape an untrusted string for interpolation into a single line of CREDITS.md.

    CREDITS.md is a legal attribution document assembled by interpolation, and
    every value in it comes from a scraper, a sidecar or an EXIF tag. A newline in
    `attribution` would otherwise forge a `## <license>` section claiming rights
    the export does not carry. So: all whitespace and control characters collapse
    to single spaces, markdown specials are backslash-escaped, and the result is
    length-bounded (truncated before escaping, so an escape can never be split
    from the character it escapes).
    """
    s = "".join(
        " " if (c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F) else c
        for c in (value or "")
    )
    s = " ".join(s.split())
    if len(s) > _MD_MAX_LEN:
        s = s[: _MD_MAX_LEN - 1].rstrip() + "…"
    return _MD_ESCAPE.sub(r"\\\1", s)


def _md_link(url: str | None, text: str | None = None) -> str:
    """A markdown link for a provenance URL, or escaped plain text if it isn't safe.

    Only `http`/`https` become links (see `utils.safe_external_url`); anything else
    renders as inert text so a `javascript:` source URL scraped off a page cannot
    become a clickable target in a document we tell people to trust.
    """
    safe = safe_external_url(url)
    if not safe:
        return _md_inline(url)
    # The URL has no whitespace by construction, but three characters still end or
    # escape the target: `(`, `)`, and a trailing `\`, which would escape the
    # closing paren and swallow the rest of the document into the link.
    target = (
        safe.replace("\\", "%5C").replace("(", "%28").replace(")", "%29")
    )
    return f"[{_md_inline(text or safe)}]({target})"


def _csv_cell(value: str | None) -> str:
    """A CSV cell that a spreadsheet will not execute as a formula.

    The `csv` module already quotes correctly — this guards only the leading
    character, which decides whether a cell is data or code on open.
    """
    s = value or ""
    return "'" + s if s[:1] in _CSV_FORMULA_PREFIXES else s


def _write_atomic(path: Path, payload: bytes) -> None:
    """Write bytes via a temp file + `os.replace` so a reader never sees a partial manifest."""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _supersedes_existing_manifest(manifest_dir: Path, dest_dir: Path) -> bool:
    """True when this run replaces, rather than adds to, the manifest already there.

    Read from `licenses.csv` (the machine-readable half) so one decision covers
    both files and the pair can never diverge. If every file the existing manifest
    lists sits under this run's `dest_dir`, this run is describing the same subtree
    again and overwriting is the correct outcome — otherwise the second export into
    a directory would strand its complete manifest on `CREDITS.2.md` while
    `CREDITS.md` kept describing a subset.

    Conservative on anything unexpected: an unreadable, malformed or empty
    `licenses.csv` returns False and the caller falls back to the numbered chain.
    Never destroying an attribution document is worth an occasional stray file.
    """
    import csv

    existing = manifest_dir / "licenses.csv"
    if not existing.exists():
        return False
    try:
        prefix = _manifest_rel(dest_dir, manifest_dir)
        with existing.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
    except (OSError, UnicodeDecodeError, csv.Error):
        return False
    if len(rows) < 2 or not rows[0] or rows[0][0] != "file":
        return False
    listed = [r[0] for r in rows[1:] if r]
    if not listed:
        return False
    # `prefix` is "." when the images sit in the manifest directory itself — then
    # every relative path is trivially "under" it and the check says nothing, so
    # the run supersedes only when it is writing to that same flat directory.
    if prefix in ("", "."):
        return dest_dir.resolve() == manifest_dir.resolve()
    return all(f == prefix or f.startswith(f"{prefix}/") for f in listed)


def _manifest_dest(
    dest_dir: Path, name: str, payload: bytes, supersede: bool = False
) -> Path | None:
    """Where to write a manifest — or None when an identical one is already there.

    Exports routinely share an output directory (kohya writes `10_concept/` beside
    an earlier `20_concept/`, and manifests live in their common parent), so
    overwriting `CREDITS.md` would silently replace an attribution document
    describing one set of files with one that never mentions them. Identical
    content is a no-op; differing content lands on `CREDITS.2.md`, `CREDITS.3.md`, …

    `supersede` is the exception: this run covers everything the existing manifest
    covers (see `_supersedes_existing_manifest`), so it takes the canonical name.
    """
    base = dest_dir / name
    if supersede:
        return base
    for cand in [base] + [dest_dir / f"{base.stem}.{n}{base.suffix}" for n in range(2, _MANIFEST_MAX_ALTERNATES + 1)]:
        if not cand.exists():
            return cand
        try:
            if cand.read_bytes() == payload:
                return None
        except OSError:
            return cand
    return dest_dir / f"{base.stem}.{_MANIFEST_MAX_ALTERNATES}{base.suffix}"


def _manifest_rel(path: Path, manifest_dir: Path) -> str:
    """A manifest `file` value: `path` relative to the manifest directory, POSIX-style.

    Not a bare basename — images sit one level below the manifests (kohya's
    `10_concept/`, plain's `images/`) and a loss mask shares its image's basename,
    so a basename alone does not identify a file in the export.
    """
    try:
        rel = path.resolve().relative_to(manifest_dir.resolve())
    except (ValueError, OSError):
        return path.name
    return rel.as_posix()


def _render_credits_md(credits: list[dict], partial: bool) -> str:
    by_license: dict[str, list[dict]] = {}
    for row in credits:
        by_license.setdefault(row["license"], []).append(row)

    def _sort_key(item):
        lic, _rows = item
        info = license_info(lic)
        # attribution-required first, then unlicensed, then the rest
        return (0 if info.requires_attribution else (1 if not lic else 2), lic)

    lines = ["# Credits", ""]
    total = len(credits)
    unlicensed = sum(1 for r in credits if not r["license"])
    lines.append(f"{total} image(s) exported; {unlicensed} with no license recorded.")
    if partial:
        lines.append("")
        lines.append(
            "**This export did not finish** (it was cancelled or failed part-way). The "
            "entries below cover only the files written before it stopped; anything else "
            "in this directory is not described here."
        )
    lines.append("")
    for lic, rows in sorted(by_license.items(), key=_sort_key):
        info = license_info(lic)
        heading = _md_inline(license_label(lic)) if lic else "No license recorded"
        if lic and lic.lower().startswith(OTHER_PREFIX):
            # An `other:` license is free text from a scraper and could otherwise
            # render a heading byte-identical to a curated one ("other:CC BY 4.0"),
            # making an unverified claim look like a vocabulary entry.
            heading = f"{heading} (unrecognised license, recorded as free text)"
        # Every obligation this license carries, so a redistributor sees them
        # without having to look the license up.
        duties = [
            label for flag, label in (
                (info.requires_attribution, "attribution required"),
                (info.share_alike, "share-alike"),
                (info.no_derivatives, "no derivatives"),
            ) if flag
        ]
        note = f" — {', '.join(duties)}" if duties else ""
        lines.append(f"## {heading}{note} ({len(rows)} image(s))")
        if info.url:
            lines.append(_md_link(info.url))
        lines.append("")
        by_source: dict[str, list[dict]] = {}
        for row in rows:
            by_source.setdefault(row["source_name"] or "Unknown source", []).append(row)
        for source, srows in sorted(by_source.items()):
            lines.append(f"- **{_md_inline(source)}** ({len(srows)} image(s))")
            # Per-image URLs, not one per source group: the primary ingest path
            # records a site-level source_name with a per-post source_url, so a
            # single representative URL would drop every citable page but one.
            for url in sorted({r["source_url"] for r in srows if r["source_url"]}):
                lines.append(f"  - {_md_link(url)}")
            for attribution in sorted({r["attribution"] for r in srows if r["attribution"]}):
                lines.append(f"  - {_md_inline(attribution)}")
        lines.append("")
    return "\n".join(lines)


def _render_licenses_csv(credits: list[dict]) -> str:
    import csv
    import io

    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(["file", "source_name", "source_url", "license", "attribution"])
    for row in credits:
        # `file` is deliberately not formula-guarded: it is a path this code
        # generated, not scraped input, and the `'` prefix would corrupt a
        # legitimate filename starting with `-`, `=` or `@`. The four provenance
        # columns are the untrusted ones.
        writer.writerow([
            row["file"], _csv_cell(row["source_name"]),
            _csv_cell(row["source_url"]), _csv_cell(row["license"]),
            _csv_cell(row["attribution"]),
        ])
    return buf.getvalue()


def _write_credits(
    dest_dir: Path, credits: list[dict], partial: bool = False, image_dir: Path | None = None
) -> list[str]:
    """Write CREDITS.md + licenses.csv for an export; returns the filenames written.

    Always written (even when nothing carries a license, and even when the export
    stopped partway) so a published dataset always ships an attribution file — a
    missing one reads as "no attribution needed", which is exactly the claim we
    can't make. Attribution-required licenses are listed first because those are
    the entries a redistributor must act on.

    Every interpolated value is untrusted: see `_md_inline` / `_md_link` /
    `_csv_cell`. The `file` column names a file this export actually wrote,
    relative to the manifest directory.

    A run that did not finish — cancelled *or* failed — writes
    `CREDITS.partial.md` / `licenses.partial.csv` instead of the canonical names.
    Otherwise it would claim `CREDITS.md` with its "did not finish" banner and
    push the later successful run's complete manifest onto `CREDITS.2.md` —
    permanently leaving the wrong file as the one a redistributor opens.

    `image_dir` is where this run wrote its images; given it, a complete run that
    covers everything the existing manifest covers overwrites in place instead of
    starting a numbered chain. Omitted (or on a partial run) the chain applies.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".partial" if partial else ""
    # One decision from licenses.csv, applied to both files, so the pair can never
    # end up with one superseded and the other chained.
    supersede = (
        not partial
        and image_dir is not None
        and _supersedes_existing_manifest(dest_dir, image_dir)
    )
    written: list[str] = []
    for name, text in (
        (f"CREDITS{suffix}.md", _render_credits_md(credits, partial)),
        (f"licenses{suffix}.csv", _render_licenses_csv(credits)),
    ):
        payload = text.encode("utf-8")
        target = _manifest_dest(dest_dir, name, payload, supersede=supersede)
        if target is None:
            continue
        _write_atomic(target, payload)
        written.append(target.name)
    return written


async def _dataset_defaults(db: AsyncSession, dataset_id: str):
    """Fetch the parent dataset's provenance defaults once, for inheritance."""
    return (await db.execute(
        select(
            Dataset.source_name, Dataset.source_url, Dataset.license, Dataset.attribution
        ).where(Dataset.id == dataset_id)
    )).one_or_none()


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
    license_filter: list[str] | None = None,
    commercial_only: bool = False,
    exclude_unlicensed: bool = False,
    exclude_no_derivatives: bool = False,
    manifest_dir: Path | None = None,
) -> dict:
    """
    Shared export loop. Returns a dict with 'exported', 'output_dir', and optionally
    'jsonl_entries' and 'csv_rows' when accumulate_plain=True. When mask_dir is set
    (and captions_only is not), a grayscale loss-mask PNG is written per exported
    image and the mask counters are included in the result. CREDITS.md and
    licenses.csv are written into manifest_dir (defaulting to dest_dir).
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

    ds_defaults = await _dataset_defaults(db, dataset_id)

    jsonl_entries: list[dict] = []
    credits: list[dict] = []
    used_stems: set[str] = set()
    exported = 0
    masks_written = 0
    masks_full_white = 0
    excluded_no_mask = 0

    manifest_target = manifest_dir or dest_dir
    manifest_target.mkdir(parents=True, exist_ok=True)

    try:
        for i, img in enumerate(images):
            if job_id:
                job_queue.raise_if_cancelled(job_id)
            src = Path(img.file_path)
            if not captions_only and not src.exists():
                continue
            prov = resolve_provenance(img, ds_defaults)
            if _is_excluded(
                img,
                aesthetic_min=aesthetic_min,
                captioned_only=captioned_only,
                exclude_flags=exclude_flags,
                style_sim_min=style_sim_min,
                license_filter=license_filter,
                commercial_only=commercial_only,
                exclude_unlicensed=exclude_unlicensed,
                exclude_no_derivatives=exclude_no_derivatives,
                effective_license=prov["license"],
            ):
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

            sidecar: Path | None = None
            if accumulate_plain or caption_format == "jsonl":
                jsonl_entries.append({"file": dest_img.name, "caption": caption})
            else:
                sidecar = _write_sidecar(dest_dir, dest_img.stem, caption, caption_format or "txt")

            # Name a file this export actually wrote. A captions-only run never
            # writes `dest_img`, so it lists the caption sidecar instead (or, when
            # captions go to captions.jsonl, that file's own entry key).
            listed = (sidecar or dest_img) if captions_only else dest_img
            credits.append({
                "file": _manifest_rel(listed, manifest_target),
                "source_name": prov["source_name"],
                "source_url": prov["source_url"],
                "license": prov["license"],
                "attribution": prov["attribution"],
            })
            exported += 1

            if job_id and i % 5 == 0:
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": job_type,
                    "status": "running", "done": exported, "total": len(images),
                    "percent": round((i + 1) / len(images) * 100, 1),
                    "current_item": img.filename, "message": f"Exporting {img.filename}",
                })
    except BaseException:
        # Any non-completion — cancellation, a truncated image, ENOSPC, EACCES —
        # leaves the files written so far on disk. Ship the manifests for that
        # partial set rather than an unattributed pile: the exact state this
        # feature exists to prevent. `BaseException`, not `Exception`, because
        # `CancelledError` is the most common way to land here.
        #
        # Written synchronously (a real task cancellation is not obliged to
        # schedule another await) and best-effort: if the manifest write itself
        # fails — a full disk is exactly what raised the original error — that
        # must not mask the exception the caller needs to see.
        try:
            _write_credits(manifest_target, credits, partial=True)
        except Exception:
            pass
        raise

    manifest_files = await asyncio.get_event_loop().run_in_executor(
        None, _write_credits, manifest_target, credits, False, dest_dir
    )

    loop_result: dict = {
        "exported": exported,
        "jsonl_entries": jsonl_entries,
        "unlicensed_count": sum(1 for c in credits if not c["license"]),
        # The names actually written — a supersede, a numbered alternate and an
        # unchanged no-op all produce something other than CREDITS.md/licenses.csv,
        # so the completion message must not hardcode those two.
        "manifest_files": manifest_files,
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
    license_filter: list[str] | None = None,
    commercial_only: bool = False,
    exclude_unlicensed: bool = False,
    exclude_no_derivatives: bool = False,
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
        license_filter=license_filter, commercial_only=commercial_only,
        exclude_no_derivatives=exclude_no_derivatives,
        exclude_unlicensed=exclude_unlicensed,
        manifest_dir=Path(output_dir),
    )

    if caption_format == "jsonl" and loop_result["jsonl_entries"]:
        out = Path(output_dir) / "captions.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for entry in loop_result["jsonl_entries"]:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    result = {"exported": loop_result["exported"], "output_dir": str(dest)}
    if mask_dir is not None:
        result["mask_dir"] = str(mask_dir)
    result["unlicensed_count"] = loop_result["unlicensed_count"]
    result["manifest_files"] = loop_result["manifest_files"]
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
    license_filter: list[str] | None = None,
    commercial_only: bool = False,
    exclude_unlicensed: bool = False,
    exclude_no_derivatives: bool = False,
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
        license_filter=license_filter, commercial_only=commercial_only,
        exclude_no_derivatives=exclude_no_derivatives,
        exclude_unlicensed=exclude_unlicensed,
        manifest_dir=Path(output_dir),
    )

    if caption_format == "jsonl" and loop_result["jsonl_entries"]:
        out = Path(output_dir) / "captions.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for entry in loop_result["jsonl_entries"]:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    result = {"exported": loop_result["exported"], "output_dir": str(dest)}
    if mask_dir is not None:
        result["mask_dir"] = str(mask_dir)
    result["unlicensed_count"] = loop_result["unlicensed_count"]
    result["manifest_files"] = loop_result["manifest_files"]
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
    license_filter: list[str] | None = None,
    commercial_only: bool = False,
    exclude_unlicensed: bool = False,
    exclude_no_derivatives: bool = False,
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
        license_filter=license_filter, commercial_only=commercial_only,
        exclude_no_derivatives=exclude_no_derivatives,
        exclude_unlicensed=exclude_unlicensed,
        manifest_dir=Path(output_dir),
    )

    jsonl_path = Path(output_dir) / "captions.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for entry in loop_result["jsonl_entries"]:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    result = {"exported": loop_result["exported"], "output_dir": output_dir}
    if mask_dir is not None:
        result["mask_dir"] = str(mask_dir)
    result["unlicensed_count"] = loop_result["unlicensed_count"]
    result["manifest_files"] = loop_result["manifest_files"]
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
    license_filter: list[str] | None = None,
    commercial_only: bool = False,
    exclude_unlicensed: bool = False,
    exclude_no_derivatives: bool = False,
) -> dict:
    exclude_flags = exclude_flags or []

    query = select(
        Image.id, Image.filename, Image.caption_text,
        Image.aesthetic_score, Image.quality_flags, Image.style_similarity_score,
        Image.source_name, Image.source_url, Image.license, Image.attribution,
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

    ds_defaults = await _dataset_defaults(db, dataset_id)

    total = len(rows)
    will_export = 0
    excl_license = 0
    unlicensed = 0
    excl_aesthetic = 0
    excl_uncaptioned = 0
    excl_flagged = 0
    excl_style_sim = 0
    unlicensed_will_export = 0
    without_detections = 0
    sample_files: list[dict] = []

    for r in rows:
        low_aes = aesthetic_min is not None and (r.aesthetic_score is None or r.aesthetic_score < aesthetic_min)
        no_cap = captioned_only and not r.caption_text
        flagged = bool(exclude_flags) and any((r.quality_flags or {}).get(f) for f in exclude_flags)
        low_sim = style_sim_min is not None and (r.style_similarity_score is None or r.style_similarity_score < style_sim_min)

        # The license filters operate on the *effective* value, so inheritance is
        # resolved here rather than reading `r.license`. A per-license breakdown is
        # deliberately not returned — Stats owns that view, via its Licenses panel.
        lic = resolve_provenance(r, ds_defaults)["license"]
        if not lic:
            unlicensed += 1
        bad_license = (
            (exclude_unlicensed and not lic)
            or (bool(license_filter) and lic not in (license_filter or []))
            or (commercial_only and not allows_commercial(lic))
            or (exclude_no_derivatives and license_info(lic).no_derivatives)
        )
        if bad_license:
            excl_license += 1

        if low_aes:
            excl_aesthetic += 1
        if no_cap:
            excl_uncaptioned += 1
        if flagged:
            excl_flagged += 1
        if low_sim:
            excl_style_sim += 1

        if not (low_aes or no_cap or flagged or low_sim or bad_license):
            no_det = export_masks and r.id not in ids_with_detections
            if no_det:
                without_detections += 1
                # skip policy: these images are excluded from the export entirely
                if mask_missing == "skip":
                    continue
            will_export += 1
            if not lic:
                unlicensed_will_export += 1
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
        "excluded_license": excl_license,
        # Counted over the whole dataset scope, not just what will export — the
        # point of the warning is "you have unlicensed images", which stays true
        # whether or not the current filters happen to drop them.
        "unlicensed_count": unlicensed,
        # …but whether they *ship* depends on every filter, not just the license
        # ones. Derived here rather than in the client, which could only guess
        # from the license flags and so claimed "they still export" whenever a
        # caption or aesthetic filter had already dropped them.
        "unlicensed_will_export": unlicensed_will_export,
        "sample_files": sample_files,
    }
    if export_masks:
        preview["images_without_detections"] = without_detections
    return preview
