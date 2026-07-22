import json
import re
import shutil
import time
from collections.abc import Iterator, Sequence
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

import regex as _regex
from fastapi import HTTPException

_T = TypeVar("_T")


def chunked(seq: Sequence[_T], size: int = 10_000) -> Iterator[Sequence[_T]]:
    """Yield successive ``size``-length slices of ``seq``.

    The single source of truth for chunking id lists before an SQL ``IN (...)``
    so the number of bind parameters stays under SQLite's 999-variable limit.
    ``size`` defaults to 10k. Empty ``seq`` yields nothing.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    for start in range(0, len(seq), size):
        yield seq[start:start + size]


REGEX_TIMEOUT_SECONDS = 30.0

# Alias so callers can `except backend.utils.regex_error` without importing `regex`.
regex_error = _regex.error


def compile_user_regex(pattern: str):
    """Compile a client-supplied regex pattern. Raises `regex_error` if invalid.

    Always use this — never stdlib `re` — for a pattern that comes from a client.
    `re`'s matching loop is C code that never drops the GIL and cannot be
    interrupted, so a catastrophic pattern freezes the whole process: wrapping it
    in `run_in_executor` + `asyncio.wait_for` does nothing, because the event loop
    can't get scheduled to fire the timeout. `regex` releases the GIL and honours
    `timeout=`. Pair with `regex_sub_deadline` to bound a batch.
    """
    return _regex.compile(pattern)


def regex_sub_deadline(compiled, repl: str, text: str, deadline: float) -> str:
    """`compiled.sub(repl, text)` bounded by an absolute `time.monotonic()` deadline.

    Raises TimeoutError once the deadline passes, so one budget covers a whole
    batch rather than granting each item its own (which would let N items stretch
    the worst case to N × timeout).
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("regex time budget exhausted")
    return compiled.sub(repl, text, timeout=remaining)


@lru_cache(maxsize=None)
def _get_enc():
    """Cached GPT-2 BPE encoder (tiktoken imported lazily to keep import cheap)."""
    import tiktoken
    return tiktoken.get_encoding("gpt2")


def count_caption_tokens(text: str | None) -> int:
    """GPT-2 BPE token count for a caption. Empty/whitespace/None → 0."""
    trimmed = (text or "").strip()
    if not trimmed:
        return 0
    return len(_get_enc().encode_ordinary(trimmed))

ALLOWED_FLAG_KEYS = frozenset({"is_blurry", "is_noisy", "is_uniform", "has_watermark", "is_duplicate", "is_nsfw", "has_ai_artifacts"})


def sanitize_abs_path(path: str) -> Path:
    """Validate a user-supplied filesystem path string and return a Path.

    Rejects null bytes and relative paths with HTTP 400. Use in every router
    that accepts an arbitrary path from the client (file browser, ComfyUI
    workflow folder scan); never re-inline this check.
    """
    if "\x00" in path:
        raise HTTPException(400, "Invalid path")
    p = Path(path)
    if not p.is_absolute():
        raise HTTPException(400, "Path must be absolute")
    return p


def normalize_subfolder(s: str) -> str:
    """Normalize a subfolder path: strip leading/trailing slashes, reject '..' segments."""
    parts = [p for p in s.replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise HTTPException(400, "Subfolder path must not contain '..'")
    return "/".join(parts)


def normalize_license_filter(values: list[str] | None) -> list[str] | None:
    """Strip entries and drop an all-empty list; None means "no license filter".

    ``""`` is a meaningful *entry* (images with no license recorded) but an empty
    list must not be read as "match nothing".
    """
    if values is None:
        return None
    cleaned = [v.strip() for v in values]
    return cleaned or None


def parse_license_filter_param(value: str) -> list[str] | None:
    """Parse a JSON-array license_filter query param into normalized ids.

    A JSON array rather than a comma-separated string because an
    ``other:<free text>`` license id may itself contain commas — splitting on
    commas would silently match nothing. The single encoding for license id
    lists across the API (export preview and ``GET /images/``); empty means
    "no filter".
    """
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        raise HTTPException(400, "license_filter must be a JSON array of strings")
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise HTTPException(400, "license_filter must be a JSON array of strings")
    return normalize_license_filter(parsed)


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
