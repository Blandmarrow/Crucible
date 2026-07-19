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
| `sam3` | `ml/sam3_predictor.py` (native open-vocabulary text-prompt segmentation via `sam3` package; ~3.5 GB VRAM; checkpoint loaded from local `models/sam3/*.safetensors` — safetensors only, no HF download; detection only) |
| `tag_embedder` | `ml/tag_embedder.py` (`sentence-transformers/all-MiniLM-L6-v2`, ~90 MB, ~500 MB VRAM; text-only, not image captioning; embeds the tag vocabulary for dataset-wide tag consolidation — see `docs/dev/tag-consolidation.md`) |

**JoyCaption inference details** (`ml/joycaption_captioner.py`): Uses `LlavaForConditionalGeneration` with a system + user chat template via `processor.apply_chat_template`. After the processor call, `inputs["pixel_values"]` is explicitly cast to bfloat16 via `safe_dtype_for_device` — required by LLaVA's architecture. Generation uses `do_sample=True, temperature=0.6, top_p=0.9, max_new_tokens=512`. The four tag-producing styles (`danbooru`, `e621`, `rule34`, `booru_like`) are members of `_TAG_STYLES` in `routers/captioning.py` — the router splits their output on commas and stores individual tags, the same as WD14/booru output. Custom prompts override the style prompt entirely.

**`_TAG_STYLES`** (`backend/routers/captioning.py`): module-level `frozenset` containing all comma-separated-output styles: `{"tags", "booru", "danbooru", "e621", "rule34", "booru_like"}`. Both `_run()` and `_run_pipeline_job()` use this set to decide whether to split caption output into individual tags. Do not duplicate this set locally — import or reference it from the module.

**EXIF-consistent opening (mandatory for inference)**: every ML inference path must open images through `ml/image_utils.py::open_rgb(path)` — `Image.open().convert("RGB")` + `ImageOps.exif_transpose`. This guarantees all predictors (captioners, quality scorers, SAM2/SAM3, NudeNet) see pixels in the same EXIF-transposed frame, so normalized point/box coordinates denormalize consistently and detection overlays align with the subject on rotated images. Never open with a bare `Image.open(...)` in an inference path; `preprocess_for_caption` itself calls `open_rgb`. Close the returned image right after handing pixels to the model (per the "Close PIL Images after preprocessing" invariant).

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

Detection runs as a background job, same pattern as quality scoring. Four model families are supported.

**Router**: `backend/routers/detection.py`, prefix `/detection`.

| Endpoint | Body / params | Returns |
|---|---|---|
| `POST /detection/run` | `DetectionJobRequest` | `{ job_id, total }` |
| `GET /detection/image/{image_id}` | — | `list[DetectionOut]` |
| `GET /detection/labels/{dataset_id}` | — | `[{label, image_count}]` — distinct labels in the dataset; feeds the ExportPage loss-mask label chips (see `docs/dev/export-and-bulk-ops.md`) and the bulk-delete label chips |
| `GET /detection/models/{dataset_id}` | — | `[{model, image_count}]` — distinct models in the dataset; feeds the bulk-delete model chips |
| `GET /detection/stats/{dataset_id}?subfolder=` | — | Aggregate stats for the Stats "Detections & Masks" section (totals, label/model distributions, score & coverage histograms, detections-per-image); see `docs/dev/dashboard-pages.md` § Statistics page |
| `PATCH /detection/{detection_id}` | `DetectionUpdate {label}` | `DetectionOut` — relabel one detection |
| `DELETE /detection/{detection_id}` | — | 204 — delete one detection (no dialog; regenerable) |
| `POST /detection/bulk-delete` | `DetectionBulkDeleteRequest` | `{deleted, dry_run}` — scoped delete; `dry_run` returns the match count only |
| `POST /detection/merge` | `DetectionMergeRequest {detection_ids ≥2}` | `DetectionOut` — merge ≥2 same-image rows into one `model="manual"` row |
| `POST /detection/manual` | `ManualDetectionRequest` | `DetectionOut` (no SAM) **or** `{job_id}` (with SAM) — hand-drawn box |
| `POST /detection/{detection_id}/refine` | `DetectionRefineRequest {point_prompts, point_labels}` | `{job_id}` — point-refine an existing mask (400 if the row has no mask) |

**Management routes are sync/DB-only** except `manual` (with SAM) and `refine`, which enqueue a `job_type="detection"` job. Declare the static paths (`/models`, `/bulk-delete`, `/merge`, `/manual`) before the `/{detection_id}` routes. `bulk-delete` resolves scope like `crop_to_detection` (image_ids > dataset + `normalize_subfolder` + `ALLOWED_FLAG_KEYS` exclusions), then optional `label.in_` / `model.in_` / `score < score_below`. SQL `score <` never matches NULL, so unscored/manual rows are immune to a score filter (intended).

**`model="manual"` provenance** — refined, merged, and hand-drawn rows are stored with `model="manual"` so automatic re-runs (which now scope their overwrite delete to the running model) never wipe them. The `task` column records how the row was made: `"manual"` (drawn box, no SAM), `"box_prompt"` (drawn box segmented by SAM), `"refine"` (point-refined mask), `"merge"` (geometry union of ≥2 rows). `_ALLOWED_MODELS`/`_ALLOWED_TASKS` are unchanged — these new tasks are created by the management endpoints, never validated through `/run`.

**Per-model overwrite scoping** — each `/run` branch's `overwrite` delete is scoped to its own model (`Detection.model == "nudenet"`/`"sam2"`/`"sam3"`, or `body.model` for Florence), so re-running one model leaves the others' rows — and every `model="manual"` row — intact. Do not revert to the old dataset-wide `delete(Detection).where(image_id == …)`.

**`Detection.mask_area` (persisted coverage fraction)** — the fraction (0–1) of the image covered by a detection's geometry, computed by `mask_utils.detection_mask_area` (shoelace sum over the normalized polygons, or bbox rectangle area when there is no polygon, clamped to [0, 1]). It is kept in sync by two SQLAlchemy `set` listeners in `backend/models/detection.py` — one on `Detection.mask`, one on `Detection.bbox` — that recompute it on every ORM attribute assignment (including constructor kwargs), so all write paths (run branches, `/manual`, `/merge`, `/refine`'s in-place mutation, and the crop remap `detection_service.remap_detections_for_crop`, which assigns `mask` then `bbox`) update it with zero call-site changes. `bbox` is non-nullable so it is always assigned; when both are set the polygon area wins over the bbox. **Invariant: geometry must always be written via ORM attribute assignment** — a raw `update(Detection)` / SQL write to `mask` or `bbox` bypasses the listeners and leaves `mask_area` stale (mirrors the `Image.caption_token_count` invariant). Stats read this column instead of parsing polygon JSON per request; the migration backfills existing rows with an inlined copy of the math (`c9e2f4a6b8d1`).

**`DetectionJobRequest`** fields:

| Field | Default | Effect |
|---|---|---|
| `dataset_id` | required | Target dataset |
| `image_ids` | `null` | If non-null, only these images (an **empty list matches nothing**); `null` = the whole dataset, optionally narrowed by `subfolder`/`quality_flags` |
| `subfolder` | `null` | Whole-dataset runs only (ignored when `image_ids` is set): restrict to this subfolder (`normalize_subfolder`) |
| `quality_flags` | `null` | Whole-dataset runs only: exclude images where any listed flag is `True` (`ALLOWED_FLAG_KEYS`-validated, `as_boolean().is_not(True)`) — same convention as `bulk-delete`/`crop` |
| `model` | required | `"florence2_large"`, `"florence2_promptgen"`, `"nudenet"`, `"sam2"`, or `"sam3"` |
| `task` | required | `"<OD>"`, `"<CAPTION_TO_PHRASE_GROUNDING>"`, `"nudenet"`, `"text_prompt"`, or `"points"` |
| `custom_prompt` | `""` | Phrase to ground (`<CAPTION_TO_PHRASE_GROUNDING>` / SAM2 or SAM3 `text_prompt`) |
| `use_caption_as_prompt` | `false` | When `true`, each image's `caption_text` is used as the per-image prompt; images without a caption are skipped |
| `overwrite` | `true` | Delete this model's existing detections for each image before inserting new ones (scoped to the running model — other models' and `manual` rows survive; see Per-model overwrite scoping above) |
| `min_prob` | `0.5` | NudeNet confidence threshold (ignored for other models) |
| `point_prompts` | `null` | Normalized `[x, y]` coordinates for SAM2 `points` mode |
| `point_labels` | `null` | Foreground (1) / background (0) labels matching `point_prompts` |
| `sync_watermark_flag` | `false` | Set/clear `Image.has_watermark` from per-image results (see below); rejected with 400 for non-grounding tasks |

**Tasks by model**:
- Florence-2: `<OD>` (fixed-vocabulary, no prompt) and `<CAPTION_TO_PHRASE_GROUNDING>` (phrase grounding with text or per-image caption).
- NudeNet: `"nudenet"` only — CPU ONNX, body-part bounding boxes. Not tracked by `model_manager`.
- SAM2: `"text_prompt"` (Grounding DINO → SAM2 masks from text) or `"points"` (point-prompted SAM2 masks). Auto mode was removed — it produced unlabelled "region" outputs with no semantic value.
- SAM3: `"text_prompt"` only — native open-vocabulary segmentation, every instance of the phrase gets a mask in one pass. No caption-as-prompt, no points (points stay SAM2-only).

**Comma-separated multi-phrase prompts** — `custom_prompt` may hold several phrases separated by commas; one job runs them all (no schema change — the frontend still sends one string). A single phrase is byte-identical to the old single-prompt behavior. Per model:
- **SAM3** (`sam3_predictor.predict_sync`): splits on `,`, calls `processor.set_image(img)` **once**, then loops `set_text_prompt(prompt=phrase, state=state)` per phrase, collecting each phrase's masks/scores with `label = phrase`. Reusing the encoded image state avoids re-running the expensive ViT pass per phrase.
- **SAM2 / Grounding DINO** (`sam2_predictor._predict_text`): commas are normalized to GDINO's native `" . "` class separator (`" . ".join(p.strip() for p in text.split(",") if p.strip())`) in a single pass; GDINO returns a per-phrase label for each box.
- **Florence-2** `<CAPTION_TO_PHRASE_GROUNDING>` already grounds multi-phrase free text — no change.
- One shared threshold applies to all phrases; `sync_watermark_flag` still sets the flag if *any* phrase matched.

**`_ALLOWED_MODELS`** (router): `{"florence2_large", "florence2_promptgen", "nudenet", "sam2", "sam3"}`. **`_ALLOWED_TASKS`**: `{"<OD>", "<CAPTION_TO_PHRASE_GROUNDING>", "nudenet", "text_prompt", "points"}`.

**Watermark flag sync** (`sync_watermark_flag`) — grounds watermark phrases (e.g. prompt `"watermark. text. logo."`) and writes the located result back to the CLIP-derived `has_watermark` flag. Eligible only for the text-prompt grounding tasks in `_WATERMARK_SYNC_TASKS` = `{"text_prompt", "<CAPTION_TO_PHRASE_GROUNDING>"}` (so SAM2/SAM3 text_prompt and Florence grounding; the request-path check 400s for `<OD>`, `nudenet`, and SAM2 `points`). Inside each per-image session, immediately before that image's commit, `_apply_watermark_flag(session, img_id, bool(detections))` sets the flag `True` on a hit and `False` when scanned clean — using the in-memory result, never a re-query, and following the copy-then-reassign JSON invariant. It runs **only after successful inference** (`inference_ok` guard): an inference exception leaves the old flag untouched, never silently clearing a never-scanned image. Because it lives inside the run, "last synced run wins" — with `overwrite` off, the flag still reflects only this run's result. Under caption-as-prompt grounding, uncaptioned images are filtered out of the scan set and so are left untouched. Each branch's early `return` first awaits `_finish_watermark_sync()` — a no-op unless the run synced flags — which lazily imports `refresh_stats` (matching `captioning.py`) and refreshes the dataset's cached stats in a fresh session so the Stats flag counts update; a cancelled run may skip it (acceptable).

**ML inference**:
- Florence-2: `backend/ml/florence_captioner.py::infer_sync_detection` / `detect_image`. Returns normalized bboxes `[x1, y1, x2, y2]` in 0–1 range.
- NudeNet: `backend/ml/nudenet_scorer.py::detect_sync`. Module-level cache with `threading.Lock`. Returns `[{label, bbox, score}]`.
- SAM2: `backend/ml/sam2_predictor.py`. `_load_sam2_sync` loads `SAM2ImagePredictor` from `facebook/sam2.1-hiera-large` via `sam2` package. `_ensure_gdino()` lazy-loads `IDEA-Research/grounding-dino-tiny` (Grounding DINO) on first `text_prompt` call, module-level cached with `threading.Lock` double-check. `_predict_text`: GDINO → pixel boxes → SAM2 with `multimask_output=True` (3 candidates); best mask selected by `argmax(iou_scores)`. `_predict_box` (extracted from `_predict_text`, label-agnostic) segments a single pixel box and is reused by `predict_sync(mode="box")` for the hand-drawn-box path. `_predict_points`: point-prompted SAM2. Bboxes derived from mask min/max. Masks stored as Douglas-Peucker simplified polygon JSON in `Detection.mask` (polygon/bbox helpers shared with SAM3 via `backend/ml/mask_utils.py`).
  - **Mask refine** (`refine_sync`): seeds SAM2's `mask_input` from the detection's existing geometry. `mask_utils.polygons_to_mask_input(mask_json, bbox)` rasterizes the polygons (or bbox fallback) onto a **256×256 square canvas** and maps the binary mask to **±8.0 logits** as a `(1, 256, 256)` float32 array (SAM2 square-resizes images with no aspect-preserving padding, so normalized coords map straight onto that square — see the docstring's frame-assumption note; a padding-based sam2 build would offset refined masks, fixable in isolation there). `predict.predict(point_coords, point_labels, mask_input=…, multimask_output=False)` under `torch.inference_mode()` returns the refined single mask. Returns `None` (leave the row unchanged) when there is no geometry to seed from or SAM2 yields an empty mask.
  - **Merge geometry** (`mask_utils.merge_detection_geometry`): union-bbox envelope of all rows; polygons concatenated (no boolean union — overlapping polygons render/rasterize fine, each fills independently); a bbox-only row contributes a rectangle polygon when any merged row has polygons; all-bbox merge → `(None, union_bbox)`.
- SAM3: `backend/ml/sam3_predictor.py`. **Safetensors-only loader** — `_resolve_checkpoint()` picks the newest `*.safetensors` in `settings.sam3_models_dir` (`models/sam3/`), never `.pt` pickles, never a gated HF download; a missing checkpoint raises with download instructions (mirror: `https://huggingface.co/1038lab/sam3`). The model is built weightless via `build_sam3_image_model(load_from_HF=False, enable_inst_interactivity=False)`, then `_rewrite_state_dict` maps the checkpoint on: it drops `tracker_model.`/`tracker_neck.` keys, strips the `detector_model.` wrapper, and — because the 1038lab mirror uses **HF-transformers Sam3 tensor naming**, not the native sam3-package naming — converts each native model key through the official `convert_sam3_to_hf.py` rename rules (embedded as `_NATIVE_TO_HF_RULES`), re-fusing split q/k/v projections into `qkv`/`in_proj` tensors, re-transposing the CLIP text projection, and zero-filling the stripped cls position-embedding row (the trunk discards that row at inference). Mapping stats are always logged; `load_state_dict(strict=False)` warns unless the only missing keys are expected ones (`freqs_cis` rope buffers rebuilt at build time; `geometry_encoder.points_*` projections absent from HF checkpoints and unused by text prompts). Inference (`predict_sync`) runs `Sam3Processor.set_image` once → `set_text_prompt` per comma-separated phrase (see "Comma-separated multi-phrase prompts" above) under **bf16 autocast** (sam3's fused ViT MLP kernel hard-casts to bfloat16) and reads each phrase's `state["masks"]`/`state["scores"]`; the processor's `confidence_threshold` is set to `sam3_threshold` per call. Off-CUDA quirks are patched at build/load time (`_non_cuda_build_patch`, `_disable_pin_memory_off_cuda`) — sam3 hardcodes `device="cuda"` in two compile warm-up caches and calls `pin_memory()` unconditionally. Output rows are shape-identical to SAM2 (`label` = the prompt text, mask-derived bbox, polygon JSON mask).

**Deferred work & upstream constraints (detection / SAM 3)** — the "why" behind the current SAM 3 scope, so it isn't rediscovered the hard way:

- **SAM 3.0, not 3.1.** 3.1's official checkpoint does not load through any public image-model code path ([facebookresearch/sam3#526](https://github.com/facebookresearch/sam3/issues/526), open) and its gains are video-tracking focused. The loader keeps the checkpoint swappable, so 3.1 becomes a drop-in once upstream fixes the loader (re-check that issue before attempting a 3.1 upgrade).
- **Checkpoint source.** The ungated [1038lab/sam3](https://huggingface.co/1038lab/sam3) mirror (`sam3.safetensors`) is the source; the gated `facebook/sam3` download (via `HF_TOKEN`) becomes an alternative if the pending HF access appeal is granted. Local file wins either way. Safetensors only — never `.pt` pickles.
- **SAM 3 points / mask-refine is blocked by the checkpoint.** The 1038lab mirror strips the geometry-encoder point-prompt weights (`sam3_predictor._is_expected_missing`), so a SAM 3 points-detection or refine mode has no trained weights to run — hence `build_sam3_image_model(enable_inst_interactivity=False)`. Points and refine stay SAM2-only (`sam2_predictor.refine_sync`) until a checkpoint ships those weights.
- **`triton` is a hard dependency of SAM 3, and it is Windows-specific pain.** sam3 imports triton unconditionally at module load (in its `sam3.model.edt` module, reached from the `sam3` package `__init__`), and genuinely *calls* triton kernels on CUDA: its `sam3.perflib.nms` and `sam3.perflib.connected_components` modules each lazily import them inside an `.is_cuda` branch, and `nms_masks` sits on the image inference path. torch's **Linux** wheels pull triton in transitively, so it is invisible in the dev container; torch's **Windows** wheels never ship it. `manage.ps1` therefore installs `triton-windows` alongside SAM3 (`Install-TritonWindows`), and `_load_sam3_sync` converts a triton `ModuleNotFoundError` into an actionable message. Note the trap: on a CPU-only torch every triton call site is skipped, so SAM3 appears to work without triton — that combination means the GPU is not being used at all. See `docs/dev/postmortems/PM-004-silent-cpu-torch-fallback.md`.
- **Do not filter sam3's "CUDA is not available ... Disabling autocast" warning.** It fires at sam3 import time exactly when `torch.cuda.is_available()` is False (`torch/cuda/amp/common.py::amp_definitely_not_available`), which makes it the clearest available signal that the venv has a CPU-only torch build. `backend/main.py` filters the neighbouring cosmetic timm `FutureWarning` and deliberately leaves this one alone.
- **Deferred features:** freehand brush mask editing (the polygon storage has no hole/ring support, so background-brush "cut a hole" edits can't be represented — would need an RLE/raster mask option); watermark crop-away and watermark removal via ComfyUI inpainting (mask-exclusion on export covers the training case); video segmentation/tracking (Crucible is image-only).

**Storage**: `backend/models/detection.py::Detection` table. Indexed on `image_id` and `label`. `Detection.mask: Text | None` stores polygon JSON: `{"polygons": [[[x,y],...], ...]}` in normalized 0–1 coordinates. `ImageOut.detections: list[DetectionOut]` is populated only in `GET /images/{image_id}` (the detail endpoint) — not in `list_images`.

**Frontend surfaces**:
- `SelectionToolbar` — "Detect" button opens a modal (model, task, prompt, min_prob slider for NudeNet, overwrite toggle). Model list includes `sam2` and `sam3`; for either, the task dropdown is hidden (fixed `text_prompt`), the use-captions toggle is hidden, and a prompt is required. A **"Sync watermark flag from results"** checkbox renders below Overwrite only when `detectSyncEligible` (sam2/sam3, or Florence grounding); the mutation sends `sync_watermark_flag: detectSyncEligible && detectSyncWatermark` so a stale checkbox state is harmless. **The modal closes immediately on job start** (toast "Detection queued") — progress shows in the global job bar, not in the modal — and the job id is appended to a tracked `detectJobIds: string[]` (the id-list pattern, see `docs/dev/frontend-core.md`) so the user can reopen and queue another run right away. No extra completion invalidation is needed — the detection-job effect already invalidates `["images", datasetId]` and StatsPage live-polls `"detection"` jobs.
- `CaptioningPage` — "Object Detection" section when a Florence-2 model is selected; uses the same model as captioning.
- `ImageDetailPage` — `DetectionsPanel` (`components/detection/DetectionsPanel.tsx`, extracted from the page): collapsible, label chips (visibility toggles filtering both boxes and mask fills via `hiddenLabels: Set<string>`) **plus a per-detection row list** (sorted label, id) — color dot, click-to-rename inline label input (`updateDetection`), score, model chip, Refine button (only when `det.mask`), merge checkbox, and a direct delete icon (no confirm — regenerable). A merge bar appears at ≥2 checked rows (`merge` in selection order → one `model="manual"`, `task="merge"` row). A "Crop from Detections" button shows at ≥1 visible detection. Panel-owned mutations invalidate `["image", imageId]` + `["detection-labels", datasetId]`.
  - **Manual box drawing**: toolbar "Draw Box" button (`enterMode("draw")`). A capture div (sibling of the SAM-points div, same normalized `getBoundingClientRect` math) turns mouse drag into a normalized rect finalized at ≥0.005 extent per axis; a toolbar strip then takes a label + optional "Refine with SAM" checkbox and calls `createManual`. Sync response refreshes and keeps draw mode on for multi-box annotation; `{job_id}` (SAM path) is tracked to completion. SAM empty/failure falls back to a plain `task="manual"` box so the drawing is never lost.
  - **Mask refine**: the panel's Refine button calls `enterMode("refine", det)`; a contextual strip ("Refining {label} — click fg / right-click bg") reuses `samPoints` for its clicks, Apply (disabled at 0 points) calls `refine`. While refining, the overlay dims to just the target detection at higher opacity.
  - `enterMode("draw"|"points"|"refine"|null)` enforces mutual exclusivity between the three annotation modes; entering crop/upscale/LUT or navigating to another image clears all three. Escape exits draw/refine/pending-box. SAM2 point-prompt mode ("SAM Points" toolbar button, when the detect model is sam2/points) and the detect modal (SAM 3 fixed `text_prompt`, no mode radios) are unchanged; SAM3 mask rows render through the same polygon overlay as SAM2. Like `SelectionToolbar`, the detect modal **closes on job start** (SAM points cleared then, since they were already sent in the payload) and tracks `detectJobIds: string[]` — no in-modal progress bar, run button never disabled by an active job, so a second run can be queued immediately.
  - **Crop prefill**: "Crop from Detections" (`utils/detectionCrop.ts::detectionCropPrefill`) computes the padded union of visible bboxes and seeds react-easy-crop via `initialCroppedAreaPixels` on fresh mount; the plain "Crop" button clears the prefill so it never leaks into a manual crop.
  - Detection jobs (`job_type="detection"`) invalidate detail pages via a dedicated TopBar completion branch (`["image"]` + `["detection-labels"|"detection-models", ds]`) — deliberately **not** in `IMAGE_MODIFYING_JOB_TYPES` (detections don't change the gallery).
- `BulkEditPage` — "Detections" tab: a **Run Detection** panel (`components/detection/DetectionRunForm.tsx` — inline, non-modal; runs `POST /detection/run` across the scope; offers every model — Florence-2 Large/PromptGen, NudeNet, SAM2, SAM3 — with SAM2 text-prompt only here since point prompts are per-image) above a **Delete Detections** panel (`components/detection/DetectionBulkDeleteForm.tsx`): label chips + model chips + "Score below" input, a live dry-run count (`bulkDelete({dry_run:true})`), and a danger `ConfirmDialog` before the real delete. See `docs/dev/export-and-bulk-ops.md`.

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

`sam3_threshold` (default 0.5) in `threshold_settings` is the SAM 3 instance confidence cutoff — read at the start of each SAM3 detection job and applied as `Sam3Processor.confidence_threshold` (plus a defensive per-instance filter in `predict_sync`).

All thresholds are user-configurable via Settings (`/settings` → `GET/PATCH /api/v1/settings/thresholds`). Quality flag thresholds take effect on the next scoring run; `gdino_threshold`/`sam3_threshold` take effect on the next SAM2/SAM3 detection run. Constants in `technical_scorer.py` serve only as parameter defaults — the quality router always passes DB-fetched values via `backend/services/threshold_service.py::get_thresholds()`.

**Duplicate detection** (`technical_scorer.find_duplicates_sync`) runs after technical scoring: it greedily groups images whose phash Hamming distance is `< duplicate_threshold` (first unassigned image is the group root and claims every *later* unassigned image within the threshold, members in input order; each image is claimed once). `find_duplicates_sync` is a **dispatcher over two exact, output-identical implementations** — only speed differs, never results:

- `_find_duplicates_indexed` (the path at scale): a pigeonhole multi-index chunk search, ~linear in N. Each hash is split into 4 chunks; any pair within distance d must agree on ≥1 chunk up to ⌊d/4⌋ bit flips, so probing 4 chunk tables with the chunk value XOR every ≤⌊d/4⌋-bit mask is guaranteed to surface every true neighbor as a candidate, which is then verified with the exact `dist < duplicate_threshold` popcount comparison. **Do not "simplify" the chunk count or radius derivation** — an undershoot silently drops duplicate pairs.
- `_find_duplicates_bruteforce`: the O(N²) vectorized all-pairs scan (numpy + module-level 256-entry `POPCNT` table). Semantically frozen — it is the reference implementation the golden tests compare against, and the fallback when `n < MIN_INDEX_N` (2048), the hash is shorter than 4 bytes, the threshold is so large the index would probe more than `CANDIDATE_FRACTION_CUTOFF` (0.25) of all rows per query (≥ ~21 for 64-bit hashes; practical thresholds are 4–12), or the total probe volume exceeds `n // PROBE_COST_DIVISOR` (8) — probes are pure-Python dict lookups, far costlier each than a vectorized scan row, so the index must be clearly cheaper before it engages (at 64-bit hashes, thresholds 13–20 need n ≳ 22k–80k).

Both paths are length-generic (no 64-bit assumption; the chunk-key fold must stay **unsigned** — a signed int64 fold wraps 8-byte-chunk keys negative and silently drops pairs whose probe crosses bit 63). The dispatcher and the golden tests both derive the chunk split and probe radius from the shared `_chunk_plan()` helper, so the tested plan cannot drift from the production one. `backend/tests/test_find_duplicates.py` pins the byte-identical-output property (groups, roots, member order) across sizes, thresholds (incl. floats — `threshold_settings.duplicate_threshold` is a Float column, though the quality router currently truncates it to `int` before calling, so algorithm-level float support is future-proofing), and hash lengths up to 256-bit; it runs in CI via `.github/workflows/backend-tests.yml` (cv2 is imported lazily inside `score_technical_sync`, so the tests and CI need no cv2 and no stub). The O(N²) path was the critical scaling wall found in `backend/scripts/scaling_bottlenecks_report.md` (~3.4 h projected at 1M images); re-verify with `python -m backend.scripts.bench_scaling --only dedup` after touching this code. The consumer `_flag_duplicates` (`routers/quality.py`) then loads the flagged images with a single chunked `select(...).where(Image.id.in_(...))` (≤10k ids per chunk) rather than per-row `session.get`, and follows the copy-then-reassign `quality_flags` invariant.

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

**All-layers scoring is vectorized and RAM-bounded** (`dino_all_layers` / `combined_all_layers`): rather than re-slicing every reference blob per candidate per layer (the old `slice_layer_embedding` inner loops), the per-layer normalized mean reference is computed once via `_mean_layer_refs()` (stack refs → mean per layer → L2-normalize), and each candidate blob is decoded whole with `_decode_dino_layers()` (`np.frombuffer(...).reshape(12, 768)`); scoring is one matmul per layer (`cand[:, l, :] @ mean_refs[l]`), matching `compute_style_similarity`'s normalize-then-dot exactly. In combined mode the CLIP score doesn't depend on the layer, so it's computed once per chunk. Candidates are **keyset-paginated** (`WHERE Image.id > last ORDER BY Image.id LIMIT 2000`) through the shared local `_score_all_layers_paginated()` helper so the ~18 KB per-layer blobs are never all resident at once (~1.8 GB at 100k images). Output shape and rounding are byte-identical to the pre-vectorization loop (combined rounds the blended score to 4 decimals; both round each per-layer cosine to 4).

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
