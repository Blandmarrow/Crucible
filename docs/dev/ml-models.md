# ML models, object detection, upscaling & LUT grading

This file covers the ML model lifecycle (loading, VRAM management, device abstraction), the model ID registry used by captioning and quality scoring, object detection, image upscaling, and LUT color grading.

### ML model management

`ml/model_manager.py` is a singleton that tracks loaded models and their VRAM usage. Before loading a model it calls `_evict_lru(needed_mb)` to free space. Each model_id gets its own `asyncio.Lock` to serialize inference. All inference runs in `loop.run_in_executor(None, _sync_fn)` to avoid blocking the event loop. Ollama models are not tracked — Ollama manages its own VRAM.

After a successful load, `_registry[model_id]["vram_mb"]` is updated with the measured GPU delta so eviction decisions reflect reality.

**Explicit unload methods** — two public async methods allow callers to free VRAM on demand:

| Method | Effect |
|---|---|
| `await model_manager.unload(model_id)` | Acquires per-model lock, moves weights to CPU, deletes model + processor objects, removes entry, calls `torch.cuda.empty_cache()`. |
| `await model_manager.evict_all()` | Calls `unload()` for every registered model + final `cuda.empty_cache()`. Returns list of unloaded IDs. Used by `POST /api/v1/models/unload-all`. |

**`POST /api/v1/models/unload-all`** (router: `backend/routers/models.py`, prefix `/models`) — evicts all ML models from VRAM without restarting. Returns `{ "status": "ok", "unloaded": [model_id, ...] }`. Call after quality scoring so scoring models don't occupy VRAM when no longer needed.

`HF_TOKEN` from `.env` is injected into `os.environ` early in `main.py` so all `hf_hub_download` calls pick it up automatically.

Model IDs and their captioner/scorer modules:
| Prefix | Module |
|---|---|
| `florence2*` | `ml/florence_captioner.py` |
| `paligemma2` | `ml/paligemma_captioner.py` (needs `HF_TOKEN` in `.env`; accept license at huggingface.co/google/paligemma2-3b-pt-448) |
| `joycaption_alpha` | `ml/joycaption_captioner.py` (`fancyfeast/llama-joycaption-alpha-two-hf-llava`; Llama 3.1 8B + SigLIP via `LlavaForConditionalGeneration`; ~17 GB VRAM; 12 styles; custom prompt supported) |
| `joycaption_beta` | `ml/joycaption_captioner.py` (`fancyfeast/llama-joycaption-beta-one-hf-llava`; Llama 3.1 8B + SigLIP2; otherwise identical to alpha) |
| `ollama:*` | `ml/ollama_captioner.py` (HTTP calls to localhost:11434) |
| `openai_compat:{id}:{model}` | `ml/openai_compat_captioner.py` (OpenAI-compatible vision API; `id` is the `OpenAIProvider` DB row UUID, `model` is the model name string) |
| `wd14:{variant}` | `ml/wd14_tagger.py` (ONNX inference via `onnxruntime`; downloads from SmilingWolf HuggingFace repos; not tracked by `model_manager` — uses its own module-level cache with `threading.Lock` and double-check locking; variants: `eva02_large` (~2.0 GB RAM), `vit_large` (~1.4 GB RAM), `swinv2` (~0.6 GB RAM); `list_wd14_models()` returns `{id, name, ram_mb}` — `ram_mb` is included in the `/captioning/models` response and displayed in the UI alongside each model) |
| `upscale:{abs_path}` | `ml/upscaler.py` (spandrel; keyed by absolute model file path to support multiple loaded upscalers) |
| `aesthetic` | `ml/aesthetic_scorer.py` (auto-downloads weights from `camenduru/improved-aesthetic-predictor` via `hf_hub_download`; also used for CLIP zero-shot watermark detection and CLIP embedding extraction) |
| `dino` | `ml/dino_scorer.py` (`facebook/dinov2-base` via HuggingFace `transformers`; ~1.2 GB VRAM; used for DINOv2 embedding extraction) |
| `nsfw` | `ml/nsfw_scorer.py` (`Marqo/nsfw-image-detection-384` ViT classifier; sets `nsfw_score` + `is_nsfw` flag) |
| `sam2` | `ml/sam2_predictor.py` (`facebook/sam2.1-hiera-large` via `sam2` package; ~900 MB VRAM; detection only, not captioning; Grounding DINO loaded lazily on first `text_prompt` call from `IDEA-Research/grounding-dino-tiny`) |

**JoyCaption inference details** (`ml/joycaption_captioner.py`): Uses `LlavaForConditionalGeneration` with a system + user chat template via `processor.apply_chat_template`. After the processor call, `inputs["pixel_values"]` is explicitly cast to bfloat16 via `safe_dtype_for_device` — required by LLaVA's architecture. Generation uses `do_sample=True, temperature=0.6, top_p=0.9, max_new_tokens=512`. The four tag-producing styles (`danbooru`, `e621`, `rule34`, `booru_like`) are members of `_TAG_STYLES` in `routers/captioning.py` — the router splits their output on commas and stores individual tags, the same as WD14/booru output. Custom prompts override the style prompt entirely.

**`_TAG_STYLES`** (`backend/routers/captioning.py`): module-level `frozenset` containing all comma-separated-output styles: `{"tags", "booru", "danbooru", "e621", "rule34", "booru_like"}`. Both `_run()` and `_run_pipeline_job()` use this set to decide whether to split caption output into individual tags. Do not duplicate this set locally — import or reference it from the module.

**Target resolution preprocessing**: `CaptionJobRequest` accepts optional `target_width` / `target_height`. When set, `ml/image_utils.py::preprocess_for_caption()` center-crops each image to the target aspect ratio and resizes it to the exact target resolution before inference. This ensures captions describe the composition the model will actually see at training time. All captioners (Florence-2, PaliGemma-2, JoyCaption, Ollama) call this utility; Ollama's existing `max_px` scale-down runs afterward on the already-cropped image. Omitting both fields leaves behavior unchanged.

Quality scorers and what they add to `Image`:
| Module | Columns written | Notes |
|---|---|---|
| `ml/technical_scorer.py` | `blur_score`, `noise_score`, `uniformity_score`, `color_score`, `saturation_score`; flags `is_blurry`, `is_noisy`, `is_uniform` | Pure OpenCV/numpy, no GPU |
| `ml/aesthetic_scorer.py` | `aesthetic_score` (1–10), `watermark_score` (0–1), flag `has_watermark`, `clip_embedding` (BLOB, float16) | CLIP ViT-L-14; text encoder used for zero-shot watermark; image encoder for embeddings |
| `ml/dino_scorer.py` | `dino_embedding` (BLOB, float16), `dino_layer_embeddings` (BLOB, float16) | `dino_embedding`: final-layer CLS token, 768-dim. `dino_layer_embeddings`: all 12 transformer-layer CLS tokens concatenated, 18 432 bytes (12 × 768 × float16); layer N (1-indexed) at offset `(N-1)*768*2`. `slice_layer_embedding(blob, layer)` extracts one layer's bytes. |
| `ml/similarity_scorer.py` | — | CPU-only. `compute_style_similarity(ref_bytes, cand_bytes)` — cosine similarity of candidates to mean reference. `compute_combined_similarity(ref_clip, cand_clip, ref_dino, cand_dino, clip_w=0.38, dino_w=0.62)` — weighted blend of CLIP and DINOv2 cosine similarities. |

**Config validation** (`backend/config.py`): `@model_validator(mode="after")` enforces at startup: (1) `max_vram_mb < 1000` → `ValueError` (fail fast); (2) empty `hf_token` → debug-level warning (not hard error); (3) unrecognised `.env` keys → WARNING log; `extra="ignore"` retained so OS env vars are never flagged. `config.py` still declares `watermark_threshold` for legacy `.env` compatibility but the quality router reads all five thresholds from the `threshold_settings` DB table instead.

**TorchDynamo is disabled** (`TORCHDYNAMO_DISABLE=1` set in `main.py`). Triton is unavailable on Windows and single-image inference gains nothing from `torch.compile`, so it is disabled for the entire process. Do not remove this without re-testing all ML inference paths on Windows.

**Florence-2 PromptGen v2 compatibility patches** (in `_load_florence2_sync`, `ml/model_manager.py`): Two runtime patches are applied after loading `MiaoshouAI/Florence-2-large-PromptGen-v2.0` to fix breakage with `transformers >= 4.50`:
1. `_initialize_missing_keys` is temporarily replaced with a version that swallows `AttributeError` during `from_pretrained`, because the DaViT vision encoder doesn't implement `_initialize_weights` and newer transformers always calls the missing-keys hook after loading.
2. `GenerationMixin` is appended to `Florence2LanguageForConditionalGeneration.__bases__` (after `PreTrainedModel`, as the library requires) because `transformers >= 4.50` removed `GenerationMixin` from `PreTrainedModel`'s inheritance chain, leaving the sub-model's `.generate()` undefined. `generation_config` is then initialised via `GenerationConfig.from_model_config(model.language_model.config)` because it is not set on the sub-model instance. Do not remove either patch without verifying on `transformers >= 4.50`.

**ML device abstraction** (`backend/ml/device.py`): centralises all device-detection and device-aware utilities. All ML modules (`model_manager.py`, `aesthetic_scorer.py`, `florence_captioner.py`, `paligemma_captioner.py`, `dino_scorer.py`, `upscaler.py`) import from here — nothing else calls `torch.cuda.*` directly. Detection priority: `cuda` (NVIDIA or ROCm, identical API) → `mps` (Apple Silicon) → `cpu`. Key helpers: `get_device()`, `empty_cache()`, `memory_allocated_bytes()`, `memory_reserved_mb()`, `autocast_ctx()`, `safe_dtype_for_device()`, `is_oom_error()`.

### Object detection

Detection runs as a background job, same pattern as quality scoring. Three model families are supported.

**Router**: `backend/routers/detection.py`, prefix `/detection`.

| Endpoint | Body / params | Returns |
|---|---|---|
| `POST /detection/run` | `DetectionJobRequest` | `{ job_id, total }` |
| `GET /detection/image/{image_id}` | — | `list[DetectionOut]` |

**`DetectionJobRequest`** fields:

| Field | Default | Effect |
|---|---|---|
| `dataset_id` | required | Target dataset |
| `image_ids` | `null` | If set, only these images; otherwise the whole dataset |
| `model` | required | `"florence2_large"`, `"florence2_promptgen"`, `"nudenet"`, or `"sam2"` |
| `task` | required | `"<OD>"`, `"<CAPTION_TO_PHRASE_GROUNDING>"`, `"nudenet"`, `"text_prompt"`, or `"points"` |
| `custom_prompt` | `""` | Phrase to ground (`<CAPTION_TO_PHRASE_GROUNDING>` / SAM2 `text_prompt`) |
| `use_caption_as_prompt` | `false` | When `true`, each image's `caption_text` is used as the per-image prompt; images without a caption are skipped |
| `overwrite` | `true` | Delete existing detections for each image before inserting new ones |
| `min_prob` | `0.5` | NudeNet confidence threshold (ignored for other models) |
| `point_prompts` | `null` | Normalized `[x, y]` coordinates for SAM2 `points` mode |
| `point_labels` | `null` | Foreground (1) / background (0) labels matching `point_prompts` |

**Tasks by model**:
- Florence-2: `<OD>` (fixed-vocabulary, no prompt) and `<CAPTION_TO_PHRASE_GROUNDING>` (phrase grounding with text or per-image caption).
- NudeNet: `"nudenet"` only — CPU ONNX, body-part bounding boxes. Not tracked by `model_manager`.
- SAM2: `"text_prompt"` (Grounding DINO → SAM2 masks from text) or `"points"` (point-prompted SAM2 masks). Auto mode was removed — it produced unlabelled "region" outputs with no semantic value.

**`_ALLOWED_MODELS`** (router): `{"florence2_large", "florence2_promptgen", "nudenet", "sam2"}`. **`_ALLOWED_TASKS`**: `{"<OD>", "<CAPTION_TO_PHRASE_GROUNDING>", "nudenet", "text_prompt", "points"}`.

**ML inference**:
- Florence-2: `backend/ml/florence_captioner.py::infer_sync_detection` / `detect_image`. Returns normalized bboxes `[x1, y1, x2, y2]` in 0–1 range.
- NudeNet: `backend/ml/nudenet_scorer.py::detect_sync`. Module-level cache with `threading.Lock`. Returns `[{label, bbox, score}]`.
- SAM2: `backend/ml/sam2_predictor.py`. `_load_sam2_sync` loads `SAM2ImagePredictor` from `facebook/sam2.1-hiera-large` via `sam2` package. `_ensure_gdino()` lazy-loads `IDEA-Research/grounding-dino-tiny` (Grounding DINO) on first `text_prompt` call, module-level cached with `threading.Lock` double-check. `_predict_text`: GDINO → pixel boxes → SAM2 with `multimask_output=True` (3 candidates); best mask selected by `argmax(iou_scores)`. `_predict_points`: point-prompted SAM2. Bboxes derived from mask min/max. Masks stored as Douglas-Peucker simplified polygon JSON in `Detection.mask`.

**Storage**: `backend/models/detection.py::Detection` table. Indexed on `image_id` and `label`. `Detection.mask: Text | None` stores polygon JSON: `{"polygons": [[[x,y],...], ...]}` in normalized 0–1 coordinates. `ImageOut.detections: list[DetectionOut]` is populated only in `GET /images/{image_id}` (the detail endpoint) — not in `list_images`.

**Frontend surfaces**:
- `SelectionToolbar` — "Detect" button opens a modal (model, task, prompt, min_prob slider for NudeNet, overwrite toggle).
- `CaptioningPage` — "Object Detection" section when a Florence-2 model is selected; uses the same model as captioning.
- `ImageDetailPage` — DETECTIONS panel (collapsible, label chips with counts + SVG overlay with per-label color coding). For SAM2 results, polygon mask fills are rendered in addition to bounding boxes. Label chips toggle `hiddenLabels: Set<string>` to filter both boxes and mask fills. Eye icon shows/hides all detections. SAM2 point-prompt mode: toolbar button "SAM Points" activates `samPointMode`; left-click = foreground point, right-click = background point; points rendered as colored circles on the SVG overlay and submitted via `point_prompts`/`point_labels` in the detection request.

Flag thresholds:
| Flag | Column | Default threshold | Source |
|---|---|---|---|
| `is_blurry` | `blur_score` (Laplacian variance) | < 100 | `blur_threshold` in `threshold_settings` DB table |
| `is_noisy` | `noise_score` (smooth-region std dev) | > 15 | `noise_threshold` in `threshold_settings` DB table |
| `is_uniform` | `uniformity_score` (grayscale std dev) | < 12 | `uniformity_threshold` in `threshold_settings` DB table |
| `has_watermark` | `watermark_score` (CLIP zero-shot, 0–1) | ≥ 0.6 | `watermark_threshold` in `threshold_settings` DB table |
| `is_duplicate` | `phash` (perceptual hash Hamming distance) | < 8 | `duplicate_threshold` in `threshold_settings` DB table |
| `is_nsfw` | `nsfw_score` (Marqo classifier, 0–1) | ≥ 0.5 | `nsfw_threshold` in `threshold_settings` DB table |

`gdino_threshold` (default 0.35) in `threshold_settings` controls the Grounding DINO box confidence cutoff passed to SAM2 `text_prompt` detection — read via `get_thresholds()` at the start of each SAM2 detection job. `text_threshold` scales with it (`gdino_threshold - 0.10`, floored at 0.01).

All thresholds are user-configurable via Settings (`/settings` → `GET/PATCH /api/v1/settings/thresholds`). Quality flag thresholds take effect on the next scoring run; `gdino_threshold` takes effect on the next SAM2 detection run. Constants in `technical_scorer.py` serve only as parameter defaults — the quality router always passes DB-fetched values via `backend/services/threshold_service.py::get_thresholds()`.

**Style similarity flow**: (1) run scoring with the desired embedding flags — `run_embeddings=True` stores `clip_embedding`; `run_dino=True` stores `dino_embedding` (independent of `run_embeddings`); `run_dino_layers=True` (requires `run_dino=True`) stores `dino_layer_embeddings`. (2) call `POST /quality/style-similarity` with `reference_image_ids` and/or `reference_embeddings` (base64 float16 bytes, CLIP-only). The `embedding_type` field selects the scoring mode:

| `embedding_type` | `dino_layer` | Column(s) written | Description |
|---|---|---|---|
| `"clip"` | — | `style_similarity_score` | Cosine similarity of CLIP embeddings |
| `"dino"` | `null` | `style_similarity_score` | Cosine similarity of DINOv2 final-layer embeddings |
| `"dino"` | 1–12 | `style_similarity_score` | Cosine similarity using a specific DINOv2 transformer layer (from `dino_layer_embeddings`) |
| `"combined"` | `null` | `style_similarity_score` | `0.38 × clip_sim + 0.62 × dino_sim` (final layer) |
| `"combined"` | 1–12 | `style_similarity_score` | `0.38 × clip_sim + 0.62 × dino_layer_sim` (specific layer) |
| `"dino_all_layers"` | — | `dino_layer_scores` (JSON) + `style_similarity_score` | Scores each of the 12 DINOv2 layers independently; writes `{"1": score, …, "12": score}` and sets `style_similarity_score` to layer 12's value |
| `"combined_all_layers"` | — | `dino_layer_scores` (JSON) + `style_similarity_score` | Blended score (0.38 CLIP + 0.62 DINOv2) for each of the 12 layers; writes `{"1": score, …, "12": score}` and sets `style_similarity_score` to layer 12's value |

Local reference files can be embedded on-the-fly via `POST /quality/embed-references` (multipart upload → returns base64 CLIP embeddings). External refs are CLIP-only; `"combined"`, `"dino"`, and `"dino_all_layers"` / `"combined_all_layers"` modes require dataset images as references. No job queue — all similarity computation is CPU-only numpy and runs synchronously in the request. `StyleSimilarityRequest` accepts an optional `image_ids: list[str] | None` field; when set, only those images are scored (candidate queries in all embedding-type branches are filtered accordingly). `QualityPage` omits `image_ids` (scores the whole dataset); `SelectionToolbar` passes the current selection.

### Upscaling

ML-based image upscaling via the `spandrel` library, which auto-detects architecture from `.pth`/`.safetensors` files (RealESRGAN/RRDB, SwinIR, HAT, OmniSR, and more).

**Router**: `backend/routers/upscaling.py`, prefix `/upscaling`.

| Endpoint | Body / params | Returns |
|---|---|---|
| `GET /upscaling/models` | — | `list[UpscaleModelInfo]` — scans `settings.upscale_models_dir` |
| `POST /upscaling/run` | `UpscaleRunRequest` | `{ job_id, total }` |

**Config**: `settings.upscale_models_dir` (default `models/upscale_models/`). Override with `UPSCALE_MODELS_DIR=` in `.env` (e.g. pointing at a ComfyUI models folder). The directory is created automatically on startup.

**`UpscaleRunRequest`** fields: `dataset_id`, `image_ids` (null = whole dataset), `model_path`, `replace` (overwrite source vs. new file), `target_width`/`target_height` (optional: upscale then resize down to fit, maintaining AR), `subfolder` (null = all; applied only when `image_ids` is null), `quality_flags` (null = no filter; when set, excludes images where any of the listed flags is `True`; applied only when `image_ids` is null).

**ML inference** (`backend/ml/upscaler.py`):
- `scan_upscale_models(dir)` — globs `*.pth`/`*.safetensors`, detects scale from filename heuristics (`4x-`, `_x4`, `_X4`, etc.), returns `[{name, path, scale}]` without loading weights.
- `upscale_image_sync(src, dest, model_path, replace, target_w, target_h)` — loads via `spandrel.ModelLoader().load_from_file()`, tiles if either dimension > 1024 px (512 px tiles, 64 px overlap, linear-ramp seam blending), optional LANCZOS resize post-upscale.
- Model caching uses `model_manager._registry` under ID `upscale:{abs_path}`; `_ensure_upscaler_loaded` includes a double-check after re-acquiring `_sync_lock` to prevent the TOCTOU double-load race.

**Output modes**: *New file* — filename `{stem}_up{N}x{ext}` (collision-handled via `unique_filename_with_thumb`), new `Image` record created, thumbnail generated. *Replace* — updates `width`/`height`/`file_size_bytes`/`updated_at`/`processing_history` on existing record, thumbnail regenerated. Replace mode calls `protect_file_before_overwrite` before overwriting the file — see `docs/dev/versioning.md` for the copy-on-write mechanism.

**History management**: Non-replace upscale navigation uses `{ replace: true }` so the source image's history entry is overwritten rather than stacked, leaving a single clean entry so one Back press returns to the gallery. Do not remove the `replace: true` from these `paneGo` calls without considering the double-Back regression. See `docs/dev/gallery-and-images.md` § Gallery navigation state for `injectNavId`/`paneGo` mechanics.

**Frontend surfaces**:
- `ImageDetailPage` — "Upscale" toolbar button toggles inline controls (model select, Replace checkbox, optional W×H). Uses `upscalingApi.run()` with `image_ids: [imageId]`.
- `SelectionToolbar` — "Upscale" button opens a modal with `<UpscaleForm>`.
- `BulkEditPage` — "Upscale" tab (see Bulk caption editing section).

`UpscaleForm` (`frontend/src/components/upscale/UpscaleForm.tsx`) — reusable form used by `SelectionToolbar` and `BulkEditPage`. Queries `["upscale-models"]` with `staleTime: Infinity` (model list never changes at runtime).

### LUT Color Grading

Applies `.cube` and `.3dl` 3D color look-up tables to images with a user-controlled blend intensity.

**Router**: `backend/routers/lut.py`, prefix `/lut`.

| Endpoint | Body / params | Returns |
|---|---|---|
| `GET /lut/models` | — | `list[LutModelInfo]` — scans `settings.lut_models_dir` |
| `POST /lut/run` | `LutRunRequest` | `{ job_id, total }` |

**Config**: `settings.lut_models_dir` (default `models/lut/`). The directory is created automatically on startup.

**`LutRunRequest`** fields: `dataset_id`, `image_ids` (null = whole dataset), `lut_path`, `intensity` (0.0–1.0, clamped by validator), `replace` (overwrite source vs. new file), `subfolder` (null = all; applied only when `image_ids` is null), `quality_flags` (null = no filter; when set, excludes images where any of the listed flags is `True`; applied only when `image_ids` is null).

**ML processing** (`backend/ml/lut_processor.py`):
- `scan_lut_models(dir)` — globs `*.cube`/`*.3dl`, returns `[{name, path, format}]`.
- `apply_lut_sync(src, dest, lut_path, intensity, replace)` — loads PIL image + `exif_transpose`, converts to float32 [0,1], applies trilinear LUT interpolation, blends `original * (1-intensity) + graded * intensity`, saves. Returns `{width, height, file_size_bytes, format, out_path}`. Note `out_path` may differ from `dest` when the source format is unsupported and falls back to PNG — the router uses `info["out_path"]` to derive the actual output path.
- Module-level `_lut_cache: dict[str, np.ndarray]` — parsed LUT arrays are cached for the process lifetime. LUTs are tiny (<1 MB each); no eviction needed in practice.

**LUT axis-ordering invariant**: The `.cube` spec (and `.3dl`) stores data with **R varying fastest, B slowest**. After `reshape(N, N, N, 3)` numpy's axis order is `[B, G, R]`. Both `_parse_cube` and `_parse_3dl` therefore call `.transpose(2, 1, 0, 3)` to produce a `[R, G, B]`-indexed array, so that `lut[r, g, b]` is the natural lookup in `_apply_lut_array`. Do not remove this transpose — without it R and B are swapped in the lookup, producing visually wrong results.

**Output modes**: *New file* — filename `{stem}_lut{ext}` (collision-handled via `unique_filename_with_thumb`), new `Image` record created, thumbnail generated. *Replace* — updates `file_size_bytes`/`updated_at`/`processing_history` on existing record, thumbnail regenerated. Replace mode calls `protect_file_before_overwrite` before overwriting the file — see `docs/dev/versioning.md` for the copy-on-write mechanism.

**Frontend surfaces**:
- `ImageDetailPage` — "LUT" toolbar button (mutually exclusive with Crop and Upscale) toggles inline controls: LUT `<select>`, intensity slider, Replace checkbox, Run button. Non-replace completion calls `injectNavId` + `paneGo` to navigate to the new image (same pattern as upscaling). See `docs/dev/gallery-and-images.md` § Gallery navigation state for `injectNavId`/`paneGo` mechanics.
- `SelectionToolbar` — "LUT" button opens a modal with `<LutForm>`.
- `BulkEditPage` — "Apply LUT" tab (see Bulk caption editing section).

`LutForm` (`frontend/src/components/lut/LutForm.tsx`) — reusable form used by `SelectionToolbar` and `BulkEditPage`. Queries `["lut-models"]` with `staleTime: Infinity`. On job completion invalidates `["images", datasetId]` and calls `onSuccess?.()`.
