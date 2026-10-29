# Gallery — selection, subfolders, filters and navigation

`GalleryPage` is where a dataset is read and curated: selecting images, organising them into
virtual subfolders, filtering and sorting the grid, dragging cards and folders around, and the
state the page keeps between visits and hands to the detail view's arrows. This file is the
family's entry point: the tables below route from a code path to whichever file in
`docs/dev/gallery/` owns it. The detail view itself is `docs/dev/image-detail.md`; file naming,
imports and rescan are `docs/dev/image-files.md`; the `GET /images/` param contract every
filter sends is `docs/dev/image-filters.md`.

## The family

| File | Covers |
|---|---|
| [selection.md](gallery/selection.md) | `ImageCard`'s `onSelect`, `GalleryCheckbox` and the overlay z-order, the shift-click range model, select-all-matching-filters, the count key and pagination |
| [subfolders.md](gallery/subfolders.md) | The subfolder sidebar: the `(root)` row, create/delete/move/copy, the upload target, expand/collapse and its persistence, the row context menu |
| [subfolder-rename.md](gallery/subfolder-rename.md) | `PATCH /datasets/{id}/subfolders`, `repath_subfolder`, `repathSubfolderMutation`'s path-keyed bookkeeping, the inline rename, `MoveSubfolderModal` |
| [filters.md](gallery/filters.md) | The toolbar's two flex groups, every filter control and what it persists, `SORT_OPTIONS` and the server's sort allowlist |
| [drag-drop.md](gallery/drag-drop.md) | The `DndContext`, droppable-id namespaces and collision detection, dragging cards and folders onto subfolder rows, manual `sort_order` ordering |
| [state.md](gallery/state.md) | The `gallery-state-*` blob, the debounce rules (PM-012), the save effect and unmount flush, **Reset filters** |
| [nav.md](gallery/nav.md) | The `gallery-nav-*` context the detail view's arrows walk, `injectNavId`, `atEnd`, the live refresh and live total |

## Where things live

Each row names the file that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the code it owns.

### Backend

| Editing… | Read |
|---|---|
| `backend/routers/datasets.py` (the `/{dataset_id}/subfolders` routes) | `docs/dev/gallery/subfolder-rename.md` for the `PATCH` and its guard table; + `docs/dev/gallery/subfolders.md` for the create and delete the sidebar calls |
| `backend/services/dataset_service.py` (its subfolder half) | `docs/dev/gallery/subfolder-rename.md` — `repath_subfolder`, the LIKE escaping it duplicates from `delete_subfolder`, the `declared_subfolders` rebuild; + `docs/dev/gallery/subfolders.md` for `list_subfolders`' root row and `delete_subfolder`'s subtree |
| `backend/routers/images.py` | `docs/dev/gallery/filters.md` § Sorting for the ordering block and `_ALLOWED_SORT_FIELDS`; + `docs/dev/gallery/drag-drop.md` for `PATCH /images/batch/reorder`; + `docs/dev/image-filters.md` for the filter params |
| `backend/tests/test_subfolder_repath_http.py` | `docs/dev/gallery/subfolder-rename.md` |
| `backend/tests/test_image_sort_fields.py` | `docs/dev/gallery/filters.md` § Sorting |

### Frontend

| Editing… | Read |
|---|---|
| `frontend/src/pages/GalleryPage.tsx` | `docs/dev/gallery/selection.md` for `handleSelect`, select-all and the pagination row; + `docs/dev/gallery/subfolders.md` for the sidebar, `expandedPaths` and the row menu; + `docs/dev/gallery/subfolder-rename.md` for `repathSubfolderMutation` and the inline rename; + `docs/dev/gallery/filters.md` for the toolbar, the filter state and the sort dropdown; + `docs/dev/gallery/drag-drop.md` for the `DndContext` and every drop; + `docs/dev/gallery/state.md` for the persisted blob and **Reset filters**; + `docs/dev/gallery/nav.md` § The stored context for the `writeNavContext` effect, whose memo a new filter must join |
| `frontend/src/components/gallery/ImageCard.tsx` | `docs/dev/gallery/selection.md` for `onSelect`, the checkbox click target and the overlay z-order; + `docs/dev/quality/style-match.md` for the style meter |
| `frontend/src/components/gallery/GalleryCheckbox.tsx` | `docs/dev/gallery/selection.md` |
| `frontend/src/components/gallery/MoveSubfolderModal.tsx` | `docs/dev/gallery/subfolder-rename.md` |
| `frontend/src/components/gallery/SubfolderRowDnd.tsx`, `frontend/src/components/gallery/DropZone.tsx` | `docs/dev/gallery/drag-drop.md` |
| `frontend/src/components/gallery/SelectionToolbar.tsx` | outside this family: `docs/dev/frontend-core.md` § Frontend state |
| `frontend/src/constants/galleryOptions.ts` | `docs/dev/gallery/filters.md` for `SORT_OPTIONS` (append only) and `MISSING_LICENSE`; + `docs/dev/gallery/drag-drop.md` for the droppable and draggable id helpers |
| `frontend/src/store/selectionStore.ts` | `docs/dev/frontend-core.md` § Frontend state; + `docs/dev/gallery/selection.md` for the additive bulk-select rule and `clearDataset` |
| `frontend/src/hooks/usePaneDatasetId.ts` (`usePaneGallerySubfolder`) | `docs/dev/panes-routing.md`; + `docs/dev/gallery/subfolders.md` for why a deep link seeds `seenSubfolder` as an arrival |
| `frontend/src/utils/galleryNav.ts`, `frontend/src/pages/ImageDetailPage.tsx` (the arrows) | `docs/dev/gallery/nav.md` |
| `frontend/e2e/gallery-select-all.spec.ts` | `docs/dev/gallery/selection.md`; + `docs/dev/frontend-core.md` § Frontend state for the sticky `SelectionToolbar` regression |
| `frontend/e2e/gallery-subfolders.spec.ts` | `docs/dev/gallery/subfolder-rename.md`; + `docs/dev/gallery/drag-drop.md` for why the drag is verified by hand |
| `frontend/e2e/gallery-restore.spec.ts` | `docs/dev/gallery/state.md`; + `docs/dev/gallery/subfolders.md` for the expand/collapse round trips |
| `frontend/e2e/gallery-nav-filters.spec.ts` | `docs/dev/gallery/nav.md` |
