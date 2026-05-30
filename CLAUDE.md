# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Launch

**Windows**: `manage.ps1 <cmd>` (or double-click `Crucible.bat`) | **Linux/macOS**: `./manage.sh <cmd>` (run `chmod +x manage.sh` once)

| Command | Purpose |
|---|---|
| `setup` | First time — creates venv, installs deps, builds frontend |
| `start` | Production: runs migrations, rebuilds frontend if needed, serves on :8000 |
| `update` | `git pull` → update pip deps → `npm install` → rebuild frontend |
| `dev` | Dev mode: backend on :8000 (hot reload) + Vite frontend on :5173 |

To shut down the running server, click the power icon button in the top-right of the TopBar (confirms before shutting down), or press Ctrl+C in the terminal. A circular-arrow **Restart** button sits immediately left of the power button — it restarts the server in place without requiring terminal access (see Server control endpoints below).

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

`BackgroundJob` has a nullable `label: str | None` column (max 200 chars). Every router that creates a job sets an auto-generated descriptive label (e.g. `"Florence-2 (large) — 50 images"`, `"Quality: technical, aesthetic — 100 images"`) and accepts an optional `label` field in the request body to override it (`body.label or auto_label`). A `_model_short_label(model: str) -> str` helper in `routers/captioning.py` converts raw model IDs to readable names for the auto-label.

### Frontend serving (production)

`backend/main.py` serves the built React app after all API routers are registered. Two parts:

1. `app.mount("/assets", StaticFiles(directory=frontend_dist/"assets"))` — serves JS/CSS/fonts at their exact paths with correct content-type headers.
2. `@app.get("/{full_path:path}")` catch-all — for any unmatched path, serves the file directly if it exists in `frontend/dist/` (favicon, manifest, etc.), otherwise returns `index.html` so React Router handles client-side navigation.

**Do not replace this with a bare `StaticFiles(html=True)` mount.** Starlette's `html=True` only falls back to `index.html` for directory-style paths (`/`); it returns 404 for deep URLs like `/datasets/abc123` on hard refresh. The catch-all route is required for SPA routing to work correctly.

### Server control endpoints

Three endpoints are registered directly in `backend/main.py` (not via a router), immediately before the frontend-serving block:

| Endpoint | Behaviour |
|---|---|
| `POST /api/v1/shutdown` | Touches `.shutdown` sentinel, then spawns a daemon thread that sends `SIGTERM` to the current process, triggering uvicorn's graceful shutdown. The sentinel lets the manage script distinguish a clean shutdown from a crash. |
| `GET /api/v1/health` | Returns `{"status": "ok", "start_time": <float>}`. `start_time` is `time.time()` captured once at module load (`_START_TIME`). Used by the frontend to detect when a restarted server is a *new* process. |
| `POST /api/v1/restart` | Removes `.shutdown` (if present), creates `.restart` sentinel file (`_RESTART_SENTINEL = Path(__file__).parent.parent / ".restart"`), then sends `SIGTERM`. The manage-script restart loop detects the sentinel after uvicorn exits and re-launches the server. |

**Restart loop** — `Cmd-Start`/`cmd_start` in `manage.ps1`/`manage.sh` wrap the uvicorn call in a `while` loop. After uvicorn exits they check for `.restart`; if present they delete it and restart, otherwise they break. `Cmd-Dev`/`cmd_dev` have the same loop (bash uses a background subshell so the frontend Vite server can run concurrently). Stale `.restart` and `.shutdown` files left by a crash are deleted at the top of each start before the loop begins. The uvicorn call is written as `python -m uvicorn ... || true` in `manage.sh` so that `set -euo pipefail` does not abort the loop on a non-zero exit before the sentinel check runs.

**Terminal auto-close on shutdown** — after the restart loop exits (no `.restart` sentinel), `Cmd-Start` in `manage.ps1` checks for `.shutdown`. If found, it deletes the file and calls `exit 0`. `Crucible.bat` only runs `pause` when the PowerShell exit code is non-zero, so a clean shutdown closes the terminal window automatically while a crash keeps it open so the user can read the error output.

**Frontend restart flow** (`TopBar.tsx` `handleRestart`): (1) fetch `/api/v1/health` to record the current `start_time`; (2) POST `/api/v1/restart`; (3) poll `/api/v1/health` with `{ cache: "no-store" }` every second until the response carries a *different* `start_time` (confirming a new process, not the dying old one); (4) `window.location.reload()`. If no new process appears within 60 s, `setRestarting(false)` resets the UI so the user can retry. Both the restart and shutdown buttons are disabled while `restarting || shuttingDown`.

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

**Subfolder-based naming**: when `rename_on_caption=True` or when images are moved between subfolders (`POST /images/batch/move-subfolder`) or between datasets (`POST /images/batch/move-dataset` / `POST /images/batch/copy-dataset`), filenames are derived from the target subfolder slug (e.g. images in `"animals"` become `animals.jpg`, `animals_001.jpg`, …; images in root become `image.jpg`, `image_001.jpg`, …). Cross-dataset moves and copies always rename. For same-dataset subfolder moves, renaming is conditional: `BatchMoveSubfolderRequest.rename_on_move: bool = True` — when `False`, only the `subfolder` metadata column is updated and filenames are preserved (the user's preference is stored in `localStorage` under `SUBFOLDER_RENAME_KEY` and read by `SelectionToolbar` at mutation time).

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
| `await model_manager.unload(model_id)` | Acquires per-model lock, moves weights to CPU, deletes model + processor objects, removes entry, calls `torch.cuda.empty_cache()`. |
| `await model_manager.evict_all()` | Calls `unload()` for every registered model + final `cuda.empty_cache()`. Returns list of unloaded IDs. Used by `POST /api/v1/models/unload-all`. |

**`POST /api/v1/models/unload-all`** (router: `backend/routers/models.py`, prefix `/models`) — evicts all ML models from VRAM without restarting. Returns `{ "status": "ok", "unloaded": [model_id, ...] }`. Call after quality scoring so scoring models don't occupy VRAM when no longer needed.

`HF_TOKEN` from `.env` is injected into `os.environ` early in `main.py` so all `hf_hub_download` calls pick it up automatically.

Model IDs and their captioner/scorer modules:
| Prefix | Module |
|---|---|
| `florence2*` | `ml/florence_captioner.py` |
| `paligemma2` | `ml/paligemma_captioner.py` (needs `HF_TOKEN` in `.env`; accept license at huggingface.co/google/paligemma2-3b-pt-448) |
| `ollama:*` | `ml/ollama_captioner.py` (HTTP calls to localhost:11434) |
| `openai_compat:{id}:{model}` | `ml/openai_compat_captioner.py` (OpenAI-compatible vision API; `id` is the `OpenAIProvider` DB row UUID, `model` is the model name string) |
| `wd14:{variant}` | `ml/wd14_tagger.py` (ONNX inference via `onnxruntime`; downloads from SmilingWolf HuggingFace repos; not tracked by `model_manager` — uses its own module-level cache with `threading.Lock` and double-check locking; variants: `eva02_large` (~2.0 GB RAM), `vit_large` (~1.4 GB RAM), `swinv2` (~0.6 GB RAM); `list_wd14_models()` returns `{id, name, ram_mb}` — `ram_mb` is included in the `/captioning/models` response and displayed in the UI alongside each model) |
| `upscale:{abs_path}` | `ml/upscaler.py` (spandrel; keyed by absolute model file path to support multiple loaded upscalers) |
| `aesthetic` | `ml/aesthetic_scorer.py` (auto-downloads weights from `camenduru/improved-aesthetic-predictor` via `hf_hub_download`; also used for CLIP zero-shot watermark detection and CLIP embedding extraction) |
| `dino` | `ml/dino_scorer.py` (`facebook/dinov2-base` via HuggingFace `transformers`; ~1.2 GB VRAM; used for DINOv2 embedding extraction) |

**Target resolution preprocessing**: `CaptionJobRequest` accepts optional `target_width` / `target_height`. When set, `ml/image_utils.py::preprocess_for_caption()` center-crops each image to the target aspect ratio and resizes it to the exact target resolution before inference. This ensures captions describe the composition the model will actually see at training time. All three captioners (Florence-2, PaliGemma-2, Ollama) call this utility; Ollama's existing `max_px` scale-down runs afterward on the already-cropped image. Omitting both fields leaves behavior unchanged.

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
- `CaptioningPage` — "Object Detection" section when a Florence-2 model is selected; uses the same model as captioning.
- `ImageDetailPage` — DETECTIONS panel (collapsible, label chips with counts + SVG overlay with per-label color coding). Label chips toggle `hiddenLabels: Set<string>` to filter the overlay; state resets on navigation. Eye icon shows/hides all boxes.

Flag thresholds:
| Flag | Column | Default threshold | Source |
|---|---|---|---|
| `is_blurry` | `blur_score` (Laplacian variance) | < 100 | `blur_threshold` in `threshold_settings` DB table |
| `is_noisy` | `noise_score` (smooth-region std dev) | > 15 | `noise_threshold` in `threshold_settings` DB table |
| `is_uniform` | `uniformity_score` (grayscale std dev) | < 12 | `uniformity_threshold` in `threshold_settings` DB table |
| `has_watermark` | `watermark_score` (CLIP zero-shot, 0–1) | ≥ 0.6 | `watermark_threshold` in `threshold_settings` DB table |
| `is_duplicate` | `phash` (perceptual hash Hamming distance) | < 8 | `duplicate_threshold` in `threshold_settings` DB table |

All five thresholds are user-configurable via Settings (`/settings` → `GET/PATCH /api/v1/settings/thresholds`). Changes take effect on the next scoring run; existing images are not re-flagged. Constants in `technical_scorer.py` serve only as parameter defaults — the quality router always passes DB-fetched values via `backend/services/threshold_service.py::get_thresholds()`.

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

**Config validation** (`backend/config.py`): `@model_validator(mode="after")` enforces at startup: (1) `max_vram_mb < 1000` → `ValueError` (fail fast); (2) empty `hf_token` → debug-level warning (not hard error); (3) unrecognised `.env` keys → WARNING log; `extra="ignore"` retained so OS env vars are never flagged. `config.py` still declares `watermark_threshold` for legacy `.env` compatibility but the quality router reads all five thresholds from the `threshold_settings` DB table instead.

**TorchDynamo is disabled** (`TORCHDYNAMO_DISABLE=1` set in `main.py`). Triton is unavailable on Windows and single-image inference gains nothing from `torch.compile`, so it is disabled for the entire process. Do not remove this without re-testing all ML inference paths on Windows.

**Florence-2 PromptGen v2 compatibility patches** (in `_load_florence2_sync`, `ml/model_manager.py`): Two runtime patches are applied after loading `MiaoshouAI/Florence-2-large-PromptGen-v2.0` to fix breakage with `transformers >= 4.50`:
1. `_initialize_missing_keys` is temporarily replaced with a version that swallows `AttributeError` during `from_pretrained`, because the DaViT vision encoder doesn't implement `_initialize_weights` and newer transformers always calls the missing-keys hook after loading.
2. `GenerationMixin` is appended to `Florence2LanguageForConditionalGeneration.__bases__` (after `PreTrainedModel`, as the library requires) because `transformers >= 4.50` removed `GenerationMixin` from `PreTrainedModel`'s inheritance chain, leaving the sub-model's `.generate()` undefined. `generation_config` is then initialised via `GenerationConfig.from_model_config(model.language_model.config)` because it is not set on the sub-model instance. Do not remove either patch without verifying on `transformers >= 4.50`.

**Venv ML packages**: `torch`, `transformers`, `open_clip_torch`, `accelerate`, `safetensors`, and `timm` are listed as real dependencies in `backend/requirements.txt`. The venv is created with `--system-site-packages`, so if any of these are already present in the system Python they are reused (no reinstall). `huggingface-hub` is pinned to `>=0.30,<1.0` to stay compatible with system-installed ML packages.

**Prerequisite auto-install**: `Cmd-Setup` / `cmd_setup` call `Install-Deps` (PowerShell) / `_install_deps` (bash) as their first step, replacing the old `Check-Deps` / `_check_deps` functions that only checked and errored. Both check Python 3.10+ and Node.js 18+ by version number (not just existence) and prompt (`[Y/n]`, default Yes) before auto-installing if missing or outdated; declining either exits with code 1. PowerShell uses `winget install --scope user` (no elevation needed) and refreshes `$env:PATH` from the registry immediately after; bash uses `brew` on macOS and `apt`/`dnf`/`pacman` on Linux for Python, and NodeSource LTS + `nvm` fallback for Node.js on Linux. `pip install -r requirements.txt` is also prompted before running in both `Cmd-Setup`/`Cmd-Update` and `cmd_setup`/`cmd_update`; declining skips the install and setup continues without error (the app will not function without dependencies). In non-interactive mode (redirected stdin), all prompts default to Yes silently and the package listing is suppressed. The `$hasCuda` check in `Install-TorchIfNeeded` is wrapped in `try/catch` (catch is empty — swallows any exception): on a fresh venv before `requirements.txt` is installed, `import torch` fails and Python writes a traceback to stderr; PS5.1 converts that stderr output to a `NativeCommandError` and `$ErrorActionPreference = "Stop"` turns it into a terminating error — without the try/catch this aborts setup. Do not remove the try/catch.

**PyTorch GPU auto-detection**: `manage.ps1 setup` / `manage.sh setup` (and `update`) run `Install-TorchIfNeeded` / `_install_torch_if_needed` **before** `pip install -r requirements.txt`. On Linux/macOS the helper checks three GPU backends in order:

1. **NVIDIA** — skips if `torch.cuda.is_available()` is already True; otherwise checks for `nvidia-smi`, parses the `CUDA Version: X.Y` line, shows the wheel index URL, and prompts (`[Y/n]`) before downloading `torch>=2.0` (~2.5 GB) from the matching PyTorch wheel index (`cu128`, `cu126`, `cu124`, `cu121`, or `cu118`). Declining skips the GPU wheel; CPU-only torch installs later via `requirements.txt`.
2. **AMD ROCm** (Linux only) — detected via `rocm-smi`; ROCm version determined via `rocminfo`, `/opt/rocm-*` dirname, or `/opt/rocm/VERSION` in that order; maps to wheel tag `rocm6.1`, `rocm6.2`, or `rocm6.3`. ROCm < 6.1 falls back to CPU. Prompts before downloading, same as NVIDIA. `manage.ps1` is unchanged (ROCm has no Windows support).
3. **Apple Silicon MPS** (macOS) — no wheel change needed; standard CPU PyTorch already includes MPS support, so setup just prints a message and returns.

If none of the above are detected, CPU-only torch is installed as a fallback.

**ML device abstraction** (`backend/ml/device.py`): centralises all device-detection and device-aware utilities. All ML modules (`model_manager.py`, `aesthetic_scorer.py`, `florence_captioner.py`, `paligemma_captioner.py`, `dino_scorer.py`, `upscaler.py`) import from here — nothing else calls `torch.cuda.*` directly. Detection priority: `cuda` (NVIDIA or ROCm, identical API) → `mps` (Apple Silicon) → `cpu`. Key helpers: `get_device()`, `empty_cache()`, `memory_allocated_bytes()`, `memory_reserved_mb()`, `autocast_ctx()`, `safe_dtype_for_device()`, `is_oom_error()`.

**`manage.ps1` encoding constraint**: PowerShell 5.1 reads `.ps1` files using Windows-1252 by default (no BOM = legacy encoding). Non-ASCII characters in string literals are misread — the UTF-8 byte sequence for an em dash (`E2 80 94`) decodes as `a`, Euro sign, `"` in Windows-1252, and that stray `"` silently terminates the string, corrupting the parser state for the rest of the file. **Never use non-ASCII characters (em dashes, curly quotes, ellipses, etc.) anywhere in `manage.ps1`.** Use plain ASCII equivalents: ` - ` instead of ` — `, `...` instead of `…`, etc. This constraint does not apply to `manage.sh` (bash reads UTF-8 natively) or to any `.md`/`.py`/`.ts` files.

### Upscaling

ML-based image upscaling via the `spandrel` library, which auto-detects architecture from `.pth`/`.safetensors` files (RealESRGAN/RRDB, SwinIR, HAT, OmniSR, and more).

**Router**: `backend/routers/upscaling.py`, prefix `/upscaling`.

| Endpoint | Body / params | Returns |
|---|---|---|
| `GET /upscaling/models` | — | `list[UpscaleModelInfo]` — scans `settings.upscale_models_dir` |
| `POST /upscaling/run` | `UpscaleRunRequest` | `{ job_id, total }` |

**Config**: `settings.upscale_models_dir` (default `models/upscale_models/`). Override with `UPSCALE_MODELS_DIR=` in `.env` (e.g. pointing at a ComfyUI models folder). The directory is created automatically on startup.

**`UpscaleRunRequest`** fields: `dataset_id`, `image_ids` (null = whole dataset), `model_path`, `replace` (overwrite source vs. new file), `target_width`/`target_height` (optional: upscale then resize down to fit, maintaining AR), `subfolder` (null = all; applied only when `image_ids` is null).

**ML inference** (`backend/ml/upscaler.py`):
- `scan_upscale_models(dir)` — globs `*.pth`/`*.safetensors`, detects scale from filename heuristics (`4x-`, `_x4`, `_X4`, etc.), returns `[{name, path, scale}]` without loading weights.
- `upscale_image_sync(src, dest, model_path, replace, target_w, target_h)` — loads via `spandrel.ModelLoader().load_from_file()`, tiles if either dimension > 1024 px (512 px tiles, 64 px overlap, linear-ramp seam blending), optional LANCZOS resize post-upscale.
- Model caching uses `model_manager._registry` under ID `upscale:{abs_path}`; `_ensure_upscaler_loaded` includes a double-check after re-acquiring `_sync_lock` to prevent the TOCTOU double-load race.

**Output modes**: *New file* — filename `{stem}_up{N}x{ext}` (collision-handled via `unique_filename`), new `Image` record created, thumbnail regenerated. *Replace* — updates `width`/`height`/`file_size_bytes`/`updated_at`/`processing_history` on existing record, thumbnail regenerated.

**History management**: Non-replace upscale navigation uses `{ replace: true }` so the source image's history entry is overwritten rather than stacked, leaving a single clean entry so one Back press returns to the gallery. Do not remove the `replace: true` from these `paneGo` calls without considering the double-Back regression.

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

**`LutRunRequest`** fields: `dataset_id`, `image_ids` (null = whole dataset), `lut_path`, `intensity` (0.0–1.0, clamped by validator), `replace` (overwrite source vs. new file), `subfolder` (null = all; applied only when `image_ids` is null).

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

Three performance indexes: `ix_images_dataset_created_at` on `(dataset_id, created_at)` (gallery sort), `ix_images_file_path` on `file_path` (filesystem lookups), `ix_images_dataset_caption` on `(dataset_id, caption_text)` (caption filter + listing).

**Deferred blob columns**: `clip_embedding`, `dino_embedding`, and `dino_layer_embeddings` are declared with `deferred=True` on their `mapped_column`. SQLAlchemy omits them from `SELECT *` queries — they are only fetched when explicitly accessed or when `undefer()` is passed as a load option. The `GET /images/{image_id}` endpoint undefers `dino_layer_embeddings` so the `has_dino_layer_embeddings` property on `ImageOut` works. Quality/similarity routers use column-explicit selects (`select(Image.id, Image.clip_embedding, ...)`) and are unaffected. Never access these columns from a full-row ORM load without adding `undefer()`.

### SSE progress

`ProgressBroadcaster` (singleton in `workers/progress.py`) maintains per-job `asyncio.Queue`s. Emitting a progress event pushes to the job-specific channel and the `"all"` channel. A 25-second heartbeat keeps proxies from closing idle connections. Per-job streams (`GET /jobs/stream/{job_id}`) close when status becomes `completed`, `failed`, or `cancelled`. The global stream (`GET /jobs/stream/all/events`) uses `stop_on_terminal=False` and stays open for the session lifetime. All progress events include `dataset_id` (nullable for jobs with no associated dataset) and `label` (the job's display name, nullable — always taken directly from `job.label` on the in-memory `BackgroundJob` object).

### Frontend state

- **TanStack Query** — all server state (datasets, images, captions, jobs). Query keys follow `["resource", id]` pattern.
- **Zustand stores** — `datasetStore` (active dataset), `selectionStore` (Set of selected image IDs + `datasetByImageId: Map<string, string>` tracking which dataset each selected image belongs to), `jobStore` (Map of active job progress from SSE), `promptPresetsStore` (saved AI prompt presets, persisted to localStorage), `paneStore` (split-view pane layout — see Split view pane manager section), `uploadStore` (active upload progress — see below). `selectionStore.toggle(id, datasetId)` and `selectionStore.selectAll(ids, datasetId)` both require a `datasetId` argument — all callsites (ImageCard, GalleryPage, ImageDetailPage) must pass it.
- **`useJobSSE(jobId)`** — opens `EventSource` for one job, writes progress to `jobStore`.
- **`useAllJobsSSE()`** — opened at app root in `TopBar`, drives the global progress bar.
- **Job label display**: `TopBar` running-job pill shows `runningJob.label || runningJob.message || runningJob.job_type`; pending queue chips show `j.label || j.job_type`. `CaptioningPage` live-progress panel shows the label above the done/total counter (`{jobProgress.label && <div>…</div>}`); its pending-queue list shows `qJob.label || fallbackLabel`. `JobProgress` in `frontend/src/types/index.ts` types `label` as `string | null | undefined` since SSE delivers JSON `null` when no label was set.
- **Job completion → cache invalidation**: pages that trigger background jobs (`QualityPage`, `SelectionToolbar`, `ImageDetailPage`) watch their job ID in `jobStore` via `useEffect` and call `qc.invalidateQueries` when status becomes `"completed"`. Always follow this pattern when adding new job-triggering UI. Additionally, `TopBar` watches all jobs globally and invalidates `["images", dataset_id]` for image-modifying job types (`batch_upscale`, `batch_lut`, `crop_upscale`, `quality_score`) on completion — this catches the case where the user navigates away before the job finishes and the page-local watcher is no longer mounted. `quality_score` completions also invalidate `["duplicates", dataset_id]`.
- **Per-image cache invalidation (captioning)**: Caption SSE events carry `image_id`; `CaptioningPage` invalidates `["images", datasetId]` on every `done` increment so the gallery updates in real-time.
- **SelectionToolbar score modal**: the "Run Scoring" action accepts six boolean toggles — `run_technical`, `run_aesthetic`, `run_watermark` (CLIP zero-shot watermark detection), `run_embeddings` (CLIP embeddings), `run_dino` (DINOv2 embeddings), and `run_dino_layers` (DINOv2 per-layer — only shown/sent when `run_dino` is also checked). `run_watermark`, `run_embeddings`, `run_dino`, and `run_dino_layers` default to `false` since they add significant VRAM/time overhead. The modal also contains a collapsible **Style similarity** section (same controls as `QualityPage` — embedding model buttons, DINOv2 layer picker, `StyleReferencePicker`, Score similarity button) that calls `POST /quality/style-similarity` with `image_ids: ids` so similarity is scoped to the selected images only. Modal state that is session-specific (`showStyleSection`, `selectedRefIds`, `externalRefFiles`) is reset via `useEffect` when `showScore` becomes `false`; scoring preference toggles (`runAesthetic`, `runTechnical`, etc.) persist across open/close cycles.
- **SelectionToolbar caption modal**: the "Caption" action supports all four model types from the `["captioning-models"]` query (same payload as `CaptioningPage`): `local_models`, `ollama_models`, `wd14_models`, and `openai_compat_models`. A module-level `resolveModelId(base, providerModel)` helper assembles the full `openai_compat:{provider_id}:{model_name}` ID at mutation time. WD14 models show only a threshold slider (0–1, default 0.35) — the style picker, custom prompt, `PromptPresetManager`, and `ResolutionPicker` are hidden. OpenAI-compat providers render a `ModelPicker` inline below the selected provider row; `captionProviderModel` state tracks the sub-model. The query is gated on `enabled: showCaption` and the model section shows a loading message while `!modelsData`.
- **SelectionToolbar dataset breakdown**: because selections persist across dataset navigation, the toolbar pill and every action modal header show badge chips for each dataset represented in the current selection — solid style for the current dataset, amber `badge-warn` for images from other datasets. Computed via `useMemo` from `selectionStore.datasetByImageId` + the cached `["datasets"]` query (already fetched, `staleTime: 30_000`). `MoveToDatasetModal` receives this as `sourceInfo?: ReactNode`.
- **QualityPage subfolder scope**: `POST /quality/score` (`ScoreRequest`) accepts `subfolder: str | None`. When set and `image_ids` is absent, the backend filters `Image.subfolder == normalize_subfolder(subfolder)` so only images in that subfolder are scored. `QualityPage` exposes a subfolder `<select>` in the "Run quality analysis" panel header (shown only when subfolders exist, uses the shared `["subfolders", datasetId]` query). `image_ids` (SelectionToolbar path) takes precedence over `subfolder` when both are provided.
- **Upload progress** (`frontend/src/store/uploadStore.ts`): `uploadStore` holds `progress: { datasetId, done, total, errors } | null`. `GalleryPage` uploads files one at a time via `imagesApi.uploadSingle()`, writing progress to this global store after each file. `TopBar` (always mounted) reads the store and renders a `progress-pill` so the indicator persists across navigation. `GalleryPage` additionally shows an inline progress bar below the toolbar, filtered to the current dataset (`globalUploadProgress?.datasetId === datasetId`). `handleDrop` checks `!uploading` before calling `handleUpload` to prevent a drag-drop from starting a second concurrent upload that would clobber the store. Per-file `invalidateQueries` uses `{ cancelRefetch: false }` to coalesce rapid invalidations without cancelling in-flight gallery fetches.

**Thumbnail cache-busting**: `imagesApi.thumbnailUrlVersioned(id, updatedAt)` appends `?v={timestamp}` derived from `image.updated_at` (present on both `ImageListItem` and `ImageOut`). Use this helper — not the raw `thumbnailUrl` — wherever a thumbnail could change due to crop or resize. It is already used in `ImageCard`, `QualityPage`, `StatsPage`, and `StyleReferencePicker`; any new thumbnail `<img>` in those contexts should follow the same pattern.

**Full-image cache-busting**: `imagesApi.fileUrlVersioned(id, updatedAt)` works the same way for the full-resolution image URL. Use it — not the raw `fileUrl` — in any `<img>` or Cropper source that displays the full image in `ImageDetailPage`, so that in-place replacements (replace-crop, replace-upscale) are not served stale from the browser cache.

### Frontend constants

`frontend/src/constants/captionStyles.ts` — `STYLE_LABELS: Record<string, string[]>` (style names per model type — Florence-2 and PaliGemma only; Ollama has no entry so the style picker is hidden for it) and `modelType(model: string): string | null` (maps a model ID to its type key). Shared by `CaptioningPage`, `ImageDetailPage`, and `SelectionToolbar`; do not redeclare locally.

`frontend/src/constants/dinoLabels.ts` — `DINO_LAYER_LABELS: Record<string, string>` mapping layer number (1–12) to a human-readable description. Shared by `ImageDetailPage` and any future UI that shows per-layer DINOv2 scores.

`frontend/src/constants/flags.ts` — `FLAG_OPTIONS: readonly [{key, label}]` mapping each quality flag key to its display label. `FlagKey` is the derived union type. Shared by `ExportPage`, `BulkEditForm`, and `BulkEditPage`; do not redeclare locally.

`frontend/src/constants/providerPresets.ts` — `PROVIDER_PRESETS: Record<string, string[]>` hardcoded model lists keyed by hostname substring (Gemini, Groq, OpenAI, Together.ai). `getPresetsForUrl(baseUrl: string): string[]` parses the URL and returns the matching preset list (or `[]` for unknown/local providers). Used by `ModelPicker` to show a dropdown without requiring a live fetch. Add new cloud provider entries here when preset model lists need updating.

`frontend/src/constants/storage.ts` — `CONFIRM_DEFAULT_KEY`: the `localStorage` key for the user's delete-confirmation default-button preference (`"cancel"` or `"confirm"`). Imported by both `ConfirmDialog` (reads on mount) and `SettingsPage` (reads/writes on toggle). `BRANCH_SNAPSHOT_KEY`: the `localStorage` key for the branch/checkout snapshot behavior preference (`"ask"` or `"auto"`). Read by `BranchSelector` before checkout and branch creation; written by `SettingsPage`. `VERSIONS_BRANCH_KEY`: the `sessionStorage` key prefix (`"versions-branch"`) for the user's last-browsed branch on `VersionsPage`; append `-${datasetId}` for the full key. Written by `VersionsPage.handleBranchSelect` and by `SidebarVersionPanel`'s `onSelect` after checkout; read by `VersionsPage` on mount. `GALLERY_PAGE_SIZE_KEY`: the `localStorage` key for images-per-page in the gallery (`25 | 50 | 100 | 200`); read by `GalleryPage`, `ImageDetailPage` (for prefetch limit and end-of-page detection), and `SettingsPage`. `SUBFOLDER_RENAME_KEY`: the `localStorage` key for the subfolder auto-rename preference (`"on" | "off"`); read by `SelectionToolbar` at mutation time and written by `SettingsPage`. `getGalleryPageSize(): number` — shared helper that reads `GALLERY_PAGE_SIZE_KEY`, guards against `NaN`, and returns the default `100` on parse failure. Use this everywhere the page size is read; never inline the `parseInt` + fallback pattern. Add new storage keys here rather than defining them inline in components.

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

`ImageDetailPage` reads `gallery-nav-*` for arrow-key navigation. At page boundaries it pre-fetches the adjacent page (`useQuery`, `enabled: atEnd / atStart`); on crossing, writes the new context back to `gallery-nav-*` and updates `gallery-state-*` so **Back** returns to the correct gallery page. Arrow keys are suppressed when an `<input>`, `<textarea>`, or `<select>` has focus, or when `isContentEditable` is true. The `Delete` key opens a confirm dialog in both gallery and detail view; both handlers share the same focus guard. The arrow-key handler is additionally suppressed while the delete confirm dialog is open (`showDeleteConfirm`) to prevent background navigation.

**Nav context invariant for newly created images**: When navigating to an image that was just created (crop, upscale new-file), the new image ID is not in the existing `gallery-nav-*` list, so `currentIndex === -1` and arrow keys would silently do nothing. Always call `injectNavId(datasetId, sourceImageId, newImageId)` (defined at module level in `ImageDetailPage.tsx`) before calling `paneGo` to insert the new ID immediately after the source in the nav context. This applies to: sync crop, crop+upscale job completion, and standalone upscale (non-replace) completion. Conversely, call `removeNavId(datasetId, imageId)` when deleting an image from `ImageDetailPage` to remove the stale ID so arrow-key navigation on adjacent images cannot land on it. Both functions delegate to `mutateNavIds(datasetId, transform)` — the shared helper that handles sessionStorage read/parse/write.

**ImageDetailPage crop tool**: Two output modes controlled by the **Replace** checkbox. *New file* (default) — creates a new `Image` record (filename `{source_stem}_crop{ext}`, collision-handled via `unique_filename`) and navigates to it on success. *Replace* — overwrites the source file in-place, updates the existing `Image` record (width, height, file_size_bytes, format, phash), regenerates the thumbnail, and stays on the same image. The aspect dropdown and zoom slider control the crop selection shape and size; W×H inputs control the output pixel dimensions (resize-after-crop, independent of the selection). When both W and H are filled in, the crop box aspect ratio automatically locks to W/H. The crop endpoint (`POST /images/{id}/crop`) accepts `replace: bool = False`; in replace mode it calls `protect_file_before_overwrite` before touching the file. New-file mode uses `asyncio.get_running_loop()` and a targeted `LIKE '{stem}%'` query for collision detection (not a full dataset scan). An optional upscale model selector (shown when upscale models are configured) enables atomic crop+upscale in either mode: the crop is saved to a temp file, a `crop_upscale` background job runs the upscale, and the endpoint returns `{job_id}` instead of the image dict. The frontend branches on `"job_id" in data` to distinguish the async path.

**ImageDetailPage selection**: A **Select / Selected** toggle button sits in the top toolbar (right of the filename, before the Boxes button). It calls `selectionStore.toggle(imageId, datasetId)` and reflects `isSelected(imageId)` via a targeted selector. Pressing **Space** anywhere on the page (except when a text field is focused or a modal is open) does the same thing — handled in the arrow-key `useEffect` alongside ArrowLeft/ArrowRight; `showDetectModal` is additionally checked. The button is styled `btn-primary` + `CheckSquare` icon when selected, `btn-ghost` + `Square` when not.

**ImageDetailPage caption panel**: Contains only the caption text textarea and Save button (plus the collapsible AI Generate section). The `tags` and `caption_style` fields are still present in the DB schema, backend save endpoint (`PATCH /captions/{id}`), and save mutation — they are read from `captionData` and re-persisted unchanged — but neither a tag editor nor a style picker is exposed in the UI. A live **token counter** (`N words · N tokens`) is displayed right-aligned beside the "Caption Text" label, computed via `gpt-tokenizer` (`encode` with GPT-2 BPE) inside a `useMemo` keyed on `captionText`. The counter turns amber at ≥ 70 tokens and red at ≥ 77 to signal the CLIP truncation limit.

The **AI Generate** collapsible (`showAi` state, gated `enabled: showAi`) uses the same four-model-type picker pattern and `resolveModelId` helper as `SelectionToolbar`. WD14 models show only the threshold slider and hide `PromptPresetManager` and `ResolutionPicker` (both are wrapped in `{aiModel && !aiModel.startsWith("wd14:") && (...)}` — keep this consistent with `SelectionToolbar`'s ternary). An `aiOverwrite: bool` state (default `true`) is exposed as a checkbox that appears once a model is selected. The `["captioning-models"]` query is defined at component level (not inside the collapsible) but gated on `enabled: showAi` to avoid loading until the section is first opened.

### Datasets page

`DatasetsPage` uses `queryKey: ["datasets"]` with `staleTime: 0` so the list is always refetched on mount.

**Preview strip**: `GET /datasets/` (`DatasetOut`) accepts optional `skip` (default 0) and `limit` (default 0 = no limit) query params for pagination. Includes `preview_image_ids: list[str]` — up to 8 image IDs fetched in a single batch query alongside the datasets list. The card renders these as `<img src="/api/v1/images/{id}/thumbnail">` tiles. When a dataset has no images the strip falls back to deterministic colour gradients.

**Sort control**: a `<select>` in the page header lets the user sort the dataset list. Sorting is frontend-only (`useMemo` on the already-loaded list). Options: Newest (default) / Oldest / Recently updated / Name A→Z / Name Z→A / Most images / Fewest images / Largest / Smallest / Most captioned %. Sorting applies within each category section when grouped, not across sections.

**Category groups**: `Dataset` has a nullable `category: str` column (default `""`). When at least one dataset has a non-empty category, the page switches from a flat grid to a **folder-sectioned layout**:
- Each category renders a collapsible section: folder icon + name + count badge + chevron. Section collapse state is `useState<Set<string>>`.
- Datasets with no category appear at the bottom in a muted "(Uncategorized)" section.
- When all datasets have `category = ""` the flat grid is restored.
- **Rename category**: hover the section header to reveal a pencil button. Clicking enters an inline rename form (input + Enter/Escape). On confirm, `renameCategoryMutation` batch-PATCHes all affected datasets via `Promise.all`. Invalidates `["datasets"]` on both success and error (to recover from partial failures).
- **Delete category**: hover to reveal a trash button. `ConfirmDialog` → `deleteCategoryMutation` batch-PATCHes `category: ""` on all affected datasets. Invalidates `["datasets"]` on both success and error.
- The "Uncategorized" section has no rename/delete buttons.

**`CategoryPicker` component** (`DatasetsPage.tsx`, module-level): used in both Create and Edit modals. Renders a `<select>` showing existing categories + "(None)" + "New category…"; choosing "New category…" reveals a text input below. Because the component is always inside a conditionally-rendered modal it remounts on each open — `useState(!inExisting)` init is sufficient; no sync `useEffect` is needed or present.

**Import job tracking**: after starting an import (`POST /datasets/{id}/import`) `DatasetsPage` stores the returned `job_id` and watches it in `jobStore` via `useEffect`. The `["datasets"]` query is invalidated only when the job status becomes `"completed"` — not when the job is created — so image counts update after the import actually finishes.

**Import subfolder options**: the import modal accepts `subfolder` (target logical subfolder, empty = root) and `preserve_structure: bool` (when true, recursively walks the source folder and maps each subdirectory level to a logical subfolder matching the relative path; when false, all images land in the specified `subfolder`). Both are passed in the `POST /datasets/{id}/import` body as `DatasetImportWithOptions`.

**Card navigation**: Dataset card clicks use `usePaneNavigate().go(url, view)` (not raw `useNavigate`) so that clicking a dataset inside a split pane updates that pane's view rather than the URL. Do not revert to `useNavigate` here.

**Drag-and-drop upload**: `GalleryPage` supports dropping onto the grid (`onDragEnter`/`onDragLeave`/`onDrop` on the scroll container) — works. `DatasetsPage` has the plumbing (native `dragover`/`drop` via `useEffect` on `pageRef`, `data-dataset-id` attrs, `dragOverId` state) but drop does not trigger reliably — **TODO: debug and fix**. Already tried: React synthetic events, `onDragOver`-based debounce timer, `addEventListener` with `elementFromPoint`.

**Dataset folder naming**: `create_dataset()` in `dataset_service.py` derives the folder name from the dataset name via `_name_to_slug()` (lowercase, spaces → underscores, special chars stripped, max 80 chars) rather than using the UUID. The UUID is still the DB primary key. If the slug folder already exists (name collision edge case), a `{slug}_{uuid8}` suffix is appended. Example: dataset named `"My Portraits"` creates `data/datasets/my_portraits/`.

**Dataset edit**: the pencil (Edit) button on each dataset card opens a modal for editing the name, description, and category. `PATCH /datasets/{id}` accepts `{ name?, description?, category? }`. When the name changes, `rename_dataset()` renames the folder on disk, bulk-updates all `Image.file_path`/`thumbnail_path` records via string prefix replacement, and updates `Dataset.folder_path`/`name` — all in one transaction. Returns 400 on name conflict. The Save button is enabled when any field differs from the current values.

**Dataset duplicate**: the copy icon button on each card opens a Duplicate modal. `POST /datasets/{id}/duplicate` is a background job that returns `{job_id}` immediately; `DatasetsPage` watches the job in `jobStore` and invalidates `["datasets"]` on completion. The new dataset inherits the source's description, category, and declared subfolders; blob columns (`clip_embedding`, `dino_embedding`, `dino_layer_embeddings`) are not copied. When versioning is enabled and the source dataset has branches, the modal shows a branch + version dropdown; choosing a specific snapshot copies from the object store (using the snapshot's `file_hash` if present, otherwise the current file path). `datasetsApi.duplicate(id, newName, sourceVersionId?)` in `frontend/src/api/datasets.ts`. Backend: `duplicate_dataset()` in `dataset_service.py`; `DatasetDuplicateRequest` schema (`new_name`, `source_version_id?`) in `backend/schemas/dataset.py`.

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

**`DatasetStats` subfolder invariant**: When subfolder is not None, all out-of-row-scan queries in `get_dataset_stats()` must include `.where(Image.subfolder == subfolder)`: (a) embedding count, (b) score coverage (`func.count` per score column), (c) quality flag counts (`json_extract` + `SUM(CASE …)` per flag key). `total_size_mb` derives from the filtered `file_sizes_mb` list; `ds.total_size_bytes` only used for the all-images case.

**`DatasetStats` schema** (`backend/schemas/dataset.py`; all computed in one row-scan in `dataset_service.get_dataset_stats()`):

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

**CSV export**: Two buttons in the page header export data without any backend call — all data is already loaded in the component.

- **Export Stats CSV** (`downloadCsv`) — disabled while the `["score-values"]` query is loading (`svLoading`). Produces a two-column key-value CSV with labeled section headers (`## SECTION NAME,`). Sections: SUMMARY (dataset_id, dataset_name, image_count, captioned_count, caption_coverage_pct, total_size_mb, avg_width, avg_height), FILE SIZE SUMMARY (min_mb, median_mb, p95_mb, max_mb), QUALITY FLAGS, SCORE COVERAGE, MEAN SCORES (computed from `sv` arrays), and one section per histogram distribution (aesthetic, blur, noise, uniformity, watermark, color, saturation, style similarity, megapixels, file size, aspect ratio, format, caption word count, caption tokens). Aspect ratio falls back to `aspect_ratio_distribution` (coarse) when `aspect_ratio_fine` is empty — matches the chart's own fallback. Distribution key names are sanitized with `.replace(/[^a-z0-9]/gi, "_").replace(/^_+|_+$/g, "").toLowerCase()` (the final strip removes trailing underscores produced by labels ending in `+`, e.g. `"21:9+"` → `aspect_ratio_21_9` not `aspect_ratio_21_9_`). `dataset_name` is passed through `escapeCsv()` since it is a user-controlled string. Filename: `dataset-{name}-stats.csv` (name sanitized via `safeFilename()`; falls back to `{id}` if name is empty).
- **Export Tags CSV** (`downloadTagsCsv`) — disabled when `tagStats.length === 0`. Produces a proper tabular CSV with header row `tag,count,category`; tag and category values are run through `escapeCsv()` (quotes fields containing `,`, `"`, or newlines). Filename: `dataset-{name}-tags.csv` (same sanitization/fallback as stats export).

`escapeCsv(v)` wraps the value in double-quotes and escapes internal double-quotes (`"` → `""`) when the value contains any of `,`, `"`, `\n`, `\r`. Used for `dataset_name` in `downloadCsv` and for all tag/category values in `downloadTagsCsv`. `safeFilename(name)` strips characters illegal in filenames (`/\:*?"<>|`) before the name is used in a `triggerDownload` filename — use it whenever a user-supplied string appears in a download filename. `triggerDownload(csv, filename)` is the shared blob-download helper (Blob → `URL.createObjectURL` → synthetic `<a>` click → `URL.revokeObjectURL`).

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

The Settings page uses a **tab-based layout** with five tabs. All localStorage-backed preferences take effect immediately (no Save button); the quality thresholds and versioning mode require an explicit Save.

**Gallery tab** — two immediate-save preferences:
- Images per page (`25 | 50 | 100 | 200`). Stored under `GALLERY_PAGE_SIZE_KEY`. Read by `GalleryPage` (gallery list limit) and `ImageDetailPage` (end-of-page detection + prefetch limit for cross-page arrow-key navigation). Parse and default via `getGalleryPageSize()`.
- Subfolder rename on move (`on | off`). Stored under `SUBFOLDER_RENAME_KEY`. Read by `SelectionToolbar`'s `moveSubfolderMutation` at mutation time; passed as `rename_on_move` to `POST /images/batch/move-subfolder`.

**UI Behavior tab** — two immediate-save preferences:
- Delete-confirmation default button (`cancel` / `confirm`). Stored under `CONFIRM_DEFAULT_KEY`. Read by `ConfirmDialog` on every mount when `danger=true` and no `defaultFocus` prop is provided.
- Branch snapshot behavior (`ask` / `auto`). Stored under `BRANCH_SNAPSHOT_KEY`. When `"ask"`, `BranchSelector` shows an inline prompt before checkout or branch creation letting the user choose whether to create a snapshot. When `"auto"`, snapshots are always created without prompting.

**Quality Thresholds tab** — five editable number inputs (blur, noise, uniformity, watermark, duplicate thresholds). Requires Save; changes apply to the next scoring run only.

**Versioning tab** — version control mode radio (`off | manual | auto`) plus branch snapshot behavior radio. Requires Save for the version control mode; branch snapshot behavior is immediate (localStorage).

**LLM Providers tab** — manage OpenAI-compatible provider configurations (see OpenAI-compatible providers section). Add / edit / delete providers. Name and Base URL are required; changes are saved immediately per-mutation (no page-level Save). Provider mutations also invalidate `["captioning-models"]` so the model picker on CaptioningPage reflects changes immediately.

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

### System hardware stats

Router: `backend/routers/system.py`, two endpoints both mounted at `/api/v1/system`.

**`GET /system/gpu`** returns `{ name, used_mb, total_mb, utilization_pct }` by trying three external sources in priority order: (1) `nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits` for NVIDIA GPUs; (2) `rocm-smi --showmeminfo vram --csv` for AMD ROCm (ROCm 6.x CSV: `device,VRAM Total Memory (B),VRAM Total Used Memory (B)` — GPU name falls back to device ID e.g. `card0`); (3) `torch.mps.current_allocated_memory()` / `torch.mps.driver_allocated_memory()` for Apple Silicon (name = `"Apple Silicon (MPS)"`, `utilization_pct = null` since unified memory has no fixed GPU partition). Returns `{ name: null }` when all three fail. **Do not revert to `torch.cuda.memory_allocated()` or `torch.cuda.mem_get_info()` here** — both are per-process CUDA context reads that miss VRAM allocated by other processes (e.g. Ollama).

**`GET /system/cpu-ram`** returns `{ cpu_pct, ram_used_mb, ram_total_mb }` via `psutil` (`psutil>=5.9` in `requirements.txt`). Both `psutil.cpu_percent(interval=0.1)` and `psutil.virtual_memory()` are run together inside `asyncio.get_running_loop().run_in_executor()` to avoid blocking the event loop (both calls perform blocking I/O — `/proc/meminfo` on Linux, `GetPerformanceInfo` on Windows). Wrapped in `try/except`; returns `{ cpu_pct: 0.0, ram_used_mb: 0, ram_total_mb: 0 }` on any failure.

**Frontend**: The Sidebar footer (`frontend/src/components/layout/Sidebar.tsx`) renders three stacked hardware meters — CPU, RAM, and GPU — using a shared `MeterRow` helper component (defined in the same file). CPU and RAM are driven by `useCpuRamStats` (`frontend/src/hooks/useCpuRamStats.ts`); GPU is driven by `useGpuStats` (`frontend/src/hooks/useGpuStats.ts`). Both hooks poll every 5 s via TanStack Query with `retry: false`. In-loop SSE progress emitters (captioning, detection) use `_device.memory_reserved_mb()` from `backend/ml/device.py` — subprocess overhead is unacceptable inside the per-image inference loop, and those emitters only cover PyTorch-loaded models anyway.

### Captioning post-processing

`CaptionJobRequest` (in `backend/routers/captioning.py`) accepts three post-processing flags, one WD14-specific field, and a job label:

| Field | Default | Effect |
|---|---|---|
| `label` | `null` | Optional display name shown in the job queue. When omitted, the router auto-generates `"{model_short} — N images"`. |
| `strip_refusals` | `true` | Remove common AI refusal phrases from generated captions via `_REFUSAL_RE` compiled regex. |
| `save_backup` | `false` | Before calling `set_caption`, write the existing `.txt` sidecar to `.txt.bak`. |
| `rename_on_caption` | `false` | After saving each caption, rename the image file to `{subfolder_slug}_{NNN}.ext` (or `image_{NNN}.ext` for root). Sets `is_auto_named=True`. Subfolder and original filename are fetched from the initial bulk query — no per-image DB round-trip. |
| `wd14_threshold` | `0.35` | Minimum confidence (0–1) for a WD14 tag to be included in output. Only used when `model` starts with `wd14:`. |

**Captioning job execution**: `_run` in `routers/captioning.py` processes images one at a time (generate → save → emit SSE). Each event carries `image_id`, `throughput_ips`, and `vram_used_mb` (sampled every 10 images; Ollama always 0; WD14 and OpenAI-compat always 0). Failed images accumulate in `failed_image_ids`; a `caption_summary` SSE event is emitted after the loop if any failed. Cancellation is checked at each image boundary via a scalar `SELECT status` on the outer session (not a new `AsyncSessionLocal` per image).

**Job queuing**: The backend `asyncio.Queue` in `workers/job_queue.py` runs caption and pipeline jobs serially — any number of jobs may be submitted while one is running. `enqueue()` emits a `"pending"` SSE event immediately after putting the job on the queue (before the worker picks it up), so the frontend knows the job exists right away. The worker checks the DB status after dequeuing; if a job was cancelled while pending it emits a `"cancelled"` SSE event and skips without running. The cancel endpoint (`DELETE /jobs/{id}`) accepts both `"running"` and `"pending"` status. The frontend tracks all submitted job IDs in `submittedJobIds: string[]`; `submittedActiveJobId` is the oldest non-terminal entry (gated only by `seenTerminalJobIds` ref, not live store status, to avoid the effectiveJobId race where the gallery invalidation never fires). `otherPendingJobs` (the queue list displayed in the CaptioningPage live-progress panel) is derived from `allActiveJobs` (the persistent Zustand store), not from `submittedJobIds`, so the list survives navigation/remount. `globalCaptionJob` (the fallback when `submittedActiveJobId` is null) also includes `"pending"` status for the same reason. Cancelling a pending job in either `CaptioningPage` or `TopBar` calls `useJobStore.getState().updateJob(id, { status: "cancelled" })` optimistically before the API call so the job disappears from the queue list immediately without waiting for an SSE event.

**Ollama timeout**: `httpx.AsyncClient` in `ollama_captioner.py` uses a 300-second timeout per image to accommodate slow hardware and cold model loads.

### OpenAI-compatible providers

**Router**: `backend/routers/providers.py`, prefix `/providers`. Registered in `main.py`. No service layer — CRUD is thin enough to live in the router.

| Endpoint | Behaviour |
|---|---|
| `GET /` | List all providers ordered by `created_at` |
| `POST /` | Create provider; 409 on duplicate name |
| `PATCH /{id}` | Update any fields; `exclude_none=True` so omitted fields are not cleared |
| `DELETE /{id}` | Hard delete |
| `GET /{id}/models` | Calls provider's `/v1/models` endpoint via `openai.OpenAI` with 5-second timeout; returns `{"models": []}` on any error (provider offline, auth failure) — never raises |

**Model**: `backend/models/openai_provider.py` — `OpenAIProvider` table. Fields: `id` (UUID), `name` (unique), `base_url`, `api_key` (stored plaintext), `default_model`, `max_image_px` (128–4096, default 1024 — image is JPEG-encoded at this resolution before sending), `max_tokens` (64–32768, default 2048), `created_at`.

**Schema**: `OpenAIProviderOut` masks the API key (last 4 chars visible) and adds a computed `is_remote: bool` — true when the base URL hostname is not `localhost`/`127.0.0.1`/`::1`. Remote providers show a warning banner in the CaptioningPage and Settings form.

**Captioner**: `ml/openai_compat_captioner.py` — `caption_image(image_path, base_url, api_key, model_name, style, custom_prompt, max_px, max_tokens, target_w, target_h)`. Encodes the image as JPEG base64 (after `preprocess_for_caption` and optional `max_px` downscale), sends via `openai.ChatCompletion` with a `image_url` content block. 120-second per-image timeout. Not tracked by `model_manager`.

**Model ID format** in captioning: `openai_compat:{provider_id}:{model_name}`. The router splits on `:` with `maxsplit=2` to recover `provider_id` and `model_name`. If `model_name` is empty, `openai_provider.default_model` is used.

**Settings UI**: Settings page → "LLM Providers" tab. Add/edit/delete providers. The "Default model" field uses `ModelPicker` (preset dropdown for well-known cloud APIs; fetch button for local servers). Provider mutations also invalidate `["captioning-models"]` so CaptioningPage model list updates immediately.

**`ModelPicker` component** (`frontend/src/components/providers/ModelPicker.tsx`): Props `{ value, onChange, providerId?, baseUrl?, placeholder? }`. On mount with `providerId`, auto-fetches models via `providersApi.fetchModels(providerId)`. Computes `presets = getPresetsForUrl(baseUrl)` from `providerPresets.ts`. When both are empty: plain text input. When either has entries: `<select>` with a "Custom…" sentinel + optional text input below for custom entry. When `value` is not in the list, the select shows "Custom…" and the text input is pre-filled. Explicitly selecting "Custom…" sets local `showCustom: bool` state so the text input appears even when the current value is a known model; selecting any list item resets it. A `useEffect` on `[value, allModels]` also resets `showCustom` whenever `value` becomes a known model (e.g. parent switches providers and passes a new default), preventing the select from staying stuck on "Custom…" after an external value change. A refresh button (↻) allows manual re-fetch. `fetchError` shows a muted "Could not reach provider" message on empty result.

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
| `subfolder` | `null` | If set, restrict to images in this subfolder (ignored when `image_ids` is provided) |

Images with no `caption_text` are skipped for `remove` and `find_replace`. For `prepend`/`append` they receive just the added text. A single `db.commit()` is made after the loop — not per image.

### Bulk image operations (rename / delete / count)

Three endpoints in `backend/routers/images.py` share a common `_apply_bulk_filters(query, image_ids, subfolder, quality_flags)` helper (module-level private function) that applies the triple filter — `image_ids` takes precedence over `subfolder`; `quality_flags` always applies as exclusion. All three accept a `BulkFilterBase`-derived schema (`backend/schemas/image.py`).

`BulkFilterBase` fields (shared by all three schemas): `dataset_id`, `image_ids: list[str] | None`, `quality_flags: list[str] | None`, `subfolder: str | None`.

| Endpoint | Extra fields | Returns |
|---|---|---|
| `POST /images/bulk-count` | — | `{ count: int }` — count of matching images without making any changes |
| `POST /images/bulk-rename` | `new_stem: str` | `{ affected: int }` — renames matching images to `{slug}_001.ext`, `_002`, … Uses `slugify_filename` + `unique_filename`; pre-plans all renames before touching the filesystem; DB updated via ORM bulk-by-PK executemany then `rename_with_sidecar` per file; sets `is_auto_named=True` |
| `POST /images/bulk-delete` | — | `{ deleted: int }` — permanently deletes matching images; calls `mark_image_deleted_in_versions` per image for versioning hooks; unlinks image, `.txt` sidecar, and thumbnail; calls `refresh_stats` |

**Frontend surfaces**:
- `SelectionToolbar` — **Edit** button (pencil icon) opens a modal with `<BulkEditForm imageIds={selectedIds} />`. On success, invalidates `["images", datasetId]` and clears the selection.
- `BulkEditPage` (`/datasets/:datasetId/bulk-edit`, sidebar "Bulk Edit") — five tabs: *Edit Captions*, *Upscale*, *Apply LUT*, *Rename*, and *Delete*. All tabs share the same scope radio (*All images* / *Exclude images with quality flags* / *Currently selected*) and a **Subfolder** filter dropdown (shown when subfolders exist; hidden for the "Currently selected" scope). A `POST /images/bulk-count` query fires on every scope/flag/subfolder change and shows "N images will be affected" at the bottom of the scope panel. The "Exclude flags" scope requires at least one flag to be chosen before the form can submit.

`BulkEditForm` (`frontend/src/components/caption/BulkEditForm.tsx`) — reusable form. `qualityFlags` prop: uses those flags and hides the internal selector; when omitted the internal selector is shown. `disabled` prop prevents submission (used by `BulkEditPage` when scope is "flags" but nothing is selected).

`BulkRenameForm` (`frontend/src/components/image/BulkRenameForm.tsx`) — base-name input with live slug preview (`{slug}_001.ext, …`); `useMutation` → `imagesApi.bulkRename`; on success invalidates `["images", datasetId]`.

`BulkDeleteForm` (`frontend/src/components/image/BulkDeleteForm.tsx`) — amber warning panel + danger button; `useMutation` → `imagesApi.bulkDelete`; on success invalidates `["images", datasetId]` and calls `selectionStore.clear()`.

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
- `frontend/src/pages/VersionsPage.tsx` — route `/datasets/:datasetId/versions`, sidebar "Versions". Shows disabled-state when `versioning_mode="off"` (link to Settings). Otherwise shows branch selector, filter bar (debounced search + date range), version list with source badges (`Manual`/`Pre-restore`/`Branch init`) and pin icon per card. Pin toggle uses `setQueryData` optimistic update + client-side re-sort (no refetch). Active branch persisted to `sessionStorage` under `VERSIONS_BRANCH_KEY-${datasetId}`; falls back to `dataset.current_branch_id`, then `branches[0]`. `resolvedBranchId = activeBranch?.id` is passed to `BranchSelector` (not raw `activeBranchId`) so the dropdown stays in sync after restarts. A `useRef`+`useEffect` watches `dataset.current_branch_id` post-mount; the guard (`prev !== undefined`) prevents the initial data load from clobbering the stored preference.
- `frontend/src/components/versioning/CreateSnapshotModal.tsx` — name + description inputs; shows `JobProgressBar` during bg job; passes `activeBranchId` in the snapshot body so new snapshots land on the correct branch.
- `frontend/src/components/versioning/RestoreConfirmModal.tsx` — keep/remove radio for extra images, pre-restore snapshot checkbox, file-unavailability warning, `JobProgressBar`
- `frontend/src/components/versioning/DiffModal.tsx` — select two versions; shows Added/Removed/Modified sections with field-level changes
- `frontend/src/components/versioning/BranchSelector.tsx` — branch `<select>` + "New branch…" option. Checkout and branch creation show a `SnapshotPrompt` dialog first when `BRANCH_SNAPSHOT_KEY === "ask"`. Checkout triggers a bg job; a fixed-position progress card appears bottom-right; `onSelect` fires only after job completion to avoid stale data. For sync branch creation (≤100 images), `doCheckout(result.id, false)` is called immediately so `current_branch_id` updates — `pre_restore_snapshot=false` because the branch was just created. A trash icon opens a delete modal with its own `<select>` (excluding `currentBranchId`); the active branch cannot be deleted.
- `frontend/src/components/common/JobProgressBar.tsx` — shared progress bar (message + animated fill bar); used by snapshot, restore, and branch-checkout flows
- `frontend/src/api/versioning.ts` — `versioningApi`: `listBranches`, `createBranch(datasetId, name, fromVersionId?, includeSnapshot = true)`, `checkoutBranch(datasetId, branchId, preRestoreSnapshot = true)`, `deleteBranch(datasetId, branchId)`, `listVersions` (accepts `ListVersionsParams` object with `branchId`, `search`, `createdAfter`, `createdBefore`), `createSnapshot`, `getVersion`, `deleteVersion`, `updateVersion` (PATCH for `is_pinned`), `restoreVersion`, `diff`. `createSnapshot`/`createBranch` return `Version | { job_id: string }` — discriminate with `"job_id" in data`.
- **Sidebar version panel** (`SidebarVersionPanel.tsx`) — accordion below "Active dataset" label. Collapsed: branch name · head snapshot + chevron. Expanded: `<BranchSelector>`, the 7 most recent snapshots with `[Restore]` buttons (current shows "Now"), and "View all →" link. Snapshot query (`["versions", datasetId, activeBranch.id, "sidebar"]`) gated on `expanded` with `limit: 7`. `onSelect` writes `VERSIONS_BRANCH_KEY-${datasetId}` to sessionStorage; `onSuccess` after restore invalidates branches, dataset, images, captions, and versions queries. Only rendered when `activeBranch` is defined.

**TanStack Query keys**:
- `["branches", datasetId]` — invalidated after snapshot creation, restore, checkout
- `["versions", datasetId, resolvedBranchId, search, createdAfter, createdBefore]` — invalidated after snapshot creation, delete, restore; pin toggle uses `setQueryData` instead of invalidation
- `["images", datasetId]` — invalidated after restore and after checkout (image set and captions change)
- `["image"]` — prefix invalidation (no imageId) after restore and checkout; clears all cached image detail pages so `ImageDetailPage` refetches immediately
- `["caption"]` — prefix invalidation after restore and checkout; clears all cached caption data
- `["dataset", datasetId]` — invalidated after restore and checkout (image count, `current_branch_id`)

**`DatasetVersion` fields**: `id`, `dataset_id`, `branch_id`, `parent_id`, `name`, `description`, `image_count`, `created_at`, `source` (`Literal["manual", "pre_restore", "branch_init"]`), `is_pinned` (`bool`).
