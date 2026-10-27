"""`GET /jobs/` filters: `status` and `ids` (GPT-014).

The frontend's reconcile (`frontend/src/utils/jobReconcile.ts`) asks for the jobs
it is watching by id, and for every job active right now by status, to catch up on
terminal events an SSE gap lost. Rows are written directly: what is under test is
the listing, not the worker.
"""

from datetime import datetime, timedelta

from backend.models import BackgroundJob
from backend.tests.conftest import API, api_env, run

_ROWS = [
    ("p1", "pending"),
    ("r1", "running"),
    ("c1", "completed"),
    ("f1", "failed"),
    ("x1", "cancelled"),
    ("c2", "completed"),
]


async def _seed(env):
    base = datetime(2026, 1, 1)
    async with env.Session() as db:
        for i, (job_id, status) in enumerate(_ROWS):
            db.add(BackgroundJob(
                id=job_id, job_type="t", status=status, created_at=base + timedelta(minutes=i),
            ))
        await db.commit()


async def _ids(env, **params):
    r = await env.client.get(f"{API}/jobs/", params=params)
    assert r.status_code == 200, r.text
    return [j["id"] for j in r.json()]


def test_filter_by_status(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await _seed(env)
            assert await _ids(env, status="pending,running") == ["r1", "p1"]
            assert await _ids(env, status="completed") == ["c2", "c1"]
            # Whitespace around the separators is tolerated.
            assert await _ids(env, status=" failed , cancelled ") == ["x1", "f1"]
            # No filter: everything, newest first.
            assert await _ids(env) == ["c2", "x1", "f1", "c1", "r1", "p1"]

    run(scenario())


def test_filter_by_ids_and_both(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await _seed(env)
            assert await _ids(env, ids="p1,c1,unknown") == ["c1", "p1"]
            # ANDed with status.
            assert await _ids(env, ids="p1,c1,f1", status="completed,failed") == ["f1", "c1"]
            # And with limit, newest first.
            assert await _ids(env, ids="p1,c1,f1", limit=2) == ["f1", "c1"]
            # An ids filter naming nothing matches nothing, never "no filter".
            assert await _ids(env, ids="") == []
            assert await _ids(env, ids=" , ") == []

    run(scenario())


def test_unknown_status_and_too_many_ids_are_422(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/jobs/", params={"status": "running,stuck"})
            assert r.status_code == 422, r.text
            assert "stuck" in r.json()["detail"]

            many = ",".join(f"id{i}" for i in range(501))
            r = await env.client.get(f"{API}/jobs/", params={"ids": many})
            assert r.status_code == 422, r.text

            ok = ",".join(f"id{i}" for i in range(500))
            r = await env.client.get(f"{API}/jobs/", params={"ids": ok})
            assert r.status_code == 200, r.text

    run(scenario())
