"""Unit tests for the `background_jobs` retention sweep.

`sweep_old_jobs()` runs once per startup and is the only thing that ever deletes
from a table every captioning/export/detection run appends to. The rules it must
not break:

- only finished rows are eligible — a pending/running row is never touched;
- rows inside the 30-day window survive regardless of age ordering;
- the newest `JOB_RETENTION_MIN_KEEP` rows survive even when every one of them is
  older than the window (an install idle for a year still shows its last runs).

The keep-floor is exercised with the constants patched down to a testable size;
seeding 500+ rows to assert the same branch would only be slower.
"""
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models  # noqa: F401 — register every model on Base
import backend.workers.job_queue as job_queue_mod
from backend.database import Base
from backend.models import BackgroundJob
from backend.tests.conftest import run


async def _seed(Session, rows: list[tuple[str, str, int]]) -> None:
    """rows: (id, status, days_ago)."""
    now = datetime.utcnow()
    async with Session() as db:
        for job_id, status, days_ago in rows:
            db.add(BackgroundJob(
                id=job_id,
                job_type="captioning",
                status=status,
                created_at=now - timedelta(days=days_ago),
            ))
        await db.commit()


async def _ids(Session) -> set[str]:
    from sqlalchemy import select
    async with Session() as db:
        return {r[0] for r in (await db.execute(select(BackgroundJob.id))).all()}


async def _env(tmp_path):
    """A throwaway DB wired into the module global sweep_old_jobs resolves."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, Session


def test_sweep_keeps_recent_and_unfinished_jobs(tmp_path, monkeypatch):
    async def scenario():
        engine, Session = await _env(tmp_path)
        monkeypatch.setattr(job_queue_mod, "AsyncSessionLocal", Session)
        monkeypatch.setattr(job_queue_mod, "JOB_RETENTION_MIN_KEEP", 2)
        try:
            await _seed(Session, [
                ("old-done", "completed", 60),
                ("old-failed", "failed", 45),
                ("old-cancelled", "cancelled", 40),
                ("old-running", "running", 90),    # unfinished — never swept
                ("old-pending", "pending", 90),    # unfinished — never swept
                ("fresh-done", "completed", 1),
                ("fresh-failed", "failed", 2),
            ])
            deleted = await job_queue_mod.sweep_old_jobs()
            assert deleted == 3
            assert await _ids(Session) == {
                "old-running", "old-pending", "fresh-done", "fresh-failed",
            }
        finally:
            await engine.dispose()

    run(scenario())


def test_sweep_always_keeps_the_newest_min_keep_rows(tmp_path, monkeypatch):
    async def scenario():
        engine, Session = await _env(tmp_path)
        monkeypatch.setattr(job_queue_mod, "AsyncSessionLocal", Session)
        monkeypatch.setattr(job_queue_mod, "JOB_RETENTION_MIN_KEEP", 3)
        try:
            # Every row is well outside the retention window.
            await _seed(Session, [
                ("j1", "completed", 400),
                ("j2", "completed", 300),
                ("j3", "completed", 200),
                ("j4", "completed", 100),
                ("j5", "completed", 50),
            ])
            deleted = await job_queue_mod.sweep_old_jobs()
            assert deleted == 2
            assert await _ids(Session) == {"j3", "j4", "j5"}
        finally:
            await engine.dispose()

    run(scenario())


def test_sweep_is_a_noop_below_the_keep_floor(tmp_path, monkeypatch):
    async def scenario():
        engine, Session = await _env(tmp_path)
        monkeypatch.setattr(job_queue_mod, "AsyncSessionLocal", Session)
        monkeypatch.setattr(job_queue_mod, "JOB_RETENTION_MIN_KEEP", 10)
        try:
            await _seed(Session, [("j1", "completed", 999), ("j2", "failed", 999)])
            assert await job_queue_mod.sweep_old_jobs() == 0
            assert await _ids(Session) == {"j1", "j2"}
        finally:
            await engine.dispose()

    run(scenario())
