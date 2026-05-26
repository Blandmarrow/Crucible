import asyncio
import json
from collections import defaultdict
from typing import AsyncGenerator


class ProgressBroadcaster:
    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
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
        for q in list(self._queues.get(job_id, [])):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
        for q in list(self._queues.get("all", [])):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def stream(self, channel: str, stop_on_terminal: bool = True) -> AsyncGenerator[dict, None]:
        q = self.subscribe(channel)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield event
                    if stop_on_terminal and event.get("status") in ("completed", "failed", "cancelled"):
                        break
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat"}
        finally:
            self.unsubscribe(channel, q)


broadcaster = ProgressBroadcaster()
