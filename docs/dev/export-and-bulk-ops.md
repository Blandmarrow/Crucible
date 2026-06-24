# Export & bulk operations

This file covers bulk caption editing, bulk image rename/delete/count/reorder, and the dataset export page.

### Bulk caption editing

`POST /captions/dataset/{dataset_id}/bulk-edit` (router: `backend/routers/captions.py`, service: `backend/services/caption_service.py::bulk_edit_captions`) — synchronous bulk text operation on caption_text across a dataset. Returns `{ affected, skipped }`.

**`BulkEditRequest`** fields:

| Field | Default | Effect |
|---|---|---|
| `operation` | required | `"prepend"` / `"append"` / `"remove"` / `"find_replace"` |
| `text` | required | Text to add (prepend/append) or text to find (remove/find_replace) |
| `replacement` | `""` | Replacement string for `find_replace` |
| `use_regex` | `false` | Treat `text` (and `replacement`) as a Python regex; invalid patterns skip the image. Regex matching runs in a thread executor with a 30-second `asyncio.wait_for` timeout to prevent catastrophic backtracking from blocking the event loop; returns 408 on timeout. |
| `image_ids` | `null` | If set, restrict to these image IDs |
| `quality_flags` | `null` | If set, additionally **exclude** images where any of these flags is `True` (AND IS NOT TRUE per flag); validated against `ALLOWED_FLAG_KEYS` from `utils.py` |
| `subfolder` | `null` | If set, restrict to images in this subfolder (ignored when `image_ids` is provided) |

Images with no `caption_text` are skipped for `remove` and `find_replace`. For `prepend`/`append` they receive just the added text. A single `db.commit()` is made after the loop — not per image.

### Bulk image operations (rename / delete / count)

Three endpoints in `backend/routers/images.py` share a common `_apply_bulk_filters(query, image_ids, subfolder, quality_flags, include_flagged=False)` helper (module-level private function) that applies the triple filter — `image_ids` takes precedence over `subfolder`; `quality_flags` direction is controlled by `include_flagged`: when `False` (default) it excludes images where ANY flag is `True` (`AND IS NOT TRUE` per flag); when `True` it targets images where ANY flag is `True` (`OR IS TRUE` per flag). All three accept a `BulkFilterBase`-derived schema (`backend/schemas/image.py`).

`BulkFilterBase` fields (shared by all three schemas): `dataset_id`, `image_ids: list[str] | None`, `quality_flags: list[str] | None`, `subfolder: str | None`.

| Endpoint | Extra fields | Returns |
|---|---|---|
| `POST /images/bulk-count` | `include_flagged: bool = False` | `{ count: int }` — count of matching images without making any changes |
| `POST /images/bulk-rename` | `new_stem: str`, `sort_by_sort_order: bool = False` | `{ affected: int }` — renames matching images to `{slug}_001.ext`, `_002`, … Uses `slugify_filename` + `unique_filename_with_thumb`; pre-plans all renames before touching the filesystem; DB updated via ORM bulk-by-PK executemany (includes `thumbnail_path`) then `rename_with_sidecar` + thumbnail `replace()` per file; sets `is_auto_named=True`. When `sort_by_sort_order=True`, images are ordered by `sort_order ASC NULLS LAST, created_at ASC` before numbering — used by the gallery's "Renumber Files" button. |
| `POST /images/bulk-delete` | `include_flagged: bool = True` | `{ deleted: int }` — permanently deletes matching images; calls `mark_image_deleted_in_versions` per image for versioning hooks; unlinks image, `.txt` sidecar, and thumbnail; calls `refresh_stats` |
| `PATCH /images/batch/reorder` | `{ dataset_id, updates: [{id, sort_order}] }` | `{ updated: int }` — bulk-sets `sort_order` on a list of images; validates all IDs belong to `dataset_id` before updating. Used by drag-and-drop reordering in the gallery. |

**Frontend surfaces**:
- `SelectionToolbar` — **Edit** button (pencil icon) opens a modal with `<BulkEditForm imageIds={selectedIds} />`. On success, invalidates `["images", datasetId]` + all four stats queries and clears the selection.
- `BulkEditPage` (`/datasets/:datasetId/bulk-edit`, sidebar "Bulk Edit") — five tabs: *Edit Captions*, *Upscale*, *Apply LUT*, *Rename*, and *Delete*. All tabs share the same scope radio (*All images* / *[Exclude/Only] images with quality flags* / *Currently selected*) and a **Subfolder** filter dropdown (shown when subfolders exist; hidden for the "Currently selected" scope). The quality-flag scope label and semantics depend on the active tab: on the Delete tab the label reads "Only images with quality flags" and `include_flagged=True` (OR logic — target images with any selected flag); on all other tabs it reads "Exclude images with quality flags" and `include_flagged=False` (AND NOT logic — skip images with any selected flag). `const targetsFlaggedImages = tab === "delete"` is the single source of truth for this distinction and drives the label ternaries, the `bulk-count` query fn, and the `bulk-count` query key (boolean, not the raw tab string — collapses the four non-delete tabs into one cache slot). A `POST /images/bulk-count` query fires on every scope/flag/subfolder/tab change and shows "N images will be affected" at the bottom of the scope panel. The flags scope requires at least one flag to be chosen before the form can submit. `qualityFlags` is passed to all five tab forms including `<UpscaleForm>` and `<LutForm>`.

`BulkEditForm` (`frontend/src/components/caption/BulkEditForm.tsx`) — reusable form. `qualityFlags` prop: uses those flags and hides the internal selector; when omitted the internal selector is shown. `disabled` prop prevents submission (used by `BulkEditPage` when scope is "flags" but nothing is selected).

`BulkRenameForm` (`frontend/src/components/image/BulkRenameForm.tsx`) — base-name input with live slug preview (`{slug}_001.ext, …`); `useMutation` → `imagesApi.bulkRename`; on success invalidates `["images", datasetId]`.

`BulkDeleteForm` (`frontend/src/components/image/BulkDeleteForm.tsx`) — amber warning panel + danger button; `useMutation` → `imagesApi.bulkDelete`; on success invalidates `["images", datasetId]` + all four stats queries and calls `selectionStore.clear()`.

### Export page

`ExportPage.tsx` supports 3 format buttons: kohya, ai-toolkit, plain folder. All three are fully implemented. The left panel uses `.form-row` layout throughout.

**Shared export loop**: `export_service.py` uses a shared `_run_export_loop(session, dataset_id, dest_dir, filters, progress_cb, format_fn)` helper that handles the DB query (column-explicit select, no blob fields), filter loop, progress emission, and result accumulation. Each of `export_kohya`, `export_aitoolkit`, and `export_plain` delegates to this helper and provides only a format-specific callback. Blob columns (`clip_embedding`, `dino_embedding`, `dino_layer_embeddings`) are excluded from the query — only `id`, `file_path`, `filename`, `caption_text`, `aesthetic_score`, `quality_flags`, and `style_similarity_score` are loaded. **Export order** is always `sort_order ASC NULLS LAST, created_at ASC` — datasets with a custom drag order export in that sequence; datasets without one export in stable chronological order. This determines the numbered filename sequence (`0001.jpg`, `0002.jpg`, …) in kohya/ai-toolkit formats.

**Filters** (applied in `export_service.py::_is_excluded()`, shared by all three formats):

| Control | Param sent | Backend behaviour |
|---|---|---|
| Aesthetic ≥ N | `aesthetic_min: float` | Excludes images where `aesthetic_score` is NULL or below threshold |
| Has caption | `captioned_only: bool` | Excludes images with no `caption_text` |
| Per-flag checkboxes (Blurry / Noisy / Near-uniform / Watermarked / Duplicate) | `exclude_flags: str` (comma-separated flag names, e.g. `"is_blurry,has_watermark"`) | Excludes images where any of the named keys in `quality_flags` JSON is truthy |
| Style similarity ≥ N | `style_sim_min: float` | Excludes images where `style_similarity_score` is NULL or below threshold |

Filter params are debounced 350 ms on the frontend; the preview query (`GET /export/preview/{dataset_id}`) reacts to changes and returns `{ will_export, total, excluded_low_aesthetic, excluded_uncaptioned, excluded_flagged, excluded_style_sim, sample_files }`.

**Caption format** (`caption_format: "txt" | "caption" | "jsonl"`): controls sidecar extension for kohya/ai-toolkit; `"jsonl"` writes a single `captions.jsonl` in the output root instead of per-image sidecars. Hidden for plain folder (always writes `captions.jsonl`). JSONL entries are `{file, caption}` — the old per-tag `tags.csv` and the `tags` key in each JSONL entry were dropped along with the tags table.

**Resize** (`resize_to: int | None`): after copying/converting, resizes the longest side to the given pixel count via Pillow (only downscales; originals untouched). Skips the PIL round-trip entirely when `resize_to=None`, `output_format="original"`, and `strip_metadata=False`.

**Strip metadata** (`strip_metadata: bool`, default `False`): when `True`, forces a PIL round-trip even for the "original format, no resize" case, which naturally discards PNG text chunks (A1111 `parameters`, ComfyUI `workflow`/`prompt`, etc.) and EXIF. The PIL paths (format conversion, resize) already strip metadata — this flag only affects the `shutil.copy2()` fast path.

**Captions only** (`captions_only: bool`, default `False`): when `True`, skips all image file writes. The `src.exists()` check is also bypassed so images with missing files are still included (their caption data is in the DB). For kohya/aitoolkit, only sidecar/JSONL caption files are written to the concept subdirectory. For plain, no `images/` subdirectory is created — only `captions.jsonl` is written to `output_dir`. In captions-only mode, JSONL entries always use `img.filename` (the original filename) regardless of `output_format`, since no format conversion occurs. Image format, resize, and strip-metadata settings are ignored when this flag is set.
