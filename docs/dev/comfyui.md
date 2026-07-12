# ComfyUI generation queue

Per-dataset prompt/parameter queues run against a remote ComfyUI server via its native HTTP
API, with outputs auto-imported into the dataset. Covers `ComfyPage`, the `comfy` router,
`comfy_service.py`, and the `comfy_generate` job.

### Concepts & data model

A **plan** (`comfy_plans`, `backend/models/comfy.py`) belongs to a dataset and stores:

- `workflow_json` — a ComfyUI **API-format** workflow (`{node_id: {class_type, inputs, _meta?}}`),
  exported in ComfyUI via *Workflow → Export (API)*. The UI-format editor export (`nodes`/`links`
  keys) is rejected client-side with a hint.
- `pinned_params` — JSON list of `{node_id, input, alias, is_prompt}`. Pinned inputs become
  per-row columns; at most one pin has `is_prompt=True` (enforced in `_validate_pins`), which
  drives bulk prompt paste, caption text, and the progress-bar preview.
- `seed_mode` (`fixed|random|increment`) — applies to a pin aliased `seed` (case-insensitive)
  when a row leaves it blank: keep template value / `random.randint(0, 2**48)` /
  `template + row_index_within_run`.

A **row** (`comfy_rows`) holds `values` (`{alias: json-native value}`; absent/blank → template
value), `status` (`pending|running|completed|failed`), `error_msg`, `sort_order`, `prompt_id`
(last ComfyUI prompt id), and output links: `image_id` (FK → images, `SET NULL`, first output)
plus `image_ids` (JSON list — multi-SaveImage workflows produce several images per row).
Plan names are unique per dataset (`uq_comfy_plan_dataset_name`).

The ComfyUI server URL is the global `ThresholdSettings.comfyui_url` (Settings → ComfyUI tab,
with a Test-connection button → `GET /comfy/ping`, which hits `{url}/system_stats`).

### Backend (`backend/routers/comfy.py`, `backend/services/comfy_service.py`)

`comfy_service.py` is pure (no DB/HTTP routing): `ComfyClient` wraps the ComfyUI endpoints
(`POST /prompt`, `GET /history/{id}`, `GET /view`, `POST /interrupt` best-effort);
`patch_workflow` deep-copies the template and applies row values with type coercion to the
template value's JSON type; `extract_output_images` filters history outputs to
`type == "output"` (skips `temp` previews); `check_history_error` reads
`status.status_str == "error"` **only when `status` is present** (older ComfyUI omits it —
entry presence alone means completion).

Router endpoints: plan CRUD (`/comfy/plans…`), row CRUD (`…/rows`, `…/rows/bulk` one row per
non-empty line into the `is_prompt` alias, `…/rows/delete`, `…/rows/reorder`, `…/rows/reset`
failed→pending), and `POST /comfy/run`. Editing a completed/failed row's `values` via
`PATCH /comfy/rows/{id}` resets it to `pending` and clears `error_msg`/`prompt_id` (image links
are kept). `workflow_json` is returned only by `GET /comfy/plans/{id}` — never in the plan list
or row responses.

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
- **`WorkflowPinPanel`** — paste textarea + `.json` file loader; validates API-format
  client-side; node list with scalar inputs (array inputs are node connections and are hidden)
  and Pin toggles; pinned list with alias input + single `is_prompt` radio; seed-mode select.
  Saves via `PATCH /comfy/plans/{id}`.
- **`ComfyRowsTable`** — `@tanstack/react-virtual` grid (same mechanics as
  `TagConsolidatePage`); one editable cell per pinned alias (placeholder = template value,
  `number` input when the template value is numeric; blank cell = use template), status badge
  with the error as tooltip, View button → image detail via `usePaneNavigate`. Cell edits PATCH
  on blur.
- **`ComfyRunBar`** — subfolder select (`["subfolders", datasetId]`), "Prompt as caption"
  toggle, *Run pending (n)* / *Run selected (n)* → `setActiveJobId` + `useJobSSE`; progress bar
  from `jobStore`. While a run is live the page invalidates `["comfy","rows",planId]` on each
  progress event so row statuses update in place; on terminal status it also invalidates
  `["images", datasetId]`, `["subfolders", datasetId]`, `["comfy","plans",datasetId]`, and
  `["datasets"]`.
- Rows toolbar: *+ Add row*, *Paste prompts…* (modal, one prompt per line → `rows/bulk`;
  disabled until a pin is marked as prompt), *Reset failed (n)*, *Delete selected (n)*
  (`ConfirmDialog`).

### Gotchas

- `server_default` strings on these columns are plain (`"pending"`, `"fixed"`, `""`) —
  SQLAlchemy quotes them itself. Do **not** copy the `"'off'"` style from
  `threshold_settings.versioning_mode`; that embeds literal quotes into the DDL default.
- JSON-column reassignment invariant applies to `row.values`, `row.image_ids`, and
  `plan.pinned_params` (see CLAUDE.md Key invariants).
- `/interrupt` only affects the currently executing prompt — sufficient here because rows are
  submitted one at a time, so nothing of ours ever waits in ComfyUI's queue.
- A mock ComfyUI server for end-to-end testing (system_stats/prompt/history/view with
  PNG-embedded workflow chunks, FAIL400/FAILEXEC trigger prompts) exists in the session
  scratchpad pattern; recreate from this description if needed.
