"""`JobQueue` lifecycle: bounded shutdown (GPT-012) and a worker that survives a
failed job start (GPT-013).

Ported from the gpt-review probes with their assertions inverted. Service-level:
each scenario drives a private `JobQueue` against a throwaway SQLite database, with
the worker module's session factory patched to it.

The shape that matters most: `stop()` used to deliver one `task.cancel()` that the
per-job `except CancelledError` swallowed, so the worker went back to `get()` and
`stop()` awaited it forever. Every test that stops a queue with a job in flight
therefore bounds `stop()` itself with `asyncio.wait_for` — a regression fails here
instead of hanging the run.
"""

import asyncio
import logging
import threading
import time

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models  # noqa: F401  (registers every table on Base.metadata)
import backend.workers.job_queue as jq_mod
from backend.database import Base
from backend.models import BackgroundJob
from backend.workers.job_queue import INTERRUPTED_MSG, JobQueue, mark_interrupted_jobs
from backend.workers.progress import broadcaster


async def _env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(jq_mod, "AsyncSessionLocal", session_local)
    return engine, session_local


async def _add(session_local, job_id, job_type="t"):
    async with session_local() as db:
        job = BackgroundJob(id=job_id, job_type=job_type, status="pending")
        db.add(job)
        await db.commit()
    return job


async def _row(session_local, job_id):
    async with session_local() as db:
        return await db.get(BackgroundJob, job_id)


def _capture_events(monkeypatch):
    events: list[dict] = []
    real = broadcaster.emit

    async def capturing(job_id, event):
        events.append({**event, "job_id": job_id})
        await real(job_id, event)

    monkeypatch.setattr(broadcaster, "emit", capturing)
    return events


def test_idle_stop_returns(tmp_path, monkeypatch):
    async def go():
        engine, _ = await _env(tmp_path, monkeypatch)
        q = JobQueue()
        await q.start()
        await asyncio.sleep(0.05)
        await asyncio.wait_for(q.stop(), 1.0)
        assert q._worker_task.done()
        await engine.dispose()

    asyncio.run(go())


def test_cooperative_job_stops_within_grace_and_queued_job_never_runs(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue()
        await q.start()
        started = asyncio.Event()
        ran2 = asyncio.Event()

        async def cooperative(job_id):
            started.set()
            while True:
                q.raise_if_cancelled(job_id)
                await asyncio.sleep(0.02)

        async def second(job_id):
            ran2.set()

        await q.enqueue(await _add(S, "j1"), cooperative)
        await q.enqueue(await _add(S, "j2"), second)
        await asyncio.wait_for(started.wait(), 2)

        t0 = time.monotonic()
        await asyncio.wait_for(q.stop(grace=5.0), 3.0)
        # Well inside the grace: the job honoured the flag, nothing was forced.
        assert time.monotonic() - t0 < 2.0
        assert q._worker_task.done() and not q._worker_task.cancelled()

        j1 = await _row(S, "j1")
        assert j1.status == "failed" and j1.error_msg == INTERRUPTED_MSG
        await asyncio.sleep(0.1)
        assert not ran2.is_set()
        assert (await _row(S, "j2")).status == "pending"

        # The next startup's sweep resolves the job that never started, with the
        # same message.
        assert await mark_interrupted_jobs() == 1
        j2 = await _row(S, "j2")
        assert j2.status == "failed" and j2.error_msg == INTERRUPTED_MSG
        await engine.dispose()

    asyncio.run(go())


def test_job_ignoring_the_flag_is_force_stopped(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        events = _capture_events(monkeypatch)
        q = JobQueue()
        await q.start()
        started = asyncio.Event()

        async def stubborn(job_id):
            started.set()
            await asyncio.sleep(3600)

        await q.enqueue(await _add(S, "a"), stubborn)
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(q.stop(grace=0.3, unwind=2.0), 3.0)

        assert q._worker_task.cancelled()
        row = await _row(S, "a")
        assert row.status == "failed" and row.error_msg == INTERRUPTED_MSG
        terminal = [e for e in events if e.get("status") == "failed"]
        assert terminal and terminal[-1]["message"] == INTERRUPTED_MSG
        await engine.dispose()

    asyncio.run(go())


def test_cancel_during_completion_bookkeeping_leaves_row_for_the_sweep(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue()
        await q.start()
        gate = asyncio.Event()

        class Blocker:
            async def __aenter__(self):
                gate.set()
                await asyncio.sleep(3600)

            async def __aexit__(self, *exc):
                return False

        async def fn(job_id):
            # The completion write's session open is the next one: make it hang.
            jq_mod.AsyncSessionLocal = Blocker

        await q.enqueue(await _add(S, "a"), fn)
        await asyncio.wait_for(gate.wait(), 2)
        await asyncio.wait_for(q.stop(grace=0.2, unwind=1.0), 3.0)
        jq_mod.AsyncSessionLocal = S

        assert q._worker_task.done()
        assert (await _row(S, "a")).status == "running"
        assert await mark_interrupted_jobs() == 1
        row = await _row(S, "a")
        assert row.status == "failed" and row.error_msg == INTERRUPTED_MSG
        await engine.dispose()

    asyncio.run(go())


def test_executor_job_stop_returns_while_thread_runs(tmp_path, monkeypatch):
    """The documented limit: an executor thread cannot be stopped, only abandoned."""

    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue()
        await q.start()
        started = threading.Event()
        finished = threading.Event()

        def blocking():
            started.set()
            time.sleep(1.0)
            finished.set()

        async def fn(job_id):
            await asyncio.get_running_loop().run_in_executor(None, blocking)

        await q.enqueue(await _add(S, "a"), fn)
        while not started.is_set():
            await asyncio.sleep(0.01)
        await asyncio.wait_for(q.stop(grace=0.1, unwind=1.0), 2.0)

        assert not finished.is_set()
        row = await _row(S, "a")
        assert row.status == "failed" and row.error_msg == INTERRUPTED_MSG
        while not finished.is_set():
            await asyncio.sleep(0.05)
        await engine.dispose()

    asyncio.run(go())


def test_user_cancel_before_stop_stays_cancelled(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue()
        await q.start()
        started = asyncio.Event()

        async def slow_to_notice(job_id):
            started.set()
            # Notices the flag only after a while — long enough for stop() to
            # arrive while the user's cancel is still pending.
            await asyncio.sleep(0.3)
            q.raise_if_cancelled(job_id)

        await q.enqueue(await _add(S, "a"), slow_to_notice)
        await asyncio.wait_for(started.wait(), 2)
        q.request_cancel("a")
        await asyncio.wait_for(q.stop(grace=2.0), 3.0)

        row = await _row(S, "a")
        assert row.status == "cancelled" and row.error_msg is None
        await engine.dispose()

    asyncio.run(go())


def test_cooperative_cancel_leaves_queue_usable(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue()
        await q.start()
        started = asyncio.Event()
        ran2 = asyncio.Event()

        async def cancellable(job_id):
            started.set()
            while True:
                q.raise_if_cancelled(job_id)
                await asyncio.sleep(0.02)

        async def second(job_id):
            ran2.set()

        await q.enqueue(await _add(S, "a"), cancellable)
        await q.enqueue(await _add(S, "b"), second)
        await asyncio.wait_for(started.wait(), 2)
        q.request_cancel("a")
        await asyncio.wait_for(ran2.wait(), 2)
        await asyncio.wait_for(q._queue.join(), 2)

        assert (await _row(S, "a")).status == "cancelled"
        assert (await _row(S, "b")).status == "completed"
        assert q._queue._unfinished_tasks == 0
        assert q.current_job_id is None
        assert not q._worker_task.done()
        await asyncio.wait_for(q.stop(), 1.0)
        await engine.dispose()

    asyncio.run(go())


def test_start_works_after_stop(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue()
        await q.start()
        started = asyncio.Event()

        async def stubborn(job_id):
            started.set()
            await asyncio.sleep(3600)

        await q.enqueue(await _add(S, "a"), stubborn)
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(q.stop(grace=0.1, unwind=1.0), 2.0)

        # What `api_env` does between tests: a fresh queue, the same object.
        q._queue = asyncio.Queue()
        await q.start()
        ran = asyncio.Event()

        async def quick(job_id):
            ran.set()

        await q.enqueue(await _add(S, "b"), quick)
        await asyncio.wait_for(ran.wait(), 2)
        await asyncio.wait_for(q._queue.join(), 2)
        assert (await _row(S, "b")).status == "completed"
        await asyncio.wait_for(q.stop(), 1.0)
        await engine.dispose()

    asyncio.run(go())


def test_non_cooperative_job_type_gets_the_long_grace(tmp_path, monkeypatch):
    """A restore that ignores the flag is waited for, not cut off at the default."""

    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        monkeypatch.setattr(jq_mod, "STOP_GRACE_SECONDS", 0.1)
        monkeypatch.setattr(jq_mod, "NON_COOPERATIVE_GRACE", {"restore_snapshot": 3.0})
        q = JobQueue()
        await q.start()
        started = asyncio.Event()

        async def restore(job_id):
            started.set()
            await asyncio.sleep(0.6)  # never polls the flag

        await q.enqueue(await _add(S, "r", job_type="restore_snapshot"), restore)
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(q.stop(), 4.0)

        assert (await _row(S, "r")).status == "completed"
        assert not q._worker_task.cancelled()

        # ...while any other type gets the short default and is forced.
        q._queue = asyncio.Queue()
        await q.start()
        started.clear()

        async def other(job_id):
            started.set()
            await asyncio.sleep(0.6)

        await q.enqueue(await _add(S, "o", job_type="caption"), other)
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(q.stop(), 2.0)
        row = await _row(S, "o")
        assert row.status == "failed" and row.error_msg == INTERRUPTED_MSG
        await engine.dispose()

    asyncio.run(go())


def test_session_failure_at_start_fails_only_that_job(tmp_path, monkeypatch):
    """GPT-013: a raise while writing `running` used to kill the only worker."""

    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        armed = {"on": True}

        def flaky():
            if armed["on"]:
                armed["on"] = False
                raise RuntimeError("db open failed")
            return S()

        monkeypatch.setattr(jq_mod, "AsyncSessionLocal", flaky)
        q = JobQueue()
        await q.start()
        ran = []

        async def fn(job_id):
            ran.append(job_id)

        await q.enqueue(await _add(S, "a"), fn)
        await q.enqueue(await _add(S, "b"), fn)
        await asyncio.wait_for(q._queue.join(), 2)

        a = await _row(S, "a")
        assert a.status == "failed"
        assert "Could not start job" in a.error_msg and "db open failed" in a.error_msg
        assert (await _row(S, "b")).status == "completed"
        assert ran == ["b"]
        assert not q._worker_task.done()
        assert q._queue._unfinished_tasks == 0
        assert q.current_job_id is None
        await asyncio.wait_for(q.stop(), 1.0)
        await engine.dispose()

    asyncio.run(go())


def test_failed_completion_write_ends_failed_and_emits_failed(tmp_path, monkeypatch):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        events = _capture_events(monkeypatch)
        q = JobQueue()
        await q.start()

        real_write = JobQueue._write_final

        async def flaky_write(job_id, status, error):
            if status == "completed":
                raise RuntimeError("disk I/O error")
            return await real_write(job_id, status, error)

        monkeypatch.setattr(JobQueue, "_write_final", staticmethod(flaky_write))

        async def fn(job_id):
            pass

        await q.enqueue(await _add(S, "a"), fn)
        await asyncio.wait_for(q._queue.join(), 2)

        row = await _row(S, "a")
        assert row.status == "failed"
        assert "could not be recorded" in row.error_msg and "disk I/O error" in row.error_msg
        terminal = [e for e in events if e.get("status") in ("completed", "failed", "cancelled")]
        assert [e["status"] for e in terminal] == ["failed"]
        assert not q._worker_task.done()
        await asyncio.wait_for(q.stop(), 1.0)
        await engine.dispose()

    asyncio.run(go())


def test_unexpected_worker_death_is_logged_critical(tmp_path, monkeypatch, caplog):
    async def go():
        engine, S = await _env(tmp_path, monkeypatch)
        q = JobQueue()

        async def broken_get():
            raise RuntimeError("queue broke")

        q._queue.get = broken_get
        with caplog.at_level(logging.CRITICAL, logger="backend.workers.job_queue"):
            await q.start()
            await asyncio.sleep(0.05)
        assert q._worker_task.done()
        critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert critical and "no further background jobs" in critical[0].getMessage()
        assert critical[0].exc_info and "queue broke" in str(critical[0].exc_info[1])
        await engine.dispose()

    asyncio.run(go())
