# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Launch

**Windows**: `manage.ps1 <cmd>` (or double-click `Crucible.bat`) | **Linux/macOS**: `./manage.sh <cmd>` (run `chmod +x manage.sh` once)

| Command | Purpose |
|---|---|
| `setup` | First time — creates venv, installs deps (GPU PyTorch auto-detect + optional SAM2), builds frontend |
| `start` | Production: runs migrations, rebuilds frontend if needed, serves on :8000 |
| `update` | `git pull` → update pip deps (GPU PyTorch auto-detect) → optional SAM2 update → `npm install` → rebuild frontend |
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

This file covers conventions that apply across the whole codebase: commands, the request/job data flow above, the key invariants and shared utilities below. Subsystem-specific details — ML models and inference, gallery/image handling, captioning, export and bulk operations, dataset versioning, dashboard pages, frontend state/pane management, and backend infrastructure/environment setup — live in topic files under `docs/dev/`, indexed in the Documentation Map near the end of this file. Read the relevant topic file(s) with the Read tool before working on that subsystem; do not read all of them up front.

### Shared utilities

`backend/utils.py` — thin module for helpers shared across multiple routers. Currently contains:

- `normalize_subfolder(s: str) -> str` — strips leading/trailing slashes and `.` segments, rejects `..` with HTTP 400. Import this; never copy the logic inline or re-import it from a router.
- `slugify_filename(name: str) -> str` — lowercases, removes non-word characters, collapses whitespace/underscores/hyphens to `_`, strips leading/trailing `_-`, truncates to 200 chars. Returns `"image"` if the result is empty.
- `unique_filename(directory: Path, stem: str, suffix: str, db_names: set) -> str` — returns a filename not on disk and not in `db_names`. Tries `{stem}{suffix}` first, then `{stem}_001{suffix}`, `_002`, … Both checks are required: `db_names` covers in-flight batch collisions within the same request; the filesystem check covers files that exist but have no DB record.
- `unique_filename_with_thumb(images_dir, stem, suffix, db_names, occupied_thumb_stems, planned_thumb_stems) -> str` — like `unique_filename` but also avoids thumbnail-stem collisions. Thumbnails are always `.webp` keyed by image stem, so two images with different extensions but the same stem would share a thumbnail path. Call this instead of `unique_filename` in every code path that creates or renames an image file and associates a thumbnail with it. Mutates `db_names` (adds the chosen filename) and `planned_thumb_stems` (adds the chosen stem) so subsequent calls within the same batch stay consistent. Build `occupied_thumb_stems` from `thumb_dir.glob("*.webp")` once before the loop; do **not** exclude the stems of images being renamed/moved from this set — doing so re-introduces the within-batch clobber bug where one image's new thumbnail path matches another's current path.
- `rename_with_sidecar(old_path: Path, new_path: Path) -> None` — renames a file and its `.txt` sidecar (if present) in one call. Use this everywhere a file rename happens; never copy the two-step pattern inline.
- `copy_with_sidecar(old_path: Path, new_path: Path) -> None` — copies a file and its `.txt` sidecar (if present) using `shutil.copy2`. Use this in any copy path; mirrors `rename_with_sidecar` but leaves the source intact.
- `read_caption_sidecar(image_path: Path | str) -> str | None` — reads the `.txt` caption sidecar next to an image (the read-side counterpart of `caption_service._write_txt_sidecar`). Returns the stripped text of `{stem}.txt` if present and non-empty, else `None`. Use everywhere a sidecar caption is read (folder import, rescan/sync, standalone caption import); never inline the `.with_suffix(".txt")` logic. See `docs/dev/gallery-and-images.md` (§ Importing captions & folder rescan).
- `ALLOWED_FLAG_KEYS: frozenset` — the canonical set of valid quality flag names (`is_blurry`, `is_noisy`, `is_uniform`, `has_watermark`, `is_duplicate`, `is_nsfw`, `has_ai_artifacts`). Import this wherever flag names must be validated or used in SQL filters; never redefine the set locally.
- `normalize_image_format(suffix: str, out_path: str) -> tuple[str, str]` — normalises a file suffix to a PIL format name (`JPG`→`JPEG`, unsupported→`PNG`). Returns `(fmt, out_path)` — `out_path` may be updated when the format falls back to PNG (extension changes). Use in any image-save path; do not inline the JPG/PNG fallback logic again.
- `image_save_kwargs(fmt: str) -> dict` — returns PIL `save()` kwargs for the given format (JPEG → `{quality: 95, subsampling: 0}`; others → `{}`). Use alongside `normalize_image_format`.
- `thumbnail_path_for(image_path: Path | str) -> str` — derives the `.webp` thumbnail path for an image sitting in a dataset `images/` folder (`parent.parent/thumbnails/{stem}.webp`). Use in any router that creates or regenerates thumbnails; never reconstruct the path manually.
- `subsume_tags(tags: list[str]) -> list[str]` — order-stable tag dedup: drops any tag that is a whole-word subsequence of a longer tag in the same list (`tail` when `long tail` present) and collapses case-insensitive exact duplicates. Whole-word matching means `car` does not subsume `scar`/`carpet`. Single source of truth for the captioning `dedupe_tags` post-processing flag and for the per-caption subsumption cleanup exposed via the `tag-consolidation` router's `subsume` endpoint (the Consolidate Tags page "Quick cleanup", the `SelectionToolbar` "Merge tags" action, and the `ImageDetailPage` per-image button) — never reimplement the rule. See `docs/dev/tag-consolidation.md`.

**Shared frontend components**: `SelectionToolbar`, `MoveToDatasetModal`, `ConfirmDialog`, `GenerationMetadata`, and `JobProgressBar` are reusable components referenced from multiple subsystem doc files below — don't be surprised when the same component name recurs across files. Each is documented where it's most central: `SelectionToolbar`'s modal/cache-invalidation conventions and `ConfirmDialog` in `docs/dev/frontend-core.md` (§ Frontend state and § Styling respectively); `MoveToDatasetModal` (§ Image file naming) and `GenerationMetadata` (§ AI generation metadata) in `docs/dev/gallery-and-images.md`; `JobProgressBar` in `docs/dev/versioning.md` (§ Dataset versioning, Frontend). Other files document only how that subsystem *uses* them.

### Key invariants

- **DB-before-filesystem for batch rename/move.** `bulk_rename` and `batch_move_dataset` commit DB changes *before* the filesystem renames so the DB is always the authoritative record of intended file locations. If a rename fails mid-batch, the DB reflects the final intended state; do not revert this ordering. `batch_copy_dataset` uses the opposite ordering (stage DB inserts, do copies, then commit) because an incomplete copy should leave nothing — see the cross-dataset copies note above.
- **Always `ImageOps.exif_transpose()` first.** Every Pillow operation in `image_service.py` calls this before anything else to correct orientation from EXIF data.
- **Close PIL Images after preprocessing.** In all ML inference paths (`aesthetic_scorer.py`, `dino_scorer.py`, all captioners) call `img.close()` immediately after the image has been passed to the model's preprocessor/processor — before the GPU inference runs. In `export_service.py::_write_image()` use a `try/finally` block. This frees the decoded pixel buffer (potentially several MB per image) during slow inference and prevents accumulation across large batches.
- **Absolute DB path.** `config.py` derives the database URL from `Path(__file__).parent.parent` so it resolves correctly regardless of the working directory when uvicorn is launched.
- **Path traversal guard.** `_safe_path()` in `routers/images.py` validates that resolved file paths stay within `settings.datasets_dir`.

## Documentation Map

Each file below covers one subsystem in depth. Read the relevant file(s) with the Read tool
when your task touches that subsystem — do not read all of them up front. Do NOT use `@`-paths
to reference these files anywhere — `@path` syntax causes Claude Code to auto-load the target
file into every conversation, defeating the purpose of this split. Use plain relative paths.

| File | Contents | Read this when... |
|---|---|---|
| `docs/dev/ml-models.md` | Model manager (VRAM/unload), model ID registry, JoyCaption/Florence-2 details, quality scorers, object detection, upscaling, LUT grading, device abstraction, TorchDynamo, config validation | Working on captioning models, quality scoring, object detection, upscaling, LUT grading, or `backend/ml/` |
| `docs/dev/gallery-and-images.md` | Image naming/renaming/collisions, gallery selection/filters/subfolder sidebar, manual drag ordering, gallery navigation state (incl. ImageDetailPage crop/selection/caption panel), generation metadata | Working on `GalleryPage`, `ImageDetailPage`, image upload/move/copy/rename, or generation-metadata display |
| `docs/dev/captioning.md` | Captioning post-processing (delimiter modes, refusal stripping, rename-on-caption), pipeline job execution, OpenAI-compatible provider config and ModelPicker | Working on `CaptioningPage`, the caption job pipeline, or LLM provider integration |
| `docs/dev/export-and-bulk-ops.md` | Bulk caption find/replace/regex, bulk image rename/delete/count, dataset export (kohya/ai-toolkit/plain, filters, resize, metadata stripping) | Working on `ExportPage`, `BulkEditPage`, or any `bulk-*` endpoint |
| `docs/dev/tag-consolidation.md` | Dataset-wide semantic tag consolidation: MiniLM tag embedder, analyze/apply background jobs, whole-tag (non-substring) rewrite, `TagConsolidatePage` preview/confirm UI | Working on `TagConsolidatePage`, the `tag-consolidation` router, `tag_embedder`, or per-image `dedupe_tags` |
| `docs/dev/versioning.md` | Dataset version control: snapshots, branches, copy-on-write object store, diff, restore, COW injection points | Working on `VersionsPage`, branch/snapshot logic, or any code path that overwrites/deletes image files in place |
| `docs/dev/dashboard-pages.md` | Datasets page (categories, duplicate, import), Statistics page (histograms, CSV export, BucketPanel), Settings page (tabs, thresholds), hardware stats, file browser, Logs page (job history + JS error console) | Working on `DatasetsPage`, `StatsPage`, `SettingsPage`, hardware meters, `FileBrowserPage`, or `LogsPage` |
| `docs/dev/frontend-core.md` | TanStack Query/Zustand conventions, SSE hooks, job-completion cache invalidation, shared constants modules, Sidebar/Layout, split-view pane manager, Tailwind/CSS design system, `errorConsoleStore`, `ErrorConsole` overlay | Working on global frontend state, a new job-triggering UI, the pane/split-view system, styling, or the JS error console |
| `docs/dev/backend-infrastructure.md` | Production frontend serving, server shutdown/restart + restart loop, database (subfolders, indexes, deferred columns), SSE progress broadcaster, venv/ML setup, prereq auto-install, GPU auto-detection, manage.ps1 encoding constraint | Working on `main.py` server lifecycle, `manage.ps1`/`manage.sh`, Alembic migrations, or SSE infrastructure |

## Maintaining this documentation

This documentation is split across this file (always loaded) and topic files under `docs/dev/`
(loaded on demand via the Read tool, per the Documentation Map above). Keep it that way as you
learn new things during a session:

- **Plain relative paths only.** Never write `@docs/dev/...` anywhere in this file or in any
  `docs/dev/*.md` file. The `@path` syntax triggers Claude Code's automatic recursive loading,
  which would pull every topic file into context on every conversation. Always write paths as
  plain text, e.g. `docs/dev/ml-models.md`.

- **Narrow, subsystem-specific knowledge** (a quirk in one router, one component's behavior, one
  ML model's inference detail): append it to the relevant file under `docs/dev/` — find it via
  the Documentation Map, add a new bullet/paragraph under the most relevant heading (or a new
  heading if none fits). If the change makes that file's "when to read" hint noticeably
  incomplete, update the hint too.

- **Cross-cutting knowledge** (applies to most tasks regardless of subsystem — a new shared
  utility function, a new universal invariant, a pattern every router/component must follow):
  add it to **Key invariants** or **Shared utilities** in this file, not to a topic file. Ask:
  "would I want this loaded even for a task in an unrelated subsystem?" If yes, it belongs here.

- **New subsystem, or a topic file growing past ~250 lines**: split the new/overgrown cluster
  into a new `docs/dev/<topic>.md` file and add a row to the Documentation Map. If a new feature
  doesn't fit any existing file's theme, create a new file rather than appending to the
  least-bad existing one.

- **Cross-references between topic files**: when documenting something in file A that depends
  on something documented in file B, add a one-line pointer, e.g. "see `docs/dev/versioning.md`
  for the copy-on-write mechanism" — don't duplicate the explanation.

- **Periodic rebalancing**: if a topic file becomes a dumping ground for unrelated facts (low
  thematic coherence), propose splitting it during that session rather than continuing to append.

- **`docs/*.md` vs `docs/dev/*.md`**: `docs/*.md` (flat, no `dev/`) is end-user documentation
  referenced from `README.md` — different audience, do not confuse the two.
