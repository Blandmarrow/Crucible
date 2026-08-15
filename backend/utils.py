import asyncio
import errno
import json
import logging
import re
import shutil
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import regex as _regex
from fastapi import HTTPException

if TYPE_CHECKING:  # `models/image.py` imports this module, so a runtime import cycles.
    from backend.models.image import Image

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def chunked(seq: Sequence[_T], size: int = 10_000) -> Iterator[Sequence[_T]]:
    """Yield successive ``size``-length slices of ``seq``.

    The single source of truth for chunking id lists before an SQL ``IN (...)``
    so the number of bind parameters stays under SQLite's ``SQLITE_MAX_VARIABLE_NUMBER``.
    That ceiling is **32766** on any SQLite >= 3.32 (2020); the widely-quoted 999
    is the pre-3.32 default and does not apply here — note the 10k default below
    would already exceed it, so a comment citing 999 is describing the wrong limit,
    not a tighter one. Empty ``seq`` yields nothing. Use this in every batched
    ``IN`` query; never re-inline a ``range(0, len(x), N)`` slice loop.
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
    """Cached GPT-2 BPE encoder (tiktoken imported lazily to keep import cheap).

    The single tokenizer entry point: never call tiktoken.get_encoding("gpt2")
    inline anywhere else.
    """
    import tiktoken
    return tiktoken.get_encoding("gpt2")


def count_caption_tokens(text: str | None) -> int:
    """GPT-2 BPE token count for a caption. Empty/whitespace/None → 0."""
    trimmed = (text or "").strip()
    if not trimmed:
        return 0
    return len(_get_enc().encode_ordinary(trimmed))

# The canonical set of valid quality flag names. Import this wherever flag names
# must be validated or used in a SQL filter; never redefine the set locally.
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


def within_datasets_dir(path_str: str, base_dir: Path) -> Path | None:
    """Resolve a stored file path, returning it only if it is inside `base_dir`.

    The non-raising half of `safe_dataset_path`. A serve route wants the 403 and
    uses the wrapper; a *destructive* route wants to still drop the row for a
    path it refuses to touch, so it takes the `None` and skips the filesystem op
    — an undeletable row the user can see is the worse failure.

    `is_relative_to`, not a string prefix: `/data/datasets_backup/x.mp4`
    startswith `/data/datasets` and is not inside it.
    """
    resolved = Path(path_str).resolve()
    return resolved if resolved.is_relative_to(base_dir.resolve()) else None


def safe_dataset_path(path_str: str, base_dir: Path) -> Path:
    """Resolve a stored file path and refuse anything outside `base_dir` (HTTP 403).

    The paths this guards come from the DB rather than a request body, but a row
    can carry a path written by an earlier import, a rename, or a hand edit — so
    every endpoint that turns a stored path into a FileResponse runs it through
    here. Used by the image file/thumbnail routes and the video file/poster
    routes; never re-inline the prefix check.
    """
    resolved = within_datasets_dir(path_str, base_dir)
    if resolved is None:
        raise HTTPException(403, "Access denied")
    return resolved


def contained_path(
    path_str: str | None, base_dir: Path, *, context: str, ident: str = ""
) -> Path | None:
    """Resolve one of a row's stored paths, or None if it escaped `base_dir`.

    The form every **destructive** site takes: the non-raising counterpart of the
    403 the serve routes give, with the log line attached. A delete still drops
    the row for a path it refuses to touch — an undeletable row the user can see
    is the worse failure — and skips only the filesystem work. Callers unlink the
    *resolved* path this hands back, never the raw string: validating one path and
    deleting another would defeat the check entirely. Gate **per row**, so one
    escaped path does not stop its neighbours.

    The gate covers the versioning hook as much as the unlink:
    `mark_image_deleted_in_versions` reaches `_store_object(dataset_folder,
    file_path)` and copies those bytes into `{ds}/.versions/objects/`, so an
    out-of-tree `file_path` is an arbitrary-file *read* primitive — retrievable
    through a snapshot restore — even with the unlink skipped.

    `base_dir` is explicit because `utils.py` deliberately imports no
    `backend.config`; pass `settings.datasets_dir`. `context` names the site and
    `ident` the row, for the warning. Never raises — one caller
    (`version_service._remove_stale_files`) sits inside a bare
    `except Exception: pass` that would swallow it.

    Callers: `images.delete_image`/`batch_delete`/`bulk_delete_filtered`,
    `videos._delete_previous_frames`, `quality.resolve_duplicates`,
    `version_service._remove_stale_files`, plus the two sites that gate only the
    versioning hook rather than an unlink — `filesystem.delete_path`'s row loop and
    `version_service.restore_snapshot`'s extras loop. `filesystem._add_orphan`
    hand-rolls the equivalent and stays that way: it carries an extra
    `is_dir`/`old_prefix` responsibility this helper does not model.
    """
    if not path_str:
        return None
    safe = within_datasets_dir(path_str, base_dir)
    if safe is None:
        logger.warning("%s %s: refusing to touch out-of-tree path %s", context, ident, path_str)
    return safe


def _subfolder_parts(s: str) -> list[str]:
    """The segments of a subfolder path: backslashes folded, blanks and `.` dropped.

    Shared by `normalize_subfolder` and `join_subfolder` so the two disagree only
    about what `..` means, never about anything else.
    """
    return [p for p in s.replace("\\", "/").split("/") if p and p != "."]


def normalize_subfolder(s: str) -> str:
    """Normalize a subfolder path: strip leading/trailing slashes, reject '..' segments.

    Rejects '..' with HTTP 400. Import this; never copy the logic inline and never
    re-import it from a router. This is the **write-time** guard — use it wherever a
    subfolder arrives from a client; see `join_subfolder` for the job-side counterpart.
    """
    parts = _subfolder_parts(s)
    if any(p == ".." for p in parts):
        raise HTTPException(400, "Subfolder path must not contain '..'")
    return "/".join(parts)


def join_subfolder(*fragments: str) -> str:
    """Join subfolder fragments into one normalized path. **Never raises.**

    The job-side counterpart to `normalize_subfolder`: it drops `..` segments
    rather than rejecting them. Gallery subfolders are virtual labels — nothing
    nests on disk — so a stray `..` is a wrong folder *name*, not a path escape,
    and failing a long-running job's item over one is worse than filing the image
    a level up. Raising here would also be unactionable: the message would surface
    on a background row and re-fail identically on every re-run.

    Needed because rows can hold a subfolder written before the write-time guard
    existed, or by future code that renders paths rather than accepting them.
    """
    parts: list[str] = []
    for fragment in fragments:
        parts.extend(p for p in _subfolder_parts(fragment or "") if p != "..")
    return "/".join(parts)


def _is_unsafe_url_char(c: str) -> bool:
    """The single character predicate `safe_external_url` trims *and* rejects with."""
    return c.isspace() or ord(c) < 0x20 or ord(c) in (0x7F, 0xFEFF)


def _trim_unsafe_url_chars(s: str) -> str:
    """`str.strip()` over `_is_unsafe_url_char` — which `strip()` itself cannot express."""
    start, end = 0, len(s)
    while start < end and _is_unsafe_url_char(s[start]):
        start += 1
    while end > start and _is_unsafe_url_char(s[end - 1]):
        end -= 1
    return s[start:end]


def safe_external_url(value: str | None) -> str:
    """A provenance URL if it is safe to put behind a link, else ``""``.

    Only ``http``/``https`` survive: source URLs come from scrapers, sidecars and
    EXIF, so a ``javascript:``/``data:``/``file:`` value must never reach an
    ``href`` or a markdown link target. Whitespace (including embedded newlines,
    which would otherwise break out of a markdown link) is rejected outright
    rather than stripped — a URL with a space in the middle is not a URL.
    Callers render a rejected value as escaped plain text, never as a link.

    Kept character-for-character in step with
    `frontend/src/utils/url.ts::safeExternalUrl`: the two guards decide whether the
    *same* URL becomes a link in the UI and in `CREDITS.md`, so a character one
    side rejects and the other accepts is a silent divergence between what a user
    sees and what the export ships. The two sets agree over the whole Unicode
    range only with the odd ones out spelled explicitly — ``U+FEFF`` matches JS
    ``\\s`` but not Python's ``isspace()``, and ``U+0085`` is the reverse (the JS
    side names it in its character class for the same reason).

    **The trim uses that same set, deliberately not** ``str.strip()``. ``strip()``
    strips exactly ``isspace()`` and JS ``trim()`` strips exactly ECMA ``\\s``, and
    those two differ on the very characters named above — so a ``U+FEFF`` at the
    *end* of a URL was trimmed away by the UI and rejected by the export, while
    ``U+0085`` there did the reverse. Trimming the rejected set on both sides makes
    the two identical by construction, whatever the character's position.
    """
    s = _trim_unsafe_url_chars(value or "")
    if not s or any(_is_unsafe_url_char(c) for c in s):
        return ""
    scheme = s.split(":", 1)[0].lower() if ":" in s else ""
    return s if scheme in ("http", "https") else ""


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


def parse_id_list_param(value: str | None, param_name: str) -> list[str] | None:
    """Parse a JSON-array-of-ids query param into a list. None means "no filter".

    The same wire encoding as ``license_filter`` (a JSON array in a string, so the
    three image endpoints and the export preview all agree), but deliberately
    *not* ``parse_license_filter_param`` — that one also runs license
    normalization, which has no meaning for an opaque uuid. Entries are returned
    verbatim, blanks included: a blank id is meaningless and the caller rejects it
    with a 400 rather than silently narrowing the list.
    """
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        raise HTTPException(400, f"{param_name} must be a JSON array of strings")
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise HTTPException(400, f"{param_name} must be a JSON array of strings")
    return parsed or None


# One `EXISTS` per id is built for `label_match="all"`, so the id count is an
# expression-tree depth, not just a bind count: SQLite's SQLITE_MAX_EXPR_DEPTH
# defaults to 1,000 and a longer list turns into an uncaught 500 rather than a
# 400. Nobody selects a hundred chips — the cap is a guard, not a budget.
MAX_LABEL_FILTER_IDS = 100


def validate_label_filter_params(
    label_ids: list[str] | None,
    match: str | None,
    missing: bool | None,
) -> list[str]:
    """Validate the `label_filter`/`label_match`/`label_missing` triple. Returns the ids.

    The **one** validator behind both the three image endpoints and export, so a
    filter shape the gallery refuses cannot be smuggled in through an export
    request body and fail an already-enqueued job instead. It lives here beside
    `parse_id_list_param` — the established home for a client-supplied filter
    param that raises `HTTPException` — so the service layer stays HTTP-free.

    Four 400s: a blank entry, an unrecognised `match`, `missing=True` alongside a
    non-empty list, and more than `MAX_LABEL_FILTER_IDS` ids. `match=None` is
    accepted and means "any" (the image endpoints declare it a plain `str` so a
    typo is a 400 from here rather than a per-route 422; export declares a
    `Literal` and gets the 422 — the deliberate asymmetry `_parse_flags` already
    documents).
    """
    ids = list(label_ids or [])
    if match is not None and match not in ("any", "all"):
        raise HTTPException(400, f"Invalid label_match: {match} (expected 'any' or 'all')")
    if any(not lid for lid in ids):
        # The `license_filter` reasoning: a blank id is meaningless, and dropping
        # it silently narrows a mixed list while voiding an all-blank one. Both
        # are silent lies. "No labels at all" is `label_missing=true`.
        raise HTTPException(
            400,
            "label_filter contains an empty entry; use label_missing=true "
            "to select images with no labels",
        )
    if ids and missing is True:
        # Unsatisfiable, and a query that always returns zero rows is
        # indistinguishable from a broken filter.
        raise HTTPException(
            400, "label_missing=true cannot be combined with a non-empty label_filter"
        )
    if len(ids) > MAX_LABEL_FILTER_IDS:
        raise HTTPException(
            400, f"label_filter cannot name more than {MAX_LABEL_FILTER_IDS} labels"
        )
    return ids


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

    Call this instead of unique_filename in every code path that creates or
    renames an image file and associates a thumbnail with it. Build
    occupied_thumb_stems from thumb_dir.glob("*.webp") once before the loop; do
    NOT exclude the stems of images being renamed/moved from that set — doing so
    re-introduces the within-batch clobber bug where one image's new thumbnail
    path matches another image's current one.

    The one sanctioned exception is `images.bulk_rename`, which *must* exclude
    them so a second Renumber restarts its counter at 001 instead of continuing
    past the stems the first one left behind. It pays for the exclusion itself,
    by deferring any rename whose target image, thumbnail **or** `.txt` sidecar
    path is a batch member's current one through a temp name (PM-017).
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


class FileInUseError(OSError):
    """Raised by `unlink_retrying`/`rename_retrying` — carries a message meant for the user.

    `backend/main.py` translates it to a 409; routers should let it propagate
    rather than catching it, except where the mutation is deliberately
    non-fatal (a poster is never a gate).
    """


# Windows refuses to unlink or rename a file another handle has open without
# FILE_SHARE_DELETE, which Python's open() does not request — and Crucible is
# often that other handle itself, because Starlette's FileResponse keeps the
# file open for the whole body send and a browser can hold a range request open
# indefinitely. A native POSIX filesystem allows both, so nothing in CI or a dev
# container can see this; the retry is the only reason a delete issued while the
# player is tearing down succeeds.
#
# ERROR_SHARING_VIOLATION (32) and ERROR_LOCK_VIOLATION (33) are the Windows
# codes. The errnos are **not** a POSIX afterthought and must not be narrowed to
# Windows — both halves of the set are load-bearing on their own:
#   - EACCES is the *workhorse on Windows*. CPython's PC/errmap.h maps
#     ERROR_ACCESS_DENIED (5) onto it, and that is what MoveFileEx returns when
#     the destination is open.
#   - EBUSY is how the Linux cifs client spells a host-side sharing violation
#     (`ERRbadshare`/STATUS_SHARING_VIOLATION → -EBUSY in
#     fs/smb/client/netmisc.c), which is the SMB-share case: the Windows box
#     serving the mount holds the handle, and the lock clears the same way.
#   - ETXTBSY is that same cifs mapping *before* it was changed in 2013 — the
#     remap commit cites `unlink` as the operation it was wrong for — so it is
#     here for an older client only, and is unreachable otherwise.
# WSL2 /mnt/*, Docker bind mounts and CIFS shares are all places Crucible users
# keep video, and a lock there arrives through one of these three and genuinely
# clears within the backoff.
#
# The set is deliberately wider than "definitely a lock": a real EACCES (an ACL,
# a read-only parent) costs ~0.75 s of backoff and then reports the same
# FileInUseError, which is why the message names the folder possibility too
# rather than asserting a cause nothing here checked.
_LOCKED_WINERRORS = (32, 33)
_LOCKED_ERRNOS = (errno.EACCES, errno.EBUSY, errno.ETXTBSY)
_LOCK_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4)  # ~0.75 s worst case; zero cost when the first try wins


def _is_locked_error(exc: OSError) -> bool:
    """True when `exc` means 'another handle holds this file', not a real failure."""
    if getattr(exc, "winerror", None) in _LOCKED_WINERRORS:
        return True
    return exc.errno in _LOCKED_ERRNOS


async def _retry_locked(op: Callable[[], None], description: str, attempts: int) -> None:
    """Run `op`, retrying on a locked-file OSError with a short backoff.

    Any other OSError propagates unchanged, so callers keep their existing
    FileNotFoundError branches. `asyncio.sleep` rather than `time.sleep`
    because every caller is an async route — a blocking sleep would stall the
    event loop, and with it the very response whose socket teardown we are
    waiting on.
    """
    if attempts < 1:
        raise ValueError("attempts must be positive")
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            op()
            return
        except OSError as exc:
            if not _is_locked_error(exc):
                raise
            last = exc
            if attempt == attempts - 1:
                break
            await asyncio.sleep(_LOCK_RETRY_DELAYS[min(attempt, len(_LOCK_RETRY_DELAYS) - 1)])
    # Hedged on purpose. Nothing above *checked* why the call failed — even on
    # Windows this fires for an ACL or Controlled Folder Access denial, and on
    # POSIX for a read-only parent — so the message names both possibilities and
    # then names the step that actually helps. "another program" is load-bearing
    # wording: docs/video.md promises it and test_video_locked_files_http.py
    # asserts it.
    raise FileInUseError(
        f"{description} is open in another program, or its folder does not allow "
        f"the change. Close any preview or player showing it, then try again."
    ) from last


async def unlink_retrying(path: Path, *, missing_ok: bool = True, attempts: int = 5) -> None:
    """`path.unlink`, retrying while the file is locked; raises FileInUseError if it stays locked."""
    await _retry_locked(lambda: path.unlink(missing_ok=missing_ok), f"'{path.name}'", attempts)


async def rename_retrying(src: Path, dst: Path, *, attempts: int = 5) -> None:
    """`src.rename(dst)`, retrying while the file is locked; raises FileInUseError if it stays locked."""
    await _retry_locked(lambda: src.rename(dst), f"'{src.name}'", attempts)


async def replace_retrying(src: Path, dst: Path, *, attempts: int = 5) -> None:
    """`src.replace(dst)`, retrying while the file is locked; raises FileInUseError if it stays locked.

    A third helper rather than a flag on `rename_retrying`, because the two are
    not interchangeable on Windows: `Path.rename` is `MoveFileW` *without*
    `MOVEFILE_REPLACE_EXISTING` and raises `FileExistsError` the moment the
    destination exists — which is the normal case for an in-place overwrite, and
    a failure that would pass on POSIX (where `rename` clobbers silently) while
    breaking every such rewrite on Windows. `Path.replace` is the overwriting
    form on both platforms.

    `dst` names the description, not `src`: the caller's `src` is a uuid temp the
    user has never seen, while `dst` is the file they are looking at.
    """
    await _retry_locked(lambda: src.replace(dst), f"'{dst.name}'", attempts)


class InsufficientDiskSpaceError(RuntimeError):
    """Raised by `require_free_space` — carries a message meant for the user."""


# A run that writes files needs room for more than the bytes it copies: resized
# JPEGs, mask PNGs, caption sidecars, manifests and SQLite's own WAL all land on
# the same volume. The multiplier covers those; the floor covers the case where
# the payload is tiny but the disk is nearly full, which breaks the whole app and
# not just this run.
DISK_HEADROOM = 1.2
DISK_FLOOR_BYTES = 256 * 2 ** 20


def format_bytes(n: float) -> str:
    """Human byte size for user-facing messages ('1.4 GB'). Not for filenames or IDs."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def require_free_space(
    target_dir: Path | str,
    needed_bytes: int = 0,
    headroom: float = DISK_HEADROOM,
    floor_bytes: int = DISK_FLOOR_BYTES,
) -> None:
    """Raise InsufficientDiskSpaceError when `target_dir`'s volume is too full.

    The requirement is `max(needed_bytes * headroom, floor_bytes)`, so a call with
    no size estimate (`needed_bytes=0`) still enforces the floor — that is the cheap
    request-path form. `target_dir` need not exist yet: the check walks up to the
    nearest existing ancestor, which is on the same volume. An unreadable path is
    never fatal on its own — the operation is allowed to proceed and fail for real.

    Every run that writes many files (export, folder import) preflights through
    here; never inline `shutil.disk_usage`. Routers map the error to HTTP 507.
    """
    probe = Path(target_dir).resolve()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return
    required = max(int(needed_bytes * headroom), floor_bytes)
    if free >= required:
        return
    detail = f" for {format_bytes(needed_bytes)} of files" if needed_bytes else ""
    raise InsufficientDiskSpaceError(
        f"Not enough free disk space on {probe}: {format_bytes(free)} available, "
        f"about {format_bytes(required)} needed{detail}. Free up space and try again."
    )


def rename_with_sidecar(old_path: Path, new_path: Path) -> None:
    """Rename a file and its .txt sidecar (if it exists) atomically.

    Use this everywhere a file is renamed; never copy the two-step pattern inline.

    **The caller must have proved `new_path` free.** `Path.rename` silently
    replaces an existing target on POSIX and raises `FileExistsError` on Windows,
    so an unproven target either destroys a live file or aborts a batch halfway —
    and the same is true of the sidecar, which is renamed onto `{new
    stem}.txt` independently of whatever the image rename found there. Neither is
    fixed here: `os.replace` would only make Windows destroy data as quietly as
    POSIX does, and an `exists()` guard would add a TOCTOU race to a contract the
    caller can satisfy exactly (see `bulk_rename`'s two-phase pass, PM-017).
    """
    old_path.rename(new_path)
    old_txt = old_path.with_suffix(".txt")
    if old_txt.exists():
        old_txt.rename(new_path.with_suffix(".txt"))


def copy_with_sidecar(old_path: Path, new_path: Path) -> None:
    """Copy a file and its .txt sidecar (if it exists) to new_path.

    Uses shutil.copy2 and leaves the source intact. Use this everywhere a file is
    copied; never copy the two-step pattern inline.
    """
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


@lru_cache(maxsize=None)
def score_columns(cls: type) -> frozenset[str]:
    """Every `*_score` column on a mapped class, derived by suffix.

    Derived rather than listed so the *eleventh* score column is covered the
    moment it is added. The suffix rule is already load-bearing — the structural
    guards in `backend/tests/test_video_lineage_mirrors.py` and
    `backend/tests/test_versioning_restore.py` derive the same set, and the
    comment on `Image.scores_stale` exists to protect it.

    Takes the *class* rather than importing `Image`, so this module still has no
    runtime import of `backend.models` (`models/image.py` imports this one).
    Correctly excludes `dino_layer_scores` (plural) and `scores_stale` itself.
    """
    return frozenset(c.key for c in cls.__table__.columns if c.key.endswith("_score"))


def record_in_place(img: "Image", op: str, **params) -> None:
    """Record that an operation overwrote an image's file, in the two columns that carry it.

    **The single writer of both `Image.processing_history` and
    `Image.scores_stale`.** Every path that rewrites an image's pixels in place —
    batch and single resize, batch and single crop, LUT, upscale, crop-to-detection,
    video frame re-extraction — must go through this and nothing else. That is what
    keeps the two columns from drifting: a site that appends history by hand records
    the edit and silently leaves the scores looking trustworthy.

    `processing_history` is the durable signal that a row's pixels are no longer
    what produced it, and video re-extraction reads it as its skip guard: a frame
    carrying any op other than `reextract` is left alone, because re-cutting it from
    the source would discard the edit (`docs/dev/video-reextract.md`).

    `scores_stale` says the ten `*_score` columns and the `quality_flags` derived
    from them were measured against pixels that no longer exist. Nothing here
    recomputes a score — that is a manual job — so the bit stands until a quality
    run that actually measured something refreshes every score the row carries
    (`routers/quality.py`).

    **The two columns deliberately diverge**: the history entry is the durable
    "these pixels were rewritten" record and pass 2's skip guard, so it is written
    either way, while the bit qualifies a *measurement* — a row that carries no
    score has none to invalidate, and marking it would put a "scores describe
    pixels that no longer exist" warning on the commonest workflow there is
    (upload → resize → export). It is never written `False` here: clearing is the
    quality job's job.

    List-concat reassignment, never `.append()` — SQLAlchemy compares JSON columns
    by equality, so mutating the loaded list in place looks unchanged and the
    UPDATE is skipped (CLAUDE.md § Key invariants).

    Pure attribute access and assignment: it cannot raise, which is what lets every
    caller place it between an irreversible `os.replace` and the `commit()` that
    describes it, rather than in the post-commit epilogue (PM-013).
    """
    now = datetime.now(timezone.utc)
    img.processing_history = (img.processing_history or []) + [
        {"op": op, **params, "at": now.isoformat()}
    ]
    img.updated_at = now
    # Pure attribute access, so the PM-013 placement above still holds: no
    # `*_score` column is `deferred` and nothing loads an `Image` under
    # `load_only`, so none of these getattrs can emit IO or raise. Both facts are
    # pinned by `backend/tests/test_scores_stale.py`.
    if any(getattr(img, c) is not None for c in score_columns(type(img))):
        img.scores_stale = True


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
    """Derive the .webp thumbnail path for an image sitting in a dataset images/ folder.

    `parent.parent/thumbnails/{stem}.webp`. Use this in any router that creates or
    regenerates thumbnails; never reconstruct the path manually. (Its video sibling
    `poster_path_for` is only a proposal — see there for why they differ.)
    """
    p = Path(image_path)
    return str(p.parent.parent / "thumbnails" / (p.stem + ".webp"))


def poster_path_for(video_path: Path | str) -> str:
    """Derive the .webp poster path for a video sitting in a dataset videos/ folder.

    `parent`, not thumbnail_path_for's `parent.parent`: videos live *flat* in
    `{dataset}/videos/` and their posters in `{dataset}/videos/thumbnails/`, so
    the poster directory is one level down from the video, not one level up.

    This is only the *proposal*. Unlike an image thumbnail, a poster name is not
    guaranteed to match its video's stem — rescan adopts filenames off disk and
    can meet two containers sharing a stem, so it resolves this through
    `unique_poster_path`. Read `Video.poster_path` for an existing row; never
    re-derive it.
    """
    p = Path(video_path)
    return str(p.parent / "thumbnails" / (p.stem + ".webp"))


def unique_poster_path(poster_dir: Path, stem: str, claimed: set[str]) -> Path:
    """Return a poster path whose stem is neither claimed nor already on disk.

    Tries `{stem}.webp`, then `{stem}_001.webp`, `_002`, … — the same counter
    convention as `unique_filename`, so a disambiguated poster reads like every
    other disambiguated name in the app.

    The counterpart to `unique_filename_with_thumb` for the paths that *adopt* a
    filename instead of picking one (video rescan, the poster backfill). Those
    cannot rename the user's file to dodge a stem collision, so they move the
    poster instead. Build `claimed` with
    `video_service.claimed_poster_stems`; this helper does not mutate it, so a
    caller looping over several videos must add each chosen stem itself.
    """
    def _free(candidate_stem: str) -> bool:
        return candidate_stem not in claimed and not (poster_dir / f"{candidate_stem}.webp").exists()

    if _free(stem):
        return poster_dir / f"{stem}.webp"
    counter = 1
    while True:
        candidate = f"{stem}_{counter:03d}"
        if _free(candidate):
            return poster_dir / f"{candidate}.webp"
        counter += 1


def subsume_tags(tags: list[str]) -> list[str]:
    """Drop any tag that is a whole-word subsequence of a longer tag in the same list.

    "tail" is removed when "long tail" is present; "shirt" when "white shirt" is present.
    Exact case-insensitive duplicates collapse to the first occurrence. Order-stable:
    survivors keep their first-seen order. Matching is case-insensitive and whole-word
    (so "car" does not subsume "scar" or "carpet"). Used by captioning post-processing
    and the bulk dedupe operation so the rule never diverges — never reimplement it.
    See docs/dev/tag-consolidation.md.
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
