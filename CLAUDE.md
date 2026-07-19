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
- `sanitize_abs_path(path: str) -> Path` — validates a client-supplied filesystem path (rejects null bytes and relative paths with HTTP 400). Use in every router that accepts an arbitrary path (`filesystem`, `comfy` workflow scan); never re-inline the check.
- `slugify_filename(name: str) -> str` — lowercases, removes non-word characters, collapses whitespace/underscores/hyphens to `_`, strips leading/trailing `_-`, truncates to 200 chars. Returns `"image"` if the result is empty.
- `unique_filename(directory: Path, stem: str, suffix: str, db_names: set, disk_exclude: set[str] | None = None) -> str` — returns a filename not on disk and not in `db_names`. Tries `{stem}{suffix}` first, then `{stem}_001{suffix}`, `_002`, … Both checks are required: `db_names` covers in-flight batch collisions within the same request; the filesystem check covers files that exist but have no DB record. `disk_exclude` names files that exist on disk but should be treated as absent (files being renamed away in the same batch — used by bulk-rename renumbering so the counter restarts from `001` instead of skipping past the source files).
- `unique_filename_with_thumb(images_dir, stem, suffix, db_names, occupied_thumb_stems, planned_thumb_stems) -> str` — like `unique_filename` but also avoids thumbnail-stem collisions. Thumbnails are always `.webp` keyed by image stem, so two images with different extensions but the same stem would share a thumbnail path. Call this instead of `unique_filename` in every code path that creates or renames an image file and associates a thumbnail with it. Mutates `db_names` (adds the chosen filename) and `planned_thumb_stems` (adds the chosen stem) so subsequent calls within the same batch stay consistent. Build `occupied_thumb_stems` from `thumb_dir.glob("*.webp")` once before the loop; do **not** exclude the stems of images being renamed/moved from this set — doing so re-introduces the within-batch clobber bug where one image's new thumbnail path matches another's current path.
- `rename_with_sidecar(old_path: Path, new_path: Path) -> None` — renames a file and its `.txt` sidecar (if present) in one call. Use this everywhere a file rename happens; never copy the two-step pattern inline.
- `copy_with_sidecar(old_path: Path, new_path: Path) -> None` — copies a file and its `.txt` sidecar (if present) using `shutil.copy2`. Use this in any copy path; mirrors `rename_with_sidecar` but leaves the source intact.
- `read_caption_sidecar(image_path: Path | str) -> str | None` — reads the `.txt` caption sidecar next to an image (the read-side counterpart of `caption_service._write_txt_sidecar`). Returns the stripped text of `{stem}.txt` if present and non-empty, else `None`. Use everywhere a sidecar caption is read (folder import, rescan/sync, standalone caption import); never inline the `.with_suffix(".txt")` logic. See `docs/dev/gallery-and-images.md` (§ Importing captions & folder rescan).
- `compile_user_regex(pattern: str)` / `regex_sub_deadline(compiled, repl, text, deadline: float)` / `REGEX_TIMEOUT_SECONDS` / `regex_error` — the only sanctioned way to run a **client-supplied** regex. `compile_user_regex` raises `regex_error` (map to HTTP 400); `regex_sub_deadline` substitutes under an absolute `time.monotonic()` deadline and raises `TimeoutError` (map to HTTP 408) so one budget covers a whole batch instead of N × timeout. See the Key invariant below for why stdlib `re` is unusable here. Used by the `comfy` rows bulk-edit and both `caption_service` regex paths.
- `chunked(seq, size=10_000) -> Iterator[Sequence]` — yields successive `size`-length slices of a sequence. The single source of truth for splitting id lists before an SQL `IN (...)` so bind-parameter count stays under SQLite's 999-variable limit; use it in every batched `IN` query (detection crop worker, `_fetch_bboxes_by_image`, `export_service._fetch_detections_by_image`). Never re-inline a `range(0, len(x), N)` slice loop.
- `ALLOWED_FLAG_KEYS: frozenset` — the canonical set of valid quality flag names (`is_blurry`, `is_noisy`, `is_uniform`, `has_watermark`, `is_duplicate`, `is_nsfw`, `has_ai_artifacts`). Import this wherever flag names must be validated or used in SQL filters; never redefine the set locally.
- `normalize_image_format(suffix: str, out_path: str) -> tuple[str, str]` — normalises a file suffix to a PIL format name (`JPG`→`JPEG`, unsupported→`PNG`). Returns `(fmt, out_path)` — `out_path` may be updated when the format falls back to PNG (extension changes). Use in any image-save path; do not inline the JPG/PNG fallback logic again.
- `image_save_kwargs(fmt: str) -> dict` — returns PIL `save()` kwargs for the given format (JPEG → `{quality: 95, subsampling: 0}`; others → `{}`). Use alongside `normalize_image_format`.
- `thumbnail_path_for(image_path: Path | str) -> str` — derives the `.webp` thumbnail path for an image sitting in a dataset `images/` folder (`parent.parent/thumbnails/{stem}.webp`). Use in any router that creates or regenerates thumbnails; never reconstruct the path manually.
- `subsume_tags(tags: list[str]) -> list[str]` — order-stable tag dedup: drops any tag that is a whole-word subsequence of a longer tag in the same list (`tail` when `long tail` present) and collapses case-insensitive exact duplicates. Whole-word matching means `car` does not subsume `scar`/`carpet`. Single source of truth for the captioning `dedupe_tags` post-processing flag and for the per-caption subsumption cleanup exposed via the `tag-consolidation` router's `subsume` endpoint (the Consolidate Tags page "Quick cleanup", the `SelectionToolbar` "Merge tags" action, and the `ImageDetailPage` per-image button) — never reimplement the rule. See `docs/dev/tag-consolidation.md`.
- `count_caption_tokens(text: str | None) -> int` / `_get_enc()` — GPT-2 BPE token count of a caption (empty/whitespace/None → 0), backed by an `@lru_cache` tiktoken encoder (`tiktoken` imported lazily). This is the single tokenizer entry point; never call `tiktoken.get_encoding("gpt2")` inline. **`Image.caption_token_count` is a persisted column kept in sync by a SQLAlchemy `set` listener on `Image.caption_text`** (in `backend/models/image.py`), which calls `count_caption_tokens` on every ORM assignment (including `Image(caption_text=...)` constructor kwargs). Consumers (Stats aggregation in `dataset_service.py`, the gallery token filter in `routers/images.py`) read the column and never tokenize in the request path. **Invariant: captions must always be written via ORM attribute assignment** — a raw `update(Image)` / SQL write to `caption_text` bypasses the listener and leaves `caption_token_count` stale. No such bulk-update write to `caption_text` exists today; keep it that way.

**Shared frontend components**: `SelectionToolbar`, `MoveToDatasetModal`, `ConfirmDialog`, `GenerationMetadata`, `DirPickerModal`, and `JobProgressBar` are reusable components referenced from multiple subsystem doc files below — don't be surprised when the same component name recurs across files. Each is documented where it's most central: `SelectionToolbar`'s modal/cache-invalidation conventions and `ConfirmDialog` in `docs/dev/frontend-core.md` (§ Frontend state and § Styling respectively); `MoveToDatasetModal` (§ Image file naming) and `GenerationMetadata` (§ AI generation metadata) in `docs/dev/gallery-and-images.md`; `DirPickerModal` (the in-app "Browse…" folder picker, used by folder/caption import and Export) in `docs/dev/dashboard-pages.md` (§ Datasets page); `JobProgressBar` in `docs/dev/versioning.md` (§ Dataset versioning, Frontend). Other files document only how that subsystem *uses* them.

### Key invariants

- **DB-before-filesystem for batch rename/move.** `bulk_rename` and `batch_move_dataset` commit DB changes *before* the filesystem renames so the DB is always the authoritative record of intended file locations. If a rename fails mid-batch, the DB reflects the final intended state; do not revert this ordering. `batch_copy_dataset` uses the opposite ordering (stage DB inserts, do copies, then commit) because an incomplete copy should leave nothing — see the cross-dataset copies note above.
- **Always `ImageOps.exif_transpose()` first.** Every Pillow operation in `image_service.py` calls this before anything else to correct orientation from EXIF data. The same rule holds for every ML inference path: open images through `backend/ml/image_utils.py::open_rgb` (`Image.open().convert("RGB")` + `exif_transpose`), never a bare `Image.open`, so all predictors work in one transposed frame and normalized coordinates denormalize consistently. `image_service._open_safe` is the equivalent gate for image-processing paths.
- **Close PIL Images after preprocessing.** In all ML inference paths (`aesthetic_scorer.py`, `dino_scorer.py`, all captioners) call `img.close()` immediately after the image has been passed to the model's preprocessor/processor — before the GPU inference runs. In `export_service.py::_write_image()` use a `try/finally` block. This frees the decoded pixel buffer (potentially several MB per image) during slow inference and prevents accumulation across large batches.
- **Absolute DB path.** `config.py` derives the database URL from `Path(__file__).parent.parent` so it resolves correctly regardless of the working directory when uvicorn is launched.
- **Path traversal guard.** `_safe_path()` in `routers/images.py` validates that resolved file paths stay within `settings.datasets_dir`.
- **Never run a client-supplied regex through stdlib `re`.** Use `compile_user_regex` + `regex_sub_deadline` from `backend/utils.py` (the `regex` package). `re`'s matching loop is C code that never releases the GIL and cannot be interrupted, so a catastrophic pattern freezes the entire process — and the obvious guard does not work: wrapping it in `run_in_executor` + `asyncio.wait_for` can never fire, because the event loop can't be scheduled to fire it, and Python cannot kill the thread. Verified: `(a|a)*$` against 30 `a`s takes `re` **105 s** at 100% GIL, versus a clean `TimeoutError` from `regex` with the loop still live. Hardcoded patterns and `re.escape`'d literals (`slugify_filename`, `subsume_tags`) are fine on `re` — they can't backtrack. A comment claiming a thread + `wait_for` bounds a regex is the bug, not the fix.
- **Never mutate a loaded JSON column in place.** For JSON columns like `Image.quality_flags`, copy before mutating: `flags = dict(img.quality_flags or {})`, edit `flags`, then reassign `img.quality_flags = flags`. SQLAlchemy's default change detection compares by equality, so mutating and reassigning the *same* dict object looks unchanged and the UPDATE is silently skipped. The correct pattern lives in `services/caption_service.py`.

## Documentation Map

Each file below covers one subsystem in depth. Read the relevant file(s) with the Read tool
when your task touches that subsystem — do not read all of them up front. Do NOT use `@`-paths
to reference these files anywhere — `@path` syntax causes Claude Code to auto-load the target
file into every conversation, defeating the purpose of this split. Use plain relative paths.

| File | Contents | Read this when... | Lines |
|---|---|---|---|
| `docs/dev/ml-models.md` | Model manager (VRAM/unload), model ID registry, JoyCaption/Florence-2 details, quality scorers, object detection, upscaling, LUT grading, device abstraction, TorchDynamo, config validation | Working on captioning models, quality scoring, object detection, upscaling, LUT grading, or `backend/ml/` | ~235 |
| `docs/dev/gallery-and-images.md` | Image naming/renaming/collisions, gallery selection/filters/subfolder sidebar, manual drag ordering, gallery navigation state (incl. ImageDetailPage crop/selection/caption panel), generation metadata | Working on `GalleryPage`, `ImageDetailPage`, image upload/move/copy/rename, or generation-metadata display | ~150 |
| `docs/dev/captioning.md` | Captioning post-processing (delimiter modes, refusal stripping, rename-on-caption), pipeline job execution, OpenAI-compatible provider config and ModelPicker | Working on `CaptioningPage`, the caption job pipeline, or LLM provider integration | ~55 |
| `docs/dev/export-and-bulk-ops.md` | Bulk caption find/replace/regex, bulk image rename/delete/count, detection-driven cropping (`/detection/crop`, `detection_crop_rect`), dataset export (kohya/ai-toolkit/plain, filters, resize, metadata stripping) | Working on `ExportPage`, `BulkEditPage`, `CropToDetectionForm`, or any `bulk-*` endpoint | ~109 |
| `docs/dev/tag-consolidation.md` | Dataset-wide semantic tag consolidation: MiniLM tag embedder, analyze/apply background jobs, whole-tag (non-substring) rewrite, `TagConsolidatePage` preview/confirm UI | Working on `TagConsolidatePage`, the `tag-consolidation` router, `tag_embedder`, or per-image `dedupe_tags` | ~100 |
| `docs/dev/versioning.md` | Dataset version control: snapshots, branches, copy-on-write object store, diff, restore, COW injection points | Working on `VersionsPage`, branch/snapshot logic, or any code path that overwrites/deletes image files in place | ~100 |
| `docs/dev/dashboard-pages.md` | Datasets page (categories, duplicate, import), Statistics page (histograms, CSV export, BucketPanel), Settings page (tabs, thresholds), hardware stats, file browser, Logs page (job history + JS error console), Booru tag lookup page | Working on `DatasetsPage`, `StatsPage`, `SettingsPage`, hardware meters, `FileBrowserPage`, `LogsPage`, or `BooruPage` | ~230 |
| `docs/dev/frontend-core.md` | TanStack Query/Zustand conventions, SSE hooks, job-completion cache invalidation, shared constants modules, Sidebar/Layout, split-view pane manager, Tailwind/CSS design system, `errorConsoleStore`, `ErrorConsole` overlay | Working on global frontend state, a new job-triggering UI, the pane/split-view system, styling, or the JS error console | ~120 |
| `docs/dev/backend-infrastructure.md` | Production frontend serving, server shutdown/restart + restart loop, database (subfolders, indexes, deferred columns), SSE progress broadcaster, venv/ML setup, prereq auto-install, GPU auto-detection, manage.ps1 encoding constraint | Working on `main.py` server lifecycle, `manage.ps1`/`manage.sh`, Alembic migrations, or SSE infrastructure | ~70 |
| `docs/dev/comfyui.md` | ComfyUI generation queue: plans (workflow template + pinned params), prompt rows, global prompt library (categories), `comfy_generate` job (submit/poll/import), ComfyClient/patch_workflow, `ComfyPage` UI, `comfyui_url` setting | Working on `ComfyPage`, the `comfy` router, `comfy_service.py`, ComfyUI integration, the prompt library, LLM prompt generation, or the `comfy_generate` job | ~250 |
| `docs/dev/comfyui-sync.md` | Workflow sync: "Sync from canvas" button, `GET /comfy/canvas-workflow`, `ComfyUI-CrucibleBridge` extension (`extras/`), history-pull fallback, pin keep/drop on sync, ComfyUI API constraints | Working on workflow sync, the sync button, the bridge extension, `canvas-workflow`, or pulling workflows from ComfyUI | ~85 |
| `docs/dev/postmortems.md` | Postmortem index: past incidents as one-line rows (symptom, root-cause category, LIVE/MITIGATED/STRUCTURAL status), linking detail files under `docs/dev/postmortems/` | Doing a code review or investigating a bug — check the code under review against known failure classes | ~15 |

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

This documentation is split across this file (always loaded) and topic files under
`docs/dev/` (loaded on demand via the Documentation Map above). Keep it that way as
you learn new things during a session:

- **Plain relative paths only.** Never write `@docs/...` anywhere in documentation.
  The `@path` syntax triggers automatic recursive loading into every conversation.
- **Narrow, subsystem-specific knowledge**: append it to the relevant `docs/dev/`
  file under the best-fitting heading. If this makes that file's "read this when"
  hint incomplete, update the hint (keep trigger keywords front-loaded).
- **Cross-cutting knowledge** (a new shared utility, a universal invariant, a
  pattern every module must follow): add it to Key invariants or Shared utilities
  in this file. Test: "would I want this loaded even for a task in an unrelated
  subsystem?" Utility entries stay one line here; detailed behavior goes in the
  utility's docstring.
- **New subsystem, or a topic file growing past ~250 lines**: split into a new
  `docs/dev/<topic>.md` and add a Documentation Map row (contents, keyword-front-
  loaded triggers, line count). Don't append new features to the least-bad
  existing file.
- **Cross-references between topic files**: when documenting something in file A
  that depends on something in file B, add a one-line pointer, e.g. "see
  `docs/dev/versioning.md` for the copy-on-write mechanism" — don't duplicate it.
- **Line counts in the Documentation Map** are approximate; refresh a row's count
  when you substantially edit its file.
- **Run `scripts/check_docs.py`** after any documentation change; fix what it
  reports.
- **Periodic rebalancing**: if a topic file becomes a dumping ground of unrelated
  facts, propose splitting it during that session rather than continuing to append.
- **Doc audits**: when asked for a "doc audit", diff each topic file against the
  code it describes and propose corrections for anything stale.
- **`docs/*.md` vs `docs/dev/*.md`**: `docs/*.md` (flat, no `dev/`) is end-user
  documentation referenced from `README.md` — different audience, do not confuse
  the two.
- **User-facing features need user-facing docs.** `docs/dev/` explains a subsystem
  to whoever maintains it; it never counts as documenting the feature. When a change
  adds or alters something a user can see — a page, a sidebar item, a settings tab,
  a setup step — update `README.md` and the relevant `docs/*.md` **in the same
  change**, not just `docs/dev/`. A whole subsystem (its own page + settings) earns
  its own `docs/<topic>.md` plus a README Docs-table row (see `docs/comfyui.md`);
  a smaller capability is a section in `docs/features.md`. README's Workflow chain,
  Prerequisites, and Docs table are part of the change when the feature affects
  them. `scripts/check_docs.py` link-checks these files but cannot tell that a
  feature is missing from them — that is on you.

### Proposing skills

Reference documentation stays in `docs/dev/` — never duplicate it into skills.
But when you notice **procedural** knowledge that meets ALL of these criteria,
propose creating a project skill in `.claude/skills/<name>/SKILL.md`:

1. It's a *workflow* (a sequence of steps/commands), not facts about the code.
2. It has recurred, or clearly will recur, across sessions (e.g. release process,
   migration workflow, scaffolding a new module of an established pattern,
   regenerating fixtures).
3. It benefits from automatic triggering and/or a bundled script whose code
   shouldn't occupy context (only script *output* costs tokens).

**Never create a skill without approval.** Propose it in this exact format and
wait for a yes/no:

> **Skill proposal:** `<name>` — <one sentence: what workflow it captures>.
> **Trigger description:** "<the frontmatter description, keyword-front-loaded>"
> **Bundles:** <scripts/templates, or "none">
> **Why a skill and not docs:** <one sentence>

If approved: keep SKILL.md focused on the workflow steps, put reusable code in
bundled scripts rather than inline instructions, and keep the description short
and keyword-rich (descriptions of all skills are always loaded and may be
truncated when many skills exist — every word must earn its place). If rejected,
don't re-propose the same skill unless circumstances change.

Be conservative: a handful of high-value skills beats many marginal ones, since
every skill's description permanently occupies context and dilutes trigger
matching for the others.
