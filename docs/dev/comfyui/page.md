# ComfyUI page (ComfyPage)

The frontend of the ComfyUI queue: how `ComfyPage` is routed and registered, what its e2e spec
covers, the header line, `WorkflowPinPanel` and the workflow scan, the run-default strip, the rows
table and its Folder column, the run bar and how the page tracks concurrent runs, the prompt
library modals, the Rows toolbar, and the Rows tab's plan rail, folder tree and grouped table
(M6 §8b). The Blueprint tab is `docs/dev/comfyui/blueprint-tab.md`. The data model and endpoints it calls are in
`docs/dev/comfyui/queue.md`; what a run does server-side is `docs/dev/comfyui/run.md`; *Generate
prompts* is `docs/dev/comfyui/prompts.md` and *Sync from canvas* `docs/dev/comfyui/sync.md`.

### Frontend (`frontend/src/pages/ComfyPage.tsx`, `frontend/src/components/comfy/`)

Dataset-scoped page at `/datasets/:datasetId/comfy` (PageType `"comfy"`, registered in App
routes, `PageRenderer`, `PaneHeader` `PAGE_OPTIONS`+`NEEDS_DATASET`, Sidebar). Job type
`comfy_generate` is in TopBar's `IMAGE_MODIFYING_JOB_TYPES` and `LIVE_IMAGE_JOB_TYPES`; it is
the one job type whose live branch also invalidates `["dataset", id]`, because it is the one
whose worker refreshes the stored counters per row (see `docs/dev/job-invalidation.md`). API module
`frontend/src/api/comfy.ts` (`comfyApi`).

`frontend/e2e/comfy.spec.ts` covers the journeys that need no ComfyUI server: the empty
state → create a plan (which selects it and jumps to *Workflow & Pins*), *Paste prompts…*
→ rows, and per-row folders (typed into a cell, then bulk-set over the top, cross-checked
through `GET …/rows`). §8b added four, all through buttons and menus rather than drags: a
plan duplicated from the rail, a folder made and renamed from the tree and then picked to
filter the table, the plan and tab coming back after a reload, and Set folder asking before
it moves a blueprint row. Its textarea-count assertion is scoped to
`data-testid="comfy-rows-table"` on purpose — the prompt cells must be the only textareas in
the table, so it fails if the single-line Folder cell is ever rebuilt as one. It never starts
a run. The journeys that press *Run* are `frontend/e2e/comfy-run.spec.ts`, against the fake
ComfyUI in `docs/dev/comfyui/run.md` § Testing the run: rows filed under their folders with the
image's generation metadata on its detail page, and a failed row's error tooltip and
*Reset failed*.

- **The header line** shows the plan's name (`comfy-plan-name`) and the section tabs *Rows* /
  *Workflow & Pins* / *Blueprint*, in the sticky header with `ComfyRunBar` (§ The layout).
  Plans are picked, made, renamed, duplicated and deleted in the Rows tab's rail
  (§ The plan rail, the folder tree and the grouped table). Until §8b this was a plan
  `<select>` the e2e found as the page's first; nothing depends on select order now.
- **`BlueprintPanel`** (the *Blueprint* tab) is its own file:
  `docs/dev/comfyui/blueprint-tab.md`.
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
  row identity rather than a parameter, so it stays put as pins come and go. Its `FolderCell` is modelled on `EditableCell` (commit on blur,
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
  - **A row the blueprint still counts is locked** (`blueprint_linked`): a lock icon and a
    read-only input, keeping `placeholder="(root)"` so the e2e's cell count holds. Moving it
    unlinks it from its leaf, and only Set folder asks about that.
- **`SetRowFolderModal`** (Rows toolbar *Set folder (n)*, needs a selection, disabled while
  that plan is running) — a text input with a `<datalist>` of the dataset's existing folders
  (query key `["subfolders", datasetId]`, usually already cached by `ComfyRunBar`); a datalist
  rather than the run bar's closed select, because creating folders that do not exist yet is
  the point. Calls `setRowsSubfolder`, invalidates `["comfy","rows",planId]`, toasts the count.
  - It takes the selected **rows**, not ids, to know which are `blueprint_linked`. When the
    typed folder would move any of them (compared on the server's slash-folded spelling, so a
    no-op never asks), Apply shows the three choices of the plan's ruling in place of its
    buttons (`comfy-linked-confirm`): *Move and unlink* (`linked: "unlink"`, which also
    invalidates the blueprint preview, since the leaf reads short), *Move hand-made rows
    only* (`"skip"`) and *Cancel*. Otherwise it sends `"refuse"`, the server's default, which
    409s if the client was wrong about a row.
- **`ComfyRunBar`** — a strip in the sticky header, shown on every tab once a plan exists
  (before §8a it rendered on *Rows* only). *Run selected* shows on *Rows* only
  (`showRunSelected`): elsewhere the selection it would run is out of sight. Base-folder select (`["subfolders", datasetId]`; labelled *Base folder*,
  since per-row folders nest under it, and folders a run declares appear in the next run's
  options automatically), "Prompt as caption"
  toggle, *Run pending (n)* / *Run selected (n)* / *Run all (n)* (runs every row regardless of
  status — re-runs completed prompts and regenerates images; passes all row ids to `/comfy/run`,
  which runs explicit `row_ids` regardless of status).
  - The page tracks runs in an
  `activeRuns: Map<jobId, planId>` (planId captured at **mutate** time, so switching the plan
  select mid-POST can't retag a run); one `JobSSESubscriber` component per entry keeps a
  dedicated `useJobSSE` subscription, and the run bar / `isRunning` gating applies only to the
  plan being viewed — other plans stay interactive and can start their own concurrent runs
  (the backend 409 is per-plan).
  - While a run is live the page invalidates
  `["comfy","rows",planId]` (that run's plan, not the viewed one) and `["comfy","plans",datasetId]`
  on each progress event, so row statuses and the rail's dot update in place; on terminal status it also invalidates `["images", datasetId]`,
  `["subfolders", datasetId]`, `["comfy","plans",datasetId]`, and `["datasets"]`, fires the
  completion/failure toast, and drops the run from the map — every tracked run gets its
  invalidations and toast even if a different plan is selected when it finishes.
  - **Live gallery refresh during a run is driven by TopBar**, not this page:
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

### The plan rail, the folder tree and the grouped table

M6 §8b (`docs/dev/comfy-automation-plan/README.md` § 8b, which records the rulings). The rail
is the Rows tab's left column, and it also shows when there are no plans, so the empty state
can make one. The approved mockup put it on the Rows tab only, so switching plans from
another tab means going back to Rows.

- **`ComfyPlanRail`** lists `GET /comfy/plans` with each plan's `row_count` and a dot. The dot
  is *running* when `status_counts.running` is set or the plan is in `activeRuns` (which
  knows a run started from this page before its first row moves), else *failed* when
  `status_counts.failed` is set. Its ⋯ button opens `ContextMenu` at the button: *Rename…*
  (inline input, Enter or blur saves), *Duplicate…* and *Delete…*. *New plan* sits under the
  list.
  - Rename and delete take the plan's id from the menu, not `effectivePlanId`: a menu can
    name a plan other than the one shown. Deleting the shown plan falls back to the first.
  - **`DuplicatePlanModal`** (on `ModalShell`) proposes a free name ("X (copy)", then
    "(copy 2)" and on) and an *Include rows* checkbox, on by default. The copy is selected
    on success. What the server copies is `docs/dev/comfyui/queue.md` § Backend.
- **`ComfyFolderTree`** is built with the gallery's `buildSubfolderTree` from
  `planFolderItems(rows, plan.declared_folders)`: every row folder, every declared folder,
  and the ancestors neither lists (`withMissingAncestors`). Counts are rows, each folder's
  `totalCount` including its subtree. *All rows* and *(root)* head the list.
  - **Clicking a folder filters the table to its subtree** (`inFolder`); *(root)* shows the
    rows with no folder. The filter lives in `ComfyPage` with a text filter (prompt text and
    folder, case-blind) and status chips above the table. None is persisted, a plan switch
    resets all three, and **any filter change clears the selection**, since Set folder and
    Delete would act on rows the view hides.
  - **The menu** (⋯ or right-click): *New folder inside…*, *Rename…*, *Move to…* and
    *Delete…*; on *(root)* only *New folder…*. The header's + makes one inside the selected
    folder. `FolderNameModal` takes one segment (a slash is refused; Move nests), and
    *Move to…* reuses the gallery's `MoveSubfolderModal`, fed the plan's folders.
  - **Locked folders.** A folder whose subtree holds a `blueprint_linked` row
    (`lockedFolderTest`) shows a lock, and its Rename, Move and Delete are disabled with the
    reason as the tooltip. The server refuses them anyway (409). While the plan runs every
    folder write is disabled the same way.
  - `ContextMenu` gained `disabled` and `title` for this: `aria-disabled` rather than
    `disabled`, since a disabled button shows no tooltip (`docs/dev/styling.md`).
  - A rename or move re-points the stored collapsed paths (`repath`) and the folder filter;
    a delete clears a filter inside it. Every folder write invalidates rows, the plan, the
    plan list, prompts and the blueprint preview.
- **`ComfyRowsTable` groups the rows it is given by folder**, in tree order (`compareFolders`:
  *(root)* first, then segment by segment). The virtualizer runs over a flat item list of
  group headers and rows, keyed `g:<path>` and by row id, so a header measures like a row.
  - A header (`comfy-group`, `data-folder`) has a collapse chevron, the folder, its pending,
    running, failed and done counts, and a *from blueprint* pill. It holds **no checkbox,
    textarea or `(root)`-placeholder input**, which the e2e counts as row cells. Select-all
    in the column header covers the rows on show, leaving out folded groups, and folding a
    group drops its rows from the selection (`toggleGroup`), for the same reason a filter
    change clears it.
  - The ✎ "set on all rows" dialog counts the plan's rows, not the shown ones, and says it
    ignores the filter: `rows/set-value` writes every row.
  - A plan whose rows all sit in *(root)* gets no header: one group adds nothing.
  - A completed row's View button shows a 24px thumbnail, with `aria-label` "View" (or
    "View (n)") so `comfy-run.spec.ts` still finds it by name.
  - "No rows yet" shows only for a plan with no rows; a filter that hides everything says
    "No rows match".
- **Persistence** (`docs/dev/persistence.md`). The last plan and tab are
  `COMFY_PAGE_STATE_PREFIX` per dataset. The saved plan id is trusted only until the plan list
  first loads (`isPending`); after that a list without it falls back to the first. Trusting
  it on every refetch flipped a pane back to a plan deleted in another pane each time the
  list refreshed. A plan created or duplicated here goes into the cached list at once
  (`addPlanToList`), so selecting it never waits on a refetch. The save waits until the state was
  loaded for the current dataset (`stateDataset`): in the render where a pane switches
  datasets, `planId` is still the old one's. The tree's and the table's collapsed folders
  are `COMFY_ROWS_VIEW_KEY` per plan, through `usePlanRowsView` (`components/comfy/rowsView.ts`),
  stored as *closed* paths so a new folder is open; deleting a plan clears it.

### The layout

M6 §8a (`docs/dev/comfy-automation-plan/README.md` § 8a). The CSS is the `.comfy-head` and
`.bp-*` rules in `frontend/src/index.css`.

- **`ComfyPage` is its own bounded scroller** (`height: 100%; minHeight: 0; overflowY: auto`),
  in both shells. Outside pane mode `main` used to scroll instead, with no fixed height for a
  sticky side column to fit. `DatasetsPage` is the precedent.
  - **No top padding on the scroller.** Sticky `top: 0` counts from inside its padding, so
    the header stopped 24px down with content showing above it. `.page-h` carries the space.
  - `scrollPaddingTop` is the header's height, so `scrollIntoView` (the jump bar's, and
    Playwright's before a click) lands targets below the header, not under it.
- **The sticky header** (`.comfy-head`, `data-testid="comfy-head"`) holds the plan's name, the
  tabs and `ComfyRunBar` on every tab. `z-index` 20, under `.dialog-bg` (60) and the `z-50` overlays.
- **`hooks/useStickyOffsets.ts`** writes `--comfy-head-h` (the header wraps on a narrow pane,
  so its height varies) and `--comfy-view-h` (the scroller's `clientHeight`) onto the
  scroller from a ResizeObserver, never through state. `100vh` would be wrong: a pane is
  shorter than the window.
- **The Blueprint tab's two columns**, its jump bar and its collapsible sections are in
  `docs/dev/comfyui/blueprint-tab.md` § The layout.
- **The Rows tab's rail** (`.comfy-rows-wrap` / `.comfy-rows-grid`, M6 §8b): a 240px
  `<aside class="comfy-rail">` beside the table, sticky and capped at the visible height
  like the Blueprint side column, with its own scroll. A container query at 760px stacks it
  above the table and drops the sticky, as the mockup's wrapping rail did.
