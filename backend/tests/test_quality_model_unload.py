"""The scoring-model lifecycle: GET /quality/models, POST /quality/models/unload,
and the auto-unload the scoring job runs in its `finally`.

Everything here asserts on the **registry**, never on a real model: the whole
point is that a scoring run frees VRAM, and no CI runner has any. `model_manager`
is a module-level singleton, so `monkeypatch.setattr(model_manager, …)` reaches
the copy `routers/quality.py` imported.

The auto-unload case drives the real endpoint and the real job. It gets there by
stubbing `backend.ml.aesthetic_scorer` in `sys.modules` — `_run` imports that
module unconditionally at its top, and it imports torch at *its* top, so without
the stub the job fails on the import line long before any of this is reached.
"""
import sys
import types

import pytest

from backend.ml import device as _device
from backend.ml.model_manager import ModelEntry, model_manager
from backend.models import Image
from backend.routers.quality import SCORING_MODEL_IDS
from backend.services.threshold_service import DEFAULTS
from backend.tests.conftest import API, api_env, run, wait_for_job


def test_scoring_ids_all_exist_in_the_registry():
    """The structural guard: a renamed registry id fails here rather than
    silently unloading nothing for the rest of the app's life."""
    known = {m["id"] for m in model_manager.list_models()}
    assert set(SCORING_MODEL_IDS) <= known, set(SCORING_MODEL_IDS) - known


def test_tag_embedder_is_not_a_scoring_model():
    """Why `SCORING_MODEL_IDS` is a literal and not a `kind` filter: `dino` and
    `tag_embedder` share `kind == "embed"`, and scoring never loads the latter."""
    by_id = {m["id"]: m for m in model_manager.list_models()}
    assert by_id["tag_embedder"]["kind"] == by_id["dino"]["kind"] == "embed"
    assert "tag_embedder" not in SCORING_MODEL_IDS


def test_list_models_returns_exactly_the_scoring_four(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/quality/models")
            assert r.status_code == 200, r.text
            body = r.json()
            assert [m["id"] for m in body] == list(SCORING_MODEL_IDS)
            for m in body:
                assert "loaded" in m
                assert isinstance(m["vram_mb"], int)

    run(scenario())


def test_unload_frees_only_scoring_models_and_reports_the_saving(tmp_path, monkeypatch):
    """Two of the four resident plus a captioning model that must survive."""
    resident = {"aesthetic", "dino", "florence2_large"}
    real_list = model_manager.list_models()
    monkeypatch.setattr(
        model_manager,
        "list_models",
        lambda: [{**m, "loaded": m["id"] in resident} for m in real_list],
    )
    calls: list[str] = []

    async def fake_unload(model_id: str) -> None:
        calls.append(model_id)

    monkeypatch.setattr(model_manager, "unload", fake_unload)

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{API}/quality/models/unload")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["unloaded"] == ["aesthetic", "dino"]
            # 3500 + 1200, from the registry's own vram_mb figures.
            assert body["freed_mb"] == 4700

    run(scenario())
    # Every scoring id is asked (unload is a no-op for an unregistered one), and
    # the captioning model is never touched — this is not `evict_all()`.
    assert calls == list(SCORING_MODEL_IDS)
    assert "florence2_large" not in calls


def test_unload_with_nothing_resident_reports_nothing(tmp_path, monkeypatch):
    real_list = model_manager.list_models()
    monkeypatch.setattr(
        model_manager, "list_models", lambda: [{**m, "loaded": False} for m in real_list]
    )

    async def fake_unload(model_id: str) -> None:
        return None

    monkeypatch.setattr(model_manager, "unload", fake_unload)

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{API}/quality/models/unload")
            assert r.status_code == 200, r.text
            assert r.json() == {"unloaded": [], "freed_mb": 0}

    run(scenario())


# --- The setting ------------------------------------------------------------


def test_default_is_on_in_DEFAULTS():
    """`get_thresholds` builds a *transient* row when none exists, where an unset
    attribute reads None rather than the column's server_default — so the value
    has to be in DEFAULTS, not only on the model."""
    assert DEFAULTS["auto_unload_after_scoring"] is True


def test_setting_round_trips(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/settings/thresholds")
            assert r.status_code == 200, r.text
            assert r.json()["auto_unload_after_scoring"] is True

            r = await env.client.patch(
                f"{API}/settings/thresholds", json={"auto_unload_after_scoring": False}
            )
            assert r.status_code == 200, r.text
            assert r.json()["auto_unload_after_scoring"] is False

            r = await env.client.get(f"{API}/settings/thresholds")
            assert r.json()["auto_unload_after_scoring"] is False

            # `exclude_none` on the PATCH body means False must still land — a
            # `exclude_unset`-style bug here would make the toggle one-way.
            r = await env.client.patch(
                f"{API}/settings/thresholds", json={"auto_unload_after_scoring": True}
            )
            assert r.json()["auto_unload_after_scoring"] is True

    run(scenario())


# --- Getting the weights off the GPU ----------------------------------------
#
# The tests above assert that `unload` is *called* with the right ids; these
# assert that the call frees something. That gap is where the original bug lived:
# `unload` did a bare `entry.model.cpu()`, which raises `AttributeError` on the
# two dict-shaped entries (`aesthetic`, `nsfw`) straight into a swallowing
# `except`, so the weights never left the GPU and every id-level assertion still
# passed.


class _FakeWeights:
    """Stands in for a torch module: the only thing under test is whether the
    unload path reaches it."""

    def __init__(self) -> None:
        self.on_cpu = False

    def cpu(self):
        self.on_cpu = True
        return self


def _register(monkeypatch, model_id: str, model) -> ModelEntry:
    """Put an entry in the real registry and guarantee it leaves again.

    `_device.empty_cache` is stubbed for the same reason the rest of this module
    stubs the scorers: it imports torch, which CI does not have."""
    monkeypatch.setattr(_device, "empty_cache", lambda: None)
    entry = ModelEntry(model, None, vram_mb=3500)
    model_manager._registry[model_id] = entry
    return entry


def test_unload_moves_every_tenant_of_a_dict_entry_off_the_gpu(monkeypatch):
    """`aesthetic`'s `{"clip", "mlp", "preprocess"}` and `nsfw`'s
    `{"model", "processor", "nsfw_idx"}` are dicts, not modules.

    `preprocess` sits between the two sets of weights on purpose: it is a
    transform with no `.cpu()`, so a loop that let one tenant's failure abort it
    would move `clip` and leave `mlp` resident."""
    clip, mlp = _FakeWeights(), _FakeWeights()
    _register(monkeypatch, "aesthetic", {"clip": clip, "preprocess": object(), "mlp": mlp})
    try:
        run(model_manager.unload("aesthetic"))
    finally:
        model_manager._registry.pop("aesthetic", None)

    assert clip.on_cpu, "CLIP backbone was left on the GPU"
    assert mlp.on_cpu, "a tenant with no .cpu() stopped the loop"
    assert "aesthetic" not in model_manager._registry


def test_unload_still_moves_a_plain_entry_off_the_gpu(monkeypatch):
    """The other entry shape — `dino`, the captioners — must not regress."""
    model = _FakeWeights()
    _register(monkeypatch, "dino", model)
    try:
        run(model_manager.unload("dino"))
    finally:
        model_manager._registry.pop("dino", None)

    assert model.on_cpu
    assert "dino" not in model_manager._registry


# --- Auto-unload at the end of a run ----------------------------------------


class _FakeEntry:
    """What `load_aesthetic` returns: `.model` is the three-tenant dict, and the
    job only ever passes it straight back to a scorer."""
    model = {"clip": None, "mlp": None, "preprocess": None}
    processor = None
    vram_mb = 3500


def _install_scorer_stub(monkeypatch, scores: list[float]) -> None:
    """Replace `backend.ml.aesthetic_scorer` for the duration of one test.

    `monkeypatch.setitem` restores whatever was there, including the real module
    on a developer machine that has torch."""
    stub = types.ModuleType("backend.ml.aesthetic_scorer")

    async def score_images_batch(paths, handle, job_id=None, model=None):
        return list(scores)[: len(paths)]

    async def score_images_watermark(*a, **kw):  # pragma: no cover - unticked here
        return []

    async def extract_clip_embeddings_batch(*a, **kw):  # pragma: no cover - unticked
        return []

    stub.score_images_batch = score_images_batch
    stub.score_images_watermark = score_images_watermark
    stub.extract_clip_embeddings_batch = extract_clip_embeddings_batch
    monkeypatch.setitem(sys.modules, "backend.ml.aesthetic_scorer", stub)


def _record_loads_and_unloads(monkeypatch) -> list[str]:
    unloaded: list[str] = []

    async def fake_load_aesthetic(job_id=None, loop=None, dataset_id=None):
        return _FakeEntry()

    async def fake_unload(model_id: str) -> None:
        unloaded.append(model_id)

    monkeypatch.setattr(model_manager, "load_aesthetic", fake_load_aesthetic)
    monkeypatch.setattr(model_manager, "unload", fake_unload)
    return unloaded


async def _seed_one_image(env) -> str:
    ds = await env.create_dataset("scored")
    async with env.Session() as session:
        img = Image(
            dataset_id=ds["id"],
            filename="a.png",
            original_filename="a.png",
            file_path=str(env.datasets_dir / "scored" / "a.png"),
        )
        session.add(img)
        await session.commit()
    return ds["id"]


@pytest.mark.parametrize("auto_unload", [True, False])
def test_job_unloads_what_it_loaded_iff_the_setting_is_on(tmp_path, monkeypatch, auto_unload):
    _install_scorer_stub(monkeypatch, [6.25])
    unloaded = _record_loads_and_unloads(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            dataset_id = await _seed_one_image(env)
            r = await env.client.patch(
                f"{API}/settings/thresholds",
                json={"auto_unload_after_scoring": auto_unload},
            )
            assert r.status_code == 200, r.text

            r = await env.client.post(
                f"{API}/quality/score",
                json={
                    "dataset_id": dataset_id,
                    "run_aesthetic": True,
                    "run_technical": False,
                },
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            # The run itself still did its work either way.
            async with env.Session() as session:
                img = (await session.execute(
                    Image.__table__.select().where(Image.dataset_id == dataset_id)
                )).first()
                assert img.aesthetic_score == 6.25

    run(scenario())

    # Only what this run loaded: the aesthetic model, and nothing else in
    # SCORING_MODEL_IDS — the four are not swept blindly.
    assert unloaded == (["aesthetic"] if auto_unload else [])


def test_a_failing_scorer_still_frees_vram(tmp_path, monkeypatch):
    """The `finally`, not a tail statement: a scorer that raises must not strand
    3.5 GB, and the job is still allowed to fail."""
    _install_scorer_stub(monkeypatch, [])
    unloaded = _record_loads_and_unloads(monkeypatch)

    async def boom(paths, handle, job_id=None, model=None):
        raise RuntimeError("CUDA out of memory")

    sys.modules["backend.ml.aesthetic_scorer"].score_images_batch = boom

    async def scenario():
        async with api_env(tmp_path) as env:
            dataset_id = await _seed_one_image(env)
            r = await env.client.post(
                f"{API}/quality/score",
                json={
                    "dataset_id": dataset_id,
                    "run_aesthetic": True,
                    "run_technical": False,
                },
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "failed", job

    run(scenario())
    assert unloaded == ["aesthetic"]


def test_the_job_leaves_no_weights_on_the_gpu(tmp_path, monkeypatch):
    """The whole feature, end to end, with the **real** `unload`.

    Every other job-level test here stubs `unload` and asserts on the id it was
    handed, which is why the shipped bug survived them: the ids were right and
    nothing was freed. This one registers a dict-shaped entry the way
    `load_aesthetic` does and asserts the tenants came back off the GPU.

    It covers the `_release_to_cpu` half of the fix. The other half — `_run`
    clearing its own `handle`/`entry` locals before awaiting the unload, so the
    allocator flush has nothing live to skip — is not observable once the job's
    frame is gone, and is belt-and-braces anyway: weights already moved to the CPU
    are off the GPU whoever still holds a reference to them.
    """
    _install_scorer_stub(monkeypatch, [6.25])
    monkeypatch.setattr(_device, "empty_cache", lambda: None)

    clip, mlp = _FakeWeights(), _FakeWeights()
    tenants = {"clip": clip, "preprocess": object(), "mlp": mlp}

    async def fake_load_aesthetic(job_id=None, loop=None, dataset_id=None):
        entry = ModelEntry(tenants, None, vram_mb=3500)
        model_manager._registry["aesthetic"] = entry
        return entry

    monkeypatch.setattr(model_manager, "load_aesthetic", fake_load_aesthetic)

    async def scenario():
        async with api_env(tmp_path) as env:
            dataset_id = await _seed_one_image(env)
            r = await env.client.post(
                f"{API}/quality/score",
                json={
                    "dataset_id": dataset_id,
                    "run_aesthetic": True,
                    "run_technical": False,
                },
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

    try:
        run(scenario())
    finally:
        model_manager._registry.pop("aesthetic", None)

    assert clip.on_cpu, "the run finished with the CLIP backbone still in VRAM"
    assert mlp.on_cpu, "the run finished with the aesthetic MLP still in VRAM"
    assert "aesthetic" not in model_manager._registry
