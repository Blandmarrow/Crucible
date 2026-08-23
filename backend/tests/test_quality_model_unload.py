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

from backend.ml.model_manager import model_manager
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
