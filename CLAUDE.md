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

This file covers conventions that apply across the whole codebase: commands, the request/job data flow above, the key invariants and shared utilities below. Subsystem-specific details live in topic files under `docs/dev/`, each listed with its trigger in the Documentation Map near the end of this file. Read the relevant topic file(s) with the Read tool before working on that subsystem; do not read all of them up front.

### Shared utilities

`backend/utils.py` — thin module for helpers shared across multiple routers. Currently contains:

- **Client-supplied path/URL validators.** `normalize_subfolder(s: str) -> str` — strips leading/trailing slashes and `.` segments, rejects `..` with HTTP 400. Import this; never copy the logic inline or re-import it from a router. `sanitize_abs_path(path: str) -> Path` validates a client-supplied filesystem path (rejects null bytes and relative paths with HTTP 400). Use in every router that accepts an arbitrary path (`filesystem`, `comfy` workflow scan); never re-inline the check. Its sibling `safe_external_url(value) -> str` returns a URL only if it is `http`/`https` with no whitespace or control characters, else `""` — the only sanctioned check before a **provenance** URL reaches a markdown link target or an `href` (mirrored client-side by `frontend/src/utils/url.ts::safeExternalUrl`).
- `slugify_filename(name: str) -> str` — lowercases, removes non-word characters, collapses whitespace/underscores/hyphens to `_`, strips leading/trailing `_-`, truncates to 200 chars. Returns `"image"` if the result is empty.
- `unique_filename(directory: Path, stem: str, suffix: str, db_names: set, disk_exclude: set[str] | None = None) -> str` — returns a filename not on disk and not in `db_names`. Tries `{stem}{suffix}` first, then `{stem}_001{suffix}`, `_002`, … Both checks are required: `db_names` covers in-flight batch collisions within the same request; the filesystem check covers files that exist but have no DB record. `disk_exclude` names files that exist on disk but should be treated as absent (files being renamed away in the same batch — used by bulk-rename renumbering so the counter restarts from `001` instead of skipping past the source files).
- `unique_filename_with_thumb(images_dir, stem, suffix, db_names, occupied_thumb_stems, planned_thumb_stems) -> str` — like `unique_filename` but also avoids thumbnail-stem collisions. Thumbnails are always `.webp` keyed by image stem, so two images with different extensions but the same stem would share a thumbnail path. Call this instead of `unique_filename` in every code path that creates or renames an image file and associates a thumbnail with it. Mutates `db_names` (adds the chosen filename) and `planned_thumb_stems` (adds the chosen stem) so subsequent calls within the same batch stay consistent. Build `occupied_thumb_stems` from `thumb_dir.glob("*.webp")` once before the loop; do **not** exclude the stems of images being renamed/moved from this set — doing so re-introduces the within-batch clobber bug where one image's new thumbnail path matches another's current path.
- **Caption sidecar helpers.** `rename_with_sidecar(old_path, new_path)` / `copy_with_sidecar(old_path, new_path)` — move or copy a file together with its `.txt` sidecar (if present) in one call; the copy variant uses `shutil.copy2` and leaves the source intact. Use these everywhere a file is renamed or copied; never copy the two-step pattern inline. `read_caption_sidecar(image_path: Path | str) -> str | None` reads the `.txt` sidecar next to an image (the read-side counterpart of `caption_service._write_txt_sidecar`), returning the stripped text of `{stem}.txt` if present and non-empty, else `None` — use it everywhere a sidecar caption is read (folder import, rescan/sync, standalone caption import); never inline the `.with_suffix(".txt")` logic. See `docs/dev/image-files.md` (§ Importing captions & folder rescan).
- `compile_user_regex(pattern: str)` / `regex_sub_deadline(compiled, repl, text, deadline: float)` / `REGEX_TIMEOUT_SECONDS` / `regex_error` — the only sanctioned way to run a **client-supplied** regex. `compile_user_regex` raises `regex_error` (map to HTTP 400); `regex_sub_deadline` substitutes under an absolute `time.monotonic()` deadline and raises `TimeoutError` (map to HTTP 408) so one budget covers a whole batch instead of N × timeout. See the Key invariant below for why stdlib `re` is unusable here. Used by the `comfy` rows bulk-edit and both `caption_service` regex paths.
- `chunked(seq, size=10_000) -> Iterator[Sequence]` — yields successive `size`-length slices of a sequence. The single source of truth for splitting id lists before an SQL `IN (...)` so bind-parameter count stays under SQLite's 999-variable limit; use it in every batched `IN` query (detection crop worker, `_fetch_bboxes_by_image`, `export_service._fetch_detections_by_image`). Never re-inline a `range(0, len(x), N)` slice loop.
- `ALLOWED_FLAG_KEYS: frozenset` — the canonical set of valid quality flag names (`is_blurry`, `is_noisy`, `is_uniform`, `has_watermark`, `is_duplicate`, `is_nsfw`, `has_ai_artifacts`). Import this wherever flag names must be validated or used in SQL filters; never redefine the set locally.
- `normalize_image_format(suffix: str, out_path: str) -> tuple[str, str]` — normalises a file suffix to a PIL format name (`JPG`→`JPEG`, unsupported→`PNG`); returns `(fmt, out_path)`, where `out_path` may be updated when the format falls back to PNG (extension changes). Pair it with `image_save_kwargs(fmt: str) -> dict`, which returns the PIL `save()` kwargs for that format (JPEG → `{quality: 95, subsampling: 0}`; others → `{}`). Use both in any image-save path; do not inline the JPG/PNG fallback logic again.
- `require_free_space(target_dir, needed_bytes=0)` / `InsufficientDiskSpaceError` — disk preflight for any run that writes many files (export, folder import): requires `max(needed × 1.2, 256 MB)` free on the destination volume. `needed_bytes=0` is the cheap request-path form (→ HTTP 507). Never inline `shutil.disk_usage`.
- `format_bytes(n) -> str` — human byte size for user-facing messages (`"1.4 GB"`). Prose only; never for filenames or ids.
- `thumbnail_path_for(image_path: Path | str) -> str` — derives the `.webp` thumbnail path for an image sitting in a dataset `images/` folder (`parent.parent/thumbnails/{stem}.webp`). Use in any router that creates or regenerates thumbnails; never reconstruct the path manually.
- `poster_path_for(video_path) -> str` / `unique_poster_path(poster_dir, stem, claimed) -> Path` — the video-poster counterparts. `poster_path_for` is `parent/thumbnails/{stem}.webp` (**`parent`**, not `thumbnail_path_for`'s `parent.parent` — videos are flat in `videos/`) and is only the *proposal*: unlike an image thumbnail, a poster's stem is not guaranteed to match its video's, so read `Video.poster_path` for an existing row. `unique_poster_path` resolves that proposal against a claimed-stem set from `video_service.claimed_poster_stems`, stepping `_001` like `unique_filename`. It is the tool for paths that **adopt** a filename off disk instead of picking one — see the invariant below. Never rebuild either path inline.
- `safe_dataset_path(path_str: str, base_dir: Path) -> Path` — resolves a stored file path and raises HTTP 403 if it escapes `base_dir`. The paths are DB-sourced rather than client-supplied, but a row can carry a path written by an earlier import or a hand edit, so every endpoint turning a stored path into a `FileResponse` goes through it (image file/thumbnail, video file/poster). Never re-inline the prefix check.
- `subsume_tags(tags: list[str]) -> list[str]` — order-stable tag dedup: drops any tag that is a whole-word subsequence of a longer tag in the same list (`tail` when `long tail` present) and collapses case-insensitive exact duplicates. Whole-word matching means `car` does not subsume `scar`/`carpet`. Single source of truth for the captioning `dedupe_tags` post-processing flag and for the per-caption subsumption cleanup exposed via the `tag-consolidation` router's `subsume` endpoint (the Consolidate Tags page "Quick cleanup", the `SelectionToolbar` "Merge tags" action, and the `ImageDetailPage` per-image button) — never reimplement the rule. See `docs/dev/tag-consolidation.md`.
- `count_caption_tokens(text: str | None) -> int` / `_get_enc()` — GPT-2 BPE token count of a caption (empty/whitespace/None → 0), backed by an `@lru_cache` tiktoken encoder (`tiktoken` imported lazily). This is the single tokenizer entry point; never call `tiktoken.get_encoding("gpt2")` inline. **`Image.caption_token_count` is a persisted column kept in sync by a SQLAlchemy `set` listener on `Image.caption_text`** (in `backend/models/image.py`), which calls `count_caption_tokens` on every ORM assignment (including `Image(caption_text=...)` constructor kwargs). Consumers (Stats aggregation in `dataset_service.py`, the gallery token filter in `routers/images.py`) read the column and never tokenize in the request path. **Invariant: captions must always be written via ORM attribute assignment** — a raw `update(Image)` / SQL write to `caption_text` bypasses the listener and leaves `caption_token_count` stale. No such bulk-update write to `caption_text` exists today; keep it that way.

**`backend/media_types.py`** — the single allowlist of ingestible file types: `IMAGE_EXTENSIONS`, `VIDEO_EXTENSIONS`, `MEDIA_EXTENSIONS`, `media_kind_for(suffix) -> "image" | "video" | None`, `video_mime(suffix)` and `codec_label(fourcc)`. Import from here in any code path that decides whether a file can be ingested; never write a local extension set — three of them had already drifted before this module existed. `.avif` is gated on `features.check("avif")` because AVIF is a build-time Pillow feature, not a version guarantee. Discriminators stay `media_kind`-shaped, never `is_video` booleans, so the eventual audio arc adds a value rather than a second flag. See `docs/dev/video.md`.

**`backend/licenses.py`** — the license vocabulary (`LICENSES`, `LICENSE_IDS`, `FIELD_MAX_LEN`, `normalize_license`, `license_info`, `allows_commercial`; `frontend/src/constants/licenses.ts` mirrors the ids, **every** `LicenseInfo` field and `FIELD_MAX_LEN`, and a test enforces the whole match — `allows_commercial`/`no_derivatives` decide what an export ships, so a divergence there is a rights error) and the provenance rules built on it. Import from here; never hardcode a license id list or re-inline the inheritance coalesce. Read side: `resolve_provenance(img, ds)`, the NULL-coalescing image-over-dataset read — duck-typed, and must not import models or it re-creates an import cycle. Write side: `merge_provenance(*layers)` (left-wins ingest merge, clamping), `clamp_provenance(values)`, `normalize_license_input(v)` (the Pydantic validator — normalize **then** length-check), `copy_provenance(img)` (same-dataset derivative), `materialize_provenance(img, ds)` (cross-dataset copy/move), `materialize_by_source(rows, ds_by_id)` (the batch form, resolving each row against its own source dataset). **Ingest truncates; the API rejects** — an import must never fail on a bad sidecar, and an API client must never silently lose data. A **client-supplied** license-id list is read only through `utils.parse_license_filter_param` / `normalize_license_filter`: a JSON array, never comma-separated, because an `other:<free text>` id may contain commas. An empty list always means "no filter", never "match nothing". `""` inside the list is **not** uniform across the API: it is a meaningful entry (no license recorded) for the **export** filters, while `GET /images/` expresses unlicensed through its separate `license_missing` param and **rejects (400) any list containing a blank entry** — dropping it would silently narrow a mixed list or void the filter entirely. Check which endpoint you are on before relying on it. See `docs/dev/provenance.md`.

**Shared frontend components**: `SelectionToolbar`, `MoveToDatasetModal`, `ConfirmDialog`, `GenerationMetadata`, `DirPickerModal`, and `JobProgressBar` are reusable components referenced from multiple subsystem doc files below — don't be surprised when the same component name recurs across files. Each is documented where it's most central: `SelectionToolbar`'s modal/cache-invalidation conventions in `docs/dev/frontend-core.md` (§ Frontend state) and `ConfirmDialog` (plus `useModalBehavior`, which every modal spreads) in `docs/dev/styling.md`; `MoveToDatasetModal` in `docs/dev/image-files.md` (§ Image file naming) and `GenerationMetadata` in `docs/dev/image-detail.md` (§ AI generation metadata); `DirPickerModal` (the in-app "Browse…" folder picker, used by folder/caption import and Export) in `docs/dev/datasets-page.md`; `JobProgressBar` in `docs/dev/versioning.md` (§ Dataset versioning, Frontend). Other files document only how that subsystem *uses* them.

### Key invariants

- **DB-before-filesystem for batch rename/move.** `bulk_rename` and `batch_move_dataset` commit DB changes *before* the filesystem renames so the DB is always the authoritative record of intended file locations. If a rename fails mid-batch, the DB reflects the final intended state; do not revert this ordering. `batch_copy_dataset` uses the opposite ordering (stage DB inserts, do copies, then commit) because an incomplete copy should leave nothing — see the cross-dataset copies note above.
- **Always `ImageOps.exif_transpose()` first.** Every Pillow operation in `image_service.py` calls this before anything else to correct orientation from EXIF data. The same rule holds for every ML inference path: open images through `backend/ml/image_utils.py::open_rgb` (`Image.open().convert("RGB")` + `exif_transpose`), never a bare `Image.open`, so all predictors work in one transposed frame and normalized coordinates denormalize consistently. `image_service._open_safe` is the equivalent gate for image-processing paths.
- **Close PIL Images after preprocessing.** In all ML inference paths (`aesthetic_scorer.py`, `dino_scorer.py`, all captioners) call `img.close()` immediately after the image has been passed to the model's preprocessor/processor — before the GPU inference runs. In `export_service.py::_write_image()` use a `try/finally` block. This frees the decoded pixel buffer (potentially several MB per image) during slow inference and prevents accumulation across large batches.
- **Absolute DB path.** `config.py` derives the database URL from `Path(__file__).parent.parent` so it resolves correctly regardless of the working directory when uvicorn is launched.
- **Path traversal guard.** A stored file path must be proven to resolve inside `settings.datasets_dir` before it becomes a `FileResponse`. `utils.safe_dataset_path` (see Shared utilities above) is the one implementation; every image and video file/thumbnail/poster route goes through it, and no router keeps a private copy.
- **Never run a client-supplied regex through stdlib `re`.** Use `compile_user_regex` + `regex_sub_deadline` from `backend/utils.py` (the `regex` package). `re`'s matching loop is C code that never releases the GIL and cannot be interrupted, so a catastrophic pattern freezes the entire process — and the obvious guard does not work: wrapping it in `run_in_executor` + `asyncio.wait_for` can never fire, because the event loop can't be scheduled to fire it, and Python cannot kill the thread. Verified: `(a|a)*$` against 30 `a`s takes `re` **105 s** at 100% GIL, versus a clean `TimeoutError` from `regex` with the loop still live. Hardcoded patterns and `re.escape`'d literals (`slugify_filename`, `subsume_tags`) are fine on `re` — they can't backtrack. A comment claiming a thread + `wait_for` bounds a regex is the bug, not the fix.
- **Provenance NULL means inherit; materialize it on cross-dataset copy/move.** An `Image` whose `source_name`/`source_url`/`license`/`attribution` is NULL **or `""`** inherits its `Dataset`'s default, resolved at read time by `licenses.resolve_provenance` — which coalesces on *falsiness*, so a blank string is not "explicitly nothing"; recording that needs a real value (the `no-license` id, a written-out attribution).
  - **Cross-dataset.** Any code path that moves or copies an image into a *different* dataset must call `licenses.materialize_provenance(img, source_dataset)` and write concrete values, or the image silently re-inherits the destination's unrelated default. `duplicate_dataset` is the one sanctioned exception: it copies the four dataset defaults onto the new dataset first, so raw `copy_provenance` keeps inheritance equivalent.
  - **Resolve each image against its own source dataset** — a selection can span datasets, so use `licenses.materialize_by_source(rows, ds_by_id)` rather than resolving the batch against `rows[0]`'s dataset; the same goes for the batch's busy guard and stats refresh.
  - **Same-dataset derivatives** (crop, upscale, LUT, detection crop) copy raw values instead, via `copy_provenance` (which deep-copies `source_meta`, so parent and derivative never share one mutable JSON dict). `VersionImageState` mirrors all five image columns — adding a provenance column without the mirror makes a snapshot restore wipe it.
  - **`Image.source_meta` is `deferred=True`**: every reader that loads `Image` **as an ORM entity** (those derivative paths, and `create_snapshot`) must `undefer` it, or `getattr(img, "source_meta")` lazy-loads on an async session and raises `MissingGreenlet` — a failure that only appears on the live async path, never in a helper-level unit test. A `select(Image.source_meta, …)` column list already loads it and needs no undefer, and `batch_move_dataset` omits the column on purpose.
  - **Provenance strings are untrusted input in every document they reach** — `source_name`/`source_url`/`attribution`/free-text `license` come from scrapers, sidecars and EXIF, so anything interpolating them into a generated artifact must neutralise them for that syntax: markdown via `export_service._md_inline`/`_md_link`, CSV cells via `_csv_cell` (leading `=`/`+`/`-`/`@`/TAB/CR), any linked URL via `utils.safe_external_url`. `CREDITS.md` is a legal attribution document built by interpolation — a newline in `attribution` forged a `## <license>` section claiming rights the export did not carry.
- **Videos are sources, not images, and their posters live apart.** A video is a `Video` row in `videos`, flat in `{dataset}/videos/`, never a row in `images`. Poster thumbnails go in `{dataset}/videos/thumbnails/` — a separate directory, *not* the images thumbnail folder with a distinguishing suffix: eight **modules** build `occupied_thumb_stems` from `thumb_dir.glob("*.webp")` (enumerated in `docs/dev/video.md`; more call sites than modules), and a suffix convention means all eight must learn to filter it, with any one that forgets being a silent thumbnail clobber. `Dataset.video_count`/`video_size_bytes` are deliberately separate from `image_count`/`total_size_bytes` — and must be passed explicitly wherever a `DatasetOut` is hand-built field by field (`list_datasets`), since both default to 0 and an omission reports every dataset as video-free instead of failing. A poster is a nicety, never a gate: `video_service.generate_poster` returns False rather than raising, so a video whose frames will not decode still ingests, lists, plays, renames and deletes. See `docs/dev/video.md`, `docs/dev/video-decode.md` for the probe/poster decode surface itself, and `docs/dev/video-extract.md` for turning a video into frames.
- **A column added to `Image` must be mirrored on `VersionImageState`, and carried by every path that rebuilds an `Image` field by field.** There are eight: `create_snapshot`, the restore write-back, both branches of `duplicate_dataset`, `batch_copy_dataset`, `batch_move_dataset`, and the two model definitions themselves. Every one fails **silently** — a missing mirror means any snapshot restore blanks the column, and a missing constructor entry means a copy quietly drops it. `backend/tests/test_video_lineage_mirrors.py` holds the structural guard: `Image` columns − `VersionImageState` columns must equal an explicit `NOT_MIRRORED` allowlist, in both directions, so the *next* unmirrored column fails CI rather than a restore. Two rules decide what a rebuild path writes. **Derived-from-elsewhere columns are not copied across a dataset boundary**: `Image.source_video_id` (frame lineage, `ondelete="SET NULL"`) is NULLed by `batch_copy_dataset`, `batch_move_dataset` and both `duplicate_dataset` branches, because it would point at a video the destination does not contain — while `source_timestamp_ms`/`source_shot_index` travel, being facts about the frame rather than about which dataset holds it. **Immutable columns are mirrored but not diffed**: lineage is written once by extraction and never changes, so it is absent from `_DIFF_COLS` on purpose. `licenses.copy_provenance` returns the five provenance keys only, so same-dataset derivatives (crop, upscale, LUT, detection crop) inherit no lineage — but the **replace** mode of those same operations mutates the row in place and therefore keeps it, which is why any re-extraction pass must skip or warn on a frame with a non-empty `processing_history`. See `docs/dev/video-extract.md` for the lineage columns themselves and `docs/dev/video-reextract.md` for that skip rule.
- **A stem-keyed derived artifact needs a collision guard at every site that *writes* it — including the ones that adopt a filename rather than pick one.** Thumbnails and posters are `.webp` keyed by stem, so two files differing only in extension share one derived path and the second write clobbers the first, leaving two rows pointing at one picture. Most creation paths pick a free name via `unique_filename_with_thumb` and are covered. The two rescan paths are not: they register files found on disk under the names the user gave them, and both shipped without the guard. They now resolve it in **opposite** directions, and the asymmetry is deliberate — `rescan_dataset` renames the *image file* (`a.jpg` → `a_001.jpg`) because eleven sites re-derive an image's thumbnail path from its filename, so a drifted thumbnail stem would be orphaned by the next rename/move/crop/restore; `_rescan_videos` renames the *poster* (`clip_001.webp`) and leaves the video file alone, because nothing re-derives a poster path — every consumer reads `Video.poster_path`. When reviewing any new path that writes a derived file, ask which of the two it is. Note also that a path registering a file already in place must pass `disk_exclude={f.name}`, or the uniquifier sees each file's own name occupied and renames everything.
  - A *pure extension change* (`a.jpg` → `a.png`, from a re-extraction or a PNG format fallback) is the one rename that disturbs nothing derived: the stem is unchanged, so the thumbnail and the `.txt` sidecar stay put, and no uniquifier call is needed — this very rule guarantees no other row owns the new name. The only hazard is an **unregistered** file already at the target path, which has no row to guard it and must be refused, not clobbered. See `docs/dev/video-reextract.md` § The extension change.
- **Never mutate a loaded JSON column in place.** For JSON columns like `Image.quality_flags`, copy before mutating: `flags = dict(img.quality_flags or {})`, edit `flags`, then reassign `img.quality_flags = flags`. SQLAlchemy's default change detection compares by equality, so mutating and reassigning the *same* dict object looks unchanged and the UPDATE is silently skipped. The correct pattern lives in `services/caption_service.py`.

## Documentation Map

Each file below covers one subsystem in depth. Read the relevant file(s) with the Read tool when
your task touches that subsystem — do not read all of them up front. Do NOT use `@`-paths to reference
these files anywhere — `@path` auto-loads the target into every conversation, defeating this split.

| File | Contents | Read this when... | Words |
|---|---|---|---|
| `docs/dev/ml-models.md` | Model manager (VRAM/unload), model ID registry, JoyCaption/Florence-2, upscaling, LUT grading, device abstraction, TorchDynamo, config validation | Working on captioning models, upscaling, LUT grading, or `backend/ml/` loading | ~1970 |
| `docs/dev/detection.md` | `/detection` router, `DetectionJobRequest` scope/model/task matrix, SAM2/SAM3/Florence-2/NudeNet inference, mask geometry and `mask_area`, watermark-flag sync, detection frontend surfaces | Working on object detection, masks, or `DetectionsPanel` | ~2890 |
| `docs/dev/scoring.md` | Quality scorers and the columns they write, flag thresholds, duplicate detection (pigeonhole index + brute force), style similarity and DINOv2 per-layer scoring | Working on `QualityPage`, quality flags, or thresholds | ~1555 |
| `docs/dev/gallery.md` | Gallery selection and shift-click ranges, subfolder sidebar, filters, manual drag ordering, drag-images-onto-subfolders (dnd-kit droppables, DragOverlay, collision detection) | Working on `GalleryPage` or gallery drag & drop | ~2925 |
| `docs/dev/image-detail.md` | Gallery/nav persisted keys, `injectNavId`/`paneGo`, crop tool, selection toggle, caption panel, AI generation metadata extraction and display | Working on `ImageDetailPage` or generation-metadata display | ~1550 |
| `docs/dev/image-files.md` | Image naming/renaming/collisions, cross-dataset move and copy, folder import, rescan/sync, standalone caption import, drag-`.txt`-onto-image | Working on image upload/move/copy/rename or caption import | ~1800 |
| `docs/dev/provenance.md` | `backend/licenses.py` vocabulary, dataset→image inheritance, ingest capture precedence, derived-image and cross-dataset materialization, ComfyUI synthetic stamping, `bulk-provenance`/`licenses-in-use`, the provenance components | Anything touching license, attribution, `source_meta`, or license filters | ~3170 |
| `docs/dev/captioning.md` | Caption post-processing (delimiter modes, refusal stripping, rename-on-caption), pipeline job execution, OpenAI-compatible provider config, `ModelPicker`, sidecar-write executor rules | Working on `CaptioningPage`, LLM provider integration, or a caption sidecar write | ~1760 |
| `docs/dev/export.md` | Export (kohya/ai-toolkit/plain): shared loop, stem uniquification, filters incl. license/commercial/no-derivatives, resize, metadata stripping, loss masks, disk preflight, CREDITS.md/licenses.csv | Working on `ExportPage` or `export_service.py` | ~3180 |
| `docs/dev/bulk-ops.md` | Bulk caption find/replace/regex, bulk image rename/delete/count/reorder, detection-driven cropping (`detection_crop_rect`) and the crop detection remap | Working on `BulkEditPage`, `CropToDetectionForm`, or a `bulk-*` endpoint | ~2480 |
| `docs/dev/tag-consolidation.md` | MiniLM tag embedder, analyze/apply background jobs, whole-tag (non-substring) rewrite, preview/confirm UI | Working on `TagConsolidatePage`, `tag_embedder`, or `dedupe_tags` | ~890 |
| `docs/dev/video.md` | `Video` model and `videos/` layout, `media_types.py` allowlist, poster stems and collisions, video ingest via upload/folder-import/rescan, the `/videos` endpoints incl. rename, range serving and `frames-summary`, the cv2 CI install and test skip convention | Working on videos, video ingest, the `/videos` endpoints, any file-extension allowlist, or a cv2-gated test | ~2930 |
| `docs/dev/video-ui.md` | `VideoStrip` and its selection, `VideoDetailPage`, `ExtractFramesModal` + `CropOverlay`/`TrimBar`, the extraction history panel, the `useVideoExtractJobs` re-attach hook, the frame-lineage line, and the gallery subfolder + "frames from video" deep links | Working on any video screen, the extraction modal, or the frame lineage filter | ~3465 |
| `docs/dev/video-decode.md` | cv2 probe ladder and its duration guard, `measure_duration_ms` seek search, `isOpened()` as ingest gate, container rotation, poster generation and the fallback ladder, lazy backfill and retry backoff | Working on video probe/metadata, video duration, or poster frames | ~1530 |
| `docs/dev/video-extract.md` | Pass 1 — triage frame extraction: probe sampling and its RSS rule, the PySceneDetect contract and cost cliff, shot rendering, `POST /videos/probe` and `/videos/extract`, the `video_extract` job | Working on frame extraction, shot detection, or the extract/probe endpoints | ~3070 |
| `docs/dev/video-reextract.md` | Pass 2 — full-res re-extraction: seek-by-timestamp, the `processing_history` skip rule, `_write_frame`/`render_at_timestamps`, the preview/enqueue resolver, the `video_reextract` job, the extension change (and the LUT gap it closed), `ReextractFramesForm`/`ReextractFramesModal` | Working on re-extraction, `POST /videos/reextract`, or a replace-mode extension change | ~2390 |
| `docs/dev/video-heuristics.md` | The pure-numpy `video_frames.py` judgement calls: cropdetect profiles, combing and telecine detection, sharpness, `pick_index` candidate rejection | Tuning cropdetect, interlace/telecine detection, or which frame a shot yields | ~875 |
| `docs/dev/versioning.md` | Snapshots, branches, copy-on-write object store (atomic `_store_object`), diff, restore, COW injection points, object-store prune/GC, the dataset-busy 409 guard | Working on `VersionsPage`, `dataset_busy`, or any path that overwrites image files in place | ~3005 |
| `docs/dev/datasets-page.md` | Preview strip, license badge, sort/density/grouping, category rail, persisted page UI, `ImportFolderModal`/`DirPickerModal`, folder naming, edit, duplicate | Working on `DatasetsPage`, categories, or the folder picker | ~2395 |
| `docs/dev/statistics.md` | Stats queries and live polling, server-side aggregation, `DatasetStats` schema, validator-keyed cache, editable histograms, CSV export, the `GET /images/` filter extensions, Detections and Licenses panels | Working on `StatsPage`, `BucketPanel`, or `get_dataset_stats` | ~2845 |
| `docs/dev/settings.md` | The `ThresholdSettings` singleton row and every tab (Gallery, Captioning, UI Behavior, Quality Thresholds, Versioning, LLM Providers, ComfyUI) | Working on `SettingsPage`, a new app-wide setting, or `threshold_service.py` | ~1060 |
| `docs/dev/workspace.md` | Sidebar hardware meters and `/system`, file browser and `/filesystem`, Logs page (job history + JS error console), Booru tag lookup | Working on hardware meters, `FileBrowserPage`, `LogsPage`, or `BooruPage` | ~1780 |
| `docs/dev/frontend-core.md` | TanStack Query/Zustand conventions, the `SelectionToolbar` action modals, `uploadStore`/`uiPrefsStore`, `errorConsoleStore`/`ErrorConsole`, shared `constants/` modules | Working on global frontend state, a shared constants module, or the JS error console | ~1970 |
| `docs/dev/frontend-jobs.md` | SSE hooks, `jobStore`, job labels, job-completion cache invalidation (single-job and id-list patterns), detection/per-image invalidation helpers | Adding a job-triggering UI or changing what a finished job invalidates | ~1065 |
| `docs/dev/panes-routing.md` | Sidebar layout, split-view pane manager, `usePaneNavigate`, route-level code splitting and the six-site routed-page checklist | Working on panes, adding a routed page, or lazy page loading | ~910 |
| `docs/dev/persistence.md` | `constants/storage.ts` key registry, `loadPersisted`/`useDebouncedPersist`, the three persistence shapes, the workflow/filters persistent page state pattern | Adding a storage key or persisting page configuration | ~1590 |
| `docs/dev/styling.md` | CSS variable tokens, `@layer components` classes, `CrucibleMark` and its export/drift checks, `ConfirmDialog`, `useModalBehavior` (modal keyboard/focus/ARIA), hist-bar CSS | Working on Tailwind/CSS, the brand mark, or any modal dialog | ~1610 |
| `docs/dev/backend-infrastructure.md` | Production frontend serving, shutdown/restart + restart loop, database (subfolders, indexes, deferred columns), migration drift check, startup DB backup, SSE progress broadcaster, job cancellation, stale-job cleanup, job retention sweep | Working on `main.py` lifecycle, Alembic migrations, SSE, or job cancellation | ~2670 |
| `docs/dev/environment-setup.md` | Venv ML packages, prereq auto-install, Python version discovery, PyTorch GPU auto-detection (NVIDIA/ROCm/MPS), SAM2/SAM3 install, update self-handoff, lockfile reset, startup splash + browser launch, encoding constraint | Working on `manage.ps1`/`manage.sh`, torch wheels, the startup splash, or the setup/update flow | ~2400 |
| `docs/dev/comfyui.md` | Plans (workflow template + pinned params, `output_is_synthetic`), prompt rows, prompt library, the `comfy_generate` job, ComfyClient/patch_workflow, `ComfyPage` | Working on `ComfyPage`, the `comfy` router, or ComfyUI integration | ~2820 |
| `docs/dev/comfy-prompts.md` | One-shot generate endpoint, the durable `comfy_prompts` job (per-batch commits, cancel/PM-004 discipline), `parse_prompts` filtering, `GeneratePromptsModal` re-attach | Generating prompts with an LLM or working on `prompt_generator.py` | ~1880 |
| `docs/dev/comfyui-sync.md` | "Sync from canvas", `GET /comfy/canvas-workflow`, the `ComfyUI-CrucibleBridge` extension (`extras/`), history-pull fallback, pin keep/drop, ComfyUI API constraints | Working on workflow sync or the bridge extension | ~710 |
| `docs/dev/postmortems.md` | Postmortem index: past incidents as one-line rows (symptom, root-cause category, LIVE/MITIGATED/STRUCTURAL status), linking detail files under `docs/dev/postmortems/` | Doing a code review or investigating a bug — check the code under review against known failure classes | ~605 |

### Code review & bug investigation

When reviewing code (any `/code-review` run or ad-hoc review request) or investigating a bug:

- Always read `docs/dev/postmortems.md` first. Pull a specific detail file from
  `docs/dev/postmortems/` only when the code under review touches that entry's area.
- Treat LIVE and MITIGATED entries as an active checklist for their code class;
  ignore STRUCTURAL entries (kept for history only).
- These are known failure modes to check against, but do not let them narrow the
  review. New code can fail in new ways — check for novel issues too, not only
  documented ones.

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
