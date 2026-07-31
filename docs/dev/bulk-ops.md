# Bulk operations

This file covers the bulk operations that act across a selection or a whole dataset by editing
*metadata*: bulk caption editing (find/replace, regex), the bulk image endpoints (rename /
delete / count / provenance / reorder), and `BulkEditPage`'s tabs. The interesting content
here is **which rows are selected** — `_apply_bulk_filters` and the scope panel that drives it.

The bulk jobs that rewrite pixels or files — batch resize/crop, the thumbnail rebuild, and
detection-driven cropping — are in `docs/dev/bulk-image-jobs.md`. Dataset export has its own
file — see `docs/dev/export.md`.

## Bulk caption editing

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

**Regression tests**: `backend/tests/test_caption_bulk_http.py` drives `/find-replace` and `/bulk-edit` over HTTP — selective literal replace (DB + sidecar), regex `remove` whitespace-normalization and skip counts, invalid regex swallowed as a 200 no-op, and the 408 regex-timeout path leaving every caption and sidecar byte-identical (both code paths).

Per-caption subsumption cleanup (drop `tail` when `long tail` is present) is **not** a bulk-edit operation — it lives on the Consolidate Tags page as "Quick cleanup" (`POST /tag-consolidation/dataset/{id}/subsume`); see `docs/dev/tag-consolidation.md`. `BulkEditForm` invalidates the per-image `["caption"]` / `["image"]` query families on success (in addition to `["images", datasetId]` + the four stats keys) so an open `ImageDetailPage` refreshes immediately.

## Bulk image operations (rename / delete / count / provenance / thumbnails)

Five endpoints in `backend/routers/images.py` share a common `_apply_bulk_filters(query, image_ids, subfolder, quality_flags, include_flagged=False)` helper (module-level private function) that applies the triple filter — `image_ids` takes precedence over `subfolder`; `quality_flags` direction is controlled by `include_flagged`: when `False` (default) it excludes images where ANY flag is `True` (`AND IS NOT TRUE` per flag); when `True` it targets images where ANY flag is `True` (`OR IS TRUE` per flag). All of them accept a `BulkFilterBase`-derived schema (`backend/schemas/image.py`).

`BulkFilterBase` fields (shared by every one of these schemas): `dataset_id`, `image_ids: list[str] | None`, `quality_flags: list[str] | None`, `subfolder: str | None`.

| Endpoint | Extra fields | Returns |
|---|---|---|
| `POST /images/bulk-count` | `include_flagged: bool = False` | `{ count: int }` — count of matching images without making any changes |
| `POST /images/bulk-rename` | `new_stem: str`, `sort_by_sort_order: bool = False` | `{ affected: int }` — renames matching images to `{slug}_001.ext`, `_002`, … Uses `slugify_filename` + `unique_filename_with_thumb`; pre-plans all renames before touching the filesystem; DB updated via ORM bulk-by-PK executemany (includes `thumbnail_path`) then `rename_with_sidecar` + thumbnail `replace()` per file; sets `is_auto_named=True`. When `sort_by_sort_order=True`, images are ordered by `sort_order ASC NULLS LAST, created_at ASC` before numbering — used by the gallery's "Renumber Files" button. See § Renumber's two-phase rename below for what the planner deliberately does *not* guard. |
| `POST /images/bulk-delete` | `include_flagged: bool = True` | `{ deleted: int }` — permanently deletes matching images; calls `mark_image_deleted_in_versions` per image for versioning hooks; unlinks image, `.txt` sidecar, and thumbnail; calls `refresh_stats` |
| `POST /images/bulk-provenance` | `source_name` / `source_url` / `license` / `attribution`, each `str \| None`; `include_flagged` | `{ updated: int }` — sets source/license across the selection: an omitted field is left unchanged, `""` (or any whitespace-only string — the router does `raw.strip() or None`) clears it to inherit the dataset default, anything else is written. Applied in chunked `sa_update` batches (`utils.chunked`, keeping bind parameters under SQLite's ceiling). `include_flagged` comes from the body like `bulk_count`/`bulk_delete` and defaults to **`False`**, matching `BulkCountRequest` and `bulk_rename`, so `quality_flags` selects the same set they would — it used to be hardcoded to `True`, i.e. the opposite set. No frontend caller sends it: `BulkProvenanceParams` extends `BulkFilterParams`, which has no `includeFlagged`, and the gallery selection is always an explicit `image_ids` list, so the schema default is what applies. An explicit `image_ids` selection can span datasets, so the query is scoped by `dataset_id` **only** for whole-dataset/subfolder selections, and `ensure_not_busy` runs for every dataset the rows actually touch. This is how an existing library of thousands of images gets labeled. Shares `_provenance_values()` with `PATCH /images/{id}/provenance` — see `docs/dev/provenance.md`. |
| `POST /images/bulk-thumbnails` | `include_flagged: bool = False`, `label: str \| None` | `{ job_id, total }` — enqueues a `regenerate_thumbnails` job that re-cuts the preview for every image in the scope. See `docs/dev/bulk-image-jobs.md` § Rebuilding thumbnails. |
| `PATCH /images/batch/reorder` | `{ dataset_id, updates: [{id, sort_order}] }` | `{ updated: int }` — bulk-sets `sort_order` on a list of images; validates all IDs belong to `dataset_id` before updating. Used by drag-and-drop reordering in the gallery. |

**Frontend surfaces**:
- `SelectionToolbar` — **Edit** button (pencil icon) opens a modal with `<BulkEditForm imageIds={selectedIds} />`. On success, invalidates `["images", datasetId]` + all four stats queries and clears the selection.
- `BulkEditPage` (`/datasets/:datasetId/bulk-edit`, sidebar "Bulk Edit") — eight tabs: *Edit Captions*, *Upscale*, *Crop to Subject*, *Detections*, *Apply LUT*, *Rename*, *Thumbnails*, and *Delete*. All tabs share the same scope radio (*All images* / *[Exclude/Only] images with quality flags* / *Currently selected*) and a **Subfolder** filter dropdown (shown when subfolders exist; hidden for the "Currently selected" scope). The quality-flag scope label and semantics depend on the active tab: on the Delete tab the label reads "Only images with quality flags" and `include_flagged=True` (OR logic — target images with any selected flag); on all other tabs it reads "Exclude images with quality flags" and `include_flagged=False` (AND NOT logic — skip images with any selected flag). `const targetsFlaggedImages = tab === "delete"` is the single source of truth for this distinction and drives the label ternaries, the `bulk-count` query fn, and the `bulk-count` query key (boolean, not the raw tab string — collapses the four non-delete tabs into one cache slot). A `POST /images/bulk-count` query fires on every scope/flag/subfolder/tab change and shows "N images will be affected" at the bottom of the scope panel. The flags scope requires at least one flag to be chosen before the form can submit. `qualityFlags` is passed to all nine tab forms including `<UpscaleForm>` and `<LutForm>` — nine rather than eight because the Detections tab renders two keyed forms (the Run Detection and Delete Detections panels).

`BulkEditForm` (`frontend/src/components/caption/BulkEditForm.tsx`) — reusable form. `qualityFlags` prop: uses those flags and hides the internal selector; when omitted the internal selector is shown. `disabled` prop prevents submission (used by `BulkEditPage` when scope is "flags" but nothing is selected).

**Detections tab** — renders two stacked panels: a **Run Detection** panel (`DetectionRunForm`) above a **Delete Detections** panel (`DetectionBulkDeleteForm`). Both take the standard bulk-form scope props (`datasetId, imageIds?, subfolder?, qualityFlags?, disabled?`) and share the page's "N images will be affected" counter (no per-form dry-run count on the run side).

- `DetectionRunForm` (`frontend/src/components/detection/DetectionRunForm.tsx`) — inline (non-modal) counterpart of the `SelectionToolbar` Detect modal; runs `POST /detection/run` across the scope. Model select (Florence-2 Large/PromptGen, NudeNet + min-confidence slider, SAM 2.1 text-prompt only — points are per-image and unavailable here, SAM 3), Florence task select (`<OD>` / `<CAPTION_TO_PHRASE_GROUNDING>`), use-caption-as-prompt checkbox, comma-separable prompt input, overwrite + watermark-sync checkboxes (sync gated on sam2/sam3/grounding, same `detectSyncEligible` rule), optional job label. Uses `detectionModelFamily` to reset task/prompt only on family change. Job tracking uses the **id-list pattern** (`jobIds: string[]`, see `docs/dev/frontend-jobs.md`) so multiple runs can be queued; toasts "Detection queued" at start and "Detection complete"/failure per job, invalidating detection queries + `["image"]` + `["images", datasetId]` on completion (the gallery key matters for watermark-sync runs, which change flag badges; TopBar's detection branch deliberately skips it).
- `DetectionBulkDeleteForm` (`frontend/src/components/detection/DetectionBulkDeleteForm.tsx`) deletes detections (not images) matching the shared scope plus optional label chips (`["detection-labels", datasetId]`), model chips (`["detection-models", datasetId]`), and a "Score below" number (0–1; unscored/manual rows never match). A **dry-run count query** — `bulkDelete({dry_run:true})` keyed on the full param set, `staleTime ~5s` (the POST is idempotent) — drives a live "N detections will be deleted" line; the real delete is gated behind a danger `ConfirmDialog` showing the count, and on success invalidates `["detection-labels"|"detection-models", ds]`, the count query, and `["image"]` (detail pages refresh without navigation).

This tab behaves like Crop/Upscale for scope (`targetsFlaggedImages` stays `tab === "delete"`, so it uses the exclude-flags semantics), not like the image Delete tab.

`DetectionJobRequest` (`backend/schemas/detection.py`) accepts `subfolder` + `quality_flags` (exclude images with these flags) for whole-dataset runs, applied in `run_detection`'s image query exactly like `bulk-delete`/`crop` — the same `normalize_subfolder` + `ALLOWED_FLAG_KEYS`-validated `as_boolean().is_not(True)` filters. Explicit `image_ids` win and bypass both filters.

`BulkRenameForm` (`frontend/src/components/image/BulkRenameForm.tsx`) — base-name input with live slug preview (`{slug}_001.ext, …`); `useMutation` → `imagesApi.bulkRename`; on success invalidates `["images", datasetId]`.

`BulkDeleteForm` (`frontend/src/components/image/BulkDeleteForm.tsx`) — amber warning panel + danger button; `useMutation` → `imagesApi.bulkDelete`; on success invalidates `["images", datasetId]` + all four stats queries and calls `selectionStore.clear()`.

### Renumber's two-phase rename

`bulk_rename` is the **one sanctioned exception** to `unique_filename_with_thumb`'s "never exclude the stems of images being renamed" contract. It passes `disk_exclude=batch_current_filenames` and subtracts the batch's own `.webp` stems from `occupied_thumb_stems`, because without both a second Renumber of `image.jpg … image_007.jpg` sees all eight stems and all eight files as occupied and continues at `image_008` instead of restarting at `001`. Both exclusions arrived together in commit `37bba00` — the author needed both — and both are pinned by `test_bulk_renumber_restarts_its_counter_on_a_second_pass`, so neither can be "simplified away".

What pays for the exception is the two-phase filesystem pass. Any row whose target collides with a batch member's *current* files renames to a temp name first and moves to its final name in a second sweep; the rest rename directly. **The collision test covers all three artifacts a rename moves** — the image path, the `{stem}.webp` thumbnail, and the `{stem}.txt` sidecar. Testing only the image path was PM-017: the two derived files are keyed on the stem alone, so a cross-extension collision (`img.jpg` taking the stem `img.png` holds) was invisible, took the direct branch, and destroyed a live sibling's thumbnail and caption while leaving every *filename* correct. Nothing repairs that — `serve_thumbnail` regenerates only a **missing** thumbnail, never a stale one.

Two smaller rules in the same function. Temp names are keyed on the row id (`__renaming__{img_id}`), like the DB half's staging names, so a user typing `__renaming__image` as the new stem cannot collide with a live temp file. And `occupied_thumb_stems` unions in the stems of every non-batch `db_names` entry, not just the `.webp` files present: a row whose thumbnail is missing still owns its stem, since the next view regenerates it there — the same two-term occupancy `docs/dev/file-browser.md` § `POST /rename` defines.

## Bulk jobs that rewrite files

`POST /images/batch/resize`, `POST /images/batch/crop`, `POST /images/bulk-thumbnails` and
`POST /detection/crop` are documented in `docs/dev/bulk-image-jobs.md` — they share a loop
shape (PM-013 commit ordering, `contained_path` gating, `thumbnails_stale`,
`remap_detections_for_crop`) that has nothing to do with the scope filtering above.

