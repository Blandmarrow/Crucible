"""The dry run and the shared start check (M6 §6): `run_service.plan_run`.

What is pinned (docs/dev/comfyui/automation.md § Starting, and the checks it runs first):
- a dry run writes nothing: no run, job, row or leaf, and ComfyUI gets no prompt;
- the start raises the check's first error, so a run that the dry run says cannot
  start is never written — ComfyUI unreachable, a missing caption provider, a model
  its provider does not list;
- warnings (versioning off with Snapshot on, a model list nobody could fetch) never
  block;
- the dry run's first-pass row count is the count the run really generates, with the
  image cap's round-robin clamp applied the same way (`round_robin_counts`).

Torch-free and cv2-free: no stage here scores.
"""
import httpx
import pytest
from sqlalchemy import func, select

from backend.models import BackgroundJob, ComfyRow, Run
from backend.services import run_service
from backend.tests.conftest import API, api_env, run
from backend.tests.fake_comfy import FakeComfy
from backend.tests.test_blueprint_top_up_http import SPEC
from backend.tests.test_comfy_run_body import _add_row
from backend.tests.test_run_loop import GOOD, _loop, _loop_plan, _spec
from backend.tests.test_run_stepping import _plan, _start, wait_for_run


async def _dry(env, ds_id, plan_id, stages, **recipe) -> tuple[int, dict]:
    r = await env.client.post(f"{API}/runs/dry-run", json={
        "dataset_id": ds_id, "plan_id": plan_id, "name": "dry",
        "recipe": {"stages": stages, "score": {"run_aesthetic": False}, **recipe},
    })
    return r.status_code, r.json()


def _errors(report: dict) -> dict[str, dict]:
    return {c["key"]: c for c in report["checks"] if c["level"] == "error"}


def _warnings(report: dict) -> dict[str, dict]:
    return {c["key"]: c for c in report["checks"] if c["level"] == "warning"}


async def _count(env, model, **where) -> int:
    async with env.Session() as db:
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return (await db.execute(stmt)).scalar()


async def _provider(env, name="lmstudio", model="qwen-vl") -> str:
    r = await env.client.post(f"{API}/providers/", json={
        "name": name, "base_url": "http://127.0.0.1:1234/v1", "default_model": model,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _llm_lists(monkeypatch, *ids: str) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": i} for i in ids]})

    monkeypatch.setattr(run_service, "_LLM_TRANSPORT", httpx.MockTransport(_handler))


def test_a_dry_run_writes_nothing_and_reports_the_fill(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch, blueprint=SPEC)
            code, report = await _dry(env, ds["id"], plan_id, ["prompts", "generate"])
            assert code == 200, report
            assert report["ok"] is True, report["checks"]
            assert report["has_blueprint"] is True
            assert [s["key"] for s in report["stages"]] == ["prompts", "generate"]
            assert report["rows_to_add"] == report["rows_planned"] > 0
            assert report["folder_count"] == len(report["folders"])
            assert sum(f["rows"] for f in report["folders"]) == report["rows_planned"]

            assert await _count(env, Run) == 0
            assert await _count(env, BackgroundJob) == 0
            assert await _count(env, ComfyRow, plan_id=plan_id) == 0
            assert fake.submitted == []
    run(go())


def test_the_dry_run_count_is_what_the_run_generates(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch, blueprint=SPEC)
            code, report = await _dry(env, ds["id"], plan_id, ["prompts", "generate"])
            assert code == 200 and report["ok"], report
            code, body = await _start(env, ds["id"], plan_id, ["prompts", "generate"])
            assert code == 200, body
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed", done
            assert done["row_count"] == report["rows_planned"]
            assert len(fake.submitted) == report["rows_planned"]
    run(go())


def test_the_dry_run_applies_the_image_cap_clamp_the_run_applies(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            gold = {**GOOD, "id": "g", "label": "Gold Knight"}
            _fake, ds, plan_id = await _loop_plan(env, monkeypatch, _spec(GOOD, gold, count=2))
            recipe = {"loop": _loop(max_images=3)}
            code, report = await _dry(env, ds["id"], plan_id, ["prompts", "generate"], **recipe)
            assert code == 200 and report["ok"], report
            assert report["rows_before_clamp"] == 4
            assert report["rows_planned"] == 3
            assert "image_cap" in _warnings(report)
            # Round-robin: neither folder is starved.
            assert sorted(f["rows"] for f in report["folders"]) == [1, 2]

            code, body = await _start(env, ds["id"], plan_id, ["prompts", "generate"], **recipe)
            assert code == 200, body
            await wait_for_run(env, body["id"], timeout=60)
            async with env.Session() as db:
                r = await db.get(Run, body["id"])
                assert len(r.context["passes"][0]) == report["rows_planned"]
    run(go())


def test_comfyui_unreachable_is_an_error_and_the_start_refuses(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch, FakeComfy(unreachable=True))
            await _add_row(env, plan_id, "a knight")
            code, report = await _dry(env, ds["id"], plan_id, ["generate"])
            assert code == 200, report
            assert report["ok"] is False
            assert "does not answer" in _errors(report)["comfyui_reachable"]["message"]
            code, body = await _start(env, ds["id"], plan_id, ["generate"])
            assert code == 400, body
            assert "does not answer" in body["detail"]
            assert await _count(env, Run) == 0
    run(go())


def test_a_missing_caption_provider_refuses_the_start_with_404(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")
            caption = {"model": "openai_compat:no-such-provider:qwen-vl"}
            code, report = await _dry(env, ds["id"], plan_id, ["generate", "caption"], caption=caption)
            assert code == 200 and report["ok"] is False, report
            assert _errors(report)["caption_provider"]["status_code"] == 404
            code, body = await _start(env, ds["id"], plan_id, ["generate", "caption"], caption=caption)
            assert code == 404, body
            assert await _count(env, Run) == 0
    run(go())


@pytest.mark.parametrize("listed, expect", [
    (("qwen-vl", "other"), "ok"),
    (("other",), "error"),
    ((), "warning"),
])
def test_the_caption_model_is_checked_against_the_providers_list(tmp_path, monkeypatch, listed, expect):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            pid = await _provider(env)
            if listed:
                _llm_lists(monkeypatch, *listed)
            caption = {"model": f"openai_compat:{pid}:qwen-vl"}
            code, report = await _dry(env, ds["id"], plan_id, ["caption"], caption=caption)
            assert code == 200, report
            if expect == "ok":
                assert report["ok"] is True and "caption_model" not in _warnings(report), report["checks"]
            elif expect == "error":
                assert "does not list the caption model qwen-vl" in _errors(report)["caption_model"]["message"]
            else:
                # The fixture refuses the connection: nobody could list, so it is a warning.
                assert report["ok"] is True, report["checks"]
                assert "Could not list" in _warnings(report)["caption_model"]["message"]
    run(go())


def test_warnings_never_block_the_start(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")
            # Versioning is off on a fresh install, so Snapshot will be skipped: a warning.
            code, report = await _dry(env, ds["id"], plan_id, ["snapshot", "generate"])
            assert code == 200, report
            assert report["ok"] is True
            assert "snapshot" in _warnings(report)
            code, body = await _start(env, ds["id"], plan_id, ["snapshot", "generate"])
            assert code == 200, body
    run(go())


def test_a_second_live_run_is_the_last_check(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch, FakeComfy(canvas_running=False, complete=True, polls_before_done=50))
            await _add_row(env, plan_id, "a knight")
            code, body = await _start(env, ds["id"], plan_id, ["generate"])
            assert code == 200, body
            code, report = await _dry(env, ds["id"], plan_id, ["generate"])
            assert code == 200, report
            assert [c["key"] for c in report["checks"] if c["level"] == "error"] == ["live_run"]
            assert _errors(report)["live_run"]["status_code"] == 409
            await env.client.post(f"{API}/runs/{body['id']}/cancel")
    run(go())


def test_an_unknown_or_foreign_plan_is_an_http_error(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            other = await env.create_dataset("other")
            code, _ = await _dry(env, ds["id"], "no-such-plan", ["generate"])
            assert code == 404
            code, _ = await _dry(env, other["id"], plan_id, ["generate"])
            assert code == 400
    run(go())


def test_round_robin_counts():
    rr = run_service.round_robin_counts
    assert rr([3, 1, 2], 4) == [2, 1, 1]
    assert rr([3, 1, 2], 10) == [3, 1, 2]
    assert rr([0, 5], 2) == [0, 2]
    assert rr([2, 2], 0) == [0, 0]
