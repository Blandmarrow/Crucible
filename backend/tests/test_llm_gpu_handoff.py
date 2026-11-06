"""M6 §9: a local Strata server drops its model before ComfyUI generates.

Strata holds the whole card on a 16 GB GPU, so with Settings → ComfyUI's GPU handoff on,
Crucible sends it `POST /v1/unload` wherever ComfyUI is about to need the card: a run's
`handoff_to_comfy`, a run's `free_*_model` stages, and the start of every `comfy_generate`
job (a run's, or one started by hand). The setting is off by default, and then nothing
changes. Everything is best effort: a server that refuses is a note, never a failure.
"""
import asyncio

import httpx

from backend.services import llm_server, run_service
from backend.tests.conftest import API, api_env, run
from backend.tests.test_comfy_run_body import _add_row, _run
from backend.tests.test_run_stepping import STUB_CAPTION, _plan, _start, wait_for_run


class FakeStrata:
    """Strata's `/health` and `/v1/unload` at one address; LM Studio's API nowhere."""

    def __init__(self, host: str = "127.0.0.1:8080", loaded: bool = True):
        self.host = host
        self.loaded = loaded
        self.calls: list[tuple[str, str, str]] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append((request.method, request.url.netloc.decode(), request.url.path))
            if request.url.netloc.decode() != self.host:
                return httpx.Response(404)
            if request.url.path == "/health":
                return httpx.Response(200, json={"service": "strata", "loaded": self.loaded})
            if request.url.path == "/v1/unload" and request.method == "POST":
                if request.headers.get("content-type") != "application/json":
                    return httpx.Response(415)
                assert request.url.params.get("wait") == "1"
                self.loaded = False
                return httpx.Response(200, json={"status": "unloaded", "loaded": False})
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    def unloads(self) -> int:
        return sum(1 for m, h, p in self.calls if p == "/v1/unload")


async def _provider(env, name: str, base_url: str) -> str:
    r = await env.client.post(f"{API}/providers/", json={
        "name": name, "base_url": base_url, "default_model": "local-model",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _handoff(env, on: bool) -> None:
    r = await env.client.patch(f"{API}/settings/thresholds", json={"llm_gpu_handoff": on})
    assert r.status_code == 200, r.text
    assert r.json()["llm_gpu_handoff"] is on


def test_the_setting_is_off_by_default_and_then_nothing_is_unloaded(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/settings/thresholds")
            assert r.json()["llm_gpu_handoff"] is False
            strata = FakeStrata()
            monkeypatch.setattr(run_service, "_LLM_TRANSPORT", strata.transport())
            await _provider(env, "Strata", "http://127.0.0.1:8080/v1")

            assert await llm_server.free_local_llm_servers() == []
            assert strata.calls == []  # not even a /health
    run(go())


def test_only_a_loaded_local_strata_is_unloaded_and_once_per_server(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            await _handoff(env, True)
            strata = FakeStrata()
            monkeypatch.setattr(run_service, "_LLM_TRANSPORT", strata.transport())
            # Two provider rows on one server (say "Strata" and "llama.cpp"), an
            # LM Studio (its /health is a 404 here) and a remote API.
            await _provider(env, "Strata", "http://127.0.0.1:8080/v1")
            await _provider(env, "llama.cpp", "http://127.0.0.1:8080/v1/")
            await _provider(env, "LM Studio", "http://localhost:1234/v1")
            await _provider(env, "Remote", "https://api.example.com/v1")

            notes = await llm_server.free_local_llm_servers()
            assert notes == ["Unloaded Strata's model from the GPU"]
            assert strata.unloads() == 1
            assert not any(h == "api.example.com" for m, h, p in strata.calls)

            # Already unloaded: a /health per server, and nothing else.
            assert await llm_server.free_local_llm_servers() == []
            assert strata.unloads() == 1
    run(go())


def test_a_server_that_fails_to_unload_is_a_note(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"service": "strata", "loaded": True})
        return httpx.Response(503)

    monkeypatch.setattr(run_service, "_LLM_TRANSPORT", httpx.MockTransport(handler))
    note = asyncio.run(llm_server.unload_strata("http://127.0.0.1:8080/v1", label="Strata"))
    assert note == "Strata did not unload its model (HTTPStatusError); continuing"


def test_a_run_unloads_strata_at_the_handoff_and_after_captioning(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            await _handoff(env, True)
            fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")
            strata = FakeStrata()
            monkeypatch.setattr(run_service, "_LLM_TRANSPORT", strata.transport())
            provider_id = await _provider(env, "Strata", "http://127.0.0.1:8080/v1")
            import backend.ml.openai_compat_captioner as mod

            async def _caption(image_path, **kwargs):
                strata.loaded = True  # the first request reloads Strata by itself
                return STUB_CAPTION

            monkeypatch.setattr(mod, "caption_image", _caption)

            code, body = await _start(
                env, ds["id"], plan_id,
                ["handoff_to_comfy", "generate", "caption", "free_caption_model"],
                caption={"model": f"openai_compat:{provider_id}:local-model"},
            )
            assert code == 200, body
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed", done
            by_key = {s["key"]: s for s in done["stages"]}
            assert "Unloaded Strata's model from the GPU" in by_key["handoff_to_comfy"]["summary"]
            assert by_key["free_caption_model"]["summary"] == "Unloaded Strata's model from the GPU"
            # The generate job found Strata already unloaded: no second unload before it.
            assert strata.unloads() == 2
    run(go())


def test_with_the_setting_off_a_run_leaves_strata_loaded_and_says_so(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")
            strata = FakeStrata()
            monkeypatch.setattr(run_service, "_LLM_TRANSPORT", strata.transport())
            provider_id = await _provider(env, "Strata", "http://127.0.0.1:8080/v1")
            import backend.ml.openai_compat_captioner as mod

            async def _caption(image_path, **kwargs):
                return STUB_CAPTION

            monkeypatch.setattr(mod, "caption_image", _caption)

            code, body = await _start(
                env, ds["id"], plan_id, ["generate", "caption", "free_caption_model"],
                caption={"model": f"openai_compat:{provider_id}:local-model"},
            )
            assert code == 200, body
            done = await wait_for_run(env, body["id"])
            assert done["status"] == "completed", done
            # Off means exactly the pre-§9 behaviour: LM Studio's API, Strata's 404 for it.
            assert "no unload API" in _summary(done, "free_caption_model")
            assert not any(p == "/health" for m, h, p in strata.calls)
            assert strata.unloads() == 0
            assert strata.loaded is True
    run(go())


def test_a_generation_started_by_hand_unloads_strata_first(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            await _handoff(env, True)
            fake, ds, plan_id = await _plan(env, monkeypatch)
            await _add_row(env, plan_id, "a knight")
            strata = FakeStrata()
            monkeypatch.setattr(run_service, "_LLM_TRANSPORT", strata.transport())
            await _provider(env, "Strata", "http://127.0.0.1:8080/v1")

            job = await _run(env, plan_id)
            assert job["status"] == "completed", job
            assert strata.unloads() == 1
            assert strata.loaded is False
    run(go())


def _summary(done: dict, key: str) -> str:
    return next(s["summary"] for s in done["stages"] if s["key"] == key) or ""
