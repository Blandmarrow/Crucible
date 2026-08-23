# Image detail view: crop, caption panel & generation metadata

This file covers `ImageDetailPage` itself: the crop tool and crop+upscale, the selection toggle, the labels panel and its hotkeys, the caption panel, and AI generation-metadata extraction and display. The two pieces of state it shares with the gallery live next door — what `GalleryPage` remembers between visits (`gallery-state-*`, the debounce rules, Reset filters) is in `docs/dev/gallery-state.md`, and the id list the arrows walk (`gallery-nav-*`, `injectNavId`/`paneGo`, `atEnd`, the post-delete re-derivation, the live refresh and the live total) is in `docs/dev/gallery-nav.md`. The gallery grid itself is in `docs/dev/gallery.md`.

## ImageDetailPage crop tool

**ImageDetailPage crop tool**: Two output modes controlled by the **Replace** checkbox. *New file* (default) — creates a new `Image` record (filename `{source_stem}_crop{ext}`, collision-handled via `unique_filename_with_thumb`) and navigates to it on success. *Replace* — overwrites the source file in-place, updates the existing `Image` record (width, height, file_size_bytes, format, phash), records the edit through `images._record_in_place` (a module-level alias for `utils.record_in_place`, the single writer of `processing_history` for every in-place overwrite in that router — video re-extraction reads it as its skip guard, see `docs/dev/video-reextract.md` — and of `scores_stale`, which it sets only when the row already carries a score), regenerates the thumbnail **in a post-commit epilogue** (a `generate_thumbnail` failure is logged and the crop still lands; running it before the commit rolled the row back onto the already-replaced file, PM-013), and stays on the same image. The aspect dropdown and zoom slider control the crop selection shape and size; W×H inputs control the output pixel dimensions (resize-after-crop, independent of the selection). When both W and H are filled in, the crop box aspect ratio automatically locks to W/H. The crop endpoint (`POST /images/{id}/crop`) accepts `replace: bool = False`; in replace mode it calls `protect_file_before_overwrite` before touching the file. New-file mode uses `asyncio.get_running_loop()` and a targeted `LIKE '{stem}%'` query for collision detection (not a full dataset scan).

## Crop + upscale

**Crop + upscale**: an optional upscale model selector (shown when upscale models are configured) makes the crop atomic with an upscale in either mode: the crop is saved to a temp file, a `crop_upscale` background job runs the upscale, and the endpoint returns `{job_id}` instead of the image dict. The frontend branches on `"job_id" in data` to distinguish the async path. Because the upscaler writes through `normalize_image_format`, a crop+upscale of a `.bmp`/`.gif`/`.tiff`/`.avif` lands at a `.png` path: new-file mode reserves the name under that extension and both workers take the row and the thumbnail from the written file, while **replace + upscale answers 409** ("… writes `{name}`, which already exists on disk") if an unregistered file is already sitting at the fallback path. One image per request means it can refuse before touching anything, unlike the batch jobs that skip and continue — see `docs/dev/image-processing-models.md` § Upscaling. The crop mutation surfaces the server's `detail` through `apiErrorDetail`, so that message reaches the toast.

Only the **replace** worker (`_run_crop_upscale_replace`) writes `result_data` — `{processed, thumbnails_stale}` — and it writes it *inside* its `async with AsyncSessionLocal()` block, because it emits its own terminal `completed` event afterwards and `TopBar`'s completion branch fetches the job row the moment that event lands (`docs/dev/frontend-jobs.md` § The stale-thumbnail warning). The asymmetry is deliberate: new-file mode has no post-commit thumbnail epilogue to report on — it creates a fresh row whose thumbnail is cut on the write path, so a failure there fails the job outright rather than leaving a stale tile behind.

## ImageDetailPage selection

**ImageDetailPage selection**: A **Select / Selected** toggle button sits in the top toolbar (right of the filename, before the Boxes button). It calls `selectionStore.toggle(imageId, datasetId)` and reflects `isSelected(imageId)` via a targeted selector. Pressing **Space** anywhere on the page (except when a text field is focused or a modal is open) does the same thing — handled in the arrow-key `useEffect` alongside ArrowLeft/ArrowRight; `showDetectModal` is additionally checked. The button is styled `btn-primary` + `CheckSquare` icon when selected, `btn-ghost` + `Square` when not.

## ImageDetailPage labels panel and hotkeys

**ImageDetailPage labels panel and hotkeys**: `components/image/LabelsPanel.tsx` renders between `DetectionsPanel` and `GenerationMetadata`, mounted with ``key={`labels-${image.id}`}`` — **prefixed**, because `ProvenancePanel` is a sibling in the same children array and already uses a bare `image.id`, and two siblings sharing one key is a reconciliation error React does not warn about: it renders the panel twice. A third keydown effect binds the label hotkeys, gated on the `labelHotkeysEnabled` pref, the active pane, no modal, no modifier, **`!e.repeat`** (the guard the other two effects do not need — a held key would fire dozens of assigns) and `!isTextEntryTarget`, which is load-bearing here because the caption editor is a `<textarea>` on this page. Both pre-existing keydown effects now call the shared `utils/keyboard.ts::isTextEntryTarget` rather than each hand-rolling the INPUT/TEXTAREA/SELECT/contentEditable check. See `docs/dev/labels.md`.

## ImageDetailPage caption panel

**ImageDetailPage caption panel**: Contains only the caption text textarea and Save button (plus the collapsible AI Generate section). The `caption_style` field is still present in the DB schema, the `PUT /captions/image/{id}` endpoint, and the save mutation — read from `captionData` and re-persisted unchanged — but no style picker is exposed in the UI. The `tags_json` column and `tags` table were removed by migration `a8c3e1f2b9d0_drop_tags_system`; `TagEditor.tsx` and the panel's `tags` state are deleted accordingly. A live **token counter** (`N words · N tokens`) is displayed right-aligned beside the "Caption Text" label. The token count comes from `utils/tokenCount.ts::useTokenCount`, which lazily one-time `import()`s the GPT-2 (`r50k_base`) encoder from `gpt-tokenizer` so that sizeable encoder chunk stays out of the page's static graph; this also aligns the client count with the backend `caption_token_count` (`backend/utils.py::count_caption_tokens`, a GPT-2 tiktoken encoder). The word count shows immediately; tokens render as `…` until the encoder resolves, then recount as `captionText` changes. The counter turns amber at ≥ 70 tokens and red at ≥ 77 to signal the CLIP truncation limit.

**Caption textarea auto-resize**: The textarea auto-expands to fit its content via a `useEffect` keyed on `[captionText, imageId, image?.id]`. Three dependencies are required: (1) `captionText` — resize when the text changes; (2) `imageId` — resize immediately on navigation (covers same-text-on-two-images edge case); (3) `image?.id` — the critical one: the component has an early return `if (imageLoading || !image) return <Loading/>`, so the textarea is not in the DOM while the image query is pending. `captionData` (the lighter query) typically resolves before `image`, so `captionRef.current` is null when `captionText` first changes on a fresh navigation. Adding `image?.id` ensures the resize fires again once the loading phase ends and the ref becomes valid. Do not remove any of these three dependencies. A separate `useEffect` on `[imageId]` resets `captionDirty` to `false` on navigation; without this, an unsaved edit would leave `captionDirty=true` on the next image, causing the `captionData` effect to skip `setCaptionText` entirely (blocking the resize).

The **AI Generate** collapsible (`showAi` state, gated `enabled: showAi`) uses the same four-model-type picker pattern and `resolveModelId` helper as `SelectionToolbar`. WD14 models show only the threshold slider and hide `PromptPresetManager` and `ResolutionPicker` (both are wrapped in `{aiModel && !aiModel.startsWith("wd14:") && (...)}` — keep this consistent with `SelectionToolbar`'s ternary). An `aiOverwrite: bool` state (default `true`) is exposed as a checkbox that appears once a model is selected. The `["captioning-models"]` query is defined at component level (not inside the collapsible) but gated on `enabled: showAi` to avoid loading until the section is first opened.

## Gallery generation metadata

`generation_metadata` is included in both `ImageOut` and `ImageListItem` backend schemas, so it comes back with the gallery list response. `ImageCard` shows a small accent `<Cpu>` icon button in the filename row when `image.generation_metadata` is set; clicking it (without navigating) opens a page-level modal in `GalleryPage` that renders `<GenerationMetadata>`. The same component appears in the right panel of `ImageDetailPage`, expanded by default.

## Source & license provenance

Images and datasets carry `source_name` / `source_url` / `license` / `attribution`, with NULL on the image meaning *inherit the dataset default*. The gallery reads the **effective** license (`ImageListItem.license`) for its badge and offers `license_filter` / `license_missing`; `ImageDetailPage` renders `components/image/ProvenancePanel.tsx`, and the selection toolbar's bulk action uses `components/gallery/SetProvenanceModal.tsx`. Ingest capture (import/rescan/upload), the derived-image rule, cross-dataset materialization and the full API surface are in `docs/dev/provenance.md`.

## Style match & the DINOv2 layer breakdown

Two blocks below the flat scores grid, both about `style_similarity_score` and both owned by
`docs/dev/image-similarity.md` § Making the score readable — read it before changing either.
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
  which made one bar read 100% however poor the match; the layers-1–9 de-emphasis and the
  layer-12 `stored` marker landed in the same change.

## AI generation metadata

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
