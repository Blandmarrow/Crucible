import asyncio
import logging
import threading
import time
from typing import Any

from backend.ml import device as _device

logger = logging.getLogger(__name__)


def _release_to_cpu(model: Any) -> None:
    """Move a registry entry's weights off the accelerator before it is dropped.

    Two loaders store `ModelEntry.model` as a **dict** of tenants — `aesthetic`'s
    `{"clip", "mlp", "preprocess"}` and `nsfw`'s `{"model", "processor",
    "nsfw_idx"}` — so a bare `entry.model.cpu()` raises `AttributeError` on the
    dict, and under the surrounding `except Exception: pass` leaves several GB
    resident. Freeing then rests entirely on the last reference dying before
    `_device.empty_cache()` runs, which no unload can guarantee: the scoring job
    binds a second reference to `aesthetic`'s dict and still holds it while its
    own `finally` runs the auto-unload, so the weights outlived the cache flush
    and the process kept ~3.5 GB until exit.

    Walking the dict's values is the fix that does not depend on refcounts. Each
    tenant is tried independently, because a dict entry mixes weights with things
    that have no `.cpu()` — `preprocess` is a transform, `nsfw_idx` an int — and
    one of those must not stop the real weights from moving.
    """
    tenants = list(model.values()) if isinstance(model, dict) else [model]
    for obj in tenants:
        try:
            obj.cpu()
        except Exception:
            pass


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
                _release_to_cpu(entry.model)
                del entry.model
                del entry.processor
                del self._registry[mid]
                self._locks.pop(mid, None)
                _device.empty_cache()

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

        vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        _fl_dtype = _device.safe_dtype_for_device(torch.bfloat16)
        load_kwargs: dict = {"torch_dtype": _fl_dtype, "trust_remote_code": True}

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
            model = model.to(_device.get_device())
            model.eval()
        except Exception:
            if model is not None:
                try:
                    model.cpu()
                except Exception:
                    pass
            del model, processor
            _device.empty_cache()
            raise

        vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        vram_used = max(5500, (vram_after - vram_before) // (1024 * 1024))
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
        from backend.ml.download_progress import emit_sync, is_hf_cached, progress_tqdm_patch

        # PaliGemma-2 is a gated repo and needs a HuggingFace token. It is not passed here:
        # like the other nine hub loaders in this codebase, it relies on the ambient HF_TOKEN
        # environment variable, which services/secrets_service.py::sync_env writes from the
        # .env chain at import and from the DB at startup and on every Settings -> API Keys
        # save. Passing token= as well would mean the DB->runtime path had to be correct in
        # two mechanisms, only one of which the other loaders exercise.
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

        vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        _pg_dtype = _device.safe_dtype_for_device(torch.bfloat16)
        _active_device = _device.get_device()

        # device_map="cuda" is a HuggingFace/accelerate shorthand that places all
        # layers on CUDA device 0. It does not work on MPS or CPU — load without
        # it on those backends and move the model manually after from_pretrained.
        if _active_device == "cuda":
            kwargs: dict = {"torch_dtype": _pg_dtype, "device_map": "cuda"}
        else:
            kwargs = {"torch_dtype": _pg_dtype}

        processor = None
        model = None
        try:
            with progress_tqdm_patch(job_id, loop, f"Downloading {model_name}...", dataset_id):
                processor = AutoProcessor.from_pretrained(model_name)
                model = PaliGemmaForConditionalGeneration.from_pretrained(model_name, **kwargs)
            if _active_device != "cuda":
                model = model.to(_active_device)
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
            _device.empty_cache()
            raise

        vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        vram_used = max(6000, (vram_after - vram_before) // (1024 * 1024))
        return ModelEntry(model, processor, vram_mb=vram_used)

    async def load_joycaption(
        self,
        variant: str = "alpha",
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = f"joycaption_{variant}"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry

            _loop = loop or asyncio.get_event_loop()
            entry = await _loop.run_in_executor(
                None, self._load_joycaption_sync, model_id, variant, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    def _load_joycaption_sync(
        self,
        model_id: str,
        variant: str,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        import torch
        from transformers import AutoProcessor, LlavaForConditionalGeneration
        from backend.ml.download_progress import emit_sync, is_hf_cached, progress_tqdm_patch

        MODEL_MAP = {
            "alpha": "fancyfeast/llama-joycaption-alpha-two-hf-llava",
            "beta": "fancyfeast/llama-joycaption-beta-one-hf-llava",
        }
        model_name = MODEL_MAP.get(variant, MODEL_MAP["alpha"])
        logger.info("Loading %s...", model_name)

        if job_id and loop:
            needs_download = not is_hf_cached(model_name, "config.json")
            msg = (
                f"Downloading {model_name} (first run, may take several minutes)..."
                if needs_download else f"Loading {model_name} into VRAM..."
            )
            emit_sync(job_id, loop, msg, -1.0, dataset_id)

        self._evict_lru(17000)

        vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        _jc_dtype = _device.safe_dtype_for_device(torch.bfloat16)
        _active_device = _device.get_device()

        if _active_device == "cuda":
            kwargs: dict = {"torch_dtype": _jc_dtype, "device_map": "cuda"}
        else:
            kwargs = {"torch_dtype": _jc_dtype}

        processor = None
        model = None
        try:
            with progress_tqdm_patch(job_id, loop, f"Downloading {model_name}...", dataset_id):
                processor = AutoProcessor.from_pretrained(model_name)
                model = LlavaForConditionalGeneration.from_pretrained(model_name, **kwargs)
            if _active_device != "cuda":
                model = model.to(_active_device)
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
            _device.empty_cache()
            raise

        vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        vram_used = max(17000, (vram_after - vram_before) // (1024 * 1024))
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

        # LAION's sac+logos+ava1-l14-linearMSE MLP head. The filename used to read
        # "aesthetic_predictor_v2_5.pth", which names a different model entirely —
        # Aesthetic Predictor V2.5 is a SigLIP-based predictor, and its own head
        # checkpoint really is called that. The two never collided in practice:
        # `_load_aesthetic_v2_5_sync` gets its head via torch.hub, so it lands in
        # the torch hub cache and never in `models_cache_dir`.
        weights_path = settings.models_cache_dir / "laion_aesthetic_sac_logos_ava1_l14.pth"

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

        vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        clip_model = None
        mlp = None
        try:
            clip_model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-L-14", pretrained="openai"
            )
            clip_model = clip_model.to(_device.get_device()).eval()

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
            mlp = mlp.to(_device.get_device()).eval()
        except Exception:
            for m in (clip_model, mlp):
                if m is not None:
                    try:
                        m.cpu()
                    except Exception:
                        pass
            del clip_model, mlp
            _device.empty_cache()
            raise

        vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        vram_used = max(3500, (vram_after - vram_before) // (1024 * 1024))
        return ModelEntry({"clip": clip_model, "mlp": mlp, "preprocess": preprocess}, None, vram_mb=vram_used)

    async def load_aesthetic_v2_5(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        """Aesthetic Predictor V2.5 — the *second* aesthetic producer, and a
        separate registry entry rather than a replacement for `aesthetic`.

        `aesthetic`'s `ModelEntry.model` is a three-tenant dict whose `clip` and
        `preprocess` also serve zero-shot watermark scoring, CLIP-embedding
        extraction and the `embed-references` upload endpoint. Swapping the
        backbone under it would silently break three features that have nothing
        to do with aesthetics. Both backbones may therefore be resident during a
        single run (~2000 + ~3500 MB), which no realistic setup evicts over.
        """
        model_id = "aesthetic_v2_5"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            _loop = loop or asyncio.get_event_loop()
            entry = await _loop.run_in_executor(
                None, self._load_aesthetic_v2_5_sync, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    def _load_aesthetic_v2_5_sync(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        from aesthetic_predictor_v2_5 import convert_v2_5_from_siglip

        model_name = "google/siglip-so400m-patch14-384"
        logger.info("Loading Aesthetic Predictor V2.5 (%s)...", model_name)

        if job_id and loop:
            from backend.ml.download_progress import emit_sync, is_hf_cached
            # Two downloads land on first run, and they do not share a cache: the
            # SigLIP backbone comes from the hub (~1.6 GB), while the predictor
            # head is fetched by the package itself through
            # `torch.hub.load_state_dict_from_url` and lands in the *torch hub*
            # cache. Neither is a file under `models_cache_dir` to stat, and the
            # backbone is the one worth warning about, so it stands proxy.
            cached = is_hf_cached(model_name, "config.json")
            emit_sync(
                job_id, loop,
                "Loading Aesthetic Predictor V2.5 into VRAM..." if cached
                else "Downloading Aesthetic Predictor V2.5 (first run)...",
                -1.0, dataset_id,
            )

        # ~428M params in SigLIP-so400m's vision tower. Measured on the dev box;
        # the floor is the figure `_evict_lru` reserves before the load, not a
        # cap on what the entry reports afterwards.
        self._evict_lru(2000)

        vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        model = None
        try:
            # No `trust_remote_code=True` despite the package README: SigLIP has
            # been natively supported by `transformers` for many releases, so it
            # buys nothing here and is an arbitrary-code switch on a hub repo.
            # No `token=` either — `secrets_service.sync_env` puts HF_TOKEN in the
            # ambient environment, which every loader in this file relies on.
            model, processor = convert_v2_5_from_siglip(low_cpu_mem_usage=True)
            model = model.to(_device.get_device()).eval()
        except Exception:
            if model is not None:
                try:
                    model.cpu()
                except Exception:
                    pass
            del model
            _device.empty_cache()
            raise

        vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        vram_used = max(2000, (vram_after - vram_before) // (1024 * 1024))
        # The **plain** entry shape, not `aesthetic`'s dict. `_evict_lru` and
        # `unload` now go through `_release_to_cpu`, which walks a dict's tenants
        # rather than raising on it, so the dict shape no longer leaks — but it
        # still buys nothing here, since nothing else needs the backbone.
        return ModelEntry(model, processor, vram_mb=vram_used)

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

        vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        _dino_dtype = _device.safe_dtype_for_device(torch.float16)
        processor = None
        model = None
        try:
            with progress_tqdm_patch(job_id, loop, f"Downloading {model_name}...", dataset_id):
                processor = AutoImageProcessor.from_pretrained(model_name)
                model = AutoModel.from_pretrained(model_name, torch_dtype=_dino_dtype)
            if job_id and loop:
                emit_sync(job_id, loop, f"Loading {model_name} into VRAM...", -1.0, dataset_id)
            model = model.to(_device.get_device()).eval()
        except Exception:
            if model is not None:
                try:
                    model.cpu()
                except Exception:
                    pass
            del model, processor
            _device.empty_cache()
            raise

        vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        vram_used = max(1200, (vram_after - vram_before) // (1024 * 1024))
        return ModelEntry(model, processor, vram_mb=vram_used)

    async def load_nsfw(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = "nsfw"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            _loop = loop or asyncio.get_event_loop()
            entry = await _loop.run_in_executor(
                None, self._load_nsfw_sync, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    def _load_nsfw_sync(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        from transformers import AutoImageProcessor, AutoModelForImageClassification
        from backend.ml.download_progress import emit_sync, is_hf_cached, progress_tqdm_patch

        model_name = "Marqo/nsfw-image-detection-384"
        logger.info("Loading %s...", model_name)

        if job_id and loop:
            needs_download = not is_hf_cached(model_name, "config.json")
            msg = (
                f"Downloading {model_name} (first run)..."
                if needs_download else f"Loading {model_name} into VRAM..."
            )
            emit_sync(job_id, loop, msg, -1.0, dataset_id)

        self._evict_lru(1000)

        vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        processor = None
        model = None
        try:
            with progress_tqdm_patch(job_id, loop, f"Downloading {model_name}...", dataset_id):
                processor = AutoImageProcessor.from_pretrained(model_name)
                model = AutoModelForImageClassification.from_pretrained(model_name)
            if job_id and loop:
                emit_sync(job_id, loop, f"Loading {model_name} into VRAM...", -1.0, dataset_id)
            model = model.to(_device.get_device()).eval()
        except Exception:
            if model is not None:
                try:
                    model.cpu()
                except Exception:
                    pass
            del model, processor
            _device.empty_cache()
            raise

        # Resolve which output index corresponds to the "nsfw" class.
        # The model config maps label indices to class names (e.g. {0: "normal", 1: "nsfw"}).
        id2label: dict = getattr(model.config, "id2label", {})
        nsfw_idx = next(
            (int(k) for k, v in id2label.items() if v.lower() == "nsfw"),
            1,  # default: assume class 1 is nsfw if config is absent
        )

        vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        vram_used = max(1000, (vram_after - vram_before) // (1024 * 1024))
        return ModelEntry({"model": model, "processor": processor, "nsfw_idx": nsfw_idx}, None, vram_mb=vram_used)

    async def load_sam2(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = "sam2"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            _loop = loop or asyncio.get_event_loop()
            from backend.ml.sam2_predictor import _load_sam2_sync
            self._evict_lru(1800)
            entry = await _loop.run_in_executor(
                None, _load_sam2_sync, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    async def load_sam3(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = "sam3"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            _loop = loop or asyncio.get_event_loop()
            from backend.ml.sam3_predictor import _load_sam3_sync
            self._evict_lru(3500)
            entry = await _loop.run_in_executor(
                None, _load_sam3_sync, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    async def load_lama(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = "lama"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            _loop = loop or asyncio.get_event_loop()
            from backend.ml.lama_inpainter import _load_lama_sync
            self._evict_lru(2000)
            entry = await _loop.run_in_executor(
                None, _load_lama_sync, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    async def unload(self, model_id: str) -> None:
        async with self._get_lock(model_id):
            with self._sync_lock:
                if model_id not in self._registry:
                    return
                entry = self._registry.pop(model_id)
            _release_to_cpu(entry.model)
            del entry.model
            del entry.processor
            _device.empty_cache()

    async def evict_all(self) -> list[str]:
        """Unload every registered ML model from VRAM and return their IDs."""
        model_ids = list(self._registry.keys())
        for model_id in model_ids:
            await self.unload(model_id)
        _device.empty_cache()
        return model_ids

    async def load_tag_embedder(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        model_id = "tag_embedder"
        async with self._get_lock(model_id):
            if model_id in self._registry:
                entry = self._registry[model_id]
                entry.last_used = time.time()
                return entry
            _loop = loop or asyncio.get_event_loop()
            entry = await _loop.run_in_executor(
                None, self._load_tag_embedder_sync, job_id, _loop, dataset_id
            )
            with self._sync_lock:
                self._registry[model_id] = entry
            return entry

    def _load_tag_embedder_sync(
        self,
        job_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dataset_id: str | None = None,
    ) -> ModelEntry:
        from sentence_transformers import SentenceTransformer
        from backend.ml.tag_embedder import MODEL_NAME

        logger.info("Loading tag embedder (%s)...", MODEL_NAME)

        if job_id and loop:
            from backend.ml.download_progress import emit_sync, is_hf_cached
            cached = is_hf_cached(MODEL_NAME, "config.json")
            emit_sync(
                job_id, loop,
                "Loading tag embedder into VRAM..." if cached
                else "Downloading tag embedder model (first run)...",
                -1.0, dataset_id,
            )

        self._evict_lru(500)

        vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        model = SentenceTransformer(MODEL_NAME, device=_device.get_device())
        vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
        vram_used = max(500, (vram_after - vram_before) // (1024 * 1024))
        return ModelEntry(model, None, vram_mb=vram_used)

    def list_models(self) -> list[dict]:
        """The model registry, and the single source of truth for what each model *is*.

        `description` lives here rather than in frontend copy so it sits beside the
        loader that knows the repo id and the VRAM figure — the pickers render it
        verbatim. Only PaliGemma-2 mentions gating, deliberately: every other loader
        merely passes the ambient HF_TOKEN along and needs no token, so writing
        "ungated" on the rest would be noise.

        `kind` is singular — the model's *primary* role — and its one reader is the
        `kind == "caption"` filter in `routers/captioning.py::list_models`.
        `florence2_large` is "caption" even though detection also drives it, because
        detection's model list is static frontend copy that reads nothing from here.
        Do not generalise this to a multi-valued capability set: there is no second
        reader to justify one. A new *value* is a different matter and is fine —
        `lama` is `"edit"` because none of caption/score/embed/detect is honest for
        an inpainter, and with `kind == "caption"` the sole reader a new value is
        inert everywhere else.

        `vram_mb` is the literal below — a *forecast* — until the model is resident,
        and the loader's own `ModelEntry.vram_mb` once it is. That second figure is
        `max(literal, measured delta)`, so the swap only ever revises a number
        upward: a picker quoting the cost of a load is never understated, while
        `POST /quality/models/unload`'s `freed_mb` reports what the process actually
        held rather than what the table guessed it would.
        """
        with self._sync_lock:
            entries = dict(self._registry)
        loaded = set(entries)
        all_models = [
            {"id": "florence2_large", "name": "Florence-2-large", "vram_mb": 5500,
             "kind": "caption",
             "description": "Microsoft · short, detailed or tag output chosen by style · no free-text prompt"},
            {"id": "florence2_promptgen", "name": "Florence-2 PromptGen v2", "vram_mb": 5500,
             "kind": "caption",
             "description": "MiaoshouAI · Florence-2 finetuned for Stable Diffusion prompts · adds the promptgen style"},
            {"id": "paligemma2", "name": "PaliGemma-2 3B", "vram_mb": 6000,
             "kind": "caption",
             "description": "Google · gated: needs a HuggingFace token and the licence accepted · 4 styles, no custom prompt"},
            {"id": "joycaption_alpha", "name": "JoyCaption Alpha Two", "vram_mb": 17000,
             "kind": "caption",
             "description": "fancyfeast · Llama 3.1 8B + SigLIP · 12 styles; a custom prompt replaces the style"},
            {"id": "joycaption_beta", "name": "JoyCaption Beta One", "vram_mb": 17000,
             "kind": "caption",
             "description": "fancyfeast · Llama 3.1 8B + SigLIP2 · 12 styles; a custom prompt replaces the style"},
            {"id": "aesthetic", "name": "LAION Aesthetic Predictor", "vram_mb": 3500,
             "kind": "score",
             "description": "LAION sac+logos+ava1 MLP over CLIP ViT-L/14 · also powers watermark scoring and CLIP embeddings"},
            {"id": "aesthetic_v2_5", "name": "Aesthetic Predictor V2.5 (SigLIP)", "vram_mb": 2000,
             "kind": "score",
             "description": "SigLIP-so400m-patch14-384 with a linear head · the second producer of aesthetic_score"},
            {"id": "dino", "name": "DINOv2-base", "vram_mb": 1200,
             "kind": "embed",
             "description": "facebook/dinov2-base · DINOv2 embeddings for the style-similarity workflow"},
            {"id": "nsfw", "name": "Marqo NSFW Detector", "vram_mb": 1000,
             "kind": "score",
             "description": "Marqo/nsfw-image-detection-384 ViT · writes nsfw_score and the is_nsfw flag"},
            {"id": "sam2", "name": "Grounded SAM 2.1 Large (SAM2 + Grounding DINO)", "vram_mb": 1800,
             "kind": "detect",
             "description": "facebook/sam2.1-hiera-large with IDEA-Research/grounding-dino-tiny · segmentation from a text or point prompt"},
            {"id": "sam3", "name": "SAM 3 (text-prompt segmentation)", "vram_mb": 3500,
             "kind": "detect",
             "description": "Open-vocabulary segmentation · loaded from a local models/sam3/*.safetensors checkpoint, never downloaded"},
            {"id": "lama", "name": "LaMa (big-lama)", "vram_mb": 2000,
             "kind": "edit",
             "description": "advimman/lama · resolution-robust inpainting · downloads a 196 MB TorchScript archive on first use"},
            {"id": "tag_embedder", "name": "MiniLM Tag Embedder", "vram_mb": 500,
             "kind": "embed",
             "description": "sentence-transformers/all-MiniLM-L6-v2 · text-only; embeds the tag vocabulary for tag consolidation"},
        ]
        return [
            {
                **m,
                "loaded": m["id"] in loaded,
                **({"vram_mb": entries[m["id"]].vram_mb} if m["id"] in loaded else {}),
            }
            for m in all_models
        ]


model_manager = ModelManager()
