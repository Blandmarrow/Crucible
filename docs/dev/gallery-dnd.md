# Gallery drag & drop: manual ordering and dragging onto subfolders

This file covers everything in `GalleryPage` that is dnd-kit: the `DndContext` spanning the
sidebar and the grid, the droppable-id namespaces, collision detection, the drag overlay,
what each kind of drop mutates, and the manual `sort_order` reorder that is the same gesture
with a different target. The rest of the gallery — selection, filters, sorting, the subfolder
sidebar's buttons and menus — is in `docs/dev/gallery.md`. The action modals the toolbar
shares with this page are in `docs/dev/frontend-core.md` § SelectionToolbar.

### Manual image ordering

`Image.sort_order: int | None` (nullable, default `NULL`) — stores the custom display position of an image within its `(dataset_id, subfolder)` scope. `NULL` means no custom order has been assigned; such images sort last (`NULLS LAST`) with `created_at ASC` as a tiebreak.

**Activation**: the gallery sort dropdown includes a **"Custom order"** option (`sort=sort_order`). When it is selected for the first time and no image in the current page has `sort_order` set, the frontend silently initialises order from the current page's arrangement by calling `PATCH /images/batch/reorder` with `pageOffset + index` values so that page 2+ images receive sort_orders starting at `(page-1)*pageSize` rather than 0.

**Drag-and-drop**: every card is a `SortableImageCard` in **every** sort mode — the entire card surface is the drag handle (listeners spread on the outer wrapper). The `DndContext` is likewise unconditional and spans the sidebar as well as the grid (see § Drag images onto subfolders); only `SortableContext` — and therefore reordering — is gated on "Custom order". `PointerSensor` with `activationConstraint: { distance: 8 }` lets short clicks still navigate to the detail page. In custom order, `handleDragEnd` falls through its subfolder branch and calls `arrayMove` for an immediate optimistic `qc.setQueryData`, then fires `reorderMutation` (`PATCH /images/batch/reorder`) to persist.

**Renumber Files button**: visible in the gallery toolbar only when "Custom order" is active. Opens a `ConfirmDialog`, then calls `POST /images/bulk-rename` with `sort_by_sort_order: true` — renames every image in the current subfolder to `{subfolder_slug}_001.ext`, `_002`, … in drag order. Useful before export to make filenames reflect training sequence.

**`PATCH /images/batch/reorder`** (`backend/routers/images.py`): accepts `{ dataset_id, updates: [{id, sort_order}] }`. Validates all IDs belong to `dataset_id`, then bulk-updates `sort_order` via `sa_update`. Returns `{ updated: int }`.

**Upload append**: new uploads are appended to the end of the custom order only when *every* existing image in that `(dataset_id, subfolder)` already has `sort_order` set (checked via `COUNT(id) == COUNT(sort_order)` + `MAX(sort_order)`). If any image lacks a `sort_order`, the subfolder is treated as unordered and new uploads receive `NULL`.

**Cross-operation behaviour**:
| Operation | `sort_order` effect |
|---|---|
| Upload to ordered subfolder | Appended at `MAX + 1` |
| Upload to unordered subfolder | `NULL` |
| Batch move subfolder (same dataset) | Preserved |
| Batch move dataset | Preserved in relative sequence, appended after target's max `sort_order`. If target is empty: starts from 0. If target has mixed ordering (some null): cleared to `NULL`. |
| Batch copy dataset | Preserved in relative sequence, appended after target's max `sort_order`. Same logic as move: empty target starts from 0, fully ordered target appends at max+1, mixed ordering clears to `NULL`. |
| Dataset duplicate | Copied from source (both live-copy and snapshot-copy paths) |
| Crop / upscale / LUT new-file | `NULL` (sorts last) |
| Export | Always ordered `sort_order ASC NULLS LAST, created_at ASC` |
| Snapshot create | Captured in `VersionImageState.sort_order` |
| Snapshot restore / branch checkout | Restored to `Image.sort_order` |
| Version diff | Compared; appears as a `sort_order` change entry when ordering changed between versions |

A re-path of the enclosing subfolder (rename or re-nest) does not touch `sort_order` at all — it rewrites `Image.subfolder` and nothing else, so the scope moves with its images intact. See `docs/dev/gallery.md` § Renaming and moving a subfolder.

### Drag images onto subfolders

A single `DndContext` in `GalleryPage` wraps **both** the subfolder sidebar and the image grid (the `flex` row containing them), so cards can be dragged from one into the other. `handleDragEnd` branches on the drop target's id before the reorder logic.

- **Droppable ids** are namespaced `subfolder:{path}` (`subfolder:` alone = root) via `subfolderDropId` / `isSubfolderDropId` / `subfolderFromDropId` in `frontend/src/constants/galleryOptions.ts`, plus the non-namespaced `SIDEBAR_DROP_ID` sentinel. Image ids are UUIDs so the prefix cannot collide. The helpers live in the constants module rather than beside the component because `react-refresh/only-export-components` rejects a component file that also exports plain functions.
- **`DropZone`** (`components/gallery/DropZone.tsx`) is a render-prop wrapper around `useDroppable` taking a raw `id`. It exists for two reasons: `useDroppable` only registers when it runs *inside* the `DndContext`, which `GalleryPage` renders in its own JSX (so a hook at the top of `GalleryPage` would silently never register), and the rows are built inside `renderSubfolderNode`'s closure where a hook can't go at all. It yields `{ setNodeRef, isOver }`; `isOver` fills the row with `var(--accent-glow)` and layers `inset 0 0 0 2px var(--accent)` over it. **A drop target must not share a colour with the selected row.** Both used to paint the same neutral `var(--surface-3)`, leaving a 1px ring as the only thing distinguishing "this is where the images land" from "this is the folder you are filtering by" — on a row the drag preview was sitting on top of. See § Reading the drop target.
- **Cards are draggable in every sort mode.** `SortableImageCard` takes a `sortable?: boolean` prop (default `true`) passed to `useSortable`'s `disabled` as `{ draggable: false, droppable: !sortable }`. Outside custom order the card is still draggable but is not a drop target, so `over.id` can only ever be a subfolder id. **`useSortable` outside a `SortableContext` is safe** — the sortable context has a default value and `useSortable` reads it with a plain `useContext`; with `activeIndex`/`overIndex` at `-1` the sort transform stays `null` and it degrades to a plain draggable. `SortableContext` itself is still gated on `isCustomOrder`.
- **Collision detection** is a composed function that resolves in four steps: (0) a **folder** drag short-circuits everything — see § Dragging a subfolder onto another; (1) a subfolder row under the pointer always wins; (2) otherwise, if the pointer is inside the **sidebar sentinel** (below), return `[]`; (3) otherwise use the `pointerWithin` hits, falling back to `closestCenter` **with folder rows and the sentinel filtered out** so gutter drops between cards still reorder. Plain `closestCenter` is wrong here — a 180 px row's center can beat a card's when dragging near the grid's left edge.
- **The sidebar container is a sentinel droppable** (`SIDEBAR_DROP_ID`) — never a move target, only a way to answer "is the pointer in the sidebar?". Step (2) returning `[]` makes `over` `null`, so `handleDragEnd`'s existing `!over` guard no-ops. Without it, a drop on sidebar chrome — the "All" row, the header, the create form, the padding below the last row — reaches the `closestCenter` fallback, which scores against `collisionRect` (the **dragged card's** rect, not the pointer) and therefore returns a grid card; in custom-order mode that silently reordered the image and persisted it via `PATCH /images/batch/reorder`. Two constraints on the filter in step (3): the sentinel must be excluded too, or a gutter drop could resolve to the 180 px sidebar rect instead of a card; and the sentinel comparison must come **before** `!isSubfolderDropId(...)`, because that is an `id is string` predicate whose negation narrows `c.id` to `number` and stops the comparison compiling. Note `SIDEBAR_DROP_ID` (`"subfolder-sidebar"`) is deliberately outside the `"subfolder:"` namespace — one character apart, so do not widen the prefix.
- **`DragOverlay`** is portaled to `document.body`. This is required, not cosmetic: the grid's scroll container is `overflow-y: auto`, so without it the dragged card is clipped the moment it crosses into the sidebar. A consequence is that reorder drags now dim the source card in place rather than moving it. A multi-image drag shows an "N images" badge.
- **Selection semantics**: dragging a card that is in the current selection moves the whole selection; dragging an unselected card moves only it. `clear()` is gated on `vars.ids.length > 1` so a single-card drag can't wipe an unrelated selection — note that counts images *actually moved* (post no-op filter), not images dragged, so dragging a 5-image selection of which 4 are already in the target moves one and leaves the selection standing. The selection store is module-global, so ids are filtered by `datasetByImageId.get(id) === datasetId` before sending — the backend derives `dataset_id` from the first row and would otherwise move another pane's images into this dataset.
- **No-op guard**: the backend does *not* filter out images already in the target, and with `rename_on_move` they would be pointlessly renamed to a fresh unique stem. `moveImagesTo` drops those ids client-side against the current page cache (`ImageListItem.subfolder`) and toasts "Already in …" when nothing remains. Ids absent from the cache are sent through — their subfolder is unknown.
- The mutation mirrors `SelectionToolbar`'s `moveSubfolderMutation` on the parts that must not diverge: same `SUBFOLDER_RENAME_KEY` read, same two invalidations, same success toast. It deliberately differs in two places — `clear()` is conditional (above) where the toolbar's is unconditional, and `onError` surfaces the server's `detail` where the toolbar shows a flat "Move failed". No optimistic update: `rename_on_move` changes `filename`/`file_path`/`thumbnail_path` server-side. See `docs/dev/frontend-core.md` § SelectionToolbar for the toolbar side.

- **`VideoStrip` is mounted outside this `DndContext`** (and outside the grid's scroll container) on purpose: inside it, the strip's cards would join the collision detection above and the subfolder droppables, and inside the container they would sit under the drag-to-upload handler. See `docs/dev/video-ui.md`.

**Known gaps** (deliberate, not bugs): the sidebar does not auto-scroll during a drag — dnd-kit only auto-scrolls the *dragged* element's ancestors — so a target below the fold must be scrolled to first, and that applies to a dragged **folder** as much as to a dragged card. Collapsed parent rows are themselves valid drop targets; there is no spring-loaded expand-on-hover, though a folder drop re-points `expandedPaths` and adds the destination's ancestors, so a folder dropped into a collapsed parent is at least revealed where it landed. In custom-order mode, only the sidebar is sentinel-guarded: a drop anywhere else `pointerWithin` finds nothing — the toolbar, the filter bar, the pagination row, past the window edge — still reaches the `closestCenter` fallback and reorders to the nearest card. That predates the drag-to-subfolder work; guarding it would mean a second sentinel around the grid column.

### Reading the drop target

**The drag preview covers the row it is about to drop into.** `DragOverlay` sizes itself to
the dragged node, so an image card is card-sized while the sidebar is 180 px wide — the
preview lands squarely on top of the target row and whatever highlight it is wearing. The
row styling above is therefore only half a fix; it is invisible under the card. Three
things together make the target readable, and none of them is redundant:

- **The preview fades to `opacity: 0.25` while over a folder row** (0.92 otherwise), so the
  row's accent fill and ring show through it. The fade is on an inner wrapper around
  `ImageCard`, *not* the outer positioned div, so the badges below stay at full strength.
- **A chip pinned to the centre of the preview names the target** — folder glyph plus the
  path, or `(root)` for the empty-string row. It is the unambiguous half: a fill and a ring
  say *a* row is targeted, the chip says *which*, and it is legible wherever the pointer is
  because it travels with the preview.
- **The "N images" badge stays outside the faded wrapper**, since the count is the other
  thing worth reading at the moment of dropping.

`dropTargetPath` holds it: `null` for "not over a folder row", `""` for the `(root)` row,
which is a real target — so the state is three-valued and `if (dropTargetPath)` is a bug
that silently drops the root case. It is set from `onDragOver`'s `e.over`, which
**collision detection has already narrowed** (step 0 above rejects a self-or-descendant
folder drop), so the chip reads dnd-kit's decision rather than re-deriving it and the two
can never disagree. `handleDragStart`, `handleDragEnd` and `handleDragCancel` all clear it;
a missed clear leaves a chip stuck on the next drag's preview. Only the image branch of the
overlay consumes it — a folder drag's preview is a small pill that does not cover the row.

Verified by screenshot rather than by spec, for the reason § Dragging a subfolder onto
another gives: there is still no drag e2e idiom in the suite.

### Dragging a subfolder onto another

A subfolder row is *both* a droppable and a draggable, so the sidebar tree can be re-nested by dragging one row onto another. The drop re-paths the folder and its whole subtree through `PATCH /datasets/{id}/subfolders` — a pure DB label rewrite, no filesystem work — and shares one mutation with the context menu's **Move to…** and **Rename…**. See `docs/dev/gallery.md` § Renaming and moving a subfolder for the endpoint, the guards and the client-side bookkeeping a re-path requires.

- **A second id namespace.** `SUBFOLDER_DRAG_PREFIX = "folder-drag:"` with `subfolderDragId` / `isSubfolderDragId` / `subfolderFromDragId`, in `constants/galleryOptions.ts` beside the drop-id helpers (same `react-refresh/only-export-components` reason). It has to clear **both** `"subfolder:"` and `SIDEBAR_DROP_ID` (`"subfolder-sidebar"`), which are already only one character apart — hence a wholly different word rather than a longer `subfolder…` prefix. A row therefore carries two ids at once: `subfolder:{path}` as a droppable and `folder-drag:{path}` as a draggable.
- **`canDropFolderOn(src, dest)`** — `dest !== src && !dest.startsWith(src + "/")`, i.e. a folder may not be dropped on itself or on any of its own descendants. Lives in the same module and is used in three places: collision detection, the drag-end re-check, and `MoveSubfolderModal`'s list.
- **`SubfolderRowDnd`** (`components/gallery/SubfolderRowDnd.tsx`) is a render-prop wrapper running *both* `useDroppable({id: dropId})` and `useDraggable({id: dragId})` and yielding a memoized combined `setNodeRef` (the same merge `useSortable` does internally) plus `setActivatorNodeRef`, `listeners`, `attributes`, `isOver`, `isDragging`. It exists for exactly the two reasons `DropZone`'s docstring gives — the hooks must run inside the `DndContext`, and the rows are built inside `renderSubfolderNode`'s closure. Both ids are required props: the `(root)` row and the sidebar sentinel keep plain `DropZone`, so there is no disabled-draggable branch to get wrong. `DropZone.tsx` itself is untouched.
- **Collision detection short-circuits on a folder drag**, as step (0) above, and this is what keeps `isOver` honest: it returns the single `pointerWithin` hit that is a subfolder droppable **and** passes `canDropFolderOn`, else `[]`. Guarding only at drag end would let a self-or-descendant row light up with the accent ring and then refuse the drop. The early return also stops a folder drag ever resolving to a grid card — today `pointerWithin` over the grid returns a card, which survives only because `findIndex` returns `-1`, luck rather than design — and bypasses the sidebar sentinel entirely, since a folder drag has no reorder to guard against.
- **`handleDragEnd`'s folder branch goes first**, before the existing `isSubfolderDropId(over.id)` branch, which assumes `active.id` is an image id. It resolves the destination parent from the drop id (`""`, the `(root)` row, meaning top level), re-checks `canDropFolderOn` as defence in depth, returns silently when the folder is already in that parent, and otherwise fires `repathSubfolderMutation` with `kind: "move"`. It is deliberately a **separate mutation** from `moveImagesTo` — the latter's `clear()` touches the module-global selection store and must never fire for a folder drag.
- **Dropping on `(root)` means "move to top level."** It is the only un-nest gesture available by drag, since the sidebar background is swallowed by the sentinel, and it is symmetric with dragging *images* onto `(root)`. That row needs no change to support it — it is already a `subfolder:` droppable with an empty path.
- **Listeners go on the label button, not on the row.** `setNodeRef` sits on the row so dnd-kit measures the whole thing and the droppable rect stays put; `setActivatorNodeRef` + `listeners` + `attributes` sit on the label button inside it. Two reasons, both worth keeping in mind before "simplifying" them onto the row: the four hover buttons (`+`, move, copy, `×`) are *siblings* of the activator, so a pointerdown on `×` never reaches the drag listeners — and `PointerSensor.activators` has **no** interactive-element filter, so row-level listeners would turn a press-and-slide on `×` into a drag. The label's own `onClick` still fires because of the 8 px `activationConstraint`, the same contract the image cards rely on. The row gets `opacity: isDragging ? 0.4 : 1`.
- **The overlay needs its own branch.** `activeDragImage` looks up `images` only, so a folder drag would render an empty `DragOverlay`; a sibling branch renders a small pill (folder glyph + label derived from the drag id) styled off the sidebar row. `activeDragCount` is gated to 0 for a folder drag so the "N images" badge cannot appear.
- **Right-click never starts a drag**: `@dnd-kit/core`'s `PointerSensor.activators` bails on `event.button !== 0`, so the row's context menu and its draggable coexist with no special handling.

**Coverage gap, deliberate.** `frontend/e2e/gallery-subfolders.spec.ts` covers the *menu* routes to rename and move; **no committed spec drives the drag**. An 8 px `activationConstraint` needs a multi-step `mouse.move` dance and there was no existing drag spec to copy the idiom from, so the drop was verified against a throwaway spec instead — nesting, un-nesting onto `(root)`, the self-and-descendant refusal, and a press-and-slide on `×` not becoming a drag, each run three times clean. If a drag spec is ever wanted, that is the list it should cover.
