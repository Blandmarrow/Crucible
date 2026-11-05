"""The M6 §8 review fixes, over HTTP: one delete rule, a live Run's target refusing new
rows, a resulting-length cap on folder and category moves, the split `rows/copy` skip
counts, and the case-sensitive blueprint lock."""
import sqlite3
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import BackgroundJob, Run
from backend.tests.conftest import API, api_env, run
from backend.tests.test_comfy_rows_copy import _copy, _link, _row, _setup
from backend.tests.test_comfy_rows_copy import _rows as _plan_rows
from backend.tests.test_comfy_subfolder import _add_row, _link_row, _plan
from backend.tests.test_conflict_paths_http import _seed_pending_job

LIB = f"{API}/comfy/library"


async def _seed_run(env, ds_id, plan_id, status):
    async with env.Session() as db:
        db.add(Run(dataset_id=ds_id, plan_id=plan_id, name="x", recipe={}, stages=[],
                   context={}, status=status))
        await db.commit()


async def _clear_jobs(env):
    async with env.Session() as db:
        for j in (await db.execute(BackgroundJob.__table__.select())).all():
            (await db.get(BackgroundJob, j.id)).status = "completed"
        await db.commit()


# ── Deleting a plan or rows: the folder-delete rule ───────────────────────────

def test_plan_delete_refuses_a_live_job_or_run_and_runs_when_idle(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            for kind in ("comfy_generate", "comfy_rewrite", "comfy_prompts"):
                ds, plan_id = await _plan(env, ds_name=f"d-{kind}", plan_name=kind)
                await _seed_pending_job(env, kind, plan_id)
                r = await env.client.delete(f"{API}/comfy/plans/{plan_id}")
                assert r.status_code == 409, (kind, r.text)
                await _clear_jobs(env)
            for status in ("running", "paused"):
                ds, plan_id = await _plan(env, ds_name=f"d-{status}", plan_name=status)
                await _seed_run(env, ds["id"], plan_id, status)
                r = await env.client.delete(f"{API}/comfy/plans/{plan_id}")
                assert r.status_code == 409 and "automated run" in r.json()["detail"], status
            ds, plan_id = await _plan(env, ds_name="d-idle", plan_name="idle")
            r = await env.client.delete(f"{API}/comfy/plans/{plan_id}")
            assert r.status_code == 204, r.text
    run(go())


def test_rows_delete_refuses_a_run_a_rewrite_and_a_prompts_job(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds, plan_id = await _plan(env)
            row = (await _add_row(env, plan_id))["id"]
            url = f"{API}/comfy/plans/{plan_id}/rows/delete"
            await _seed_run(env, ds["id"], plan_id, "running")
            r = await env.client.post(url, json={"row_ids": [row]})
            assert r.status_code == 409 and "automated run" in r.json()["detail"]
            async with env.Session() as db:
                from sqlalchemy import update
                await db.execute(update(Run).values(status="completed"))
                await db.commit()
            for kind in ("comfy_rewrite", "comfy_prompts"):
                await _seed_pending_job(env, kind, plan_id)
                r = await env.client.post(url, json={"row_ids": [row]})
                assert r.status_code == 409, (kind, r.text)
                await _clear_jobs(env)
            r = await env.client.post(url, json={"row_ids": [row]})
            assert r.status_code == 200 and r.json() == {"deleted": 1}
    run(go())


# ── A live Run's target refuses new rows ──────────────────────────────────────

def test_copy_and_bulk_refuse_a_target_with_a_live_run(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            source, target = await _setup(env)
            from backend.models.comfy import ComfyPlan
            async with env.Session() as db:
                ds_id = (await db.get(ComfyPlan, target)).dataset_id
            row = await _row(env, source, {"text": "x"})
            await _seed_run(env, ds_id, target, "paused")
            r = await _copy(env, target, source_plan_id=source, row_ids=[row])
            assert r.status_code == 409 and "automated run" in r.json()["detail"]
            r = await env.client.post(f"{API}/comfy/plans/{target}/rows/bulk", json={"lines": ["a"]})
            assert r.status_code == 409 and "automated run" in r.json()["detail"]
            # Add row too: the Run's next pass would take a single row as its own just the same.
            r = await env.client.post(f"{API}/comfy/plans/{target}/rows", json={"values": {}})
            assert r.status_code == 409 and "automated run" in r.json()["detail"]
            assert await _plan_rows(env, target) == []
    run(go())


def test_copy_from_a_plan_with_a_live_run_is_allowed(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            source, target = await _setup(env)
            from backend.models.comfy import ComfyPlan
            async with env.Session() as db:
                src_ds = (await db.get(ComfyPlan, source)).dataset_id
            row = await _row(env, source, {"text": "x"})
            await _seed_run(env, src_ds, source, "running")
            r = await _copy(env, target, source_plan_id=source, row_ids=[row])
            assert r.status_code == 200 and r.json()["copied"] == 1, r.text
    run(go())


# ── Resulting length ──────────────────────────────────────────────────────────

def test_folder_move_caps_the_resulting_path_at_512(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            await _add_row(env, plan_id, subfolder="a/x")
            url = f"{API}/comfy/plans/{plan_id}/folders/move"
            r = await env.client.post(url, json={"path": "a", "new_path": "n" * 511})
            assert r.status_code == 400 and "512" in r.json()["detail"], r.text
            r = await env.client.post(url, json={"path": "a", "new_path": "n" * 510})
            assert r.status_code == 200, r.text
            assert (await _plan_rows(env, plan_id))[0]["subfolder"] == "n" * 510 + "/x"
    run(go())


def test_category_move_caps_the_resulting_path_at_512(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            r = await env.client.post(LIB, json={"category": "a/x", "prompts": ["p"]})
            assert r.status_code == 200, r.text
            url = f"{LIB}/categories/move"
            r = await env.client.post(url, json={"path": "a", "new_path": "n" * 511})
            assert r.status_code == 400 and "512" in r.json()["detail"], r.text
            r = await env.client.post(url, json={"path": "a", "new_path": "n" * 510})
            assert r.status_code == 200 and r.json()["moved"] == 1, r.text
    run(go())


# ── Copy: path traversal, counts ──────────────────────────────────────────────

def test_copy_rejects_a_dotdot_subfolder(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            source, target = await _setup(env)
            row = await _row(env, source, {"text": "x"})
            r = await _copy(env, target, source_plan_id=source, row_ids=[row], subfolder="../x")
            assert r.status_code == 400, r.text
    run(go())


def test_copy_splits_the_skip_counts_and_counts_unlinked_only_when_moved(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            source, target = await _setup(env)
            linked = await _row(env, source, {"text": "linked"}, "L")
            empty_linked = await _row(env, source, {"seed": 1}, "L")   # no prompt anywhere
            empty = await _row(env, source, {"seed": 2})
            free = await _row(env, source, {"text": "free"})
            await _link(env, source, linked, empty_linked)
            body = {"source_plan_id": source, "row_ids": [linked, empty_linked, empty, free], "move": True}

            r = await _copy(env, target, **body, linked="skip")
            assert r.json() == {"moved": 1, "skipped": 3, "skipped_no_prompt": 1,
                                "skipped_linked": 2, "unlinked": 0}, r.text

            # Unlink: the linked row with no prompt is skipped, not moved, so not counted.
            r = await _copy(env, target, **{**body, "row_ids": [linked, empty_linked]}, linked="unlink")
            assert r.json() == {"moved": 1, "skipped": 1, "skipped_no_prompt": 1,
                                "skipped_linked": 0, "unlinked": 1}, r.text
    run(go())


# ── The blueprint lock is case-sensitive ──────────────────────────────────────

def test_folder_move_lock_is_case_sensitive(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            linked = await _add_row(env, plan_id, subfolder="Knight")
            await _add_row(env, plan_id, subfolder="knight")
            await _link_row(env, plan_id, linked["id"], path="Knight")
            url = f"{API}/comfy/plans/{plan_id}/folders/move"
            r = await env.client.post(url, json={"path": "Knight", "new_path": "x"})
            assert r.status_code == 409, r.text
            r = await env.client.post(url, json={"path": "knight", "new_path": "y"})
            assert r.status_code == 200 and r.json() == {"updated": 1}, r.text
    run(go())


# ── The plan-name race: the unique index fires at duplicate's first flush ─────

def test_duplicate_losing_a_name_race_is_the_pre_checks_400(tmp_path, monkeypatch):
    """A plan named like the duplicate lands between the pre-check and the first flush (a
    second click, or a create racing it). The unique index then fires at that flush, which
    must answer the pre-check's 400, not a 500."""
    async def go():
        async with api_env(tmp_path) as env:
            _ds, plan_id = await _plan(env)
            orig = AsyncSession.flush
            state = {"done": False}

            async def racing_flush(self, *a, **k):
                if not state["done"]:
                    state["done"] = True
                    con = sqlite3.connect(tmp_path / "test.db")
                    try:
                        cur = con.execute("select * from comfy_plans where id=?", (plan_id,))
                        cols = [d[0] for d in cur.description]
                        clash = dict(zip(cols, cur.fetchone()))
                        clash.update(id=uuid.uuid4().hex, name="copy")
                        con.execute(
                            f"insert into comfy_plans ({','.join(clash)}) values ({','.join('?' * len(clash))})",
                            list(clash.values()),
                        )
                        con.commit()
                    finally:
                        con.close()
                return await orig(self, *a, **k)

            monkeypatch.setattr(AsyncSession, "flush", racing_flush)
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/duplicate", json={"name": "copy"})
            monkeypatch.undo()
            assert state["done"], "the race was never injected"
            assert r.status_code == 400 and "already exists" in r.json()["detail"], r.text
    run(go())
