import asyncio
import logging
import sys
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_patch_lock = threading.Lock()


def is_hf_cached(repo_id: str, filename: str) -> bool:
    """Return True if the given file is already in the HuggingFace local cache."""
    try:
        from huggingface_hub import try_to_load_from_cache
        result = try_to_load_from_cache(repo_id, filename)
        return isinstance(result, str)
    except Exception:
        return False


def emit_sync(
    job_id: str,
    loop: asyncio.AbstractEventLoop,
    message: str,
    percent: float = -1.0,
    dataset_id: str | None = None,
) -> None:
    """Emit a progress SSE event from a synchronous (executor) thread."""
    from backend.workers.progress import broadcaster
    asyncio.run_coroutine_threadsafe(
        broadcaster.emit(job_id, {
            "type": "progress",
            "job_id": job_id,
            "dataset_id": dataset_id,
            "status": "running",
            "message": message,
            "done": 0,
            "total": 0,
            "percent": percent,
        }),
        loop,
    )


def _make_progress_tqdm_class(job_id, loop, base_message, dataset_id):
    """
    Create a tqdm-compatible class that emits SSE download progress.
    Subclasses tqdm.auto.tqdm with disable=True to suppress terminal output
    while forwarding byte counts to the SSE system.
    """
    from tqdm.auto import tqdm as BaseTqdm

    class ProgressTqdm(BaseTqdm):
        def __init__(self, *args, **kwargs):
            self._emit_total = kwargs.get("total")
            self._emit_n = int(kwargs.get("initial", 0))
            self._emit_unit = kwargs.get("unit", "it")
            self._last_emit_t = 0.0
            # disable=True suppresses all terminal output from the base class
            super().__init__(*args, **{**kwargs, "disable": True})

        def update(self, n=1):
            if n is None:
                n = 1
            self._emit_n += n
            now = time.monotonic()
            # throttle: emit at most twice per second
            if now - self._last_emit_t < 0.5:
                return
            self._last_emit_t = now

            if self._emit_total and self._emit_total > 0 and self._emit_unit == "B":
                done_mb = self._emit_n / 1_048_576
                total_mb = self._emit_total / 1_048_576
                pct = min(100.0, (self._emit_n / self._emit_total) * 100.0)
                msg = f"{base_message} ({done_mb:.0f} / {total_mb:.0f} MB)"
            else:
                pct = -1.0
                msg = base_message
            emit_sync(job_id, loop, msg, pct, dataset_id)

    return ProgressTqdm


@contextmanager
def progress_tqdm_patch(job_id, loop, message, dataset_id=None):
    """
    Context manager: temporarily replace huggingface_hub's tqdm with a class
    that emits SSE byte-level download progress events.

    Safe to call even when job_id/loop are None (yields without patching).
    Acquires _patch_lock for the full duration to prevent concurrent patches.
    """
    if not (job_id and loop):
        yield
        return

    # Ensure the module is imported so it appears in sys.modules
    try:
        import huggingface_hub.utils.tqdm  # noqa: F401
    except Exception:
        yield
        return

    tqdm_module = sys.modules.get("huggingface_hub.utils.tqdm")
    if tqdm_module is None:
        yield
        return

    ProgressTqdm = _make_progress_tqdm_class(job_id, loop, message, dataset_id)
    _patch_lock.acquire()
    original = tqdm_module.tqdm
    tqdm_module.tqdm = ProgressTqdm
    try:
        yield
    finally:
        tqdm_module.tqdm = original
        _patch_lock.release()
