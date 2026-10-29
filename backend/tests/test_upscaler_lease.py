"""The upscaler leases its model per image (GPT-032).

`upscale_image_sync` runs in executor threads — from the `upscale` job and,
concurrently, from the two crop+upscale HTTP routes — and manipulates the
registry directly under `upscale:{path}`. These cases pin that the entry comes
back from `_ensure_upscaler_loaded` already leased (taken inside the same
`_sync_lock` block as the lookup or the insert), that the lease is held while the
model runs, and that it is released afterwards, on an exception too.

Torch-free: `torch` and `spandrel` are stubbed in `sys.modules` with just enough
surface for the code under test, and the `_device` helpers that import torch are
replaced. The stub tensor wraps a numpy array; the fake model is the identity.
"""
import contextlib
import sys
import types

import numpy as np
import pytest
from PIL import Image as PilImage

from backend.ml import device as _device
from backend.ml import upscaler
from backend.ml.model_manager import ModelEntry, ModelInUseError, model_manager
from backend.tests.conftest import run

MODEL_PATH = "/models/4x-test.pth"
MODEL_ID = f"upscale:{MODEL_PATH}"


class _T:
    """The handful of tensor methods `upscale_image_sync` calls, over numpy."""

    def __init__(self, arr):
        self.arr = arr

    @property
    def shape(self):
        return self.arr.shape

    def permute(self, *dims):
        return _T(self.arr.transpose(dims))

    def unsqueeze(self, dim):
        return _T(np.expand_dims(self.arr, dim))

    def squeeze(self, dim):
        return _T(np.squeeze(self.arr, dim))

    def to(self, _device):
        return self

    def clamp(self, lo, hi):
        return _T(self.arr.clip(lo, hi))

    def cpu(self):
        return self

    def numpy(self):
        return self.arr


@pytest.fixture(autouse=True)
def _torch_free(monkeypatch):
    torch = types.ModuleType("torch")
    torch.no_grad = contextlib.nullcontext
    torch.from_numpy = _T
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setattr(_device, "get_device", lambda: "cpu")
    monkeypatch.setattr(_device, "is_gpu_available", lambda: False)
    monkeypatch.setattr(_device, "empty_cache", lambda: None)
    yield
    model_manager._registry.pop(MODEL_ID, None)


class _Model:
    """Identity upscaler that records, at call time, whether its entry is leased
    and whether an unload would be refused."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.entry: ModelEntry | None = None
        self.seen: list[tuple[int, bool]] = []

    def __call__(self, tensor):
        refused = False
        try:
            run(model_manager.unload(MODEL_ID))
        except ModelInUseError:
            refused = True
        self.seen.append((self.entry.leases, refused))
        if self.fail:
            raise RuntimeError("CUDA out of memory")
        return tensor

    def eval(self):
        return self

    def cpu(self):
        return self


def _register(model) -> ModelEntry:
    entry = ModelEntry(model, 1, vram_mb=3000)
    model.entry = entry
    model_manager._registry[MODEL_ID] = entry
    return entry


def _src(tmp_path):
    src = tmp_path / "a.png"
    PilImage.new("RGB", (8, 6), (10, 120, 200)).save(src)
    return str(src)


def test_a_cached_entry_comes_back_leased():
    entry = _register(_Model())
    got = upscaler._ensure_upscaler_loaded(MODEL_ID, MODEL_PATH, "cpu")
    assert got is entry
    assert entry.leases == 1
    with pytest.raises(ModelInUseError):
        run(model_manager.unload(MODEL_ID))
    model_manager._release_lease(entry)
    assert entry.leases == 0


def test_a_fresh_load_is_inserted_leased(monkeypatch):
    model = _Model()
    spandrel = types.ModuleType("spandrel")

    class ModelLoader:
        def load_from_file(self, path):
            assert path == MODEL_PATH
            return types.SimpleNamespace(model=model, scale=4)

    spandrel.ModelLoader = ModelLoader
    monkeypatch.setitem(sys.modules, "spandrel", spandrel)
    monkeypatch.setattr(upscaler, "_extra_arches_installed", True)

    entry = upscaler._ensure_upscaler_loaded(MODEL_ID, MODEL_PATH, "cpu")
    assert model_manager._registry[MODEL_ID] is entry
    assert entry.processor == 4
    assert entry.leases == 1
    model_manager._release_lease(entry)
    assert entry.leases == 0


def test_the_lease_is_held_during_inference_and_released_after(tmp_path):
    model = _Model()
    entry = _register(model)
    info = upscaler.upscale_image_sync(
        _src(tmp_path), str(tmp_path / "out.png"), MODEL_PATH, False, None, None
    )
    assert (info["width"], info["height"]) == (8, 6)
    assert model.seen == [(1, True)], "the model ran without a lease"
    assert entry.leases == 0
    assert MODEL_ID in model_manager._registry


def test_the_lease_is_released_when_inference_raises(tmp_path):
    model = _Model(fail=True)
    entry = _register(model)
    with pytest.raises(RuntimeError, match="out of memory"):
        upscaler.upscale_image_sync(
            _src(tmp_path), str(tmp_path / "out.png"), MODEL_PATH, False, None, None
        )
    assert model.seen == [(1, True)]
    assert entry.leases == 0, "a failed image left the model pinned"
    # Nothing holds it now, so the unload goes through.
    run(model_manager.unload(MODEL_ID))
    assert MODEL_ID not in model_manager._registry
