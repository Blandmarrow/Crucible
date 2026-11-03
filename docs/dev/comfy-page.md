# ComfyUI page (ComfyPage)

The frontend of the ComfyUI queue: how `ComfyPage` is routed and registered, what its e2e spec
covers, the plan bar, `WorkflowPinPanel` and the workflow scan, the run-default strip, the rows
table and its Folder column, the run bar and how the page tracks concurrent runs, the prompt
library modals and the Rows toolbar. The data model and endpoints it calls are in
`docs/dev/comfyui.md`; what a run does server-side is `docs/dev/comfy-run.md`; *Generate
prompts* is `docs/dev/comfy-prompts.md` and *Sync from canvas* `docs/dev/comfyui-sync.md`.

### Frontend (`frontend/src/pages/ComfyPage.tsx`, `frontend/src/components/comfy/`)

Dataset-scoped page at `/datasets/:datasetId/comfy` (PageType `"comfy"`, registered in App
routes, `PageRenderer`, `PaneHeader` `PAGE_OPTIONS`+`NEEDS_DATASET`, Sidebar). Job type
`comfy_generate` is in TopBar's `IMAGE_MODIFYING_JOB_TYPES` and `LIVE_IMAGE_JOB_TYPES`; it is
the one job type whose live branch also invalidates `["dataset", id]`, because it is the one
whose worker refreshes the stored counters per row (see `docs/dev/frontend-jobs.md`). API module
`frontend/src/api/comfy.ts` (`comfyApi`).

`frontend/e2e/comfy.spec.ts` covers the three journeys that need no ComfyUI server: the empty
state → create a plan (which selects it and jumps to *Workflow & Pins*), *Paste prompts…*
→ rows, and per-row folders (typed into a cell, then bulk-set over the top, cross-checked
through `GET …/rows`). Its textarea-count assertion is scoped to
`data-testid="comfy-rows-table"` on purpose — the prompt cells must be the only textareas in
the table, so it fails if the single-line Folder cell is ever rebuilt as one. It never starts
a run. The journeys that press *Run* are `frontend/e2e/comfy-run.spec.ts`, against the fake
ComfyUI in `docs/dev/comfy-run.md` § Testing the run: rows filed under their folders with the
image's generation metadata on its detail page, and a failed row's error tooltip and
*Reset failed*.

- **Plan bar** — plan `<select>` (query `["comfy","plans",datasetId]`), inline create/rename,
  delete via `ConfirmDialog`; section tabs *Rows* / *Workflow & Pins*.
- **`WorkflowPinPanel`** — paste textarea + `.json` file loader + *Scan folder…* button
  (opens `WorkflowScanModal`) + *Sync from canvas* button (`canvas-workflow` →
  `SyncCanvasModal`, see `docs/dev/comfyui-sync.md`); validates API-format client-side; warns (non-blocking) when no
  node's `class_type` matches `/save/i` **and** no output node is selected, and when selected
  output nodes no longer exist in the workflow (runs then fail with a review-the-workflow row
  error — deliberate, no auto-heal). *Import images from*: toggle chips for Save/Preview-type
  nodes plus an "+ other node…" select → `output_node_ids` (none selected = Auto). An **Output is
  synthetic (self-created)** checkbox (`plan.output_is_synthetic`) sits below those chips, and rides
  in **both** the Save patch and the *Sync from canvas* patch so a sync never resets it. Pinned
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
- **The Folder column** (`FolderCell`, in the same file) sits between the checkbox and the
  pinned columns — leftmost because it is
  row identity rather than a parameter, so it stays put as pins come and go (and it is where a
  future group-by-leaf lands). Its `FolderCell` is modelled on `EditableCell` (commit on blur,
  Enter blurs, `placeholder="(root)"`) but is an `<input>`, never a `<textarea>` — see the e2e
  note above. It does **not** route through `commitCell`, which builds a `values` dict: the
  shared `updateMutation` takes a generic patch object, so `commitCell` passes `{ values }` and
  the folder cell passes `{ subfolder }`. A folder must never enter `values`. It commits the
  **trimmed** draft, as the modal does — the server folds slashes but keeps spaces, so
  `knight ` would be a second folder. **Every Folder cell is locked while the plan runs**, not
  only the busy row's, unlike the parameter cells: the run files each row from the rows it
  loaded at start (they reload only after a failed row), so a mid-run edit to a later row
  would show a folder its image never went to, and a finished row keeps it. The
  `columns.length === 0` early return still hides the whole table for a plan with no per-row
  pins, folder column included — accepted, since such a plan cannot run anyway.
- **`SetRowFolderModal`** (Rows toolbar *Set folder (n)*, needs a selection, disabled while
  that plan is running) — a text input with a `<datalist>` of the dataset's existing folders
  (query key `["subfolders", datasetId]`, usually already cached by `ComfyRunBar`); a datalist
  rather than the run bar's closed select, because creating folders that do not exist yet is
  the point. Calls `setRowsSubfolder`, invalidates `["comfy","rows",planId]`, toasts the count.
- **`ComfyRunBar`** — base-folder select (`["subfolders", datasetId]`; labelled *Base folder*,
  since per-row folders nest under it, and folders a run declares appear in the next run's
  options automatically), "Prompt as caption"
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
  scope all/selected → `rows/bulk-edit`), *Library…*, *Save to library (n)*,
  *Set folder (n)*, *Reset failed (n)*, *Delete selected (n)* (`ConfirmDialog`).
