# ComfyUI page (ComfyPage)

The frontend of the ComfyUI queue: how `ComfyPage` is routed and registered, what its e2e spec
covers, the header line, `WorkflowPinPanel` and the workflow scan, the run-default strip, the rows
table and its Folder column, the run bar and how the page tracks concurrent runs, the
Rows toolbar and selection bar (M6 §8d), and the Rows tab's plan rail, folder tree and grouped table (M6 §8b). The library
panel, Copy to plan, the row menu and drag and drop (M6 §8c) are `docs/dev/comfyui/library-dnd.md`. The Blueprint tab is `docs/dev/comfyui/blueprint-tab.md`. The data model and endpoints it calls are in
`docs/dev/comfyui/queue.md`; what a run does server-side is `docs/dev/comfyui/run.md`; *Generate
prompts* is `docs/dev/comfyui/prompts.md` and *Sync from canvas* `docs/dev/comfyui/sync.md`.

### Frontend (`frontend/src/pages/ComfyPage.tsx`, `frontend/src/components/comfy/`)

Dataset-scoped page at `/datasets/:datasetId/comfy` (PageType `"comfy"`, registered in App
routes, `PageRenderer`, `PaneHeader` `PAGE_OPTIONS`+`NEEDS_DATASET`, Sidebar). Job type
`comfy_generate` is in TopBar's `IMAGE_MODIFYING_JOB_TYPES` and `LIVE_IMAGE_JOB_TYPES`; it is
the one job type whose live branch also invalidates `["dataset", id]`, because it is the one
whose worker refreshes the stored counters per row (see `docs/dev/job-invalidation.md`). API module
`frontend/src/api/comfy.ts` (`comfyApi`).

`frontend/e2e/comfy.spec.ts` covers the journeys that need no ComfyUI server, and never starts
a run:
- The first ones: the empty state → create a plan (which selects it and jumps to *Workflow &
  Pins*), *Paste prompts…* → rows, and per-row folders (typed into a cell, then bulk-set over
  the top, cross-checked through `GET …/rows`). Its textarea-count assertion is scoped to
  `data-testid="comfy-rows-table"` on purpose — the prompt cells must be the only textareas in
  the table, so it fails if the single-line Folder cell is ever rebuilt as one.
- §8b added four, all through buttons and menus rather than drags: a plan duplicated from the
  rail, a folder made and renamed from the tree and then picked to filter the table, the plan
  and tab coming back after a reload, and Set folder asking before it moves a blueprint row.
- §8c added Copy to plan (folders kept, Move takes them out), the row menu's move within a
  folder and the library panel staying open across a reload; the rest of §8c's journeys are
  `docs/dev/comfyui/library-dnd.md` § Testing.
- §8d added the *Add ▾* menu's disabled reasons and Escape, the paste dialog as a real dialog,
  the selection bar's fixed slot, and Import .txt and Edit prompts from the menu.

The journeys that press *Run* are `frontend/e2e/comfy-run.spec.ts`, against the fake ComfyUI in
`docs/dev/comfyui/run.md` § Testing the run: rows filed under their folders with the image's
generation metadata on its detail page, a failed row's error tooltip and *Reset failed*, and
*Run selected* in the selection bar running only the ticked rows. The 2026-11-05 review added
*Reset failed* clearing the rail's failed dot, and a live run (eight rows, about a second each
against the fake) holding the rail's and the bar's *Delete* off, with a direct
`DELETE /comfy/plans/{id}` answering 409, until it ends. A paused Run refusing a delete stays a
hand check: the fake has no pause.

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
  would show a folder its image never went to, and a finished row keeps it. Since M6 §8c the server
  refuses the change too (`docs/dev/comfyui/queue.md` § Backend), since this lock knows only
  the runs this tab started. The
  `columns.length === 0` early return still hides the whole table for a plan with no per-row
  pins, folder column included — accepted, since such a plan cannot run anyway.
  - **A row the blueprint still counts is locked** (`blueprint_linked`): a lock icon and a
    read-only input, keeping `placeholder="(root)"` so the e2e's cell count holds. Moving it
    unlinks it from its leaf, and only Set folder asks about that.
- **`SetRowFolderModal`** (the selection bar's *Set folder…*, disabled while
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
  (before §8a it rendered on *Rows* only). *Run selected* left it in §8d for the Rows tab's
  selection bar, beside the rows it runs (before that it showed on *Rows* only, through a
  `showRunSelected` prop). Base-folder select (`["subfolders", datasetId]`; labelled *Base folder*,
  since per-row folders nest under it, and folders a run declares appear in the next run's
  options automatically), "Prompt as caption"
  toggle, *Run pending (n)* / *Run all (n)* (runs every row regardless of
  status — re-runs completed prompts and regenerates images; passes all row ids to `/comfy/run`,
  which runs explicit `row_ids` regardless of status).
  - The page tracks runs in an
  `activeRuns: Map<jobId, planId>` (planId captured at **mutate** time, so switching the plan
  select mid-POST can't retag a run); one `JobSSESubscriber` component per entry keeps a
  dedicated `useJobSSE` subscription, and the run bar / `isRunning` gating applies only to the
  plan being viewed — other plans stay interactive and can start their own concurrent runs
  (the backend 409 is per-plan).
  - **`planBusy` is wider than `isRunning`.** `busyPlanIds` adds every plan with a
    non-terminal `comfy_generate`, `comfy_prompts` or `comfy_rewrite` entry in the job store's
    `activeJobs` (`BUSY_JOB_TYPES`), so a run started in another tab, or a prompt or rewrite
    job, counts too. That needs `plan_id` on the job's events: the prompt and rewrite jobs
    always sent it, and the generate job's progress events carry it since the review. It disables the selection bar's *Delete*, the tree's folder writes
    (*New folder…* and its header + included) and the rail's *Delete…*. A paused automated
    Run has no job, so the page reads the dataset's Runs instead (below), and the server's 409
    stays the backstop (`docs/dev/comfyui/queue.md` § Backend has the guards).
  - **A paused or running automated Run is read from `["runs", datasetId]`** (the Runs page's
    key, so its invalidations apply; `runsApi.list`), refetched on a 30 s poll and whenever one of
    the dataset's jobs changes status (a statuses-only store key, as `RunsPage` builds). From it
    the page derives `liveRunPlanIds` (running or paused), `pausedPlanIds` and the running
    ones. `liveRunPlanIds` joins `busyPlanIds`; running Runs join `runningPlanIds` (blue dot
    between child jobs). The rail's `planDot` order is running (blue), paused (amber,
    `var(--warn)`, "An automated run is paused"), failed (red). The controls the server refuses with
    `_refuse_live_run` (`routers/comfy.py`) are greyed out with `LIVE_RUN_REASON` (`api/runs.ts`,
    the server's text) as the tooltip: the rail's *Delete…*, Add ▾'s *Add row*, *Paste prompts…* and
    *Import .txt…*, the selection bar's *Delete*, the library panel's *Add n to plan* (and *Copy
    here* / *Move here* on Other plans), `CopyRowsToPlanModal`'s button (target live, or the source
    on a move) and Blueprint *Apply* / *Top up* (`BlueprintPreview`'s `liveRun`). Folder writes
    ride on `planBusy`. Not covered: *Generate prompts…*'s add, a drop of rows onto a locked plan in the rail, and a
    move out of a plan in another dataset; the 409 toast handles those.
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
- **The prompt library** is a side panel since M6 §8c, toggled by the Rows toolbar's
  *Library* (`ComfyLibraryPanel`), with *Other plans* as its second tab: see
  `docs/dev/comfyui/library-dnd.md`. `PromptLibraryModal` is gone. Query key `["comfy","library"]`.
- **`SaveToLibraryModal`** (the selection bar's *Save to library…*) — category
  input (a `/` path, ≤512) with a `<datalist>` of every category path and its ancestors; saves the selected rows' **effective**
  prompts (via `GET /comfy/plans/{id}/prompts`, backend-authoritative) through
  `POST /comfy/library`; toast reports created/skipped-duplicates.
- **The Rows toolbar** (M6 §8d) is one line: the text filter, the status chips and the
  "in <folder>" note, then *Reset failed (n)* (only while rows have failed), **Add ▾** and
  *Library* (the panel toggle, `aria-pressed`). It sits outside `ComfyRowsTable` because the
  e2e counts the table's inputs as row cells.
  - **Add ▾** is a `MenuButton` (`docs/dev/shared-utilities.md`), test id `comfy-add-menu`,
    with *Add row*, *Paste prompts…* (one prompt per line → `rows/bulk`), *Import .txt…*
    (browser file picker, no modal — **one file = one prompt**, inner newlines collapsed to
    spaces, straight to `rows/bulk`, filed in the folder in view since §8c), *Generate
    prompts…* (`GeneratePromptsModal` — `docs/dev/comfyui/prompts.md`) and *Edit prompts…*
    (`BulkEditRowsModal`: find/replace, prepend, append, remove + regex toggle, scope
    all/selected → `rows/bulk-edit`). All but *Add row* need a prompt pin. A disabled entry is
    `aria-disabled` with its reason in `title`, so the reason shows as a tooltip, which a
    `disabled` button would not. *Import .txt…* clicks the hidden file input synchronously
    inside the entry's click: the picker needs that user activation.
  - **The paste dialog** is inline in `ComfyPage` on `ModalShell` (`role="dialog"`, Escape, a
    focus trap; before §8d a bare overlay). `pasteText` lives in the page, so closing keeps
    the draft, and only a paste's own add clears it (`bulkAddMutation`'s `source`). Focus
    comes back to *Add*, because `MenuButton` refocuses its trigger before the dialog mounts.
- **`ComfyRowsSelectionBar`** (M6 §8d, `comfy-selection-bar`) is between the toolbar and the
  table: *n selected*, *Set folder…*, *Copy to plan…*, *Save to library…*, *Run selected*,
  *Delete* (`ConfirmDialog`) and *Clear*. **Its slot is always there**, with a hint when
  nothing is ticked, at one fixed height that scrolls sideways rather than wrap
  (`.comfy-selbar`). That was the user's ruling on 2026-11-05: the mockup showed the bar only
  with a selection, which moves the table under the pointer on the first tick. e2e pins it by
  comparing the table's `boundingBox().y` before and after. Each button opens the dialog the
  old toolbar button did; the mockup's drop-downs were rejected because they would lose Set
  folder's blueprint ask, Copy's Move and a new library category. *Set folder…* and *Run
  selected* are disabled while the plan runs, *Delete* while it is `planBusy` (above), and *Save to library…* without a prompt pin —
  through `aria-disabled` with the reason in `title`, because `.btn:disabled` sets
  `pointer-events: none` and would hide it. `scrollbar-width: thin` keeps Firefox's
  scrollbar from eating the 24px buttons' height in a narrow column.

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
    resets all three, as do a pane's dataset switch and a fallback to the first plan when the
    shown one vanished (`shownPlanId`, adjusted during render). **Any filter change clears the
    selection**, since Set folder and Delete would act on rows the view hides.
  - **The selection is pruned to the rows on show, whatever hid them**: a filter, a fold, a
    *Set folder…*, an inline folder edit, *Reset failed* under the Failed chip, a drop into a
    folded group. `visibleKey` (the shown rows' ids minus folded groups, except when every
    shown row is in *(root)*, which the table draws flat with no header to fold) is compared with its
    previous value during render, and the selection is cut to it then, not in an effect: the
    repo's `react-hooks/set-state-in-effect` rule, and no frame with stale ids. It only ever
    shrinks the set, so it cannot loop. Every consumer (*Delete*, *Run selected*, *Copy to
    plan*, *Save to library*, *Edit prompts*, a drag, the bar's count) reads the pruned set.
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
    group drops its rows from the selection through the prune above.
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
