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

**`npm run build` is the only real typecheck.** The root `tsconfig.json` is a solution-style
config (`"files": []` + project references), so a bare `npx tsc --noEmit` type-checks
**nothing** and exits 0 on code that does not compile. Verify frontend changes with
`npm run build` (which runs `tsc -b`), never with `tsc --noEmit`.

### Tests

Backend pytest (from the repo root, venv active **in the same shell** — a `( … )`
subshell discards activation and silently runs on system Python, which has no
fastapi/sqlalchemy):

```bash
source venv/bin/activate && python -m pytest backend/tests/ -q
```

Request-level tests drive `backend.main.app` over httpx (see
`backend/tests/conftest.py`); everything else is service-level. Anything needing a
decodable video is gated on cv2 — see `docs/dev/video.md` (§ cv2 in CI, and the skip
convention) before adding a test or a CI dependency there. Coverage is opt-in:
add `--cov=backend` (or `--cov=backend/routers`) — there is no pytest `addopts`, so
CI runs plain. Lint the backend with `ruff check backend` (config in `ruff.toml`,
scoped to `E9`+`F`). Run `python scripts/check_migrations.py`
after a model/migration change (CI job `migration-drift`). To launch the app itself, use the `run-app` skill.

Frontend end-to-end (Playwright, GPU-free journeys under `frontend/e2e/`):

```bash
cd frontend
npm run build                     # refresh dist first — e2e serves it (stale dist = stale test)
npx playwright install chromium   # first run only
npx playwright test               # spins up its own backend on :8199 vs a throwaway DB
```

The full pre-merge sweep (backend pytest → frontend build+lint → e2e) is the
`qa-smoke` skill.

## Architecture

### Data flow

HTTP request → FastAPI router → service layer (pure business logic, no HTTP) → SQLAlchemy async session → SQLite.

Long-running operations (captioning, quality scoring, import, export, batch ops) are queued: the router creates a `BackgroundJob` DB record, enqueues a coroutine in `workers/job_queue.py`, and immediately returns `{job_id}`. The worker runs the job, emits SSE progress events via `workers/progress.py`, and updates the job row when done. The frontend subscribes to `GET /api/v1/jobs/stream/{job_id}` (or `/stream/all/events` for the global progress bar).

`BackgroundJob` has a nullable `label: str | None` column (max 200 chars). Every router that creates a job sets an auto-generated descriptive label (e.g. `"Florence-2 (large) — 50 images"`, `"Quality: technical, aesthetic — 100 images"`) and accepts an optional `label` field in the request body to override it (`body.label or auto_label`). A `_model_short_label(model: str) -> str` helper in `routers/captioning.py` converts raw model IDs to readable names for the auto-label.

This file covers what applies across the whole codebase: commands, the data flow above, the invariants and shared utilities below. Subsystem detail lives in topic files under `docs/dev/`, each listed with its trigger in the Documentation Map near the end. Read the relevant one(s) before working on that subsystem; do not read all of them up front.

### Shared utilities

`backend/utils.py` — helpers shared across routers. **This is an index; each helper's docstring carries its behaviour and rationale.** Import from here and never re-inline the logic — that is the point of every entry below.

- **Client-supplied path/URL validators.** `normalize_subfolder(s)` (rejects `..` → 400; never re-import it from a router), `sanitize_abs_path(path)` (rejects null bytes and relative paths → 400; use in every router taking an arbitrary path — `filesystem`, `comfy` workflow scan), `safe_external_url(value)` — the only sanctioned check before a **provenance** URL reaches a markdown link target or an `href` (mirrored client-side by `frontend/src/utils/url.ts::safeExternalUrl`).
- **Stored-path containment.** `within_datasets_dir(path_str, base_dir) -> Path | None` and its raising wrapper `safe_dataset_path(...) -> Path` (403) — the guards the Path traversal invariant below refers to. **Serve** routes take the wrapper; **destructive** ones take the `None`, skip the filesystem op and still drop the row.
- **Naming.** `slugify_filename(name)`; `unique_filename(directory, stem, suffix, db_names, disk_exclude=None)` — a name free both on disk and in `db_names`, since the two checks cover different gaps; `unique_filename_with_thumb(images_dir, stem, suffix, db_names, occupied_thumb_stems, planned_thumb_stems)` — the same, also avoiding thumbnail-stem collisions. Call the latter in every path that creates or renames an image file and associates a thumbnail with it, and **never exclude the stems of images being renamed/moved** from `occupied_thumb_stems`.
- **Derived paths.** `thumbnail_path_for(image_path)` (`parent.parent/thumbnails/{stem}.webp`); `poster_path_for(video_path)` / `unique_poster_path(poster_dir, stem, claimed)` for video posters — the poster form is only a *proposal* (a poster's stem need not match its video's), so read `Video.poster_path` for an existing row. `unique_poster_path` is the tool for paths that **adopt** a filename off disk instead of picking one — see the invariant below.
- **Caption sidecars.** `rename_with_sidecar(old, new)` / `copy_with_sidecar(old, new)` move or copy a file together with its `.txt` sidecar in one call; `read_caption_sidecar(image_path) -> str | None` is the read side. See `docs/dev/image-files.md` (§ Importing captions & folder rescan).
- **Client-supplied regex.** `compile_user_regex(pattern)` (raises `regex_error` → 400) / `regex_sub_deadline(compiled, repl, text, deadline)` (raises `TimeoutError` → 408) / `REGEX_TIMEOUT_SECONDS` / `regex_error` — the only sanctioned way to run one, with a single deadline covering a whole batch instead of N × timeout. See the Key invariant below for why stdlib `re` is unusable here.
- **Batching & saving.** `chunked(seq, size=10_000)` — the single source of truth for splitting id lists before an SQL `IN (...)`, keeping bind parameters under SQLite's `SQLITE_MAX_VARIABLE_NUMBER` (**32766** since SQLite 3.32; the familiar 999 is the pre-2020 default and would be violated by `chunked`'s own 10k default). `normalize_image_format(suffix, out_path)` + `image_save_kwargs(fmt)` — the JPG/PNG fallback and its PIL `save()` kwargs, always used as a pair.
- **Disk.** `require_free_space(target_dir, needed_bytes=0)` / `InsufficientDiskSpaceError` — preflight for any run that writes many files (→ HTTP 507); `format_bytes(n)` renders sizes for prose, never for filenames or ids.
- `ALLOWED_FLAG_KEYS: frozenset` — the canonical valid quality-flag names. `subsume_tags(tags)` — order-stable whole-word tag dedup; see `docs/dev/tag-consolidation.md`.
- `count_caption_tokens(text)` / `_get_enc()` — the single tokenizer entry point. **`Image.caption_token_count` is a persisted column kept in sync by a SQLAlchemy `set` listener on `Image.caption_text`** (`backend/models/image.py`), so **captions must always be written via ORM attribute assignment** — a raw `update(Image)` / SQL write bypasses the listener and leaves the count stale. No such bulk-update write exists today; keep it that way.

**`backend/media_types.py`** — the single allowlist of ingestible file types: `IMAGE_EXTENSIONS`, `VIDEO_EXTENSIONS`, `MEDIA_EXTENSIONS`, `media_kind_for(suffix) -> "image" | "video" | None`, `video_mime(suffix)` and `codec_label(fourcc)`. Import from here in any code path that decides whether a file can be ingested; never write a local extension set. Discriminators stay `media_kind`-shaped, never `is_video` booleans. See `docs/dev/video.md`.

**`backend/licenses.py`** — the license vocabulary (`LICENSES`, `LICENSE_IDS`, `FIELD_MAX_LEN`, `normalize_license`, `license_info`, `allows_commercial`) and the provenance rules built on it; `frontend/src/constants/licenses.ts` mirrors it under test. Read side: `resolve_provenance(img, ds)`. Write side: `merge_provenance(*layers)`, `clamp_provenance(values)`, `normalize_license_input(v)`, `copy_provenance(img)` (same-dataset derivative), `materialize_provenance(img, ds)` (cross-dataset copy/move), `materialize_by_source(rows, ds_by_id)` (the batch form). A **client-supplied** license-id list is read only through `utils.parse_license_filter_param` / `normalize_license_filter` — a JSON array, never comma-separated, because an `other:<free text>` id may contain commas; an empty list always means "no filter", never "match nothing". Whether `""` inside that list is meaningful **differs by endpoint**, so check which one you are on. See `docs/dev/provenance.md`.

**Shared frontend components** recur across the topic files below; each is documented where it is most central. `SelectionToolbar` → `docs/dev/frontend-core.md`; `ConfirmDialog` and `useModalBehavior` → `docs/dev/styling.md`; `MoveToDatasetModal` → `docs/dev/image-files.md`; `GenerationMetadata` → `docs/dev/image-detail.md`; `DirPickerModal` → `docs/dev/datasets-page.md`; `JobProgressBar` → `docs/dev/versioning.md`.

### Key invariants

- **DB-before-filesystem for batch rename/move.** `bulk_rename` and `batch_move_dataset` commit DB changes *before* the filesystem renames so the DB is always the authoritative record of intended file locations. If a rename fails mid-batch, the DB reflects the final intended state; do not revert this ordering. `batch_copy_dataset` uses the opposite ordering (stage DB inserts, do copies, then commit) because an incomplete copy should leave nothing — see the cross-dataset copies note above.
- **Always `ImageOps.exif_transpose()` first.** Every Pillow operation in `image_service.py` calls this before anything else to correct orientation from EXIF data. The same rule holds for every ML inference path: open images through `backend/ml/image_utils.py::open_rgb` (`Image.open().convert("RGB")` + `exif_transpose`), never a bare `Image.open`, so all predictors work in one transposed frame and normalized coordinates denormalize consistently. `image_service._open_safe` is the equivalent gate for image-processing paths.
- **Close PIL Images after preprocessing.** In all ML inference paths (`aesthetic_scorer.py`, `dino_scorer.py`, all captioners) call `img.close()` immediately after the image has been passed to the model's preprocessor/processor — before the GPU inference runs. In `export_service.py::_write_image()` use a `try/finally` block. This frees the decoded pixel buffer (potentially several MB per image) during slow inference and prevents accumulation across large batches.
- **Absolute DB path.** `config.py` derives the database URL from `Path(__file__).parent.parent` so it resolves correctly regardless of the working directory when uvicorn is launched.
- **Path traversal guard.** No stored path becomes a `FileResponse` or gets mutated until the guards above prove it inside `settings.datasets_dir`, and no router keeps a private copy. The five destructive sites (`delete_image`, `batch_delete`, `bulk_delete_filtered`, `delete_video`, `rename_video`) gate **every** column they touch — `file_path`, `thumbnail_path`/`poster_path`, the derived `.txt` — and unlink the *resolved* path, never the raw string.
- **Never run a client-supplied regex through stdlib `re`.** Use `compile_user_regex` + `regex_sub_deadline` from `backend/utils.py` (the `regex` package). `re`'s matching loop is C code that never releases the GIL and cannot be interrupted, so a catastrophic pattern freezes the entire process — and the obvious guard does not work: wrapping it in `run_in_executor` + `asyncio.wait_for` can never fire, because the event loop can't be scheduled to fire it, and Python cannot kill the thread. Verified: `(a|a)*$` against 30 `a`s takes `re` **105 s** at 100% GIL, versus a clean `TimeoutError` from `regex` with the loop still live. Hardcoded patterns and `re.escape`'d literals (`slugify_filename`, `subsume_tags`) are fine on `re` — they can't backtrack. A comment claiming a thread + `wait_for` bounds a regex is the bug, not the fix.
- **Provenance NULL means inherit; materialize it on cross-dataset copy/move.** An `Image` whose `source_name`/`source_url`/`license`/`attribution` is NULL **or `""`** inherits its `Dataset`'s default, resolved at read time by `licenses.resolve_provenance` — which coalesces on *falsiness*, so a blank string is not "explicitly nothing"; recording that needs a real value (the `no-license` id, a written-out attribution).
  - **Cross-dataset.** Any code path that moves or copies an image into a *different* dataset must call `licenses.materialize_provenance(img, source_dataset)` and write concrete values, or the image silently re-inherits the destination's unrelated default. `duplicate_dataset` is the one sanctioned exception: it copies the four dataset defaults onto the new dataset first, so raw `copy_provenance` keeps inheritance equivalent.
  - **Resolve each image against its own source dataset** — a selection can span datasets, so use `licenses.materialize_by_source(rows, ds_by_id)` rather than resolving the batch against `rows[0]`'s dataset; the same goes for the batch's busy guard and stats refresh.
  - **Same-dataset derivatives** (crop, upscale, LUT, detection crop) copy raw values instead, via `copy_provenance` (which deep-copies `source_meta`, so parent and derivative never share one mutable JSON dict). `VersionImageState` mirrors all five image columns — adding a provenance column without the mirror makes a snapshot restore wipe it.
  - **`Image.source_meta` is `deferred=True`**: every reader that loads `Image` **as an ORM entity** (those derivative paths, and `create_snapshot`) must `undefer` it, or `getattr(img, "source_meta")` lazy-loads on an async session and raises `MissingGreenlet` — a failure that only appears on the live async path, never in a helper-level unit test. A `select(Image.source_meta, …)` column list already loads it and needs no undefer, and `batch_move_dataset` omits the column on purpose.
  - **Provenance strings are untrusted input in every document they reach** — `source_name`/`source_url`/`attribution`/free-text `license` come from scrapers, sidecars and EXIF, so anything interpolating them into a generated artifact must neutralise them for that syntax: markdown via `export_service._md_inline`/`_md_link`, CSV cells via `_csv_cell` (leading `=`/`+`/`-`/`@`/TAB/CR), any linked URL via `utils.safe_external_url`. `CREDITS.md` is a legal attribution document built by interpolation — a newline in `attribution` forged a `## <license>` section claiming rights the export did not carry.
- **Videos are sources, not images, and their posters live apart.** A video is a `Video` row in `videos`, flat in `{dataset}/videos/`, never a row in `images`. Poster thumbnails go in `{dataset}/videos/thumbnails/` — a separate directory, *not* the images thumbnail folder with a distinguishing suffix: nine **modules** build `occupied_thumb_stems` from `thumb_dir.glob("*.webp")` (enumerated in `docs/dev/video.md`), and a suffix convention means every one of them must learn to filter it, with any one that forgets being a silent thumbnail clobber. `Dataset.video_count`/`video_size_bytes` are deliberately separate from `image_count`/`total_size_bytes`, and **must be passed explicitly** wherever a `DatasetOut` is hand-built field by field (`list_datasets`): both default to 0, so an omission reports every dataset as video-free instead of failing. A poster is a nicety, never a gate — `video_service.generate_poster` returns False rather than raising, so a video whose frames will not decode still ingests, lists, plays, renames and deletes. See `docs/dev/video.md`, `docs/dev/video-decode.md` for the decode surface, and `docs/dev/video-extract.md` for frames.
- **A column added to `Image` must be mirrored on `VersionImageState`, and carried by every path that rebuilds an `Image` field by field.** Every one of those paths fails **silently** — a missing mirror means a snapshot restore blanks the column, a missing constructor entry means a copy quietly drops it — which is why the rule is here rather than in a topic file. `backend/tests/test_video_lineage_mirrors.py` holds the structural guard (an explicit `NOT_MIRRORED` allowlist checked in both directions), so the *next* unmirrored column fails CI rather than a restore; it also enumerates the rebuild paths. Two rules decide what such a path writes: **derived-from-elsewhere columns are not copied across a dataset boundary** (`Image.source_video_id` would point at a video the destination does not contain, while `source_timestamp_ms`/`source_shot_index` travel), and **immutable columns are mirrored but not diffed** (lineage is written once by extraction, so it is absent from `_DIFF_COLS` on purpose). See `docs/dev/video-extract.md` for the lineage columns and `docs/dev/video-reextract.md` for the `processing_history` skip rule that follows from them.
- **A stem-keyed derived artifact needs a collision guard at every site that *writes* it — including the ones that adopt a filename rather than pick one.** Thumbnails and posters are `.webp` keyed by stem, so two files differing only in extension share one derived path and the second write clobbers the first, leaving two rows pointing at one picture. Creation paths pick a free name via `unique_filename_with_thumb`; the two rescan paths instead register files found on disk under the names the user gave them, and resolve collisions in **opposite** directions on purpose — `rescan_dataset` renames the *image file* (`a.jpg` → `a_001.jpg`) because eleven sites re-derive an image's thumbnail path from its filename, so a drifted thumbnail stem would be orphaned by the next rename/move/crop/restore; `_rescan_videos` renames the *poster* (`clip_001.webp`) and leaves the video file alone, because nothing re-derives a poster path — every consumer reads `Video.poster_path`. When reviewing any new path that writes a derived file, ask which of the two it is. A path registering a file already in place must also pass `disk_exclude={f.name}`, or the uniquifier sees each file's own name occupied and renames everything.
  - An **unregistered** file sitting at a rename's target path has no row to guard it and must be refused, not clobbered. (A *pure extension change* — `a.jpg` → `a.png`, from a re-extraction or a PNG format fallback — is otherwise the one rename that disturbs nothing derived: the stem is unchanged, so the thumbnail and the `.txt` sidecar stay put, and no uniquifier call is needed.) See `docs/dev/video-reextract.md` § The extension change.
- **Nothing fallible between an irreversible filesystem mutation and the `commit()` that describes it.** A transaction rolls back a row; nothing rolls back an `os.replace` or an overwrite. Write the file, assign **every** row field, `commit()`, then run whatever can raise in a `try/except` that logs and cannot change the item's outcome or feed a failure breaker (those post-commit reads are safe only because `AsyncSessionLocal` sets `expire_on_commit=False`). `Path.unlink` is fallible too, so a superseded original goes in that epilogue — which also leaves the row naming a file that exists if the `commit()` itself fails. See `docs/dev/postmortems/PM-013-fs-mutation-before-the-commit.md`.
- **Never mutate a loaded JSON column in place.** For JSON columns like `Image.quality_flags`, copy before mutating: `flags = dict(img.quality_flags or {})`, edit `flags`, then reassign `img.quality_flags = flags`. SQLAlchemy's default change detection compares by equality, so mutating and reassigning the *same* dict object looks unchanged and the UPDATE is silently skipped. The correct pattern lives in `services/caption_service.py`.

## Documentation Map

Each file below covers one subsystem in depth. Read the relevant file(s) with the Read tool when
your task touches that subsystem — do not read all of them up front. Do NOT use `@`-paths to reference
these files anywhere — `@path` auto-loads the target into every conversation, defeating this split.

| File | Read this when... | Words |
|---|---|---|
| `docs/dev/ml-models.md` | Working on captioning models, upscaling, LUT grading, or `backend/ml/` loading — the model manager (VRAM/unload), model ID registry, device abstraction, TorchDynamo | ~2425 |
| `docs/dev/detection.md` | Working on object detection, masks, or `DetectionsPanel` — the `/detection` router, its scope/model/task matrix, SAM2/SAM3/Florence-2/NudeNet inference, `mask_area` | ~2890 |
| `docs/dev/scoring.md` | Working on `QualityPage`, quality flags, or thresholds — the scorers and the columns they write, duplicate detection, style similarity, DINOv2 per-layer scoring | ~1575 |
| `docs/dev/gallery.md` | Working on `GalleryPage` or gallery drag & drop — selection and shift-click ranges, subfolder sidebar, filters, manual ordering, dnd-kit droppables and collision detection | ~2925 |
| `docs/dev/image-detail.md` | Working on `ImageDetailPage` or generation-metadata display — gallery/nav persisted keys, `injectNavId`/`paneGo`, crop tool, selection toggle, caption panel | ~1865 |
| `docs/dev/image-files.md` | Working on image upload/move/copy/rename or caption import — naming and collisions, cross-dataset move and copy, folder import, rescan/sync, drag-`.txt`-onto-image | ~1805 |
| `docs/dev/provenance.md` | Anything touching license, attribution, `source_meta`, or license filters — the `backend/licenses.py` vocabulary, dataset→image inheritance, ingest capture precedence, cross-dataset materialization | ~3185 |
| `docs/dev/captioning.md` | Working on `CaptioningPage`, LLM provider integration, or a caption sidecar write — post-processing (delimiter modes, refusal stripping, rename-on-caption), `ModelPicker` | ~1760 |
| `docs/dev/export.md` | Working on `ExportPage` or `export_service.py` — kohya/ai-toolkit/plain, stem uniquification, license/commercial/no-derivatives filters, resize, loss masks, CREDITS.md | ~3185 |
| `docs/dev/bulk-ops.md` | Working on `BulkEditPage`, `CropToDetectionForm`, or a `bulk-*` endpoint — caption find/replace/regex, image rename/delete/reorder, `detection_crop_rect` and the crop remap | ~2480 |
| `docs/dev/tag-consolidation.md` | Working on `TagConsolidatePage`, `tag_embedder`, or `dedupe_tags` — the MiniLM tag embedder, analyze/apply jobs, whole-tag (non-substring) rewrite, preview/confirm UI | ~895 |
| `docs/dev/video.md` | Working on videos, video ingest, the `/videos` endpoints, any file-extension allowlist, or a cv2-gated test — the `Video` model and `videos/` layout, poster stems and collisions, range serving | ~3120 |
| `docs/dev/video-ui.md` | Working on any video screen, the extraction modal, or the frame lineage filter — `VideoStrip`, `VideoDetailPage`, `ExtractFramesModal` + `CropOverlay`/`TrimBar`, `useVideoExtractJobs` | ~3500 |
| `docs/dev/video-decode.md` | Working on video probe/metadata, video duration, or poster frames — the cv2 probe ladder, `measure_duration_ms`, `isOpened()` as ingest gate, the poster fallback ladder | ~1540 |
| `docs/dev/video-shots.md` | Working on probe sampling, shot detection, or frame rendering — the pass 1 pipeline in `video_extract.py`: the RSS rule, the PySceneDetect contract and cost cliff, `render_shot`/`_write_frame` | ~1485 |
| `docs/dev/video-extract.md` | Working on the extract/probe endpoints or the `video_extract` job — Pass 1's router half: crop/trim validation, subfolder modes, step order and the replace delete, SSE progress, frame lineage | ~2360 |
| `docs/dev/video-reextract.md` | Working on re-extraction, `POST /videos/reextract`, or a replace-mode extension change — Pass 2 full-res: seek-by-timestamp, the `processing_history` skip rule, `render_at_timestamps` | ~3020 |
| `docs/dev/video-heuristics.md` | Tuning cropdetect, interlace/telecine detection, or which frame a shot yields — the pure-numpy `video_frames.py` judgement calls, sharpness, `pick_index` candidate rejection | ~875 |
| `docs/dev/versioning.md` | Working on `VersionsPage`, `dataset_busy`, or any path that overwrites image files in place — snapshots, branches, the copy-on-write object store, diff, restore, prune/GC | ~3165 |
| `docs/dev/datasets-page.md` | Working on `DatasetsPage`, categories, or the folder picker — preview strip, license badge, sort/density/grouping, category rail, `ImportFolderModal`/`DirPickerModal` | ~2395 |
| `docs/dev/statistics.md` | Working on `StatsPage`, `BucketPanel`, or `get_dataset_stats` — stats queries and live polling, server-side aggregation, the validator-keyed cache, editable histograms | ~2845 |
| `docs/dev/settings.md` | Working on `SettingsPage`, a new app-wide setting, or `threshold_service.py` — the `ThresholdSettings` singleton row and every tab it backs | ~1060 |
| `docs/dev/workspace.md` | Working on hardware meters, `FileBrowserPage`, `LogsPage`, or `BooruPage` — the sidebar meters and `/system`, the file browser and `/filesystem`, job history + JS error console | ~2745 |
| `docs/dev/frontend-core.md` | Working on global frontend state, a shared constants module, or the JS error console — TanStack Query/Zustand conventions, the `SelectionToolbar` action modals, `uploadStore` | ~1965 |
| `docs/dev/frontend-jobs.md` | Adding a job-triggering UI or changing what a finished job invalidates — SSE hooks, `jobStore`, job labels, job-completion cache invalidation (single-job and id-list patterns) | ~1190 |
| `docs/dev/panes-routing.md` | Working on panes, adding a routed page, or lazy page loading — sidebar layout, the split-view pane manager, `usePaneNavigate`, the six-site routed-page checklist | ~955 |
| `docs/dev/persistence.md` | Adding a storage key or persisting page configuration — the `constants/storage.ts` key registry, `loadPersisted`/`useDebouncedPersist`, the three persistence shapes | ~1585 |
| `docs/dev/styling.md` | Working on Tailwind/CSS, the brand mark, or any modal dialog — CSS variable tokens, `@layer components` classes, `CrucibleMark` drift checks, `ConfirmDialog`, `useModalBehavior` | ~1610 |
| `docs/dev/backend-infrastructure.md` | Working on `main.py` lifecycle, Alembic migrations, SSE, or job cancellation — production frontend serving, the shutdown/restart loop, DB indexes and deferred columns, the SSE broadcaster | ~2670 |
| `docs/dev/environment-setup.md` | Working on `manage.ps1`/`manage.sh`, torch wheels, the startup splash, or the setup/update flow — venv ML packages, PyTorch GPU auto-detection, SAM2/SAM3 install, lockfile reset | ~2395 |
| `docs/dev/comfyui.md` | Working on `ComfyPage`, the `comfy` router, or ComfyUI integration — plans (workflow template + pinned params), prompt rows, prompt library, the `comfy_generate` job, ComfyClient | ~2815 |
| `docs/dev/comfy-prompts.md` | Generating prompts with an LLM or working on `prompt_generator.py` — the one-shot generate endpoint, the durable `comfy_prompts` job, `parse_prompts` filtering, `GeneratePromptsModal` re-attach | ~1880 |
| `docs/dev/comfyui-sync.md` | Working on workflow sync or the bridge extension — "Sync from canvas", `GET /comfy/canvas-workflow`, the `ComfyUI-CrucibleBridge` extension (`extras/`), history-pull fallback, ComfyUI API constraints | ~710 |
| `docs/dev/postmortems.md` | Doing a code review or investigating a bug — the postmortem index: past incidents as one-line rows with LIVE/MITIGATED/STRUCTURAL status, linking `docs/dev/postmortems/` | ~845 |

### Code review & bug investigation

When reviewing code (any `/code-review` run or ad-hoc review request) or investigating a bug:

- Always read `docs/dev/postmortems.md` first. Pull a specific detail file from
  `docs/dev/postmortems/` only when the code under review touches that entry's area.
- Treat LIVE and MITIGATED entries as an active checklist for their code class;
  ignore STRUCTURAL entries (kept for history only).
- Do not let them narrow the review. New code can fail in new ways — check for
  novel issues too, not only documented ones.

## Maintaining this documentation

Documentation is split across this file (always loaded) and the topic files above
(loaded on demand). Three rules apply to every change; the full workflow — where a
new fact goes, split thresholds, cross-reference conventions, the end-of-branch doc
audit, and the skill-proposal format — lives in the `doc-maintenance` skill. Invoke
it before editing any doc.

- **Plain relative paths only.** Never write `@docs/...` or `@CLAUDE` anywhere in
  documentation; the `@path` syntax recursively auto-loads the target into every
  conversation, defeating the split.
- **User-facing features need user-facing docs.** `docs/dev/` explains a subsystem to
  whoever maintains it and never counts as documenting the feature. A change to
  anything a user can see — a page, a sidebar item, a settings tab, a setup step —
  updates `README.md` and the relevant `docs/*.md` **in the same change**.
- **Run `python scripts/check_docs.py`** after any documentation change and fix what
  it reports. It enforces word budgets, not line counts, and warns on any paragraph
  over 250 words — see its module docstring for why.
