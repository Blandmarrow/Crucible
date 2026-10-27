import asyncio, pytest
from backend.workers.progress import ProgressBroadcaster


# ---- GPT-014 backend ----
def test_014_full_queue_drops_terminal():
    async def go():
        b = ProgressBroadcaster()
        qj = b.subscribe("J"); qa = b.subscribe("all")
        for i in range(200):
            await b.emit("J", {"type": "progress", "status": "running", "done": i})
        await b.emit("J", {"type": "progress", "status": "completed"})
        evs_j = [qj.get_nowait() for _ in range(qj.qsize())]
        evs_a = [qa.get_nowait() for _ in range(qa.qsize())]
        print(len(evs_j), len(evs_a), [e["status"] for e in evs_j][-1])
        assert len(evs_j) == 200 and all(e["status"] == "running" for e in evs_j)
        assert len(evs_a) == 200 and all(e["status"] == "running" for e in evs_a)
    asyncio.run(go())


def test_014_no_replay_for_late_subscriber():
    async def go():
        b = ProgressBroadcaster()
        await b.emit("J", {"status": "completed"})
        q = b.subscribe("J")
        assert q.empty()
        # stream would only emit heartbeats; bounded observation
        async def first():
            async for ev in b.stream("J"):
                return ev
        # heartbeat arrives after 25s -> just assert nothing within 1s
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first(), 1.0)
    asyncio.run(go())
