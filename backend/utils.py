import re
import shutil
from pathlib import Path

from fastapi import HTTPException

ALLOWED_FLAG_KEYS = frozenset({"is_blurry", "is_noisy", "is_uniform", "has_watermark", "is_duplicate"})


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


def unique_filename(directory: Path, stem: str, suffix: str, db_names: set) -> str:
    """Return a filename that is neither on disk nor in db_names. Tries stem+suffix, then stem_001, _002, ..."""
    candidate = f"{stem}{suffix}"
    if candidate not in db_names and not (directory / candidate).exists():
        return candidate
    counter = 1
    while True:
        candidate = f"{stem}_{counter:03d}{suffix}"
        if candidate not in db_names and not (directory / candidate).exists():
            return candidate
        counter += 1


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
