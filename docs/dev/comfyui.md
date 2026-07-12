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
roll (random/increment) → run default `value` → template; `effective_prompt` follows the
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

Prompt tooling: `POST /comfy/generate-prompts` (`{provider_id, model_name?,
system_instructions?, instruction, batch_size≤10, existing, temperature}`) makes **one** LLM
batch call via
`ml/prompt_generator.py` (text-only `AsyncOpenAI` sibling of the captioner; not
model-manager-tracked) and returns `{prompts}` — `existing` is the anti-similarity context
(the model is told those already exist and to diverge). Structure is enforced decoder-level
via `response_format: json_schema` **with a mandatory plain-call fallback whenever that
attempt errors OR parses to zero prompts** — LM Studio + Qwen3 (thinking) returns *empty
content* under a schema constraint, so error-only fallback is not enough. `max_tokens` is
floored at 8192 (the provider's captioning-tuned value truncates thinking models
mid-reasoning; a zero-prompt `finish_reason=="length"` raises a "raise max tokens" hint).
`parse_prompts` (unit of truth for splitting) strips closed AND unclosed `<think>` blocks,
then prefers JSON (bare array or `{"prompts": []}`), falling back to line splitting with
list-marker/commentary stripping. The client loops batches for "generate until N".
`POST /comfy/plans/{id}/rows/bulk-edit` (`{operation: prepend|append|remove|find_replace,
text, replacement, use_regex, row_ids?}`) mirrors caption bulk-edit semantics on the prompt
column: base = effective prompt (row → default → template), result written into row values
(changed completed/failed rows reset to pending), regex bounded by the same 30 s executor
timeout (408).

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

Failure/cancel semantics:

- Per-row failures (`ComfyRowError`, httpx errors, OSError) mark the row `failed` with a
  readable message (400 `node_errors` are flattened by `_format_node_errors`) and continue.
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
  (opens `WorkflowScanModal`); validates API-format client-side; warns (non-blocking) when no
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
  per-scan override via `DirPickerModal`), format badge per file, Load (API-format only) →
  fetches `workflow-file` and feeds the JSON through the panel's normal paste/validate flow
  (still needs Save).
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
  which runs explicit `row_ids` regardless of status) → `setActiveJobId` + `useJobSSE`; progress
  bar from `jobStore`. While a run is live the page invalidates `["comfy","rows",planId]` on each
  progress event so row statuses update in place; on terminal status it also invalidates
  `["images", datasetId]`, `["subfolders", datasetId]`, `["comfy","plans",datasetId]`, and
  `["datasets"]`. **Live gallery refresh during a run is driven by TopBar**, not this page:
  `comfy_generate` is in TopBar's `LIVE_IMAGE_JOB_TYPES`, so TopBar re-invalidates
  `["images", datasetId]` (+ `["subfolders"]`) each time the job's `done` count advances — the
  gallery updates per-row as images import, regardless of which pane is showing it.
- **`ImportPromptsModal`** (Rows toolbar *Import prompts…*, disabled until a pin is marked as
  prompt) — reuse prompts across plans/datasets: pick any dataset → one of its plans (self
  excluded) → checkbox-list its prompts (`GET /comfy/plans/{id}/prompts`) → *Copy* (adds them to
  the current plan via `rows/bulk`) or *Move* (also `rows/delete`s them from the source). Only
  prompt **text** carries over (lands in the current plan's prompt column; other params use this
  plan's defaults/template).
- Rows toolbar: *+ Add row*, *Paste prompts…* (modal, one prompt per line → `rows/bulk`;
  disabled until a pin is marked as prompt; a *Load .txt files…* button appends browser-picked
  files to the textarea, **one file = one prompt**, inner newlines collapsed to spaces),
  *✨ Generate prompts…* (`GeneratePromptsModal`: provider select + `ModelPicker`; two text
  fields — standing *Instructions* (HOW prompts are written → `system_instructions`, persisted
  per plan in localStorage via `loadPersisted`, along with provider/model/batch/temperature)
  and the per-call *Request* (WHAT to generate); *Generate N more* = one batch call appending
  to a review textarea, *Generate
  until N* loops batches client-side with a Stop button, max 12 calls; every call sends queue
  prompts + current textarea lines as diverge-from context; *Add N rows* → `rows/bulk`),
  *Edit prompts…* (`BulkEditRowsModal`: find/replace, prepend, append, remove + regex toggle,
  scope all/selected → `rows/bulk-edit`), *Reset failed (n)*, *Delete selected (n)*
  (`ConfirmDialog`).

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
- Planned workflow-sync feature (bridge extension, sync button, history-pull fallback):
  see `docs/dev/comfyui-sync-roadmap.md`.
