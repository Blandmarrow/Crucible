"""Model leases: what keeps a model resident while something is using it (GPT-032).

Before leases, `unload` ran `del entry.model` under a running job, and every image
the job had left failed. These cases pin the registry contract every consumer
relies on: a leased entry cannot be unloaded (409 over HTTP), `evict_all` and LRU
eviction skip it, and an entry that left the registry cannot be leased.

Every case runs on a fresh `ModelManager` or on registry entries it removes
again, with `_device.empty_cache` stubbed — it imports torch, which CI does not
have.
"""
import pytest

from backend.ml import device as _device
from backend.ml.model_manager import ModelEntry, ModelInUseError, ModelManager, model_manager
from backend.tests.conftest import API, api_env, run


class _FakeWeights:
    def __init__(self):
        self.on_cpu = False

    def cpu(self):
        self.on_cpu = True
        return self


@pytest.fixture(autouse=True)
def _no_torch(monkeypatch):
    monkeypatch.setattr(_device, "empty_cache", lambda: None)


def _add(mm: ModelManager, model_id: str, vram_mb: int = 1000) -> ModelEntry:
    entry = ModelEntry(_FakeWeights(), None, vram_mb=vram_mb)
    mm._registry[model_id] = entry
    return entry


def test_unload_refuses_a_leased_entry_and_keeps_its_weights():
    mm = ModelManager()
    entry = _add(mm, "florence2_large")
    with mm.lease(entry):
        with pytest.raises(ModelInUseError) as exc:
            run(mm.unload("florence2_large"))
        assert exc.value.model_id == "florence2_large"
        assert "florence2_large" in mm._registry
        assert entry.model.on_cpu is False, "a refused unload must not touch the weights"
    run(mm.unload("florence2_large"))
    assert "florence2_large" not in mm._registry
    assert entry.released


def test_leases_are_counted_not_flagged():
    """The first holder's release must not clear the second holder's lease."""
    mm = ModelManager()
    entry = _add(mm, "aesthetic")
    with mm.lease(entry):
        with mm.lease(entry):
            assert entry.leases == 2
        assert entry.in_use
        with pytest.raises(ModelInUseError):
            run(mm.unload("aesthetic"))
    assert entry.leases == 0 and not entry.in_use


def test_a_lease_is_released_when_its_block_raises():
    mm = ModelManager()
    entry = _add(mm, "dino")
    with pytest.raises(ValueError):
        with mm.lease(entry):
            raise ValueError("inference failed")
    assert not entry.in_use
    run(mm.unload("dino"))


def test_evict_all_skips_leased_entries_and_returns_only_what_it_unloaded():
    mm = ModelManager()
    held = _add(mm, "sam2")
    idle_weights = _add(mm, "nsfw").model
    with mm.lease(held):
        unloaded = run(mm.evict_all())
        assert unloaded == ["nsfw"]
        assert mm.leased_ids() == ["sam2"]
        assert idle_weights.on_cpu and not held.model.on_cpu
        assert "sam2" in mm._registry and "nsfw" not in mm._registry
    assert run(mm.evict_all()) == ["sam2"]
    assert mm.leased_ids() == []


def test_lru_eviction_never_picks_a_leased_entry():
    mm = ModelManager(max_vram_mb=2000)
    held = _add(mm, "aesthetic", vram_mb=1500)
    with mm.lease(held):
        mm._evict_lru(1000)
    assert "aesthetic" in mm._registry
    mm._evict_lru(1000)
    assert "aesthetic" not in mm._registry
    assert held.released


def test_leasing_an_entry_that_already_left_the_registry_fails_up_front():
    """The executor-thread race: a loader returns an entry, an LRU eviction in
    another thread takes it, and only then does the caller lease it."""
    mm = ModelManager(max_vram_mb=1000)
    entry = _add(mm, "lama", vram_mb=900)
    mm._evict_lru(500)
    with pytest.raises(RuntimeError, match="unloaded before"):
        with mm.lease(entry):
            pass
    assert entry.leases == 0


def test_unload_of_a_leased_model_is_a_409_over_http(tmp_path):
    entry = ModelEntry(_FakeWeights(), None, vram_mb=1000)
    model_manager._registry["florence2_large"] = entry
    try:
        async def scenario():
            async with api_env(tmp_path) as env:
                with model_manager.lease(entry):
                    r = await env.client.delete(f"{API}/captioning/model/florence2_large/unload")
                    assert r.status_code == 409, r.text
                    assert "in use" in r.json()["detail"]

                    r = await env.client.post(f"{API}/models/unload-all")
                    assert r.status_code == 200, r.text
                    assert "florence2_large" not in r.json()["unloaded"]
                    assert r.json()["in_use"] == ["florence2_large"]
                assert "florence2_large" in model_manager._registry

                r = await env.client.delete(f"{API}/captioning/model/florence2_large/unload")
                assert r.status_code == 204, r.text
                assert "florence2_large" not in model_manager._registry

        run(scenario())
    finally:
        model_manager._registry.pop("florence2_large", None)
