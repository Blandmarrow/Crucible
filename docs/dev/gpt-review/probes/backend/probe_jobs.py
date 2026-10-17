import asyncio, pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import backend.models  # noqa
import backend.workers.job_queue as jq_mod
from backend.workers.job_queue import JobQueue
from backend.workers.progress import ProgressBroadcaster
from backend.database import Base
from backend.models import BackgroundJob


async def _env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'j.db'}")
    S = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(jq_mod, "AsyncSessionLocal", S)
    return engine, S


async def _add(S, jid):
    async with S() as db:
        j = BackgroundJob(id=jid, job_type="t", status="pending")
        db.add(j); await db.commit()
    return j


async def _status(S, jid):
    async with S() as db:
        return (await db.get(BackgroundJob, jid)).status


def test_012_idle_stop_completes(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue(); await q.start(); await asyncio.sleep(0.05)
        await asyncio.wait_for(q.stop(), 1.0)
    asyncio.run(go())


def test_012_active_job_stop_hangs_and_queued_job_runs(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue(); await q.start()
        ran2 = asyncio.Event(); started1 = asyncio.Event()
        async def fn1(job_id):
            started1.set(); await asyncio.sleep(3600)
        async def fn2(job_id):
            ran2.set()
        j1 = await _add(S, "j1"); j2 = await _add(S, "j2")
        await q.enqueue(j1, fn1); await q.enqueue(j2, fn2)
        await asyncio.wait_for(started1.wait(), 2)
        stop = asyncio.create_task(q.stop())
        await asyncio.sleep(0.5)
        print("stop done after 0.5s?", stop.done(), "j1:", await _status(S, "j1"),
              "j2 ran after stop requested:", ran2.is_set(), "j2:", await _status(S, "j2"))
        # claim: second queued function ran after stop was requested
        assert ran2.is_set()
        assert await _status(S, "j1") == "cancelled"
        # claim: stop still pending (worker looped back to get())
        await asyncio.sleep(0.5)
        print("stop done after 1s?", stop.done(), "worker done?", q._worker_task.done())
        assert not stop.done()
        # cleanup: second cancel finishes it
        q._worker_task.cancel()
        await asyncio.wait_for(stop, 2)
        await engine.dispose()
    asyncio.run(go())


def test_012_active_job_no_queued_stop_hangs(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue(); await q.start()
        started = asyncio.Event()
        async def fn(job_id):
            started.set(); await asyncio.sleep(3600)
        await q.enqueue(await _add(S, "a"), fn)
        await asyncio.wait_for(started.wait(), 2)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(t := asyncio.create_task(q.stop())), 1.0)
        print("stop hang reproduced; status:", await _status(S, "a"))
        q._worker_task.cancel(); await asyncio.wait_for(t, 2)
        await engine.dispose()
    asyncio.run(go())


def test_012_cancel_during_completion_bookkeeping_hangs(tmp_path, monkeypatch):
    """Does the unexplained doc'd hang arise when cancel lands in the post-fn DB section (inside try)?"""
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue(); await q.start()
        gate = asyncio.Event(); real = jq_mod.AsyncSessionLocal
        calls = {"n": 0}
        class Slow:
            def __call__(self):
                calls["n"] += 1
                return real()
        # make the completion-session open block: patch only after fn finished
        async def fn(job_id):
            jq_mod.AsyncSessionLocal = Blocker  # next session open (completion) blocks
        class Blocker:
            def __init__(self): pass
            async def __aenter__(self):
                gate.set(); await asyncio.sleep(3600)
            async def __aexit__(self, *a): return False
        await q.enqueue(await _add(S, "a"), fn)
        await asyncio.wait_for(gate.wait(), 2)
        jq_mod.AsyncSessionLocal = S
        t = asyncio.create_task(q.stop())
        done, _ = await asyncio.wait({t}, timeout=1.0)
        print("stop finished while cancel landed in completion bookkeeping:", bool(done))
        assert not done
        q._worker_task.cancel(); await asyncio.wait_for(t, 2)
        await engine.dispose()
    asyncio.run(go())


def test_012_executor_cancel_marks_cancelled_thread_runs(tmp_path, monkeypatch):
    import threading, time
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue(); await q.start()
        started = threading.Event(); finished = threading.Event()
        def blocking():
            started.set(); time.sleep(1.0); finished.set()
        async def fn(job_id):
            await asyncio.get_running_loop().run_in_executor(None, blocking)
        await q.enqueue(await _add(S, "a"), fn)
        while not started.is_set(): await asyncio.sleep(0.01)
        t = asyncio.create_task(q.stop()); await asyncio.sleep(0.2)
        st = await _status(S, "a")
        print("status", st, "thread finished yet?", finished.is_set())
        assert st == "cancelled" and not finished.is_set()
        q._worker_task.cancel(); await asyncio.wait_for(t, 2)
        await engine.dispose()
    asyncio.run(go())


def test_013_session_failure_kills_worker(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue(); 
        real = S
        boom = {"armed": True}
        def flaky():
            if boom["armed"]:
                boom["armed"] = False
                raise RuntimeError("db open failed")
            return real()
        monkeypatch.setattr(jq_mod, "AsyncSessionLocal", flaky)
        await q.start()
        ran = asyncio.Event()
        async def fn(job_id): ran.set()
        await q.enqueue(await _add(real, "a"), fn)
        await asyncio.sleep(0.3)
        print("worker done:", q._worker_task.done(), "exc:", q._worker_task.exception() if q._worker_task.done() else None)
        assert q._worker_task.done()
        await q.enqueue(await _add(real, "b"), fn)
        await asyncio.sleep(0.5)
        print("a:", await _status(real, "a"), "b:", await _status(real, "b"), "ran:", ran.is_set(),
              "queue unfinished:", q._queue._unfinished_tasks, "current_job_id:", q.current_job_id)
        assert not ran.is_set() and await _status(real, "b") == "pending"
        await engine.dispose()
    asyncio.run(go())


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
