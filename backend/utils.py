import re
import shutil
from pathlib import Path

from fastapi import HTTPException

ALLOWED_FLAG_KEYS = frozenset({"is_blurry", "is_noisy", "is_uniform", "has_watermark", "is_duplicate", "is_nsfw", "has_ai_artifacts"})


def normalize_subfolder(s: str) -> str:
    """Normalize a subfolder path: strip leading/trailing slashes, reject '..' segments."""
    parts = [p for p in s.replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise HTTPException(400, "Subfolder path must not contain '..'")
    return "/".join(parts)


def slugify_filename(name: str) -> str:
    """Convert an arbitrary name into a safe filename stem (lowercase, underscores, max 200 chars)."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "_", s)
    s = s.strip("_-")
    return s[:200] or "image"


def unique_filename(directory: Path, stem: str, suffix: str, db_names: set, disk_exclude: set[str] | None = None) -> str:
    """Return a filename that is neither on disk nor in db_names. Tries stem+suffix, then stem_001, _002, ...

    disk_exclude: filenames that exist on disk but should be treated as absent (e.g. files being renamed away
    in the same batch operation). Without this, a bulk-rename planning pass would see its own current files
    as occupied and skip past them, causing the counter to jump instead of restarting from 001.
    """
    def _on_disk(fn: str) -> bool:
        if disk_exclude and fn in disk_exclude:
            return False
        return (directory / fn).exists()

    candidate = f"{stem}{suffix}"
    if candidate not in db_names and not _on_disk(candidate):
        return candidate
    counter = 1
    while True:
        candidate = f"{stem}_{counter:03d}{suffix}"
        if candidate not in db_names and not _on_disk(candidate):
            return candidate
        counter += 1


def unique_filename_with_thumb(
    images_dir: Path,
    stem: str,
    suffix: str,
    db_names: set[str],
    occupied_thumb_stems: set[str],
    planned_thumb_stems: set[str],
    disk_exclude: set[str] | None = None,
) -> str:
    """Like unique_filename but also avoids thumbnail-stem collisions.

    Thumbnails are always .webp keyed by the image stem, so two images with
    different extensions but the same stem share a thumbnail path. This helper
    rejects any candidate whose stem is in occupied_thumb_stems (pre-built from
    the thumbnail directory before the loop) or planned_thumb_stems (accumulated
    within the current batch/loop).

    Mutates db_names (adds the chosen filename) and planned_thumb_stems (adds
    the chosen stem) so subsequent calls within the same batch stay consistent.
    """
    candidate = unique_filename(images_dir, stem, suffix, db_names, disk_exclude)
    while True:
        cand_stem = Path(candidate).stem
        if cand_stem not in occupied_thumb_stems and cand_stem not in planned_thumb_stems:
            break
        db_names.add(candidate)  # prevent unique_filename from returning this again
        candidate = unique_filename(images_dir, stem, suffix, db_names, disk_exclude)
    db_names.add(candidate)
    planned_thumb_stems.add(Path(candidate).stem)
    return candidate


def rename_with_sidecar(old_path: Path, new_path: Path) -> None:
    """Rename a file and its .txt sidecar (if it exists) atomically."""
    old_path.rename(new_path)
    old_txt = old_path.with_suffix(".txt")
    if old_txt.exists():
        old_txt.rename(new_path.with_suffix(".txt"))


def copy_with_sidecar(old_path: Path, new_path: Path) -> None:
    """Copy a file and its .txt sidecar (if it exists) to new_path."""
    shutil.copy2(old_path, new_path)
    old_txt = old_path.with_suffix(".txt")
    if old_txt.exists():
        shutil.copy2(old_txt, new_path.with_suffix(".txt"))


def read_caption_sidecar(image_path: Path | str) -> str | None:
    """Read the .txt caption sidecar next to an image (symmetric with _write_txt_sidecar).

    Returns the stripped text of {stem}.txt sitting beside image_path if it exists and is
    non-empty, else None. Use everywhere a sidecar is read (import, rescan, caption import);
    never inline the .with_suffix(".txt") logic.
    """
    txt = Path(image_path).with_suffix(".txt")
    if not txt.exists():
        return None
    try:
        text = txt.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def normalize_image_format(suffix: str, out_path: str) -> tuple[str, str]:
    """Normalise a file suffix to a PIL format name; fall back to PNG for unsupported types.

    Returns (fmt, out_path) — out_path may be updated when the format falls back to PNG.
    """
    fmt = suffix.lstrip(".").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    if fmt not in ("JPEG", "PNG", "WEBP"):
        fmt = "PNG"
        out_path = str(Path(out_path).with_suffix(".png"))
    return fmt, out_path


def image_save_kwargs(fmt: str) -> dict:
    """Return PIL save() kwargs for the given format."""
    if fmt == "JPEG":
        return {"quality": 95, "subsampling": 0}
    return {}


def thumbnail_path_for(image_path: Path | str) -> str:
    """Derive the .webp thumbnail path for an image sitting in a dataset images/ folder."""
    p = Path(image_path)
    return str(p.parent.parent / "thumbnails" / (p.stem + ".webp"))


def subsume_tags(tags: list[str]) -> list[str]:
    """Drop any tag that is a whole-word subsequence of a longer tag in the same list.

    "tail" is removed when "long tail" is present; "shirt" when "white shirt" is present.
    Exact case-insensitive duplicates collapse to the first occurrence. Order-stable:
    survivors keep their first-seen order. Matching is case-insensitive and whole-word
    (so "car" does not subsume "scar" or "carpet"). Used by captioning post-processing
    and the bulk dedupe operation so the rule never diverges.
    """
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tags:
        low = t.lower()
        if low not in seen:
            seen.add(low)
            deduped.append(t)
    lowered = [t.lower() for t in deduped]
    patterns = [re.compile(r"\b" + re.escape(low) + r"\b") for low in lowered]
    survivors: list[str] = []
    for i, tag in enumerate(deduped):
        # subsumed if a strictly longer tag contains this tag as a whole word
        subsumed = any(
            j != i and len(lowered[j]) > len(lowered[i]) and patterns[i].search(lowered[j])
            for j in range(len(deduped))
        )
        if not subsumed:
            survivors.append(tag)
    return survivors
