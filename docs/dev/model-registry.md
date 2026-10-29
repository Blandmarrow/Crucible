# Model registry

This file covers the model IDs `model_manager` knows: each registry entry's `kind` and `description`, the prefix → module table, and the loader quirks particular to one model — JoyCaption's generation settings and the `_TAG_STYLES` set, the Florence-2 compatibility patches, and why Aesthetic Predictor V2.5 is an entry of its own. LaMa, the one `kind: "edit"` entry, is documented with the other pixel pipelines in `docs/dev/image-processing-models.md`.

The manager these register with is `docs/dev/ml-models.md`, eviction and unloads are `docs/dev/model-residency.md`, and the `kind == "caption"` filter that decides what the captioning picker offers is `docs/dev/captioning.md`.

### The model ID registry

Every registry entry carries a `kind` (`"caption"` / `"score"` / `"detect"` / `"embed"` / `"edit"`) and a `description`, both authored beside the loader that knows the repo id and the VRAM figure — `kind` is the model's *primary* role, singular, and `description` is the copy the pickers render verbatim. `GET /captioning/models` filters on `kind == "caption"`, so a new entry declaring the wrong one silently changes what the captioning picker offers; see `docs/dev/captioning.md` for that filter and the `_caption_backend` validator behind it.

Model IDs and their captioner/scorer modules:
| Prefix | Module |
|---|---|
| `florence2*` | `ml/florence_captioner.py` |
| `paligemma2` | `ml/paligemma_captioner.py` (gated: needs an `HF_TOKEN` from Settings → API Keys or `.env`; accept license at huggingface.co/google/paligemma2-3b-pt-448). `_load_paligemma2_sync` deliberately passes **no** `token=` — it reads the ambient env var like the other nine hub loaders, so the DB→runtime path has one mechanism rather than two |
| `joycaption_alpha` | `ml/joycaption_captioner.py` (`fancyfeast/llama-joycaption-alpha-two-hf-llava`; Llama 3.1 8B + SigLIP via `LlavaForConditionalGeneration`; ~17 GB VRAM; 12 styles; custom prompt supported) |
| `joycaption_beta` | `ml/joycaption_captioner.py` (`fancyfeast/llama-joycaption-beta-one-hf-llava`; Llama 3.1 8B + SigLIP2; otherwise identical to alpha) |
| `ollama:*` | `ml/ollama_captioner.py` (HTTP calls to localhost:11434) |
| `openai_compat:{id}:{model}` | `ml/openai_compat_captioner.py` (OpenAI-compatible vision API; `id` is the `OpenAIProvider` DB row UUID, `model` is the model name string) |
| `wd14:{variant}` | `ml/wd14_tagger.py` (ONNX inference via `onnxruntime`; downloads from SmilingWolf HuggingFace repos; not tracked by `model_manager` — uses its own module-level cache with `threading.Lock` and double-check locking; variants: `eva02_large` (~2.0 GB RAM), `vit_large` (~1.4 GB RAM), `swinv2` (~0.6 GB RAM); `list_wd14_models()` returns `{id, name, ram_mb, description}` — `ram_mb` is included in the `/captioning/models` response and displayed in the UI alongside each model) |
| `upscale:{abs_path}` | `ml/upscaler.py` (spandrel; keyed by absolute model file path to support multiple loaded upscalers) |
| `aesthetic` | `ml/aesthetic_scorer.py` (auto-downloads LAION's `sac+logos+ava1-l14-linearMSE` weights from `camenduru/improved-aesthetic-predictor` via `hf_hub_download`, cached at `models_cache_dir / "laion_aesthetic_sac_logos_ava1_l14.pth"`; also used for CLIP zero-shot watermark detection and CLIP embedding extraction) |
| `aesthetic_v2_5` | `ml/aesthetic_v2_5_scorer.py` (Aesthetic Predictor V2.5: SigLIP-so400m-patch14-384 + linear head via the `aesthetic-predictor-v2-5` package; ~2.0 GB VRAM; a **separate** entry, not a replacement for `aesthetic` — see below) |
| `dino` | `ml/dino_scorer.py` (`facebook/dinov2-base` via HuggingFace `transformers`; ~1.2 GB VRAM; used for DINOv2 embedding extraction) |
| `nsfw` | `ml/nsfw_scorer.py` (`Marqo/nsfw-image-detection-384` ViT classifier; sets `nsfw_score` + `is_nsfw` flag) |
| `sam2` | `ml/sam2_predictor.py` (`facebook/sam2.1-hiera-large` via `sam2` package; ~1800 MB VRAM budgeted for eviction planning (`list_models`), and the same 1800 MB as the post-load floor, since that budget includes the Grounding DINO the entry later gains; detection only, not captioning; Grounding DINO (`IDEA-Research/grounding-dino-tiny`) is loaded lazily on the first `text_prompt` call **into the sam2 entry's own dict** — see `docs/dev/model-residency.md` § Releasing an entry's weights) |
| `sam3` | `ml/sam3_predictor.py` (native open-vocabulary text-prompt segmentation via `sam3` package; ~3.5 GB VRAM; checkpoint loaded from local `models/sam3/*.safetensors` — safetensors only, no HF download; detection only) |
| `tag_embedder` | `ml/tag_embedder.py` (`sentence-transformers/all-MiniLM-L6-v2`, ~90 MB, ~500 MB VRAM; text-only, not image captioning; embeds the tag vocabulary for dataset-wide tag consolidation — see `docs/dev/tag-consolidation.md`) |

### JoyCaption and tag styles

**JoyCaption inference details** (`ml/joycaption_captioner.py`): Uses `LlavaForConditionalGeneration` with a system + user chat template via `processor.apply_chat_template`. After the processor call, `inputs["pixel_values"]` is explicitly cast to bfloat16 via `safe_dtype_for_device` — required by LLaVA's architecture. Generation uses `do_sample=True, temperature=0.6, top_p=0.9, max_new_tokens=512`. The four tag-producing styles (`danbooru`, `e621`, `rule34`, `booru_like`) are members of `_TAG_STYLES` in `routers/captioning.py` — the router splits their output on commas and stores individual tags, the same as WD14/booru output. Custom prompts override the style prompt entirely.

**`_TAG_STYLES`** (`backend/routers/captioning.py`): module-level `frozenset` containing all comma-separated-output styles: `{"tags", "booru", "danbooru", "e621", "rule34", "booru_like"}`. Both `_run()` and `_run_pipeline_job()` use this set to decide whether to split caption output into individual tags. Do not duplicate this set locally — import or reference it from the module.

### Florence-2 compatibility patches

**Florence-2 PromptGen v2 compatibility patches** (in `_load_florence2_sync`, `ml/model_manager.py`): Two runtime patches are applied after loading `MiaoshouAI/Florence-2-large-PromptGen-v2.0` to fix breakage with `transformers >= 4.50`:
1. `_initialize_missing_keys` is temporarily replaced with a version that swallows `AttributeError` during `from_pretrained`, because the DaViT vision encoder doesn't implement `_initialize_weights` and newer transformers always calls the missing-keys hook after loading.
2. `GenerationMixin` is appended to `Florence2LanguageForConditionalGeneration.__bases__` (after `PreTrainedModel`, as the library requires) because `transformers >= 4.50` removed `GenerationMixin` from `PreTrainedModel`'s inheritance chain, leaving the sub-model's `.generate()` undefined. `generation_config` is then initialised via `GenerationConfig.from_model_config(model.language_model.config)` because it is not set on the sub-model instance. Do not remove either patch without verifying on `transformers >= 4.50`.

**Both Florence-2 variants need two more settings on current transformers** (seen on 4.57, where without them every Florence caption and detection job failed before producing a token):
1. `_load_florence2_sync` passes `attn_implementation="eager"`. Left unset, `from_pretrained` probes `_supports_sdpa`, which the remote code reads off a sub-model its `__init__` has not built yet, raising `AttributeError`.
2. Both `generate()` calls in `ml/florence_captioner.py` take `_GENERATE_KWARGS`, which sets `use_cache=False`. The remote `prepare_inputs_for_generation` indexes the Cache object that newer transformers passes as if it were the legacy tuple (`past_key_values[0][0].shape`), so the first beam-search step raised on the empty cache. The cost is a decoder that re-reads its prefix each step, about 0.6 s for a detailed caption on an RTX 5090.

Every caption and detection test fakes the model, so the suite could not see either failure. `backend/tests/test_florence_transformers_compat.py` pins both settings structurally, since CI has no torch.

### Aesthetic Predictor V2.5

**Why V2.5 gets its own registry entry.** `aesthetic`'s `ModelEntry.model` is a three-tenant dict `{"clip", "mlp", "preprocess"}`, and only `mlp` is aesthetic-specific: `clip` + `preprocess` also serve zero-shot watermark scoring, CLIP-embedding extraction and the `POST /quality/embed-references` upload endpoint. Swapping the backbone under that entry would silently break three features that have nothing to do with aesthetics, so `aesthetic_v2_5` is a **separate** entry and both backbones may be resident during one run (~2000 + ~3500 MB, which no realistic setup evicts over). The existing entry is untouched: `_evict_lru(3500)`, its `vram_mb=3500` floor and its `"LAION Aesthetic Predictor"` registry row all stay exactly as they were.

**The two halves of V2.5 come from different places, and neither is `models_cache_dir`.** `convert_v2_5_from_siglip` pulls the SigLIP backbone from the hub in the ordinary way, but fetches the predictor head itself via `torch.hub.load_state_dict_from_url` from the project's GitHub — so it lands in the *torch hub* cache and no HF-cache probe can see it. The first-run SSE proxy therefore checks the backbone only, which is the download worth warning about anyway. That checkpoint's own filename is `aesthetic_predictor_v2_5.pth` — exactly the name LAION's MLP was stored under in `models_cache_dir` until this feature renamed it. The two never actually collided, because they live in different caches; the rename is insurance against any later change that writes the head by hand, plus a filename that stops lying.

`_load_aesthetic_v2_5_sync` returns the **plain** `ModelEntry(model, processor, vram_mb=max(2000, delta))`, not the dict shape. That used to matter for unloading and no longer does: `_evict_lru` and `unload` now both go through `model_manager._release_to_cpu`, which walks a dict entry's tenants instead of calling `.cpu()` on the dict itself. See `docs/dev/model-residency.md` § Releasing an entry's weights for why "reclaimed by refcount anyway" was wrong. It passes **no `trust_remote_code=True`** despite the package README: SigLIP has been natively supported by `transformers` for many releases, so it buys nothing and is an arbitrary-code switch on a hub repo. `low_cpu_mem_usage=True` only. No `token=` either — like the other hub loaders it relies on the ambient `HF_TOKEN` that `services/secrets_service.py::sync_env` maintains. The 2000 MB floor is SigLIP-so400m's figure (~428M params in the vision tower), not CLIP's 3500.
