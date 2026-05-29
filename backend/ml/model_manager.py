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

    async def load_florence2(
        self,
        variant: str = "large",
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = f"florence2_{variant}"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry

            _loop = loop or asyncio.get_event_loop()
            entry = await _loop.run_in_executor(
                None, self._load_florence2_sync, model_id, variant, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    def _load_florence2_sync(
        self,
        model_id: str,
        variant: str,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        from backend.ml.download_progress import emit_sync, is_hf_cached, progress_tqdm_patch

        MODEL_MAP = {
            "large": "microsoft/Florence-2-large",
            "promptgen": "MiaoshouAI/Florence-2-large-PromptGen-v2.0",
        }
        model_name = MODEL_MAP.get(variant, MODEL_MAP["large"])
        logger.info("Loading %s...", model_name)

        if job_id and loop:
            needs_download = not is_hf_cached(model_name, "config.json")
            msg = (
                f"Downloading {model_name} (first run, may take several minutes)..."
                if needs_download else f"Loading {model_name} into VRAM..."
            )
            emit_sync(job_id, loop, msg, -1.0, dataset_id)

        self._evict_lru(5500)

        vram_before = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
        load_kwargs: dict = {"torch_dtype": torch.bfloat16, "trust_remote_code": True}

        processor = None
        model = None
        try:
            with progress_tqdm_patch(job_id, loop, f"Downloading {model_name}...", dataset_id):
                processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
                if variant == "promptgen":
                    # PromptGen v2's DaViT vision encoder doesn't implement _initialize_weights.
                    # Newer transformers always calls _initialize_missing_keys after loading,
                    # which hits this missing method. Patch it out for the duration of the load.
                    import transformers.modeling_utils as _mu
                    _orig = _mu.PreTrainedModel._initialize_missing_keys
                    def _safe_init_missing(self, *a, **kw):
                        try:
                            _orig(self, *a, **kw)
                        except AttributeError:
                            pass
                    _mu.PreTrainedModel._initialize_missing_keys = _safe_init_missing
                    try:
                        model = AutoModelForCausalLM.from_pretrained(model_name, **load_kwargs)
                    finally:
                        _mu.PreTrainedModel._initialize_missing_keys = _orig
                    # transformers >= 4.50: PreTrainedModel no longer inherits GenerationMixin.
                    # The PromptGen v2 model code doesn't explicitly inherit it either, so
                    # .generate() is missing on the language_model sub-component. Inject it.
                    # Also ensure generation_config is not None, which GenerationMixin.generate()
                    # dereferences unconditionally via _prepare_generation_config.
                    from transformers import GenerationMixin, GenerationConfig
                    lang_cls = type(model.language_model)
                    if not issubclass(lang_cls, GenerationMixin):
                        lang_cls.__bases__ = lang_cls.__bases__ + (GenerationMixin,)
                    if getattr(model.language_model, "generation_config", None) is None:
                        model.language_model.generation_config = GenerationConfig.from_model_config(
                            model.language_model.config
                        )
                else:
                    model = AutoModelForCausalLM.from_pretrained(model_name, **load_kwargs)
            if job_id and loop:
                emit_sync(job_id, loop, f"Loading {model_name} into VRAM...", -1.0, dataset_id)
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

    async def load_paligemma2(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = "paligemma2"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry

            _loop = loop or asyncio.get_event_loop()
            entry = await _loop.run_in_executor(
                None, self._load_paligemma2_sync, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    def _load_paligemma2_sync(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        import torch
        from transformers import AutoProcessor, PaliGemmaForConditionalGeneration
        from backend.config import settings
        from backend.ml.download_progress import emit_sync, is_hf_cached, progress_tqdm_patch

        model_name = "google/paligemma2-3b-pt-448"
        logger.info("Loading %s...", model_name)

        if job_id and loop:
            needs_download = not is_hf_cached(model_name, "config.json")
            msg = (
                f"Downloading {model_name} (first run, may take several minutes)..."
                if needs_download else f"Loading {model_name} into VRAM..."
            )
            emit_sync(job_id, loop, msg, -1.0, dataset_id)

        self._evict_lru(6000)

        vram_before = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
        tok_kwargs = {"token": settings.hf_token} if settings.hf_token else {}
        kwargs: dict = {"torch_dtype": torch.bfloat16, "device_map": "cuda", **tok_kwargs}

        processor = None
        model = None
        try:
            with progress_tqdm_patch(job_id, loop, f"Downloading {model_name}...", dataset_id):
                processor = AutoProcessor.from_pretrained(model_name, **tok_kwargs)
                model = PaliGemmaForConditionalGeneration.from_pretrained(model_name, **kwargs)
            if job_id and loop:
                emit_sync(job_id, loop, f"Loading {model_name} into VRAM...", -1.0, dataset_id)
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

    async def load_aesthetic(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = "aesthetic"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            _loop = loop or asyncio.get_event_loop()
            entry = await _loop.run_in_executor(
                None, self._load_aesthetic_sync, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    def _load_aesthetic_sync(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        import torch
        import open_clip
        from backend.config import settings
        from backend.ml.aesthetic_scorer import AestheticMLP, download_weights

        logger.info("Loading aesthetic predictor...")

        weights_path = settings.models_cache_dir / "aesthetic_predictor_v2_5.pth"

        if job_id and loop:
            from backend.ml.download_progress import emit_sync, is_hf_cached
            # open_clip CLIP backbone (~800 MB from OpenAI CDN) downloads separately;
            # check aesthetic weights as proxy for overall first-run status.
            weights_cached = weights_path.exists()
            clip_cached = is_hf_cached("openai/clip-vit-large-patch14", "config.json")
            if not weights_cached or not clip_cached:
                emit_sync(job_id, loop, "Downloading aesthetic predictor model (first run)...", -1.0, dataset_id)
            else:
                emit_sync(job_id, loop, "Loading aesthetic predictor into VRAM...", -1.0, dataset_id)

        self._evict_lru(3500)

        vram_before = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
        clip_model = None
        mlp = None
        try:
            clip_model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-L-14", pretrained="openai"
            )
            clip_model = clip_model.to("cuda").eval()

            if not weights_path.exists():
                if job_id and loop:
                    from backend.ml.download_progress import emit_sync
                    emit_sync(job_id, loop, "Downloading aesthetic predictor weights...", -1.0, dataset_id)
                download_weights(weights_path)

            if job_id and loop:
                from backend.ml.download_progress import emit_sync
                emit_sync(job_id, loop, "Loading aesthetic predictor into VRAM...", -1.0, dataset_id)

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

    async def load_dino(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = "dino"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            _loop = loop or asyncio.get_event_loop()
            entry = await _loop.run_in_executor(
                None, self._load_dino_sync, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    def _load_dino_sync(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        import torch
        from transformers import AutoModel, AutoImageProcessor
        from backend.ml.download_progress import emit_sync, is_hf_cached, progress_tqdm_patch

        model_name = "facebook/dinov2-base"
        logger.info("Loading %s...", model_name)

        if job_id and loop:
            needs_download = not is_hf_cached(model_name, "config.json")
            msg = (
                f"Downloading {model_name} (first run, may take a few minutes)..."
                if needs_download else f"Loading {model_name} into VRAM..."
            )
            emit_sync(job_id, loop, msg, -1.0, dataset_id)

        self._evict_lru(1200)

        vram_before = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
        processor = None
        model = None
        try:
            with progress_tqdm_patch(job_id, loop, f"Downloading {model_name}...", dataset_id):
                processor = AutoImageProcessor.from_pretrained(model_name)
                model = AutoModel.from_pretrained(model_name, torch_dtype=torch.float16)
            if job_id and loop:
                emit_sync(job_id, loop, f"Loading {model_name} into VRAM...", -1.0, dataset_id)
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
            with self._sync_lock:
                if model_id not in self._registry:
                    return
                entry = self._registry.pop(model_id)
            try:
                entry.model.cpu()
            except Exception:
                pass
            del entry.model
            del entry.processor
            torch.cuda.empty_cache()

    async def evict_all(self) -> list[str]:
        """Unload every registered ML model from VRAM and return their IDs."""
        import torch
        model_ids = list(self._registry.keys())
        for model_id in model_ids:
            await self.unload(model_id)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return model_ids

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
