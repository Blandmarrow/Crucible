# The Rows tab (ComfyPage)

The Rows tab of `ComfyPage` (M6 §8b, §8d), the frontend of the queue: the grouped rows table and its Folder column, the toolbar with *Add ▾* and the paste dialog, the selection bar, `planBusy`, the plan rail and the folder tree, and the remembered collapsed folders. The user guide is `docs/comfy-queue.md`. The page around it (routing, the header, `activeRuns`, the layout) is `docs/dev/comfyui/page.md`; the endpoints these controls call are `docs/dev/comfyui/rows-api.md`; the library panel and drag and drop are `docs/dev/comfyui/library-dnd.md`.

## The rows table and its Folder column

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
  note in `docs/dev/comfyui/page.md` § Frontend. It does **not** route through `commitCell`, which builds a `values` dict: the
  shared `updateMutation` takes a generic patch object, so `commitCell` passes `{ values }` and
  the folder cell passes `{ subfolder }`. A folder must never enter `values`. It commits the
  **trimmed** draft, as the modal does — the server folds slashes but keeps spaces, so
  `knight ` would be a second folder. **Every Folder cell is locked while the plan runs**, not
  only the busy row's, unlike the parameter cells: the run files each row from the rows it
  loaded at start (they reload only after a failed row), so a mid-run edit to a later row
  would show a folder its image never went to, and a finished row keeps it. Since M6 §8c the server
  refuses the change too (`docs/dev/comfyui/rows-api.md` § A live run locks its plan's folders and rows), since this lock knows only
  the runs this tab started. The
  `columns.length === 0` early return still hides the whole table for a plan with no per-row
  pins, folder column included — accepted, since such a plan cannot run anyway.
  - **A row the blueprint still counts is locked** (`blueprint_linked`): a lock icon and a
    read-only input, keeping `placeholder="(root)"` so the e2e's cell count holds. Moving it
    unlinks it from its leaf, and only Set folder asks about that.
- **`SetRowFolderModal`** (the selection bar's *Set folder…*, disabled while
  that plan is running) — a text input with a `<datalist>` of the dataset's existing folders
  (query key `["subfolders", datasetId]`, usually already cached by `ComfyRunBar`, `docs/dev/comfyui/page.md` § Frontend); a datalist
  rather than the run bar's closed select, because creating folders that do not exist yet is
  the point. Calls `setRowsSubfolder`, invalidates `["comfy","rows",planId]`, toasts the count.
  - It takes the selected **rows**, not ids, to know which are `blueprint_linked`. When the
    typed folder would move any of them (compared on the server's slash-folded spelling, so a
    no-op never asks), Apply shows the three choices of the plan's ruling in place of its
    buttons (`comfy-linked-confirm`): *Move and unlink* (`linked: "unlink"`, which also
    invalidates the blueprint preview, since the leaf reads short), *Move hand-made rows
    only* (`"skip"`) and *Cancel*. Otherwise it sends `"refuse"`, the server's default, which
    409s if the client was wrong about a row.

## The toolbar, the selection bar and `planBusy`

- **`planBusy` is wider than `isRunning`.** `busyPlanIds` adds every plan with a
  non-terminal `comfy_generate`, `comfy_prompts` or `comfy_rewrite` entry in the job store's
  `activeJobs` (`BUSY_JOB_TYPES`), so a run started in another tab, or a prompt or rewrite
  job, counts too. That needs `plan_id` on the job's events: the prompt and rewrite jobs
  always sent it, and the generate job's progress events carry it since the review. It disables the selection bar's *Delete*, the tree's folder writes
  (*New folder…* and its header + included) and the rail's *Delete…*. A paused automated
  Run has no job, so the page reads the dataset's Runs instead (`docs/dev/comfyui/page.md` § Frontend), and the server's 409
  stays the backstop (`docs/dev/comfyui/rows-api.md` has the guards).
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
- **`SaveToLibraryModal`** (the selection bar's *Save to library…*) — category
  input (a `/` path, ≤512) with a `<datalist>` of every category path and its ancestors; saves the selected rows' **effective**
  prompts (via `GET /comfy/plans/{id}/prompts`, backend-authoritative) through
  `POST /comfy/library`; toast reports created/skipped-duplicates.

## The plan rail, the folder tree and the grouped table

M6 §8b (rulings: `docs/dev/roadmap-archive/m6-comfy-automation.md` § The page rework). The rail
is the Rows tab's left column, and it also shows when there are no plans, so the empty state
can make one. The approved mockup put it on the Rows tab only, so switching plans from
another tab means going back to Rows.

- **`ComfyPlanRail`** lists `GET /comfy/plans` with each plan's `row_count` and a dot. The dot
  is *running* when `status_counts.running` is set or the plan is in `activeRuns` (`docs/dev/comfyui/page.md` § Frontend, which
  knows a run started from this page before its first row moves), else *failed* when
  `status_counts.failed` is set. Its ⋯ button opens `ContextMenu` at the button: *Rename…*
  (inline input, Enter or blur saves), *Duplicate…* and *Delete…*. *New plan* sits under the
  list.
  - Rename and delete take the plan's id from the menu, not `effectivePlanId`: a menu can
    name a plan other than the one shown. Deleting the shown plan falls back to the first.
  - **`DuplicatePlanModal`** (on `ModalShell`) proposes a free name ("X (copy)", then
    "(copy 2)" and on) and an *Include rows* checkbox, on by default. The copy is selected
    on success. What the server copies is `docs/dev/comfyui/rows-api.md` § Plan duplicate.
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
    `disabled`, since a disabled button shows no tooltip (`docs/dev/menus-popovers.md`).
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

## Persistence

- **Persistence** (`docs/dev/persistence.md`). The tree's and the table's collapsed folders
  are `COMFY_ROWS_VIEW_KEY` per plan, through `usePlanRowsView` (`components/comfy/rowsView.ts`),
  stored as *closed* paths so a new folder is open; deleting a plan clears it. The last plan and tab are `docs/dev/comfyui/page.md` § Frontend.
