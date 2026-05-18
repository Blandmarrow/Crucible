import asyncio
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class ModelEntry:
    def __init__(self, model: Any, processor: Any, vram_mb: int) -> None:
        self.model = model
        self.processor = processor
        self.vram_mb = vram_mb
        self.last_used = time.time()
        self.in_use = False


class ModelManager:
    def __init__(self, max_vram_mb: int = 20000) -> None:
        self._registry: dict[str, ModelEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        # threading.Lock protects _registry mutations inside executor threads,
        # where asyncio.Lock is not usable (multiple models can load in parallel threads)
        self._sync_lock = threading.Lock()
        self.max_vram_mb = max_vram_mb

    def _get_lock(self, model_id: str) -> asyncio.Lock:
        if model_id not in self._locks:
            self._locks[model_id] = asyncio.Lock()
        return self._locks[model_id]

    def _used_vram(self) -> int:
        return sum(e.vram_mb for e in self._registry.values())

    def _evict_lru(self, needed_mb: int) -> None:
        import torch
        with self._sync_lock:
            candidates = [
                (mid, entry) for mid, entry in self._registry.items()
                if not entry.in_use
            ]
            candidates.sort(key=lambda x: x[1].last_used)
            # keep evicting until we have enough headroom or run out of candidates
            while self._used_vram() + needed_mb > self.max_vram_mb and candidates:
                mid, entry = candidates.pop(0)
                logger.info("Evicting model %s from VRAM", mid)
                try:
                    entry.model.cpu()
                except Exception:
                    pass
                del entry.model
                del entry.processor
                del self._registry[mid]
                self._locks.pop(mid, None)
                torch.cuda.empty_cache()

    async def get(self, model_id: str) -> ModelEntry:
        if model_id in self._registry:
            entry = self._registry[model_id]
            entry.last_used = time.time()
            return entry
        raise KeyError(f"Model {model_id} not registered or loaded")

    async def load_florence2(self, variant: str = "large") -> ModelEntry:
        model_id = f"florence2_{variant}"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry

            loop = asyncio.get_event_loop()
            entry = await loop.run_in_executor(None, self._load_florence2_sync, model_id, variant)
            self._registry[model_id] = entry
            return entry

    def _load_florence2_sync(self, model_id: str, variant: str) -> ModelEntry:
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        MODEL_MAP = {
            "large": "microsoft/Florence-2-large",
            "promptgen": "MiaoshouAI/Florence-2-large-PromptGen-v2.0",
        }
        model_name = MODEL_MAP.get(variant, MODEL_MAP["large"])
        logger.info("Loading %s...", model_name)

        self._evict_lru(5500)

        vram_before = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
        processor = None
        model = None
        try:
            processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
            model = AutoModelForCausalLM.from_pretrained(
                model_name,
                torch_dtype=torch.bfloat16,
                trust_remote_code=True,
            )
            model = model.to("cuda")
            model.eval()
        except Exception:
            if model is not None:
                try:
                    model.cpu()
                except Exception:
                    pass
            del model, processor
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise

        vram_used = max(5500, int((torch.cuda.memory_allocated() - vram_before) / 1024 / 1024))
        return ModelEntry(model, processor, vram_mb=vram_used)

    async def load_paligemma2(self) -> ModelEntry:
        model_id = "paligemma2"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry

            loop = asyncio.get_event_loop()
            entry = await loop.run_in_executor(None, self._load_paligemma2_sync)
            self._registry[model_id] = entry
            return entry

    def _load_paligemma2_sync(self) -> ModelEntry:
        import torch
        from transformers import AutoProcessor, PaliGemmaForConditionalGeneration
        from backend.config import settings

        model_name = "google/paligemma2-3b-pt-448"
        logger.info("Loading %s...", model_name)
        self._evict_lru(6000)

        vram_before = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
        tok_kwargs = {"token": settings.hf_token} if settings.hf_token else {}
        kwargs: dict = {"torch_dtype": torch.bfloat16, "device_map": "cuda", **tok_kwargs}

        processor = None
        model = None
        try:
            processor = AutoProcessor.from_pretrained(model_name, **tok_kwargs)
            model = PaliGemmaForConditionalGeneration.from_pretrained(model_name, **kwargs)
            model.eval()
        except Exception:
            if model is not None:
                try:
                    model.cpu()
                except Exception:
                    pass
            del model, processor
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise

        vram_used = max(6000, int((torch.cuda.memory_allocated() - vram_before) / 1024 / 1024))
        return ModelEntry(model, processor, vram_mb=vram_used)

    async def load_aesthetic(self) -> ModelEntry:
        model_id = "aesthetic"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            loop = asyncio.get_event_loop()
            entry = await loop.run_in_executor(None, self._load_aesthetic_sync)
            self._registry[model_id] = entry
            return entry

    def _load_aesthetic_sync(self) -> ModelEntry:
        import torch
        import open_clip
        from backend.config import settings
        from backend.ml.aesthetic_scorer import AestheticMLP, download_weights

        logger.info("Loading aesthetic predictor...")
        self._evict_lru(3500)

        vram_before = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
        clip_model = None
        mlp = None
        try:
            clip_model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-L-14", pretrained="openai"
            )
            clip_model = clip_model.to("cuda").eval()

            weights_path = settings.models_cache_dir / "aesthetic_predictor_v2_5.pth"
            if not weights_path.exists():
                download_weights(weights_path)

            mlp = AestheticMLP(768)
            mlp.load_state_dict(torch.load(weights_path, map_location="cpu"))
            mlp = mlp.to("cuda").eval()
        except Exception:
            for m in (clip_model, mlp):
                if m is not None:
                    try:
                        m.cpu()
                    except Exception:
                        pass
            del clip_model, mlp
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise

        vram_used = max(3500, int((torch.cuda.memory_allocated() - vram_before) / 1024 / 1024))
        return ModelEntry({"clip": clip_model, "mlp": mlp, "preprocess": preprocess}, None, vram_mb=vram_used)

    async def load_dino(self) -> ModelEntry:
        model_id = "dino"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            loop = asyncio.get_event_loop()
            entry = await loop.run_in_executor(None, self._load_dino_sync)
            self._registry[model_id] = entry
            return entry

    def _load_dino_sync(self) -> ModelEntry:
        import torch
        from transformers import AutoModel, AutoImageProcessor

        model_name = "facebook/dinov2-base"
        logger.info("Loading %s...", model_name)
        self._evict_lru(1200)

        vram_before = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
        processor = None
        model = None
        try:
            processor = AutoImageProcessor.from_pretrained(model_name)
            model = AutoModel.from_pretrained(model_name, torch_dtype=torch.float16)
            model = model.to("cuda").eval()
        except Exception:
            if model is not None:
                try:
                    model.cpu()
                except Exception:
                    pass
            del model, processor
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise

        vram_used = max(1200, int((torch.cuda.memory_allocated() - vram_before) / 1024 / 1024))
        return ModelEntry(model, processor, vram_mb=vram_used)

    async def unload(self, model_id: str) -> None:
        import torch
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry.pop(model_id)
                try:
                    entry.model.cpu()
                except Exception:
                    pass
                del entry.model
                torch.cuda.empty_cache()

    def list_models(self) -> list[dict]:
        loaded = set(self._registry.keys())
        all_models = [
            {"id": "florence2_large", "name": "Florence-2-large", "vram_mb": 5500},
            {"id": "florence2_promptgen", "name": "Florence-2 PromptGen v2", "vram_mb": 5500},
            {"id": "paligemma2", "name": "PaliGemma-2 3B", "vram_mb": 6000},
            {"id": "aesthetic", "name": "LAION Aesthetic Predictor", "vram_mb": 3500},
            {"id": "dino", "name": "DINOv2-base", "vram_mb": 1200},
        ]
        return [{**m, "loaded": m["id"] in loaded} for m in all_models]


model_manager = ModelManager()
