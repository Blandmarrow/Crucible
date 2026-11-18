"""One concurrency limit per OpenAI-compatible provider, shared by everything that calls it
(M12 phase 2, the user's ruling of 2026-11-18).

A provider's `max_concurrent` says how many requests its server takes at once. Before
this, only a caption job honoured it, and only for its own requests (`InOrderCalls`'
bound): two jobs, or a job and an analyst chat, could together exceed it. Every request
to a provider now holds one slot of this limiter while it is in flight:

- the caption job's per-image call, in `/captioning/run` and `/captioning/pipeline`
  (`routers/captioning.py`), inside the job's own `InOrderCalls` window;
- the analyst chat's streaming call (`services/chat_runner.py`), for the whole stream.

**One limiter per provider id and event loop.** An `asyncio.Semaphore` binds to the
loop that first waits on it, and each test scenario runs on a fresh loop. A changed
`max_concurrent` replaces the limiter: holders of the old one release into it, so for
the length of their requests the two together can exceed the new limit once.

Ollama has no provider row and keeps its own per-job bound. See
docs/dev/analyst/sending.md.
"""
import asyncio
import contextlib
from collections.abc import AsyncIterator

from backend.schemas.openai_provider import MAX_CONCURRENT_REQUESTS

# provider id -> (the loop it was made on, its size, the semaphore). The loop is kept
# itself, not its id: a closed loop's id can be reused by the next one.
_limiters: dict[str, tuple[asyncio.AbstractEventLoop, int, asyncio.Semaphore]] = {}


def _clamp(size: object) -> int:
    try:
        value = int(size or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(MAX_CONCURRENT_REQUESTS, value))


def limiter(provider_id: str, size: object) -> asyncio.Semaphore:
    """The provider's shared semaphore, sized by its current `max_concurrent`."""
    size = _clamp(size)
    loop = asyncio.get_running_loop()
    current = _limiters.get(provider_id)
    if current is None or current[0] is not loop or current[1] != size:
        current = (loop, size, asyncio.Semaphore(size))
        _limiters[provider_id] = current
    return current[2]


@contextlib.asynccontextmanager
async def slot(provider_id: str, size: object) -> AsyncIterator[None]:
    """Hold one of the provider's slots for the body of the `async with`."""
    async with limiter(provider_id, size):
        yield


def in_use(provider_id: str) -> int:
    """Slots taken right now on the running loop: for tests and diagnostics."""
    current = _limiters.get(provider_id)
    if current is None or current[0] is not asyncio.get_running_loop():
        return 0
    _loop, size, sem = current
    return size - sem._value  # noqa: SLF001 — asyncio exposes no public count
