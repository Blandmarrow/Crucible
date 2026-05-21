# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Launch

**Windows** — double-click the `.bat` files in Explorer, or run `manage.ps1` directly in PowerShell:

| File | Purpose |
|---|---|
| `Setup Dataset Manager.bat` | Double-click to set up (runs `manage.ps1 setup`) |
| `Start Dataset Manager.bat` | Double-click to launch (runs `manage.ps1 start`) |
| `Update Dataset Manager.bat` | Double-click to update (runs `manage.ps1 update`) |
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
- `ALLOWED_FLAG_KEYS: frozenset` — the canonical set of valid quality flag names (`is_blurry`, `is_noisy`, `is_uniform`, `has_watermark`, `is_duplicate`). Import this wherever flag names must be validated or used in SQL filters; never redefine the set locally.

### Image file naming

Images receive human-readable names derived from their original filename via `slugify_filename`. Collision handling is done by `unique_filename` (counter suffix `_001`, `_002`, …). This applies at upload time, at import time, and when the captioning rename option is used.

`Image.is_auto_named: bool` (default `False`) — set to `True` when a file is renamed by the captioning job or by a subfolder move. Used to distinguish auto-named files from manually named ones. `PATCH /images/{image_id}/rename` sets it back to `False`.

**Subfolder-based naming**: when `rename_on_caption=True` or when images are moved between subfolders (`POST /images/batch/move-subfolder`), filenames are derived from the target subfolder slug (e.g. images in `"animals"` become `animals.jpg`, `animals_001.jpg`, …; images in root become `image.jpg`, `image_001.jpg`, …). All moves rename every image in the batch unconditionally.

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
| `await model_manager.unload(model_id)` | Unloads one model by ID, acquires its per-model lock, moves weights to CPU, deletes the entry, and calls `torch.cuda.empty_cache()`. |
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
| `is_blurry` | `blur_score` (Laplacian variance) | < 80 | `BLUR_THRESHOLD` constant in `technical_scorer.py` |
| `is_noisy` | `noise_score` (smooth-region std dev) | > 15 | `NOISE_THRESHOLD` constant in `technical_scorer.py` |
| `is_uniform` | `uniformity_score` (grayscale std dev) | < 12 | `UNIFORMITY_THRESHOLD` constant in `technical_scorer.py` |
| `has_watermark` | `watermark_score` (CLIP zero-shot, 0–1) | ≥ 0.6 | `settings.watermark_threshold` — configurable via `WATERMARK_THRESHOLD=` in `.env` |


**Style similarity flow**: (1) run scoring with the desired embedding flags — `run_embeddings=True` stores `clip_embedding`; `run_dino=True` stores `dino_embedding` (independent of `run_embeddings`); `run_dino_layers=True` (requires `run_dino=True`) stores `dino_layer_embeddings`. (2) call `POST /quality/style-similarity` with `reference_image_ids` and/or `reference_embeddings` (base64 float16 bytes, CLIP-only). The `embedding_type` field selects the scoring mode:

| `embedding_type` | `dino_layer` | Column(s) written | Description |
|---|---|---|---|
| `"clip"` | — | `style_similarity_score` | Cosine similarity of CLIP embeddings |
| `"dino"` | `null` | `style_similarity_score` | Cosine similarity of DINOv2 final-layer embeddings |
| `"dino"` | 1–12 | `style_similarity_score` | Cosine similarity using a specific DINOv2 transformer layer (from `dino_layer_embeddings`) |
| `"combined"` | `null` | `style_similarity_score` | `0.38 × clip_sim + 0.62 × dino_sim` (final layer) |
| `"combined"` | 1–12 | `style_similarity_score` | `0.38 × clip_sim + 0.62 × dino_layer_sim` (specific layer) |
| `"dino_all_layers"` | — | `dino_layer_scores` (JSON) | Scores each of the 12 DINOv2 layers independently; writes `{"1": score, …, "12": score}` |
| `"combined_all_layers"` | — | `dino_layer_scores` (JSON) | Blended score (0.38 CLIP + 0.62 DINOv2) for each of the 12 layers; writes `{"1": score, …, "12": score}` |

Local reference files can be embedded on-the-fly via `POST /quality/embed-references` (multipart upload → returns base64 CLIP embeddings). External refs are CLIP-only; `"combined"`, `"dino"`, and `"dino_all_layers"` / `"combined_all_layers"` modes require dataset images as references. No job queue — all similarity computation is CPU-only numpy and runs synchronously in the request.

**Config validation** (`backend/config.py`): A `@model_validator(mode="after")` in the Pydantic `Settings` class enforces two rules at startup: (1) `max_vram_mb < 1000` raises `ValueError` with a clear message so misconfigured deployments fail fast; (2) an empty `hf_token` logs a debug-level warning (not a hard error, since most users don't use PaliGemma). `watermark_threshold` (default `0.6`) is a configurable field — set `WATERMARK_THRESHOLD=` in `.env` to tune it without a code change.

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

**Output modes**: *New file* — filename `{stem}_up{N}x{ext}` (collision-handled via `unique_filename`), new `Image` record created, thumbnail regenerated. *Replace* — updates `width`/`height`/`file_size_bytes`/`updated_at` on existing record, thumbnail regenerated.

**Frontend surfaces**:
- `ImageDetailPage` — "Upscale" toolbar button toggles inline controls (model select, Replace checkbox, optional W×H). Uses `upscalingApi.run()` with `image_ids: [imageId]`.
- `SelectionToolbar` — "Upscale" button opens a modal with `<UpscaleForm>`.
- `BulkEditPage` — "Upscale" tab (see Bulk caption editing section).

`UpscaleForm` (`frontend/src/components/upscale/UpscaleForm.tsx`) — reusable form used by `SelectionToolbar` and `BulkEditPage`. Queries `["upscale-models"]` with `staleTime: Infinity` (model list never changes at runtime).

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
- **Zustand stores** — `datasetStore` (active dataset), `selectionStore` (Set of selected image IDs), `jobStore` (Map of active job progress from SSE), `promptPresetsStore` (saved AI prompt presets, persisted to localStorage), `paneStore` (split-view pane layout — see Split view pane manager section).
- **`useJobSSE(jobId)`** — opens `EventSource` for one job, writes progress to `jobStore`.
- **`useAllJobsSSE()`** — opened at app root in `TopBar`, drives the global progress bar.
- **Job completion → cache invalidation**: pages that trigger background jobs (`QualityPage`, `SelectionToolbar`, `ImageDetailPage`) watch their job ID in `jobStore` via `useEffect` and call `qc.invalidateQueries` when status becomes `"completed"`. Always follow this pattern when adding new job-triggering UI.
- **Per-image cache invalidation (captioning)**: Caption SSE events carry `image_id`; `CaptioningPage` invalidates `["images", datasetId]` on every `done` increment so the gallery updates in real-time.
- **SelectionToolbar score modal**: the "Run Scoring" action accepts four boolean toggles — `run_technical`, `run_aesthetic`, `run_watermark` (CLIP zero-shot watermark detection), and `run_embeddings` (CLIP + DINOv2 embedding extraction for style similarity). `run_watermark` and `run_embeddings` default to `false` since they add significant VRAM/time overhead.

**Thumbnail cache-busting**: `imagesApi.thumbnailUrlVersioned(id, updatedAt)` appends `?v={timestamp}` derived from `image.updated_at` (present on both `ImageListItem` and `ImageOut`). Use this helper — not the raw `thumbnailUrl` — wherever a thumbnail could change due to crop or resize. It is already used in `ImageCard`, `QualityPage`, `StatsPage`, and `StyleReferencePicker`; any new thumbnail `<img>` in those contexts should follow the same pattern.

### Frontend constants

`frontend/src/constants/captionStyles.ts` — `STYLE_LABELS: Record<string, string[]>` (style names per model type) and `modelType(model: string): string | null` (maps a model ID to its type key). Shared by `CaptioningPage`, `ImageDetailPage`, and `SelectionToolbar`; do not redeclare locally.

`frontend/src/constants/dinoLabels.ts` — `DINO_LAYER_LABELS: Record<string, string>` mapping layer number (1–12) to a human-readable description. Shared by `ImageDetailPage` and any future UI that shows per-layer DINOv2 scores.

`frontend/src/constants/flags.ts` — `FLAG_OPTIONS: readonly [{key, label}]` mapping each quality flag key to its display label. `FlagKey` is the derived union type. Shared by `ExportPage`, `BulkEditForm`, and `BulkEditPage`; do not redeclare locally.

### Layout

**Sidebar** uses `useMatch("/datasets/:datasetId/*")` (not `useParams`) to detect the active dataset, because the Sidebar renders outside the `<Routes>` tree and `useParams` would always return `{}` there.

### Gallery generation metadata

`generation_metadata` is included in both `ImageOut` and `ImageListItem` backend schemas, so it comes back with the gallery list response. `ImageCard` shows a small accent `<Cpu>` icon button in the filename row when `image.generation_metadata` is set; clicking it (without navigating) opens a page-level modal in `GalleryPage` that renders `<GenerationMetadata>`. The same component appears in the right panel of `ImageDetailPage`, expanded by default.

### Gallery subfolder sidebar

`GalleryPage` shows a left-hand subfolder sidebar (180 px fixed) when any subfolder exists or when the create form is open. Items: "All" (no filter), "(root)" (empty-string subfolder, only shown if images exist there), and one button per named subfolder with its image count. Active item is highlighted with `var(--surface-3)`.

- **Create**: `+` icon in the sidebar header opens an inline form (input + Enter/Escape handling). If no subfolders exist yet, a `+ Subfolder` button appears in the main toolbar instead to surface the sidebar. On confirm, calls `datasetsApi.createSubfolder` → `POST /datasets/{id}/subfolders`, sets active subfolder to the new path.
- **Delete**: hover-revealed `×` button on each row opens a `ConfirmDialog`. If the subfolder has images, the dialog warns they will be moved to root (not deleted). On confirm, calls `datasetsApi.deleteSubfolder` → `DELETE /datasets/{id}/subfolders?path=...`. If the deleted subfolder was active, resets to "All".
- **Upload subfolder**: a `<select>` next to the Upload button lets users target a specific subfolder for drag-drop or file-picker uploads. Defaults to the active subfolder; can be overridden independently.
- **Query key**: `["subfolders", datasetId]` — invalidated after upload, batch delete, batch move, create, and delete.
- **CSS**: `.subfolder-row .subfolder-delete-btn` is `opacity: 0`; `.subfolder-row:hover .subfolder-delete-btn` reveals it. Defined in `frontend/src/index.css`.

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

`ImageDetailPage` reads `gallery-nav-*` to support arrow-key navigation. When the user reaches the boundary of the current page it pre-fetches the adjacent page (`useQuery`, `enabled: atEnd / atStart`) and on crossing writes the new page's context back to `gallery-nav-*` and updates `gallery-state-*` so that **Back** returns to the correct gallery page. Arrow keys are suppressed when an `<input>` or `<textarea>` has focus.

**Nav context invariant for newly created images**: When navigating to an image that was just created (crop, upscale new-file), the new image ID is not in the existing `gallery-nav-*` list, so `currentIndex === -1` and arrow keys would silently do nothing. Always call `injectNavId(datasetId, sourceImageId, newImageId)` (defined at module level in `ImageDetailPage.tsx`) before calling `paneGo` to insert the new ID immediately after the source in the nav context. This applies to: sync crop, crop+upscale job completion, and standalone upscale (non-replace) completion.

**ImageDetailPage crop tool**: Non-destructive — creates a new `Image` record (filename `{source_stem}_crop{ext}`, collision-handled via `unique_filename`) rather than overwriting the source. The aspect dropdown and zoom slider control the crop selection shape and size; W×H inputs control the output pixel dimensions (resize-after-crop, independent of the selection). When both W and H are filled in, the crop box aspect ratio automatically locks to W/H. On success, navigates to the new image. The crop endpoint (`POST /images/{id}/crop`) uses `asyncio.get_running_loop()` and a targeted `LIKE '{stem}%'` query for collision detection (not a full dataset scan). An optional upscale model selector (shown when upscale models are configured) enables atomic crop+upscale: the crop is saved to a temp file, a `crop_upscale` background job runs the upscale, and the endpoint returns `{job_id}` instead of the image dict. The frontend branches on `"job_id" in data` to distinguish the async path.

**ImageDetailPage caption panel**: Contains only the caption text textarea and Save button (plus the collapsible AI Generate section). The `tags` and `caption_style` fields are still present in the DB schema, backend save endpoint (`PATCH /captions/{id}`), and save mutation — they are read from `captionData` and re-persisted unchanged — but neither a tag editor nor a style picker is exposed in the UI. A live **token counter** (`N words · N tokens`) is displayed right-aligned beside the "Caption Text" label, computed via `gpt-tokenizer` (`encode` with GPT-2 BPE) inside a `useMemo` keyed on `captionText`. The counter turns amber at ≥ 70 tokens and red at ≥ 77 to signal the CLIP truncation limit.

### Datasets page

`DatasetsPage` uses `queryKey: ["datasets"]` with `staleTime: 0` so the list is always refetched on mount.

**Preview strip**: `GET /datasets/` (`DatasetOut`) includes `preview_image_ids: list[str]` — up to 8 image IDs fetched in a single batch query alongside the datasets list. The card renders these as `<img src="/api/v1/images/{id}/thumbnail">` tiles. When a dataset has no images the strip falls back to deterministic colour gradients.

**Import job tracking**: after starting an import (`POST /datasets/{id}/import`) `DatasetsPage` stores the returned `job_id` and watches it in `jobStore` via `useEffect`. The `["datasets"]` query is invalidated only when the job status becomes `"completed"` — not when the job is created — so image counts update after the import actually finishes.

**Import subfolder options**: the import modal accepts `subfolder` (target logical subfolder, empty = root) and `preserve_structure: bool` (when true, recursively walks the source folder and maps each subdirectory level to a logical subfolder matching the relative path; when false, all images land in the specified `subfolder`). Both are passed in the `POST /datasets/{id}/import` body as `DatasetImportWithOptions`.

**Card navigation**: Dataset card clicks use `usePaneNavigate().go(url, view)` (not raw `useNavigate`) so that clicking a dataset inside a split pane updates that pane's view rather than the URL. Do not revert to `useNavigate` here.

**Drag-and-drop upload**: `GalleryPage` supports dropping image files onto the grid (`onDragEnter`/`onDragLeave`/`onDrop` on the scroll container wrapper) — this works. `DatasetsPage` has the plumbing in place (native `dragover`/`drop` listeners via `useEffect` on `pageRef`, `data-dataset-id` attributes on cards, `dragOverId` state for the overlay) but the drop does not trigger uploads reliably — **TODO: debug and fix**. Approaches already tried without success: React synthetic `onDragEnter`+`onDragLeave`, `onDragOver`-based debounce timer, native `addEventListener` on the page container with `elementFromPoint`.

**Dataset folder naming**: `create_dataset()` in `dataset_service.py` derives the folder name from the dataset name via `_name_to_slug()` (lowercase, spaces → underscores, special chars stripped, max 80 chars) rather than using the UUID. The UUID is still the DB primary key. If the slug folder already exists (name collision edge case), a `{slug}_{uuid8}` suffix is appended. Example: dataset named `"My Portraits"` creates `data/datasets/my_portraits/`.

**Dataset rename**: `PATCH /datasets/{id}` accepts `{ name?, description? }`. When the name changes, `rename_dataset()` renames the folder on disk, bulk-updates all `Image.file_path`/`thumbnail_path` records via string prefix replacement, and updates `Dataset.folder_path`/`name` — all in one transaction. Returns 400 on name conflict.

### Statistics page

`frontend/src/pages/StatsPage.tsx` renders the dataset analytics dashboard. A compact subfolder dropdown in the page header (shown only when subfolders exist) scopes all four queries to a specific subfolder. It makes five queries:

| Query key | Source | Contents |
|---|---|---|
| `["subfolders", datasetId]` | `GET /datasets/{id}/subfolders` | Subfolder list for the dropdown |
| `["dataset-stats", datasetId, activeSubfolder]` | `GET /datasets/{id}/stats?subfolder=` | All distributions (see schema below) |
| `["tag-stats", datasetId, activeSubfolder]` | `GET /captions/dataset/{id}/tag-stats?subfolder=` | Top 500 tags with counts |
| `["tag-cooccurrence", datasetId, activeSubfolder]` | `GET /datasets/{id}/tag-cooccurrence?limit=15&subfolder=` | Top-15 tag co-occurrence matrix |
| `["score-values", datasetId, activeSubfolder]` | `GET /datasets/{id}/score-values?subfolder=` | Raw float arrays for all 8 score fields + `megapixels`, `file_size_mb`, `caption_words`, `caption_tokens` — used for client-side histogram rebucketing |

All four stat endpoints accept `subfolder: str | None = Query(None)`. `activeSubfolder` resets to `undefined` on dataset change. `BucketPanel` receives `subfolder` as a prop and passes it to `GET /images/`.

**`DatasetStats` subfolder invariant**: `get_dataset_stats()` has two scalar queries that run outside the main row-scan — embedding count and disk usage — and both must include `.where(Image.subfolder == subfolder)` when subfolder is not None. The row-scan itself drives all other fields (distributions, caption coverage, mean aesthetic, quality flags). `total_size_mb` is derived from the filtered `file_sizes_mb` list when a subfolder is active; `ds.total_size_bytes` (the cached dataset total) is only used for the all-images case.

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

**CSS hist bars**: The `.hist` class sets `display: grid; align-items: end; height: 90px`. For percentage `height` on bar children to resolve, you must also set `gridTemplateRows: "1fr"` as an inline style on the `.hist` div. Without this the single implicit row has no definite height and percentage heights collapse to 0.

### System GPU stats

`GET /api/v1/system/gpu` (router: `backend/routers/system.py`) returns `{ name, used_mb, total_mb, utilization_pct }` using `torch.cuda.memory_allocated()` and `torch.cuda.get_device_properties(0)`. Returns `{ name: null }` when CUDA is unavailable. The Sidebar's GPU meter (`useGpuStats` hook in `frontend/src/hooks/useGpuStats.ts`) polls this every 5 s via TanStack Query.

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
| `use_regex` | `false` | Treat `text` (and `replacement`) as a Python regex; invalid patterns skip the image |
| `image_ids` | `null` | If set, restrict to these image IDs |
| `quality_flags` | `null` | If set, additionally **exclude** images where any of these flags is `True` (AND IS NOT TRUE per flag); validated against `ALLOWED_FLAG_KEYS` from `utils.py` |

Images with no `caption_text` are skipped for `remove` and `find_replace`. For `prepend`/`append` they receive just the added text. A single `db.commit()` is made after the loop — not per image.

**Frontend surfaces**:
- `SelectionToolbar` — **Edit** button (pencil icon) opens a modal with `<BulkEditForm imageIds={selectedIds} />`. On success, invalidates `["images", datasetId]` and clears the selection.
- `BulkEditPage` (`/datasets/:datasetId/bulk-edit`, sidebar "Bulk Edit") — two tabs: *Edit Captions* and *Upscale*. Both tabs share the same scope radio (*All images* / *Exclude images with quality flags* / *Currently selected*). The captions tab embeds `<BulkEditForm>`; the upscale tab embeds `<UpscaleForm>`. The "Exclude flags" scope requires at least one flag to be chosen before the form can submit.

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

**Resize** (`resize_to: int | None`): after copying/converting, resizes the longest side to the given pixel count via Pillow (only downscales; originals untouched). Skips the PIL round-trip entirely when `resize_to=None` and `output_format="original"`.


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
| `POST /delete` | Delete file or directory (recursive); deletes `Image` DB records first |
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
