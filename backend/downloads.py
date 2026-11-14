"""One file fetched over HTTP and trusted only once its pinned SHA-256 matches.

The shared body of every pinned download the app makes: the LaMa weights
(`ml/lama_inpainter.py`) and the WordNet dictionary (`services/dictionary_service.py`).
It is synchronous on purpose — LaMa calls it from an executor thread already, and the
dictionary sends it to the CPU pool — so cancellation is a `should_stop` callable
checked between chunks rather than a task cancel.

The file streams to ``<dest>.part`` and is renamed onto ``dest`` only after the hash
matches, so an interrupted or tampered download never leaves a file a later call
would trust. Indexed in `docs/dev/shared-utilities.md`.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.request
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

CHUNK = 256 * 1024
USER_AGENT = "Crucible (dataset tool)"


class DownloadCancelled(Exception):
    """`should_stop` returned True mid-download. The `.part` file is already gone."""


class DownloadHashMismatch(Exception):
    """The downloaded bytes are not the pinned ones. The `.part` file is already gone."""

    def __init__(self, url: str, actual: str, expected: str):
        super().__init__(f"{url} has SHA-256 {actual}, expected {expected}")
        self.url = url
        self.actual = actual
        self.expected = expected


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def part_path(dest: Path) -> Path:
    """Where `download_verified` streams `dest` before it is verified."""
    return dest.with_name(dest.name + ".part")


def cached_verified(dest: Path, sha256: str) -> bool:
    """True when `dest` exists with the pinned hash. A file with any other hash is
    deleted, so the next download replaces it rather than failing forever."""
    if not dest.exists():
        return False
    actual = sha256_file(dest)
    if actual == sha256:
        return True
    logger.warning(
        "%s has SHA-256 %s, expected %s — deleting it so it is downloaded again",
        dest, actual, sha256,
    )
    dest.unlink(missing_ok=True)
    return False


def _open(url: str, timeout: float):
    # The one call that leaves the process: tests patch this, and conftest's autouse
    # guard makes it refuse, so no test can reach the network by accident.
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 — pinned https URLs only


def download_verified(
    url: str,
    dest: Path,
    sha256: str,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    timeout: float = 60,
) -> Path:
    """Download `url` to `dest` and return `dest`, or raise with nothing left behind.

    `on_progress(done, total)` is called after every chunk (`total` is 0 when the
    server sends no Content-Length); throttle inside it. Raises `DownloadCancelled`,
    `DownloadHashMismatch`, or whatever the transport raised.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = part_path(dest)
    try:
        with _open(url, timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as fh:
                while True:
                    if should_stop is not None and should_stop():
                        raise DownloadCancelled(url)
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
        actual = sha256_file(tmp)
        if actual != sha256:
            raise DownloadHashMismatch(url, actual, sha256)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return dest
