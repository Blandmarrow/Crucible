"""One concurrency limit per OpenAI-compatible provider, shared by the analyst chat and
caption jobs (M12 phase 2, the user's ruling of 2026-11-18).

A provider's `max_concurrent` says how many requests its server takes at once. Before
this, only a caption job honoured it, and only for its own requests (`InOrderCalls`'
bound), so a job and an analyst chat could together exceed it. These requests now hold
one slot of the provider's limit while in flight:

- the caption job's per-image call, in `/captioning/run` and `/captioning/pipeline`
  (`routers/captioning.py`), inside the job's own `InOrderCalls` window;
- the analyst chat's streaming call (`services/chat_runner.py`), for the whole stream.

ComfyUI prompt generation (`ml/prompt_generator.py`) builds its own client and does not
take a slot yet.

- **One limit per provider and event loop**, keyed by the provider id alone. Its size is
  set when it is first made and changed only by `resize`, which the providers route calls
  when `max_concurrent` changes; a caller's own (possibly stale) copy of the setting never
  splits the limit in two. Each test scenario runs on a fresh loop, so a limit made on
  another loop is replaced.
- **Slots are handed out first come, first served.** A shrink takes effect as slots come
  back; nothing in flight is interrupted.
- **A waiter can give up.** `slot(..., stop=...)` polls `stop` while it waits and raises
  `SlotSkipped` once it is true, so a caption job's Stop is not followed, minutes later, by
  requests that were queued behind a chat. Such a request was never sent.

Ollama has no provider row and keeps its own per-job bound. See
docs/dev/analyst/sending.md.
"""
import asyncio
import collections
import contextlib
from collections.abc import AsyncIterator, Callable

from backend.schemas.openai_provider import MAX_CONCURRENT_REQUESTS

# How often a waiter with a `stop` checks it.
STOP_POLL_S = 0.25


class SlotSkipped(Exception):
    """Stop was asked for while the request waited for a slot: it was never sent."""


class _Limit:
    def __init__(self, size: int):
        self.size = size
        self.held = 0
        self.waiters: collections.deque[asyncio.Future] = collections.deque()

    def _wake(self) -> None:
        while self.waiters and self.held < self.size:
            fut = self.waiters.popleft()
            if not fut.done():
                self.held += 1  # handed over: the waiter now holds it
                fut.set_result(None)

    def release(self) -> None:
        self.held -= 1
        self._wake()

    async def acquire(self, stop: Callable[[], bool] | None) -> None:
        if stop is not None and stop():
            raise SlotSkipped
        if self.held < self.size and not self.waiters:
            self.held += 1
            return
        fut = asyncio.get_running_loop().create_future()
        self.waiters.append(fut)
        try:
            while True:
                try:
                    await asyncio.wait_for(asyncio.shield(fut), STOP_POLL_S if stop else None)
                    return
                except TimeoutError:
                    if stop():
                        raise SlotSkipped from None
        except BaseException:
            if fut.done() and not fut.cancelled():
                self.release()  # handed a slot while leaving: give it back
            else:
                fut.cancel()
                with contextlib.suppress(ValueError):
                    self.waiters.remove(fut)
            raise


# provider id -> (the loop it was made on, its limit). The loop is kept itself, not its
# id: a closed loop's id can be reused by the next one.
_limits: dict[str, tuple[asyncio.AbstractEventLoop, _Limit]] = {}


def _clamp(size: object) -> int:
    try:
        value = int(size or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(MAX_CONCURRENT_REQUESTS, value))


def _limit(provider_id: str, size: object) -> _Limit:
    loop = asyncio.get_running_loop()
    current = _limits.get(provider_id)
    if current is None or current[0] is not loop:
        current = (loop, _Limit(_clamp(size)))
        _limits[provider_id] = current
    return current[1]


@contextlib.asynccontextmanager
async def slot(provider_id: str, size: object, *, stop: Callable[[], bool] | None = None) -> AsyncIterator[None]:
    """Hold one of the provider's slots for the body of the `async with`. `size` is used
    only when the limit is first made; `resize` changes it."""
    limit = _limit(provider_id, size)
    await limit.acquire(stop)
    try:
        yield
    finally:
        limit.release()


def resize(provider_id: str, size: object) -> None:
    """Apply a changed `max_concurrent` to a limit already in use on this loop."""
    current = _limits.get(provider_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if current is not None and current[0] is loop:
        current[1].size = _clamp(size)
        current[1]._wake()


def in_use(provider_id: str) -> int:
    """Slots held right now on the running loop: for tests and diagnostics."""
    current = _limits.get(provider_id)
    if current is None or current[0] is not asyncio.get_running_loop():
        return 0
    return current[1].held
