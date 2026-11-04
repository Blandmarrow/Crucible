"""The run's snapshot stage (M6 §6): an opt-in undo point taken once, before the first
pass, and the cancel that waits for it.

What is pinned (docs/dev/comfyui/run-stages.md § The stages;
docs/dev/comfyui/automation.md § Cancel, pause, resume, restart):
- versioning off → the stage is skipped with a note, and the run goes on;
- the snapshot is a head stage: a looping run takes it once, not once per pass;
- a cancel during a running snapshot waits for it (`cancel_requested`), then the run
  is cancelled with the version recorded; a queued snapshot is cancelled at once;
- the restart reconcile cancels a run whose cancel was waiting;
- a resume never takes a second snapshot once one is done.

Torch-free; the loop case needs cv2 for the gate, like `test_run_loop.py`.
"""
import asyncio

from sqlalchemy import select

from backend.models import BackgroundJob, Run
from backend.models.versioning import DatasetVersion
from backend.services import run_service, version_service
from backend.tests.conftest import API, api_env, needs_cv2, run, wait_for_job
from backend.tests.test_comfy_run_body import _add_row
from backend.tests.test_run_loop import BLURRY, GOOD, LOOP_STAGES, _loop, _loop_plan, _spec
from backend.tests.test_run_stepping import (
    _block_worker, _get, _jobs, _plan, _start, _statuses, wait_for_run,
)
from backend.workers.job_queue import job_queue


async def _mode(env, mode: str) -> None:
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": mode})
    assert r.status_code == 200, r.text


async def _versions(env, ds_id: str) -> list[DatasetVersion]:
    async with env.Session() as db:
        return list((await db.execute(
            select(DatasetVersion).where(DatasetVersion.dataset_id == ds_id)
        )).scalars())


def _hold_snapshots(monkeypatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Make the snapshot job wait on a gate once it is running, so a cancel can land
    while it is mid-flight. The route's job looks the service up at call time."""
    started, gate = asyncio.Event(), asyncio.Event()
    real = version_service.create_snapshot

    async def _held(*args, **kwargs):
        started.set()
        await gate.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(version_service, "create_snapshot", _held)
    return started, gate


def test_versioning_off_skips_the_snapshot_with_a_note(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            await _mode(env, "off")
            await _add_row(env, plan_id, "a knight")
            code, body = await _start(env, ds["id"], plan_id, ["snapshot", "generate"])
            assert code == 200, body
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed", done
            assert _statuses(done) == {"snapshot": "skipped", "generate": "done"}
            assert "Versioning is off" in done["stages"][0]["note"]
            assert done["stages"][0]["pass_no"] is None
            assert await _jobs(env, "create_snapshot") == []
    run(go())


def test_the_snapshot_runs_first_and_records_the_version(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            await _mode(env, "manual")
            await _add_row(env, plan_id, "a knight")
            code, body = await _start(env, ds["id"], plan_id, ["generate", "snapshot"])
            assert code == 200, body
            assert [s["key"] for s in body["stages"]] == ["snapshot", "generate"]
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed", done
            versions = await _versions(env, ds["id"])
            assert len(versions) == 1
            assert versions[0].name == "Before run r1"
            assert done["stages"][0]["summary"] == '0 images recorded as "Before run r1"'
            [job] = await _jobs(env, "create_snapshot")
            assert job.config["run_id"] == body["id"]
            assert job.label.startswith('Run "r1" · Snapshot the dataset — ')
    run(go())


@needs_cv2
def test_a_looping_run_takes_its_snapshot_once(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, BLURRY))
            await _mode(env, "manual")
            code, body = await _start(
                env, ds["id"], plan_id, ["snapshot"] + LOOP_STAGES,
                gate={"exclude_flags": ["is_blurry", "is_duplicate"]}, loop=_loop(),
            )
            assert code == 200, body
            done = await wait_for_run(env, body["id"], timeout=90)
            assert done["status"] == "completed", done
            assert {s["pass_no"] for s in done["stages"] if s["pass_no"]} == {1, 2}
            assert [s["key"] for s in done["stages"]].count("snapshot") == 1
            assert len(await _jobs(env, "create_snapshot")) == 1
            assert len(await _versions(env, ds["id"])) == 1
    run(go())


def test_a_cancel_during_a_running_snapshot_waits_for_it(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            await _mode(env, "manual")
            await _add_row(env, plan_id, "a knight")
            started, gate = _hold_snapshots(monkeypatch)
            code, body = await _start(env, ds["id"], plan_id, ["snapshot", "generate"])
            assert code == 200, body
            await asyncio.wait_for(started.wait(), 5)

            r = await env.client.post(f"{API}/runs/{body['id']}/cancel")
            assert r.status_code == 200, r.text
            out = r.json()
            # Still running: the snapshot cannot stop partway, so nothing reads cancelled yet.
            assert out["status"] == "running" and out["cancel_requested"] is True
            assert _statuses(out)["snapshot"] == "running"
            # Pause and resume are refused while it waits; a second cancel is a no-op.
            assert (await env.client.post(f"{API}/runs/{body['id']}/pause")).status_code == 409
            assert (await env.client.post(f"{API}/runs/{body['id']}/resume")).status_code == 409
            assert (await env.client.post(f"{API}/runs/{body['id']}/cancel")).status_code == 200

            gate.set()
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "cancelled", done
            assert done["cancel_requested"] is False
            assert _statuses(done) == {"snapshot": "done", "generate": "pending"}
            assert len(await _versions(env, ds["id"])) == 1
            assert await _jobs(env, "comfy_generate") == []
    run(go())


def test_a_cancel_while_the_snapshot_is_queued_is_immediate(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            await _mode(env, "manual")
            worker = await _block_worker(env)
            code, body = await _start(env, ds["id"], plan_id, ["snapshot", "generate"])
            assert code == 200, body
            child = body["current_job_id"]
            r = await env.client.post(f"{API}/runs/{body['id']}/cancel")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "cancelled"
            worker.set()
            assert (await wait_for_job(env, child))["status"] == "cancelled"
            await job_queue._queue.join()
            done = await _get(env, body["id"])
            assert _statuses(done) == {"snapshot": "cancelled", "generate": "pending"}
            assert await _versions(env, ds["id"]) == []
    run(go())


def test_the_reconcile_cancels_a_run_whose_cancel_was_waiting(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            async with env.Session() as db:
                r = Run(dataset_id=ds["id"], name="x", recipe={}, context={}, status="running",
                        current_job_id="gone", cancel_requested=True,
                        stages=[{"key": "snapshot", "status": "running", "job_id": "gone"},
                                {"key": "generate", "status": "pending"}])
                db.add(r)
                await db.commit()
                run_id = r.id
            assert await run_service.reconcile_interrupted_runs() == 1
            out = await _get(env, run_id)
            assert out["status"] == "cancelled" and out["cancel_requested"] is False
            assert out["finished_at"] is not None
            assert _statuses(out) == {"snapshot": "cancelled", "generate": "pending"}
    run(go())


def test_a_resume_never_takes_a_second_snapshot(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            await _mode(env, "manual")
            await _add_row(env, plan_id, "a knight")
            worker = await _block_worker(env)
            code, body = await _start(env, ds["id"], plan_id, ["snapshot", "generate", "handoff_to_local"])
            assert code == 200, body
            r = await env.client.post(f"{API}/runs/{body['id']}/pause")
            assert r.status_code == 200, r.text
            worker.set()
            paused = await wait_for_run(env, body["id"])
            assert paused["status"] == "paused", paused
            assert _statuses(paused)["snapshot"] == "done"
            r = await env.client.post(f"{API}/runs/{body['id']}/resume")
            assert r.status_code == 200, r.text
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed", done
            assert len(await _jobs(env, "create_snapshot")) == 1
            assert len(await _versions(env, ds["id"])) == 1
    run(go())


def test_the_snapshot_route_still_answers_with_a_job_and_records_its_result(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _mode(env, "manual")
            r = await env.client.post(f"{API}/datasets/{ds['id']}/versions", json={"name": "hand"})
            assert r.status_code == 200, r.text
            job_id = r.json()["job_id"]
            job = await wait_for_job(env, job_id)
            assert job["status"] == "completed", job
            async with env.Session() as db:
                row = await db.get(BackgroundJob, job_id)
                assert row.result_data["name"] == "hand"
                assert row.config == {}
    run(go())
