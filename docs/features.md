# Features

## Datasets & Gallery

- Create multiple named datasets, each pointing to a folder of images
- Edit datasets — rename (folder is moved on disk and all image paths are updated automatically), update the description, or assign a **category**
- **Sort** the dataset list by: Newest / Oldest / Recently updated / Name A→Z / Name Z→A / Most images / Fewest images / Largest / Smallest / Most captioned %
- **Category groups** — assign datasets to named categories; the page switches from a flat grid to collapsible folder sections. Rename or delete a category (batch-updates all datasets in it) by hovering the section header
- **Duplicate** a dataset — deep-copies all images, captions, subfolders, and metadata into a new dataset as a background job; optionally duplicate from a specific version snapshot instead of the current on-disk state
- Gallery view with search (filename or caption text), pagination, and sort
- Filter by caption status, quality flags, score ranges (multi-chip — add any number of field + min/max conditions combined as AND), aspect ratio, file size, format, and detected object label
- Drag-and-drop image files onto the gallery to add them to the dataset; a live progress bar shows how many files have been processed, and the counter persists in the top bar if you navigate away mid-upload
- Organize images into subfolders (logical groupings — images stay flat on disk); move or copy images or entire subfolders to a different dataset in one operation
- Per-image detail view with metadata, caption editor, and crop/rotate tools
- **Generation Metadata** — PNG metadata from AUTOMATIC1111 and ComfyUI workflows is extracted at import and displayed per-image: prompt, negative prompt, model, sampler, steps, CFG scale, seed, VAE, size, and optional raw ComfyUI workflow JSON

## Object Detection

Run Florence-2 bounding-box detection on any selection of images as a background job.

Two detection tasks:

| Task | Description |
|---|---|
| **Object Detection** (`<OD>`) | Fixed-vocabulary detection — finds categories the model was trained on, no prompt needed |
| **Phrase Grounding** (`<CAPTION_TO_PHRASE_GROUNDING>`) | Draws boxes around noun phrases in a text prompt; use "Use caption as prompt" to automatically ground each image's own caption |

Results are shown in the **DETECTIONS** panel on the Image Detail page:
- Label chips with per-label counts
- SVG overlay on the image with per-label colour coding
- Click any label chip to toggle its boxes on/off
- Eye icon in the toolbar hides/shows all boxes at once

Available from the **Detect** button in the SelectionToolbar, and from the Object Detection section on the Captioning page when a Florence-2 model is selected.

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

## Batch Operations

Select any images in the gallery (or use **Space** while viewing an image in the detail view) to add them to the selection. Selections persist across dataset navigation — the selection toolbar and every action modal show a **per-dataset badge breakdown** so you can always see which datasets your selected images come from (images from a dataset other than the current one are highlighted in amber as a warning).

- **Batch caption** — run any captioning model on the selection with all the same options as the full-dataset run
- **Batch score** — run technical, aesthetic, watermark, CLIP embedding, DINOv2 embedding, and/or DINOv2 per-layer embedding scoring on the selection; includes a collapsible style-similarity section to score cosine similarity against reference images (scoped to the selection)
- **Batch upscale** — upscale selected images using any installed upscale model
- **Batch LUT** — apply a LUT to selected images with a chosen intensity
- **Batch detect** — run Florence-2 object detection or phrase grounding on the selection
- **Batch crop** — crop selected images to a target aspect ratio (center, top-left, or custom anchor)
- **Batch resize** — resize the longest side of selected images to a target pixel count (downscale only)
- **Caption find-replace** — regex-capable search-and-replace across caption text for a whole dataset or a selection
- **Bulk rename** — rename matching images to a sequential base name pattern (`{slug}_001.ext`, `_002`, …)
- **Bulk delete** — remove selected images from the dataset and disk

## Statistics Dashboard

- 14+ interactive histograms: aesthetic, blur, noise, uniformity, color, saturation, watermark, megapixels, file size, aspect ratio, caption length, caption token distribution, style similarity, quality flags
  - **Caption token distribution** uses GPT-2 BPE tokenisation and highlights captions that exceed CLIP's 77-token truncation limit
- Editable histogram bucket edges — rebucketing runs entirely client-side against raw score arrays
- Top-500 tag frequency chart and tag co-occurrence matrix
- Click any histogram bar or quality flag card to open a filtered thumbnail grid
- A gear icon in the page header opens a settings drawer to toggle individual histogram panels on/off; visibility state is persisted per-browser
- All histograms and charts can be scoped to a specific subfolder via a dropdown in the page header
- **Export Stats CSV** — downloads a key-value CSV of all dataset statistics: summary fields, file-size percentiles, quality flag counts, score coverage, mean scores, and every histogram distribution; button is disabled while score data is still loading
- **Export Tags CSV** — downloads a tabular CSV (`tag,count,category`) of all tag frequencies (up to 500 tags); disabled when no tags exist

## File Browser

A three-panel filesystem explorer built into the app:

- Left panel: drive roots + quick-access shortcut to the datasets folder
- Centre panel: breadcrumb navigation, sortable file list (name / size / date), images-only toggle, context menu (rename / delete / import into dataset)
- Right panel: image preview + dimensions/format/size metadata + generation metadata (A1111 / ComfyUI)
- Create folders, rename files and directories, delete items (syncs DB records automatically)
- Import any folder of images directly into an existing dataset without leaving the browser

## Settings

Route: `/settings` — accessible from the sidebar. Settings are grouped into five tabs.

**Gallery** — browser-local preferences, each taking effect immediately:
- Images per page: 25 / 50 / 100 / 200 — controls gallery pagination and detail-view prefetch; lower values reduce memory usage with large high-resolution datasets
- Subfolder rename on move: *Rename to subfolder name* (default) or *Keep original filenames* — when disabled, moving images to a subfolder updates their subfolder metadata only, without renaming the files

**UI Behavior** — browser-local preferences, each taking effect immediately:
- Default-focused button in destructive confirmation dialogs: *Cancel* (safe default) or *Confirm* (faster workflows)
- Branch snapshot behavior: *Ask* (shows a prompt before checkout or branch creation, letting you choose whether to create a snapshot) or *Auto* (always creates snapshots without prompting)

**Quality Thresholds** — five configurable number inputs (require Save; changes apply to the next scoring run only):

| Setting | Controls |
|---|---|
| Blur threshold | Laplacian variance cutoff for `is_blurry` (default 100) |
| Noise threshold | Smooth-region std dev cutoff for `is_noisy` (default 15) |
| Uniformity threshold | Grayscale std dev cutoff for `is_uniform` (default 12) |
| Watermark threshold | CLIP zero-shot score cutoff for `has_watermark` (default 0.6) |
| Duplicate threshold | pHash Hamming distance cutoff for `is_duplicate` (default 8) |

**Versioning** — version control mode (Off / Manual / Auto; see [Dataset Versioning](versioning.md)) plus branch snapshot behavior. Requires Save for the version control mode.

**LLM Providers** — add, edit, and delete OpenAI-compatible API provider configurations for use as captioning backends:
- Name and Base URL are required; API key is optional (leave blank for local servers)
- Default model — selected from a hardcoded preset list for well-known cloud APIs (Gemini, Groq, OpenAI, Together.ai), or fetched live from local servers (LM Studio, llama.cpp) via a refresh button, or typed freely
- Max image resolution (128–4096 px) — images are JPEG-encoded at this size before being sent
- Max tokens — controls the length of generated captions (64–32768)

## Booru Tag Lookup

Search booru image boards for tag vocabulary when building tag lists for your training subjects:

- Searches **Safebooru** (SFW) or **Gelbooru** (requires API key + user ID in `.env`)
- Shows tag name, category (character / artist / copyright / general / meta), and post count
- Configurable result limit (20 / 50 / 100); results cached for 5 minutes
- Copy individual tags or the full list to clipboard

## Split View

Split the main content area into two independently operating panes:

- Toggle via the **Columns** icon in the top-right toolbar
- Split any pane horizontally or vertically with the split buttons in the pane header, split panes can be split again
- Each pane has its own page selector and dataset selector — run Gallery in one pane and Stats in another, for example
- Drag the resize handle between panes to adjust the split ratio
- Close all panes to return to single-view
