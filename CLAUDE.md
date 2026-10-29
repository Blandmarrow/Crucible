# CLAUDE.md

Agent entry point: what applies across the whole codebase — commands, verification, the data
flow, the invariants. Subsystem detail lives in `docs/dev/` topic files, routed by the
Documentation Map near the end; load only the ones a task needs.

## Working here

**Milestone (2026-10-29):** the docs-architecture upgrade, on branch `docs/architecture-v3.1`
— M3, the split queue; order and the session
handoff are in `docs/dev/roadmap.md`.

## Commands

### Launch

**Windows**: `manage.ps1 <cmd>` (or double-click `Crucible.bat`) | **Linux/macOS**: `./manage.sh <cmd>` (run `chmod +x manage.sh` once)

| Command | Purpose |
|---|---|
| `setup` | First time — creates venv, installs deps (GPU PyTorch auto-detect + optional SAM2), builds frontend |
| `start` | Production: runs migrations, rebuilds frontend if needed, serves on :8000 |
| `update` | `git pull` → update pip deps (GPU PyTorch auto-detect) → optional SAM2 update → `npm install` → rebuild frontend |
| `dev` | Dev mode: backend on :8000 (hot reload) + Vite frontend on :5173 |

Shutdown and Restart are the TopBar's power and circular-arrow buttons (or Ctrl+C): see
`docs/workspace.md` § Restarting & shutting down, and `docs/dev/backend-infrastructure.md`
§ Server control endpoints for the routes behind them.

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
npm run typecheck:e2e  # tsc over frontend/e2e/ — outside the build, see below
npm run lint     # ESLint — CI runs it `continue-on-error`, so it never blocks
```

What actually verifies a frontend change is in § Verification below.

### Tests

Backend pytest (from the repo root, venv active **in the same shell** — a `( … )`
subshell discards activation and silently runs on system Python, which has no
fastapi/sqlalchemy):

```bash
source venv/bin/activate && python -m pytest backend/tests/ -q
```

Conventions for writing a backend test — `requirements-ci.txt`, the `needs_torch`/`needs_cv2`/`needs_symlinks` gates, `api_env` foreign keys, coverage — are in `docs/dev/backend-tests.md`. To launch the app itself, use the `run-app` skill.

Frontend end-to-end (Playwright, GPU-free journeys under `frontend/e2e/`):

```bash
cd frontend
npm run build                     # refresh dist first — e2e serves it (stale dist = stale test)
npm run typecheck:e2e             # the specs' only typecheck (see above)
npx playwright install chromium   # first run only
npx playwright test               # spins up its own backend on :8199 vs a throwaway DB
```

The full pre-merge sweep (backend pytest → frontend build + e2e typecheck + lint → e2e) is
the `qa-smoke` skill.

## Verification

**Report what the run says, not that it built.** Quote the test count, the exit code or the
`RESULT:` line. "It compiled" is not "it works", and a skipped stage is reported as skipped.

**`npm run build` is the only real typecheck for `src/`.** The root `tsconfig.json` is a
solution-style config (`"files": []` + project references), so a bare `npx tsc --noEmit`
type-checks **nothing** and exits 0 on code that does not compile. Verify frontend changes
with `npm run build` (which runs `tsc -b`), never with `tsc --noEmit`.

**`frontend/e2e/` is deliberately outside that build** — its specs must never enter
`vite build` — and Playwright only transpiles, so `npm run typecheck:e2e`
(`tsc -p e2e/tsconfig.json --noEmit`, ~2 s) is the only thing that checks them. It is a
blocking CI step and part of the `qa-smoke` skill; run it after touching a spec.

Which gate runs where — the one place this list lives:

| Gate | Command | Locally | CI job | Blocks a PR |
|---|---|---|---|---|
| Backend tests | `python -m pytest backend/tests/ -q` | `qa-smoke` | `backend-tests` (Ubuntu) | yes |
| Backend tests on Windows | the same | — | `backend-tests-windows` | **no** — `continue-on-error` until it runs green |
| Backend lint | `ruff check backend` | by hand (not in the venv; CI installs it) | `backend-lint` | yes |
| Migration drift | `python scripts/check_migrations.py` | after a model or migration change | `migration-drift` | yes |
| `src/` typecheck + build | `npm run build` | `qa-smoke` | `frontend-build` | yes |
| e2e spec typecheck | `npm run typecheck:e2e` | `qa-smoke` | `frontend-build` | yes |
| Frontend lint | `npm run lint` | `qa-smoke` | `frontend-build` | **no** — the react-hooks backlog |
| End-to-end journeys | `npx playwright test` | `qa-smoke` | `e2e-smoke` | yes |
| Docs drift check | `python scripts/check_docs.py` (CI runs `--self-test` first) | after any doc change | `check-docs`, every PR | its FAILs only |
| Brand-mark drift | `python scripts/check_mark.py` | after touching the mark | `check-mark` | yes |

**What no gate runs** — stated so it is not discovered later:
- **Torch, GPU and real model inference.** `requirements-ci.txt` is torch-free, so anything
  under `backend/ml/` past the pure-numpy modules is tested only where `needs_torch` lets it
  skip. Verify such a change by running it.
- **scenedetect and ffmpeg in e2e**, deliberately absent so the `capabilities: false`
  branches are exercised (`docs/dev/video/tests.md`).
- **Windows filesystem rules** — the open-file locks of PM-021, symlink privilege — reach
  only the non-blocking Windows job; `manage.ps1` is parse-checked, never run.
- **A real ComfyUI, the booru lookup, Restart and Shutdown**, which `qa-smoke` and e2e leave
  out.

## Architecture

### Data flow

HTTP request → FastAPI router → service layer (pure business logic, no HTTP) → SQLAlchemy async session → SQLite.

Long-running operations (captioning, quality scoring, import, export, batch ops) are queued: the router creates a `BackgroundJob` DB record, enqueues a coroutine in `workers/job_queue.py`, and immediately returns `{job_id}`. The worker runs the job, emits SSE progress events via `workers/progress.py`, and updates the job row when done. The frontend subscribes to `GET /api/v1/jobs/stream/{job_id}` (or `/stream/all/events` for the global progress bar).

`BackgroundJob` has a nullable `label: str | None` column (max 200 chars). Every router that creates a job sets an auto-generated descriptive label (e.g. `"Florence-2 (large) — 50 images"`, `"Quality: technical, aesthetic — 100 images"`) and accepts an optional `label` field in the request body to override it (`body.label or auto_label`). A `_model_short_label(model: str) -> str` helper in `routers/captioning.py` converts raw model IDs to readable names for the auto-label.


### Shared utilities

Three modules carry the helpers every router and service shares: `backend/utils.py` (path guards and containment, naming and collisions, caption sidecars, `record_in_place`, client-supplied regex, batching, disk preflight, the tokenizer), `backend/media_types.py` (the single allowlist of ingestible file types) and `backend/licenses.py` (the license vocabulary and the provenance rules built on it). Import from them and **never re-inline the logic** — that is the point of all three. `docs/dev/shared-utilities.md` indexes every helper by name, alongside the shared frontend components; read it before writing anything one of them already does.

### Key invariants

- **DB-before-filesystem for batch rename/move.** `bulk_rename` and `batch_move_dataset` commit DB changes *before* the filesystem renames so the DB is always the authoritative record of intended file locations. If a rename fails mid-batch, the DB reflects the final intended state; do not revert this ordering. `batch_copy_dataset` uses the opposite ordering (stage DB inserts, do copies, then commit) because an incomplete copy should leave nothing — see the **Cross-dataset copies** note in `docs/dev/image-files.md`.
- **Always `ImageOps.exif_transpose()` first.** Every Pillow operation in `image_service.py` calls this before anything else to correct orientation from EXIF data. The same rule holds for every ML inference path: open images through `backend/ml/image_utils.py::open_rgb` (`Image.open().convert("RGB")` + `exif_transpose`), never a bare `Image.open`, so all predictors work in one transposed frame and normalized coordinates denormalize consistently. `image_service._open_safe` is the equivalent gate for image-processing paths.
- **Close PIL Images after preprocessing.** In all ML inference paths (`aesthetic_scorer.py`, `dino_scorer.py`, all captioners) call `img.close()` immediately after the image has been passed to the model's preprocessor/processor — before the GPU inference runs. In `export_service.py::_write_image()` use a `try/finally` block. This frees the decoded pixel buffer (potentially several MB per image) during slow inference and prevents accumulation across large batches.
- **Absolute DB path.** `config.py` derives the database URL from `Path(__file__).parent.parent` so it resolves correctly regardless of the working directory when uvicorn is launched.
- **Path traversal guard.** No stored path becomes a `FileResponse` or gets mutated until the containment guards in `backend/utils.py` (`safe_dataset_path`, `contained_path` — indexed in `docs/dev/shared-utilities.md`) prove it inside `settings.datasets_dir`, and no router keeps a private copy. Thirteen destructive sites gate **every** column they touch — `file_path`, `thumbnail_path`/`poster_path`, the derived `.txt` — and unlink the *resolved* path, never the raw string: `delete_image`, `batch_delete`, `bulk_delete_filtered`, `delete_video`, `rename_video`, `filesystem.delete_path` (via `_add_orphan`) and `filesystem.rename_path`'s thumbnail move, which PM-014 added; the three V-83 closed — `videos._delete_previous_frames` (replace-mode extraction), `quality.resolve_duplicates`, and `version_service._remove_stale_files` (gated inside the helper, since it has five call sites); the two the sub-bullet below names; and `images.bulk_thumbnails`, whose gate gains nothing from an unlink — `generate_thumbnail` mkdirs its parent, so an ungated `thumbnail_path` is an arbitrary-file *write* primitive. This list is a reviewer's checklist: a new destructive path belongs in the gated group.
  - **The gate covers the versioning hook as much as the unlink**, and the last two sites gate nothing else: `mark_image_deleted_in_versions` *and* `protect_file_before_overwrite` both copy the bytes into `{ds}/.versions/objects/`, so an out-of-tree `file_path` is an arbitrary-file *read* primitive even where no unlink follows. `filesystem.delete_path`'s row loop and `version_service.restore_snapshot`'s extras loop were the two that gated their unlinks but not their hook; the extras loop needed the gate around the **whole** loop, not just its `"remove"` branch, since the `else` branch reaches the same primitive.
  - **The rule is about untrusted path components, not about `datasets_dir`** — a `FileResponse` built from a *URL segment* needs the same `resolve()` + `is_relative_to(root)` gate against whatever root it serves, applied **before** `is_file()`. `main.py`'s SPA catch-all is the one such site (it serves `frontend/dist`), and it went unguarded for exactly this reason: every sweep enumerated routers, and it is not in one. Two escapes need no visible `..` — percent-encoded dots (`%2e%2e`) survive the client-side normalization that removes literal `../`, and `base / user_input` silently **discards `base`** when `user_input` is absolute. See PM-015.
- **Never run a client-supplied regex through stdlib `re`.** Use `compile_user_regex` + `regex_sub_deadline` from `backend/utils.py` (the `regex` package; see `docs/dev/shared-utilities.md`). `re`'s matching loop is C code that never releases the GIL and cannot be interrupted, so a catastrophic pattern freezes the entire process — and the obvious guard does not work: wrapping it in `run_in_executor` + `asyncio.wait_for` can never fire, because the event loop can't be scheduled to fire it, and Python cannot kill the thread. Verified: `(a|a)*$` against 30 `a`s takes `re` **105 s** at 100% GIL, versus a clean `TimeoutError` from `regex` with the loop still live. Hardcoded patterns and `re.escape`'d literals (`slugify_filename`, `subsume_tags`) are fine on `re` — they can't backtrack. A comment claiming a thread + `wait_for` bounds a regex is the bug, not the fix.
- **Provenance NULL means inherit; materialize it on cross-dataset copy/move.** An `Image` whose `source_name`/`source_url`/`license`/`attribution` is NULL **or `""`** inherits its `Dataset`'s default, resolved at read time by `licenses.resolve_provenance` — which coalesces on *falsiness*, so a blank string is not "explicitly nothing"; recording that needs a real value (the `no-license` id, a written-out attribution).
  - **Cross-dataset.** Any code path that moves or copies an image into a *different* dataset must call `licenses.materialize_provenance(img, source_dataset)` and write concrete values, or the image silently re-inherits the destination's unrelated default. `duplicate_dataset` is the one sanctioned exception: it copies the four dataset defaults onto the new dataset first, so raw `copy_provenance` keeps inheritance equivalent.
  - **Resolve each image against its own source dataset** — a selection can span datasets, so use `licenses.materialize_by_source(rows, ds_by_id)` rather than resolving the batch against `rows[0]`'s dataset; the same goes for the batch's busy guard and stats refresh. The rule is not provenance's alone: any id-list route that reads a folder, filename set or guard from `rows[0]` must work per row or refuse a mixed list, and a toolbar refresh covers every dataset the selection touched (PM-029).
  - **Same-dataset derivatives** (crop, upscale, LUT, detection crop) copy raw values instead, via `copy_provenance` (which deep-copies `source_meta`, so parent and derivative never share one mutable JSON dict). `VersionImageState` mirrors all five image columns — adding a provenance column without the mirror makes a snapshot restore wipe it.
  - **`Image.source_meta` is `deferred=True`**: every reader that loads `Image` **as an ORM entity** (those derivative paths, and `create_snapshot`) must `undefer` it, or `getattr(img, "source_meta")` lazy-loads on an async session and raises `MissingGreenlet` — a failure that only appears on the live async path, never in a helper-level unit test. A `select(Image.source_meta, …)` column list already loads it and needs no undefer, and `batch_move_dataset` omits the column on purpose.
  - **Provenance strings are untrusted input in every document they reach** — `source_name`/`source_url`/`attribution`/free-text `license` come from scrapers, sidecars and EXIF, so anything interpolating them into a generated artifact must neutralise them for that syntax: markdown via `export_service._md_inline`/`_md_link`, CSV cells via `_csv_cell` (leading `=`/`+`/`-`/`@`/TAB/CR), any linked URL via `utils.safe_external_url`. `CREDITS.md` is a legal attribution document built by interpolation — a newline in `attribution` forged a `## <license>` section claiming rights the export did not carry.
- **Videos are sources, not images, and their posters live apart** — a video is a `Video` row in `{dataset}/videos/`, its poster in `{dataset}/videos/thumbnails/` (never the image thumbnail folder), and `video_count`/`video_size_bytes` must be passed explicitly to a hand-built `DatasetOut` or every dataset reads as video-free. See `docs/dev/video.md` § Videos are sources, not images, and their posters live apart.
- **A column added to `Image` must be mirrored on `VersionImageState`, and carried by every path that rebuilds an `Image` field by field.** Every one of those paths fails **silently** — a missing mirror means a snapshot restore blanks the column, a missing constructor entry means a copy quietly drops it — which is why the rule is here rather than in a topic file. `backend/tests/test_video_lineage_mirrors.py` holds the structural guard (an explicit `NOT_MIRRORED` allowlist checked in both directions), so the *next* unmirrored column fails CI rather than a restore; it also enumerates the rebuild paths. Three rules decide what such a path writes. See `docs/dev/video/extract.md` for the lineage columns and `docs/dev/video/reextract-endpoints.md` for the `processing_history` skip rule that follows from them.
  - **Derived-from-elsewhere columns are not copied across a dataset boundary** — `Image.source_video_id` would point at a video the destination does not contain, while `source_timestamp_ms`/`source_shot_index` travel. The one path that *does* carry the videos across — `duplicate_dataset` under `include_videos` — **remaps** `source_video_id` through an old→new id map onto the clone's own video, and falls back to NULL on a miss. Remapped, never copied, and only by the path that carried the videos; every other path still NULLs it. **`quality_flags["duplicate_of"]` is the same shape and takes the same rule** at the same four boundary sites — `batch_copy_dataset`, `batch_move_dataset`, `duplicate_dataset`'s two branches — through `duplicate_service.carry_duplicate_flags`: remapped onto the destination's own copy of the root when that root crossed too, both duplicate keys stripped when it did not. A move maps the ids to *themselves*, which is what lets a whole group travel intact.
  - **Immutable columns are mirrored but not diffed** — lineage is written once by extraction, so it is absent from `_DIFF_COLS` on purpose. That carve-out is for immutability alone: `scores_stale` is **mutable** state and so is mirrored *and* diffed (in `_DIFF_COLS` and `_DIFF_COMPARE_FIELDS` both), because an in-place rewrite between two snapshots flips it and nothing else, and a restore that brought back stale scores without the bit would present them as trustworthy.
  - **A `*_score` column is authored data as far as a snapshot is concerned** — nothing recomputes a technical score, so all ten are mirrored *and* diffed, and `NOT_MIRRORED` holds no score. The immutable carve-out above is not license to skip the diff for one.
- **A row attached to an `Image` by a join table follows the same rule as a column: mirrored on `VersionImageState`, and carried by every path that rebuilds an `Image` field by field.** `image_labels` is the first such table, and `test_video_lineage_mirrors.py` is structurally blind to it — that guard walks `Image`'s *columns* against `VersionImageState`'s, so an attachment stored as rows is exactly the shape it cannot see. The mirror stores **ids, not names** (a rename is then a spelling change that detaches nothing, while a delete honestly drops the assignment), NOT NULL with a `'[]'` server_default (a NULL/`[]` split makes every pre-migration row differ from every post-migration one), sorted (the diff compares with `!=`), and with no FK (a snapshot must survive the deletion of what it names). The join table deliberately carries **no `dataset_id`** — that absence is *why* `batch_move_dataset` needs no changes, since the move UPDATEs `Image.dataset_id` in place and never touches `Image.id`; denormalizing it would silently break every cross-dataset move. `backend/tests/test_labels_survive_rebuild_paths.py` holds the structural guard over the four rebuild paths. See `docs/dev/label-consumers.md`.
  - **`detections` is the second such table, with two deliberate differences** (GPT-026). Its mirror `VersionImageState.detections` is **nullable with no server default**: NULL means "not recorded" (a pre-migration row, which a restore leaves alone), where `'[]'` would wipe every detection on restoring an old snapshot. And only snapshot/restore carry it — dataset duplication and cross-dataset copies still drop detections by design. Its geometry is a pixel qualifier, so a restore's failed-content reconcile puts the pre-restore set back. `backend/tests/test_detections_snapshot.py` holds the guard. See `docs/dev/versioning-service.md`.
- **Every path that rewrites an image's pixels in place goes through `utils.record_in_place(img, op, **params)`**, the single writer of `processing_history` and `scores_stale`: a site that records the edit by hand leaves stale scores looking trustworthy, and the AST guard in `backend/tests/test_scores_stale.py` fails CI on one. The ten sites and the clear predicate: `docs/dev/quality/scores-stale.md` § Every in-place pixel rewrite goes through record_in_place (PM-010).
- **A stem-keyed derived artifact (thumbnail, poster) needs a collision guard at every site that writes it, including the rescans that adopt a filename** — two files differing only in extension otherwise share one derived path, and the second write clobbers the first. The two rescans resolve collisions in opposite directions on purpose; see `docs/dev/image-files.md` § A stem-keyed derived artifact needs a collision guard at every writer.
- **Nothing fallible between an irreversible filesystem mutation and the `commit()` that describes it.** A transaction rolls back a row; nothing rolls back an `os.replace` or an overwrite. Write the file, assign **every** row field, `commit()`, then run whatever can raise in a `try/except` that logs and cannot change the item's outcome or feed a failure breaker. DB work is fallible too: where the write can go to a temp file, *stage, flush, swap, commit* — every row field and DB write (a detection remap) staged and `flush()`ed while the original is intact, the swap last (GPT-010, `docs/dev/bulk-image-jobs.md`) (those post-commit reads are safe only because `AsyncSessionLocal` sets `expire_on_commit=False`). `Path.unlink` is fallible too, so a superseded original goes in that epilogue — which also leaves the row naming a file that exists if the `commit()` itself fails. See `docs/dev/postmortems/PM-013-fs-mutation-before-the-commit.md`.
- **A file the app is still serving cannot be deleted or renamed on Windows.** POSIX unlinks an open file happily, so this whole class is invisible to CI, the suite and the dev container — it has to be stated rather than discovered. Starlette's `FileResponse` holds the file open for the whole body send, and a browser under `preload="metadata"` never finishes consuming that body, so the blocking handle is routinely **Crucible's own**; Python's `open()` does not request `FILE_SHARE_DELETE`, and `os.unlink`/`MoveFileEx` then fail with `ERROR_SHARING_VIOLATION`. Both halves are required. Server side: mutate through `utils.unlink_retrying`/`rename_retrying`/`replace_retrying`, so a socket-teardown race clears within the backoff and anything longer becomes the 409 `main.py` maps `FileInUseError` to — never a 500, and never a partial delete, since the raise must sit before the row goes. Client side: **unmount** the element holding the request before firing the mutation; clearing `src` leaves it attached and fires a spurious `error` event, and no server-side retry can outlast a connection still being held; a modal is not the only trigger (`FileBrowserPage`'s rename is inline). See `docs/dev/postmortems/PM-021-served-file-handle-blocked-windows-delete.md`.
  - **Not every site is converted, on purpose.** The converted sites, and the three deliberate exemptions that stop the next sweep "finishing the conversion", are listed in `docs/dev/shared-utilities.md` § Locked-file mutation: which sites are converted.
- **`is_duplicate` is a relationship, not a column value**: `services/duplicate_service.py` is its only writer, the Technical scan is the authority (it clears every flag it did not group), and every path that deletes `Image` rows prunes via `prune_orphaned_duplicate_flags`. Sites and exemptions: `docs/dev/quality/duplicate-detection.md` § The flag is a relationship, and both directions are maintained (PM-022).
- **Never mutate a loaded JSON column in place.** For JSON columns like `Image.quality_flags`, copy before mutating: `flags = dict(img.quality_flags or {})`, edit `flags`, then reassign `img.quality_flags = flags`. SQLAlchemy's default change detection compares by equality, so mutating and reassigning the *same* dict object looks unchanged and the UPDATE is silently skipped. The correct pattern lives in `services/caption_service.py`.

## Documentation Map

One subsystem per file. Read a file with the Read tool when your task touches its subsystem,
never all of them up front, and never reference one with an `@`-path (§ Maintaining this
documentation). The `Words` column is hand-written and checked; refresh it after a
substantial edit.

| File | Read this when... | Words |
|---|---|---|
| `docs/dev/shared-utilities.md` | Importing anything from `backend/utils.py`, `media_types.py` or `licenses.py` — path guards and containment, naming and collisions, caption sidecars, `record_in_place`, client-supplied regex, batching, the ingestible-extension allowlist, the license vocabulary | ~2100 |
| `docs/dev/ml-models.md` | Working on `backend/ml/` loading, `model_manager`, `backend/ml/device.py`, or how an inference path opens images — `open_rgb_bytes`, the `test_ml_image_opens` AST guard, why torch is imported per function | ~1250 |
| `docs/dev/model-residency.md` | Working on a model unload, `evict_all`, `_evict_lru`, or a consumer's lease on a model — `POST /models/unload-all`, the per-consumer lease scopes, the Grounding DINO tenant in sam2's entry, a hard cancel outliving a lease | ~1500 |
| `docs/dev/model-registry.md` | Adding a model ID or a captioner/scorer module, or changing a registry entry's `kind`/`description` — PaliGemma-2 gating, the WD14 tagger's own cache, where the V2.5 predictor head is cached | ~1300 |
| `docs/dev/image-processing-models.md` | Working on upscaling, `spandrel` model scanning, LUT color grading, or the LaMa inpainter — the upscale job and its PNG fallback, the `.cube`/`.3dl` parsers and the LUT axis-ordering invariant, the hash-pinned TorchScript weight download | ~2750 |
| `docs/dev/detection.md` | Working on the `/detection` router, masks, or `DetectionsPanel` — the endpoints and request validation, per-model overwrite scoping and its `inference_ok` gate | ~2410 |
| `docs/dev/detection-inference.md` | Working on SAM2/SAM3/Florence-2/NudeNet inference or the SAM 3 checkpoint — mask refine and merge geometry, triton and deferred work | ~1340 |
| `docs/dev/quality.md` | **Start here for anything on the Quality page** — scorers and quality flags, thresholds, the aesthetic model picker, `scores_stale` and in-place pixel rewrites, duplicate detection and resolution, style similarity and the style-match meter; its § Where things live table routes from a code path to the file in `docs/dev/quality/` that owns it | ~500 (+ details) |
| `docs/dev/gallery.md` | **Start here for anything on `GalleryPage`** — selection, select-all-matching-filters and pagination, the subfolder sidebar, its context menu and expand/collapse, subfolder rename/move, a gallery filter or sort, drag & drop, reading the drop target and manual ordering, the `gallery-state-*` blob, persisting a gallery filter and debounced filter inputs (PM-012), the detail view's arrows (`gallery-nav-*`, `injectNavId`/`paneGo`, `serverPageContext`, the live refresh and live total); its § Where things live table routes from a code path to the file in `docs/dev/gallery/` that owns it | ~600 (+ details) |
| `docs/dev/image-detail.md` | Working on `ImageDetailPage`, the crop tool, the style-match block or generation-metadata display — caption panel and its image-keyed draft, `StyleMatchPanel` and `DinoLayerBreakdown` | ~2035 |
| `docs/dev/image-files.md` | Working on image upload/move/copy/rename or caption import — drag-`.txt`-onto-image | ~3500 |
| `docs/dev/provenance.md` | Anything touching license, attribution, `source_meta`, or license filters — ingest capture precedence, cross-dataset materialization | ~3425 |
| `docs/dev/captioning.md` | Working on a caption job, `CaptioningPage`'s progress panel, or a caption sidecar write — delimiter modes, refusal stripping, rename-on-caption, `failure_summary` and `result_data` | ~2500 |
| `docs/dev/caption-model-picker.md` | Working on a captioning model picker or the registry's `kind`/`description` — the silent no-op, why `modelType` is the wrong gate | ~900 |
| `docs/dev/llm-providers.md` | Working on the `/providers` router, an OpenAI-compatible provider, `openai_compat_captioner.py`, the Settings LLM Providers tab or `ModelPicker` — `from_orm_row`'s hand-kept field list, `finish_reason` logging, `max_retries=0` | ~800 |
| `docs/dev/export.md` | Working on `ExportPage` or `export_service.py` — the filter table and preview | ~2435 |
| `docs/dev/export-licensing.md` | Working on `CREDITS.md`/`licenses.csv` or an export license filter | ~1325 |
| `docs/dev/bulk-ops.md` | Working on `BulkEditPage` or a `bulk-*` metadata endpoint — Renumber's two-phase rename | ~2180 |
| `docs/dev/bulk-image-jobs.md` | Working on `CropToDetectionForm`, batch resize/crop, or the thumbnail rebuild — `detection_crop_rect`, the crop remap, `batch_inpaint` and the LaMa paint mask | ~3770 |
| `docs/dev/tag-consolidation.md` | Working on `TagConsolidatePage`, `tag_embedder`, or `dedupe_tags` — analyze/apply jobs, whole-tag (non-substring) rewrite | ~895 |
| `docs/dev/video.md` | **Start here for anything video** — the `/videos` router, probe/duration/posters, shot detection and frame heuristics, the extract and re-extract jobs and their dialogs, the crop/trim controls and `NumberField` (any bounded numeric input), video rename/delete and range serving, cropdetect and interlace/telecine, seek-by-timestamp re-extraction, the video screens and the frame lineage filter, video tests and the cv2 gate; its § Where things live table routes from a code path to whichever file in `docs/dev/video/` owns it. Also covers the two `Dataset` columns | ~3100 (+ details) |
| `docs/dev/versioning.md` | Working on `VersionsPage`, `dataset_busy`, or the versioning routes — branches and snapshot tables, prune/GC | ~2570 |
| `docs/dev/versioning-service.md` | Working on `version_service.py` or any path that overwrites or deletes an image file in place | ~3855 |
| `docs/dev/datasets-page.md` | Working on `DatasetsPage`, categories, the duplicate toggle, or the folder picker | ~3265 |
| `docs/dev/statistics.md` | Working on `StatsPage`, `BucketPanel`, or `get_dataset_stats` — the validator-keyed cache | ~2690 |
| `docs/dev/labels.md` | Working on the `/labels` router or the label vocabulary — the `Label`/`image_labels` tables and their migrations, `label_service.py`, `POST /labels/assign`'s chunking, race-safe 409 and busy-dataset guard | ~1700 |
| `docs/dev/label-consumers.md` | Working on a label filter, label export narrowing, or labels across snapshots, copies and derivatives — the EXISTS-only filter block, the `VersionImageState.label_ids` mirror and restore's Pass 2c, `labelParams()`'s falsy-omit encoding | ~1600 |
| `docs/dev/labels-ui.md` | Working on the Settings Labels tab, `LabelsPanel`, `LabelsBulkModal` or the detail-view label hotkeys — the per-image assign queue, `invalidateLabelScope`, the six `GalleryPage` persistence edits | ~1200 |
| `docs/dev/image-filters.md` | Adding or changing a `GET /images/` filter param — plus `GET /images/count` and `/images/ids` and the `ImageFilterParams` model all three share | ~1750 |
| `docs/dev/settings.md` | Working on `SettingsPage`, a new app-wide setting or secret, `threshold_service.py` or `secrets_service.py` — the DB-over-`.env` secret precedence and the `HF_TOKEN` projection | ~2785 |
| `docs/dev/workspace.md` | Working on hardware meters, `LogsPage`, or `BooruPage` — booru tag search, its per-request credential resolution and its TTL cache | ~1365 |
| `docs/dev/file-browser.md` | Working on `FileBrowserPage` or any `/filesystem` endpoint — the move/rename/delete DB-sync guards and their 409s, structural-folder refusals | ~3810 |
| `docs/dev/frontend-core.md` | Working on global frontend state, a shared constants module, or the JS error console — `uploadStore` | ~3255 |
| `docs/dev/frontend-jobs.md` | Adding a job-triggering UI or changing what a finished job invalidates — job-completion cache invalidation (single-job and id-list patterns), the stale-thumbnail warning and the terminal-emit ordering rule | ~3215 |
| `docs/dev/panes-routing.md` | Working on panes, adding a routed page, or lazy page loading — sidebar layout and `usePaneNavigate` | ~1435 |
| `docs/dev/persistence.md` | Adding a storage key or persisting page configuration | ~2250 |
| `docs/dev/styling.md` | Working on Tailwind/CSS, the brand mark, a modal dialog, the right-click menu, or an anchored popover/dropdown — `useModalBehavior` and `ModalShell`, `ContextMenu`, `usePopover`, `LabelPicker` | ~2770 |
| `docs/dev/backend-infrastructure.md` | Working on `main.py` lifecycle, middleware, SSE, the job queue or job cancellation — the Host/Origin request guard (`request_guard.py`, `CRUCIBLE_ALLOWED_HOSTS`), app-level exception handlers, the shutdown/restart loop and `JobQueue.stop()`'s bounded grace-then-cancel shutdown, the SSE broadcaster (terminal events never dropped) and `GET /jobs` filters, stale-job cleanup and retention | ~3655 |
| `docs/dev/backend-tests.md` | Writing or fixing a backend test, or adding a test/CI dependency — which harness a test drives, what `backend/requirements-ci.txt` installs, the `needs_torch`/`needs_cv2`/`needs_symlinks` gates, `api_env` foreign keys, coverage, ruff and the migration drift check | ~320 |
| `docs/dev/database.md` | Working on a model or Alembic migration, an index or FK `ondelete`, deferred columns, `check_migrations.py`, or the startup DB backup — the drift check's `ACCEPTED_DRIFT` and `db_maintenance` rotation | ~1885 |
| `docs/dev/environment-setup.md` | Working on `manage.ps1`/`manage.sh`, the bind address or `--lan`, torch wheels, the startup splash, or the setup/update flow — the Node version check, `bind_host.py`/`CRUCIBLE_HOST` and lockfile reset | ~3140 |
| `docs/dev/comfyui.md` | Working on `ComfyPage`, the `comfy` router, or ComfyUI integration — plans (workflow template + pinned params), prompt rows, prompt library, ComfyClient, removing our own prompt from ComfyUI's queue on cancel/failure | ~3315 |
| `docs/dev/comfy-prompts.md` | Generating prompts with an LLM or working on `prompt_generator.py` — `parse_prompts` filtering, `GeneratePromptsModal` re-attach | ~1880 |
| `docs/dev/comfyui-sync.md` | Working on workflow sync or the bridge extension — "Sync from canvas", `GET /comfy/canvas-workflow` and ComfyUI API constraints | ~710 |
| `docs/dev/postmortems.md` | Doing a code review or investigating a bug — the postmortem index: past incidents as one-line rows with LIVE/MITIGATED/STRUCTURAL status, linking `docs/dev/postmortems/` | ~2170 |
| `docs/dev/roadmap.md` | Deciding what to work on next, landing a task, or picking up a branch mid-way — order and dependencies of open work and the dated "Next session" handoff; tick it in the same session | ~750 |
| `docs/dev/decisions.md` | Asking *why* the project does X, or proposing to change direction — the decision log: what was decided, by whom, and what was rejected | ~310 |

### Code review & bug investigation

When reviewing code (any `/code-review` run or ad-hoc review request) or investigating a bug:

- Always read `docs/dev/postmortems.md` first. Pull a specific detail file from
  `docs/dev/postmortems/` only when the code under review touches that entry's area.
- Treat LIVE and MITIGATED entries as an active checklist for their code class;
  ignore STRUCTURAL entries (kept for history only).
- Do not let them narrow the review. New code can fail in new ways — check for
  novel issues too, not only documented ones.

## Maintaining this documentation

Three rules apply to every documentation change; the full workflow — where a
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
- **Run `python scripts/check_docs.py`** after any documentation change. Fix every
  **FAIL** before you finish. A **WARN** is not a fix-it-now item: an over-budget file
  wants a *split*, never compression — finish the task you were asked to do, record the
  seam in `docs/dev/pending-splits.md`, and leave the prose alone. The split itself is
  the **first** action of the next session that appends to that file, never the last
  action of this one.
