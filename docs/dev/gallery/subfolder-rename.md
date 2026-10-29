# Renaming and moving a gallery subfolder

This file covers re-pathing a virtual subfolder: `PATCH /datasets/{id}/subfolders` and its guards, `dataset_service.repath_subfolder`, why a re-path renames no files, and the client side — `repathSubfolderMutation` and the path-keyed state it re-points, the inline rename input and `MoveSubfolderModal`. The sidebar and context menu that reach it are `docs/dev/gallery/subfolders.md`. Part of the Gallery family; the hub, `docs/dev/gallery.md`, routes from a code path to the file that owns it.

## Renaming and moving a subfolder

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
atomic transaction into a fallible filesystem batch, pulling in PM-013, PM-021 (`images.py`'s
renames are unconverted) and the stem-keyed thumbnail collision rule. There is no
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
  a collapsed parent visible where it landed. The `activeSubfolder` effect (`docs/dev/gallery/subfolders.md`) does not cover
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
in the sidebar, `docs/dev/gallery/subfolders.md`; `FileBrowserPage`'s `RenameInput`), and an in-row input keeps the
tree indentation visible so it is obvious which folder is being renamed. When
`renamingPath === node.path` the row renders the indent button, then the input at
`flex: 1`, and **hides the four hover buttons** (`×` sits one pixel from Enter). It is
**single-segment by construction**: the draft seeds from `node.label` and separators are
stripped on input (`.replace(/[\\/]/g, "")`) rather than rejected on submit, so typing
`a/b` yields `ab` and the control structurally cannot *move* a folder. Enter composes
`parent ? parent + "/" + name : name`; empty, unchanged, `.` and `..` are ignored, which
makes the endpoint's 400 branches unreachable from the UI. Escape cancels and
`stopPropagation()`s (defensive: GalleryPage has no Escape handler of its own); blur cancels rather
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

The drag half is in `docs/dev/gallery/drag-drop.md` § Dragging a subfolder onto another.
