# ComfyUI generation queue

Per-dataset prompt/parameter queues run against a remote ComfyUI server via its native HTTP
API, with outputs auto-imported into the dataset. Covers `ComfyPage`, the `comfy` router,
`comfy_service.py`, and the `comfy_generate` job.

### Concepts & data model

A **plan** (`comfy_plans`, `backend/models/comfy.py`) belongs to a dataset and stores:

- `workflow_json` — a ComfyUI **API-format** workflow (`{node_id: {class_type, inputs, _meta?}}`),
  exported in ComfyUI via *Workflow → Export (API)*. The UI-format editor export (`nodes`/`links`
  keys) is rejected client-side with a hint.
- `pinned_params` — JSON list of `{node_id, input, alias, is_prompt, per_row, value, int_mode}`.
  **Two-tier model**: a pin with `per_row=False` is a *run default* — its `value` (null =
  template) applies to every row, no queue column; `per_row=True` gives it a queue-table
  column whose blank cells fall back to the run default, then the template. At most one pin
  has `is_prompt=True` (enforced in `_validate_pins`, and forced `per_row=True`) — it drives
  bulk prompt paste, .txt import, caption text, and the progress-bar preview. `int_mode`
  (`null/fixed|random|increment`, integer params) applies when a row has no value:
  `random.randint(0, 2**48)` / `base + row_index_within_run` where base = run default or
  template. Replaces the old plan-level `seed_mode`.
- `output_node_ids` — JSON list of workflow node ids whose history outputs are imported
  regardless of image type (so PreviewImage nodes work as import sources; their temp files
  are downloaded before ComfyUI clears them, and nothing accumulates in ComfyUI's output
  folder). `[]` = auto: all `type=="output"` images (SaveImage nodes). In `PATCH /comfy/plans`
  `[]` clears back to auto (absent = unchanged).

A **row** (`comfy_rows`) holds `values` (`{alias: json-native value}`; absent/blank → template
value), `status` (`pending|running|completed|failed`), `error_msg`, `sort_order`, `prompt_id`
(last ComfyUI prompt id), and output links: `image_id` (FK → images, `SET NULL`, first output)
plus `image_ids` (JSON list — multi-SaveImage workflows produce several images per row).
Plan names are unique per dataset (`uq_comfy_plan_dataset_name`).

The ComfyUI server URL is the global `ThresholdSettings.comfyui_url` (Settings → ComfyUI tab,
with a Test-connection button → `GET /comfy/ping`, which hits `{url}/system_stats`). The same
tab also holds `ThresholdSettings.comfy_workflow_dir` — the default folder scanned for
workflow `.json` exports (with a Browse button via `DirPickerModal`); a path on the machine
running the backend.

### Backend (`backend/routers/comfy.py`, `backend/services/comfy_service.py`)

`comfy_service.py` is pure (no DB/HTTP routing): `ComfyClient` wraps the ComfyUI endpoints
(`POST /prompt`, `GET /history/{id}`, `GET /view`, `POST /interrupt` best-effort);
`patch_workflow(workflow, pinned, row_values, row_index)` deep-copies the template and
resolves each pin as: row value (coerced to the template value's JSON type) → `int_mode`
roll (random/increment) → run default `value` → template. Coercion is
backend-authoritative: the frontend's `coerceCellValue` deliberately keeps big or
non-round-tripping numerics (seeds > 2^53, `'1e5'`, `'1.0'`) as strings, and `_coerce` /
the increment base both accept them via `_lenient_int` (`int()` → `int(float())`
fallback; garbage still raises `ComfyRowError`); `effective_prompt` follows the
same row → default → template chain; `extract_output_images(entry, output_node_ids=None)` —
default/empty: all `type == "output"` images (skips `temp` previews; a workflow with only a
PreviewImage node and no output-node selection fails its rows with a "add a SaveImage node or
select an output node" message); with the plan's `output_node_ids`: those nodes' images
regardless of type, in list order; `check_history_error` reads `status.status_str == "error"`
**only when `status` is present** (older ComfyUI omits it — entry presence alone means
completion); `workflow_format(obj)` classifies parsed workflow JSON as
`"api" | "ui" | "invalid"` (server-side mirror of the client's `isApiFormat`).

Router endpoints: plan CRUD (`/comfy/plans…`), row CRUD (`…/rows`, `…/rows/bulk` one row per
non-empty line into the `is_prompt` alias, `…/rows/delete`, `…/rows/reorder`, `…/rows/reset`
failed→pending, `GET …/rows` — full rows; `GET /comfy/plans/{id}/prompts` returns
`{prompts: [{row_id, prompt, status}]}` — the **effective** prompt (row→default→template) of
each non-empty row, used to browse/reuse prompts across plans and datasets (the
`ImportPromptsModal`), `…/rows/set-value` set/clear one pinned alias on **every** row of the plan —
`{alias, value}`, `value: null|""` clears back to the default/template), and `POST /comfy/run`. Editing a
completed/failed row's `values` via `PATCH /comfy/rows/{id}` (or touching it via
`rows/set-value`) resets it to `pending` and clears `error_msg`/`prompt_id` (image links are
kept). `workflow_json` is returned only by `GET /comfy/plans/{id}` — never in the plan list
or row responses.

Prompt tooling — `POST /comfy/generate-prompts` (one LLM batch call),
`POST /comfy/plans/{id}/generate-prompts` (the durable `comfy_prompts` job), the output
parser and `GeneratePromptsModal`: `docs/dev/comfy-prompts.md`.

`POST /comfy/plans/{id}/rows/bulk-edit` (`{operation: prepend|append|remove|find_replace,
text, replacement, use_regex, row_ids?}`) mirrors caption bulk-edit semantics on the prompt
column: base = effective prompt (row → default → template), result written into row values
(changed completed/failed rows reset to pending), regex bounded like caption bulk-edit — one
30 s batch deadline enforced inside the `regex` engine via `regex_sub_deadline` (408 on timeout).

Prompt library: `comfy_library_prompts` (`ComfyLibraryPrompt`) is a **global** store of
prompt texts grouped by a free-text `category` (≤100 chars, indexed) — deliberately no
dataset/plan FK, so prompts are reusable everywhere without duplicating plans.
`GET /comfy/library` (all prompts, ordered category → created_at; client groups),
`POST /comfy/library` (`{category, prompts[]}` — blanks dropped, case/whitespace-insensitive
dedupe against the target category → `{created, skipped}`), `POST /comfy/library/move`
(`{ids, category}` re-categorize; upholds the same per-category uniqueness — a moved
prompt whose text already exists in the target is deleted instead → `{moved, merged}`),
`POST /comfy/library/delete` (`{ids}`).

Workflow sync: `GET /comfy/canvas-workflow` pulls the current workflow from ComfyUI (bridge
snapshot, else last-queued history entry) — `docs/dev/comfyui-sync.md`.
Workflow folder scan: `GET /comfy/workflow-files?dir=…` (dir falls back to
`comfy_workflow_dir`) recursively lists `.json` files (cap 500, >5 MB skipped as invalid) with
a `format` sniff via `workflow_format`; `GET /comfy/workflow-file?path=…` returns the parsed
workflow, 400 with an "Export (API)" hint for UI-format files. Both validate paths with
`utils.sanitize_abs_path` (shared with the `filesystem` router).

### The `comfy_generate` job

`POST /comfy/run` (`{plan_id, row_ids?, subfolder, set_caption, label?}`): explicit `row_ids`
run regardless of status; otherwise all `pending` rows in `sort_order`. A 409 guards against a
second pending/running run for the same plan. Standard job pattern (nested `_run(job_id)`
closure, own `AsyncSessionLocal`, auto label `ComfyUI: {plan} — N rows`).

Per row, sequentially (one at a time — ComfyUI's own queue is never stacked): mark `running` →
`patch_workflow` → submit → poll history every 1 s (30-min deadline, `PER_ROW_TIMEOUT_S`) →
download each output image → write into `{dataset}/images/` named
`{plan_slug}_001.ext` via `unique_filename_with_thumb` → `_register_file_sync` in an executor
(metadata + thumbnail; the PNG's embedded `prompt`/`workflow` chunks give `generation_metadata`
provenance for free) → optional caption (effective prompt = row override or template value,
assigned **via ORM attribute** + `_write_txt_sidecar`, `captioned_by="comfyui"`) → row
`completed`, commit per row. `refresh_stats` + `result_data`
(`{created_image_ids, failed_row_ids, completed, failed}`) at the end.

Imported rows get their provenance from `_comfy_output_provenance(ds, plan.output_is_synthetic)`.
`ComfyPlan.output_is_synthetic` (bool, `NOT NULL DEFAULT true`, migration `c3f7a9e1d4b6`) is the
plan's own declaration that its output is self-created — nothing about the workflow or the dataset
can answer that, and one dataset holds both kinds of plan. True stamps **concrete**
`license="synthetic"`, `source_name="ComfyUI"` and `attribution='Generated by ComfyUI plan "<name>"'`
— concrete because a blank attribution reads as "inherit", which credited a real photographer for an
AI image in `CREDITS.md`. False returns `{}` so all four fields stay NULL and inherit together (never
a partial stamp — that would mix a "synthetic" license onto an inherited real credit). `source_url`
is the one field left inheriting; the helper's docstring explains why and shows the manifest it
produces.

`source_meta` comes from `_comfy_source_meta(plan_row, row, wf)` — built from `wf`, the graph
actually submitted, not the plan template, and computed **once per row** next to `wf` (deep-copied
per image so multi-output rows never share one JSON dict). `_workflow_checkpoints` reads
`ckpt_name` / `unet_name` / `model_name` so Flux/SD3 `UNETLoader` graphs record a model too. See
`docs/dev/gallery-and-images.md` § Source & license provenance.

Failure/cancel semantics:

- Per-row failures (`ComfyRowError`, httpx errors, OSError) mark the row `failed` with a
  readable message (400 `node_errors` are flattened by `_format_node_errors`) and continue.
  **Per-row atomicity**: `row_files`/`row_images` track every file + Image the row produces
  — files are tracked *before* they are written (a corrupt download can fail inside
  `_write_and_register` after the file is on disk) — and the failure handler unlinks the
  files and deletes the Image rows, so a partly-imported row leaves no orphans. A hard
  `CancelledError` (server shutdown; normal cancel is cooperative and never reaches here
  with files written) runs the same cleanup best-effort and resets the row to `pending`
  before re-raising.
- **3 consecutive connect errors abort the run** with a job-level error; untouched rows stay
  `pending`.
- Cancel (`DELETE /jobs/{id}`) is cooperative: checked before each row and inside the poll
  loop, which best-effort `POST /interrupt`s ComfyUI and reverts the in-flight row to
  `pending` before raising. Note `DELETE /jobs` flips the job row to `cancelled` immediately,
  so a row can briefly read `running` until the worker's next check (~1 s).

### Frontend (`frontend/src/pages/ComfyPage.tsx`, `frontend/src/components/comfy/`)

Dataset-scoped page at `/datasets/:datasetId/comfy` (PageType `"comfy"`, registered in App
routes, `PageRenderer`, `PaneHeader` `PAGE_OPTIONS`+`NEEDS_DATASET`, Sidebar). Job type
`comfy_generate` is in TopBar's `IMAGE_MODIFYING_JOB_TYPES`. API module
`frontend/src/api/comfy.ts` (`comfyApi`).

- **Plan bar** — plan `<select>` (query `["comfy","plans",datasetId]`), inline create/rename,
  delete via `ConfirmDialog`; section tabs *Rows* / *Workflow & Pins*.
- **`WorkflowPinPanel`** — paste textarea + `.json` file loader + *Scan folder…* button
  (opens `WorkflowScanModal`) + *Sync from canvas* button (`canvas-workflow` →
  `SyncCanvasModal`, see `docs/dev/comfyui-sync.md`); validates API-format client-side; warns (non-blocking) when no
  node's `class_type` matches `/save/i` **and** no output node is selected, and when selected
  output nodes no longer exist in the workflow (runs then fail with a review-the-workflow row
  error — deliberate, no auto-heal). *Import images from*: toggle chips for Save/Preview-type
  nodes plus an "+ other node…" select → `output_node_ids` (none selected = Auto). Pinned
  parameters render **grouped by source node** with per-pin: alias input, run-default value
  editor (placeholder = template value), `int_mode` select on integer params, single ★ prompt
  radio (forces per-row), and a *per row* toggle; *Pin node (n)* in the node browser pins all
  remaining scalar inputs of a node as run defaults in one click. Node browser shows every
  node (connection-only nodes like PreviewImage get a "no editable inputs" note) with Pin
  toggles and a search box filtering by id/class_type/title/input name/value. Saves via
  `PATCH /comfy/plans/{id}`.
- **`WorkflowScanModal`** — lists `GET /comfy/workflow-files` for the settings folder (or a
  per-scan override via `DirPickerModal`), format badge per file. Load fetches
  `workflow-file` and feeds the JSON through the panel's normal paste/validate flow (still
  needs Save). The Load button is always visible but disabled for non-API files (tooltip
  explains why); when a scan finds no API-format file at all, an explanation line tells the
  user that normal ComfyUI saves are UI-format and to use *Workflow → Export (API)* —
  ComfyUI workflow folders typically contain only UI-format saves.
- **`ComfyDefaultsStrip`** — chips above the rows table for run-default pins (`per_row=False`):
  `#node · alias — value` with a click-to-edit modal that PATCHes the plan's `pinned_params`.
  Hidden when every pin is per-row.
- **`ComfyRowsTable`** — `@tanstack/react-virtual` grid (same mechanics as
  `TagConsolidatePage`); columns are the `per_row` pins only. Two-line headers: alias (★ marks
  the prompt column) + a source chip (`#6 KSampler · seed`) + ✎ bulk-edit (modal → 
  `rows/set-value`, set or clear-to-default on all rows). Cell placeholders show the effective
  fallback (run default / template / "🎲 random" / "auto +n"); `number` input when the template
  value is numeric; blank cell = use the fallback. The **prompt column renders a `PromptCell`
  textarea** (`resize: vertical` per-cell drag handle; Enter commits, Shift+Enter newline) instead
  of the single-line `EditableCell`; a ⤢/⤒ toggle in the prompt column header auto-sizes every
  prompt cell to its full text (height set to `scrollHeight` in a layout effect; collapsing resets
  inline heights, wiping manual drags — the virtualizer's `measureElement` ResizeObserver absorbs
  the variable row heights). Status badge with the error as tooltip,
  View button → image detail via `usePaneNavigate`. Cell edits PATCH on blur.
- **`ComfyRunBar`** — subfolder select (`["subfolders", datasetId]`), "Prompt as caption"
  toggle, *Run pending (n)* / *Run selected (n)* / *Run all (n)* (runs every row regardless of
  status — re-runs completed prompts and regenerates images; passes all row ids to `/comfy/run`,
  which runs explicit `row_ids` regardless of status). The page tracks runs in an
  `activeRuns: Map<jobId, planId>` (planId captured at **mutate** time, so switching the plan
  select mid-POST can't retag a run); one `JobSSESubscriber` component per entry keeps a
  dedicated `useJobSSE` subscription, and the run bar / `isRunning` gating applies only to the
  plan being viewed — other plans stay interactive and can start their own concurrent runs
  (the backend 409 is per-plan). While a run is live the page invalidates
  `["comfy","rows",planId]` (that run's plan, not the viewed one) on each progress event so
  row statuses update in place; on terminal status it also invalidates `["images", datasetId]`,
  `["subfolders", datasetId]`, `["comfy","plans",datasetId]`, and `["datasets"]`, fires the
  completion/failure toast, and drops the run from the map — every tracked run gets its
  invalidations and toast even if a different plan is selected when it finishes. **Live gallery refresh during a run is driven by TopBar**, not this page:
  `comfy_generate` is in TopBar's `LIVE_IMAGE_JOB_TYPES`, so TopBar re-invalidates
  `["images", datasetId]` (+ `["subfolders"]`) each time the job's `done` count advances — the
  gallery updates per-row as images import, regardless of which pane is showing it.
- **`PromptLibraryModal`** (Rows toolbar *Library…*, disabled until a pin is marked as
  prompt) — two tabs. **Library**: the global prompt library (`GET /comfy/library`), category
  sidebar (derived client-side, "All" + per-category counts), checkbox prompt list; *Add n to
  plan* → `rows/bulk`, *Move to…* (existing category or new) → `library/move`, *Delete* →
  `library/delete` behind `ConfirmDialog`. **Other plans**: reuse prompts across
  plans/datasets — pick any dataset → one of its plans (self excluded) → checkbox-list its
  prompts (`GET /comfy/plans/{id}/prompts`) → *Copy* (adds them to the current plan via
  `rows/bulk`) or *Move* (also `rows/delete`s them from the source). Both tabs carry only
  prompt **text** (lands in the current plan's prompt column; other params use this plan's
  defaults/template). Query key `["comfy","library"]`.
- **`SaveToLibraryModal`** (Rows toolbar *Save to library (n)*, needs a selection) — category
  input with a `<datalist>` of existing categories; saves the selected rows' **effective**
  prompts (via `GET /comfy/plans/{id}/prompts`, backend-authoritative) through
  `POST /comfy/library`; toast reports created/skipped-duplicates.
- Rows toolbar: *+ Add row*, *Paste prompts…* (modal, one prompt per line → `rows/bulk`;
  disabled until a pin is marked as prompt), *Import .txt* (browser file picker, no modal —
  **one file = one prompt**, inner newlines collapsed to spaces, straight to `rows/bulk`),
  *✨ Generate prompts…* (`GeneratePromptsModal` — `docs/dev/comfy-prompts.md`),
  *Edit prompts…* (`BulkEditRowsModal`: find/replace, prepend, append, remove + regex toggle,
  scope all/selected → `rows/bulk-edit`), *Library…*, *Save to library (n)*, *Reset failed
  (n)*, *Delete selected (n)* (`ConfirmDialog`).

### Gotchas

- `server_default` strings on these columns are plain (`"pending"`, `""`) — SQLAlchemy quotes
  them itself. Do **not** copy the `"'off'"` style from `threshold_settings.versioning_mode`;
  that embeds literal quotes into the DDL default.
- JSON-column reassignment invariant applies to `row.values`, `row.image_ids`,
  `plan.pinned_params`, and `plan.output_node_ids` (see CLAUDE.md Key invariants).
- `/interrupt` only affects the currently executing prompt — sufficient here because rows are
  submitted one at a time, so nothing of ours ever waits in ComfyUI's queue.
- A mock ComfyUI server for end-to-end testing (system_stats/prompt/history/view with
  PNG-embedded workflow chunks, FAIL400/FAILEXEC trigger prompts) exists in the session
  scratchpad pattern; recreate from this description if needed.
- Workflow sync (bridge extension, sync button, history-pull): `docs/dev/comfyui-sync.md`.
