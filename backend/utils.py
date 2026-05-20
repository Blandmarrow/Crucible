import re
from pathlib import Path

from fastapi import HTTPException


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
