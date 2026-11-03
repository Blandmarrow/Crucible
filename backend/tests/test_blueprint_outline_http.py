"""The outline job (M6 §2b): a brief → a draft blueprint saved for review, never rows.
The LLM call is replaced; parse_outline has its own tests (test_parse_outline.py)."""
import asyncio

from backend.ml import prompt_generator
from backend.models import BackgroundJob
from backend.tests.conftest import API, api_env, run, wait_for_job

WORKFLOW = {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "template prompt"}},
    "9": {"class_type": "SaveImage", "inputs": {}},
}
DRAFT = {
    "axes": {"character": [{"id": "k", "label": "Iron Knight", "descriptor": "a tall knight"}]},
    "variations": {"camera": ["low angle"]},
    "scenarios": [{"name": "solo", "combine": ["character"], "count": 3, "path": "{character}",
                   "template": "{character}, {camera}"}],
}


async def _setup(env):
    ds = await env.create_dataset("outline-ds")
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds["id"], "name": "p", "workflow_json": WORKFLOW,
        "pinned_params": [{"node_id": "6", "input": "text", "alias": "prompt", "is_prompt": True}],
        "output_node_ids": ["9"],
    })
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]
    r = await env.client.post(f"{API}/providers/", json={
        "name": "fake-llm", "base_url": "http://127.0.0.1:9/v1", "default_model": "m",
    })
    assert r.status_code == 201, r.text
    return plan_id, r.json()["id"]


def _fake(monkeypatch, result=None, error=None, calls=None):
    async def fake_outline(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        if error:
            raise error
        return result
    monkeypatch.setattr(prompt_generator, "generate_outline", fake_outline)


async def _outline(env, plan_id, provider_id, brief="a medieval set"):
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/outline",
                              json={"provider_id": provider_id, "brief": brief})
    assert r.status_code == 200, r.text
    return await wait_for_job(env, r.json()["job_id"])


async def _blueprint(env, plan_id):
    return (await env.client.get(f"{API}/comfy/plans/{plan_id}/blueprint")).json()["value"]


def test_the_draft_is_saved_for_review_and_writes_no_rows(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            plan_id, provider_id = await _setup(env)
            r = await env.client.put(f"{API}/comfy/plans/{plan_id}/gen-settings",
                                     json={"instructions": "booru tags, comma separated"})
            assert r.status_code == 200, r.text
            calls: list[dict] = []
            _fake(monkeypatch, DRAFT, calls=calls)

            job = await _outline(env, plan_id, provider_id, brief="knights in fens")
            assert job["status"] == "completed", job
            assert job["result_data"] == {"plan_id": plan_id, "axes": 1, "scenarios": 1}
            # The brief and the plan's standing instructions reach the call; the
            # provider's settings come from its row.
            assert calls[0]["brief"] == "knights in fens"
            assert calls[0]["system_instructions"] == "booru tags, comma separated"
            assert calls[0]["model_name"] == "m"

            bp = await _blueprint(env, plan_id)
            assert bp["draft"]["axes"]["character"][0]["label"] == "Iron Knight"
            assert bp["brief"] == "knights in fens"
            # Created for the draft, but the spec stays empty until the user uses it.
            assert bp["spec"] == {"axes": {}, "variations": {}, "scenarios": []}
            assert (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json() == []
            # The saved instructions are never rewritten by the call.
            gs = (await env.client.get(f"{API}/comfy/plans/{plan_id}/gen-settings")).json()["value"]
            assert gs["instructions"] == "booru tags, comma separated"

            r = await env.client.delete(f"{API}/comfy/plans/{plan_id}/blueprint/draft")
            assert r.status_code == 204, r.text
            assert (await _blueprint(env, plan_id))["draft"] is None
            r = await env.client.delete(f"{API}/comfy/plans/{plan_id}/blueprint/draft")
            assert r.status_code == 204, r.text
    run(go())


def test_an_existing_spec_is_untouched_by_a_draft(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            plan_id, provider_id = await _setup(env)
            mine = {**DRAFT, "variations": {"camera": ["mine"]}}
            r = await env.client.put(f"{API}/comfy/plans/{plan_id}/blueprint", json={"spec": mine, "seed": 4})
            assert r.status_code == 200, r.text
            _fake(monkeypatch, DRAFT)
            assert (await _outline(env, plan_id, provider_id))["status"] == "completed"
            bp = await _blueprint(env, plan_id)
            assert bp["spec"]["variations"] == {"camera": ["mine"]} and bp["seed"] == 4
            assert bp["draft"]["variations"] == {"camera": ["low angle"]}
    run(go())


def test_a_draft_with_semantic_errors_is_still_saved(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            plan_id, provider_id = await _setup(env)
            bad = {**DRAFT, "scenarios": [{**DRAFT["scenarios"][0], "template": "{character}, {mood}"}]}
            _fake(monkeypatch, bad)
            assert (await _outline(env, plan_id, provider_id))["status"] == "completed"
            assert (await _blueprint(env, plan_id))["draft"]["scenarios"][0]["template"] == "{character}, {mood}"
    run(go())


def test_a_malformed_draft_or_a_provider_error_fails_the_job(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            plan_id, provider_id = await _setup(env)
            malformed = {**DRAFT, "scenarios": [{**DRAFT["scenarios"][0], "combine": [{"axis": "character", "take": 9}]}]}
            _fake(monkeypatch, malformed)
            job = await _outline(env, plan_id, provider_id)
            assert job["status"] == "failed"
            assert "malformed" in job["error_msg"] and "take" in job["error_msg"]

            _fake(monkeypatch, error=RuntimeError("the model did not return a blueprint as JSON"))
            job = await _outline(env, plan_id, provider_id)
            assert job["status"] == "failed"
            assert job["error_msg"].startswith("Blueprint draft failed: the model did not return")
            # No draft, but the brief is kept, so a failure never costs a retype.
            bp = await _blueprint(env, plan_id)
            assert bp["draft"] is None and bp["brief"] == "a medieval set"
            assert bp["spec"] == {"axes": {}, "variations": {}, "scenarios": []}
    run(go())


def test_the_malformed_message_names_the_field_not_the_union_branch(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            plan_id, provider_id = await _setup(env)
            malformed = {**DRAFT, "scenarios": [{**DRAFT["scenarios"][0], "combine": [{"axis": "character", "take": 9}]}]}
            _fake(monkeypatch, malformed)
            job = await _outline(env, plan_id, provider_id)
            assert "malformed at scenarios.0.combine.0.take:" in job["error_msg"]
    run(go())


def test_a_stop_during_the_call_saves_no_draft(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            plan_id, provider_id = await _setup(env)
            started, release = asyncio.Event(), asyncio.Event()

            async def slow_outline(**_kwargs):
                started.set()
                await release.wait()
                return DRAFT
            monkeypatch.setattr(prompt_generator, "generate_outline", slow_outline)

            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/outline",
                                      json={"provider_id": provider_id, "brief": "x"})
            assert r.status_code == 200, r.text
            job_id = r.json()["job_id"]
            await asyncio.wait_for(started.wait(), 10)
            r = await env.client.delete(f"{API}/jobs/{job_id}")
            assert r.status_code in (200, 204), r.text
            release.set()

            job = await wait_for_job(env, job_id)
            assert job["status"] == "cancelled", job
            assert (await _blueprint(env, plan_id))["draft"] is None
    run(go())


def test_preflight_404_and_one_outline_job_per_plan(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            plan_id, provider_id = await _setup(env)
            url = f"{API}/comfy/plans/{plan_id}/blueprint/outline"
            r = await env.client.post(url, json={"provider_id": "nope", "brief": "x"})
            assert r.status_code == 404, r.text

            async with env.Session() as db:
                outline = BackgroundJob(job_type="comfy_outline", status="pending", config={"plan_id": plan_id})
                db.add(outline)
                await db.commit()
                outline_id = outline.id
            r = await env.client.post(url, json={"provider_id": provider_id, "brief": "x"})
            assert r.status_code == 409, r.text

            # A run on the same plan does not block it: an outline writes no rows.
            async with env.Session() as db:
                (await db.get(BackgroundJob, outline_id)).status = "completed"
                db.add(BackgroundJob(job_type="comfy_generate", status="pending", config={"plan_id": plan_id}))
                await db.commit()
            _fake(monkeypatch, DRAFT)
            r = await env.client.post(url, json={"provider_id": provider_id, "brief": "x"})
            assert r.status_code == 200, r.text
    run(go())
