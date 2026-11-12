"""Caption jobs send a remote captioner several requests at once (M10 T2).

What is pinned (docs/dev/captioning.md § Requests in flight):
- `bound` requests are in flight at most, and exactly `bound` when there is work: the
  provider's `max_concurrent`, or `ollama_max_concurrent` for an `ollama:` model, and 1
  by default;
- whatever order the requests finish in, each caption lands on its own image and the
  progress events count 1..N in order;
- a user Stop sends no new request, saves and counts the ones in flight, and ends the
  job `cancelled`;
- a save that fails the job raises `done_items` to the number of requests started;
- rename-on-caption picks the same names at any bound;
- one client per job (and per pipeline step), closed once, also after a cancel;
- an HTTP 429 is tallied and named in the headline.

Torch-free: the provider is faked, either at `caption_image` or one level below it, at
the SDK client (`make_client`), so the real request code runs.
"""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
from sqlalchemy import select

from backend.models import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


# ── Helpers ──────────────────────────────────────────────────────────────────

def _idx(image_path) -> int:
    return int(re.search(r"(\d+)", Path(image_path).stem).group(1))


class Fake:
    """A fake `caption_image`. Records what is in flight so a test can see how many
    requests overlapped, and finishes them in an order `delay_for` decides."""

    def __init__(self, delay_for=lambda i: 0.01, gate: asyncio.Event | None = None,
                 fail_with=None, text=lambda name, prompt: f"cap of {name}"):
        self.delay_for = delay_for
        self.gate = gate
        self.fail_with = fail_with or {}
        self.text = text
        self.in_flight = 0
        self.max_in_flight = 0
        self.started: list[str] = []
        self.prompts: dict[str, str] = {}
        self.three_started = asyncio.Event()

    async def __call__(self, image_path, **kwargs):
        name = Path(image_path).name
        i = _idx(image_path)
        self.started.append(name)
        self.prompts[name] = kwargs.get("custom_prompt", "")
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        if len(self.started) >= 3:
            self.three_started.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
            await asyncio.sleep(self.delay_for(i))
            if i in self.fail_with:
                raise self.fail_with[i]
            return self.text(name, kwargs.get("custom_prompt", ""))
        finally:
            self.in_flight -= 1


def _install(monkeypatch, fake) -> Fake:
    import backend.ml.openai_compat_captioner as mod

    monkeypatch.setattr(mod, "caption_image", fake)
    return fake


def _reverse_delay(n: int):
    """The first image is the slowest, so later ones finish first."""
    return lambda i: 0.01 + 0.02 * (n - i)


async def _provider(env, *, max_concurrent: int | None = None, name: str = "lmstudio") -> str:
    body = {"name": name, "base_url": "http://127.0.0.1:1234/v1", "default_model": "vl"}
    if max_concurrent is not None:
        body["max_concurrent"] = max_concurrent
    r = await env.client.post(f"{API}/providers/", json=body)
    assert r.status_code == 201, r.text
    return f"openai_compat:{r.json()['id']}:vl"


async def _dataset(env, n: int, name: str = "d", subfolder: str = "") -> dict:
    ds = await env.create_dataset(name)
    for i in range(n):
        await upload_image(env, ds["id"], f"i{i}.png", png_bytes(color=(i * 25, 10, 10)), subfolder=subfolder)
    return ds


async def _start(env, ds_id: str, model: str, **body) -> str:
    r = await env.client.post(f"{API}/captioning/run", json={"dataset_id": ds_id, "model": model, **body})
    assert r.status_code == 200, r.text
    assert r.json()["job_id"], r.text
    return r.json()["job_id"]


async def _captions(env, ds_id: str) -> dict[str, str]:
    """caption text -> current filename, so a rename does not lose the pairing."""
    async with env.Session() as db:
        rows = (await db.execute(select(Image.filename, Image.caption_text).where(Image.dataset_id == ds_id))).all()
    return {text: fn for fn, text in rows}


def _capture_progress(monkeypatch) -> list[dict]:
    from backend.workers.progress import broadcaster

    events: list[dict] = []
    real = broadcaster.emit

    async def capturing(job_id, event):
        events.append({**event, "job_id": job_id})
        await real(job_id, event)

    monkeypatch.setattr(broadcaster, "emit", capturing)
    return events


# ── /run at bound 1 and 3 ────────────────────────────────────────────────────

def test_bound_one_never_overlaps_and_saves_each_caption_on_its_image(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 4)
            model = await _provider(env)          # max_concurrent defaults to 1
            fake = _install(monkeypatch, Fake(_reverse_delay(4)))
            job = await wait_for_job(env, await _start(env, ds["id"], model))
            assert (job["status"], job["done_items"]) == ("completed", 4), job
            assert fake.max_in_flight == 1
            assert len(set(fake.started)) == 4
            assert await _captions(env, ds["id"]) == {f"cap of i{i}.png": f"i{i}.png" for i in range(4)}
    run(go())


def test_bound_three_overlaps_exactly_three_and_keeps_order(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 8)
            model = await _provider(env, max_concurrent=3)
            events = _capture_progress(monkeypatch)
            fake = _install(monkeypatch, Fake(_reverse_delay(8)))
            job_id = await _start(env, ds["id"], model)
            job = await wait_for_job(env, job_id)
            assert (job["status"], job["done_items"], job["total_items"]) == ("completed", 8, 8), job
            assert fake.max_in_flight == 3
            assert await _captions(env, ds["id"]) == {f"cap of i{i}.png": f"i{i}.png" for i in range(8)}
            done = [e["done"] for e in events if e.get("type") == "progress" and e["job_id"] == job_id and "image_id" in e]
            assert done == list(range(1, 9)), done
    run(go())


def test_stop_at_bound_three_saves_what_was_in_flight_and_sends_nothing_new(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 8)
            model = await _provider(env, max_concurrent=3)
            gate = asyncio.Event()
            fake = _install(monkeypatch, Fake(gate=gate))
            job_id = await _start(env, ds["id"], model)
            await asyncio.wait_for(fake.three_started.wait(), 20)
            await asyncio.sleep(0.1)              # a fourth would have started by now
            assert len(fake.started) == 3
            assert (await env.client.delete(f"{API}/jobs/{job_id}")).status_code == 204
            gate.set()
            job = await wait_for_job(env, job_id)
            assert (job["status"], job["done_items"]) == ("cancelled", 3), job
            assert len(fake.started) == 3
            saved = await _captions(env, ds["id"])
            assert len([t for t in saved if t]) == 3
            assert set(saved) - {""} == {f"cap of {n}" for n in fake.started}
    run(go())


def test_a_failed_save_at_bound_three_counts_every_request_started(tmp_path, monkeypatch):
    from backend.services import caption_service

    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 8)
            model = await _provider(env, max_concurrent=3)
            fake = _install(monkeypatch, Fake(_reverse_delay(8)))
            real = caption_service.set_caption
            saves = []

            async def _set_caption(*args, **kwargs):
                saves.append(args[1])
                if len(saves) == 2:
                    raise RuntimeError("the caption could not be saved")
                return await real(*args, **kwargs)

            monkeypatch.setattr(caption_service, "set_caption", _set_caption)
            job = await wait_for_job(env, await _start(env, ds["id"], model))
            assert job["status"] == "failed" and "could not be saved" in job["error_msg"], job
            assert len(fake.started) >= 2
            assert job["done_items"] == len(fake.started), (job["done_items"], fake.started)
            assert fake.in_flight == 0              # nothing outlived the job
    run(go())


def test_rename_on_caption_picks_the_same_names_at_any_bound(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            serial = await _dataset(env, 6, name="serial", subfolder="cats")
            wide = await _dataset(env, 6, name="wide", subfolder="cats")
            m1 = await _provider(env, max_concurrent=1, name="one")
            m3 = await _provider(env, max_concurrent=3, name="three")

            fake = _install(monkeypatch, Fake(_reverse_delay(6)))
            job = await wait_for_job(env, await _start(env, serial["id"], m1, rename_on_caption=True))
            assert job["status"] == "completed", job
            assert fake.max_in_flight == 1
            a = await _captions(env, serial["id"])

            fake = _install(monkeypatch, Fake(_reverse_delay(6)))
            job = await wait_for_job(env, await _start(env, wide["id"], m3, rename_on_caption=True))
            assert job["status"] == "completed", job
            assert fake.max_in_flight == 3
            b = await _captions(env, wide["id"])

            assert len(a) == 6 and all(not re.fullmatch(r"i\d\.png", n) for n in a.values()), a
            assert a == b
    run(go())


# ── One client per job ───────────────────────────────────────────────────────

class _SdkClient:
    """Stands in for `AsyncOpenAI`: the real `caption_image` runs against it."""

    def __init__(self, gate: asyncio.Event | None = None):
        self.gate = gate
        self.closed = 0
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        prompt = kwargs["messages"][0]["content"][1]["text"]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=f"sdk reply to [{prompt[:20]}]"), finish_reason="stop")])

    async def close(self):
        self.closed += 1


def _record_clients(monkeypatch, gate: asyncio.Event | None = None) -> list[_SdkClient]:
    import backend.ml.openai_compat_captioner as mod

    built: list[_SdkClient] = []

    def make_client(base_url, api_key, timeout_s):
        c = _SdkClient(gate)
        c.args = (base_url, api_key, timeout_s)
        built.append(c)
        return c

    monkeypatch.setattr(mod, "make_client", make_client)
    return built


def test_a_job_builds_one_client_and_closes_it_once(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 5)
            model = await _provider(env, max_concurrent=3)
            built = _record_clients(monkeypatch)
            job = await wait_for_job(env, await _start(env, ds["id"], model))
            assert (job["status"], job["done_items"]) == ("completed", 5), job
            assert len(built) == 1
            assert built[0].closed == 1
            assert len(built[0].calls) == 5
            assert built[0].args == ("http://127.0.0.1:1234/v1", "", 300)
            first = built[0].calls[0]
            assert first["model"] == "vl"
            assert first["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    run(go())


def test_a_cancelled_job_still_closes_its_one_client(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 6)
            model = await _provider(env, max_concurrent=2)
            gate = asyncio.Event()
            built = _record_clients(monkeypatch, gate)
            job_id = await _start(env, ds["id"], model)
            for _ in range(200):
                if built and len(built[0].calls) == 2:
                    break
                await asyncio.sleep(0.05)
            assert built and len(built[0].calls) == 2
            assert (await env.client.delete(f"{API}/jobs/{job_id}")).status_code == 204
            gate.set()
            job = await wait_for_job(env, job_id)
            assert (job["status"], job["done_items"]) == ("cancelled", 2), job
            assert len(built) == 1 and built[0].closed == 1
            assert len(built[0].calls) == 2
    run(go())


# ── /pipeline ────────────────────────────────────────────────────────────────

def test_a_pipeline_builds_and_closes_one_client_per_step(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 4)
            model = await _provider(env, max_concurrent=2)
            built = _record_clients(monkeypatch)
            r = await env.client.post(f"{API}/captioning/pipeline", json={
                "dataset_id": ds["id"], "steps": [{"model": model}, {"model": model}]})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert len(built) == 2
            assert [c.closed for c in built] == [1, 1]
            assert [len(c.calls) for c in built] == [4, 4]
    run(go())


def test_pipeline_steps_overlap_and_the_second_sees_the_first_steps_caption(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 6)
            model = await _provider(env, max_concurrent=3)
            phase_max = {1: 0, 2: 0}
            live = {1: 0, 2: 0}
            seen_prompts: dict[str, str] = {}

            async def fake(image_path, **kwargs):
                name = Path(image_path).name
                prompt = kwargs.get("custom_prompt", "")
                step = 2 if prompt else 1
                live[step] += 1
                phase_max[step] = max(phase_max[step], live[step])
                try:
                    await asyncio.sleep(0.01 + 0.02 * (6 - _idx(image_path)))
                finally:
                    live[step] -= 1
                if step == 2:
                    seen_prompts[name] = prompt
                    return f"second [{name}]"
                return f"first [{name}]"

            _install(monkeypatch, fake)
            r = await env.client.post(f"{API}/captioning/pipeline", json={
                "dataset_id": ds["id"],
                "steps": [{"model": model}, {"model": model, "custom_prompt": "refine: {previous_caption}"}]})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert phase_max == {1: 3, 2: 3}
            assert seen_prompts == {f"i{i}.png": f"refine: first [i{i}.png]" for i in range(6)}
            assert set(await _captions(env, ds["id"])) == {f"second [i{i}.png]" for i in range(6)}
    run(go())


# ── 429 ──────────────────────────────────────────────────────────────────────

class _RateLimited(Exception):
    status_code = 429


def test_a_429_is_tallied_and_named_in_the_headline(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 5)
            model = await _provider(env, max_concurrent=3, name="cloudy")
            _install(monkeypatch, Fake(fail_with={1: _RateLimited("slow down"), 3: _RateLimited("slow down")}))
            job = await wait_for_job(env, await _start(env, ds["id"], model))
            assert job["status"] == "completed", job
            data = job["result_data"]
            assert data["rate_limited"] == 2, data
            assert data["failed_count"] == 2, data
            assert "HTTP 429" in data["failure_summary"]
            assert "provider 'cloudy'" in data["failure_summary"]
            assert "Lower Max concurrent requests" in data["failure_summary"]
            assert len([t for t in await _captions(env, ds["id"]) if t]) == 3
    run(go())


# ── Ollama ───────────────────────────────────────────────────────────────────

def _ollama_transport(monkeypatch):
    from backend.ml import ollama_captioner

    state = {"in_flight": 0, "max": 0, "n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["in_flight"] += 1
        state["n"] += 1
        state["max"] = max(state["max"], state["in_flight"])
        try:
            await asyncio.sleep(0.05)
        finally:
            state["in_flight"] -= 1
        return httpx.Response(200, json={"response": "caption"})

    monkeypatch.setattr(ollama_captioner, "_HTTP_TRANSPORT", httpx.MockTransport(handler))
    return state


def test_ollama_requests_follow_the_app_wide_setting(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 6)
            state = _ollama_transport(monkeypatch)

            # The default is one at a time.
            job = await wait_for_job(env, await _start(env, ds["id"], "ollama:llava"))
            assert (job["status"], job["done_items"]) == ("completed", 6), job
            assert (state["n"], state["max"]) == (6, 1)

            r = await env.client.patch(f"{API}/settings/thresholds", json={"ollama_max_concurrent": 3})
            assert r.status_code == 200, r.text
            state.update(n=0, max=0)
            job = await wait_for_job(env, await _start(env, ds["id"], "ollama:llava", overwrite=True))
            assert (job["status"], job["done_items"]) == ("completed", 6), job
            assert (state["n"], state["max"]) == (6, 3)
            assert set(await _captions(env, ds["id"])) == {"caption"}
    run(go())


def test_an_ollama_timeout_under_concurrency_says_to_lower_the_setting(tmp_path, monkeypatch):
    """`ollama_captioner` turns a timeout into "", so the empty reason carries the hint."""
    from backend.ml import ollama_captioner

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("queued too long", request=request)

    async def go():
        async with api_env(tmp_path) as env:
            ds = await _dataset(env, 2)
            monkeypatch.setattr(ollama_captioner, "_HTTP_TRANSPORT", httpx.MockTransport(handler))

            job = await wait_for_job(env, await _start(env, ds["id"], "ollama:llava"))
            errors = [f["error"] for f in job["result_data"]["failed"]]
            assert errors == ["the Ollama request returned no caption text"] * 2

            r = await env.client.patch(f"{API}/settings/thresholds", json={"ollama_max_concurrent": 2})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, await _start(env, ds["id"], "ollama:llava"))
            errors = [f["error"] for f in job["result_data"]["failed"]]
            assert len(errors) == 2
            assert all("lower it in Settings" in e for e in errors), errors
    run(go())


def test_clamp_bound_tolerates_odd_stored_values():
    """`_clamp_bound` tolerates a missing or odd stored value rather than failing a job."""
    from backend.routers.captioning import _clamp_bound

    assert [_clamp_bound(v) for v in (None, 0, -4, "x", 1, 5, 16, 99)] == [1, 1, 1, 1, 1, 5, 16, 16]
