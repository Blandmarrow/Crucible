import asyncio, sys, types
from backend.ml import sam2_predictor
from backend.ml.model_manager import ModelManager, ModelEntry


class FakeCudaModel:
    def __init__(self): self.dev = "cuda"
    def to(self, d): return self
    def eval(self): return self
    def cpu(self): self.dev = "cpu"; return self


def test_034_unload_all_leaves_gdino(monkeypatch):
    fake = types.ModuleType("transformers")
    class AP:  # AutoProcessor
        @staticmethod
        def from_pretrained(repo): return object()
    class G:
        @staticmethod
        def from_pretrained(repo): return FakeCudaModel()
    fake.AutoProcessor = AP; fake.GroundingDinoForObjectDetection = G
    monkeypatch.setitem(sys.modules, "transformers", fake)
    monkeypatch.setattr(sam2_predictor, "_gdino_cache", None)
    c = sam2_predictor._ensure_gdino()
    assert c["model"].dev == "cuda"
    mm = ModelManager()
    mm._registry["sam2"] = ModelEntry({"predictor": FakeCudaModel(), "device": "cuda"}, None, 900)
    evicted = asyncio.run(mm.evict_all())
    print("evicted:", evicted, "registry:", list(mm._registry), "gdino still cached:", sam2_predictor._gdino_cache is not None,
          "gdino dev:", sam2_predictor._gdino_cache["model"].dev)
    assert evicted == ["sam2"] and not mm._registry
    assert sam2_predictor._gdino_cache is not None and sam2_predictor._gdino_cache["model"].dev == "cuda"
