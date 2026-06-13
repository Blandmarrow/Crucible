# Gallery, image naming & generation metadata

This file covers image file naming/renaming/collision handling, gallery selection, filters, the subfolder sidebar, manual drag ordering, navigation state in the detail view, and generation-metadata extraction/display.

### Image file naming

Images receive human-readable names derived from their original filename via `slugify_filename`. Collision handling is done by `unique_filename_with_thumb` (counter suffix `_001`, `_002`, …) in every path that creates or renames an image file with an associated thumbnail — upload, bulk-rename, rename-image, crop new-file, batch-move-subfolder, batch-move-dataset, batch-copy-dataset, rename-on-caption, upscale non-replace, and LUT non-replace. Use `unique_filename_with_thumb` for any new image-creation path; `unique_filename` alone is only appropriate where no thumbnail is involved (e.g. import before thumbnail generation).

`Image.is_auto_named: bool` (default `False`) — set to `True` when a file is renamed by the captioning job or by a subfolder move. Used to distinguish auto-named files from manually named ones. `PATCH /images/{image_id}/rename` sets it back to `False`.

**Subfolder-based naming**: when `rename_on_caption=True` or when images are moved between subfolders (`POST /images/batch/move-subfolder`) or between datasets (`POST /images/batch/move-dataset` / `POST /images/batch/copy-dataset`), filenames are derived from the target subfolder slug (e.g. images in `"animals"` become `animals.jpg`, `animals_001.jpg`, …; images in root become `image.jpg`, `image_001.jpg`, …). Cross-dataset moves and copies always rename. For same-dataset subfolder moves, renaming is conditional: `BatchMoveSubfolderRequest.rename_on_move: bool = True` — when `False`, only the `subfolder` metadata column is updated and filenames are preserved (the user's preference is stored in `localStorage` under `SUBFOLDER_RENAME_KEY` and read by `SelectionToolbar` at mutation time).

**Cross-dataset moves** (`POST /images/batch/move-dataset`): accepts either `image_ids` (explicit list) or `source_dataset_id + source_subfolder` (moves the whole subfolder). Files are renamed to the target subfolder slug, moved to `{target_dataset.folder_path}/images/`, thumbnails are copied then the originals removed. Calls `refresh_stats` on both source and target after the filesystem operations. **DB is committed before the filesystem operations** so the DB is always the authoritative record of where files should live; if a rename fails mid-batch, already-moved files remain accessible via their new DB paths while not-yet-moved files are temporarily unreachable.

**Cross-dataset copies** (`POST /images/batch/copy-dataset`): same request schema as move. Source images and files remain untouched; new `Image` records are inserted in the target dataset with all metadata copied (scores, captions, flags, generation metadata — deferred blob embeddings are not copied). Uses `copy_with_sidecar` for the image file and `shutil.copy2` for the thumbnail. Calls `refresh_stats` on the target only. **DB inserts are staged before file copies** so that a filesystem failure prevents commit and leaves no orphaned DB records (opposite ordering to move — here an incomplete copy should leave nothing rather than partial records).

Frontend entry points for both: `SelectionToolbar` ("Move to Dataset" / "Copy to Dataset" buttons) and `GalleryPage` subfolder sidebar (arrow icon for move, copy icon for copy). Shared modal: `MoveToDatasetModal` (`frontend/src/components/common/MoveToDatasetModal.tsx`) — accepts an optional `mode?: "move" | "copy"` prop (default `"move"`) which changes the icon, title, and confirm button label. Callback prop is `onConfirm(targetDatasetId, subfolder)`.

### Gallery image selection

`ImageCard` accepts an optional `onSelect?: (id: string, shiftKey: boolean, isCheckbox: boolean) => void` prop. When provided it routes both checkbox clicks and shift-clicks on the card body through this callback instead of calling `toggle` directly. `GalleryPage` always provides `handleSelect` as `onSelect`; contexts that render `ImageCard` without it (e.g. `BucketPanel`) fall back to the raw `toggle`.

**`handleSelect` in GalleryPage** — two `useRef`s drive range tracking:

- `lastSelectedId` — the selection anchor; set on every plain (non-shift) toggle, and on shift+checkbox when no valid range could be computed (stale anchor). Never moved by a shift-only interaction.
- `lastRangeEndId` — the endpoint of the last contiguous range; set each time a checkbox shift-click successfully applies a range, reset to `null` on any plain toggle.

| Interaction | Effect |
|---|---|
| Click checkbox | Toggle image; anchor = clicked image |
| Shift+click checkbox | Range from anchor → clicked; images that fell out of the previous range are deselected via `replaceRange`; anchor unchanged |
| Shift+click card body | Toggle image only; anchor and range-end unchanged |

The `onMouseDown` handler on the card's outer `<div>` calls `e.preventDefault()` when `Shift` is held to suppress browser text-selection. This does not interfere with dnd-kit's `PointerSensor` (which listens to `pointerdown`, not `mousedown`).

### Gallery subfolder sidebar

`GalleryPage` shows a left-hand subfolder sidebar (180 px fixed) when any subfolder exists or when the create form is open. Items: "All" (no filter), "(root)" (empty-string subfolder, only shown if images exist there), and one button per named subfolder with its image count. Active item is highlighted with `var(--surface-3)`.

- **Create**: `+` icon in the sidebar header opens an inline form (input + Enter/Escape handling). If no subfolders exist yet, a `+ Subfolder` button appears in the main toolbar instead to surface the sidebar. On confirm, calls `datasetsApi.createSubfolder` → `POST /datasets/{id}/subfolders`, sets active subfolder to the new path.
- **Delete**: hover-revealed `×` button on each row opens a `ConfirmDialog`. If the subfolder has images, the dialog warns they will be moved to root (not deleted). On confirm, calls `datasetsApi.deleteSubfolder` → `DELETE /datasets/{id}/subfolders?path=...`. If the deleted subfolder was active, resets to "All".
- **Move to dataset**: hover-revealed arrow icon button on each row opens `MoveToDatasetModal` (shared with `SelectionToolbar`). On confirm, calls `POST /images/batch/move-dataset` with `source_dataset_id + source_subfolder`. If the moved subfolder was active, resets to "All". Invalidates `["images"]` and `["subfolders"]` for both source and target datasets.
- **Copy to dataset**: hover-revealed copy icon button on each row opens `MoveToDatasetModal` with `mode="copy"`. On confirm, calls `POST /images/batch/copy-dataset`. Source subfolder stays intact. Invalidates `["images"]` and `["subfolders"]` for target dataset only.
- **Upload subfolder**: a `<select>` next to the Upload button lets users target a specific subfolder for drag-drop or file-picker uploads. Defaults to the active subfolder; can be overridden independently.
- **Query key**: `["subfolders", datasetId]` — invalidated after upload, batch delete, batch move, create, and delete.
- **CSS**: `.subfolder-row .subfolder-delete-btn`, `.subfolder-row .subfolder-move-btn`, and `.subfolder-row .subfolder-copy-btn` are `opacity: 0`; hover on the row reveals them. Defined in `frontend/src/index.css`. Move and copy buttons share base layout via `.subfolder-action-btn`; each has its own hover color (accent for move, info for copy). Delete uses inline styles (pre-existing pattern).

### Gallery filters

`GalleryPage` supports the following filter controls:

- **Search bar** — debounced 350 ms; passes `search` param to `GET /images/`; filters by filename OR caption text (case-insensitive).
- **Caption filter** — All / Captioned / Uncaptioned.
- **Quality flag** — dropdown with options: None, Blurry (`is_blurry`), Noisy (`is_noisy`), Near-uniform (`is_uniform`), Watermarked (`has_watermark`), Duplicate (`is_duplicate`). All values map directly to `quality_flag` param.
- **Score filters** — multi-chip system: each active filter is a `{field, min?, max?}` chip with a × remove button. An "Add score filter" form lets the user pick any of the 8 score fields and enter optional min/max bounds. Multiple chips are combined as AND conditions via the JSON-encoded `score_filters` param. The older single `score_field`/`min_score`/`max_score` params are not used by GalleryPage (retained only for StatsPage BucketPanel backward compat).
- **Detection label** — text input with icon prefix, debounced 350 ms; passes `detection_label` to `GET /images/`; uses a correlated `EXISTS` subquery against the `detections` table matching `label ILIKE '%...%'`; has a clear (×) button when set.
- **Subfolder filter** — see Gallery subfolder sidebar section above; passes `subfolder` query param to `GET /images/`.

### Manual image ordering

`Image.sort_order: int | None` (nullable, default `NULL`) — stores the custom display position of an image within its `(dataset_id, subfolder)` scope. `NULL` means no custom order has been assigned; such images sort last (`NULLS LAST`) with `created_at ASC` as a tiebreak.

**Activation**: the gallery sort dropdown includes a **"Custom order"** option (`sort=sort_order`). When it is selected for the first time and no image in the current page has `sort_order` set, the frontend silently initialises order from the current page's arrangement by calling `PATCH /images/batch/reorder` with `pageOffset + index` values so that page 2+ images receive sort_orders starting at `(page-1)*pageSize` rather than 0.

**Drag-and-drop**: when "Custom order" is active, the gallery grid is wrapped in a `DndContext`/`SortableContext` from `@dnd-kit/core` + `@dnd-kit/sortable`. Each card is a `SortableImageCard` — the entire card surface is the drag handle (listeners spread on the outer wrapper). `PointerSensor` with `activationConstraint: { distance: 8 }` lets short clicks still navigate to the detail page. `handleDragEnd` calls `arrayMove` for an immediate optimistic `qc.setQueryData`, then fires `reorderMutation` (`PATCH /images/batch/reorder`) to persist.

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

### Gallery navigation state

`GalleryPage` persists two keys to `sessionStorage` (keyed by `datasetId`):

| Key | Contents | Purpose |
|---|---|---|
| `gallery-state-${datasetId}` | `{ page, sortIdx, captionedFilter, qualityFilter, scrollTop }` | Restores page/sort/filter/scroll when returning from detail view |
| `gallery-nav-${datasetId}` | `{ ids, page, sort, order, captionedFilter }` | Ordered image ID list + query context for prev/next navigation in the detail view |

`ImageDetailPage` reads `gallery-nav-*` for arrow-key navigation. At page boundaries it pre-fetches the adjacent page (`useQuery`, `enabled: atEnd / atStart`); on crossing, writes the new context back to `gallery-nav-*` and updates `gallery-state-*` so **Back** returns to the correct gallery page. Arrow keys are suppressed when an `<input>`, `<textarea>`, or `<select>` has focus, or when `isContentEditable` is true. The `Delete` key opens a confirm dialog in both gallery and detail view; both handlers share the same focus guard. The arrow-key handler is additionally suppressed while the delete confirm dialog is open (`showDeleteConfirm`) to prevent background navigation.

**Nav context invariant for newly created images**: When navigating to an image that was just created (crop, upscale new-file), the new image ID is not in the existing `gallery-nav-*` list, so `currentIndex === -1` and arrow keys would silently do nothing. Always call `injectNavId(datasetId, sourceImageId, newImageId)` (defined at module level in `ImageDetailPage.tsx`) before calling `paneGo` to insert the new ID immediately after the source in the nav context. This applies to: sync crop, crop+upscale job completion, and standalone upscale (non-replace) completion. Conversely, call `removeNavId(datasetId, imageId)` when deleting an image from `ImageDetailPage` to remove the stale ID so arrow-key navigation on adjacent images cannot land on it. Both functions delegate to `mutateNavIds(datasetId, transform)` — the shared helper that handles sessionStorage read/parse/write.

**ImageDetailPage crop tool**: Two output modes controlled by the **Replace** checkbox. *New file* (default) — creates a new `Image` record (filename `{source_stem}_crop{ext}`, collision-handled via `unique_filename`) and navigates to it on success. *Replace* — overwrites the source file in-place, updates the existing `Image` record (width, height, file_size_bytes, format, phash), regenerates the thumbnail, and stays on the same image. The aspect dropdown and zoom slider control the crop selection shape and size; W×H inputs control the output pixel dimensions (resize-after-crop, independent of the selection). When both W and H are filled in, the crop box aspect ratio automatically locks to W/H. The crop endpoint (`POST /images/{id}/crop`) accepts `replace: bool = False`; in replace mode it calls `protect_file_before_overwrite` before touching the file. New-file mode uses `asyncio.get_running_loop()` and a targeted `LIKE '{stem}%'` query for collision detection (not a full dataset scan). An optional upscale model selector (shown when upscale models are configured) enables atomic crop+upscale in either mode: the crop is saved to a temp file, a `crop_upscale` background job runs the upscale, and the endpoint returns `{job_id}` instead of the image dict. The frontend branches on `"job_id" in data` to distinguish the async path.

**ImageDetailPage selection**: A **Select / Selected** toggle button sits in the top toolbar (right of the filename, before the Boxes button). It calls `selectionStore.toggle(imageId, datasetId)` and reflects `isSelected(imageId)` via a targeted selector. Pressing **Space** anywhere on the page (except when a text field is focused or a modal is open) does the same thing — handled in the arrow-key `useEffect` alongside ArrowLeft/ArrowRight; `showDetectModal` is additionally checked. The button is styled `btn-primary` + `CheckSquare` icon when selected, `btn-ghost` + `Square` when not.

**ImageDetailPage caption panel**: Contains only the caption text textarea and Save button (plus the collapsible AI Generate section). The `tags` and `caption_style` fields are still present in the DB schema, backend save endpoint (`PATCH /captions/{id}`), and save mutation — they are read from `captionData` and re-persisted unchanged — but neither a tag editor nor a style picker is exposed in the UI. A live **token counter** (`N words · N tokens`) is displayed right-aligned beside the "Caption Text" label, computed via `gpt-tokenizer` (`encode` with GPT-2 BPE) inside a `useMemo` keyed on `captionText`. The counter turns amber at ≥ 70 tokens and red at ≥ 77 to signal the CLIP truncation limit.

**Caption textarea auto-resize**: The textarea auto-expands to fit its content via a `useEffect` keyed on `[captionText, imageId, image?.id]`. Three dependencies are required: (1) `captionText` — resize when the text changes; (2) `imageId` — resize immediately on navigation (covers same-text-on-two-images edge case); (3) `image?.id` — the critical one: the component has an early return `if (imageLoading || !image) return <Loading/>`, so the textarea is not in the DOM while the image query is pending. `captionData` (the lighter query) typically resolves before `image`, so `captionRef.current` is null when `captionText` first changes on a fresh navigation. Adding `image?.id` ensures the resize fires again once the loading phase ends and the ref becomes valid. Do not remove any of these three dependencies. A separate `useEffect` on `[imageId]` resets `captionDirty` to `false` on navigation; without this, an unsaved edit would leave `captionDirty=true` on the next image, causing the `captionData` effect to skip `setCaptionText` entirely (blocking the resize).

The **AI Generate** collapsible (`showAi` state, gated `enabled: showAi`) uses the same four-model-type picker pattern and `resolveModelId` helper as `SelectionToolbar`. WD14 models show only the threshold slider and hide `PromptPresetManager` and `ResolutionPicker` (both are wrapped in `{aiModel && !aiModel.startsWith("wd14:") && (...)}` — keep this consistent with `SelectionToolbar`'s ternary). An `aiOverwrite: bool` state (default `true`) is exposed as a checkbox that appears once a model is selected. The `["captioning-models"]` query is defined at component level (not inside the collapsible) but gated on `enabled: showAi` to avoid loading until the section is first opened.

### Gallery generation metadata

`generation_metadata` is included in both `ImageOut` and `ImageListItem` backend schemas, so it comes back with the gallery list response. `ImageCard` shows a small accent `<Cpu>` icon button in the filename row when `image.generation_metadata` is set; clicking it (without navigating) opens a page-level modal in `GalleryPage` that renders `<GenerationMetadata>`. The same component appears in the right panel of `ImageDetailPage`, expanded by default.

### AI generation metadata

Extracted at import time and on direct upload via `extract_generation_metadata(path)` in `backend/services/image_service.py`. Stored in `Image.generation_metadata` (JSON column, nullable). Included in both `ImageOut` and `ImageListItem` schemas.

Supported formats:

| PNG chunk key | Tool | Parser |
|---|---|---|
| `parameters` | AUTOMATIC1111 / SD WebUI | `_parse_a1111_params()` — splits on `Negative prompt:` (case-insensitive, handles `\r\n`); extracts steps/cfg_scale/seed/sampler/sampler_name/model/model_hash/size/vae from trailing key-value line |
| `workflow` / `prompt` | ComfyUI | Stores raw workflow JSON; extracts text from `CLIPTextEncode`/`CLIPTextEncodeSDXL` nodes as prompt |
| `Comment` | Generic | Stored as `raw`; JSON-parsed if valid |
| EXIF tag 37510 (UserComment) | Various | Parsed as A1111 format if `Steps:` present, otherwise stored as `raw` |

**Parser invariant**: `prompt` is only stored when non-empty. An image generated with no positive prompt results in a dict with `negative_prompt` but no `prompt` key — this is correct, not a bug.

Frontend: `components/image/GenerationMetadata.tsx` — collapsible section titled **GENERATION METADATA** (default expanded) with source badge, prompt + copy button, negative prompt, param grid (model/sampler/steps/CFG/seed/size/VAE), and optional ComfyUI raw workflow viewer.

**Lazy backfill**: `GET /images/{image_id}` calls `extract_generation_metadata` and commits if the field is NULL, transparently backfilling pre-feature images.
