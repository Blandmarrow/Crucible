"""Automated runs (M6 §4, decision 9) end to end against FakeComfy: a `Run` row stepped
forward by the worker's post-terminal hook, never by anything waiting.

What is pinned (docs/dev/comfyui/automation.md):
- a stage with nothing in scope is **skipped**, and score/caption are never started
  with `[]` (which they read as the whole dataset);
- a blueprint run goes prompts → handoffs → generate → score → caption → export;
- `advance_run` raising leaves the child job's outcome alone and the worker going;
- cancel, a cancel from outside the run (queued and running), pause and resume;
- the startup reconcile, the rewrite pause, the start-time checks, the stale guard.

Torch-free: technical scoring needs no model, aesthetic is off, and captioning goes
through the stubbed openai-compat provider.
"""
import asyncio

from sqlalchemy import select

from backend.models import BackgroundJob, Image, Run
from backend.services import run_service
from backend.tests.conftest import API, api_env, run, wait_for_job
from backend.tests.fake_comfy import FAIL_MARKER, FakeComfy
from backend.tests.test_blueprint_top_up_http import SPEC
from backend.tests.test_comfy_run_body import _add_row, _setup, _workflow
from backend.workers.job_queue import job_queue

STUB_CAPTION = "a stub caption"


async def _plan(env, monkeypatch, fake=None, *, blueprint=None):
    fake = fake or FakeComfy(canvas_running=False, complete=True)
    ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
    if blueprint is not None:
        r = await env.client.put(f"{API}/comfy/plans/{plan_id}/blueprint", json={"spec": blueprint, "seed": 3})
        assert r.status_code == 200, r.text
    return fake, ds, plan_id


async def _caption_model(env, monkeypatch) -> str:
    import backend.ml.openai_compat_captioner as mod

    async def _fake(image_path, **kwargs):
        return STUB_CAPTION

    monkeypatch.setattr(mod, "caption_image", _fake)
    r = await env.client.post(f"{API}/providers/", json={
        "name": "lmstudio", "base_url": "http://127.0.0.1:1234/v1", "default_model": "qwen-vl",
    })
    assert r.status_code == 201, r.text
    return f"openai_compat:{r.json()['id']}:qwen-vl"


async def _start(env, ds_id, plan_id, stages, **recipe) -> tuple[int, dict]:
    r = await env.client.post(f"{API}/runs/", json={
        "dataset_id": ds_id, "plan_id": plan_id, "name": "r1",
        "recipe": {"stages": stages, "score": {"run_aesthetic": False}, **recipe},
    })
    return r.status_code, r.json()


async def _get(env, run_id) -> dict:
    r = await env.client.get(f"{API}/runs/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def wait_for_run(env, run_id, until=None, timeout: float = 40.0) -> dict:
    """Poll the run until `until(run)` (default: no longer `running`). The hook runs
    after the child's terminal event, so a test waits on the run, not the job."""
    until = until or (lambda r: r["status"] != "running")
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        out = await _get(env, run_id)
        if until(out):
            return out
        assert asyncio.get_running_loop().time() < deadline, f"run never got there: {out}"
        await asyncio.sleep(0.05)


def _statuses(r: dict) -> dict[str, str]:
    return {s["key"]: s["status"] for s in r["stages"]}


async def _jobs(env, job_type: str) -> list[BackgroundJob]:
    async with env.Session() as db:
        return list((await db.execute(select(BackgroundJob).where(BackgroundJob.job_type == job_type))).scalars())


async def _block_worker(env) -> asyncio.Event:
    """Hold the single worker on a job of our own, so the next one stays queued."""
    gate, started = asyncio.Event(), asyncio.Event()
    async with env.Session() as db:
        job = BackgroundJob(job_type="test_gate", label="gate", total_items=0, config={})
        db.add(job)
        await db.commit()

    async def _hold(job_id: str) -> None:
        started.set()
        await gate.wait()

    await job_queue.enqueue(job, _hold)
    await asyncio.wait_for(started.wait(), 5)
    return gate


# 1 ───────────────────────────────────────────────────────────────────────────

def test_a_stage_with_no_images_is_skipped_and_never_started_with_an_empty_list(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, f"broken {FAIL_MARKER}")
            # An image the run did not make: a `[]` would widen score and caption to it.
            from backend.tests.conftest import upload_image
            await upload_image(env, ds["id"], "bystander.png")
            model = await _caption_model(env, monkeypatch)

            code, body = await _start(env, ds["id"], plan_id, ["prompts", "generate", "score", "caption"],
                                      caption={"model": model})
            assert code == 200, body
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed", done
            assert _statuses(done) == {"prompts": "skipped", "generate": "done", "score": "skipped", "caption": "skipped"}
            assert "no blueprint" in done["stages"][0]["note"]
            assert "1 failed" in done["stages"][1]["summary"]
            assert await _jobs(env, "quality_score") == []
            assert await _jobs(env, "caption") == []
    run(go())


# 2 ───────────────────────────────────────────────────────────────────────────

def test_a_blueprint_run_goes_through_every_stage(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch, blueprint=SPEC)
            model = await _caption_model(env, monkeypatch)
            out_dir = tmp_path / "export"
            stages = ["prompts", "handoff_to_comfy", "generate", "handoff_to_local", "score", "caption", "export"]
            code, body = await _start(env, ds["id"], plan_id, stages,
                                      generate={"subfolder": "gen"}, caption={"model": model},
                                      export={"kind": "plain", "output_dir": str(out_dir)})
            assert code == 200, body
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed", done
            assert set(_statuses(done).values()) == {"done"}, done
            assert done["row_count"] == 4
            assert done["stages"][0]["summary"] == "4 rows added, 0 reset, 4 to generate"
            assert len(fake.frees) == 1

            async with env.Session() as db:
                imgs = list((await db.execute(select(Image).where(Image.dataset_id == ds["id"]))).scalars())
            assert len(imgs) == 4
            assert {i.caption_text for i in imgs} == {STUB_CAPTION}
            assert all(i.blur_score is not None for i in imgs)
            # The fake returns one picture for every row, so the score stage's technical
            # pass flags three as duplicates, and the export leaves them out by default.
            dupes = [i for i in imgs if (i.quality_flags or {}).get("is_duplicate")]
            assert len(dupes) == 3
            assert len(list(out_dir.rglob("*.png"))) == 1
            assert done["stages"][-1]["summary"].startswith("1 image exported to ")

            # Children keep their own job types, tagged with the run in their labels.
            async with env.Session() as db:
                children = list((await db.execute(select(BackgroundJob))).scalars())
            assert {c.job_type for c in children} == {"run_handoff", "comfy_generate", "quality_score", "caption", "export"}
            assert all(c.label.startswith('Run "r1" · ') for c in children)
            assert all(c.config["run_id"] == body["id"] for c in children)

            # A second run: the folders are full, so nothing is generated and the
            # image stages skip rather than widening to the dataset.
            code, body2 = await _start(env, ds["id"], plan_id, ["prompts", "generate", "score"])
            assert code == 200, body2
            again = await wait_for_run(env, body2["id"])
            assert _statuses(again) == {"prompts": "done", "generate": "skipped", "score": "skipped"}
            assert len(await _jobs(env, "quality_score")) == 1
    run(go())


# 3 ───────────────────────────────────────────────────────────────────────────

def test_advance_run_raising_leaves_the_child_job_and_the_worker_alone(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")

            async def _boom(run_id, job_id):
                raise RuntimeError("advance_run exploded")

            monkeypatch.setattr(run_service, "advance_run", _boom)
            code, body = await _start(env, ds["id"], plan_id, ["generate"])
            assert code == 200, body
            job = await wait_for_job(env, body["current_job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["completed"] == 1

            # The worker took the next job.
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(tmp_path / "after"),
            })
            nxt = await wait_for_job(env, r.json()["job_id"])
            assert nxt["status"] == "completed", nxt
            # The run was never stepped: it waits for the restart reconcile or a cancel.
            assert (await _get(env, body["id"]))["status"] == "running"
    run(go())


# 4, 5 ────────────────────────────────────────────────────────────────────────

def test_cancel_while_the_child_is_queued_stops_the_run_and_queues_nothing_more(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")
            gate = await _block_worker(env)
            code, body = await _start(env, ds["id"], plan_id, ["generate", "handoff_to_local"])
            assert code == 200, body
            child = body["current_job_id"]
            r = await env.client.post(f"{API}/runs/{body['id']}/cancel")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "cancelled"
            gate.set()
            job = await wait_for_job(env, child)
            assert job["status"] == "cancelled"
            await job_queue._queue.join()
            done = await _get(env, body["id"])
            assert done["status"] == "cancelled"
            assert _statuses(done) == {"generate": "cancelled", "handoff_to_local": "pending"}
            assert await _jobs(env, "run_handoff") == []
    run(go())


def test_a_child_cancelled_outside_the_run_fails_it_queued_or_running(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            # Queued: the worker skips it on the path that never reaches `_finish`.
            fake, ds, plan_id = await _plan(env, monkeypatch, FakeComfy(canvas_running=False, complete=True, polls_before_done=30))
            await _add_row(env, plan_id, "a knight")
            gate = await _block_worker(env)
            code, body = await _start(env, ds["id"], plan_id, ["generate", "handoff_to_local"])
            assert code == 200, body
            assert (await env.client.delete(f"{API}/jobs/{body['current_job_id']}")).status_code == 204
            gate.set()
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "failed", done
            assert _statuses(done)["generate"] == "cancelled"
            assert "cancelled outside the run" in done["error_msg"]

            # Running: the cooperative cancel ends it as `cancelled` through `_finish`.
            r = await env.client.post(f"{API}/runs/{body['id']}/resume")
            assert r.status_code == 200, r.text
            child = r.json()["current_job_id"]
            await wait_for_run(env, body["id"], until=lambda _r: True)
            for _ in range(200):
                if (await env.client.get(f"{API}/jobs/{child}")).json()["status"] == "running":
                    break
                await asyncio.sleep(0.05)
            assert (await env.client.delete(f"{API}/jobs/{child}")).status_code == 204
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "failed", done
            assert "cancelled outside the run" in done["error_msg"]
    run(go())


# 6 ───────────────────────────────────────────────────────────────────────────

def test_pause_stops_at_the_boundary_and_resume_reruns_nothing_done(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")
            gate = await _block_worker(env)
            code, body = await _start(env, ds["id"], plan_id, ["generate", "handoff_to_local"])
            assert code == 200, body
            r = await env.client.post(f"{API}/runs/{body['id']}/pause")
            assert r.status_code == 200 and r.json()["pause_requested"] is True
            gate.set()
            paused = await wait_for_run(env, body["id"])
            assert paused["status"] == "paused", paused
            assert _statuses(paused) == {"generate": "done", "handoff_to_local": "pending"}

            r = await env.client.post(f"{API}/runs/{body['id']}/resume")
            assert r.status_code == 200, r.text
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed", done
            assert len(await _jobs(env, "comfy_generate")) == 1
            assert len(fake.submitted) == 1
    run(go())


# 7 ───────────────────────────────────────────────────────────────────────────

def test_the_startup_reconcile_pauses_a_running_run(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            async with env.Session() as db:
                r = Run(dataset_id=ds["id"], name="x", recipe={}, context={}, status="running",
                        current_job_id="gone",
                        stages=[{"key": "prompts", "status": "done"},
                                {"key": "generate", "status": "running", "job_id": "gone"}])
                db.add(r)
                await db.commit()
                run_id = r.id
            assert await run_service.reconcile_interrupted_runs() == 1
            out = await _get(env, run_id)
            assert out["status"] == "paused"
            assert out["current_job_id"] is None
            assert _statuses(out) == {"prompts": "done", "generate": "pending"}
            assert "restarted" in out["stages"][1]["note"]
    run(go())


# 8 ───────────────────────────────────────────────────────────────────────────

def test_rows_waiting_for_a_rewrite_pause_the_run(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            spec = {**SPEC, "scenarios": [{**SPEC["scenarios"][0], "rewrite": True}]}
            fake, ds, plan_id = await _plan(env, monkeypatch, blueprint=spec)
            code, body = await _start(env, ds["id"], plan_id, ["prompts", "generate"])
            assert code == 200, body
            assert body["status"] == "paused"
            assert _statuses(body) == {"prompts": "pending", "generate": "pending"}
            assert "wait for a rewrite" in body["stages"][0]["note"]
            assert await _jobs(env, "comfy_generate") == []
    run(go())


# 9 ───────────────────────────────────────────────────────────────────────────

def test_start_time_checks_refuse_before_writing_a_run(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")

            code, body = await _start(env, ds["id"], plan_id, ["caption"], caption={"model": "not-a-model"})
            assert code == 400, body
            code, body = await _start(env, ds["id"], plan_id, ["export"],
                                      export={"kind": "plain", "output_dir": "relative/dir"})
            assert code == 400, body
            code, body = await _start(env, ds["id"], plan_id, ["export"])
            assert code == 422, body
            async with env.Session() as db:
                assert list((await db.execute(select(Run))).scalars()) == []

            gate = await _block_worker(env)
            code, first = await _start(env, ds["id"], plan_id, ["generate"])
            assert code == 200, first
            code, body = await _start(env, ds["id"], plan_id, ["generate"])
            assert code == 409, body
            r = await env.client.delete(f"{API}/runs/{first['id']}")
            assert r.status_code == 409
            gate.set()
            await wait_for_run(env, first["id"])
    run(go())


# 10 ──────────────────────────────────────────────────────────────────────────

def test_a_job_for_a_stage_the_run_has_left_does_not_step_it(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")
            code, body = await _start(env, ds["id"], plan_id, ["generate", "handoff_to_local"])
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed"

            async with env.Session() as db:
                stale = BackgroundJob(job_type="export", label="old", status="completed", total_items=0,
                                      config={"run_id": body["id"], "run_stage": "0:generate"})
                db.add(stale)
                r = await db.get(Run, body["id"])
                r.status = "running"
                stages = [dict(s) for s in r.stages]
                stages[1]["status"] = "running"
                r.stages = stages
                await db.commit()
                stale_id = stale.id
            await run_service.advance_run(body["id"], stale_id)
            out = await _get(env, body["id"])
            assert out["status"] == "running"
            assert _statuses(out) == {"generate": "done", "handoff_to_local": "running"}
    run(go())
