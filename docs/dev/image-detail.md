# Image detail view: crop, labels & generation metadata

This file covers `ImageDetailPage` itself: the crop tool and crop+upscale, the selection toggle, the labels panel and its hotkeys, provenance, the style-match block, and AI generation-metadata extraction and display. The caption panel, its draft and the helpers that edit it are in `docs/dev/captioning/editor.md`. The two pieces of state it shares with the gallery live next door — what `GalleryPage` remembers between visits (`gallery-state-*`, the debounce rules, Reset filters) is in `docs/dev/gallery/state.md`, and the id list the arrows walk (`gallery-nav-*`, `injectNavId`/`paneGo`, `atEnd`, the post-delete re-derivation, the live refresh and the live total) is in `docs/dev/gallery/nav.md`. The gallery grid itself is in `docs/dev/gallery.md`.

## ImageDetailPage crop tool

**ImageDetailPage crop tool**: Two output modes controlled by the **Replace** checkbox. *New file* (default) — creates a new `Image` record (filename `{source_stem}_crop{ext}`, collision-handled via `unique_filename_with_thumb`) and navigates to it on success. *Replace* — overwrites the source file in-place (crop to a temp file, stage the row and the detection remap, flush, swap, commit — `docs/dev/bulk-image-jobs.md` § Batch resize and aspect crop), updates the existing `Image` record (width, height, file_size_bytes, format, phash), records the edit through `images._record_in_place` (a module-level alias for `utils.record_in_place`, the single writer of `processing_history` for every in-place overwrite in that router — video re-extraction reads it as its skip guard, see `docs/dev/video/reextract-endpoints.md` — and of `scores_stale`, which it sets only when the row already carries a score), regenerates the thumbnail **in a post-commit epilogue** (a `generate_thumbnail` failure is logged and the crop still lands; running it before the commit rolled the row back onto the already-replaced file, PM-013), and stays on the same image. How the box is drawn and turned into a rect is § The crop box below. The crop endpoint (`POST /images/{id}/crop`) accepts `replace: bool = False`; in replace mode it calls `protect_file_before_overwrite` before touching the file. New-file mode uses `asyncio.get_running_loop()` and a targeted `LIKE '{stem}%'` query for collision detection (not a full dataset scan).

## The crop box

The box is `react-image-crop`: drag to move, edges and corners to resize, over a still image. It replaced `react-easy-crop` for issue #91. That library was pan-and-zoom with no resize handles, and it has no free mode at all: an `aspect` of `undefined` falls back to its default prop, `4 / 3`, so "Free" was a locked 4:3 window. Do not bring it back for a free crop.

- **State is a percent crop** (`cropPct`), so the box keeps its place when the pane resizes. The pixel rect the endpoint takes, `croppedArea`, is derived from it and the image's natural size (`cropNatural`), read in the crop `<img>`'s `onLoad`. Current browsers apply a JPEG's EXIF orientation to both the picture and `naturalWidth`, which is the frame the backend crops in after `exif_transpose`. Not confirmed for an orientation tag in a PNG or WebP, which Pillow honours: a browser that ignores it would put the box in the wrong frame (the old cropper had the same exposure). The maths lives in `frontend/src/utils/cropRect.ts`; `percentToPixels` clamps to whole pixels inside the image.
- **The box is seeded in `onLoad`** by `seedCrop`: a pending Crop from Detections prefill (`cropPending`, held in percent of the frame it was computed in), else the largest centred rect of the aspect, else the whole image. `cropNatural` is tagged with the image it was read from, so after an arrow-key move the old box still shows until the new image loads but yields no rect, and Save stays disabled. A tag rather than clearing it in the `[imageId]` effect, which a cached image's load event could beat. A reload of the *same* image at the same size keeps the box: the src carries `updated_at`, so a caption job finishing or a refetch reloads the picture, and reseeding then threw the user's box away. With the tool already open and the size known, Crop from Detections sets the box directly, because that `<img>` does not reload.
- **`effectiveAspect`** is the preset, or W/H when both **Resize to** fields are filled, so a resized crop is never stretched. An aspect change reshapes the box with `reshapeToAspect`, which keeps its area and centre. The Resize-to fields change the aspect once per keystroke, through extreme in-between shapes (H "1", "10", "102"), and fitting those cuts the area, so reshaping each step from the last shrank the box to a sliver. They reshape from `resizeBaseRef` instead, the box when the field got focus (`reshapeFromBase`), and a shape that goes back to Free restores that box. The aspect select reshapes the current box; Free keeps it as it is. A detection prefill uses `growToAspect` instead, so the subject stays inside.
- **The Box row's X/Y/W/H fields** are `NumberField`s (`aria-label` "Crop x/y/w/h") in source pixels. Their clamp is `cropSideMax`; with an aspect locked, committing W sets H and the reverse.
- **The arrow keys.** The library nudges a focused box with them and calls `preventDefault`, but does not stop the event, so the page's window keydown listener skips any event already `defaultPrevented`; without that, a nudge also turned the page. The image's max height is the measured stage (`cropStageRef`, a `ResizeObserver`), since the toolbar and the Box row both wrap.
- **Tests:** `frontend/e2e/image-crop.spec.ts` drives Free, a preset, Save, the arrow keys and the Resize-to keystrokes against the real backend.

## Crop + upscale

**Crop + upscale**: an optional upscale model selector (shown when upscale models are configured) makes the crop atomic with an upscale in either mode: the crop is saved to a temp file, a `crop_upscale` background job runs the upscale, and the endpoint returns `{job_id}` instead of the image dict. The frontend branches on `"job_id" in data` to distinguish the async path. Because the upscaler writes through `normalize_image_format`, a crop+upscale of a `.bmp`/`.gif`/`.tiff`/`.avif` lands at a `.png` path: new-file mode reserves the name under that extension and both workers take the row and the thumbnail from the written file, while **replace + upscale answers 409** ("… writes `{name}`, which already exists on disk") if an unregistered file is already sitting at the fallback path. One image per request means it can refuse before touching anything, unlike the batch jobs that skip and continue — see `docs/dev/image-edit-job.md` § Replace mode follows the PNG fallback. The crop mutation surfaces the server's `detail` through `apiErrorDetail`, so that message reaches the toast.

Only the **replace** worker (`_run_crop_upscale_replace`) writes `result_data` — `{processed, thumbnails_stale}` — and it writes it *inside* its `async with AsyncSessionLocal()` block, because it emits its own terminal `completed` event afterwards and `TopBar`'s completion branch fetches the job row the moment that event lands (`docs/dev/frontend-jobs.md` § The stale-thumbnail warning). The asymmetry is deliberate: new-file mode has no post-commit thumbnail epilogue to report on — it creates a fresh row whose thumbnail is cut on the write path, so a failure there fails the job outright rather than leaving a stale tile behind.

## ImageDetailPage selection

**ImageDetailPage selection**: A **Select / Selected** toggle button sits in the top toolbar (right of the filename, before the Compare menu and the Boxes button). It calls `selectionStore.toggle(imageId, datasetId)` and reflects `isSelected(imageId)` via a targeted selector. Pressing **Space** anywhere on the page (except when a text field is focused or a modal is open) does the same thing — handled in the arrow-key `useEffect` alongside ArrowLeft/ArrowRight; `showDetectModal` is additionally checked. The button is styled `btn-primary` + `CheckSquare` icon when selected, `btn-ghost` + `Square` when not.

The header's **Compare** menu, right of the toggle, opens the image with the selection or with its arrow-key neighbours; see `docs/dev/gallery/compare.md` § Opening it beside the gallery. The header wraps (`flex-wrap`) rather than overflowing: the filename takes the spare width at basis 0, so it truncates first and the buttons wrap only when they alone no longer fit. Before, a narrow pane ran the buttons under the info panel, and the menu's positioned wrapper painted over it.

## ImageDetailPage labels panel and hotkeys

**ImageDetailPage labels panel and hotkeys**: `components/image/LabelsPanel.tsx` renders between `DetectionsPanel` and `GenerationMetadata`, mounted with ``key={`labels-${image.id}`}`` — **prefixed**, because `ProvenancePanel` is a sibling in the same children array and already uses a bare `image.id`, and two siblings sharing one key is a reconciliation error React does not warn about: it renders the panel twice. A third keydown effect binds the label hotkeys, gated on the `labelHotkeysEnabled` pref, the active pane, no modal, no modifier, **`!e.repeat`** (the guard the other two effects do not need — a held key would fire dozens of assigns) and `!isTextEntryTarget`, which is load-bearing here because the caption editor is a `<textarea>` on this page. Both pre-existing keydown effects now call the shared `utils/keyboard.ts::isTextEntryTarget` rather than each hand-rolling the INPUT/TEXTAREA/SELECT/contentEditable check. See `docs/dev/labels/ui.md` § Hotkeys.

## Gallery generation metadata

The gallery list never carries the JSON: `ImageListItem` has a `has_generation_metadata` boolean instead, computed in SQL by `list_images` (`_HAS_GENERATION_METADATA` in `routers/images.py`), because the JSON averages about 55 KB a row on ComfyUI output and a page of 100 shipped 5.6 MB the card never read (M9 stage 11). The flag is false for SQL NULL, for the JSON text `'null'` (what assigning `None` through the JSON type stores, so `IS NOT NULL` alone is wrong) and for `{}`. The list query defers the column with `raiseload=True` as a **query option**; it is not deferred on the model, which would be the `source_meta` trap. `ImageCard` shows a small accent `<Cpu>` icon button in the filename row when the flag is set; clicking it (without navigating) opens a page-level modal in `GalleryPage` that fetches `GET /images/{id}` under the detail page's own `["image", id]` key and renders `<GenerationMetadata>`. The same component appears in the right panel of `ImageDetailPage`, expanded by default. Covered by `backend/tests/test_gallery_list_meta_http.py` and the "gallery card opens generation info" journey in `frontend/e2e/comfy-run.spec.ts`.

## Source & license provenance

Images and datasets carry `source_name` / `source_url` / `license` / `attribution`, with NULL on the image meaning *inherit the dataset default*. The gallery reads the **effective** license (`ImageListItem.license`) for its badge and offers `license_filter` / `license_missing`; `ImageDetailPage` renders `components/image/ProvenancePanel.tsx`, and the selection toolbar's bulk action uses `components/gallery/SetProvenanceModal.tsx`. Ingest capture (import/rescan/upload), the derived-image rule, cross-dataset materialization are in `docs/dev/provenance.md`; the full API surface and the edit UI are in `docs/dev/provenance-api.md`.

## Style match & the DINOv2 layer breakdown

Two blocks below the flat scores grid, both about `style_similarity_score` and both owned by
`docs/dev/quality/style-match.md` § Making the score readable — read it before changing either.
What matters at this page's level:

- **`components/image/StyleMatchPanel.tsx`** replaced a bare `Style match 62%` row *inside*
  the scores grid. It mounts below the grid rather than in it because the grid is a
  two-column list of one-line facts and this needs three rows, a meter and a reference-
  thumbnail strip. It renders nothing when the image has no style score, keeps the raw
  cosine visible beside the percentile, and takes a `distribution` from
  `useStyleDistribution(datasetId)` — called here **unconditionally**, unlike on the gallery
  card, since the detail page is where a user comes to read this number and the gallery
  meter preference should not hide it. Same query key, so the two share one cache entry.
- **`DinoLayerBreakdown`** (local to `ImageDetailPage.tsx`) renders `dino_layer_scores` on a
  **fixed 0–1 axis**. It used to normalise each bar to the largest score within the image,
  which made one bar read 100% however poor the match. No layer is dimmed, and the `stored`
  marker follows the run's recorded `dino_layer` (none when the run is unrecorded) — see
  `docs/dev/quality/style-match.md` § `DinoLayerBreakdown` — the axis it used to lie about.

## AI generation metadata

Extracted at import time and on direct upload by `extract_generation_metadata`'s body in `backend/services/image_service.py`, run inside `decode_for_ingest` so it shares the ingest's one open of the file (`docs/dev/image-import.md` § Several files at once, one decode each). Stored in `Image.generation_metadata` (JSON column, nullable). Returned by `ImageOut`; `ImageListItem` carries only `has_generation_metadata` (§ Gallery generation metadata above).

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
