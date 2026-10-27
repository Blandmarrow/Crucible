"""`ProgressBroadcaster`: a terminal event is never dropped (GPT-014).

Ported from the gpt-review probes with the drop assertion inverted. A subscriber
that stops draining keeps at most `SUBSCRIBER_BACKLOG_CAP` progress ticks, but the
`completed`/`failed`/`cancelled` event that ends a job is enqueued regardless — it
is the one event nothing later supersedes, and the one the UI's invalidations and
toasts hang off.
"""

import asyncio

import pytest

from backend.workers.progress import SUBSCRIBER_BACKLOG_CAP, ProgressBroadcaster


def _drain(q: asyncio.Queue) -> list[dict]:
    return [q.get_nowait() for _ in range(q.qsize())]


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_terminal_event_survives_a_full_backlog(terminal):
    async def go():
        b = ProgressBroadcaster()
        qj = b.subscribe("J")
        qa = b.subscribe("all")
        for i in range(SUBSCRIBER_BACKLOG_CAP + 50):
            await b.emit("J", {"type": "progress", "status": "running", "done": i})
        await b.emit("J", {"type": "progress", "status": terminal})

        for q in (qj, qa):
            events = _drain(q)
            # The ticks past the cap were dropped...
            assert len(events) == SUBSCRIBER_BACKLOG_CAP + 1
            assert all(e["status"] == "running" for e in events[:-1])
            assert [e["done"] for e in events[:-1]] == list(range(SUBSCRIBER_BACKLOG_CAP))
            # ...and the terminal event was not.
            assert events[-1]["status"] == terminal
            assert events[-1]["job_id"] == "J"

    asyncio.run(go())


def test_backlog_cap_is_per_subscriber():
    async def go():
        b = ProgressBroadcaster()
        stalled = b.subscribe("J")
        for i in range(SUBSCRIBER_BACKLOG_CAP):
            await b.emit("J", {"status": "running", "done": i})
        fresh = b.subscribe("J")
        await b.emit("J", {"status": "running", "done": -1})
        assert stalled.qsize() == SUBSCRIBER_BACKLOG_CAP
        assert _drain(fresh) == [{"status": "running", "done": -1, "job_id": "J"}]

    asyncio.run(go())


def test_no_replay_for_a_late_subscriber():
    """The stated contract: live only. A client that missed the terminal event
    reconciles from `GET /jobs`, not from the stream."""

    async def go():
        b = ProgressBroadcaster()
        await b.emit("J", {"status": "completed"})
        q = b.subscribe("J")
        assert q.empty()
        b.unsubscribe("J", q)

        async def first():
            async for ev in b.stream("J"):
                return ev

        # Nothing within a second (the first heartbeat is 25 s out).
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first(), 1.0)

    asyncio.run(go())


def test_stream_stops_on_terminal():
    async def go():
        b = ProgressBroadcaster()
        seen = []

        async def consume():
            async for ev in b.stream("J"):
                seen.append(ev["status"])

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await b.emit("J", {"status": "running"})
        await b.emit("J", {"status": "failed"})
        await asyncio.wait_for(task, 1.0)
        assert seen == ["running", "failed"]
        assert "J" not in b._queues

    asyncio.run(go())
