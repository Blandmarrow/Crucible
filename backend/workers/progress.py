import asyncio
from collections import defaultdict
from typing import AsyncGenerator

# The statuses after which a job emits nothing more. A per-job stream closes on one.
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

# Most events a subscriber can have waiting before *non-terminal* ones are dropped.
# A slow or stalled reader loses progress ticks, which the next tick supersedes; it
# never loses the terminal event, which nothing supersedes (GPT-014).
SUBSCRIBER_BACKLOG_CAP = 200


class ProgressBroadcaster:
    """Fan-out of job progress events to SSE subscribers, live only.

    There is no replay and no snapshot: a subscriber sees only what is emitted while
    it is subscribed. A client that was disconnected for a while (a network gap, a
    server restart) reconciles from `GET /jobs` instead — see
    `frontend/src/utils/jobReconcile.ts`.
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, channel: str) -> asyncio.Queue:
        # Unbounded on purpose: the cap lives in `emit`, so that it can apply to
        # non-terminal events only. A terminal event is always enqueued, so the
        # overshoot is at most one event per job.
        q: asyncio.Queue = asyncio.Queue()
        self._queues[channel].append(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        try:
            self._queues[channel].remove(q)
        except ValueError:
            pass
        # Remove the channel key when the last subscriber leaves so the dict
        # doesn't accumulate entries for every job that has ever run.
        if channel in self._queues and not self._queues[channel]:
            del self._queues[channel]

    async def emit(self, job_id: str, event: dict) -> None:
        payload = {**event, "job_id": job_id}
        terminal = payload.get("status") in TERMINAL_STATUSES
        for channel in (job_id, "all"):
            for q in list(self._queues.get(channel, [])):
                if terminal or q.qsize() < SUBSCRIBER_BACKLOG_CAP:
                    q.put_nowait(payload)

    async def stream(self, channel: str, stop_on_terminal: bool = True) -> AsyncGenerator[dict, None]:
        q = self.subscribe(channel)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield event
                    if stop_on_terminal and event.get("status") in TERMINAL_STATUSES:
                        break
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat"}
        finally:
            self.unsubscribe(channel, q)


broadcaster = ProgressBroadcaster()
