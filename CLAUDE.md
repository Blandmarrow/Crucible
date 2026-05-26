# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Launch

**Windows** — double-click `Crucible.bat` in Explorer (shows a setup/start/update menu), or run `manage.ps1` directly in PowerShell:

| File | Purpose |
|---|---|
| `Crucible.bat` | Double-click to launch — shows menu (setup / start / update) |
| `manage.ps1 setup` | First time only — creates venv, installs deps, builds frontend |
| `manage.ps1 start` | Production: runs migrations, rebuilds frontend if needed, serves on :8000 |
| `manage.ps1 update` | `git pull` → update pip deps → `npm install` → rebuild frontend |
| `manage.ps1 dev` | Dev mode: backend on :8000 (hot reload) + Vite frontend on :5173 |

**Linux / macOS** — run `manage.sh` (make it executable once with `chmod +x manage.sh`):

| Command | Purpose |
|---|---|
| `./manage.sh setup` | First time only — creates venv, installs deps, builds frontend |
| `./manage.sh start` | Production: runs migrations, rebuilds frontend if needed, serves on :8000 |
| `./manage.sh update` | `git pull` → update pip deps → `npm install` → rebuild frontend |
| `./manage.sh dev` | Dev mode: backend on :8000 (hot reload) + Vite frontend on :5173 |

To shut down the running server, click the power icon button in the top-right of the TopBar (confirms before shutting down), or press Ctrl+C in the terminal.

### Backend (always run from `backend/` with venv active)

```powershell
cd backend
..\venv\Scripts\Activate.ps1
alembic upgrade head                     # apply migrations
alembic revision --autogenerate -m "msg" # generate new migration
```

### Frontend

```powershell
cd frontend
npm run dev      # Vite dev server on :5173 (proxies /api to :8000)
npm run build    # TypeScript check + Vite production build → frontend/dist/
npm run lint     # ESLint
```

## Architecture

### Data flow

HTTP request → FastAPI router → service layer (pure business logic, no HTTP) → SQLAlchemy async session → SQLite.

Long-running operations (captioning, quality scoring, import, export, batch ops) are queued: the router creates a `BackgroundJob` DB record, enqueues a coroutine in `workers/job_queue.py`, and immediately returns `{job_id}`. The worker runs the job, emits SSE progress events via `workers/progress.py`, and updates the job row when done. The frontend subscribes to `GET /api/v1/jobs/stream/{job_id}` (or `/stream/all/events` for the global progress bar).

### Shared utilities

`backend/utils.py` — thin module for helpers shared across multiple routers. Currently contains:

- `normalize_subfolder(s: str) -> str` — strips leading/trailing slashes and `.` segments, rejects `..` with HTTP 400. Import this; never copy the logic inline or re-import it from a router.
- `slugify_filename(name: str) -> str` — lowercases, removes non-word characters, collapses whitespace/underscores/hyphens to `_`, strips leading/trailing `_-`, truncates to 200 chars. Returns `"image"` if the result is empty.
- `unique_filename(directory: Path, stem: str, suffix: str, db_names: set) -> str` — returns a filename not on disk and not in `db_names`. Tries `{stem}{suffix}` first, then `{stem}_001{suffix}`, `_002`, … Both checks are required: `db_names` covers in-flight batch collisions within the same request; the filesystem check covers files that exist but have no DB record.
- `rename_with_sidecar(old_path: Path, new_path: Path) -> None` — renames a file and its `.txt` sidecar (if present) in one call. Use this everywhere a file rename happens; never copy the two-step pattern inline.
- `copy_with_sidecar(old_path: Path, new_path: Path) -> None` — copies a file and its `.txt` sidecar (if present) using `shutil.copy2`. Use this in any copy path; mirrors `rename_with_sidecar` but leaves the source intact.
- `ALLOWED_FLAG_KEYS: frozenset` — the canonical set of valid quality flag names (`is_blurry`, `is_noisy`, `is_uniform`, `has_watermark`, `is_duplicate`). Import this wherever flag names must be validated or used in SQL filters; never redefine the set locally.
- `normalize_image_format(suffix: str, out_path: str) -> tuple[str, str]` — normalises a file suffix to a PIL format name (`JPG`→`JPEG`, unsupported→`PNG`). Returns `(fmt, out_path)` — `out_path` may be updated when the format falls back to PNG (extension changes). Use in any image-save path; do not inline the JPG/PNG fallback logic again.
- `image_save_kwargs(fmt: str) -> dict` — returns PIL `save()` kwargs for the given format (JPEG → `{quality: 95, subsampling: 0}`; others → `{}`). Use alongside `normalize_image_format`.
- `thumbnail_path_for(image_path: Path | str) -> str` — derives the `.webp` thumbnail path for an image sitting in a dataset `images/` folder (`parent.parent/thumbnails/{stem}.webp`). Use in any router that creates or regenerates thumbnails; never reconstruct the path manually.

### Image file naming

Images receive human-readable names derived from their original filename via `slugify_filename`. Collision handling is done by `unique_filename` (counter suffix `_001`, `_002`, …). This applies at upload time, at import time, and when the captioning rename option is used.

`Image.is_auto_named: bool` (default `False`) — set to `True` when a file is renamed by the captioning job or by a subfolder move. Used to distinguish auto-named files from manually named ones. `PATCH /images/{image_id}/rename` sets it back to `False`.

**Subfolder-based naming**: when `rename_on_caption=True` or when images are moved between subfolders (`POST /images/batch/move-subfolder`) or between datasets (`POST /images/batch/move-dataset` / `POST /images/batch/copy-dataset`), filenames are derived from the target subfolder slug (e.g. images in `"animals"` become `animals.jpg`, `animals_001.jpg`, …; images in root become `image.jpg`, `image_001.jpg`, …). All moves and copies rename every image in the batch unconditionally.

**Cross-dataset moves** (`POST /images/batch/move-dataset`): accepts either `image_ids` (explicit list) or `source_dataset_id + source_subfolder` (moves the whole subfolder). Files are renamed to the target subfolder slug, moved to `{target_dataset.folder_path}/images/`, thumbnails are copied then the originals removed. Calls `refresh_stats` on both source and target after commit.

**Cross-dataset copies** (`POST /images/batch/copy-dataset`): same request schema as move. Source images and files remain untouched; new `Image` records are inserted in the target dataset with all metadata copied (scores, captions, flags, generation metadata — deferred blob embeddings are not copied). Uses `copy_with_sidecar` for the image file and `shutil.copy2` for the thumbnail. Calls `refresh_stats` on the target only. DB inserts are staged before file copies so that a filesystem failure prevents commit and leaves no orphaned DB records.

Frontend entry points for both: `SelectionToolbar` ("Move to Dataset" / "Copy to Dataset" buttons) and `GalleryPage` subfolder sidebar (arrow icon for move, copy icon for copy). Shared modal: `MoveToDatasetModal` (`frontend/src/components/common/MoveToDatasetModal.tsx`) — accepts an optional `mode?: "move" | "copy"` prop (default `"move"`) which changes the icon, title, and confirm button label. Callback prop is `onConfirm(targetDatasetId, subfolder)`.

### Key invariants

- **tags_json is the source of truth.** `image.tags_json` (JSON column) is always kept in sync with the `tags` table via `_sync_tags()` in `caption_service.py` — both written in the same transaction. Never write the `tags` table directly.
- **Always `ImageOps.exif_transpose()` first.** Every Pillow operation in `image_service.py` calls this before anything else to correct orientation from EXIF data.
- **Close PIL Images after preprocessing.** In all ML inference paths (`aesthetic_scorer.py`, `dino_scorer.py`, all captioners) call `img.close()` immediately after the image has been passed to the model's preprocessor/processor — before the GPU inference runs. In `export_service.py::_write_image()` use a `try/finally` block. This frees the decoded pixel buffer (potentially several MB per image) during slow inference and prevents accumulation across large batches.
- **Absolute DB path.** `config.py` derives the database URL from `Path(__file__).parent.parent` so it resolves correctly regardless of the working directory when uvicorn is launched.
- **Path traversal guard.** `_safe_path()` in `routers/images.py` validates that resolved file paths stay within `settings.datasets_dir`.

### ML model management

`ml/model_manager.py` is a singleton that tracks loaded models and their VRAM usage. Before loading a model it calls `_evict_lru(needed_mb)` to free space. Each model_id gets its own `asyncio.Lock` to serialize inference. All inference runs in `loop.run_in_executor(None, _sync_fn)` to avoid blocking the event loop. Ollama models are not tracked — Ollama manages its own VRAM.

After a successful load, `_registry[model_id]["vram_mb"]` is updated with the measured GPU delta so eviction decisions reflect reality.

**Explicit unload methods** — two public async methods allow callers to free VRAM on demand:

| Method | Effect |
|---|---|
| `await model_manager.unload(model_id)` | Unloads one model by ID, acquires its per-model lock, moves weights to CPU, deletes both the model and processor objects, removes the entry, and calls `torch.cuda.empty_cache()`. |
| `await model_manager.evict_all()` | Calls `unload()` for every currently registered model and does a final `cuda.empty_cache()`. Returns the list of model IDs that were unloaded. Used by `POST /api/v1/models/unload-all`. |

**`POST /api/v1/models/unload-all`** (router: `backend/routers/models.py`, prefix `/models`, registered in `main.py`) — evicts all ML models from VRAM without restarting the server. Returns `{ "status": "ok", "unloaded": [model_id, ...] }`. Intended to be called after quality scoring completes so that scoring models (aesthetic, CLIP, DINOv2) do not occupy VRAM when they are no longer needed.

Model IDs and their captioner/scorer modules:
| Prefix | Module |
|---|---|
| `florence2*` | `ml/florence_captioner.py` |
| `paligemma2` | `ml/paligemma_captioner.py` (needs `HF_TOKEN` in `.env`; accept license at huggingface.co/google/paligemma2-3b-pt-448) |

`HF_TOKEN` from `.env` is injected into `os.environ` early in `main.py` so all `hf_hub_download` calls pick it up automatically.
| `ollama:*` | `ml/ollama_captioner.py` (HTTP calls to localhost:11434) |
| `upscale:{abs_path}` | `ml/upscaler.py` (spandrel; keyed by absolute model file path to support multiple loaded upscalers) |

**Target resolution preprocessing**: `CaptionJobRequest` accepts optional `target_width` / `target_height`. When set, `ml/image_utils.py::preprocess_for_caption()` center-crops each image to the target aspect ratio and resizes it to the exact target resolution before inference. This ensures captions describe the composition the model will actually see at training time. All three captioners (Florence-2, PaliGemma-2, Ollama) call this utility; Ollama's existing `max_px` scale-down runs afterward on the already-cropped image. Omitting both fields leaves behavior unchanged.
| `aesthetic` | `ml/aesthetic_scorer.py` (auto-downloads weights from `camenduru/improved-aesthetic-predictor` via `hf_hub_download`; also used for CLIP zero-shot watermark detection and CLIP embedding extraction) |
| `dino` | `ml/dino_scorer.py` (`facebook/dinov2-base` via HuggingFace `transformers`; ~1.2 GB VRAM; used for DINOv2 embedding extraction) |

Quality scorers and what they add to `Image`:
| Module | Columns written | Notes |
|---|---|---|
| `ml/technical_scorer.py` | `blur_score`, `noise_score`, `uniformity_score`, `color_score`, `saturation_score`; flags `is_blurry`, `is_noisy`, `is_uniform` | Pure OpenCV/numpy, no GPU |
| `ml/aesthetic_scorer.py` | `aesthetic_score` (1–10), `watermark_score` (0–1), flag `has_watermark`, `clip_embedding` (BLOB, float16) | CLIP ViT-L-14; text encoder used for zero-shot watermark; image encoder for embeddings |
| `ml/dino_scorer.py` | `dino_embedding` (BLOB, float16), `dino_layer_embeddings` (BLOB, float16) | `dino_embedding`: final-layer CLS token, 768-dim. `dino_layer_embeddings`: all 12 transformer-layer CLS tokens concatenated, 18 432 bytes (12 × 768 × float16); layer N (1-indexed) at offset `(N-1)*768*2`. `slice_layer_embedding(blob, layer)` extracts one layer's bytes. |
| `ml/similarity_scorer.py` | — | CPU-only. `compute_style_similarity(ref_bytes, cand_bytes)` — cosine similarity of candidates to mean reference. `compute_combined_similarity(ref_clip, cand_clip, ref_dino, cand_dino, clip_w=0.38, dino_w=0.62)` — weighted blend of CLIP and DINOv2 cosine similarities. |

### Object detection

Bounding-box detection runs as a background job, same pattern as quality scoring.

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
| `model` | required | `"florence2_large"` or `"florence2_promptgen"` |
| `task` | required | `"<OD>"` or `"<CAPTION_TO_PHRASE_GROUNDING>"` |
| `custom_prompt` | `""` | Phrase to ground (only used for `<CAPTION_TO_PHRASE_GROUNDING>`) |
| `use_caption_as_prompt` | `false` | When `true`, each image's `caption_text` is used as the per-image prompt; images without a caption are skipped |
| `overwrite` | `true` | Delete existing detections for each image before inserting new ones |

**Tasks**:
- `<OD>` — fixed-vocabulary object detection; no prompt; detects only categories the model was trained on.
- `<CAPTION_TO_PHRASE_GROUNDING>` — phrase grounding; draws boxes around noun phrases that appear in the supplied text. More useful for dataset curation because you control what gets detected.

**ML inference**: `backend/ml/florence_captioner.py::infer_sync_detection` / `detect_image`. Returns normalized bboxes `[x1, y1, x2, y2]` in 0–1 range. `_move_inputs_to_cuda(model, inputs)` is a shared helper used by both `infer_sync` (captioning) and `infer_sync_detection`.

**Storage**: `backend/models/detection.py::Detection` table. Indexed on `image_id` and `label`. `ImageOut.detections: list[DetectionOut]` is populated only in `GET /images/{image_id}` (the detail endpoint) — not in `list_images`.

**Frontend surfaces**:
- `SelectionToolbar` — "Detect" button opens a modal (model, task, prompt, overwrite toggle).
- `CaptioningPage` — "Object Detection" section shown when a Florence-2 model is selected; uses the same model as captioning.
- `ImageDetailPage` — DETECTIONS panel in the right column (collapsible, shows label chips with counts); SVG overlay on the image with per-label color coding. Label chips are toggle buttons — clicking a chip adds/removes that label from a `hiddenLabels: Set<string>` that filters the SVG overlay. State resets on image navigation. Eye icon in the toolbar shows/hides all boxes at once.

Flag thresholds:
| Flag | Column | Default threshold | Source |
|---|---|---|---|
| `is_blurry` | `blur_score` (Laplacian variance) | < 100 | `blur_threshold` in `threshold_settings` DB table |
| `is_noisy` | `noise_score` (smooth-region std dev) | > 15 | `noise_threshold` in `threshold_settings` DB table |
| `is_uniform` | `uniformity_score` (grayscale std dev) | < 12 | `uniformity_threshold` in `threshold_settings` DB table |
| `has_watermark` | `watermark_score` (CLIP zero-shot, 0–1) | ≥ 0.6 | `watermark_threshold` in `threshold_settings` DB table |
| `is_duplicate` | `phash` (perceptual hash Hamming distance) | < 8 | `duplicate_threshold` in `threshold_settings` DB table |

All five thresholds are user-configurable via the Settings page (`/settings` → `GET/PATCH /api/v1/settings/thresholds`). Changes take effect on the next scoring run; existing scored images are not re-flagged. The constants in `technical_scorer.py` (`BLUR_THRESHOLD`, `NOISE_THRESHOLD`, `UNIFORMITY_THRESHOLD`, `DUPLICATE_THRESHOLD`) serve only as parameter defaults — the quality router always passes the DB-fetched values at runtime via `backend/services/threshold_service.py::get_thresholds()`.


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

Local reference files can be embedded on-the-fly via `POST /quality/embed-references` (multipart upload → returns base64 CLIP embeddings). External refs are CLIP-only; `"combined"`, `"dino"`, and `"dino_all_layers"` / `"combined_all_layers"` modes require dataset images as references. No job queue — all similarity computation is CPU-only numpy and runs synchronously in the request.

**Config validation** (`backend/config.py`): A `@model_validator(mode="after")` in the Pydantic `Settings` class enforces three rules at startup: (1) `max_vram_mb < 1000` raises `ValueError` with a clear message so misconfigured deployments fail fast; (2) an empty `hf_token` logs a debug-level warning (not a hard error, since most users don't use PaliGemma); (3) any key found in the `.env` file that is not a recognised settings field is logged at WARNING level — `extra="ignore"` is retained so OS environment variables are never flagged. Note: `config.py` still declares a `watermark_threshold` field for legacy `.env` compatibility but the quality router no longer reads it — all five flag thresholds are now read from the `threshold_settings` DB table (see Settings page below).

**TorchDynamo is disabled** (`TORCHDYNAMO_DISABLE=1` set in `main.py`). Triton is unavailable on Windows and single-image inference gains nothing from `torch.compile`, so it is disabled for the entire process. Do not remove this without re-testing all ML inference paths on Windows.

**Venv ML packages**: torch, transformers, open_clip, etc. are installed in the system Python (`C:\Users\Tom\AppData\Local\Programs\Python\Python310`) and exposed to the venv via `venv/lib/site-packages/system_ml_packages.pth`. The venv was created with `--system-site-packages` and `huggingface-hub` is pinned to `>=0.30,<1.0` in the venv to stay compatible with those system packages.

### Upscaling

ML-based image upscaling via the `spandrel` library, which auto-detects architecture from `.pth`/`.safetensors` files (RealESRGAN/RRDB, SwinIR, HAT, OmniSR, and more).

**Router**: `backend/routers/upscaling.py`, prefix `/upscaling`.

| Endpoint | Body / params | Returns |
|---|---|---|
| `GET /upscaling/models` | — | `list[UpscaleModelInfo]` — scans `settings.upscale_models_dir` |
| `POST /upscaling/run` | `UpscaleRunRequest` | `{ job_id, total }` |

**Config**: `settings.upscale_models_dir` (default `models/upscale_models/`). Override with `UPSCALE_MODELS_DIR=` in `.env` (e.g. pointing at a ComfyUI models folder). The directory is created automatically on startup.

**`UpscaleRunRequest`** fields: `dataset_id`, `image_ids` (null = whole dataset), `model_path`, `replace` (overwrite source vs. new file), `target_width`/`target_height` (optional: upscale then resize down to fit, maintaining AR).

**ML inference** (`backend/ml/upscaler.py`):
- `scan_upscale_models(dir)` — globs `*.pth`/`*.safetensors`, detects scale from filename heuristics (`4x-`, `_x4`, `_X4`, etc.), returns `[{name, path, scale}]` without loading weights.
- `upscale_image_sync(src, dest, model_path, replace, target_w, target_h)` — loads via `spandrel.ModelLoader().load_from_file()`, tiles if either dimension > 1024 px (512 px tiles, 64 px overlap, linear-ramp seam blending), optional LANCZOS resize post-upscale.
- Model caching uses `model_manager._registry` under ID `upscale:{abs_path}`; `_ensure_upscaler_loaded` includes a double-check after re-acquiring `_sync_lock` to prevent the TOCTOU double-load race.

**Output modes**: *New file* — filename `{stem}_up{N}x{ext}` (collision-handled via `unique_filename`), new `Image` record created, thumbnail regenerated. *Replace* — updates `width`/`height`/`file_size_bytes`/`updated_at`/`processing_history` on existing record, thumbnail regenerated.

**History management**: Non-replace upscale navigation uses `{ replace: true }` so the source image's history entry is overwritten rather than stacked. This ensures that deleting the upscaled image (which navigates to the adjacent image with another `replace: true`) leaves a single clean history entry, so one Back press returns to the gallery. Do not remove the `replace: true` from these `paneGo` calls without considering the double-Back regression.

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

**`LutRunRequest`** fields: `dataset_id`, `image_ids` (null = whole dataset), `lut_path`, `intensity` (0.0–1.0, clamped by validator), `replace` (overwrite source vs. new file).

**ML processing** (`backend/ml/lut_processor.py`):
- `scan_lut_models(dir)` — globs `*.cube`/`*.3dl`, returns `[{name, path, format}]`.
- `apply_lut_sync(src, dest, lut_path, intensity, replace)` — loads PIL image + `exif_transpose`, converts to float32 [0,1], applies trilinear LUT interpolation, blends `original * (1-intensity) + graded * intensity`, saves. Returns `{width, height, file_size_bytes, format, out_path}`. Note `out_path` may differ from `dest` when the source format is unsupported and falls back to PNG — the router uses `info["out_path"]` to derive the actual output path.
- Module-level `_lut_cache: dict[str, np.ndarray]` — parsed LUT arrays are cached for the process lifetime. LUTs are tiny (<1 MB each); no eviction needed in practice.

**LUT axis-ordering invariant**: The `.cube` spec (and `.3dl`) stores data with **R varying fastest, B slowest**. After `reshape(N, N, N, 3)` numpy's axis order is `[B, G, R]`. Both `_parse_cube` and `_parse_3dl` therefore call `.transpose(2, 1, 0, 3)` to produce a `[R, G, B]`-indexed array, so that `lut[r, g, b]` is the natural lookup in `_apply_lut_array`. Do not remove this transpose — without it R and B are swapped in the lookup, producing visually wrong results.

**Output modes**: *New file* — filename `{stem}_lut{ext}` (collision-handled via `unique_filename`), new `Image` record created, thumbnail regenerated. *Replace* — updates `file_size_bytes`/`updated_at`/`processing_history` on existing record, thumbnail regenerated.

**Frontend surfaces**:
- `ImageDetailPage` — "LUT" toolbar button (mutually exclusive with Crop and Upscale) toggles inline controls: LUT `<select>`, intensity slider, Replace checkbox, Run button. Non-replace completion calls `injectNavId` + `paneGo` to navigate to the new image (same pattern as upscaling).
- `SelectionToolbar` — "LUT" button opens a modal with `<LutForm>`.
- `BulkEditPage` — "Apply LUT" tab (see Bulk caption editing section).

`LutForm` (`frontend/src/components/lut/LutForm.tsx`) — reusable form used by `SelectionToolbar` and `BulkEditPage`. Queries `["lut-models"]` with `staleTime: Infinity`. On job completion invalidates `["images", datasetId]` and calls `onSuccess?.()`.

### Database

SQLite in WAL mode (`synchronous=NORMAL`). ORM models live in `backend/models/`. Alembic migrations in `backend/alembic/versions/`. The Alembic `env.py` strips `+aiosqlite` from the URL when running synchronous migrations.

**Subfolders** (`Image.subfolder`, `Dataset.declared_subfolders`): images are physically flat in `{dataset.folder_path}/images/`; `subfolder` is pure string metadata (empty string = root/ungrouped; nested paths use `/`). `Dataset.declared_subfolders` (JSON column, default `[]`) stores explicitly-created subfolder paths so empty subfolders survive a `list_subfolders()` call — that function GROUP BYs images, so any path not in `declared_subfolders` with zero images would disappear. `declare_subfolder()` adds a path; `delete_subfolder()` bulk-moves images to root and removes the declared entry. `normalize_subfolder()` in `backend/utils.py` sanitizes path strings before storage. API: `GET/POST/DELETE /datasets/{id}/subfolders`.

Three performance indexes exist:
- `ix_images_dataset_created_at` on `(dataset_id, created_at)` — gallery page loads sorted by date
- `ix_images_file_path` on `file_path` — filesystem move/rename/delete lookups
- `ix_images_dataset_caption` on `(dataset_id, caption_text)` — caption filter + listing

**Deferred blob columns**: `clip_embedding`, `dino_embedding`, and `dino_layer_embeddings` are declared with `deferred=True` on their `mapped_column`. SQLAlchemy omits them from `SELECT *` queries — they are only fetched when explicitly accessed or when `undefer()` is passed as a load option. The `GET /images/{image_id}` endpoint undefers `dino_layer_embeddings` so the `has_dino_layer_embeddings` property on `ImageOut` works. Quality/similarity routers use column-explicit selects (`select(Image.id, Image.clip_embedding, ...)`) and are unaffected. Never access these columns from a full-row ORM load without adding `undefer()`.

### SSE progress

`ProgressBroadcaster` (singleton in `workers/progress.py`) maintains per-job `asyncio.Queue`s. Emitting a progress event pushes to the job-specific channel and the `"all"` channel. A 25-second heartbeat comment keeps proxies from closing idle connections. Streams close when status becomes `completed`, `failed`, or `cancelled`.


### Frontend state

- **TanStack Query** — all server state (datasets, images, captions, jobs). Query keys follow `["resource", id]` pattern.
- **Zustand stores** — `datasetStore` (active dataset), `selectionStore` (Set of selected image IDs + `datasetByImageId: Map<string, string>` tracking which dataset each selected image belongs to), `jobStore` (Map of active job progress from SSE), `promptPresetsStore` (saved AI prompt presets, persisted to localStorage), `paneStore` (split-view pane layout — see Split view pane manager section). `selectionStore.toggle(id, datasetId)` and `selectionStore.selectAll(ids, datasetId)` both require a `datasetId` argument — all callsites (ImageCard, GalleryPage, ImageDetailPage) must pass it.
- **`useJobSSE(jobId)`** — opens `EventSource` for one job, writes progress to `jobStore`.
- **`useAllJobsSSE()`** — opened at app root in `TopBar`, drives the global progress bar.
- **Job completion → cache invalidation**: pages that trigger background jobs (`QualityPage`, `SelectionToolbar`, `ImageDetailPage`) watch their job ID in `jobStore` via `useEffect` and call `qc.invalidateQueries` when status becomes `"completed"`. Always follow this pattern when adding new job-triggering UI.
- **Per-image cache invalidation (captioning)**: Caption SSE events carry `image_id`; `CaptioningPage` invalidates `["images", datasetId]` on every `done` increment so the gallery updates in real-time.
- **SelectionToolbar score modal**: the "Run Scoring" action accepts four boolean toggles — `run_technical`, `run_aesthetic`, `run_watermark` (CLIP zero-shot watermark detection), and `run_embeddings` (CLIP + DINOv2 embedding extraction for style similarity). `run_watermark` and `run_embeddings` default to `false` since they add significant VRAM/time overhead.
- **SelectionToolbar dataset breakdown**: because selections persist across dataset navigation, the toolbar pill and every action modal header show badge chips for each dataset represented in the current selection — solid style for the current dataset, amber `badge-warn` for images from other datasets. Computed via `useMemo` from `selectionStore.datasetByImageId` + the cached `["datasets"]` query (already fetched, `staleTime: 30_000`). `MoveToDatasetModal` receives this as `sourceInfo?: ReactNode`.
- **QualityPage subfolder scope**: `POST /quality/score` (`ScoreRequest`) accepts `subfolder: str | None`. When set and `image_ids` is absent, the backend filters `Image.subfolder == normalize_subfolder(subfolder)` so only images in that subfolder are scored. `QualityPage` exposes a subfolder `<select>` in the "Run quality analysis" panel header (shown only when subfolders exist, uses the shared `["subfolders", datasetId]` query). `image_ids` (SelectionToolbar path) takes precedence over `subfolder` when both are provided.

**Thumbnail cache-busting**: `imagesApi.thumbnailUrlVersioned(id, updatedAt)` appends `?v={timestamp}` derived from `image.updated_at` (present on both `ImageListItem` and `ImageOut`). Use this helper — not the raw `thumbnailUrl` — wherever a thumbnail could change due to crop or resize. It is already used in `ImageCard`, `QualityPage`, `StatsPage`, and `StyleReferencePicker`; any new thumbnail `<img>` in those contexts should follow the same pattern.

**Full-image cache-busting**: `imagesApi.fileUrlVersioned(id, updatedAt)` works the same way for the full-resolution image URL. Use it — not the raw `fileUrl` — in any `<img>` or Cropper source that displays the full image in `ImageDetailPage`, so that in-place replacements (replace-crop, replace-upscale) are not served stale from the browser cache.

### Frontend constants

`frontend/src/constants/captionStyles.ts` — `STYLE_LABELS: Record<string, string[]>` (style names per model type — Florence-2 and PaliGemma only; Ollama has no entry so the style picker is hidden for it) and `modelType(model: string): string | null` (maps a model ID to its type key). Shared by `CaptioningPage`, `ImageDetailPage`, and `SelectionToolbar`; do not redeclare locally.

`frontend/src/constants/dinoLabels.ts` — `DINO_LAYER_LABELS: Record<string, string>` mapping layer number (1–12) to a human-readable description. Shared by `ImageDetailPage` and any future UI that shows per-layer DINOv2 scores.

`frontend/src/constants/flags.ts` — `FLAG_OPTIONS: readonly [{key, label}]` mapping each quality flag key to its display label. `FlagKey` is the derived union type. Shared by `ExportPage`, `BulkEditForm`, and `BulkEditPage`; do not redeclare locally.

`frontend/src/constants/storage.ts` — `CONFIRM_DEFAULT_KEY`: the `localStorage` key for the user's delete-confirmation default-button preference (`"cancel"` or `"confirm"`). Imported by both `ConfirmDialog` (reads on mount) and `SettingsPage` (reads/writes on toggle). `BRANCH_SNAPSHOT_KEY`: the `localStorage` key for the branch/checkout snapshot behavior preference (`"ask"` or `"auto"`). Read by `BranchSelector` before checkout and branch creation; written by `SettingsPage`. `VERSIONS_BRANCH_KEY`: the `sessionStorage` key prefix (`"versions-branch"`) for the user's last-browsed branch on `VersionsPage`; append `-${datasetId}` for the full key. Written by `VersionsPage.handleBranchSelect` and by `SidebarVersionPanel`'s `onSelect` after checkout; read by `VersionsPage` on mount. Add new storage keys here rather than defining them inline in components.

### Layout

**Sidebar** uses `useMatch("/datasets/:datasetId/*")` (not `useParams`) to detect the active dataset, because the Sidebar renders outside the `<Routes>` tree and `useParams` would always return `{}` there.

### Gallery generation metadata

`generation_metadata` is included in both `ImageOut` and `ImageListItem` backend schemas, so it comes back with the gallery list response. `ImageCard` shows a small accent `<Cpu>` icon button in the filename row when `image.generation_metadata` is set; clicking it (without navigating) opens a page-level modal in `GalleryPage` that renders `<GenerationMetadata>`. The same component appears in the right panel of `ImageDetailPage`, expanded by default.

### Gallery subfolder sidebar

`GalleryPage` shows a left-hand subfolder sidebar (180 px fixed) when any subfolder exists or when the create form is open. Items: "All" (no filter), "(root)" (empty-string subfolder, only shown if images exist there), and one button per named subfolder with its image count. Active item is highlighted with `var(--surface-3)`.

- **Create**: `+` icon in the sidebar header opens an inline form (input + Enter/Escape handling). If no subfolders exist yet, a `+ Subfolder` button appears in the main toolbar instead to surface the sidebar. On confirm, calls `datasetsApi.createSubfolder` → `POST /datasets/{id}/subfolders`, sets active subfolder to the new path.
- **Delete**: hover-revealed `×` button on each row opens a `ConfirmDialog`. If the subfolder has images, the dialog warns they will be moved to root (not deleted). On confirm, calls `datasetsApi.deleteSubfolder` → `DELETE /datasets/{id}/subfolders?path=...`. If the deleted subfolder was active, resets to "All".
- **Move to dataset**: hover-revealed arrow icon button on each row opens `MoveToDatasetModal` (shared with `SelectionToolbar`). On confirm, calls `POST /images/batch/move-dataset` with `source_dataset_id + source_subfolder`. If the moved subfolder was active, resets to "All". Invalidates `["images"]` and `["subfolders"]` for both source and target datasets.
- **Copy to dataset**: hover-revealed copy icon button on each row opens `MoveToDatasetModal` with `mode="copy"`. On confirm, calls `POST /images/batch/copy-dataset`. Source subfolder stays intact. Invalidates `["images"]` and `["subfolders"]` for target dataset only.
- **Upload subfolder**: a `<select>` next to the Upload button lets users target a specific subfolder for drag-drop or file-picker uploads. Defaults to the active subfolder; can be overridden independently.
- **Query key**: `["subfolders", datasetId]` — invalidated after upload, batch delete, batch move, create, and delete.
- **CSS**: `.subfolder-row .subfolder-delete-btn`, `.subfolder-row .subfolder-move-btn`, and `.subfolder-row .subfolder-copy-btn` are `opacity: 0`; hover on the row reveals them. Defined in `frontend/src/index.css`. Move and copy buttons share base layout via `.subfolder-action-btn`; each has its own hover color (accent for move, info for copy). Delete uses inline styles (pre-existing pattern).

### Gallery filters

`GalleryPage` supports the following filter controls:

- **Search bar** — debounced 350 ms; passes `search` param to `GET /images/`; filters by filename OR caption text (case-insensitive).
- **Caption filter** — All / Captioned / Uncaptioned.
- **Quality flag** — dropdown with options: None, Blurry (`is_blurry`), Noisy (`is_noisy`), Near-uniform (`is_uniform`), Watermarked (`has_watermark`), Duplicate (`is_duplicate`). All values map directly to `quality_flag` param.
- **Score filters** — multi-chip system: each active filter is a `{field, min?, max?}` chip with a × remove button. An "Add score filter" form lets the user pick any of the 8 score fields and enter optional min/max bounds. Multiple chips are combined as AND conditions via the JSON-encoded `score_filters` param. The older single `score_field`/`min_score`/`max_score` params are not used by GalleryPage (retained only for StatsPage BucketPanel backward compat).
- **Detection label** — text input with icon prefix, debounced 350 ms; passes `detection_label` to `GET /images/`; uses a correlated `EXISTS` subquery against the `detections` table matching `label ILIKE '%...%'`; has a clear (×) button when set.
- **Subfolder filter** — see Gallery subfolder sidebar section above; passes `subfolder` query param to `GET /images/`.

### Gallery navigation state

`GalleryPage` persists two keys to `sessionStorage` (keyed by `datasetId`):

| Key | Contents | Purpose |
|---|---|---|
| `gallery-state-${datasetId}` | `{ page, sortIdx, captionedFilter, scrollTop }` | Restores page/sort/filter/scroll when returning from detail view |
| `gallery-nav-${datasetId}` | `{ ids, page, sort, order, captionedFilter }` | Ordered image ID list + query context for prev/next navigation in the detail view |

`ImageDetailPage` reads `gallery-nav-*` to support arrow-key navigation. When the user reaches the boundary of the current page it pre-fetches the adjacent page (`useQuery`, `enabled: atEnd / atStart`) and on crossing writes the new page's context back to `gallery-nav-*` and updates `gallery-state-*` so that **Back** returns to the correct gallery page. Arrow keys are suppressed when an `<input>`, `<textarea>`, or `<select>` has focus, or when `isContentEditable` is true. The `Delete` key opens a confirm dialog when images are selected in the gallery (`SelectionToolbar`) or when viewing an image in `ImageDetailPage`; both handlers share the same focus guard. The arrow-key handler in `ImageDetailPage` is additionally suppressed while the delete confirm dialog is open (`showDeleteConfirm`) to prevent background image navigation while the dialog is focused.

**Nav context invariant for newly created images**: When navigating to an image that was just created (crop, upscale new-file), the new image ID is not in the existing `gallery-nav-*` list, so `currentIndex === -1` and arrow keys would silently do nothing. Always call `injectNavId(datasetId, sourceImageId, newImageId)` (defined at module level in `ImageDetailPage.tsx`) before calling `paneGo` to insert the new ID immediately after the source in the nav context. This applies to: sync crop, crop+upscale job completion, and standalone upscale (non-replace) completion. Conversely, call `removeNavId(datasetId, imageId)` when deleting an image from `ImageDetailPage` to remove the stale ID so arrow-key navigation on adjacent images cannot land on it. Both functions delegate to `mutateNavIds(datasetId, transform)` — the shared helper that handles sessionStorage read/parse/write.

**ImageDetailPage crop tool**: Two output modes controlled by the **Replace** checkbox. *New file* (default) — creates a new `Image` record (filename `{source_stem}_crop{ext}`, collision-handled via `unique_filename`) and navigates to it on success. *Replace* — overwrites the source file in-place, updates the existing `Image` record (width, height, file_size_bytes, format, phash), regenerates the thumbnail, and stays on the same image. The aspect dropdown and zoom slider control the crop selection shape and size; W×H inputs control the output pixel dimensions (resize-after-crop, independent of the selection). When both W and H are filled in, the crop box aspect ratio automatically locks to W/H. The crop endpoint (`POST /images/{id}/crop`) accepts `replace: bool = False`; in replace mode it calls `protect_file_before_overwrite` before touching the file. New-file mode uses `asyncio.get_running_loop()` and a targeted `LIKE '{stem}%'` query for collision detection (not a full dataset scan). An optional upscale model selector (shown when upscale models are configured) enables atomic crop+upscale in either mode: the crop is saved to a temp file, a `crop_upscale` background job runs the upscale, and the endpoint returns `{job_id}` instead of the image dict. The frontend branches on `"job_id" in data` to distinguish the async path.

**ImageDetailPage selection**: A **Select / Selected** toggle button sits in the top toolbar (right of the filename, before the Boxes button). It calls `selectionStore.toggle(imageId, datasetId)` and reflects `isSelected(imageId)` via a targeted selector. Pressing **Space** anywhere on the page (except when a text field is focused or a modal is open) does the same thing — handled in the arrow-key `useEffect` alongside ArrowLeft/ArrowRight; `showDetectModal` is additionally checked. The button is styled `btn-primary` + `CheckSquare` icon when selected, `btn-ghost` + `Square` when not.

**ImageDetailPage caption panel**: Contains only the caption text textarea and Save button (plus the collapsible AI Generate section). The `tags` and `caption_style` fields are still present in the DB schema, backend save endpoint (`PATCH /captions/{id}`), and save mutation — they are read from `captionData` and re-persisted unchanged — but neither a tag editor nor a style picker is exposed in the UI. A live **token counter** (`N words · N tokens`) is displayed right-aligned beside the "Caption Text" label, computed via `gpt-tokenizer` (`encode` with GPT-2 BPE) inside a `useMemo` keyed on `captionText`. The counter turns amber at ≥ 70 tokens and red at ≥ 77 to signal the CLIP truncation limit.

### Datasets page

`DatasetsPage` uses `queryKey: ["datasets"]` with `staleTime: 0` so the list is always refetched on mount.

**Preview strip**: `GET /datasets/` (`DatasetOut`) accepts optional `skip` (default 0) and `limit` (default 0 = no limit) query params for pagination. Includes `preview_image_ids: list[str]` — up to 8 image IDs fetched in a single batch query alongside the datasets list. The card renders these as `<img src="/api/v1/images/{id}/thumbnail">` tiles. When a dataset has no images the strip falls back to deterministic colour gradients.

**Import job tracking**: after starting an import (`POST /datasets/{id}/import`) `DatasetsPage` stores the returned `job_id` and watches it in `jobStore` via `useEffect`. The `["datasets"]` query is invalidated only when the job status becomes `"completed"` — not when the job is created — so image counts update after the import actually finishes.

**Import subfolder options**: the import modal accepts `subfolder` (target logical subfolder, empty = root) and `preserve_structure: bool` (when true, recursively walks the source folder and maps each subdirectory level to a logical subfolder matching the relative path; when false, all images land in the specified `subfolder`). Both are passed in the `POST /datasets/{id}/import` body as `DatasetImportWithOptions`.

**Card navigation**: Dataset card clicks use `usePaneNavigate().go(url, view)` (not raw `useNavigate`) so that clicking a dataset inside a split pane updates that pane's view rather than the URL. Do not revert to `useNavigate` here.

**Drag-and-drop upload**: `GalleryPage` supports dropping image files onto the grid (`onDragEnter`/`onDragLeave`/`onDrop` on the scroll container wrapper) — this works. `DatasetsPage` has the plumbing in place (native `dragover`/`drop` listeners via `useEffect` on `pageRef`, `data-dataset-id` attributes on cards, `dragOverId` state for the overlay) but the drop does not trigger uploads reliably — **TODO: debug and fix**. Approaches already tried without success: React synthetic `onDragEnter`+`onDragLeave`, `onDragOver`-based debounce timer, native `addEventListener` on the page container with `elementFromPoint`.

**Dataset folder naming**: `create_dataset()` in `dataset_service.py` derives the folder name from the dataset name via `_name_to_slug()` (lowercase, spaces → underscores, special chars stripped, max 80 chars) rather than using the UUID. The UUID is still the DB primary key. If the slug folder already exists (name collision edge case), a `{slug}_{uuid8}` suffix is appended. Example: dataset named `"My Portraits"` creates `data/datasets/my_portraits/`.

**Dataset rename**: `PATCH /datasets/{id}` accepts `{ name?, description? }`. When the name changes, `rename_dataset()` renames the folder on disk, bulk-updates all `Image.file_path`/`thumbnail_path` records via string prefix replacement, and updates `Dataset.folder_path`/`name` — all in one transaction. Returns 400 on name conflict.

### Statistics page

`frontend/src/pages/StatsPage.tsx` renders the dataset analytics dashboard. A compact subfolder dropdown in the page header (shown only when subfolders exist) scopes all four queries to a specific subfolder.

**Panel organization**: Histograms are grouped into 5 collapsible `CategorySection` sections — *Summary*, *Aesthetic & Style*, *Technical Quality*, *Image Properties*, *Captions & Tags* — rendered below always-visible stat cards. A gear icon (`<Settings>`) in the page header opens a fixed right-side `SettingsDrawer` (zIndex 56, dimmer at 55) where per-category and per-item visibility can be toggled. State is persisted to `localStorage` under key `stats-visibility-v1` via the `useStatsVisibility` hook, which merges saved state with `defaultVisibility()` on load so newly added items default to visible. The `show(cat, item)` helper in `StatsPage` combines category + item visibility into a single boolean used in all render conditionals. Grid column counts for variable-visibility rows are computed by filtering the boolean results and using `repeat(N, 1fr)`.

It makes six queries:

| Query key | Source | Contents |
|---|---|---|
| `["subfolders", datasetId]` | `GET /datasets/{id}/subfolders` | Subfolder list for the dropdown |
| `["dataset-stats", datasetId, activeSubfolder]` | `GET /datasets/{id}/stats?subfolder=` | All distributions (see schema below) |
| `["tag-stats", datasetId, activeSubfolder]` | `GET /captions/dataset/{id}/tag-stats?subfolder=` | Top 500 tags with counts |
| `["tag-cooccurrence", datasetId, activeSubfolder]` | `GET /datasets/{id}/tag-cooccurrence?limit=15&subfolder=` | Top-15 tag co-occurrence matrix |
| `["score-values", datasetId, activeSubfolder]` | `GET /datasets/{id}/score-values?subfolder=` | Raw float arrays for all 8 score fields + `megapixels`, `file_size_mb`, `caption_words`, `caption_tokens` — used for client-side histogram rebucketing |
| `["settings", "thresholds"]` | `GET /api/v1/settings/thresholds` | Live flag threshold values for the quality flag hint text; `staleTime: 60_000` |

All four stat endpoints accept `subfolder: str | None = Query(None)`. `activeSubfolder` resets to `undefined` on dataset change. `BucketPanel` receives `subfolder` as a prop and passes it to `GET /images/`.

**`DatasetStats` subfolder invariant**: `get_dataset_stats()` has several queries that run outside the main row-scan, all of which must include `.where(Image.subfolder == subfolder)` when subfolder is not None: (a) embedding count, (b) score coverage (`func.count` per score column), and (c) quality flag counts (`json_extract` + `SUM(CASE …)` per flag key). The row-scan drives histogram distributions, caption coverage, and file size summaries. `total_size_mb` is derived from the filtered `file_sizes_mb` list when a subfolder is active; `ds.total_size_bytes` (the cached dataset total) is only used for the all-images case.

**`DatasetStats` schema** (in `backend/schemas/dataset.py`) includes these distribution dicts on top of the basic summary fields. All are computed in a single row-scan in `dataset_service.get_dataset_stats()`:

| Field | Description |
|---|---|
| `blur_distribution` | 6-bucket Laplacian variance |
| `noise_distribution` | 6-bucket smooth-region std dev |
| `uniformity_distribution` | 5-bucket grayscale std dev |
| `watermark_distribution` | 10 equal bins, 0–1 |
| `color_distribution` / `saturation_distribution` | Hasler-Süsstrunk buckets |
| `megapixel_distribution` | 7-bucket width×height/1M |
| `file_size_distribution` | 6-bucket MB ranges |
| `file_size_summary` | `{min_mb, median_mb, p95_mb, max_mb}` |
| `aspect_ratio_fine` | 8 common AR buckets |
| `caption_length_distribution` | 6-bucket word count |
| `caption_token_distribution` | 6-bucket GPT-2 BPE token count (edges: 1, 20, 40, 60, 77); computed via `tiktoken`; the 77+ bucket flags CLIP-truncated captions |
| `style_similarity_distribution` | 10 equal bins, 0–1 |
| `quality_flag_counts` | `{blurry, noisy, uniform, watermarked, duplicate}` |
| `score_coverage` | Per-score type computed count |

Default bucket edges are defined as `DEFAULT_EDGES` in `StatsPage.tsx`. Edges on the backend (`dataset_service.py`) are used only for pre-computing the initial distributions returned by `/stats`; when the user customises edges, `rebucketValues()` runs entirely client-side against the raw `score-values` arrays — no backend call needed.

**Editable histograms (HistPanel)**: Every score/metric histogram has a pencil icon that opens an inline edge editor. The user types comma-separated boundary values (e.g. `"4, 6"` for aesthetic score), presses Apply or Enter, and the chart immediately rebuckets using the raw value arrays. A "custom" badge appears in the panel title when non-default edges are active; Reset restores the defaults. Aspect ratio and file format histograms are non-editable (no raw values to rebucket). When a customised bar is clicked, `BucketPanel` still opens with the correct `min`/`max` filter derived from the custom edges.

**Clickable bars → BucketPanel**: Every histogram bar carries a `filter` object in its chart-entry data. Clicking fires a `Bar.onClick` handler (recharts v3 pattern — use `Bar.onClick`, not `BarChart.onClick`) which opens a `BucketPanel` modal. The panel queries `GET /images/` with the filter params and shows up to 200 thumbnails. Quality flag cards are also clickable.

**`GET /images/` filter extensions** (in `backend/routers/images.py`):

| Param | Type | Effect |
|---|---|---|
| `search` | `str` | Case-insensitive LIKE filter across `original_filename` and `caption_text` (OR logic) |
| `score_field` | `str` | Which score column `min_score`/`max_score` apply to (whitelist-validated; defaults to `aesthetic_score`) |
| `score_is_null` | `bool` | Filter images where `score_field IS NULL` (used for "unscored" bucket) |
| `score_filters` | `str` (JSON) | JSON-encoded array of `{field, min?, max?}` objects; each entry adds an AND condition; fields validated against `_ALLOWED_SCORE_FIELDS` whitelist |
| `quality_flag` | `str` | Filter by JSON flag key in `quality_flags` (e.g. `is_blurry`) |
| `file_size_min` / `file_size_max` | `int` | `file_size_bytes` range (bytes) |
| `mp_min` / `mp_max` | `float` | `width × height` megapixel range |
| `ar_min` / `ar_max` | `float` | Aspect ratio `width / height` range |
| `format_filter` | `str` | Exact `Image.format` match (e.g. `PNG`) |
| `detection_label` | `str` | `EXISTS` subquery: only images that have at least one detection with `label ILIKE '%...%'` |

**ImageLightbox**: Clicking a thumbnail in `BucketPanel` opens a full-resolution lightbox with prev/next navigation, metadata footer, a "View Details →" link to `/datasets/:datasetId/image/:imageId`, and a two-step **Delete** button. Deleting an image removes it from the panel's TanStack Query cache via `queryClient.setQueryData` (no refetch) and invalidates `dataset-stats`, `tag-stats`, and `tag-cooccurrence` queries. A per-thumbnail ×-on-hover delete button with an inline confirm overlay provides the same action from the grid.

### Settings page

`frontend/src/pages/SettingsPage.tsx`, route `/settings`, sidebar nav item "Settings". Exposes all five quality flag thresholds as editable number inputs.

**Backend**: `backend/routers/settings.py`, prefix `/settings`. Two endpoints:

| Endpoint | Behaviour |
|---|---|
| `GET /thresholds` | Returns current thresholds from the `threshold_settings` singleton row (id=1); if the row doesn't exist yet, returns in-memory defaults from `DEFAULTS` in `threshold_service.py` without writing anything |
| `PATCH /thresholds` | Creates the row on first save (upsert on id=1), updates only the fields present in the body, commits |

**Model**: `backend/models/threshold_settings.py` — `ThresholdSettings` table with a single row (`id=1`). Five `Float` columns with `server_default` matching the constants in `technical_scorer.py`. Defaults are canonically defined in `backend/services/threshold_service.py::DEFAULTS`.

**Frontend**: `useQuery({ queryKey: ["settings", "thresholds"], staleTime: 60_000 })` — shared key with `StatsPage` so both components see the same cached value. Save button is enabled only when at least one field differs from the loaded values (`isChanged`). Save sends only the changed fields via `PATCH`. "Reset to defaults" restores the local form state to the `DEFAULTS` constant without an API call.

The Settings page also includes a **UI Behavior** panel (above Versioning) with two `RadioGroup` controls:
- Delete-confirmation default button (`cancel` / `confirm`). Stored in `localStorage` under `CONFIRM_DEFAULT_KEY`. Read by `ConfirmDialog` on every mount when `danger=true` and no `defaultFocus` prop is provided.
- Branch snapshot behavior (`ask` / `auto`). Stored in `localStorage` under `BRANCH_SNAPSHOT_KEY`. When `"ask"`, `BranchSelector` shows an inline prompt before checkout or branch creation letting the user choose whether to create a snapshot. When `"auto"`, snapshots are always created without prompting. Takes effect immediately — no Save button.

### Styling

Tailwind CSS v3 with a dark theme. Color tokens are CSS custom properties defined in `index.css` (`:root { --bg, --surface-1/2/3, --accent, --line, --fg, --warn, --bad, --info }`) and aliased in `tailwind.config.js` so they can be used as Tailwind classes. Geist/Geist Mono fonts are loaded via Google Fonts in `index.html`. Reusable component classes are defined in `frontend/src/index.css` under `@layer components`:

| Class | Purpose |
|---|---|
| `.btn`, `.btn.primary`, `.btn.ghost`, `.btn.danger`, `.btn.sm` | Button variants |
| `.input`, `.select`, `.checkbox` | Form controls |
| `.panel`, `.panel-h`, `.panel-b` | Card container with header/body sections |
| `.form-row` | 2-col grid (200px label + 1fr control) used in CaptioningPage and ExportPage |
| `.model-row` | Radio-style model selector row with name, description, and VRAM label |
| `.stat-card` | Metric card with large value, label, and optional delta |
| `.hist` / `.hist-axis` | CSS grid bar chart; set `--cols` and `gridTemplateRows: "1fr"` inline; bars use percentage `height` |
| `.flag-card` | 3-col grid (icon, label/desc, count) for quality flags |
| `.badge`, `.badge.dot`, `.badge.good/warn/bad/info/solid` | Semantic badge variants |
| `.icon-btn` | 30×30 ghost icon button |
| `.sel-bar` | Sticky bottom pill bar for selection actions |
| `.crumbs` | Breadcrumb navigation |
| `.nav-section`, `.nav-tail` | Sidebar section header and count badge |
| `.tabs`, `.tab` | Tab bar with accent underline active state |

**`ConfirmDialog`** (`frontend/src/components/common/ConfirmDialog.tsx`) — shared modal for destructive confirmations. Keyboard-aware: auto-focuses Cancel on mount by default (safe default for destructive actions), ArrowLeft/ArrowRight switch focus between Cancel and the confirm button, Enter fires the focused button natively. When adding any global `keydown` listener that handles ArrowLeft/ArrowRight, suppress it while a `ConfirmDialog` is open to avoid background navigation competing with dialog focus — see `showDeleteConfirm` guard in `ImageDetailPage`'s arrow-key effect. Accepts an optional `defaultFocus?: "cancel" | "confirm"` prop to override the focused button per-callsite. When `danger=true` and no `defaultFocus` is provided, the component reads `localStorage.getItem(CONFIRM_DEFAULT_KEY)` (from `constants/storage.ts`) to respect the user's preference set in Settings → UI Behavior.

**CSS hist bars**: The `.hist` class sets `display: grid; align-items: end; height: 90px`. For percentage `height` on bar children to resolve, you must also set `gridTemplateRows: "1fr"` as an inline style on the `.hist` div. Without this the single implicit row has no definite height and percentage heights collapse to 0.

### System GPU stats

`GET /api/v1/system/gpu` (router: `backend/routers/system.py`) returns `{ name, used_mb, total_mb, utilization_pct }` using `torch.cuda.memory_allocated()` and `torch.cuda.get_device_properties(0)`. `utilization_pct` is VRAM utilization (`memory_allocated / total_memory × 100`), not GPU core utilization. Returns `{ name: null }` when CUDA is unavailable. The Sidebar's GPU meter (`useGpuStats` hook in `frontend/src/hooks/useGpuStats.ts`) polls this every 5 s via TanStack Query.

### Captioning post-processing

`CaptionJobRequest` (in `backend/routers/captioning.py`) accepts three post-processing flags:

| Field | Default | Effect |
|---|---|---|
| `strip_refusals` | `true` | Remove common AI refusal phrases from generated captions via `_REFUSAL_RE` compiled regex. |
| `save_backup` | `false` | Before calling `set_caption`, write the existing `.txt` sidecar to `.txt.bak`. |
| `rename_on_caption` | `false` | After saving each caption, rename the image file to `{subfolder_slug}_{NNN}.ext` (or `image_{NNN}.ext` for root). Sets `is_auto_named=True`. Subfolder and original filename are fetched from the initial bulk query — no per-image DB round-trip. |

**Captioning job execution**: `_run` in `routers/captioning.py` processes images one at a time (generate → save → emit SSE). Each event carries `image_id`, `throughput_ips`, and `vram_used_mb` (sampled every 10 images; Ollama always 0). Failed images accumulate in `failed_image_ids`; a `caption_summary` SSE event is emitted after the loop if any failed. Cancellation is checked at each image boundary via the job's DB `status` (`DELETE /jobs/{job_id}` sets it).

**Ollama timeout**: `httpx.AsyncClient` in `ollama_captioner.py` uses a 300-second timeout per image to accommodate slow hardware and cold model loads.

### Bulk caption editing

`POST /captions/dataset/{dataset_id}/bulk-edit` (router: `backend/routers/captions.py`, service: `backend/services/caption_service.py::bulk_edit_captions`) — synchronous bulk text operation on caption_text across a dataset. Returns `{ affected, skipped }`.

**`BulkEditRequest`** fields:

| Field | Default | Effect |
|---|---|---|
| `operation` | required | `"prepend"` / `"append"` / `"remove"` / `"find_replace"` |
| `text` | required | Text to add (prepend/append) or text to find (remove/find_replace) |
| `replacement` | `""` | Replacement string for `find_replace` |
| `use_regex` | `false` | Treat `text` (and `replacement`) as a Python regex; invalid patterns skip the image. Regex matching runs in a thread executor with a 30-second `asyncio.wait_for` timeout to prevent catastrophic backtracking from blocking the event loop; returns 408 on timeout. |
| `image_ids` | `null` | If set, restrict to these image IDs |
| `quality_flags` | `null` | If set, additionally **exclude** images where any of these flags is `True` (AND IS NOT TRUE per flag); validated against `ALLOWED_FLAG_KEYS` from `utils.py` |

Images with no `caption_text` are skipped for `remove` and `find_replace`. For `prepend`/`append` they receive just the added text. A single `db.commit()` is made after the loop — not per image.

**Frontend surfaces**:
- `SelectionToolbar` — **Edit** button (pencil icon) opens a modal with `<BulkEditForm imageIds={selectedIds} />`. On success, invalidates `["images", datasetId]` and clears the selection.
- `BulkEditPage` (`/datasets/:datasetId/bulk-edit`, sidebar "Bulk Edit") — three tabs: *Edit Captions*, *Upscale*, and *Apply LUT*. All tabs share the same scope radio (*All images* / *Exclude images with quality flags* / *Currently selected*). The captions tab embeds `<BulkEditForm>`; the upscale tab embeds `<UpscaleForm>`; the LUT tab embeds `<LutForm>`. The "Exclude flags" scope requires at least one flag to be chosen before the form can submit.

`BulkEditForm` (`frontend/src/components/caption/BulkEditForm.tsx`) — reusable form component. When the `qualityFlags` prop is provided it uses those and hides its own flag selector; when omitted the internal flag selector is shown. The `disabled` prop prevents submission (used by `BulkEditPage` when scope is "flags" but nothing is selected).

### Export page

`ExportPage.tsx` supports 3 format buttons: kohya, ai-toolkit, plain folder. All three are fully implemented. The left panel uses `.form-row` layout throughout.

**Shared export loop**: `export_service.py` uses a shared `_run_export_loop(session, dataset_id, dest_dir, filters, progress_cb, format_fn)` helper that handles the DB query (column-explicit select, no blob fields), filter loop, progress emission, and result accumulation. Each of `export_kohya`, `export_aitoolkit`, and `export_plain` delegates to this helper and provides only a format-specific callback. Blob columns (`clip_embedding`, `dino_embedding`, `dino_layer_embeddings`) are excluded from the query — only `id`, `file_path`, `filename`, `caption_text`, `tags_json`, `aesthetic_score`, `quality_flags`, and `style_similarity_score` are loaded.

**Filters** (applied in `export_service.py::_is_excluded()`, shared by all three formats):

| Control | Param sent | Backend behaviour |
|---|---|---|
| Aesthetic ≥ N | `aesthetic_min: float` | Excludes images where `aesthetic_score` is NULL or below threshold |
| Has caption | `captioned_only: bool` | Excludes images with no `caption_text` and empty `tags_json` |
| Per-flag checkboxes (Blurry / Noisy / Near-uniform / Watermarked / Duplicate) | `exclude_flags: str` (comma-separated flag names, e.g. `"is_blurry,has_watermark"`) | Excludes images where any of the named keys in `quality_flags` JSON is truthy |
| Style similarity ≥ N | `style_sim_min: float` | Excludes images where `style_similarity_score` is NULL or below threshold |

Filter params are debounced 350 ms on the frontend; the preview query (`GET /export/preview/{dataset_id}`) reacts to changes and returns `{ will_export, total, excluded_low_aesthetic, excluded_uncaptioned, excluded_flagged, excluded_style_sim, sample_files }`.

**Caption format** (`caption_format: "txt" | "caption" | "jsonl"`): controls sidecar extension for kohya/ai-toolkit; `"jsonl"` writes a single `captions.jsonl` in the output root instead of per-image sidecars. Hidden for plain folder (always writes `captions.jsonl` + `tags.csv`).

**Resize** (`resize_to: int | None`): after copying/converting, resizes the longest side to the given pixel count via Pillow (only downscales; originals untouched). Skips the PIL round-trip entirely when `resize_to=None`, `output_format="original"`, and `strip_metadata=False`.

**Strip metadata** (`strip_metadata: bool`, default `False`): when `True`, forces a PIL round-trip even for the "original format, no resize" case, which naturally discards PNG text chunks (A1111 `parameters`, ComfyUI `workflow`/`prompt`, etc.) and EXIF. The PIL paths (format conversion, resize) already strip metadata — this flag only affects the `shutil.copy2()` fast path.

**Captions only** (`captions_only: bool`, default `False`): when `True`, skips all image file writes. The `src.exists()` check is also bypassed so images with missing files are still included (their caption data is in the DB). For kohya/aitoolkit, only sidecar/JSONL caption files are written to the concept subdirectory. For plain, no `images/` subdirectory is created — only `captions.jsonl` and `tags.csv` are written to `output_dir`. In captions-only mode, JSONL/CSV entries always use `img.filename` (the original filename) regardless of `output_format`, since no format conversion occurs. Image format, resize, and strip-metadata settings are ignored when this flag is set.


### AI generation metadata

Extracted at import time and on direct upload via `extract_generation_metadata(path)` in `backend/services/image_service.py`. Stored in `Image.generation_metadata` (JSON column, nullable). Included in both `ImageOut` and `ImageListItem` schemas.

Supported formats:

| PNG chunk key | Tool | Parser |
|---|---|---|
| `parameters` | AUTOMATIC1111 / SD WebUI | `_parse_a1111_params()` — splits on `Negative prompt:` (case-insensitive, handles `\r\n`); extracts steps/cfg_scale/seed/sampler/sampler_name/model/model_hash/size/vae from trailing key-value line |
| `workflow` / `prompt` | ComfyUI | Stores raw workflow JSON; extracts text from `CLIPTextEncode`/`CLIPTextEncodeSDXL` nodes as prompt |
| `Comment` | Generic | Stored as `raw`; JSON-parsed if valid |
| EXIF tag 37510 (UserComment) | Various | Parsed as A1111 format if `Steps:` present, otherwise stored as `raw` |

**Parser invariant**: `prompt` is only stored when non-empty. An image generated with no positive prompt results in a dict with `negative_prompt` but no `prompt` key — this is correct, not a bug.

Frontend: `components/image/GenerationMetadata.tsx` — collapsible section titled **GENERATION METADATA** (default expanded) with source badge, prompt + copy button, negative prompt, param grid (model/sampler/steps/CFG/seed/size/VAE), and optional ComfyUI raw workflow viewer.

**Lazy backfill**: `GET /images/{image_id}` calls `extract_generation_metadata` and commits if the field is NULL, transparently backfilling pre-feature images.


### File browser

Router: `backend/routers/filesystem.py`, prefix `/api/v1/filesystem`, registered in `main.py`.

| Endpoint | Purpose |
|---|---|
| `GET /roots` | Windows drive roots (`C:\`, `D:\`, …) |
| `GET /list?path=` | Directory listing — dirs first, then files, both alphabetical; `is_image` flag for image extensions |
| `GET /preview?path=` | Serve image file directly (`FileResponse`) |
| `GET /image-meta?path=` | `{width, height, format, file_size_bytes, generation_metadata}` — reads file without touching DB |
| `POST /move` | Move file/dir; syncs `Image.file_path`, `Image.filename`, `Image.dataset_id` when path is inside a dataset folder |
| `POST /rename` | Rename in place; same DB sync |
| `POST /delete` | Delete file or directory (recursive); deletes files from filesystem first, then removes `Image` DB records — so a failed FS deletion leaves DB records intact |
| `POST /mkdir` | Create directory |

**DB sync**: `_find_dataset_for_path(path, session)` checks if `path` is inside any dataset's `folder_path` and returns the dataset. Move/rename/delete use this to keep `Image` records consistent without a separate import step.

**Path safety**: `_sanitize_path()` rejects null bytes and requires an absolute path. No further sandbox — this is a local desktop app with intentional full-filesystem access.

Frontend page: `FileBrowserPage.tsx`, route `/file-browser`, sidebar nav item "File Browser". Three-panel layout (`200px | 1fr | 280px`): left = drive roots + quick-access links, middle = breadcrumb + file list + context menu (rename/delete/import), right = image preview + `<GenerationMetadata>` panel.

API client: `frontend/src/api/filesystem.ts` — thin wrappers over all endpoints; `previewUrl(path)` returns a URL string for use in `<img src>`.

### Split view pane manager

Allows the main content area to be split into any number of nested panes, each independently showing any page with its own dataset selection.

**Data model** (`frontend/src/stores/paneStore.ts`):

```
PaneLeaf  { type: "leaf"; id: string; view: PaneView }
PaneSplit { type: "split"; id: string; direction: "horizontal"|"vertical";
            sizes: [number, number]; children: [PaneTree, PaneTree] }
PaneView  { page: PageType; datasetId?: string; imageId?: string }
```

All tree mutations (`splitNode`, `closeNode`, `updateLeafView`, `updateSplitSizes`, `updateFirstLeaf`) are pure functions — the store holds a single immutable `layout: PaneTree` root. `syncFromRoute(view)` updates only the first leaf (left-to-top traversal) when URL navigation occurs, preserving all other panes.

**Context & hooks** (`frontend/src/contexts/PaneContext.tsx`, `frontend/src/hooks/`):

| Hook | Purpose |
|---|---|
| `usePaneDatasetId()` | Returns `ctx?.view.datasetId ?? useParams().datasetId` — works both inside and outside pane mode |
| `usePaneImageId()` | Same pattern for `imageId` |
| `usePaneNavigate()` | Returns `{ go(url, view), back(fallbackView) }`. **Inside a pane**: calls `paneStore.setView(paneId, view)`. **Outside**: calls `navigate(url)`. All intra-app navigation that may occur inside a pane MUST use this hook; raw `navigate()` calls change the URL and trigger `RouteSyncer` which only updates pane 1. |

**Components** (`frontend/src/components/pane/`):

- `PaneContainer` — recursive renderer; splits use `react-resizable-panels` `Group`/`Panel`/`Separator` with `orientation` prop. Installed version exports `Group`, `Panel`, `Separator` — NOT `PanelGroup`/`PanelResizeHandle`. `onLayoutChanged` receives `{ [panelId]: number }` keyed by `id` prop on each `<Panel>`. The leaf content wrapper is `display: flex; flexDirection: column` so that pages whose root div uses `flex: 1, overflowY: "auto"` (StatsPage, QualityPage, CaptioningPage, ExportPage, DatasetsPage) correctly fill the pane height and show a scrollbar. Pages that use `height: "100%"` instead (GalleryPage, FileBrowserPage) also work because `height: 100%` resolves against the flex container's definite height.
- `PaneHeader` — 32 px header per pane: page-type `<select>`, dataset `<select>` (for pages in `NEEDS_DATASET`), split-H / split-V / close buttons.
- `PageRenderer` — switch over `view.page` → imports and renders the matching page component.

**App integration** (`frontend/src/App.tsx`):

- `MainContent` renders `<PaneContainer node={layout}>` when `paneStore.enabled`, otherwise the normal `<Routes>` tree.
- `RouteSyncer` (child of `BrowserRouter`) uses `useEffect` on `location.pathname` to call `syncFromRoute()` when pane mode is active — keeps the primary pane in sync with sidebar/URL navigation.
- Toggle: `<Columns2>` icon button in `TopBar` calls `paneStore.toggleEnabled()`.

### Dataset versioning

Snapshot-based version control for datasets. Users can create named snapshots, restore to any prior state, compare two snapshots (diff), and maintain named branches.

**Versioning mode** — stored in `threshold_settings.versioning_mode` (same singleton row as quality thresholds, same `GET/PATCH /api/v1/settings/thresholds` endpoints). Three values:

| Mode | Snapshot behaviour | COW overwrite hook |
|---|---|---|
| `"off"` | Disabled — all versioning endpoints return 400 | No-op |
| `"manual"` | Snapshot copies every file to the object store eagerly (full point-in-time backup). Always runs as a background job. | No-op |
| `"auto"` | Snapshot records metadata + `file_hash=NULL`; object store copies are made lazily on first overwrite (copy-on-write). | Fires before in-place resize/upscale/LUT replace |

Deletion protection fires in both `"manual"` and `"auto"` because deletion is irreversible — the file is backed up before `Path.unlink()`.

**Object store** — content-addressable, git-style:
`{dataset.folder_path}/.versions/objects/{sha256[:2]}/{sha256[2:]}`

Files are stored **only once per unique content** (idempotent copy). No GC in v1 — deleted versions leave orphaned objects.

**`is_present` invariant**: A `VersionImageState` row always has `is_present=True` — it records that the image was present at snapshot time. When an image is deleted, post-deletion snapshots simply have no row for it. This means restoring a pre-deletion snapshot correctly re-creates the image from the object store (the deletion hook backs up the file before it is unlinked). Do not retroactively set `is_present=False` on old rows.

**DB tables** (`backend/models/versioning.py`):

| Table | Purpose |
|---|---|
| `dataset_branches` | Named branches; `head_version_id` FK to latest snapshot on the branch |
| `dataset_versions` | Snapshot records; `parent_id` self-ref for chain; auto-named `Snapshot YYYY-MM-DD HH:MM` if name omitted |
| `version_image_states` | One row per image per snapshot — stores all metadata + `file_hash` (SHA-256; NULL until COW fills it in `"auto"` mode) + `processing_history` (JSON array of replace operations) |

`datasets.current_branch_id` — tracks the active branch (updated on checkout).

**Backend router** (`backend/routers/versioning.py`, prefix `/datasets`, registered in `main.py`):

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/{id}/versions/branches` | List branches |
| `POST` | `/{id}/versions/branches` | Create branch; body: `BranchCreate { name, from_version_id?, include_snapshot: bool = true }`. Sync ≤100 images or when `include_snapshot=false`, else bg job |
| `POST` | `/{id}/versions/branches/{branch_id}/checkout` | Checkout branch (always bg job); body: `CheckoutRequest { pre_restore_snapshot: bool = true }` |
| `DELETE` | `/{id}/versions/branches/{branch_id}` | Delete branch + all its versions (cascade); 400 if last branch or if branch is currently active (`dataset.current_branch_id`) — must switch first |
| `GET` | `/{id}/versions` | List versions — filters: `branch_id`, `search` (name/description ilike), `created_after`/`created_before` (ISO date strings); sorted pinned-first then `created_at DESC` |
| `POST` | `/{id}/versions` | Create snapshot (manual mode always bg job; auto mode inline ≤100 images) |
| `GET` | `/{id}/versions/diff` | Diff two versions (`?v1=&v2=`) — declared BEFORE `/{version_id}` to prevent FastAPI collision |
| `GET` | `/{id}/versions/{version_id}` | Get version detail |
| `PATCH` | `/{id}/versions/{version_id}` | Update version (`is_pinned`) |
| `DELETE` | `/{id}/versions/{version_id}` | Delete version (400 if last on branch) |
| `POST` | `/{id}/versions/{version_id}/restore` | Restore (always bg job → `{job_id}`) |

**Backend service** (`backend/services/version_service.py`):

Key functions:
- `protect_file_before_overwrite(image_id, file_path, db)` — COW hook; no-op unless `"auto"` mode and the image has NULL-hash snapshot rows
- `mark_image_deleted_in_versions(image_id, file_path, db)` — deletion hook; no-op if `"off"` or no snapshot rows exist for image
- `_backup_and_record_hash(image_id, file_path, dataset_folder, db)` — shared helper: hash → copy to object store → backfill all NULL `file_hash` rows for the image in one `UPDATE`
- `create_snapshot(db, dataset_id, name, description, branch_id, job_id, source)` — creates snapshot; `source` is `"manual"` (user-triggered), `"pre_restore"` (auto before restore), or `"branch_init"` (new branch). `"manual"` mode also copies every file eagerly.
- `restore_snapshot(db, dataset_id, version_id, handle_extra_images, pre_restore_snapshot, job_id)` — restores files from object store + updates DB; optionally auto-snapshots current state first; after all files are restored, sets `branch.head_version_id = version_id` so the UI "Current" badge moves to the restored snapshot
- `diff_versions(db, dataset_id, v1, v2)` — pure DB, no background job; uses `_DIFF_COLS` column-explicit select for efficiency; `processing_history` changes render as `+`/`−` operation badges in `DiffModal`

**Copy-on-write injection points** (existing routers, all fire before the file operation):

| File | Operation |
|---|---|
| `backend/routers/images.py` | `resize` endpoint, batch crop `_run` — calls `protect_file_before_overwrite` |
| `backend/routers/images.py` | `crop` endpoint, replace=True branch — calls `protect_file_before_overwrite` |
| `backend/routers/images.py` | `delete_image`, `batch_delete` — calls `mark_image_deleted_in_versions` |
| `backend/routers/upscaling.py` | `_run` coroutine, replace=True branch — calls `protect_file_before_overwrite` |
| `backend/routers/lut.py` | `_run` coroutine, replace=True branch — calls `protect_file_before_overwrite` |

**Frontend**:
- `frontend/src/pages/VersionsPage.tsx` — route `/datasets/:datasetId/versions`, sidebar "Versions". Shows disabled-state when `versioning_mode="off"` (with link to Settings). Otherwise shows branch selector, filter bar (debounced search + date range), version list with source badges (`Manual`/`Pre-restore`/`Branch init`) and pin icon per card. Pin toggle uses `setQueryData` optimistic update + client-side re-sort (no refetch). Active branch is persisted to `sessionStorage` under `VERSIONS_BRANCH_KEY-${datasetId}`; falls back to `dataset.current_branch_id` when sessionStorage is empty (e.g. after server restart), then to `branches[0]`. A `useRef`+`useEffect` watches `dataset.current_branch_id` after the initial mount; when it changes externally (e.g. sidebar branch switch), `activeBranchId` state and sessionStorage are updated — but the guard (`prev !== undefined`) prevents the initial data load from clobbering the stored preference.
- `frontend/src/components/versioning/CreateSnapshotModal.tsx` — name + description inputs; shows `JobProgressBar` during bg job; passes `activeBranchId` in the snapshot body so new snapshots land on the correct branch.
- `frontend/src/components/versioning/RestoreConfirmModal.tsx` — keep/remove radio for extra images, pre-restore snapshot checkbox, file-unavailability warning, `JobProgressBar`
- `frontend/src/components/versioning/DiffModal.tsx` — select two versions; shows Added/Removed/Modified sections with field-level changes
- `frontend/src/components/versioning/BranchSelector.tsx` — branch `<select>` + "New branch…" option; checkout and branch creation each show a `SnapshotPrompt` dialog first when `BRANCH_SNAPSHOT_KEY === "ask"`, otherwise proceed automatically. Checkout triggers a bg job; while running a fixed-position progress card appears bottom-right showing `JobProgressBar` with SSE progress. `onSelect` is called only after the job completes (not immediately on select change) to avoid showing stale data. For sync branch creation (≤100 images), `doCheckout(result.id, false)` is called immediately after creation so `current_branch_id` updates on the backend — `pre_restore_snapshot=false` because the branch was just created from current state. A trash icon button opens a delete modal with its own branch `<select>` (filtered to exclude `currentBranchId` — the currently checked-out branch), so branch deletion is independent of the checkout dropdown. The active branch cannot be deleted; the user must switch branches first.
- `frontend/src/components/common/JobProgressBar.tsx` — shared progress bar (message + animated fill bar); used by snapshot, restore, and branch-checkout flows
- `frontend/src/api/versioning.ts` — `versioningApi`: `listBranches`, `createBranch(datasetId, name, fromVersionId?, includeSnapshot = true)`, `checkoutBranch(datasetId, branchId, preRestoreSnapshot = true)`, `deleteBranch(datasetId, branchId)`, `listVersions` (accepts `ListVersionsParams` object with `branchId`, `search`, `createdAfter`, `createdBefore`), `createSnapshot`, `getVersion`, `deleteVersion`, `updateVersion` (PATCH for `is_pinned`), `restoreVersion`, `diff`. `createSnapshot`/`createBranch` return `Version | { job_id: string }` — discriminate with `"job_id" in data`.
- **Sidebar version panel** (`SidebarVersionPanel.tsx`) — interactive accordion rendered below the "Active dataset" label. Collapsed: shows current branch name (monospace) · head snapshot name and a chevron toggle. Expanded: embeds `<BranchSelector>` for switching branches, the 7 most recent snapshots with relative timestamps and `[Restore]` buttons (current snapshot shows "Now"), and a "View all →" link to `VersionsPage`. Snapshot query (`["versions", datasetId, activeBranch.id, "sidebar"]`) is gated on `expanded` with `limit: 7`. `onSelect` writes `VERSIONS_BRANCH_KEY-${datasetId}` to sessionStorage so `VersionsPage` shows the correct branch when navigated to later. `onSuccess` after restore invalidates branches, dataset, images, captions, and versions queries. Only rendered when `activeBranch` is defined (i.e. versioning is on and branches exist).

**TanStack Query keys**:
- `["branches", datasetId]` — invalidated after snapshot creation, restore, checkout
- `["versions", datasetId, resolvedBranchId, search, createdAfter, createdBefore]` — invalidated after snapshot creation, delete, restore; pin toggle uses `setQueryData` instead of invalidation
- `["images", datasetId]` — invalidated after restore and after checkout (image set and captions change)
- `["image"]` — prefix invalidation (no imageId) after restore and checkout; clears all cached image detail pages so `ImageDetailPage` refetches immediately
- `["caption"]` — prefix invalidation after restore and checkout; clears all cached caption data
- `["dataset", datasetId]` — invalidated after restore and checkout (image count, `current_branch_id`)

**`DatasetVersion` fields**: `id`, `dataset_id`, `branch_id`, `parent_id`, `name`, `description`, `image_count`, `created_at`, `source` (`Literal["manual", "pre_restore", "branch_init"]`), `is_pinned` (`bool`).
