# ComfyUI page (ComfyPage)

The frontend of the ComfyUI queue: how `ComfyPage` is routed and registered, what its e2e spec
covers, the header line, `WorkflowPinPanel` and the workflow scan, the run-default strip, the run
bar and how the page tracks concurrent runs, and the layout. The Rows tab's table, Folder column,
toolbar, selection bar, plan rail and folder tree (M6 §8b, §8d) are `docs/dev/comfyui/rows-tab.md`;
the library panel, Copy to plan, the row menu and drag and drop (M6 §8c) are
`docs/dev/comfyui/library-dnd.md`. The Blueprint tab is `docs/dev/comfyui/blueprint-tab.md`. The
data model is `docs/dev/comfyui/queue.md` and the endpoints that move rows and folders are
`docs/dev/comfyui/rows-api.md`; what a run does server-side is `docs/dev/comfyui/run.md`; *Generate
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
  (`docs/dev/comfyui/rows-tab.md` § The plan rail, the folder tree and the grouped table). Until §8b this was a plan
  `<select>` the e2e found as the page's first; nothing depends on select order now.
- **`BlueprintPanel`** (the *Blueprint* tab) is its own file:
  `docs/dev/comfyui/blueprint-tab.md`.
- **`WorkflowPinPanel`** — paste textarea + `.json` file loader + *Scan folder…* button
  (opens `WorkflowScanModal`) + *Sync from canvas* button (`canvas-workflow` →
  `SyncCanvasModal`, see `docs/dev/comfyui/sync.md`) + *Copy from plan…* button
  (`CopyFromPlanModal`, below); validates API-format client-side; warns (non-blocking) when no
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
- **`CopyFromPlanModal`** — one dialog, two `kind`s: `"workflow"` here and `"blueprint"` on
  the Blueprint tab (`docs/dev/comfyui/blueprint-tab.md` § The panel). A dataset select
  (starts on the page's) and a plan select (the current plan left out), the same pair as the
  library's *Other plans* tab. It reads the source through the ordinary `GET /comfy/plans/{id}`
  or `GET …/blueprint`; there is **no copy endpoint**, because each tab already has a write
  path to stage into. The workflow kind feeds the JSON through `handleTextChange`, exactly as
  *Scan folder…* does, so it still needs Save; with *Also copy its pinned parameters and
  output settings* (default on) it also replaces the panel's pins, `output_node_ids` and
  `output_is_synthetic`, since pins name the source's node ids. Unticked, the plan's own pins
  stay and any that do not resolve show "not in workflow" — unlike Sync, nothing is dropped.
  The blueprint read uses its own key, `["comfy", "blueprint-source", id]`, never the tab's
  `["comfy", "blueprint", id]`: that gate latches its first value, so a read from here would
  become what a later visit to that plan opens on.
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
- **`ComfyRunBar`** — a strip in the sticky header, shown on every tab once a plan exists
  (before §8a it rendered on *Rows* only). *Run selected* left it in §8d for the Rows tab's
  selection bar (`docs/dev/comfyui/rows-tab.md`), beside the rows it runs (before that it showed on *Rows* only, through a
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
    ride on `planBusy` (`docs/dev/comfyui/rows-tab.md` § The toolbar, the selection bar and `planBusy`). Not covered: *Generate prompts…*'s add, a drop of rows onto a locked plan in the rail, and a
    move out of a plan in another dataset; the 409 toast handles those.
  - While a run is live the page invalidates
  `["comfy","rows",planId]` (that run's plan, not the viewed one) and `["comfy","plans",datasetId]`
  on each progress event, so row statuses and the rail's dot update in place; on terminal status it also invalidates `["images", datasetId]`,
  `["subfolders", datasetId]`, `["comfy","plans",datasetId]`, and `["datasets"]`, fires the
  completion/failure toast, and drops the run from the map — every tracked run gets its
  invalidations and toast even if a different plan is selected when it finishes.
  - **Live gallery refresh during a run is driven by TopBar**, not this page:
  `comfy_generate` is in TopBar's `LIVE_IMAGE_JOB_TYPES`, so TopBar re-invalidates
  `["images", datasetId]` (+ `["subfolders"]`) as the job's `done` count advances, at most every
  2 s — the gallery fills in as images import, regardless of which pane is showing it.
- **The prompt library** is a side panel since M6 §8c, toggled by the Rows toolbar's
  *Library* (`ComfyLibraryPanel`; the toolbar is `docs/dev/comfyui/rows-tab.md`), with *Other plans* as its second tab: see
  `docs/dev/comfyui/library-dnd.md`. `PromptLibraryModal` is gone. Query key `["comfy","library"]`.
- **Persistence** (`docs/dev/persistence.md`). The last plan and tab are
  `COMFY_PAGE_STATE_PREFIX` per dataset. The saved plan id is trusted only until the plan list
  first loads (`isPending`); after that a list without it falls back to the first. Trusting
  it on every refetch flipped a pane back to a plan deleted in another pane each time the
  list refreshed. A plan created or duplicated here goes into the cached list at once
  (`addPlanToList`), so selecting it never waits on a refetch. The save waits until the state was
  loaded for the current dataset (`stateDataset`): in the render where a pane switches
  datasets, `planId` is still the old one's. The collapsed folders' key is in `docs/dev/comfyui/rows-tab.md` § Persistence.

### The layout

M6 §8a (`docs/dev/roadmap-archive/m6-comfy-automation.md`). The CSS is the `.comfy-head` and
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
