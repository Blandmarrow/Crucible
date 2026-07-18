# Export & bulk operations

This file covers bulk caption editing, bulk image rename/delete/count/reorder, detection-driven cropping, and the dataset export page.

### Bulk caption editing

`POST /captions/dataset/{dataset_id}/bulk-edit` (router: `backend/routers/captions.py`, service: `backend/services/caption_service.py::bulk_edit_captions`) — synchronous bulk text operation on caption_text across a dataset. Returns `{ affected, skipped }`.

**`BulkEditRequest`** fields:

| Field | Default | Effect |
|---|---|---|
| `operation` | required | `"prepend"` / `"append"` / `"remove"` / `"find_replace"` |
| `text` | required | Text to add (prepend/append) or text to find (remove/find_replace) |
| `replacement` | `""` | Replacement string for `find_replace` |
| `use_regex` | `false` | Treat `text` (and `replacement`) as a regex, compiled via `compile_user_regex` (the `regex` package — never stdlib `re`); an invalid pattern skips the whole batch. Matching is offloaded to a thread executor (real concurrency — `regex` releases the GIL) under a single 30-second time budget for the *entire batch*, enforced inside the engine via `regex_sub_deadline` (a `time.monotonic()` deadline, **not** `asyncio.wait_for`); returns 408 on timeout. See CLAUDE.md § Key invariants for why stdlib `re` + `wait_for` cannot bound a catastrophic pattern. |
| `image_ids` | `null` | If set, restrict to these image IDs |
| `quality_flags` | `null` | If set, additionally **exclude** images where any of these flags is `True` (AND IS NOT TRUE per flag); validated against `ALLOWED_FLAG_KEYS` from `utils.py` |
| `subfolder` | `null` | If set, restrict to images in this subfolder (ignored when `image_ids` is provided) |

Images with no `caption_text` are skipped for `remove` and `find_replace`. For `prepend`/`append` they receive just the added text. A single `db.commit()` is made after the loop — not per image.

**AI-artifact flag clearing.** Every caption-write site in `bulk_edit_captions` and `find_replace_captions` (both the plain and regex branches) calls `_maybe_clear_ai_artifact(img, new_text)` before writing the sidecar, so a bulk `remove`/`find_replace` that empties a caption also unmarks `has_ai_artifacts` — matching the single-image editor. See `docs/dev/captioning.md` § Captioning job execution (Flag lifecycle) for the helper. The `bulk-edit` and `find-replace` router endpoints (`routers/captions.py`) call `refresh_stats(db, dataset_id)` after the service returns so the Statistics page's AI-artifact count recomputes in the same request.

Per-caption subsumption cleanup (drop `tail` when `long tail` is present) is **not** a bulk-edit operation — it lives on the Consolidate Tags page as "Quick cleanup" (`POST /tag-consolidation/dataset/{id}/subsume`); see `docs/dev/tag-consolidation.md`. `BulkEditForm` invalidates the per-image `["caption"]` / `["image"]` query families on success (in addition to `["images", datasetId]` + the four stats keys) so an open `ImageDetailPage` refreshes immediately.

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
- `BulkEditPage` (`/datasets/:datasetId/bulk-edit`, sidebar "Bulk Edit") — six tabs: *Edit Captions*, *Upscale*, *Crop to Subject*, *Apply LUT*, *Rename*, and *Delete*. All tabs share the same scope radio (*All images* / *[Exclude/Only] images with quality flags* / *Currently selected*) and a **Subfolder** filter dropdown (shown when subfolders exist; hidden for the "Currently selected" scope). The quality-flag scope label and semantics depend on the active tab: on the Delete tab the label reads "Only images with quality flags" and `include_flagged=True` (OR logic — target images with any selected flag); on all other tabs it reads "Exclude images with quality flags" and `include_flagged=False` (AND NOT logic — skip images with any selected flag). `const targetsFlaggedImages = tab === "delete"` is the single source of truth for this distinction and drives the label ternaries, the `bulk-count` query fn, and the `bulk-count` query key (boolean, not the raw tab string — collapses the four non-delete tabs into one cache slot). A `POST /images/bulk-count` query fires on every scope/flag/subfolder/tab change and shows "N images will be affected" at the bottom of the scope panel. The flags scope requires at least one flag to be chosen before the form can submit. `qualityFlags` is passed to all five tab forms including `<UpscaleForm>` and `<LutForm>`.

`BulkEditForm` (`frontend/src/components/caption/BulkEditForm.tsx`) — reusable form. `qualityFlags` prop: uses those flags and hides the internal selector; when omitted the internal selector is shown. `disabled` prop prevents submission (used by `BulkEditPage` when scope is "flags" but nothing is selected).

`BulkRenameForm` (`frontend/src/components/image/BulkRenameForm.tsx`) — base-name input with live slug preview (`{slug}_001.ext, …`); `useMutation` → `imagesApi.bulkRename`; on success invalidates `["images", datasetId]`.

`BulkDeleteForm` (`frontend/src/components/image/BulkDeleteForm.tsx`) — amber warning panel + danger button; `useMutation` → `imagesApi.bulkDelete`; on success invalidates `["images", datasetId]` + all four stats queries and calls `selectionStore.clear()`.

### Detection-driven cropping (crop to detected subject)

`POST /detection/crop` (`backend/routers/detection.py::crop_to_detection`, schema `DetectionCropRequest`) — batch-crops images to their detection bboxes as a `crop_to_detection` BackgroundJob. Structured as a close mirror of `upscaling.py`'s `_run` (same scope triple, replace/new-file branches, progress-before-item, per-item failure continue, single commit, cancel handling).

- **Scope**: `image_ids` > `dataset_id` + `subfolder` + `quality_flags` (flag exclusion, `ALLOWED_FLAG_KEYS`-validated). `labels: list[str] | None` filters which detection labels drive the crop (None/[] = all); `label` (singular) is the job-label override — do not confuse the two.
- **Crop rect math** is the pure helper `backend/ml/mask_utils.py::detection_crop_rect(bboxes, img_w, img_h, mode, padding_pct, target_ar) -> (x, y, w, h) | None` (unit-tested in `backend/tests/test_detection_crop_rect.py`): sanitizes normalized bboxes, selects `mode="union"` (envelope of all matches) or `"largest"` (max area), pads each side by `padding_pct`% of the box's own extent, then optionally **grow-only** snaps to `target_ar` (never shrinks below the subject; clamps to the image; if no legal rect of that ratio contains the subject, the exact ratio is sacrificed rather than cutting the subject). Denormalizes against the DB `Image.width/height` — consistent because detection predictors and `crop_image_to_dest` both apply EXIF transpose.
- Detections are prefetched with the chunked pattern (`_fetch_bboxes_by_image`, ≤10k ids per `IN`); images with no matching detections are counted request-side and returned as `skipped` in `{job_id, total, skipped}` (`total_items` counts only matched images, keeping the progress denominator honest).
- Per image: `None` rect → `skipped_no_detection`; full-image rect → `skipped_noop` (a no-op crop would only re-encode the file). **Replace** mode calls `protect_file_before_overwrite` then crops via `crop_image_to_dest` to a `{stem}_croptmp{suffix}` temp + `Path.replace()`, regenerates the thumbnail, updates `width/height/file_size_bytes/format/phash/updated_at` and appends a `crop_to_detection` entry to `processing_history` (list-concat reassignment). **New-file** mode derives `{stem}_crop` via `slugify_filename` + `unique_filename_with_thumb` and creates a new Image row.
- Final counts land in `BackgroundJob.result_data` (`{cropped, skipped_no_detection, skipped_noop, failed}`); the terminal SSE message is always "Done.", so the frontend fetches the job on completion and toasts from `result_data`.

**Frontend**: `CropToDetectionForm` (`frontend/src/components/crop/CropToDetectionForm.tsx`) — shared form with the `UpscaleForm` props contract plus `disabled`; label chips from `["detection-labels", datasetId]`, mode radio, padding %, aspect `<select>` fed by `frontend/src/constants/aspectRatios.ts::ASPECT_PRESETS` (also consumed by the ImageDetailPage crop tool), New file/Replace radio. Surfaced as the **Crop** modal in `SelectionToolbar`, the **Crop to Subject** tab on `BulkEditPage`, and the **Crop to Subject** button in the `ImageDetailPage` toolbar beside Crop/Upscale/LUT (rendered only when the image has detections and crop mode is off; scoped to `imageIds=[imageId]`; passes the optional `availableLabels: {label, count}[]` prop so the chips show only that image's labels with per-detection counts instead of running the dataset-wide query, and its `onSuccess` additionally invalidates `["image", imageId]`). Job type `crop_to_detection` is registered in `TopBar`'s `IMAGE_MODIFYING_JOB_TYPES`.

### Export page

`ExportPage.tsx` supports 3 format buttons: kohya, ai-toolkit, plain folder. All three are fully implemented. The left panel uses `.form-row` layout throughout.

**Shared export loop**: `export_service.py` uses a shared `_run_export_loop(...)` helper that handles the DB query (column-explicit select, no blob fields), filter loop, progress emission, and result accumulation. It takes flat parameters — the source `db`/`dataset_id`/`image_ids`, the destination `dest_dir`, format/quality/resize settings (`output_format`, `jpeg_quality`, `resize_to`), the exclusion filters (`aesthetic_min`, `captioned_only`, `exclude_flags`, `style_sim_min`), job/progress identifiers (`job_id`, `job_type`), and caption/output flags (`caption_format`, `accumulate_plain`, `subfolders`, `strip_metadata`, `captions_only`) — and returns `{exported, jsonl_entries}`. There is no per-format callback: each of `export_kohya`, `export_aitoolkit`, and `export_plain` just calls it with their own destination directory and settings. Blob columns (`clip_embedding`, `dino_embedding`, `dino_layer_embeddings`) are excluded from the query — only `id`, `file_path`, `filename`, `caption_text`, `aesthetic_score`, `quality_flags`, and `style_similarity_score` are loaded. **Export order** is always `sort_order ASC NULLS LAST, created_at ASC` — datasets with a custom drag order export in that sequence; datasets without one export in stable chronological order. This determines the order of `captions.jsonl` entries and the on-disk write order; it does **not** rename files — exports keep each image's original stem (`_dest_img_path`). Numbered filenames (`0001.jpg`, …) only exist if the user ran the gallery's "Renumber Files" (bulk-rename with `sort_by_sort_order: true`) beforehand — see `docs/dev/gallery-and-images.md` § Manual image ordering.

**Filters** (applied in `export_service.py::_is_excluded()`, shared by all three formats):

| Control | Param sent | Backend behaviour |
|---|---|---|
| Aesthetic ≥ N | `aesthetic_min: float` | Excludes images where `aesthetic_score` is NULL or below threshold |
| Has caption | `captioned_only: bool` | Excludes images with no `caption_text` |
| Per-flag checkboxes (one per `FLAG_OPTIONS` entry — Blurry / Noisy / Near-uniform / Watermarked / Duplicate / NSFW / AI artifacts) | `exclude_flags: str` (comma-separated flag names, e.g. `"is_blurry,has_watermark"`) | Excludes images where any of the named keys in `quality_flags` JSON is truthy |
| Style similarity ≥ N | `style_sim_min: float` | Excludes images where `style_similarity_score` is NULL or below threshold |

`exclude_flags` is parsed by `_parse_flags()` in `routers/export.py`, which validates every name against `ALLOWED_FLAG_KEYS` and raises HTTP 400 on an unknown key (captioning validates the same set via a pydantic `field_validator`, so it answers 422 instead — the check is equivalent, the status differs). **Call `_parse_flags` in the request path, never inside the job coroutine**: the three POST handlers enqueue a job and return `{job_id}` before the coroutine runs, so an exception raised in there fails the job instead of reaching the client. Each handler parses once into a local and closes over it.

Filter params are debounced 350 ms on the frontend; the preview query (`GET /export/preview/{dataset_id}`) reacts to changes and returns `{ will_export, total, excluded_low_aesthetic, excluded_uncaptioned, excluded_flagged, excluded_style_sim, sample_files }`.

**Caption format** (`caption_format: "txt" | "caption" | "jsonl"`): controls sidecar extension for kohya/ai-toolkit; `"jsonl"` writes a single `captions.jsonl` in the output root instead of per-image sidecars. Hidden for plain folder (always writes `captions.jsonl`). JSONL entries are `{file, caption}` — the old per-tag `tags.csv` and the `tags` key in each JSONL entry were dropped along with the tags table.

**Resize** (`resize_to: int | None`): after copying/converting, resizes the longest side to the given pixel count via Pillow (only downscales; originals untouched). Skips the PIL round-trip entirely when `resize_to=None`, `output_format="original"`, and `strip_metadata=False`.

**Strip metadata** (`strip_metadata: bool`, default `False`): when `True`, forces a PIL round-trip even for the "original format, no resize" case, which naturally discards PNG text chunks (A1111 `parameters`, ComfyUI `workflow`/`prompt`, etc.) and EXIF. The PIL paths (format conversion, resize) already strip metadata — this flag only affects the `shutil.copy2()` fast path.

**Loss-mask export** (`export_masks: bool`, default `False`; plus `mask_labels: list[str] | None` — None/empty = all labels, `mask_invert: bool`, `mask_missing: "white" | "skip"`): when enabled, each exported image gets a grayscale mask PNG for masked-loss training (kohya `conditioning_data_dir` / ai-toolkit `mask_path` — both match masks to images by filename stem, so mask stems always equal the exported image stems). Mechanics:

- Rasterization is `backend/ml/mask_utils.py::rasterize_detections(rows, w, h, invert)` — polygon JSON from `Detection.mask` scaled to the final exported dimensions and filled white via PIL `ImageDraw`; detections without polygons (Florence-2, NudeNet) fall back to filled bbox rectangles. `_write_image` returns the final `(w, h)` (post-EXIF-transpose, post-resize; the `shutil.copy2` fast path reads size + EXIF orientation tag without decoding pixels) so masks always match the exported image dimensions.
- Detections are **prefetched in one chunked query** (`_fetch_detections_by_image`, ≤10k ids per `IN`) before the loop — never per-image.
- `mask_missing="white"` writes a full-white mask for images with no matching detections (counted as `masks_full_white` in the result); `"skip"` excludes them from the export entirely (`excluded_no_mask`). Result dict gains `masks_written` (+ `mask_dir` from the wrappers).
- Mask dirs: kohya `output_dir/{n_repeats}_{concept_token}_mask/`, ai-toolkit `output_dir/{concept_name}_mask/`, plain `output_dir/masks/`. Created only when masks are enabled; `captions_only=True` disables mask export.
- `mask_labels` is a JSON list in the POST bodies but a **JSON-array string** in the preview GET (`mask_labels=["a dog"]`) — labels are free text and may contain commas, so no comma-splitting anywhere. The preview response gains `images_without_detections` when `export_masks=true`; the preview also accepts `mask_missing`, and with `skip` those images are excluded from `will_export`/`sample_files` so the headline count matches what the export will actually write.
- The ExportPage label chips are fed by `GET /detection/labels/{dataset_id}` (`routers/detection.py`) → `[{label, image_count}]`, distinct labels joined against the dataset's images.

**Captions only** (`captions_only: bool`, default `False`): when `True`, skips all image file writes. The `src.exists()` check is also bypassed so images with missing files are still included (their caption data is in the DB). For kohya/aitoolkit, only sidecar/JSONL caption files are written to the concept subdirectory. For plain, no `images/` subdirectory is created — only `captions.jsonl` is written to `output_dir`. In captions-only mode, JSONL entries always use `img.filename` (the original filename) regardless of `output_format`, since no format conversion occurs. Image format, resize, and strip-metadata settings are ignored when this flag is set.
