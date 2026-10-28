"""GPT-032: unloading a caption model while a caption job is using it.

`DELETE /captioning/model/{id}/unload` used to `del entry.model` under a running
job, so every image the job had left failed. The caption jobs now hold a
`model_manager.lease` on the entry from the moment `load_*` returns, and the
unload answers 409 instead (`ModelInUseError`, mapped in `main.py`).

Torch-free on purpose — CI has none. The registry holds a real `ModelEntry`
around fake weights, so `load_florence2` returns it without loading anything,
and `backend.ml.florence_captioner.caption_image` (which the router imports
*inside* its per-image loop, so a module-attribute patch reaches it) is replaced
by a fake that reads `entry.model` on an executor thread, the way the real
`infer_sync` does. `_device.empty_cache` imports torch, so it is stubbed.
"""
import asyncio
import threading

import pytest
from sqlalchemy import select

from backend.ml import device as _device
from backend.ml import florence_captioner
from backend.ml.model_manager import ModelEntry, model_manager
from backend.models import Image
from backend.tests.conftest import API, api_env, run, upload_image, wait_for_job

MODEL_ID = "florence2_large"


class FakeWeights:
    """Stands in for a model on the GPU: `.cpu()` is what an unload calls."""

    def __init__(self) -> None:
        self.dev = "cuda"

    def cpu(self) -> "FakeWeights":
        self.dev = "cpu"
        return self


class BlockingCaptioner:
    """Fake `florence_captioner.caption_image` that parks image 1 mid-inference.

    The sync half runs on an executor thread and reads `entry.model` before and
    after the block, failing exactly as the real path did when the weights were
    released under it (moved to the CPU, or deleted off the entry)."""

    def __init__(self, block_first: bool = True) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.block_first = block_first
        # (entry, leases, entry-is-registered) as seen by each inference call.
        self.seen: list[tuple[ModelEntry, int, bool]] = []

    def _infer(self, entry: ModelEntry) -> str:
        weights = entry.model
        self.calls += 1
        self.seen.append((entry, entry.leases, model_manager._registry.get(MODEL_ID) is entry))
        if self.block_first and self.calls == 1:
            self.entered.set()
            assert self.release.wait(10), "test never released the blocked image"
        if weights.dev != "cuda" or entry.model is not weights:
            raise RuntimeError(f"weights were released mid-run (now on {weights.dev})")
        return "a cat"

    async def __call__(self, image_path, model_entry, style="detailed", target_w=None, target_h=None):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._infer, model_entry)


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(_device, "empty_cache", lambda: None)
    cap = BlockingCaptioner()
    monkeypatch.setattr(florence_captioner, "caption_image", cap)
    try:
        yield cap
    finally:
        cap.release.set()
        model_manager._registry.pop(MODEL_ID, None)


async def _wait_entered(cap: BlockingCaptioner) -> None:
    for _ in range(200):
        if cap.entered.is_set():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the caption job never reached inference")


async def _captions(env, dataset_id: str) -> list[str]:
    async with env.Session() as s:
        rows = await s.execute(select(Image.caption_text).where(Image.dataset_id == dataset_id))
        return [r[0] for r in rows]


def test_unload_during_a_caption_run_is_refused_and_the_run_completes(tmp_path, fakes):
    cap = fakes
    entry = ModelEntry(FakeWeights(), None, 100)
    model_manager._registry[MODEL_ID] = entry

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            for n in ("a.png", "b.png", "c.png"):
                await upload_image(env, ds["id"], n)
            r = await env.client.post(
                f"{API}/captioning/run", json={"dataset_id": ds["id"], "model": MODEL_ID}
            )
            assert r.status_code == 200, r.text
            jid = r.json()["job_id"]
            try:
                await _wait_entered(cap)
                assert entry.leases == 1

                u = await env.client.delete(f"{API}/captioning/model/{MODEL_ID}/unload")
                assert u.status_code == 409, u.text
                assert MODEL_ID in u.json()["detail"]

                ua = await env.client.post(f"{API}/models/unload-all")
                assert ua.status_code == 200, ua.text
                assert MODEL_ID in ua.json()["in_use"]
                assert MODEL_ID not in ua.json()["unloaded"]

                assert model_manager._registry.get(MODEL_ID) is entry
                assert entry.model.dev == "cuda"
            finally:
                cap.release.set()
            job = await wait_for_job(env, jid)
            assert job["status"] == "completed", job
            assert (job["result_data"] or {}).get("failed_count", 0) == 0, job["result_data"]
            assert cap.calls == 3
            assert await _captions(env, ds["id"]) == ["a cat"] * 3

            # The lease ended with the job, so the same unload now goes through.
            assert entry.leases == 0
            u = await env.client.delete(f"{API}/captioning/model/{MODEL_ID}/unload")
            assert u.status_code == 204, u.text
            assert MODEL_ID not in model_manager._registry
            assert entry.released

    run(scenario())


def test_a_cancelled_caption_run_releases_its_lease(tmp_path, fakes):
    """The lease lives on an ExitStack around the whole job body, so a run that
    leaves by an exception (here the cooperative cancel) still lets go."""
    cap = fakes
    entry = ModelEntry(FakeWeights(), None, 100)
    model_manager._registry[MODEL_ID] = entry

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            for n in ("a.png", "b.png", "c.png"):
                await upload_image(env, ds["id"], n)
            r = await env.client.post(
                f"{API}/captioning/run", json={"dataset_id": ds["id"], "model": MODEL_ID}
            )
            jid = r.json()["job_id"]
            try:
                await _wait_entered(cap)
                assert entry.leases == 1
                c = await env.client.delete(f"{API}/jobs/{jid}")
                assert c.status_code == 204, c.text
            finally:
                cap.release.set()
            job = await wait_for_job(env, jid)
            assert job["status"] == "cancelled", job
            assert entry.leases == 0
            u = await env.client.delete(f"{API}/captioning/model/{MODEL_ID}/unload")
            assert u.status_code == 204, u.text
            assert MODEL_ID not in model_manager._registry

    run(scenario())


def _pipeline_loader(monkeypatch) -> list[ModelEntry]:
    """Route `load_florence2`'s real miss path to a fake loader, so a step after
    an `evict_all` reloads (a fresh entry) instead of needing torch."""
    loaded: list[ModelEntry] = []

    def fake_load(model_id, variant, job_id, loop, dataset_id):
        entry = ModelEntry(FakeWeights(), None, 100)
        loaded.append(entry)
        return entry

    monkeypatch.setattr(model_manager, "_load_florence2_sync", fake_load)
    return loaded


def test_pipeline_leases_each_step_and_still_evicts_between_steps(tmp_path, fakes, monkeypatch):
    cap = fakes
    cap.block_first = False
    loaded = _pipeline_loader(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            for n in ("a.png", "b.png"):
                await upload_image(env, ds["id"], n)
            r = await env.client.post(
                f"{API}/captioning/pipeline",
                json={"dataset_id": ds["id"], "steps": [{"model": MODEL_ID}, {"model": MODEL_ID}]},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert (job["result_data"] or {}).get("failed_count", 0) == 0, job["result_data"]

    run(scenario())

    # Two loads, two entries: step 1's evict_all really unloaded its model (a
    # lease left held would have made it skip, and step 2 would reuse it).
    assert len(loaded) == 2 and loaded[0] is not loaded[1]
    assert cap.calls == 4
    # Every inference ran under exactly one lease on a still-registered entry.
    assert [(e is loaded[0], n, reg) for e, n, reg in cap.seen[:2]] == [(True, 1, True)] * 2
    assert [(e is loaded[1], n, reg) for e, n, reg in cap.seen[2:]] == [(True, 1, True)] * 2
    # Step 2's evict_all freed its model too, and nothing is left leased.
    assert MODEL_ID not in model_manager._registry
    assert all(e.released and e.leases == 0 for e in loaded)


def test_a_pipeline_step_that_raises_releases_its_lease(tmp_path, fakes, monkeypatch):
    """A cancel mid-step leaves through the exception path, skipping the step's
    own close and evict_all; the job-level stack must still release it."""
    cap = fakes
    loaded = _pipeline_loader(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            for n in ("a.png", "b.png"):
                await upload_image(env, ds["id"], n)
            r = await env.client.post(
                f"{API}/captioning/pipeline",
                json={"dataset_id": ds["id"], "steps": [{"model": MODEL_ID}, {"model": MODEL_ID}]},
            )
            jid = r.json()["job_id"]
            try:
                await _wait_entered(cap)
                assert loaded[0].leases == 1
                u = await env.client.delete(f"{API}/captioning/model/{MODEL_ID}/unload")
                assert u.status_code == 409, u.text
                c = await env.client.delete(f"{API}/jobs/{jid}")
                assert c.status_code == 204, c.text
            finally:
                cap.release.set()
            job = await wait_for_job(env, jid)
            assert job["status"] == "cancelled", job
            assert len(loaded) == 1 and loaded[0].leases == 0
            u = await env.client.delete(f"{API}/captioning/model/{MODEL_ID}/unload")
            assert u.status_code == 204, u.text
            assert MODEL_ID not in model_manager._registry

    run(scenario())
