# Batch Editing & Image Processing

Operations that act on many images at once, and the ML-based image processing tools.

Available from: the selection toolbar in the gallery, the **Bulk Edit** sidebar item, and the Image Detail page toolbar.

## Batch Operations

Select any images in the gallery (checkbox click), **shift+click** a checkbox to extend the selection as a contiguous range (re-shift-clicking replaces the previous range without affecting independently-selected images outside it), or use **Space** while viewing an image in the detail view. Selections persist across dataset navigation — the selection toolbar and every action modal show a **per-dataset badge breakdown** so you can always see which datasets your selected images come from (images from a dataset other than the current one are highlighted in amber as a warning).

Bulk score, upscale, LUT, detect, detection-crop, and rename operations all support a **quality flag exclusion** filter to skip flagged images without deleting them.

- **Batch caption** — run any captioning model on the selection with all the same options as the full-dataset run
- **Batch caption pipeline** — run a multi-step captioning pipeline on the selection (same as the full-dataset pipeline, scoped to selected images)
- **Batch score** — run technical, aesthetic, watermark, NSFW, CLIP embedding, DINOv2 embedding, and/or DINOv2 per-layer embedding scoring on the selection; includes a collapsible style-similarity section to score cosine similarity against reference images (scoped to the selection)
- **Batch upscale** — upscale selected images using any installed upscale model
- **Batch LUT** — apply a LUT to selected images with a chosen intensity
- **Batch detect** — run Florence-2 object detection or phrase grounding, NudeNet body-part detection, or Grounded SAM 2.1 / SAM 3 text-prompt segmentation on the selection
- **Crop to detected subject** — batch-crop selected images to their detection boxes with padding and aspect-ratio snap → [details](detection.md#crop-to-detected-subject)
- **Batch crop** — crop selected images to a target aspect ratio (center, top-left, or custom anchor)
- **Batch resize** — resize the longest side of selected images to a target pixel count (downscale only)
- **Caption find-replace** — regex-capable search-and-replace across caption text for a whole dataset or a selection
- **Merge tags** — drop redundant tags within each selected caption (e.g. `tail` when `long tail` is present) and collapse exact duplicates; also available per-image from the detail view → [details](tag-consolidation.md)
- **Bulk rename** — rename matching images to a sequential base name pattern (`{slug}_001.ext`, `_002`, …)
- **Bulk delete** — remove selected images from the dataset and disk

## Image Processing

### Upscaling

ML-based image upscaling via the [`spandrel`](https://github.com/chaiNNer-org/spandrel) library, which auto-detects architecture from model files:
- Supported architectures: RealESRGAN/RRDB, SwinIR, HAT, OmniSR, and more (anything spandrel recognises)
- Place `.pth` or `.safetensors` model files in `models/upscale_models/` — or point `UPSCALE_MODELS_DIR=` in `.env` at an existing models folder
- Two output modes: **Replace** (overwrites source image, updates DB record) or **New file** (`{stem}_upNx{ext}`, creates a new DB record)
- Optional target width × height — upscales first, then resizes down to fit, preserving aspect ratio

Available from: the **Upscale** button in the ImageDetailPage toolbar, the **Upscale** modal in SelectionToolbar, and the **Upscale** tab on the Bulk Edit page.

### LUT Color Grading

Apply 3D colour look-up tables (`.cube` or `.3dl`) to images:
- Adjustable blend intensity (0.0 – 1.0) — 0 = original, 1 = full LUT applied
- Place LUT files in `models/lut/` — or set `LUT_MODELS_DIR=` in `.env`
- Same **Replace** / **New file** output modes as upscaling

Available from: the **LUT** button in the ImageDetailPage toolbar (mutually exclusive with Crop and Upscale), the **LUT** modal in SelectionToolbar, and the **Apply LUT** tab on the Bulk Edit page.

### Crop & Resize

- **Crop** — by default creates a new image record (non-destructive); toggle **Replace** to overwrite the source instead; choose aspect ratio, anchor point, and optional output pixel dimensions; supports atomic crop + upscale in one step
- **Resize** — downscale the longest side of selected images to a target pixel count (original untouched)
