# The library panel, Copy to plan and drag and drop

M6 §8c on the ComfyUI page's Rows tab (`docs/dev/comfy-automation-plan/README.md` § 8c, which
records the rulings). It covers the prompt library side panel (`ComfyLibraryPanel`) with its
*Other plans* tab, Copy to plan, the row menu, and drag and drop with the button route behind
every drag. The rest of the page is `docs/dev/comfyui/page.md`. The endpoints are
`docs/dev/comfyui/queue.md` § Backend: the library's nested categories, `rows/place` and
`rows/copy`. The user guide is `docs/comfy-queue.md`.

## The library panel

`components/comfy/ComfyLibraryPanel.tsx` replaced `PromptLibraryModal`. A modal hid the table
it was filling, and moving prompts between plans was buried in it.

- **Where it sits.** A third column of `.comfy-rows-grid` while open (`.lib-open`: 240px |
  1fr | 300px), as `.comfy-lib` in `frontend/src/index.css`. It is sticky with its own scroll
  and gets the rail's z-index 6, for the same reason: the menus it opens.
  - Under a 1040px container it spans the top, above the rail and the table, capped at 380px
    tall. The panel was opened to be used, so it stays in view rather than falling below a long
    table. Under 760px everything is one column.
- **Toggle and memory.** The toolbar's *Library* button (`aria-pressed`) opens and closes it;
  so does the panel's ✕. Open state and tab ride in `COMFY_PAGE_STATE_PREFIX` per dataset
  (`libOpen`, `libTab`). Collapsed categories are `COMFY_LIBRARY_VIEW_KEY`, global like the
  library, stored as closed paths. Both are in `docs/dev/persistence.md`.
- **Real tabs.** *Library* and *Other plans* are `role="tab"` in a `tablist`, not pressed
  buttons. As buttons, the panel's *Library* tab and the toolbar's *Library* toggle had the
  same accessible name, which a screen reader cannot tell apart and Playwright refuses.
- **The subcomponents take everything by props** (`LibraryCategoryRow`, `LibraryPromptCard`,
  `OtherPlanRowCard`), so the drag layer can wrap them without a context of their own. Their
  test ids are `comfy-lib-category` (`data-category`), `comfy-lib-prompt` (`data-prompt-id`)
  and `comfy-xplan-row` (`data-row-id`).

### The Library tab

- **The tree** is built from every prompt's category and its ancestors, so `portraits` shows
  when only `portraits/studio` holds prompts. Counts include the subtree. A prompt card shows
  under its own category only, not under ancestors.
- **Search** matches text and category, case-blind. While searching, every matching category
  is open and the rest hide; a category-name match keeps all its prompts.
- **Selection** is derived against the cards on show, so a search that hides a selected
  card, or a folded category above it, drops it from what *Add*, *Move to…*, *Delete* and a
  drag act on: the page's rule that nothing acts on what the user cannot see. Ids a refetch
  removed fall away the same way, without an effect. *Select all / Select none* over the
  cards on show sits above the list, as on the Other plans tab. The selection's actions sit
  in a slot that is always there (`comfy-library-selection`), disabled under a hint while
  nothing is ticked, so the tree never moves under the pointer on the first tick or the last
  untick: the user's ruling on 2026-11-07, the rows bar's of 2026-11-05 applied to the panel.
  The real controls render either way, so its height is the same by construction rather
  than a measured constant. *Add n to plan* files the rows through `rows/bulk` in the folder its
  select names, which defaults to the folder in view and follows it as the tree selection
  changes. It is disabled while the plan has no prompt pin. *Move to…* takes a typed or listed
  category path. *Delete* asks first.
- **✎ edits a prompt's text** in place (Enter saves, Shift+Enter is a newline, Escape
  cancels) through `PATCH /comfy/library/{id}`. A 409 for a duplicate is toasted and the
  editor stays open.
- **The category menu** (⋯ or right-click):
  - *Rename…* uses `FolderNameModal` with `noun="category"`, which changes only its wording;
  - *Move to…* uses the gallery's `MoveSubfolderModal` over the category paths, (root)
    included;
  - *Delete…* states the subtree's prompt count, counted over the **whole** library
    (`subtreeCount`), not the search's matches: the server deletes the subtree whatever the
    search shows, so a filtered count undersold the delete. A delete also prunes the path and
    its descendants from the stored closed paths (`pruneClosed`), so a new category reusing the
    name starts open.
  - Rename and Move go through `categories/move` and re-point the stored closed paths.
  - **A known mismatch:** `MoveSubfolderModal` refuses a destination where the name already
    exists, as it does for gallery folders, though the server would merge. Rename onto an
    existing name does merge, and the toast says how many prompts merged.

### The Other plans tab

Pick any dataset, then any plan but this one, and the list joins two reads by row id:
`GET …/prompts` for the effective prompt (rows whose prompt is empty are absent there) and
`GET …/rows` for each row's folder, status (the table's badge, error as its tooltip) and
`blueprint_linked`. The panel is keyed by the page's dataset, so a pane switching datasets
starts the tab again on *(this dataset)*.
- *Copy here* and *Move here* call `rows/copy` with `subfolder: null`, so each row keeps its own
  folder.
- A move with linked rows selected first shows the same three choices as Set folder
  (`comfy-linked-confirm`); otherwise it sends `"refuse"`.
- The toast names each skip reason from `skipped_no_prompt` and `skipped_linked`, worded as in
  `copyRows.ts`.
- Both refresh the target's rows, prompts and plan list, and the source's rows, prompts, plan,
  plan list (by the source's dataset, since the tab spans datasets) and blueprint preview.

## The button routes

Every drag has a button or menu route that does the same thing, as in the gallery: it keeps
the page usable without a mouse, and e2e drives the buttons.

| Drag | Its button route |
|---|---|
| Rows onto a folder or group header | the selection bar's *Set folder…* |
| Rows onto another plan | the selection bar's *Copy to plan…* |
| Rows onto a library category | the selection bar's *Save to library…* |
| Library prompts onto the table or a folder | the panel's *Add n to plan* |
| Library prompts onto a category | the panel's *Move to…* |
| A category onto a category | the category menu's *Move to…* |
| A folder onto a folder | the tree menu's *Move to…* |
| Rows between rows | the row menu's moves, and *Set folder…* |
| `.txt` files onto the table | *Add ▾ → Import .txt…* |
| A workflow `.json` onto *Workflow & Pins* | *Load .json file* |

- **The folder in view** is where rows land when nothing names a folder: the folder picked in
  the tree, or (root) under *All rows* (`folderInView` in `ComfyPage`). *Import .txt…* and *Add n
  to plan* use it, as the drops will.
- **`CopyRowsToPlanModal`** (on `ModalShell`) picks a plan in this dataset, then Copy or Move. A
  move with linked rows asks first. The mutation is `useCopyRowsMutation` in
  `components/comfy/copyRows.ts`, shared with the drop on a plan, which opens this modal with
  `initialTargetId` and `initialMove` when a move has to ask. Its button is the selection
  bar's *Copy to plan…* since §8d (`docs/dev/comfyui/page.md`).
- **The row menu** opens from a grip button at each row's start (`aria-label="Row actions"`).
  The same button becomes the row's drag handle in the drag layer, as the gallery's label
  button is both. Its entries are *Move to top of folder*, *Move up*, *Move down* and *Move to
  bottom of folder*. Each one calls `rows/place` with the neighbouring row in the folder's run
  order as the anchor.
  - The order comes from `folderOrder` in `ComfyPage`, the plan's rows grouped by folder. The
    API returns rows in run order, so no sort is needed.
  - It steps between the rows on show: under a text or status filter, *Move up* past a
    hidden row would look like nothing happened.
  - `rowBounds` disables the moves a row cannot make.
  - Every entry is disabled while the plan runs.
  - It holds buttons only: the e2e counts the table's textareas and `(root)` inputs as row
    cells.

## Drag and drop

One `DndContext` in `ComfyPage` wraps the Rows tab: the rail, the tree, the table and the
panel. Desktop files are native events instead. The pieces:
- `components/comfy/dnd.ts` is pure: what a draggable and a droppable carry
  (`DragData`/`DropData`), their namespaced ids, and **`resolveDrop`, the one drop matrix**
  (the plan's § 8c table, with `describeDrop` for the overlay's words).
- `DndRow.tsx` is a row that is a drop target, a drag source, or both. The drag starts from a
  handle it passes back: a grip, or a label button, never a row holding inputs.
- `runDrop` in `ComfyPage` turns an action into the mutation its button route calls.
- `LinkedAskDialog.tsx` asks before a drop moves rows the blueprint counts.

**The settings, and why:**
- `PointerSensor` with `distance: 8`, as the gallery. A click on the grip still opens the row
  menu, and a click on a tree folder still filters the table.
- `MeasuringStrategy.Always`: rows scroll inside the virtualizer, so rects measured at drag
  start go stale.
- **The virtualizer places items with `top`, not `translateY`.** dnd-kit measures droppables
  ignoring transforms, so every translated row measured at the top of the body, and no drop
  between rows found its target.
- The virtualizer's `rangeExtractor` keeps the dragged row mounted while it scrolls out of
  view (`draggingRowId`).
- **Collision** is `pointerWithin`, filtered through `resolveDrop`, so a target the drag
  cannot use never lights up. A row, header or folder under the pointer wins over the table
  body around it. The body is a droppable of its own, which also lets dnd-kit auto-scroll it.
  - **A hit counts only if its node is under the pointer** (`document.elementsFromPoint`).
    `pointerWithin` does not clip a rect to its scroll container, so a category scrolled out
    of the panel, or an overscan row above the table's header, caught drops it should not. A
    hidden category catching a prompt would re-file it, and a category move merges.
  - **And only if it is the topmost thing there.** `elementsFromPoint` returns the whole
    stack, so a row or category painted *under* the sticky `.comfy-head` still matched. The
    filter takes the first element not inside the overlay (`.comfy-drag-overlay`, the class
    on `DragOverlay`, which takes no data attributes) and accepts a node only if it contains
    that element.
- **A drop that changes nothing is refused**: `resolveDrop` returns null for rows dropped on
  the folder every one of them is already in, and for prompts dropped on their own category
  (`DropContext.rowFolders`, `promptCategories`). Before, the first sent `rows/place` with no
  anchor, which moved the rows to the end of their folder, and the second toasted "Filed 0
  prompts". The page reads the library for that lookup from the panel's cache
  (`["comfy","library"]`, `enabled: false`), never fetching it itself.
- **The live pointer and Shift** are kept in refs by capture-phase `pointermove`,
  `keydown` and `keyup` listeners during the drag, seeded from the start event.
  - **Before or after a row** compares that pointer with the row's middle. Not the dragged
    rect: with a `DragOverlay` that rect is the small chip. And not start + `delta`: dnd-kit's
    delta includes the scroll since the drag began, while a droppable's rect is in viewport
    terms, so it drifted a row as soon as the body auto-scrolled.
  - **Shift** turns a drop on a plan into a move; dnd-kit's drag end carries no modifier
    state. Seeding it from the start event catches a Shift held before the drag, for which
    macOS repeats no `keydown`.
  - The overlay chip names the drop as it changes ("Copy 2 rows to X · Shift moves").

**What each drop does:**
- **Rows** drag from the grip. A selected row drags the whole selection, in run order;
  otherwise the row alone. No row drags while its plan runs.
  - Onto a folder or group header, they go through `rows/place` with the folder and no
    anchor, so they land at the end of that folder.
  - Between rows, through `rows/place` with the anchor, re-filing into the anchor's folder.
  - Either asks first (`LinkedAskDialog`) when rows the blueprint counts would move.
  - Rows re-filed out of the folder in view leave the selection, as a filter change clears
    it: the toolbar must never act on rows the table no longer shows.
  - Onto another plan in the rail, `rows/copy` copies them, or moves them with Shift. A move
    of linked rows opens `CopyRowsToPlanModal` preset to that plan, to ask.
  - Onto a category, they are saved to the library.
- **Library prompts** drag from their grip, the panel's selection when the card is in it.
  - Onto the table they land in the folder in view; onto a folder, header or row, in that
    folder. Either way through `rows/bulk`.
  - Onto a category they are re-filed (`library/move`).
- **Other plans' rows** copy through `rows/copy`: onto the table each keeps its own folder,
  onto a folder they all go there.
- **A folder** onto a folder or *(root)* re-nests (`folders/move`). A locked folder, and every
  folder while the plan runs, cannot be dragged.
- **A category** onto a category or the panel's *Top level* row re-nests (`categories/move`,
  which merges). The page then fires `LIBRARY_REPATH_EVENT` on `window`, so the panel
  re-points its stored folds as its own Rename and Move do.
- **Desktop files:**
  - `.txt` files onto the table body import into the folder in view
    (`importPromptFiles`). They are matched by extension, since a `.txt` can report an empty
    `type` on Windows.
  - A `.json` onto *Workflow & Pins*' template panel goes to `loadFile`, then waits for Save
    like a loaded file.
  - Both gate on `dataTransfer.types` holding `"Files"`, so no internal drag lights them up;
    dnd-kit uses pointer events and never sees a file drag. The table keeps a depth counter
    with a capture-phase reset, as the gallery's upload overlay does.

## Testing

`frontend/e2e/comfy-library.spec.ts` drives the panel: the nested tree and collapse, a text
edit and a refused duplicate, *Add n to plan* into a chosen folder, a category rename that
merges, and *Copy here* keeping folders. The 2026-11-05 review added the button routes it
lacked: a ticked prompt's *Move to…*, a category's *Move to…*, and a category *Delete…* under a
search quoting the whole subtree's count.
- **The library is global.** It survives across tests in the shared e2e database, so each test
  seeds categories under its own timestamped prefix and deletes them in `afterEach`.

`frontend/e2e/comfy.spec.ts` covers the rest:
- *Copy to plan* keeps folders, and Move empties the source;
- the row menu reorders, and its bounds disable *Move down* on the last row;
- the panel stays open across a reload;
- since the review, the selection bar's *Save to library…*, the tree's *Move to…* and *Load
  .json file*, Set folder dropping moved rows from the selection, a desktop file dropped
  outside any zone refused (`FileDropGuard`), and keyboard use of `ContextMenu`: a row
  reordered through its grip menu, and focus back on the rail's ⋯ after its dialog's Escape.

`frontend/e2e/comfy-dnd.spec.ts` drives the gestures themselves (its header maps each drag to
the button route that has the e2e; folder-onto-folder and category-onto-category drags have
only their *Move to…* routes tested), the repo's first committed
drags: a row onto a tree folder, a row below another, a library prompt onto the table, and
`.txt` files dropped from the desktop.
- `dragTo` presses, travels 12px to pass the 8px activation, then moves to the target in
  steps. It scrolls both ends into view first: the browser sends no pointer events past the
  viewport, and the table body's middle is often below it.
- The file drop dispatches `dragenter`, `dragover` and `drop` with a `DataTransfer` built in
  the page.
- The drag spec ran 20 of 20 under `--repeat-each=5` when it landed.
- It also pins two review findings: Shift held *before* the drag still moves, and rows
  dragged out of the folder in view leave the selection. `comfy-library.spec.ts` pins the
  panel's search shrinking the selection.
- **Not covered by any spec:** a drop between rows while the body auto-scrolls, and a drop
  over a target scrolled out of the panel. Both were found by reading dnd-kit's source and are
  hand checks.
