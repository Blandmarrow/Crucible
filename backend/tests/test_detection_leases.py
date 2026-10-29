"""Detection holds its model for as long as it reads it, and never trades the
user's rows for a failure.

Two contracts, both driven through the real endpoints and the real job:

* **Leases (GPT-032).** Every detection path that loads a model leases it — the
  batch job for each of sam2/sam3/Florence-2, and the single-image manual-box and
  refine jobs for sam2 — so an unload while inference runs answers 409 instead of
  `del entry.model` under the job, and the lease is gone once the job ends.
* **Overwrite needs a result.** `overwrite=True` used to delete the model's
  existing rows for an image *before* checking whether inference succeeded, so
  any failure — such as a model unloaded mid-run, before leases — silently erased
  them. Each branch now deletes only alongside a successful inference.

No model is real. The registry is pre-populated with a fake entry, so the real
`load_*` returns it without loading anything, and each predictor's entry point is
replaced on its module (the router imports it at call time, so `setattr` reaches
it). `_device.empty_cache` is stubbed because it imports torch, which CI does not
have.
"""
import asyncio
import threading

import pytest
from sqlalchemy import select

from backend.ml import device as _device
from backend.ml import florence_captioner, nudenet_scorer, sam2_predictor, sam3_predictor
from backend.ml.model_manager import ModelEntry, model_manager
from backend.models import Image
from backend.models.detection import Detection
from backend.tests.conftest import API, api_env, run, wait_for_job

_DET = {"label": "cat", "bbox": [0.1, 0.1, 0.5, 0.5], "score": 0.9}

# model -> (registry id, task, module, attribute) for the batch job's branches.
_BRANCHES = {
    "sam2": ("sam2", "text_prompt", sam2_predictor, "predict_sync"),
    "sam3": ("sam3", "text_prompt", sam3_predictor, "predict_sync"),
    "florence2_large": ("florence2_large", "<OD>", florence_captioner, "detect_image"),
    # CPU-only ONNX: no registry entry, so nothing to lease — but the same
    # overwrite shape, so it is covered by the overwrite test.
    "nudenet": (None, "nudenet", nudenet_scorer, "detect_sync"),
}


@pytest.fixture(autouse=True)
def _no_torch(monkeypatch):
    monkeypatch.setattr(_device, "empty_cache", lambda: None)


@pytest.fixture
def registered(request):
    """Put fake entries in the real registry and guarantee they leave again."""
    added: list[str] = []

    def _add(model_id: str) -> ModelEntry:
        entry = ModelEntry({"predictor": object()}, None, vram_mb=1800)
        model_manager._registry[model_id] = entry
        added.append(model_id)
        return entry

    yield _add
    for mid in added:
        model_manager._registry.pop(mid, None)


def _stub(monkeypatch, module, attr: str, sync_fn) -> None:
    """Replace a predictor's entry point. `detect_image` is the one async one —
    it does its own `run_in_executor` — so it is wrapped to keep the blocking
    call off the loop, as the real one does."""
    if attr == "detect_image":
        async def _async(*args, **kwargs):
            return await asyncio.get_running_loop().run_in_executor(None, sync_fn)
        monkeypatch.setattr(module, attr, _async)
    else:
        monkeypatch.setattr(module, attr, lambda *a, **kw: sync_fn())


async def _seed_image(env, *, existing: str | None = None, mask: str | None = None) -> tuple[str, str]:
    """One image, optionally with a pre-existing detection from `existing`."""
    ds = await env.create_dataset("det")
    async with env.Session() as session:
        img = Image(
            dataset_id=ds["id"],
            filename="a.png",
            original_filename="a.png",
            file_path=str(env.datasets_dir / "det" / "a.png"),
        )
        session.add(img)
        await session.flush()
        if existing is not None:
            session.add(Detection(
                image_id=img.id, label="old", bbox=[0.0, 0.0, 1.0, 1.0],
                score=0.5, model=existing, task="old", mask=mask,
            ))
        await session.commit()
        return ds["id"], img.id


async def _until(event: threading.Event, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not event.is_set():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("inference never started")
        await asyncio.sleep(0.02)


class _Gate:
    """A fake inference that parks in its executor thread until released, so the
    test can probe the registry while the job is mid-inference."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.go = threading.Event()

    def __call__(self):
        self.entered.set()
        assert self.go.wait(10), "test never released the inference"
        return [dict(_DET)]


async def _assert_held_then_released(env, model_id: str, entry: ModelEntry, gate: _Gate, job_id: str) -> dict:
    try:
        await _until(gate.entered)
        assert entry.in_use, f"{model_id} was not leased during inference"
        r = await env.client.delete(f"{API}/captioning/model/{model_id}/unload")
        assert r.status_code == 409, r.text
        r = await env.client.post(f"{API}/models/unload-all")
        assert r.status_code == 200, r.text
        assert model_id not in r.json()["unloaded"]
        assert model_id in r.json()["in_use"]
        assert model_manager._registry.get(model_id) is entry
    finally:
        gate.go.set()
    job = await wait_for_job(env, job_id)
    assert job["status"] == "completed", job
    assert entry.leases == 0, "the job ended without releasing its lease"
    r = await env.client.delete(f"{API}/captioning/model/{model_id}/unload")
    assert r.status_code == 204, r.text
    assert model_id not in model_manager._registry
    return job


@pytest.mark.parametrize("model", ["sam2", "sam3", "florence2_large"])
def test_batch_job_leases_its_model_for_the_whole_run(tmp_path, monkeypatch, registered, model):
    model_id, task, module, attr = _BRANCHES[model]
    entry = registered(model_id)
    gate = _Gate()
    _stub(monkeypatch, module, attr, gate)

    async def scenario():
        async with api_env(tmp_path) as env:
            dataset_id, image_id = await _seed_image(env)
            r = await env.client.post(f"{API}/detection/run", json={
                "dataset_id": dataset_id, "image_ids": [image_id],
                "model": model, "task": task, "custom_prompt": "cat",
            })
            assert r.status_code == 200, r.text
            await _assert_held_then_released(env, model_id, entry, gate, r.json()["job_id"])

            async with env.Session() as session:
                rows = (await session.execute(
                    select(Detection).where(Detection.image_id == image_id)
                )).scalars().all()
            assert [d.model for d in rows] == [model]

    run(scenario())


@pytest.mark.parametrize("path", ["manual", "refine"])
def test_single_image_sam2_jobs_lease_sam2(tmp_path, monkeypatch, registered, path):
    entry = registered("sam2")
    gate = _Gate()
    # The manual-box job calls `predict_sync`; refine calls `refine_sync`, which
    # returns one geometry rather than a list.
    if path == "manual":
        _stub(monkeypatch, sam2_predictor, "predict_sync", gate)
    else:
        _stub(monkeypatch, sam2_predictor, "refine_sync", lambda: {**gate()[0], "mask": None})

    async def scenario():
        async with api_env(tmp_path) as env:
            mask = '{"polygons": [[[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]]}'
            _, image_id = await _seed_image(env, existing="sam2", mask=mask)
            if path == "manual":
                r = await env.client.post(f"{API}/detection/manual", json={
                    "image_id": image_id, "bbox": [0.1, 0.1, 0.5, 0.5],
                    "label": "cat", "refine_with_sam": True,
                })
            else:
                async with env.Session() as session:
                    det_id = (await session.execute(select(Detection.id))).scalar_one()
                r = await env.client.post(f"{API}/detection/{det_id}/refine", json={
                    "point_prompts": [[0.3, 0.3]], "point_labels": [1],
                })
            assert r.status_code == 200, r.text
            await _assert_held_then_released(env, "sam2", entry, gate, r.json()["job_id"])

    run(scenario())


@pytest.mark.parametrize("model", list(_BRANCHES))
@pytest.mark.parametrize("inference_fails", [True, False])
def test_overwrite_keeps_existing_rows_when_inference_fails(
    tmp_path, monkeypatch, registered, model, inference_fails
):
    model_id, task, module, attr = _BRANCHES[model]
    if model_id is not None:
        registered(model_id)

    def _infer():
        if inference_fails:
            raise RuntimeError("model unloaded mid-run")
        return [dict(_DET)]

    _stub(monkeypatch, module, attr, _infer)

    async def scenario():
        async with api_env(tmp_path) as env:
            dataset_id, image_id = await _seed_image(env, existing=model)
            r = await env.client.post(f"{API}/detection/run", json={
                "dataset_id": dataset_id, "image_ids": [image_id],
                "model": model, "task": task, "custom_prompt": "cat",
                "overwrite": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            async with env.Session() as session:
                labels = (await session.execute(
                    select(Detection.label).where(
                        Detection.image_id == image_id, Detection.model == model
                    )
                )).scalars().all()
            if inference_fails:
                assert labels == ["old"], "a failed inference erased the existing detections"
            else:
                # The gate must not break overwrite itself: a result replaces.
                assert labels == ["cat"]

    run(scenario())


def test_an_entry_evicted_before_its_lease_fails_the_job_up_front(tmp_path, monkeypatch):
    """The loader→lease race: an executor thread's LRU eviction takes the entry
    after `load_sam2` returned it. The job fails once, before the loop, and the
    image's existing rows are untouched."""
    stale = ModelEntry({"predictor": object()}, None, vram_mb=1800)
    stale.released = True

    async def fake_load_sam2(**kwargs):
        return stale

    monkeypatch.setattr(model_manager, "load_sam2", fake_load_sam2)
    calls: list[int] = []
    _stub(monkeypatch, sam2_predictor, "predict_sync", lambda: calls.append(1) or [dict(_DET)])

    async def scenario():
        async with api_env(tmp_path) as env:
            dataset_id, image_id = await _seed_image(env, existing="sam2")
            r = await env.client.post(f"{API}/detection/run", json={
                "dataset_id": dataset_id, "image_ids": [image_id],
                "model": "sam2", "task": "text_prompt", "custom_prompt": "cat",
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "failed", job
            async with env.Session() as session:
                labels = (await session.execute(select(Detection.label))).scalars().all()
            assert labels == ["old"]

    run(scenario())
    assert calls == [], "inference ran on an entry that had left the registry"
