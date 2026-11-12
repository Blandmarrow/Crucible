"""Decode ahead of the GPU call (M10 T5).

Every GPU scoring check splits its per-image work in two. The CPU half (`prepare_*`
in each scorer module: `open_rgb`, the model's preprocess, `img.close()`) turns a
path into a CPU tensor. The GPU half (`infer_*`: the copy to the card and the
forward pass) runs on the one GPU thread. `run_prefetched` runs the CPU halves for
the next few images on asyncio's default pool while the GPU thread runs the current
one, so the card no longer waits for each file to decode. T4 timed decode plus
preprocess at 63–93% of each check on real images
(`docs/dev/quality/scoring-job.md` § The GPU checks prepare images ahead).

PM-035 is untouched: the pool only ever runs a `prepare_*` half, which never moves
a tensor to the device (`backend/tests/test_prefetch_halves.py` holds that), and
every `infer_*` half goes through `device.run_on_gpu`. Still one image per forward
pass, so the scores are the serial loop's, bit for bit (decision 14).

This module imports no torch: it only schedules the two halves.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from backend.utils import InOrderWindow

# Timed on 2026-11-12 at 1, 2, 4, 6 and 8 (docs/dev/quality/scoring-job.md § The
# GPU checks prepare images ahead): 8 is 1.3x faster than 4 on real images and
# costs ~0.2 GB of RAM, since each image in the window holds only its decoded
# buffer while it is prepared and a small tensor after.
PREFETCH_MAX_WORKERS = 8


def _prefetch_workers() -> int:
    """Half the cores, leaving the rest for the server, capped and at least 1."""
    return max(1, min(PREFETCH_MAX_WORKERS, (os.cpu_count() or 2) // 2))


async def run_prefetched(
    image_paths: Sequence[str],
    prepare: Callable[[str], Any],
    infer: Callable[[Any], Any],
    *,
    job_id: str | None,
    progress: Callable[[int], Awaitable[None]] | None,
    message: str,
    failure: str,
    log: logging.Logger,
) -> list[Any]:
    """`infer(prepare(path))` for each path, in input order; None where it failed.

    `prepare` runs on the default pool, up to `_prefetch_workers()` images at
    once; `infer` runs on the GPU thread. It answers like the serial loops it
    replaced:

    - **Results in input order**, because the oldest prepared image is always
      the next one the GPU takes.
    - **A cancel returns a prefix.** `cancel_requested` is checked before every
      top-up and before every GPU call; images already prepared are dropped, so
      a cancel stops sooner than T1's, which keeps its in-flight work.
      `routers/quality.py` relies on the prefix when it writes
      `c_ids[:len(values)]`.
    - **A failure in either half is None at that index only**, logged as
      `"<failure> failed for <path>"` on the scorer's own logger. Each caller
      maps None to its own failure value (`docs/dev/quality/scoring.md`
      § The failure contract).
    - **Progress after images 1, 11, 21, ...**, through `progress` when given,
      else an SSE event with `"<message> i/total"` when there is a `job_id`.

    The `run_in_executor` call must stay in this function's own body: the GPU
    thread guard's Rule A exempts it by its innermost enclosing function.
    """
    from backend.ml import device as _device
    from backend.workers.job_queue import job_queue
    from backend.workers.progress import broadcaster

    def cancelled() -> bool:
        return bool(job_id) and job_queue.cancel_requested(job_id)

    loop = asyncio.get_running_loop()
    total = len(image_paths)
    results: list[Any] = []
    # The window counts the image the GPU is about to take, so while it runs,
    # bound - 1 more are being prepared behind it.
    window = InOrderWindow(_prefetch_workers())
    nxt = 0
    try:
        while True:
            if cancelled():
                break
            while not window.full() and nxt < total:
                path = image_paths[nxt]
                window.push(path, loop.run_in_executor(None, prepare, path))
                nxt += 1
            if not len(window):
                break
            path, prepared, exc = await window.pop_oldest()
            if cancelled():
                break
            result = None
            if exc is None:
                try:
                    result = await _device.run_on_gpu(infer, prepared)
                except Exception as e:
                    exc = e
            # Not held across the next image's wait: a prepared tensor for a
            # large image is several MB.
            del prepared
            if exc is not None:
                log.warning("%s failed for %s", failure, path, exc_info=exc)
                # An infer error's traceback holds the GPU half's frame, and its
                # tensors with it.
                exc = None
            results.append(result)

            i = len(results) - 1
            if progress is not None:
                if i % 10 == 0:
                    await progress(i + 1)
            elif job_id and i % 10 == 0:
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "quality_score",
                    "status": "running", "done": i + 1, "total": total,
                    "percent": round((i + 1) / total * 100, 1),
                    "message": f"{message} {i + 1}/{total}",
                })
    finally:
        window.cancel_pending()
    return results
