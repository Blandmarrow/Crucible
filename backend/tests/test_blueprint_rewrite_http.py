"""The rewrite job (M6 §3) over HTTP: which rows it touches, `prompt_origin`'s writers,
the preview's figures, Top up's refusal, the guards and the outcome rules. The LLM is
a fake `RewriteSession`; the loop has its own tests (test_blueprint_rewrite.py)."""
import asyncio

from sqlalchemy import select

from backend.ml import prompt_generator
from backend.ml.prompt_generator import RewriteError
from backend.models import BackgroundJob
from backend.models.comfy import ComfyRow
from backend.tests.conftest import API, api_env, run, wait_for_job
from backend.tests.fake_comfy import FakeComfy
from backend.tests.test_comfy_run_body import PROMPT, _setup, _workflow

SPEC = {
    "axes": {"character": [{"id": "k", "label": "Iron Knight", "descriptor": "an iron knight"},
                           {"id": "w", "label": "Salt Witch", "descriptor": "a salt witch"}]},
    "variations": {"camera": ["low angle", "wide shot"]},
    "scenarios": [
        {"name": "prose", "combine": ["character"], "count": 2, "path": "prose/{character}",
         "template": "{character}, {camera}", "rewrite": True},
        {"name": "tags", "combine": ["character"], "count": 1, "path": "tags/{character}",
         "template": "{character}, {camera}"},
    ],
}


class FakeRewrite:
    """Stands in for RewriteSession. `script` items answer successive calls: "ok" or a
    RewriteError kind. Instances are kept, so a test can read what the job passed."""

    script: list = []
    instances: list = []
    gate: asyncio.Event | None = None
    started: asyncio.Event | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.max_requests = kwargs.get("max_requests")
        self.requests = 0
        self.mode = "schema"
        self.descriptors: list[list[str]] = []
        FakeRewrite.instances.append(self)

    async def rewrite(self, templates, descriptors):
        self.requests += 1
        self.descriptors.append(descriptors)
        if FakeRewrite.started is not None:
            FakeRewrite.started.set()
        if FakeRewrite.gate is not None:
            await FakeRewrite.gate.wait()
        item = FakeRewrite.script.pop(0) if FakeRewrite.script else "ok"
        if item != "ok":
            raise RewriteError(f"fake {item}", item)
        return [f"PROSE: {t}" for t in templates]


def _fake(monkeypatch, *script):
    FakeRewrite.script = list(script)
    FakeRewrite.instances = []
    FakeRewrite.gate = FakeRewrite.started = None
    monkeypatch.setattr(prompt_generator, "RewriteSession", FakeRewrite)


async def _plan(env, monkeypatch, fake_comfy=None):
    fake_comfy = fake_comfy or FakeComfy(canvas_running=False, complete=True)
    ds, plan_id = await _setup(env, monkeypatch, fake_comfy, _workflow(("9", "SaveImage")), ["9"])
    r = await env.client.post(f"{API}/providers/", json={
        "name": "fake-llm", "base_url": "http://127.0.0.1:9/v1", "default_model": "m",
    })
    assert r.status_code == 201, r.text
    return ds, plan_id, r.json()["id"]


async def _preview(env, plan_id, spec=SPEC):
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/preview", json={"spec": spec, "seed": 3})
    assert r.status_code == 200, r.text
    return r.json()


async def _apply(env, plan_id, spec=SPEC):
    pv = await _preview(env, plan_id, spec)
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/apply",
                              json={"spec": spec, "seed": 3, "confirm_total": pv["rows_to_add"]})
    assert r.status_code == 200, r.text
    return pv


async def _estimate(env, plan_id, batch_size=5):
    r = await env.client.get(f"{API}/comfy/plans/{plan_id}/blueprint/rewrite", params={"batch_size": batch_size})
    assert r.status_code == 200, r.text
    return r.json()


async def _rewrite(env, plan_id, provider_id, *, budget=None, batch_size=5, expect=200):
    if budget is None:
        budget = (await _estimate(env, plan_id, batch_size))["budget"]
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/rewrite", json={
        "provider_id": provider_id, "batch_size": batch_size, "confirm_budget": budget,
    })
    assert r.status_code == expect, r.text
    return r


async def _rows(env, plan_id):
    rows = (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json()
    return {r["id"]: r for r in rows}


def _by_folder(rows, prefix):
    return [r for r in rows.values() if r["subfolder"].startswith(prefix)]


def test_rewrite_turns_template_rows_to_prose_and_leaves_other_scenarios_alone(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, provider_id = await _plan(env, monkeypatch)
            r = await env.client.put(f"{API}/comfy/plans/{plan_id}/gen-settings",
                                     json={"instructions": "write like a novelist"})
            assert r.status_code == 200, r.text

            pv = await _preview(env, plan_id)
            # Two folders of two rows each in the rewrite scenario; one call each at batch 5.
            assert (pv["rewrite_rows"], pv["rewrite_calls"], pv["rewrite_call_budget"]) == (4, 2, 4)
            await _apply(env, plan_id)
            rows = await _rows(env, plan_id)
            assert {r["prompt_origin"] for r in rows.values()} == {"template"}
            # The flag survives the spec's save and read back.
            bp = (await env.client.get(f"{API}/comfy/plans/{plan_id}/blueprint")).json()["value"]
            assert [s["rewrite"] for s in bp["spec"]["scenarios"]] == [True, False]
            assert await _estimate(env, plan_id) == {"rows": 4, "leaves": 2, "calls": 2, "budget": 4}

            _fake(monkeypatch)
            r = await _rewrite(env, plan_id, provider_id)
            assert r.json()["total"] == 4
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            rd = job["result_data"]
            assert (rd["rewritten"], rd["requested"], rd["leaves"], rd["leaves_failed"]) == (4, 4, 2, 0)
            assert (rd["stop_reason"], rd["budget"], rd["plan_id"]) == ("done", 4, plan_id)

            fake = FakeRewrite.instances[0]
            assert fake.kwargs["system_instructions"] == "write like a novelist"
            assert fake.kwargs["max_requests"] == 4 and fake.kwargs["model_name"] == "m"
            assert fake.descriptors[0] == ['character "Iron Knight": an iron knight']

            rows = await _rows(env, plan_id)
            prose, tags = _by_folder(rows, "prose/"), _by_folder(rows, "tags/")
            assert all(r["values"][PROMPT].startswith("PROSE: ") and r["prompt_origin"] == "rewritten" for r in prose)
            assert all(not r["values"][PROMPT].startswith("PROSE") and r["prompt_origin"] == "template" for r in tags)
            # Nothing left to rewrite.
            assert (await _estimate(env, plan_id))["rows"] == 0
            r = await _rewrite(env, plan_id, provider_id, budget=0, expect=400)
            assert "Nothing to rewrite" in r.json()["detail"]
    run(go())


def test_hand_edits_are_never_rewritten(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, _ = await _plan(env, monkeypatch)
            await _apply(env, plan_id)
            prose = sorted(_by_folder(await _rows(env, plan_id), "prose/"), key=lambda r: r["sort_order"])

            # A PATCH that keeps the prompt keeps the origin; one that changes it is manual.
            r = await env.client.patch(f"{API}/comfy/rows/{prose[0]['id']}", json={"values": prose[0]["values"]})
            assert r.json()["prompt_origin"] == "template"
            r = await env.client.patch(f"{API}/comfy/rows/{prose[0]['id']}", json={"values": {PROMPT: "mine"}})
            assert r.json()["prompt_origin"] == "manual"
            # Bulk edit on one row.
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows/bulk-edit", json={
                "operation": "append", "text": ", sharp", "row_ids": [prose[1]["id"]],
            })
            assert r.status_code == 200, r.text
            rows = await _rows(env, plan_id)
            assert rows[prose[1]["id"]]["prompt_origin"] == "manual"
            assert (await _estimate(env, plan_id))["rows"] == 2

            # Set-value on the prompt column makes every row it changes manual.
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows/set-value",
                                      json={"alias": PROMPT, "value": "the same for all"})
            assert r.status_code == 200, r.text
            rows = await _rows(env, plan_id)
            assert {r["prompt_origin"] for r in rows.values()} == {"manual"}
            assert (await _estimate(env, plan_id))["rows"] == 0
    run(go())


def test_top_up_refuses_to_run_rows_that_wait_for_the_rewrite(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            comfy = FakeComfy(canvas_running=False, complete=True)
            _, plan_id, provider_id = await _plan(env, monkeypatch, comfy)
            pv = await _preview(env, plan_id)
            body = {"spec": SPEC, "seed": 3, "confirm_total": pv["rows_to_add"], "subfolder": "gen"}
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/top-up", json=body)
            assert r.status_code == 200, r.text
            out = r.json()
            # Applied, but nothing reset and nothing run.
            assert (out["created"], out["job_id"], out["total"], out["awaiting_rewrite"]) == (6, None, 0, 4)
            rows = await _rows(env, plan_id)
            assert len(rows) == 6 and {r["status"] for r in rows.values()} == {"pending"}

            _fake(monkeypatch)
            job = await wait_for_job(env, (await _rewrite(env, plan_id, provider_id)).json()["job_id"])
            assert job["status"] == "completed", job

            # Now Top up runs everything, prose included.
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/top-up", json={**body, "confirm_total": 0})
            assert r.status_code == 200, r.text
            out = r.json()
            assert (out["awaiting_rewrite"], out["total"]) == (0, 6)
            assert (await wait_for_job(env, out["job_id"]))["status"] == "completed"
    run(go())


def test_a_rewrite_that_rewrites_nothing_fails_and_keeps_the_templates(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, provider_id = await _plan(env, monkeypatch)
            await _apply(env, plan_id)
            before = {rid: r["values"] for rid, r in (await _rows(env, plan_id)).items()}
            _fake(monkeypatch, "fatal")
            job = await wait_for_job(env, (await _rewrite(env, plan_id, provider_id)).json()["job_id"])
            assert job["status"] == "failed", job
            assert job["error_msg"].startswith("Prompt rewrite failed: fake fatal")
            assert job["result_data"]["stop_reason"] == "provider_error"
            after = await _rows(env, plan_id)
            assert {rid: r["values"] for rid, r in after.items()} == before
            assert {r["prompt_origin"] for r in after.values()} == {"template"}
    run(go())


def test_a_partial_rewrite_completes_and_reports_the_failed_folder(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, provider_id = await _plan(env, monkeypatch)
            await _apply(env, plan_id)
            # First folder: three miscounts hit its cap of 1 + 2. Second folder: fine.
            _fake(monkeypatch, "mismatch", "mismatch", "mismatch", "ok")
            job = await wait_for_job(env, (await _rewrite(env, plan_id, provider_id)).json()["job_id"])
            assert job["status"] == "completed", job
            rd = job["result_data"]
            assert (rd["rewritten"], rd["leaves_done"], rd["leaves_failed"], rd["stop_reason"]) == (2, 1, 1, "done")
            assert len(rd["failed_paths"]) == 1
            assert job["total_items"] == 2
    run(go())


def test_a_stop_during_a_call_keeps_written_rows_and_ends_cancelled(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, provider_id = await _plan(env, monkeypatch)
            await _apply(env, plan_id)
            _fake(monkeypatch)
            FakeRewrite.started, FakeRewrite.gate = asyncio.Event(), asyncio.Event()
            job_id = (await _rewrite(env, plan_id, provider_id)).json()["job_id"]
            await asyncio.wait_for(FakeRewrite.started.wait(), 10)
            r = await env.client.delete(f"{API}/jobs/{job_id}")
            assert r.status_code in (200, 204), r.text
            FakeRewrite.gate.set()
            job = await wait_for_job(env, job_id)
            assert job["status"] == "cancelled", job
            # The call that was out when Stop arrived is written; the next is never made.
            assert job["result_data"]["rewritten"] == 2
            assert (await _estimate(env, plan_id))["rows"] == 2
    run(go())


def test_preflight_and_the_reviewed_budget(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, provider_id = await _plan(env, monkeypatch)
            url = f"{API}/comfy/plans/{plan_id}/blueprint/rewrite"
            body = {"provider_id": provider_id, "confirm_budget": 4}
            r = await env.client.post(url, json=body)
            assert r.status_code == 404 and "no blueprint" in r.json()["detail"], r.text
            await _apply(env, plan_id)
            r = await env.client.post(url, json={**body, "provider_id": "nope"})
            assert r.status_code == 404, r.text
            r = await env.client.post(url, json={**body, "confirm_budget": 9})
            assert r.status_code == 409 and "not the 9 you reviewed" in r.json()["detail"], r.text
            r = await env.client.get(url, params={"batch_size": 11})
            assert r.status_code == 400, r.text
            # A smaller batch means more calls, and so a different budget.
            assert await _estimate(env, plan_id, batch_size=1) == {"rows": 4, "leaves": 2, "calls": 4, "budget": 6}
    run(go())


async def _seed(env, job_type, plan_id):
    async with env.Session() as db:
        job = BackgroundJob(job_type=job_type, status="pending", config={"plan_id": plan_id})
        db.add(job)
        await db.commit()
        return job.id


async def _finish(env, job_id):
    async with env.Session() as db:
        (await db.get(BackgroundJob, job_id)).status = "completed"
        await db.commit()


def test_a_rewrite_overlaps_no_job_that_writes_or_runs_the_plans_rows(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, provider_id = await _plan(env, monkeypatch)
            pv = await _apply(env, plan_id)
            assert pv["rows_to_add"] == 6

            # A queued rewrite blocks Apply, Top up, a run and prompt generation.
            rewrite_id = await _seed(env, "comfy_rewrite", plan_id)
            for path, body in [
                ("blueprint/apply", {"spec": SPEC, "seed": 3, "confirm_total": 0}),
                ("blueprint/top-up", {"spec": SPEC, "seed": 3, "confirm_total": 0}),
                ("generate-prompts", {"provider_id": provider_id, "instruction": "x", "target_count": 50}),
            ]:
                r = await env.client.post(f"{API}/comfy/plans/{plan_id}/{path}", json=body)
                assert r.status_code == 409 and "rewrite" in r.json()["detail"], (path, r.text)
            r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id})
            assert r.status_code == 409 and "rewrite" in r.json()["detail"], r.text
            r = await _rewrite(env, plan_id, provider_id, expect=409)
            assert "queued or running" in r.json()["detail"], r.text
            await _finish(env, rewrite_id)

            # And each of those blocks a rewrite.
            for job_type in ("comfy_generate", "comfy_prompts", "comfy_rewrite"):
                job_id = await _seed(env, job_type, plan_id)
                r = await _rewrite(env, plan_id, provider_id, expect=409)
                assert "queued or running" in r.json()["detail"], (job_type, r.text)
                await _finish(env, job_id)

            # Another plan's jobs and an outline do not.
            other_ds = await env.create_dataset("other-ds")
            r = await env.client.post(f"{API}/comfy/plans", json={
                "dataset_id": other_ds["id"], "name": "other", "workflow_json": _workflow(("9", "SaveImage")),
                "pinned_params": [{"node_id": "6", "input": "text", "alias": PROMPT, "is_prompt": True}],
                "output_node_ids": ["9"],
            })
            assert r.status_code == 200, r.text
            other_plan = r.json()["id"]
            await _seed(env, "comfy_rewrite", other_plan)
            await _seed(env, "comfy_outline", plan_id)
            _fake(monkeypatch)
            job = await wait_for_job(env, (await _rewrite(env, plan_id, provider_id)).json()["job_id"])
            assert job["status"] == "completed", job
            async with env.Session() as db:
                origins = (await db.execute(select(ComfyRow.prompt_origin).where(ComfyRow.plan_id == plan_id))).scalars().all()
            assert sorted(origins) == ["rewritten"] * 4 + ["template"] * 2
    run(go())


def test_write_rewrites_rechecks_each_row_and_keeps_its_other_values(tmp_path, monkeypatch):
    """Against the database, not a fake writer: the UPDATE's own WHERE is what keeps a
    hand edit or a finished row out, and `values` keeps every other key."""
    from backend.services import blueprint_rewrite

    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, _ = await _plan(env, monkeypatch)
            await _apply(env, plan_id)
            async with env.Session() as db:
                rows = (await db.execute(
                    select(ComfyRow).where(ComfyRow.plan_id == plan_id, ComfyRow.subfolder.like("prose/%"))
                    .order_by(ComfyRow.sort_order)
                )).scalars().all()
                manual, done, failed, extra = rows
                manual.prompt_origin = "manual"
                done.status = "completed"
                failed.status, failed.error_msg, failed.prompt_id = "failed", "oom", "p1"
                extra.values = {**extra.values, "seed": 7}
                await db.commit()
                ids = [manual.id, done.id, failed.id, extra.id, "gone"]

            async with env.Session() as db:
                written = await blueprint_rewrite.write_rewrites(db, PROMPT, [(i, f"prose {i}") for i in ids])
                await db.commit()
            assert written == 2

            async with env.Session() as db:
                got = {r.id: r for r in (await db.execute(select(ComfyRow).where(ComfyRow.id.in_(ids)))).scalars()}
            assert got[manual.id].prompt_origin == "manual" and not got[manual.id].values[PROMPT].startswith("prose")
            assert got[done.id].prompt_origin == "template" and got[done.id].status == "completed"
            # A failed row is rewritten and goes back to pending, as a hand edit would.
            f = got[failed.id]
            assert (f.prompt_origin, f.status, f.error_msg, f.prompt_id) == ("rewritten", "pending", None, None)
            assert got[extra.id].values == {PROMPT: f"prose {extra.id}", "seed": 7}
    run(go())


def test_row_edits_wait_for_a_live_rewrite(tmp_path, monkeypatch):
    # The table PATCHes a whole cached row: during a rewrite that would put the
    # template text back over prose the pass just wrote.
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, _ = await _plan(env, monkeypatch)
            await _apply(env, plan_id)
            row = next(iter((await _rows(env, plan_id)).values()))
            await _seed(env, "comfy_rewrite", plan_id)

            r = await env.client.patch(f"{API}/comfy/rows/{row['id']}", json={"values": {PROMPT: "mine", "seed": 1}})
            assert r.status_code == 409 and "rewrite" in r.json()["detail"], r.text
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows/set-value", json={"alias": PROMPT, "value": "x"})
            assert r.status_code == 409, r.text
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows/bulk-edit",
                                      json={"operation": "append", "text": "!"})
            assert r.status_code == 409, r.text
            # A folder change writes no values, so it goes ahead.
            r = await env.client.patch(f"{API}/comfy/rows/{row['id']}", json={"subfolder": "elsewhere"})
            assert r.status_code == 200, r.text
            assert (await _rows(env, plan_id))[row["id"]]["values"] == row["values"]
    run(go())


def test_the_job_rewrites_only_the_scenarios_that_were_reviewed(tmp_path, monkeypatch):
    from backend.services import blueprint_rewrite

    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id, provider_id = await _plan(env, monkeypatch)
            await _apply(env, plan_id)
            _fake(monkeypatch)
            FakeRewrite.started, FakeRewrite.gate = asyncio.Event(), asyncio.Event()
            r = await _rewrite(env, plan_id, provider_id)
            job_id = r.json()["job_id"]
            async with env.Session() as db:
                assert (await db.get(BackgroundJob, job_id)).config["scenarios"] == ["prose"]
            await asyncio.wait_for(FakeRewrite.started.wait(), 10)
            FakeRewrite.gate.set()
            assert (await wait_for_job(env, job_id))["status"] == "completed"

            # Switched on after review: the narrowed selection leaves it out.
            both = {**SPEC, "scenarios": [{**s, "rewrite": True} for s in SPEC["scenarios"]]}
            r = await env.client.put(f"{API}/comfy/plans/{plan_id}/blueprint", json={"spec": both, "seed": 3})
            assert r.status_code == 200, r.text
            async with env.Session() as db:
                assert await blueprint_rewrite.select_targets(db, plan_id, PROMPT, {"prose"}) == []
                assert len(await blueprint_rewrite.select_targets(db, plan_id, PROMPT)) == 2
    run(go())
