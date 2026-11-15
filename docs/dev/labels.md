# Labels — the vocabulary, where it shows up and its UI

Labels are a small, global vocabulary of short strings attached to images, a second facet of
organisation beside the subfolder tree; why they are not tags is `docs/dev/labels/backend.md`.
This file is the family's entry point: the tables below route from a code path to whichever
file in `docs/dev/labels/` owns it. The `GET /images/` param contract the label filter joins is
`docs/dev/image-filters.md`, the export filter table `docs/dev/export.md`, the snapshot passes
the mirror rides `docs/dev/versioning/mirrors.md`, the detail page's labels panel
`docs/dev/image-detail.md`, and `LabelPicker` `docs/dev/menus-popovers.md`.

## The family

| File | Covers |
|---|---|
| [backend.md](labels/backend.md) | The vocabulary: the naming rule, the `Label`/`image_labels` tables and their migrations, `label_service.py`, the `/labels` router, `POST /labels/assign`'s chunking, race-safe 409 and busy-dataset guard |
| [consumers.md](labels/consumers.md) | Where an assignment shows up: the EXISTS-only filter block, export narrowing and `labelParams()`'s falsy-omit encoding, the `VersionImageState.label_ids` mirror and restore's Pass 2c, copies, duplicates, moves and derivatives |
| [ui.md](labels/ui.md) | The Settings Labels tab, the detail page's panel and hotkeys, `LabelsBulkModal`: the `["labels"]` query and `invalidateLabelScope`, the persisted filter's bounds check, the six `GalleryPage` persistence edits, the per-image assign queue |

## Where things live

Each row names the file that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the code it owns.

| Editing… | Read |
|---|---|
| `backend/models/label.py`, `backend/services/label_service.py`, `backend/routers/labels.py` | `docs/dev/labels/backend.md` |
| `backend/routers/images.py` (`_apply_image_filters`' label block) | `docs/dev/labels/consumers.md` § Filters |
| `backend/routers/export.py` (the three label params), `frontend/src/pages/ExportPage.tsx` (`labelParams`) | `docs/dev/labels/consumers.md` § Export; + `docs/dev/export.md` for the rest of both |
| `backend/models/versioning.py` (`VersionImageState.label_ids`) | `docs/dev/labels/consumers.md` § The mirror column |
| `backend/services/version_service.py` (its label passes), `backend/routers/images.py`'s copy and move, `dataset_service.py`'s duplicate, the derivative sites | `docs/dev/labels/consumers.md` § Versioning and cross-dataset hooks; + `docs/dev/versioning.md` for the rest of `version_service.py` |
| `frontend/src/hooks/useLabels.ts`, `frontend/src/api/labels.ts` | `docs/dev/labels/ui.md` § Files and the query key |
| `frontend/src/components/settings/LabelsPanel.tsx`, `frontend/src/components/gallery/LabelsBulkModal.tsx` | `docs/dev/labels/ui.md` |
| `frontend/src/components/image/LabelsPanel.tsx`, `ImageDetailPage.tsx`'s label hotkeys | `docs/dev/labels/ui.md` § Hotkeys; + `docs/dev/image-detail.md` for where the panel mounts |
| `frontend/src/pages/GalleryPage.tsx` (the label filter) | `docs/dev/labels/ui.md` § GalleryPage persistence: the six edits; + `docs/dev/gallery/filters.md` for the control |
| `frontend/src/components/common/LabelPicker.tsx` | `docs/dev/menus-popovers.md` |
