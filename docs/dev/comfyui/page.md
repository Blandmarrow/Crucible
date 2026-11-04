# ComfyUI page (ComfyPage)

The frontend of the ComfyUI queue: how `ComfyPage` is routed and registered, what its e2e spec
covers, the plan bar, `WorkflowPinPanel` and the workflow scan, the run-default strip, the rows
table and its Folder column, the run bar and how the page tracks concurrent runs, the prompt
library modals, the Rows toolbar and the Blueprint tab (`BlueprintPanel`, the draft dialog,
Preview, Top up and the rewrite). The data model and endpoints it calls are in
`docs/dev/comfyui/queue.md`; what a run does server-side is `docs/dev/comfyui/run.md`; *Generate
prompts* is `docs/dev/comfyui/prompts.md` and *Sync from canvas* `docs/dev/comfyui/sync.md`.

### Frontend (`frontend/src/pages/ComfyPage.tsx`, `frontend/src/components/comfy/`)

Dataset-scoped page at `/datasets/:datasetId/comfy` (PageType `"comfy"`, registered in App
routes, `PageRenderer`, `PaneHeader` `PAGE_OPTIONS`+`NEEDS_DATASET`, Sidebar). Job type
`comfy_generate` is in TopBar's `IMAGE_MODIFYING_JOB_TYPES` and `LIVE_IMAGE_JOB_TYPES`; it is
the one job type whose live branch also invalidates `["dataset", id]`, because it is the one
whose worker refreshes the stored counters per row (see `docs/dev/job-invalidation.md`). API module
`frontend/src/api/comfy.ts` (`comfyApi`).

`frontend/e2e/comfy.spec.ts` covers the three journeys that need no ComfyUI server: the empty
state → create a plan (which selects it and jumps to *Workflow & Pins*), *Paste prompts…*
→ rows, and per-row folders (typed into a cell, then bulk-set over the top, cross-checked
through `GET …/rows`). Its textarea-count assertion is scoped to
`data-testid="comfy-rows-table"` on purpose — the prompt cells must be the only textareas in
the table, so it fails if the single-line Folder cell is ever rebuilt as one. It never starts
a run. The journeys that press *Run* are `frontend/e2e/comfy-run.spec.ts`, against the fake
ComfyUI in `docs/dev/comfyui/run.md` § Testing the run: rows filed under their folders with the
image's generation metadata on its detail page, and a failed row's error tooltip and
*Reset failed*.

- **Plan bar** — plan `<select>` (query `["comfy","plans",datasetId]`), inline create/rename,
  delete via `ConfirmDialog`; section tabs *Rows* / *Workflow & Pins* / *Blueprint*.
- **`BlueprintPanel`** (the *Blueprint* tab, keyed by `plan.id`) edits the plan's blueprint.
  What the server does with it is `docs/dev/comfyui/structured.md`.
  - **Saving.** It is server-stored form state on the M5 pattern: `ServerStateGate` on
    `["comfy","blueprint",planId]`, autosaved through `useDebouncedPersist` +
    `writeToServer(blueprintPath, "PUT")` (`docs/dev/app-stored-data.md`).
  - **The seed.** A plan with no blueprint gets a random seed, saved only with the first
    edit. *New sampling seed* rerolls it.
    - That changes which combinations a `sample:K` scenario picks, and so which folders
      exist (the preview shows the orphans).
    - It also changes every leaf's template walk, so rows added later may repeat prompts
      that existing rows already used.
  - **The editors.** `BlueprintAxesEditor`, `BlueprintVariationsEditor` and
    `BlueprintScenariosEditor` hold the three parts. `BlueprintNameField` renames an axis or
    variation on blur, through `blueprintKeys.renameRefs`. That also rewrites every
    scenario's `combine`, path and template references (`{name}`, `{name.label}`); renaming
    only the key would turn each reference into an error. Entity ids are generated
    client-side and never shown. The numeric
    fields clamp to the schema's bounds (take ≤ 4, count ≤ 10,000), because past them both
    the save and the preview 422. The text inputs carry a `maxLength` for the same reason,
    from `BLUEPRINT_LIMITS` in `blueprintKeys.ts`, which mirrors `schemas/comfy_blueprint.py`
    (name 40, scenario name 100, path 400, template 4000, label 200, descriptor 2000, piece
    300). The same const holds the list sizes (8 axes, 200 entities an axis, 12 variations,
    100 pieces a variation, 50 scenarios, 6 combined axes): each *+ Add* button disables at
    its cap. The pieces box cannot be stopped by `maxLength`, so it saves the first 100
    lines, each cut to 300 characters, and shows a warning for what it left out.
  - **Read-only while the plan runs.** A disabled `<fieldset>`, since Apply would 409 anyway.
  - **The outline draft.** *✨ Draft from a description…* opens `OutlineModal`, which queues
    the `comfy_outline` job (`docs/dev/comfyui/structured-llm.md` § The outline call).
    - Like `GeneratePromptsModal`, it derives its job from `jobStore` and adds no
      `useJobSSE`. Its provider and model default to the plan's saved "Generate prompts"
      settings, read only.
    - **On start it seeds `jobStore` with the job's `plan_id`.** The queue's pending event
      carries none, so a job queued behind a long run was invisible to the plan scan
      until `_run` began. A reopened modal then offered *Draft* again, only to get a 409.
    - TopBar's `comfy_outline` branch toasts the outcome once per job (`outlineTerminalRef`)
      and invalidates the draft.
    - **The draft has its own query, `["comfy","blueprint-draft",planId]`.** The gate
      latches its first value and never refetches into the form, so it cannot show a draft
      that lands while the tab is open.
    - **While a draft exists, the modal is its review** (`DraftReview`), not the brief
      form, so a draft that lands with the modal open is shown there. The banner's
      *Review…* opens the same view, and *Draft again…* goes back to the brief, where
      *Back to the draft* returns to the review. The review's *Decide later* just closes
      the modal and keeps the draft. The review's
      expansion is the ordinary preview endpoint with the draft in the body, on its own key
      (`["comfy","blueprint-draft-preview",…]`); that endpoint writes nothing, so the saved
      spec stays as it was until *Use it*. A failure of the job this modal started shows
      inline as well as in the toast.
    - **A new *Draft* blanks the cached draft first**, with `setQueryData`. The old one
      would otherwise show in the review between the job's end and the refetch TopBar
      triggers. A failed job leaves the old draft saved, and that refetch brings it back.
  - **The draft's *Use it* (banner or review) clears the draft only after the spec holding
    it is saved.** In order:
    1. Await `serverWritesSettled`, so no older autosave can land on top.
    2. Save the draft with an awaited `comfyApi.putBlueprint`.
    3. Set the form to the draft.
    4. `DELETE …/draft`.
    - Not `writeToServer`: it drops a 4xx write, and `serverWritesSettled` then resolves,
      which would clear a draft whose save never landed.
    - A non-empty spec asks before it is replaced: the banner with a `ConfirmDialog`, the
      review with its button relabelled *Replace my blueprint* and a warning line, since a
      dialog over the modal would stack two. *Discard* just deletes the draft.
    - `applyDraft` resolves true on success, and the review closes on it.
  - **`BlueprintPreview`** is a `useQuery` on
    `["comfy","blueprint-preview",planId,debouncedJSON,allowOverCap]` (350 ms via
    `hooks/useDebouncedValue.ts`, `keepPreviousData`).
    - **Apply sends the debounced spec the shown preview came from**, never the live form,
      with `confirm_total` = that preview's `rows_to_add`.
    - It is disabled while the form lags the preview, while fetching or on placeholder data,
      on errors, over the cap without the override, and with no prompt pin.
    - A 409 toasts the server's reason and refetches. Success invalidates rows, plans,
      prompts and the preview.
    - **It also refetches on window focus and every 20 s.** Kept and short change outside
      the tab: an image deleted in the gallery makes its folder short. Without a refetch the
      panel sat on "0 short" with Top up disabled. A finished run in `activeRuns`
      invalidates it too.
    - **A background refetch is not "stale".** The buttons wait only for a spec still
      debouncing or placeholder data for a new key, never for `isFetching`. Counting it
      disabled both buttons every 20 s and dropped clicks; `confirm_total` already 409s if
      the rows moved meanwhile.
  - **Top up** sits beside Apply, under the same rules. It is also disabled when nothing
    is short, to add, pending or failed ("Every folder is full").
    - It sends Apply's body plus the run bar's **Base folder** and **Prompt as caption**,
      which live in `ComfyPage` state and are shown read-only beside the button.
    - **The returned job joins `activeRuns`** through `onRunStarted(jobId, planId)`, the
      same as `runMutation`. Tracking, invalidation, the completion toast and the
      `isRunning` gate then apply unchanged.
    - `job_id: null` toasts "Every folder is full".
    - **`awaiting_rewrite > 0` is not a run.** The reply's rows were applied but nothing ran,
      so it skips `onRunStarted` and shows the `blueprint-awaiting-rewrite` notice, which the
      next Apply or Top up clears.
  - **The rewrite** (`docs/dev/comfyui/structured-llm.md` § The rewrite pass).
    - Each scenario has a *Rewrite into prose (LLM)* checkbox (`BlueprintScenario.rewrite`).
      The TS field is what keeps the autosave from dropping it.
    - The totals line adds "N to rewrite · ≤ B LLM calls" from the preview's `rewrite_*`
      figures.
    - **The *Rewrite N prompts* button counts rows that exist now**, from
      `GET …/blueprint/rewrite` at the saved batch size, under
      `["comfy","blueprint-rewrite",planId,batch]`. It does not use the preview's count,
      which includes rows Apply would add. The button appears in the Preview header only
      while the preview says some rows need rewriting (`rewrite_rows > 0`). It is
      **disabled, with an "Apply first" tooltip,** while no such row exists yet.
    - `RewriteModal` follows `OutlineModal`: defaults from the saved settings, read only;
      its job derived from `jobStore`, seeded with `plan_id` on start. It sends the shown
      estimate's `budget` as `confirm_budget`, and a 409 refetches the estimate.
    - **TopBar's `comfy_rewrite` branch** refreshes the rows as each batch lands
      (`rewriteDoneRef`), as the `comfy_prompts` branch does, and the estimate and the
      preview with them. Both count the rows still waiting, so refreshed only at the end
      they sat on the starting count for the whole run (2026-11-04).
      - It toasts once per job (`rewriteTerminalRef`), reading `result_data` through
        `jobsApi.get`, since neither the terminal event nor a reconciled one carries it. It
        then invalidates rows, prompts, the preview and the estimate.
      - **A completed run that stopped early, or left prompts behind, gets a warning
        toast**, not a green one. It names the reason and how many prompts still wait,
        since Top up will refuse while they do.
      - The job is in neither image job-type set: it changes rows, never images.
  - **The e2e.** `comfy-run.spec.ts` authors a blueprint by hand, applies it, checks the rows'
    folders and `leaf_id`s, and runs them. Another ticks the rewrite, checks the
    totals line and Top up's notice, and opens `RewriteModal`, all without an LLM. Two more
    cover the draft: the Blueprint tab opens the draft-from-a-description dialog, and a
    waiting draft can be reviewed, with its expansion, before *Use it*.
- **`WorkflowPinPanel`** — paste textarea + `.json` file loader + *Scan folder…* button
  (opens `WorkflowScanModal`) + *Sync from canvas* button (`canvas-workflow` →
  `SyncCanvasModal`, see `docs/dev/comfyui/sync.md`); validates API-format client-side; warns (non-blocking) when no
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
  *✨ Generate prompts…* (`GeneratePromptsModal` — `docs/dev/comfyui/prompts.md`),
  *Edit prompts…* (`BulkEditRowsModal`: find/replace, prepend, append, remove + regex toggle,
  scope all/selected → `rows/bulk-edit`), *Library…*, *Save to library (n)*,
  *Set folder (n)*, *Reset failed (n)*, *Delete selected (n)* (`ConfirmDialog`).
