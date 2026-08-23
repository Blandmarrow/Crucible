# Batch Editing & Image Processing

Operations that act on many images at once, and the ML-based image processing tools.

Available from: the selection toolbar in the gallery, the **Bulk Edit** sidebar item, and the Image Detail page toolbar.

## Batch Operations

Select any images in the gallery (checkbox click), **shift+click** a checkbox to extend the selection as a contiguous range (re-shift-clicking replaces the previous range without affecting independently-selected images outside it), or use **Space** while viewing an image in the detail view. Selections persist across dataset navigation — the selection toolbar and every action modal show a **per-dataset badge breakdown** so you can always see which datasets your selected images come from (images from a dataset other than the current one are highlighted in amber as a warning).

Bulk score, upscale, LUT, detect, detection-crop, watermark-removal, thumbnail regeneration, and rename operations all support a **quality flag exclusion** filter to skip flagged images without deleting them.

- **Batch caption** — run any captioning model on the selection with all the same options as the full-dataset run
- **Batch caption pipeline** — run a multi-step captioning pipeline on the selection (same as the full-dataset pipeline, scoped to selected images)
- **Batch score** (the **Score** button) — run technical, aesthetic, watermark, CLIP embedding, DINOv2 embedding, and/or DINOv2 per-layer embedding scoring on the selection; includes a collapsible style-similarity section to score cosine similarity against reference images (scoped to the selection) → [details](scoring.md)
- **Batch upscale** — upscale selected images using any installed upscale model
- **Batch LUT** — apply a LUT to selected images with a chosen intensity
- **Batch detect** — run Florence-2 object detection or phrase grounding, NudeNet body-part detection, or Grounded SAM 2.1 / SAM 3 text-prompt segmentation on the selection
- **Crop to detected subject** — batch-crop selected images to their detection boxes with padding and aspect-ratio snap → [details](detection.md#crop-to-detected-subject)
- **Remove watermark** (the **Remove WM** button) — paint the selection's detected regions out of the images entirely, instead of cropping them off → [details](#watermark-removal)
- **Caption find-replace** — regex-capable search-and-replace across caption text for a whole dataset or a selection
- **Merge tags** — drop redundant tags within each selected caption (e.g. `tail` when `long tail` is present) and collapse exact duplicates; also available per-image from the detail view → [details](tag-consolidation.md)
- **Set source/license** — write source name, source URL, license, and attribution across the selection. Each field has its own mode: **Keep** (leave it alone), **Set** (write this value), or **Inherit** (clear it so the image follows its dataset default) → [details](provenance.md)
- **Re-extract frames** — re-cut selected video frames from their source at full resolution, replacing the triage-sized files in place → [details](video.md)
- **Bulk rename** — rename matching images to a sequential base name pattern (`{slug}_001.ext`, `_002`, …)
- **Regenerate thumbnails** — re-cut the small preview the gallery draws for every image in the scope. Use it when an upscale, LUT, crop or frame re-extraction reported that some previews are out of date: those images are already correct on disk, only the preview is stale. Available from the **Thumbnails** tab on the Bulk Edit page and the **Thumbnails** button in the selection toolbar. If the disk is too full to write them, the run is refused up front with a message rather than failing image by image
- **Bulk delete** — remove selected images from the dataset and disk

## Image Processing

### Upscaling

ML-based image upscaling via the [`spandrel`](https://github.com/chaiNNer-org/spandrel) library, which auto-detects architecture from model files:
- Supported architectures: RealESRGAN/RRDB, SwinIR, HAT, OmniSR, and more (anything spandrel recognises)
- Place `.pth` or `.safetensors` model files in `models/upscale_models/` — or point `UPSCALE_MODELS_DIR=` in `.env` at an existing models folder
- **1× restoration models** (denoise, deblur, JPEG-artifact removal, descreen) work here too — they clean the image up at its original size instead of enlarging it. The model dropdown labels them `(1× restore)`; anything else shows its scale, e.g. `(4×)`
- Two output modes: **Replace** (overwrites source image, updates DB record) or **New file** (creates a new DB record, named `{stem}_upNx{ext}` — or `{stem}_1x{ext}` for a 1× restoration model, and `{stem}_upscale{ext}` when the scale cannot be read from the model's filename)
- Optional target width × height — upscales first, then resizes down to fit, preserving aspect ratio
- BMP, GIF, TIFF and AVIF cannot be written back, so the result is saved as **PNG** and the image is renamed to match — same as LUT grading. If a file of that name is already sitting in the folder untracked, the image is skipped (the job carries on) rather than overwriting it
- The run reports how many images it upscaled, skipped and failed when it finishes. If it could not rebuild some of the small gallery previews — usually because the disk is full or the `thumbnails/` folder is read-only — the upscaled images are still correct and saved, and you get a separate warning saying how many previews are out of date. **Bulk Edit → Thumbnails** repairs them

Available from: the **Upscale** button in the ImageDetailPage toolbar, the **Upscale** modal in SelectionToolbar, and the **Upscale** tab on the Bulk Edit page.

### LUT Color Grading

Apply 3D colour look-up tables (`.cube` or `.3dl`) to images:
- Adjustable blend intensity (0.0 – 1.0) — 0 = original, 1 = full LUT applied
- Place LUT files in `models/lut/` — or set `LUT_MODELS_DIR=` in `.env`
- Same **Replace** / **New file** output modes as upscaling
- Like upscaling, the run reports how many images it graded, skipped and failed, and warns separately if some gallery previews could not be rebuilt — the graded images are still correct, and **Bulk Edit → Thumbnails** repairs the previews

Available from: the **LUT** button in the ImageDetailPage toolbar (mutually exclusive with Crop and Upscale), the **LUT** modal in SelectionToolbar, and the **Apply LUT** tab on the Bulk Edit page.

### Watermark removal

Paints a detected region out of the image and fills it in with plausible surroundings, using the [LaMa](https://github.com/advimman/lama) inpainting model. Use it when the watermark sits in the middle of the picture and cropping it off would take the subject with it.

**It is a two-step flow, and the first step is detection.** This tool does not look for anything — it paints out what a detection run already found:

1. Run a detection pass over the images: **Detect** in the selection toolbar (or the Detections panel on a single image), using SAM 3 or Grounded SAM 2.1 with the text prompt `watermark`, or Florence-2 phrase grounding. Tick **sync watermark flag** so the flagged images are easy to find afterwards.
2. Check what it found, then run **Remove Watermark**. Leave the label chips unselected to paint out every detection, or pick specific labels.

Options and behaviour:
- **Mask padding px** (default 6) grows the painted area. Semi-transparent watermarks bleed past the edge a segmentation model draws, so if a faint halo survives the first run, raise this and run it again
- Two output modes: **Replace original** (the default here — the point is usually to fix the image) or **New file**, which writes a `{stem}_nowm` copy and leaves the original untouched
- On success the consumed detections are **deleted** and the image's watermark flag is cleared — the region they named no longer contains anything. In New file mode nothing is consumed: the original still has the watermark, so it keeps its detections and its flag
- The image is marked **scores stale**, because its watermark and quality scores were measured against pixels that no longer exist. The old numbers stay visible but flagged until you re-run scoring
- **The first run downloads the model** — about 196 MB, into `models_cache/`. Progress shows in the job pill. On a CPU-only machine expect seconds to a minute or so per image; on a GPU it is fast
- BMP, GIF, TIFF and AVIF cannot be written back, so the result is saved as **PNG** and the image renamed to match — same as upscaling and LUT grading
- Like those, the run reports how many images it painted, skipped and failed, and warns separately if some gallery previews could not be rebuilt

Available from: the **Remove Watermark** button in the Image Detail page toolbar (shown once the image has detections) and the **Remove WM** modal in the selection toolbar.

### Crop

- **Crop** — by default creates a new image record (non-destructive); toggle **Replace** to overwrite the source instead; choose aspect ratio, anchor point, and optional output pixel dimensions; supports atomic crop + upscale in one step. Replace + upscale on a BMP/GIF/TIFF/AVIF saves as PNG (above); it is refused with a message naming the file if something untracked already occupies that name
