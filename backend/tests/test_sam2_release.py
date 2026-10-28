"""Unloading sam2 frees both of its networks: SAM2's and Grounding DINO's (GPT-034).

Two leaks, one cause — weights the unload path could not reach:

* Grounding DINO lived in a module-global `_gdino_cache` in `sam2_predictor`, so
  `unload("sam2")` and "Unload all" reported sam2 freed while Grounding DINO
  stayed on the accelerator until the process exited.
* The entry held the `SAM2ImagePredictor`, which upstream is not an `nn.Module`
  (its network is `predictor.model`), so `_release_to_cpu`'s `predictor.cpu()`
  raised `AttributeError` into a swallowing `except` and SAM2's own weights never
  moved either.

Both now sit in the sam2 `ModelEntry.model` dict as tenants, so the stock
`_release_to_cpu` walk reaches them with no unload hook. Asserted at the entry
shape, through the real loader and the real unload paths, never at the id — see
`docs/dev/ml-models.md` § Releasing an entry's weights for why an id-level check
cannot see this class of bug.

Torch-free: `transformers` and `sam2.sam2_image_predictor` are stubbed in
`sys.modules`, and the `_device` calls that import torch are stubbed out.
"""
import sys
import types

import pytest

from backend.ml import device as _device
from backend.ml import sam2_predictor
from backend.ml.model_manager import ModelManager
from backend.tests.conftest import run


class _FakeCudaModel:
    """Stands in for a torch module: records whether it was moved to the CPU."""

    def __init__(self) -> None:
        self.dev = "cuda"

    def to(self, dev):
        self.dev = dev
        return self

    def eval(self):
        return self

    def cpu(self):
        self.dev = "cpu"
        return self


class _FakePredictor:
    """Mirrors upstream `SAM2ImagePredictor`: **no** `.cpu()` of its own, the
    network at `.model`."""

    def __init__(self) -> None:
        self.model = _FakeCudaModel()

    @classmethod
    def from_pretrained(cls, repo, device=None):
        return cls()


@pytest.fixture
def stubs(monkeypatch):
    loads = {"gdino": 0}

    transformers = types.ModuleType("transformers")

    class AutoProcessor:
        @staticmethod
        def from_pretrained(repo):
            return object()   # a processor has no .cpu(); the walk must tolerate it

    class GroundingDinoForObjectDetection:
        @staticmethod
        def from_pretrained(repo):
            loads["gdino"] += 1
            return _FakeCudaModel()

    transformers.AutoProcessor = AutoProcessor
    transformers.GroundingDinoForObjectDetection = GroundingDinoForObjectDetection
    monkeypatch.setitem(sys.modules, "transformers", transformers)

    sam2_pkg = types.ModuleType("sam2")
    sam2_pkg.__path__ = []
    sip = types.ModuleType("sam2.sam2_image_predictor")
    sip.SAM2ImagePredictor = _FakePredictor
    sam2_pkg.sam2_image_predictor = sip
    monkeypatch.setitem(sys.modules, "sam2", sam2_pkg)
    monkeypatch.setitem(sys.modules, "sam2.sam2_image_predictor", sip)

    monkeypatch.setattr(_device, "get_device", lambda: "cuda")
    monkeypatch.setattr(_device, "is_gpu_available", lambda: True)
    monkeypatch.setattr(_device, "memory_allocated_bytes", lambda: 0)
    monkeypatch.setattr(_device, "empty_cache", lambda: None)
    return loads


def test_no_module_global_cache_remains():
    """The structural half: nothing outside the entry can hold Grounding DINO."""
    assert not hasattr(sam2_predictor, "_gdino_cache")


def test_loader_exposes_the_sam2_network_as_a_tenant_and_budgets_grounding_dino(stubs):
    entry = sam2_predictor._load_sam2_sync()
    assert isinstance(entry.model["predictor"], _FakePredictor)
    assert entry.model["sam2_model"] is entry.model["predictor"].model
    # The floor matches `list_models`' forecast, which includes Grounding DINO.
    forecast = {m["id"]: m["vram_mb"] for m in ModelManager().list_models()}["sam2"]
    assert entry.vram_mb == forecast == 1800


def test_ensure_gdino_loads_into_the_entry_once(stubs):
    entry = sam2_predictor._load_sam2_sync()
    d = sam2_predictor._ensure_gdino(entry.model)
    assert d is entry.model
    assert d["gdino_model"].dev == "cuda"
    assert "gdino_processor" in d and d["gdino_device"] == "cuda"
    sam2_predictor._ensure_gdino(entry.model)
    assert stubs["gdino"] == 1, "a second call reloaded Grounding DINO"


@pytest.mark.parametrize("how", ["unload", "evict_all"])
def test_unloading_sam2_moves_both_networks_off_the_gpu(stubs, how):
    mm = ModelManager()
    entry = run(mm.load_sam2())
    sam2_predictor._ensure_gdino(entry.model)
    sam2_net = entry.model["sam2_model"]
    gdino = entry.model["gdino_model"]
    assert sam2_net.dev == gdino.dev == "cuda"

    if how == "unload":
        run(mm.unload("sam2"))
    else:
        assert run(mm.evict_all()) == ["sam2"]

    assert not mm._registry
    assert sam2_net.dev == "cpu", "SAM2's own network was left on the GPU"
    assert gdino.dev == "cpu", "Grounding DINO was left on the GPU"


def test_a_reloaded_sam2_reloads_grounding_dino_too(stubs):
    """With the old global, a reload after unload reused the never-freed cache;
    now the next text prompt after a reload loads into the new entry."""
    mm = ModelManager()
    first = run(mm.load_sam2())
    sam2_predictor._ensure_gdino(first.model)
    run(mm.unload("sam2"))

    second = run(mm.load_sam2())
    assert "gdino_model" not in second.model
    sam2_predictor._ensure_gdino(second.model)
    assert stubs["gdino"] == 2
    assert second.model["gdino_model"].dev == "cuda"
    run(mm.unload("sam2"))


def test_text_prompt_mode_hands_the_whole_entry_dict_to_predict_text(monkeypatch):
    """`_predict_text` needs the dict, not the predictor: Grounding DINO is
    loaded *into* it, and passing the predictor would put it back out of the
    unload path's reach (or fail on the first text prompt)."""
    from PIL import Image as PilImage

    seen = {}

    def fake_predict_text(img_np, w, h, model_dict, prompt, thr):
        seen["model_dict"] = model_dict
        return []

    monkeypatch.setattr(sam2_predictor, "_predict_text", fake_predict_text)
    monkeypatch.setattr(sam2_predictor, "open_rgb", lambda p: PilImage.new("RGB", (8, 8)))
    entry_dict = {"predictor": _FakePredictor(), "device": "cuda"}
    assert sam2_predictor.predict_sync("x.png", entry_dict, "text_prompt", "cat") == []
    assert seen["model_dict"] is entry_dict
