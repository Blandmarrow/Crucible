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
npm run typecheck:e2e  # tsc over frontend/e2e/ — outside the build, see below
npm run lint     # ESLint — CI runs it `continue-on-error`, so it never blocks
```

**`npm run build` is the only real typecheck for `src/`.** The root `tsconfig.json` is a
solution-style config (`"files": []` + project references), so a bare `npx tsc --noEmit`
type-checks **nothing** and exits 0 on code that does not compile. Verify frontend changes
with `npm run build` (which runs `tsc -b`), never with `tsc --noEmit`.

**`frontend/e2e/` is deliberately outside that build** — its specs must never enter
`vite build` — and Playwright only transpiles, so `npm run typecheck:e2e`
(`tsc -p e2e/tsconfig.json --noEmit`, ~2 s) is the only thing that checks them. It is a
blocking CI step and part of the `qa-smoke` skill; run it after touching a spec.

### Tests

Backend pytest (from the repo root, venv active **in the same shell** — a `( … )`
subshell discards activation and silently runs on system Python, which has no
fastapi/sqlalchemy):

```bash
source venv/bin/activate && python -m pytest backend/tests/ -q
```

Request-level tests drive `backend.main.app` over httpx (see
`backend/tests/conftest.py`); everything else is service-level. Anything needing a
decodable video is gated on cv2 — see `docs/dev/video-tests.md` (§ cv2 in CI, and the skip
convention) before adding a test or a CI dependency there. **CI installs
`backend/requirements-ci.txt`** — the floor-pinned, torch-free base set all three CI jobs share
(each adding only its own alembic/uvicorn/cv2 on top); a new import in the app or the
suite's collection path goes there, not into a workflow's install line. Torch is the one
dependency that will never arrive, so a test importing anything under `backend/ml/` past the
pure-numpy modules carries `conftest.needs_torch` (the sibling of `needs_cv2`) — without it
the module *errors* on the runner rather than skipping. Coverage is opt-in:
add `--cov=backend` (or `--cov=backend/routers`) — there is no pytest `addopts`, so
CI runs plain. Lint the backend with `ruff check backend` (config in `ruff.toml`,
scoped to `E9`+`F`). Run `python scripts/check_migrations.py`
after a model/migration change (CI job `migration-drift`). To launch the app itself, use the `run-app` skill.

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

## Architecture

### Data flow

HTTP request → FastAPI router → service layer (pure business logic, no HTTP) → SQLAlchemy async session → SQLite.

Long-running operations (captioning, quality scoring, import, export, batch ops) are queued: the router creates a `BackgroundJob` DB record, enqueues a coroutine in `workers/job_queue.py`, and immediately returns `{job_id}`. The worker runs the job, emits SSE progress events via `workers/progress.py`, and updates the job row when done. The frontend subscribes to `GET /api/v1/jobs/stream/{job_id}` (or `/stream/all/events` for the global progress bar).

`BackgroundJob` has a nullable `label: str | None` column (max 200 chars). Every router that creates a job sets an auto-generated descriptive label (e.g. `"Florence-2 (large) — 50 images"`, `"Quality: technical, aesthetic — 100 images"`) and accepts an optional `label` field in the request body to override it (`body.label or auto_label`). A `_model_short_label(model: str) -> str` helper in `routers/captioning.py` converts raw model IDs to readable names for the auto-label.

This file covers what applies across the whole codebase: commands, the data flow above, and the invariants below. Subsystem detail lives in topic files under `docs/dev/`, each listed with its trigger in the Documentation Map near the end. Read the relevant one(s) before working on that subsystem; do not read all of them up front.

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
  - **Resolve each image against its own source dataset** — a selection can span datasets, so use `licenses.materialize_by_source(rows, ds_by_id)` rather than resolving the batch against `rows[0]`'s dataset; the same goes for the batch's busy guard and stats refresh.
  - **Same-dataset derivatives** (crop, upscale, LUT, detection crop) copy raw values instead, via `copy_provenance` (which deep-copies `source_meta`, so parent and derivative never share one mutable JSON dict). `VersionImageState` mirrors all five image columns — adding a provenance column without the mirror makes a snapshot restore wipe it.
  - **`Image.source_meta` is `deferred=True`**: every reader that loads `Image` **as an ORM entity** (those derivative paths, and `create_snapshot`) must `undefer` it, or `getattr(img, "source_meta")` lazy-loads on an async session and raises `MissingGreenlet` — a failure that only appears on the live async path, never in a helper-level unit test. A `select(Image.source_meta, …)` column list already loads it and needs no undefer, and `batch_move_dataset` omits the column on purpose.
  - **Provenance strings are untrusted input in every document they reach** — `source_name`/`source_url`/`attribution`/free-text `license` come from scrapers, sidecars and EXIF, so anything interpolating them into a generated artifact must neutralise them for that syntax: markdown via `export_service._md_inline`/`_md_link`, CSV cells via `_csv_cell` (leading `=`/`+`/`-`/`@`/TAB/CR), any linked URL via `utils.safe_external_url`. `CREDITS.md` is a legal attribution document built by interpolation — a newline in `attribution` forged a `## <license>` section claiming rights the export did not carry.
- **Videos are sources, not images, and their posters live apart.** A video is a `Video` row in `videos`, flat in `{dataset}/videos/`, never a row in `images`. Poster thumbnails go in `{dataset}/videos/thumbnails/` — a separate directory, *not* the images thumbnail folder with a distinguishing suffix: **ten modules** derive image-thumbnail-stem occupancy from a `thumb_dir.glob("*.webp")` over that folder (enumerated in `docs/dev/video.md`, nine of them building `occupied_thumb_stems` to pick a name and `routers/filesystem.py` to refuse one), and a suffix convention means every one of them must learn to filter it, with any one that forgets being a silent thumbnail clobber. `Dataset.video_count`/`video_size_bytes` are deliberately separate from `image_count`/`total_size_bytes`, and **must be passed explicitly** wherever a `DatasetOut` is hand-built field by field (`list_datasets`): both default to 0, so an omission reports every dataset as video-free instead of failing. A poster is a nicety, never a gate — every *decode* failure in `video_service.generate_poster` is a False, and every caller wraps its encode tail (which can still raise), so a video whose frames will not decode still ingests, lists, plays, renames and deletes. See `docs/dev/video.md`, `docs/dev/video-decode.md` for the decode surface, and `docs/dev/video-extract.md` for frames.
- **A column added to `Image` must be mirrored on `VersionImageState`, and carried by every path that rebuilds an `Image` field by field.** Every one of those paths fails **silently** — a missing mirror means a snapshot restore blanks the column, a missing constructor entry means a copy quietly drops it — which is why the rule is here rather than in a topic file. `backend/tests/test_video_lineage_mirrors.py` holds the structural guard (an explicit `NOT_MIRRORED` allowlist checked in both directions), so the *next* unmirrored column fails CI rather than a restore; it also enumerates the rebuild paths. Three rules decide what such a path writes. See `docs/dev/video-extract.md` for the lineage columns and `docs/dev/video-reextract.md` for the `processing_history` skip rule that follows from them.
  - **Derived-from-elsewhere columns are not copied across a dataset boundary** — `Image.source_video_id` would point at a video the destination does not contain, while `source_timestamp_ms`/`source_shot_index` travel. The one path that *does* carry the videos across — `duplicate_dataset` under `include_videos` — **remaps** `source_video_id` through an old→new id map onto the clone's own video, and falls back to NULL on a miss. Remapped, never copied, and only by the path that carried the videos; every other path still NULLs it.
  - **Immutable columns are mirrored but not diffed** — lineage is written once by extraction, so it is absent from `_DIFF_COLS` on purpose. That carve-out is for immutability alone: `scores_stale` is **mutable** state and so is mirrored *and* diffed (in `_DIFF_COLS` and `_DIFF_COMPARE_FIELDS` both), because an in-place rewrite between two snapshots flips it and nothing else, and a restore that brought back stale scores without the bit would present them as trustworthy.
  - **A `*_score` column is authored data as far as a snapshot is concerned** — nothing recomputes a technical score, so all ten are mirrored *and* diffed, and `NOT_MIRRORED` holds no score. The immutable carve-out above is not license to skip the diff for one.
- **Every path that rewrites an image's pixels in place goes through `utils.record_in_place(img, op, **params)`.** It is the single writer of `Image.processing_history` — pass 2's re-extraction skip guard — *and* `Image.scores_stale`, the bit saying the ten `*_score` columns and the `quality_flags` derived from them were measured on pixels that no longer exist — written only for a row that carries one of those scores, since an unscored row has no measurement to invalidate, while the history entry is written either way. Two writers drift, and the failure is silent in the worse direction: a site that records the edit by hand leaves the scores looking trustworthy, and `exclude_flags` then drops images at export on flags computed against a deleted image. A structural AST guard in `backend/tests/test_scores_stale.py` fails CI for any `processing_history = … + …` list-concat outside `backend/utils.py`. Ten sites today: batch/single resize, batch/single crop, `crop_upscale`, LUT, upscale, crop-to-detection, re-extraction. The bit is cleared **only** by a `routers/quality.py` run that **actually measured something** and refreshed every score the row carries. See `docs/dev/scores-stale.md` and PM-010.
- **A stem-keyed derived artifact needs a collision guard at every site that *writes* it — including the ones that adopt a filename rather than pick one.** Thumbnails and posters are `.webp` keyed by stem, so two files differing only in extension share one derived path and the second write clobbers the first, leaving two rows pointing at one picture. Creation paths pick a free name via `unique_filename_with_thumb`; the two rescan paths instead register files found on disk under the names the user gave them, and resolve collisions in **opposite** directions on purpose — `rescan_dataset` renames the *image file* (`a.jpg` → `a_001.jpg`) because eleven sites re-derive an image's thumbnail path from its filename (enumerated in the code comment at that rename branch), so a drifted thumbnail stem would be orphaned by the next rename/move/crop/restore; `_rescan_videos` renames the *poster* (`clip_001.webp`) and leaves the video file alone, because nothing re-derives a poster path — every consumer reads `Video.poster_path`. When reviewing any new path that writes a derived file, ask which of the two it is. A path registering a file already in place must also pass `disk_exclude={f.name}`, or the uniquifier sees each file's own name occupied and renames everything.
  - An **unregistered** file sitting at a rename's target path has no row to guard it and must be refused, not clobbered. (A *pure extension change* — `a.jpg` → `a.png`, from a re-extraction or a PNG format fallback — is otherwise the one rename that disturbs nothing derived: the stem is unchanged, so the thumbnail and the `.txt` sidecar stay put, and no uniquifier call is needed.) See `docs/dev/video-reextract.md` § The extension change.
- **Nothing fallible between an irreversible filesystem mutation and the `commit()` that describes it.** A transaction rolls back a row; nothing rolls back an `os.replace` or an overwrite. Write the file, assign **every** row field, `commit()`, then run whatever can raise in a `try/except` that logs and cannot change the item's outcome or feed a failure breaker (those post-commit reads are safe only because `AsyncSessionLocal` sets `expire_on_commit=False`). `Path.unlink` is fallible too, so a superseded original goes in that epilogue — which also leaves the row naming a file that exists if the `commit()` itself fails. See `docs/dev/postmortems/PM-013-fs-mutation-before-the-commit.md`.
- **A file the app is still serving cannot be deleted or renamed on Windows.** POSIX unlinks an open file happily, so this whole class is invisible to CI, the suite and the dev container — it has to be stated rather than discovered. Starlette's `FileResponse` holds the file open for the whole body send, and a browser under `preload="metadata"` never finishes consuming that body, so the blocking handle is routinely **Crucible's own**; Python's `open()` does not request `FILE_SHARE_DELETE`, and `os.unlink`/`MoveFileEx` then fail with `ERROR_SHARING_VIOLATION`. Both halves are required. Server side: mutate through `utils.unlink_retrying`/`rename_retrying`/`replace_retrying`, so a socket-teardown race clears within the backoff and anything longer becomes the 409 `main.py` maps `FileInUseError` to — never a 500, and never a partial delete, since the raise must sit before the row goes. Client side: **unmount** the element holding the request before firing the mutation; clearing `src` leaves it attached and fires a spurious `error` event, and no server-side retry can outlast a connection still being held; a modal is not the only trigger (`FileBrowserPage`'s rename is inline). See `docs/dev/postmortems/PM-021-served-file-handle-blocked-windows-delete.md`.
  - **Which sites are converted is a fact worth stating, because the list is not "all of them".** `images.py` and `filesystem.py` are wholly unconverted. Three sites in `videos.py` are converted — `delete_video`, `rename_video`, and pass 2's in-place frame overwrite, which is a *job* and so counts the lock as one failed frame instead of answering 409. Three more are deliberately **exempt**, and naming them is what stops the next sweep "finishing the conversion": `_delete_previous_frames`, `_rewrite`'s superseded-original unlink, and `video_service.generate_poster`'s poster replace (which is synchronous anyway). All three already log and continue, and what a lock costs there is re-derivable — a stray file a rescan adopts, or a missing poster. Pick the helper by the call being replaced: `replace_retrying` is the overwriting form and `rename_retrying` is not a substitute for it, since `Path.rename` refuses an existing destination on Windows while clobbering silently on POSIX — a swap that passes CI and breaks only on the platform the rule is about.
- **Never mutate a loaded JSON column in place.** For JSON columns like `Image.quality_flags`, copy before mutating: `flags = dict(img.quality_flags or {})`, edit `flags`, then reassign `img.quality_flags = flags`. SQLAlchemy's default change detection compares by equality, so mutating and reassigning the *same* dict object looks unchanged and the UPDATE is silently skipped. The correct pattern lives in `services/caption_service.py`.

## Documentation Map

Each file below covers one subsystem in depth. Read the relevant file(s) with the Read tool when
your task touches that subsystem — do not read all of them up front. Do NOT use `@`-paths to reference
these files anywhere — `@path` auto-loads the target into every conversation, defeating this split.
The `Words` column is hand-written: `check_docs.py` WARNs when it drifts by more than 5% (min 50
words), and again if a row loses the cell, so refresh it after a substantial edit.

| File | Read this when... | Words |
|---|---|---|
| `docs/dev/shared-utilities.md` | Importing anything from `backend/utils.py`, `media_types.py` or `licenses.py` — path guards and containment, naming and collisions, caption sidecars, `record_in_place`, client-supplied regex, batching, the ingestible-extension allowlist, the license vocabulary, the shared frontend components | ~1235 |
| `docs/dev/ml-models.md` | Working on captioning models, upscaling, LUT grading, or `backend/ml/` loading — the model manager (VRAM/unload), model ID registry, device abstraction, EXIF-consistent opening, TorchDynamo | ~3175 |
| `docs/dev/detection.md` | Working on the `/detection` router, masks, or `DetectionsPanel` — the endpoints and request validation, `DetectionJobRequest` scope, per-model overwrite scoping, `mask_area`, watermark flag sync | ~1940 |
| `docs/dev/detection-inference.md` | Working on SAM2/SAM3/Florence-2/NudeNet inference or the SAM 3 checkpoint — tasks by model family, multi-phrase prompts, mask refine and merge geometry, the safetensors loader, triton and deferred work | ~1235 |
| `docs/dev/scoring.md` | Working on `QualityPage`, quality flags, or thresholds — the scorers and the columns they write, the failure contract when nothing can be measured, the flag thresholds, e2e coverage | ~1320 |
| `docs/dev/scores-stale.md` | Working on the `scores_stale` bit, an in-place pixel rewrite, or the re-score clear predicate — `record_in_place` as single writer, `score_columns`, `_JOB_SCORE_COLUMNS`, the badge/chip/export surfaces | ~1450 |
| `docs/dev/image-similarity.md` | Working on duplicate detection, pHash grouping, or style similarity — `find_duplicates_sync`'s two implementations, same-source duplicate groups, the `embedding_type` modes, all-layers DINOv2 scoring | ~1535 |
| `docs/dev/gallery.md` | Working on `GalleryPage` or gallery drag & drop — selection and shift-click ranges, subfolder sidebar, filters, manual ordering, dnd-kit droppables and collision detection | ~3375 |
| `docs/dev/image-detail.md` | Working on `ImageDetailPage` or generation-metadata display — gallery/nav persisted keys, `injectNavId`/`paneGo`, crop tool, selection toggle, caption panel | ~2160 |
| `docs/dev/image-files.md` | Working on image upload/move/copy/rename or caption import — naming and collisions, cross-dataset move and copy, folder import, rescan/sync, drag-`.txt`-onto-image | ~2555 |
| `docs/dev/provenance.md` | Anything touching license, attribution, `source_meta`, or license filters — the `backend/licenses.py` vocabulary, dataset→image inheritance, ingest capture precedence, cross-dataset materialization | ~3425 |
| `docs/dev/captioning.md` | Working on `CaptioningPage`, LLM provider integration, or a caption sidecar write — post-processing (delimiter modes, refusal stripping, rename-on-caption), `ModelPicker` | ~1760 |
| `docs/dev/export.md` | Working on `ExportPage` or `export_service.py` — kohya/ai-toolkit/plain, the shared export loop, stem uniquification, the filter table and preview, resize, loss masks, captions-only | ~2165 |
| `docs/dev/export-licensing.md` | Working on `CREDITS.md`/`licenses.csv` or an export license filter — the manifest lifecycle and supersede rule, `_md_inline`/`_csv_cell` neutralisers, `license_filter` encoding, the Export page license panel | ~1325 |
| `docs/dev/bulk-ops.md` | Working on `BulkEditPage` or a `bulk-*` metadata endpoint — the scope triple `_apply_bulk_filters`, caption find/replace/regex, bulk rename/delete/provenance/reorder, Renumber's two-phase rename | ~2030 |
| `docs/dev/bulk-image-jobs.md` | Working on `CropToDetectionForm`, batch resize/crop, or the thumbnail rebuild — the bulk jobs that rewrite files: `detection_crop_rect`, the crop remap, `thumbnails_stale`, PM-013 loop ordering | ~2210 |
| `docs/dev/tag-consolidation.md` | Working on `TagConsolidatePage`, `tag_embedder`, or `dedupe_tags` — the MiniLM tag embedder, analyze/apply jobs, whole-tag (non-substring) rewrite, preview/confirm UI | ~895 |
| `docs/dev/video.md` | **Start here for anything video** — its § Where things live table routes from a code path to whichever of the eleven sibling docs owns it. Also covers the `Video` model and `videos/` layout, poster stems and collisions, the three ingest paths, the two `Dataset` columns | ~2610 |
| `docs/dev/video-endpoints.md` | Working on the `/videos` router, video rename/delete, or range serving — the twelve routes, `GET /capabilities`' declaration order, `frames-summary`, the delete ordering and the containment gates | ~1330 |
| `docs/dev/video-tests.md` | Adding a video test, a cv2-gated test, or a CI media dependency — the arc's test index, the four `mp4_*` fixtures and their constraints, branches no fixture reaches, cv2/scenedetect in CI, the skip convention | ~1720 |
| `docs/dev/video-ui.md` | Working on a video browsing screen or the frame lineage filter — `VideoStrip` and its selection, `VideoDetailPage`, the extraction history panel, the `ImageDetailPage` lineage row and its gallery deep link | ~2835 |
| `docs/dev/video-extract-ui.md` | Working on the extraction modal or its progress rows — `ExtractFramesModal`'s two steps, the touched-flag guards, `ExtractProgressList`, `useVideoExtractJobs` and the extraction re-attach | ~2700 |
| `docs/dev/video-extract-controls.md` | Working on the crop/trim controls or any bounded numeric input — `CropOverlay`'s mattes and even-snap, `TrimBar`'s tail semantics and arrow keys, `NumberField`'s draft contract | ~1590 |
| `docs/dev/video-decode.md` | Working on video probe/metadata, video duration, or poster frames — the cv2 probe ladder, `measure_duration_ms`, `isOpened()` as ingest gate, the poster fallback ladder | ~2465 |
| `docs/dev/video-shots.md` | Working on probe sampling, shot detection, or frame rendering — the pass 1 pipeline in `video_extract.py`: the RSS rule, the PySceneDetect contract and cost cliff, `render_shot`/`_write_frame` | ~2225 |
| `docs/dev/video-extract.md` | Working on the extract/probe endpoints or the `video_extract` job — Pass 1's router half: crop/trim validation, subfolder modes, step order and the replace delete, SSE progress, frame lineage | ~3020 |
| `docs/dev/video-reextract.md` | Working on re-extraction, `POST /videos/reextract`, or a replace-mode extension change — Pass 2 full-res: seek-by-timestamp, the `processing_history` skip rule, `render_at_timestamps` | ~3620 |
| `docs/dev/video-reextract-ui.md` | Working on the re-extract dialog or what a finished `video_reextract` refreshes — `ReextractFramesForm`/`ReextractFramesModal`, the three entry points, the `jobStore` adoption re-attach, the `TopBar` invalidations | ~1225 |
| `docs/dev/video-heuristics.md` | Tuning cropdetect, interlace/telecine detection, or which frame a shot yields — the pure-numpy `video_frames.py` judgement calls, sharpness, `pick_index` candidate rejection | ~1435 |
| `docs/dev/versioning.md` | Working on `VersionsPage`, `dataset_busy`, or the versioning routes — the versioning-mode guard, branches and snapshot tables, the content-addressed object store, prune/GC, the frontend | ~1940 |
| `docs/dev/versioning-service.md` | Working on `version_service.py` or any path that overwrites or deletes an image file in place — `protect_file_before_overwrite`/`mark_image_deleted_in_versions` and their call sites, `create_snapshot`, restore's four passes, diff | ~2220 |
| `docs/dev/datasets-page.md` | Working on `DatasetsPage`, categories, the duplicate toggle, or the folder picker — preview strip, license badge, sort/density/grouping, category rail, `ImportFolderModal`/`DirPickerModal` | ~3265 |
| `docs/dev/statistics.md` | Working on `StatsPage`, `BucketPanel`, or `get_dataset_stats` — stats queries and live polling, server-side aggregation, the validator-keyed cache, editable histograms | ~2555 |
| `docs/dev/image-filters.md` | Adding or changing a `GET /images/` filter param — the shared listing contract behind the gallery filter bar, stats click-through, frame lineage and the license filters | ~830 |
| `docs/dev/settings.md` | Working on `SettingsPage`, a new app-wide setting, or `threshold_service.py` — the `ThresholdSettings` singleton row and every tab it backs | ~1145 |
| `docs/dev/workspace.md` | Working on hardware meters, `LogsPage`, or `BooruPage` — the sidebar CPU/RAM/GPU meters and `/system`, job history + the JS error console, booru tag search and its TTL cache | ~1095 |
| `docs/dev/file-browser.md` | Working on `FileBrowserPage` or any `/filesystem` endpoint — the eight endpoints, the move/rename/delete DB-sync guards and their 409s, structural-folder refusals, path safety | ~3525 |
| `docs/dev/frontend-core.md` | Working on global frontend state, a shared constants module, or the JS error console — TanStack Query/Zustand conventions, the `SelectionToolbar` action modals, `uploadStore` | ~2305 |
| `docs/dev/frontend-jobs.md` | Adding a job-triggering UI or changing what a finished job invalidates — SSE hooks, `jobStore`, job labels, job-completion cache invalidation (single-job and id-list patterns), the stale-thumbnail warning and the terminal-emit ordering rule | ~2050 |
| `docs/dev/panes-routing.md` | Working on panes, adding a routed page, or lazy page loading — sidebar layout, the split-view pane manager, `usePaneNavigate`, the six-site routed-page checklist | ~1065 |
| `docs/dev/persistence.md` | Adding a storage key or persisting page configuration — the `constants/storage.ts` key registry, `loadPersisted`/`useDebouncedPersist`, the three persistence shapes | ~2040 |
| `docs/dev/styling.md` | Working on Tailwind/CSS, the brand mark, or any modal dialog — CSS variable tokens, `@layer components` classes, `CrucibleMark` drift checks, `ConfirmDialog`, `useModalBehavior` | ~1610 |
| `docs/dev/backend-infrastructure.md` | Working on `main.py` lifecycle, Alembic migrations, SSE, or job cancellation — production frontend serving, the shutdown/restart loop and `JobQueue.stop()`'s open hang, DB indexes and deferred columns, the SSE broadcaster | ~3550 |
| `docs/dev/environment-setup.md` | Working on `manage.ps1`/`manage.sh`, torch wheels, the startup splash, or the setup/update flow — venv ML packages, PyTorch GPU auto-detection, SAM2/SAM3 install, lockfile reset | ~2685 |
| `docs/dev/comfyui.md` | Working on `ComfyPage`, the `comfy` router, or ComfyUI integration — plans (workflow template + pinned params), prompt rows, prompt library, the `comfy_generate` job, ComfyClient | ~2815 |
| `docs/dev/comfy-prompts.md` | Generating prompts with an LLM or working on `prompt_generator.py` — the one-shot generate endpoint, the durable `comfy_prompts` job, `parse_prompts` filtering, `GeneratePromptsModal` re-attach | ~1880 |
| `docs/dev/comfyui-sync.md` | Working on workflow sync or the bridge extension — "Sync from canvas", `GET /comfy/canvas-workflow`, the `ComfyUI-CrucibleBridge` extension (`extras/`), history-pull fallback, ComfyUI API constraints | ~710 |
| `docs/dev/postmortems.md` | Doing a code review or investigating a bug — the postmortem index: past incidents as one-line rows with LIVE/MITIGATED/STRUCTURAL status, linking `docs/dev/postmortems/` | ~1385 |

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
- **Run `python scripts/check_docs.py`** after any documentation change. Fix every
  **FAIL** before you finish. A **WARN** is not a fix-it-now item: an over-budget file
  wants a *split*, never compression — finish the task you were asked to do, record the
  seam in `docs/dev/pending-splits.md`, and leave the prose alone. The split itself is
  the **first** action of the next session that appends to that file, never the last
  action of this one. Budgets count words, not lines, and any paragraph over 250 words
  warns too — see the script's module docstring for why.
