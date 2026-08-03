# Gallery: selection, subfolders, filters & sorting

This file covers reading and curating a gallery in `GalleryPage`: image selection and the shift-click range model, the subfolder sidebar and its rename/move operations, the filter controls, and sorting. Everything dnd-kit — manual drag ordering, dragging image cards onto subfolder rows, and dragging a subfolder onto another — is in `docs/dev/gallery-dnd.md`. The detail view and its navigation context are in `docs/dev/image-detail.md`; file naming, imports and rescan in `docs/dev/image-files.md`.

### Gallery image selection

`ImageCard` accepts an optional `onSelect?: (id: string, shiftKey: boolean, isCheckbox: boolean) => void` prop. When provided it routes both checkbox clicks and shift-clicks on the card body through this callback instead of calling `toggle` directly. `GalleryPage` always provides `handleSelect` as `onSelect`; contexts that render `ImageCard` without it (e.g. `BucketPanel`) fall back to the raw `toggle`.

**Checkbox size is user-configurable** (Settings → Gallery, 14–32 px, default 18) via `uiPrefsStore.galleryCheckboxSize` — see `docs/dev/frontend-core.md` (§ Frontend state) for why this is a store and not a localStorage read. The checkbox itself is `components/gallery/GalleryCheckbox.tsx` (`size` + `selected` props, purely presentational — callers read the store and pass the size down). It derives the tick SVG (`size / 2`), border radius (`max(4, size / 4.5)`) and border width (2 at ≥ 26 px, else 1.5) from that one number, so the checkbox scales as a unit rather than growing a box around a fixed-size tick. Those formulas are private to that file and must stay there: `SettingsPage`'s slider preview renders **this same component** rather than a lookalike, so the preview cannot drift from what the gallery draws. `ImageCard` wraps it in a click target that adds `CB_PAD` (4 px) of padding with a matching negative `top`/`left` offset: this widens the click target past the visual box while keeping the box pinned at the card's 8 px corner inset. Clicks in that pad toggle selection rather than opening the image — that is intentional, and is most of the "the checkbox is hard to hit" fix. The quality-flag badges opposite it are deliberately left at a fixed 18 px.

**The card's thumbnail overlay is a fixed z-order**, and a new overlay has to pick a layer in it deliberately: checkbox `top:8 left:8 z:3`, quality-flag cluster `top:8 right:8 z:3`, aesthetic badge `bottom:8 right:8 z:3`, caption-drop overlay `inset:0 z:4` (it must cover everything), and the style-match meter — a 4-px strip at `left/right:0 bottom:0` — at **z:2**, under all three badges, which it clears anyway on their 8 px insets. The meter carries no `pointerEvents: "none"`: that would kill its `title`, which is where the mode, reference count and mixed-scope caveat live. It renders nothing at all on an image with no `style_similarity_score`, and its fill is a single `--accent` — never a colour band — because a low style match means *different*, not *defective*, and the card already spends red/amber on four flags plus the aesthetic score. See `docs/dev/image-similarity.md` § Making the score readable for the percentile contract behind its width.

**`handleSelect` in GalleryPage** — two `useRef`s drive range tracking:

- `lastSelectedId` — the selection anchor; set on every plain (non-shift) toggle, and on shift+checkbox when no valid range could be computed (stale anchor). Never moved by a shift-only interaction.
- `lastRangeEndId` — the endpoint of the last contiguous range; set each time a checkbox shift-click successfully applies a range, reset to `null` on any plain toggle.

| Interaction | Effect |
|---|---|
| Click checkbox | Toggle image; anchor = clicked image |
| Shift+click checkbox | Range from anchor → clicked; images that fell out of the previous range are deselected via `replaceRange`; anchor unchanged |
| Shift+click card body | Toggle image only; anchor and range-end unchanged |

The `onMouseDown` handler on the card's outer `<div>` calls `e.preventDefault()` when `Shift` is held to suppress browser text-selection. This does not interfere with dnd-kit's `PointerSensor` (which listens to `pointerdown`, not `mousedown`).

**"Select all" means the filters, not the page.** `GET /images/` returns one page, so the button cannot answer the question from what it has: it calls `imagesApi.listIds(filterParams)` and selects the whole matching set — the dataset, or the subfolder/license/video-frames view if one is active. Page-only lives in a caret menu beside it (`data-testid="select-all-menu-btn"`, rendered only when `totalCount > images.length`), whose two items are *All 1,240 matching filters* and *This page only (50)* — page-only being the rarer intent, and the one with alternatives in drag and shift-click. Over the server's `SELECT_ALL_ID_CAP` the response comes back `truncated` and the toast says *the first 20,000 of N* instead of claiming success — see `docs/dev/image-filters.md` for both endpoints. Nothing downstream cares: `SelectionToolbar` and every batch endpoint take a plain id list, and their confirm text reads from the selection count, so a destructive action states the real number.

The inline row above the grid (`data-testid="select-all-matching"`, Gmail's pattern) survives as the follow-up for the paths that select only a page — the caret menu, a drag, a shift-click range, or checking every tile: *All 50 on this page selected — Select all 1,240 matching filters*. It is not the primary route to the whole set any more, and must not be relied on as one; it renders at the top of the **scrolling** grid container, so a user who scrolled before selecting never sees it, which is exactly how the feature shipped once already and read as "select all is broken".

Three details are load-bearing:

- **Every bulk select is additive** — `selectMany` unions, and nothing but `clear`/`clearDataset`/`deselectMany` removes an id. The store is the only record a selection has, so a "select all" that rebuilt the Set (as `selectAll` did) silently discarded the previous page's ids, the previous subfolder's, and the other pane's, with no way to get them back. The button's other face is **Deselect all** → `clearDataset(datasetId)`, not `clear()`: a gallery's deselect means its own dataset, never the split pane's unrelated selection. Its label is driven by `pageAllSelected = images.length > 0 && images.every(i => selectedIds.has(i.id))` — a question about the visible ids, never about `count`, which routinely exceeds the page once a whole-filter selection exists.
- **"All N matching selected" counts only this dataset's ids, and asserts equality.** The selection store is module-global and in a split-pane setup holds ids from other datasets, so the row compares `totalCount` against a filtered `selectedHere`, not against the store's raw `count`. That comparison is `===`, not `>=`: the row asserts *set identity* and approximates it by cardinality, and since filters never clear the selection, a `>=` let a stale superset read as complete — take the offer over 8 images, narrow to a 3-image subfolder, and the row claimed *All 3 matching images selected* while the toolbar read *8 selected*. Additive selects make that superset ordinary rather than rare (gather one subfolder, then another), so the row offering again is the common branch and the honest one.
- **The count query key nests under `["images", datasetId]`.** Every gallery mutation invalidates that prefix (rescan, import, subfolder move, and the same pattern in `SelectionToolbar`) and TanStack matches by prefix, so `["images", datasetId, "count", …filter deps]` is refreshed by a delete for free — a sibling key like `["images-count", …]` would keep offering a stale total. There is no collision with the list key, whose third element is the page *number*, and `setQueryData` optimistic updates still address the full list key. Paging and sort are absent from the key on purpose: neither changes how many images match.

The same count drives the pagination row, which previously could only guess: `totalPages = Math.ceil(totalCount / pageSize)` gives *Page 3 of 25 · 1,240 images* and gates **Next** on `page < totalPages`. The old `images.length === pageSize` heuristic survives as the fallback for the first paint, so the row does not flicker in. A clamp pulls `page` back when it exceeds `totalPages` (reachable by deleting the tail of a dataset while parked on its last page), gated on `isPlaceholderData` — with `keepPreviousData` a pane that has just switched datasets briefly holds the *previous* dataset's total, and clamping against that would yank the user to an unrelated page.

### Gallery subfolder sidebar

`GalleryPage` shows a left-hand subfolder sidebar (180 px fixed) when any subfolder exists or when the create form is open. Items: "All" (no filter), "(root)" (empty-string subfolder), and one button per named subfolder with its image count. Active item is highlighted with `var(--surface-3)`.

The `(root)` row renders **whenever the sidebar does**, even at count 0: `rootEntry` falls back to a synthetic `{ path: "", image_count: 0 }` because `list_subfolders` only returns a `""` row while at least one image still lives there. Without the fallback, dragging the last root image into a subfolder would delete the only drop target for dragging images back out. Its hover-revealed move/copy buttons are gated on `image_count > 0` (they would otherwise confirm into a "Moved 0 images" toast); the row stays a drop target either way. The synthetic entry is **not** pushed into the `subfolders` array — that array gates sidebar visibility and feeds `buildSubfolderTree`, the upload `<select>`, and `SelectionToolbar`.

- **Create**: `+` icon in the sidebar header opens an inline form (input + Enter/Escape handling). If no subfolders exist yet, a `+ Subfolder` button appears in the main toolbar instead to surface the sidebar. On confirm, calls `datasetsApi.createSubfolder` → `POST /datasets/{id}/subfolders`, sets active subfolder to the new path.
- **Delete**: hover-revealed `×` button on each row opens a `ConfirmDialog`. If the subfolder has images, the dialog warns they will be moved to root (not deleted). On confirm, calls `datasetsApi.deleteSubfolder` → `DELETE /datasets/{id}/subfolders?path=...`. Its `onSuccess` runs the same path-keyed bookkeeping as a re-path (§ Renaming and moving a subfolder below), except that the folder is *gone*, so each piece is cleared rather than rewritten: `activeSubfolder` resets to "All", `expandedPaths` loses the whole subtree via `withoutSubtree`, `uploadSubfolder` falls back to `""` and `createChildOf` to `null`. The prune is not cosmetic now that the set is persisted — a dead key would be stored forever, and a folder later re-created at the same path would come back pre-expanded. The subtree is the right unit because `delete_subfolder` removes `path` **and** `path/%` plus every declared entry below it.
- **Move to dataset**: hover-revealed arrow icon button on each row opens `MoveToDatasetModal` (shared with `SelectionToolbar`). On confirm, calls `POST /images/batch/move-dataset` with `source_dataset_id + source_subfolder`. If the moved subfolder was active, resets to "All". Invalidates `["images"]` and `["subfolders"]` for both source and target datasets. It deliberately **prunes nothing** from `expandedPaths`, unlike Delete: the endpoint matches `Image.subfolder == source_subfolder` — exact, one level — and never touches `declared_subfolders`, so moving `alpha` leaves everything under `alpha/inner` where it was. Snapping that branch shut would close a folder that is still full, and the persist effect would record it as the user's choice.
- **Copy to dataset**: hover-revealed copy icon button on each row opens `MoveToDatasetModal` with `mode="copy"`. On confirm, calls `POST /images/batch/copy-dataset`. Source subfolder stays intact. Invalidates `["images"]` and `["subfolders"]` for target dataset only.
- **Upload subfolder**: a `<select>` next to the Upload button lets users target a specific subfolder for drag-drop or file-picker uploads. Defaults to the active subfolder; can be overridden independently.
- **Drop target**: every subfolder row — and the `(root)` row — is a dnd-kit droppable, so image cards can be dragged from the grid onto a row to move them into that subfolder, and a named row is also *draggable* so the tree can be re-nested by dragging one folder onto another. See `docs/dev/gallery-dnd.md` § Drag images onto subfolders and § Dragging a subfolder onto another. The "All" row is deliberately **not** a droppable (it has no target path); the sidebar *container* is a sentinel droppable so that missing a row lands on a no-op instead of a reorder.
- **Rename / Move**: see § Renaming and moving a subfolder below. Both are reached from the row's right-click menu; neither has a hover button.
- **Expand / collapse**: nested rows are drawn only when their parent's path is in `expandedPaths`, a `Set<string>` toggled by the row's leading glyph button (which doubles as the indent spacer, and carries the `Expand {path}`/`Collapse {path}` label that is the only accessible name it has). The set is **persisted** — as a `string[]` in `gallery-state-${datasetId}`, restored behind an `Array.isArray` guard — because `GalleryPage` unmounts on every trip to the image detail view, so an in-memory-only set meant the tree came back fully collapsed around a still-selected deep folder. It is deliberately *not* cleared by **Reset filters**, which removes that same blob: the open branches are the tree's shape, not a filter.
- **Selecting a folder opens it, and opens everything above it.** An effect on `activeSubfolder` adds every ancestor prefix (`ancestorPaths`, the module-level helper shared with the re-path bookkeeping below) *and the path itself*. The two halves answer different complaints, and — since the fix below — they are governed by different conditions. The **ancestors go in on every run**, because they make the row **reachable**, covering the three places a selection arrives from without knowing the tree's shape: the restored blob, the `?subfolder=` deep link applied during render, and `createSubfolderMutation`'s `setActiveSubfolder(data.path)`, which is why that mutation no longer expands anything by hand. The **path itself** makes picking a folder **show what is filed under it**, the way every other tree behaves, and goes in only when the selection actually *changed*. The effect returns the previous set unchanged when everything is already open, so it cannot re-trigger itself, and a childless path in the set is inert — both the toggle and the render bail on `hasChildren`.
- **A dep array is not what makes it fire on the change.** `useEffect` fires on **mount** whether or not its dep moved, and `GalleryPage` unmounts on every trip to the image detail view — so keying on `activeSubfolder` alone re-added the selected path on the way back, re-opening a folder the user had deliberately collapsed while standing in it. The persisted blob had the collapse recorded correctly; the effect overrode it. The `seenSubfolder` ref is what distinguishes a real change from a re-mount, and three things about it are load-bearing:
  - **It remembers the last value, not a `useRef(true)` first-run flag.** `main.tsx` enables `StrictMode`, which double-invokes mount effects in dev: a flag flipped by run #1 makes run #2 look like a change and the bug returns — and *only* under `npm run dev`, since `e2e/serve.sh` serves the production bundle where StrictMode is inert, so no e2e test can catch that regression. Remembering the value makes both runs decide the same thing.
  - **Its seed distinguishes a restore from an arrival.** It is seeded from `saved?.activeSubfolder` normally, but `undefined` when `linkedSubfolder !== undefined` — a deep link is an arrival and must open the folder it names, even though that is a mount. The test is `!== undefined` and never truthiness: `usePaneGallerySubfolder` returns `undefined` for "no link asked for anything" and `""` for a real link to the dataset root. The render-phase apply block runs before the commit, so the effect already sees the linked value on that first run.
  - **The ref is written before the `!activeSubfolder` bail**, so a cleared selection (arriving with `?source_video_id=`) does not leave a stale value behind that would make the user's next click on that same folder look like a no-op.
  - The consequence users see: collapse `alpha` while standing in `alpha/inner`, leave and come back, and `alpha` is open again (the active row has to be reachable) while `alpha/inner` is still closed. Guarded from both directions by `gallery-restore.spec.ts` — *a restored subfolder selection opens the branch containing it* for the unconditional half, *collapsing the folder you are standing in survives a round trip* and *a subfolder deep link opens the folder it names* for the conditional one.
- **Re-clicking the row you are already standing in expands it too**, from the label button's `onClick` rather than the effect: `setActiveSubfolder` with the unchanged value is a React bail-out, so no effect fires, and a collapsed active folder would otherwise stay shut with no way in but the ▶ toggle. Reachable across a reload now that the collapse survives one.
- **Query key**: `["subfolders", datasetId]` — invalidated after upload, batch delete, batch move, create, delete, and re-path.
- **CSS**: `.subfolder-row .subfolder-delete-btn`, `.subfolder-row .subfolder-move-btn`, and `.subfolder-row .subfolder-copy-btn` are `opacity: 0`; hover on the row reveals them. Defined in `frontend/src/index.css`. Move and copy buttons share base layout via `.subfolder-action-btn`; each has its own hover color (accent for move, info for copy). Delete uses inline styles (pre-existing pattern).

### The subfolder row context menu

Right-clicking a **named** subfolder row opens the shared `ContextMenu`
(`components/common/ContextMenu.tsx` — see `docs/dev/styling.md` § Context menu). State
is `folderMenu: {x, y, node}`, and the menu renders with the other overlays after
`</DndContext>` rather than inside `renderSubfolderNode`, whose node can collapse out
from under it.

Six entries, most-used first and destructive last: *New subfolder inside…*, **Rename…**,
**Move to…**, *Move to another dataset…*, *Copy to another dataset…*, *Delete*. Four of
them call the **same setters the hover buttons already call** — the menu is additive and
nothing was relocated into it. Rename and Move are the two with no button counterpart,
because a 180 px row already carries four; that is a deliberate stopping point, not an
unfinished set.

**Neither the `(root)` row nor "All" gets a menu.** Root has no path to rename, its entry
is synthetic when empty, and its two actions are already one pixel away as buttons; "All"
is the no-filter state rather than a folder, which is the same reason it is deliberately
not a droppable.

Right-press and drag coexist without special handling: `@dnd-kit/core`'s
`PointerSensor.activators` bails on `event.button !== 0`, so a right-press never starts a
drag.

### Renaming and moving a subfolder

Gallery subfolders are **virtual** — `Image.subfolder` is a `String(512)` column, every
image lives flat in `{dataset}/images/`, and empty folders are remembered in
`Dataset.declared_subfolders`. Nothing exists on disk. So renaming `a/b` → `a/c` and
re-nesting `a` under `b` (→ `b/a`) are the same subtree prefix rewrite, and one endpoint
serves both. This is unrelated to `routers/filesystem.py`, which manipulates real
directories.

**`PATCH /datasets/{id}/subfolders`** takes `{path, new_path}` and returns
`{path, previous_path, images_updated}`. `previous_path` exists for the frontend's
re-pointing bookkeeping below. Guards, in this order — normalization first so a `..`
never reaches a comparison, existence and collision last because they cost a query:

| Condition | Code | Message |
|---|---|---|
| dataset missing | 404 | `Dataset not found` |
| `ensure_not_busy(dataset_id)` | 409 | from the guard |
| `..` in either field | 400 | from `normalize_subfolder` |
| `src == ""` | 400 | `Subfolder path must not be empty` |
| `dst == ""` | 400 | `New subfolder path must not be empty` |
| `dst == src` | 400 | `New path is the same as the current path` |
| `dst.startswith(src + "/")` | 400 | `Cannot move a subfolder into itself` |
| `src not in existing` | 404 | `Subfolder not found: {src}` |
| `dst in existing` | 409 | `A subfolder named "{dst}" already exists` |

`new_path` is a **whole path**, so moving to the top level is `new_path = "<basename>"`,
never `""` — `""` is the root pseudo-folder, which holds images but cannot itself be
renamed. That is the obvious wrong reading, hence the docstring. The last two guards
share one `list_subfolders` call, which is already the merged image-derived + declared
set, and both stay in the **router** rather than the service (no HTTP in services).
`ensure_not_busy` is justified concretely: `VersionImageState.subfolder` means a snapshot
restore rewrites this exact column. `create_subfolder` and `remove_subfolder` still do
not guard — left alone rather than drive-by edited.

`dataset_service.repath_subfolder` does two UPDATEs — an exact match and a
`LIKE prefix + "/%"` for the descendants, splicing the tail on with
`literal(new_path) + func.substr(Image.subfolder, len(path) + 1)` (`substr` is 1-based, so
`len(path)+1` keeps the leading `/`). The LIKE escaping is copied verbatim from
`delete_subfolder` and **both halves matter**: `_` is a LIKE wildcard *and* an ordinary
character, so unescaped, re-pathing `a_b` would rewrite every image in `axb`. It then
rebuilds `declared_subfolders` — re-pathing every entry in the subtree, appending the
destination's ancestors (mirroring `declare_subfolder`, or an empty folder moved under a
merely-declared folder vanishes from the sidebar), dedupe preserving order — and
**reassigns the list**, because SQLAlchemy compares JSON columns by equality and an
in-place edit is a silently skipped UPDATE whose failure is narrow: images re-path
correctly and only empty declared folders are lost.

**A re-path is a label change and renames no files.** An image auto-named from the old
folder (`characters_001.png`) keeps that stem after `characters` → `people`; the remedies
are the existing **Renumber Files** button and move-to-subfolder-with-rename. Contrast
`POST /images/batch/move-subfolder`'s `rename_on_move`. Renaming files here would turn one
atomic transaction into a fallible filesystem batch, pulling in PM-013, PM-021 (`images.py`
is wholly unconverted) and the stem-keyed thumbnail collision rule. There is no
`record_in_place`, no thumbnail work and no filesystem mutation at all.

`backend/tests/test_subfolder_repath_http.py` is the request-level coverage: the subtree
rename with an explicit *filenames are unchanged* assertion, both move directions, the
`declared_subfolders` rewrite read back through the session (the branch nothing else
covers), the LIKE escaping, every guard row, and the busy 409.

**One frontend mutation serves all three gestures** — `repathSubfolderMutation`, taking
`{path, newPath, kind: "rename" | "move"}` and used by the inline rename, the picker modal
and the drag. It is kept **separate** from `moveToSubfolderMutation`, whose `clear()`
touches the module-global selection store and must never fire for a folder operation. Its
`onSuccess` runs the path-keyed bookkeeping, with `from = data.previous_path`,
`to = data.path` and the subtree predicate `isInSubtree(p, from)` — one of two module-level
helpers this shares with `deleteSubfolderMutation`, alongside `withoutSubtree(set, root)`,
which returns the **same** set when nothing matched so a prune never forces a render or a
needless write to `gallery-state-${datasetId}`. `isInSubtree("", root)` is false for any
named root, which is what keeps the dataset root out of every one of these rewrites:

- Invalidates `["subfolders", datasetId]` and `["images", datasetId]` — the count key nests
  under that prefix, so pagination refreshes for free.
- **`activeSubfolder` is re-pointed, not cleared.** Delete clears because the folder is
  gone; here it still exists. And there is **no `resetPage()`** — the image set is
  identical, only its label changed.
- **`expandedPaths` must be re-pointed**, and this is the one that gets missed: it is a
  `Set<string>` keyed by path, so a re-path orphans every key and silently collapses the
  branch the user was working in. It is rebuilt through the same map *and* gains the
  destination's ancestors (via `ancestorPaths`), which is what makes a folder dropped into
  a collapsed parent visible where it landed. The `activeSubfolder` effect does not cover
  this: a re-path can move a folder the user is not standing in.
- **`uploadSubfolder`** — the `activeSubfolder` effect covers the common case, but the user
  can override the upload target independently, so it is re-pointed too or the `<select>`
  renders blank.
- **`createChildOf`** is cleared if it pointed into the subtree.
- Persistence needs nothing: `activeSubfolder` and `expandedPaths` both feed the debounced
  persist effect and `liveStateRef`, so `gallery-state-${datasetId}` follows.
- The toast is driven off `kind`, not re-derived: *Renamed to "{to}"* vs
  *Moved "{label}" into "{parent || "(root)"}"*.

**Rename is an inline input, not a modal** — two precedents (the inline child-create form
in this same sidebar, `FileBrowserPage`'s `RenameInput`), and an in-row input keeps the
tree indentation visible so it is obvious which folder is being renamed. When
`renamingPath === node.path` the row renders the indent button, then the input at
`flex: 1`, and **hides the four hover buttons** (`×` sits one pixel from Enter). It is
**single-segment by construction**: the draft seeds from `node.label` and separators are
stripped on input (`.replace(/[\\/]/g, "")`) rather than rejected on submit, so typing
`a/b` yields `ab` and the control structurally cannot *move* a folder. Enter composes
`parent ? parent + "/" + name : name`; empty, unchanged, `.` and `..` are ignored, which
makes the endpoint's 400 branches unreachable from the UI. Escape cancels and
`stopPropagation()`s (GalleryPage has a document-level Escape handler); blur cancels rather
than commits; there are no Cancel/Confirm buttons. `FileBrowserPage`'s `RenameInput` is
**not** reused — the two contracts genuinely diverge on separator stripping, Escape
propagation and blur-cancel.

**Move to… is `components/gallery/MoveSubfolderModal.tsx`**, props
`{node, subfolders, isPending, onConfirm(newPath), onClose}`. It adopts `useModalBehavior`
(no backdrop close) — it is a newly extracted component, so `docs/dev/styling.md`'s
long-lived-parent blocker does not apply. It renders a **vertical list, not chips**, so it
is immune by construction to the overflow the toolbar's chip row had: `(root) — top level`
first, then every folder passing `canDropFolderOn`, indented by `depth * 10` px, each row
ellipsised. The folder's current parent renders **disabled** with `(current location)`
rather than hidden. A footer preview line (`Result: characters/poses/hero`) is the point of
the modal — this re-paths a whole subtree — and **Move** is disabled when nothing is
selected, while pending, or when the composed path already exists (a client-side echo of
the 409). `MoveToDatasetModal` is not reused: it is about *datasets*, carries copy-mode and
cross-dataset provenance, and has four call sites.

The drag half is in `docs/dev/gallery-dnd.md` § Dragging a subfolder onto another.

### Gallery filters

`GalleryPage` supports the following filter controls. What each one sends is one row of the
shared `GET /images/` param contract in `docs/dev/image-filters.md`; this section is the UI
side only.

- **Search bar** — debounced 350 ms; passes `search` param to `GET /images/`; filters by filename OR caption text (case-insensitive).
- **Caption filter** — All / Captioned / Uncaptioned.
- **Quality flag** — dropdown labelled *All quality*, then seven *Flagged: …* entries — blurry (`is_blurry`), noisy (`is_noisy`), near-uniform (`is_uniform`), watermark (`has_watermark`), duplicate (`is_duplicate`), NSFW (`is_nsfw`), AI artifacts (`has_ai_artifacts`). All values map directly to the `quality_flag` param, which validates them against `utils.ALLOWED_FLAG_KEYS`.
- **Score filters** — multi-chip system: each active filter is a `{field, min?, max?}` chip with a × remove button. An "Add score filter" form lets the user pick any of the 9 score fields and enter optional min/max bounds. Multiple chips are combined as AND conditions via the JSON-encoded `score_filters` param. The older single `score_field`/`min_score`/`max_score` params are not used by GalleryPage (retained only for StatsPage BucketPanel backward compat).
- **Detection label** — text input with icon prefix, debounced 350 ms; passes `detection_label` to `GET /images/`; uses a correlated `EXISTS` subquery against the `detections` table matching `label ILIKE '%...%'`; has a clear (×) button when set.
- **Subfolder filter** — see Gallery subfolder sidebar section above; passes `subfolder` query param to `GET /images/`.
- **License filter** — single-select dropdown over the curated vocabulary, a "Missing license only" entry, and a "Used in this dataset" optgroup of the free-text `other:` licenses actually recorded there (`hooks/useCustomLicenses`). `""` means no filter; the `MISSING_LICENSE` sentinel (`"__missing__"`, in `constants/galleryOptions.ts`) sends `license_missing=true`; anything else is sent as a one-element `license_filter` JSON array. A restored `other:` filter that is no longer in use keeps its own option, or the `<select>` would render no option for the filter it is applying. See `docs/dev/provenance.md` for the effective-license semantics and for why `""` behaves differently here than in the export filters.
- **Frames-from filter** — a "Frames from *filename*" dropdown, rendered **only when the dataset has videos** (the same "look untouched for image-only datasets" rule `VideoStrip` follows), sending `source_video_id` to `GET /images/`. It answers a question the subfolder sidebar cannot: curation moves, renames and re-files frames, at which point the extraction subfolder stops being a handle, while `Image.source_video_id` does not move. State lives in `frameVideoId`, is persisted in `gallery-state-${datasetId}` alongside `licenseFilter`, is cleared by **Reset filters**, is cleared by an incoming `?subfolder=` deep link (which would otherwise intersect it and show an empty grid — see `docs/dev/video-ui.md` § Frame lineage and the gallery deep link), and self-clears once `["videos", datasetId]` resolves without a match (a deleted video would otherwise leave a permanently empty grid behind a blank `<select>` — the same guard `licenseFilter`'s vocabulary bounds-check provides). Deep-linked from the video detail page and the image detail lineage row; see `docs/dev/video-ui.md` § Frame lineage and the gallery deep link.
- **Caption token / word filters** (`caption_tokens_min`/`caption_tokens_max`, `caption_words_min`/`caption_words_max`) — used by StatsPage's caption-length/token histograms (`BucketPanel`) to drill into a bucket. Semantics are min-inclusive, max-exclusive; an empty caption counts as 0. The token filter is **pure SQL** over the persisted `func.coalesce(Image.caption_token_count, 0)` column (kept in sync by the `caption_text` listener — see `docs/dev/shared-utilities.md`), so normal `ORDER BY`/`OFFSET`/`LIMIT` paging applies. (It previously fetched a capped 5,000-row set and re-tokenized in a thread; that cap and the in-Python BPE pass are gone.)

### Sorting

The dropdown is driven by `SORT_OPTIONS` in `frontend/src/constants/galleryOptions.ts`, each entry a `{label, sort, order}` triple. **Append only, never insert**: `sortIdx` is persisted as an *index* into that array in `gallery-state-${datasetId}`, and `SettingsPage`'s default-sort picker maps by index too, so a mid-array insert silently changes the sort of every saved gallery and everyone's default.

Two of the entries are frame lineage — **"Video timeline"** (`source_timestamp_ms`) and **"Shot order"** (`source_shot_index`), both ascending only. They are the natural order for reviewing a triage extraction pass, which the subfolder view cannot give: curation moves and renames frames, and neither disturbs the lineage columns. Descending is not offered because nobody reviews a pass backwards, and each entry costs a persisted index forever.

Server side, `GET /images/`'s ordering block reaches `Image` by `getattr`, so it is gated on the `_ALLOWED_SORT_FIELDS` frozenset in `backend/routers/images.py`. An unknown name is **coerced to `created_at`, never rejected** — unknown names already fell back before, and a stale persisted `sortIdx` has to degrade to a working gallery rather than a 400. What the allowlist actually closes is a 500: `Image` carries attributes that are not columns, so `?sort=metadata`, `?sort=dataset` (the relationship) and `?sort=has_dino_layer_embeddings` (the property) each returned something SQLAlchemy cannot order by and raised server-side; the old `getattr` default only ever caught names that do not exist at all.

The set is `_ALLOWED_SCORE_FIELDS` **plus `nsfw_score`**, plus the ordinary metadata columns. The two differ deliberately: `_ALLOWED_SCORE_FIELDS` also drives the score *filters*, the Stats histograms and `score_filters`, all of which exclude NSFW on purpose — but sorting is a separate question, and inheriting the filter set silently removed an ordering that worked before the allowlist existed. So a new score column goes in the sort allowlist regardless of whether it is filterable, and `test_every_score_column_is_sortable` checks exactly that against `utils.score_columns(Image)`.

Both lineage columns are in `_NULLS_LAST_SORT_FIELDS`, which gives them `.nulls_last()` plus a `created_at ASC` tiebreak. Both halves are load-bearing: NULL means "not a video frame", which is most of a typical dataset, so a plain ASC would float every ordinary upload above every frame; and two frames cut from one held shot share a timestamp, so without the tiebreak their order is SQLite's scan order and the grid reshuffles between identical requests. `Index("ix_images_source_video_timeline", "source_video_id", "source_timestamp_ms")` (migration `c8a1d3f5b7e2`) covers both the dominant filter-then-order shape (the frames-from filter plus a timeline sort) and the unfiltered whole-dataset sort. It makes `ix_images_source_video_id` a redundant prefix; that one stays because dropping it touches the delete-video NULLing UPDATE and `frames-summary`.

`sort_order` is **not** in that frozenset, despite wanting the same treatment: its own `if sort == "sort_order"` branch matches first and applies nulls-last plus the tiebreak itself, so an entry there could never be reached. That branch also ignores `order` on purpose — "custom order, descending" is not something the drag-and-drop grid can mean.

`backend/tests/test_image_sort_fields.py` holds the allowlist, the coercion, the 500 regression, both orderings, the nulls-last-in-both-directions rule, the tiebreak's stability, and a structural check that every `sort` value the UI offers is on the allowlist — a name the dropdown offers that the backend coerces away is a menu entry that does nothing.
