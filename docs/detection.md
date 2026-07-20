# Object Detection

Locate and segment objects in your images — to crop to a subject, to build loss masks for
training, or to find watermarks. Four models are available: Florence-2 (bounding boxes),
NudeNet (body parts), Grounded SAM 2.1, and SAM 3 (segmentation masks).

Available from: the **Detect** button in the gallery selection toolbar, the Image Detail page,
and the **Detections** tab on the Bulk Edit page.

Detection runs in the background: the **Detect** dialog closes as soon as the job is queued (progress shows in the global job bar at the top), so you can immediately open it again and queue another run — jobs run one after another. This works on both the gallery **Detect** button and the Image Detail page.

## Florence-2 (bounding boxes)

| Task | Description |
|---|---|
| **Object Detection** (`<OD>`) | Fixed-vocabulary detection — finds categories the model was trained on, no prompt needed |
| **Phrase Grounding** (`<CAPTION_TO_PHRASE_GROUNDING>`) | Draws boxes around noun phrases in a text prompt; use "Use caption as prompt" to automatically ground each image's own caption |

Available from the **Detect** button in SelectionToolbar, and from the Object Detection section on the Captioning page when a Florence-2 model is selected.

## NudeNet (body-part detection)

ONNX-based body-part detector producing labelled bounding boxes (exposed skin regions, clothing, etc). CPU-only, no GPU required. A **Min confidence** slider controls the detection threshold (default 0.5).

## Grounded SAM 2.1 (segmentation masks)

Two-stage pipeline: **Grounding DINO** localises objects from a text description → **SAM2** produces a precise pixel-level segmentation mask for each detected region.

| Mode | Description |
|---|---|
| **Text prompt** | Describe what to segment; separate multiple targets with commas (e.g. `face, hand, watermark`) — one run detects them all, and each result is labelled with its phrase |
| **Point prompts** | Use the **SAM Points** toolbar button on the image detail page to place foreground points (left-click) and background points (right-click), then run |

Mask outputs are rendered as semi-transparent polygon fills on the SVG overlay in addition to bounding boxes.

The **DINO box confidence** threshold (Settings → Quality Thresholds, default 0.35) controls how confident Grounding DINO must be before passing a detected region to SAM2 — lower values return more detections, higher values return fewer but more precise ones.

## SAM 3 (text-prompt segmentation)

Native open-vocabulary segmentation: type a phrase (e.g. `a person's face`) and SAM 3 finds and masks **every instance** of that concept in one pass — no separate detector stage, and typically better recall than the Grounding DINO → SAM2 pipeline for concept prompts. Separate several phrases with commas (e.g. `face, hand, watermark`) to detect them all in one run, each labelled with its phrase. Text prompt only (point prompts remain a SAM2 feature).

The **SAM 3 confidence** threshold (Settings → Quality Thresholds, default 0.5) controls the minimum instance confidence for a mask to be kept.

SAM 3 requires two manual setup steps (both offered by `manage` setup/update):
1. The `sam3` package: `pip install git+https://github.com/facebookresearch/sam3.git`
2. The checkpoint: download `sam3.safetensors` (~3.4 GB) from [1038lab/sam3](https://huggingface.co/1038lab/sam3) into `models/sam3/`. Only safetensors checkpoints are supported.

**On Windows**, SAM 3 also needs `triton`, which PyTorch does not ship there: `pip install triton-windows`. Setup/update installs it for you; without it SAM 3 fails to load with `ModuleNotFoundError: No module named 'triton'`. Linux installs already include it via PyTorch.

## Viewing results

Results are shown in the **DETECTIONS** panel on the Image Detail page:
- Label chips with per-label counts
- SVG overlay on the image with per-label colour coding (filled polygon masks for SAM2/SAM3, bounding boxes for all models)
- Click any label chip to toggle its boxes/masks on/off
- Eye icon in the toolbar hides/shows all detections at once

The detection modal includes an **Overwrite existing detections** toggle (on by default) — uncheck to add new detections without clearing prior results. Re-running a model now only replaces **that model's** detections, so results from other models — and any hand-made detections — survive a re-run.

> **Note on rotated images:** detections are now computed in the same EXIF-corrected frame the rest of the app uses, so overlays line up with the subject on photos that carry an EXIF orientation tag. Detections produced *before* this fix on such rotated images may be misaligned — re-run detection on those images to correct them.

## Locating watermarks

The batch **watermark** score (Quality → Batch score) flags *that* an image has a watermark but not *where* it is. To find the region, run a text-prompt detection (SAM 2, SAM 3, or Florence-2 Grounded Caption) with a watermark prompt such as `watermark. text. logo.` — the located boxes appear on the Image Detail overlay like any other detection, with the same delete/relabel tools.

When one of those grounding tasks is selected, the detect modal shows a **Sync watermark flag from results** checkbox. With it on, the run updates each scanned image's watermark flag from its own result: images where a region was found are flagged, and images scanned clean have the flag cleared. Only images actually scanned are touched — an image whose inference fails keeps its previous flag, and (with caption-as-prompt grounding) uncaptioned images are left alone. Because the flag reflects the run that just finished, re-running with a better prompt corrects earlier mistakes. The Statistics watermark counts refresh when the run completes. A typical loop: filter the gallery to watermarked images → detect with sync → review the overlay → [exclude the watermark region from the loss mask](export.md#loss-masks-masked-training-loss) on export.

## Managing detections

The DETECTIONS panel lists every detection as an editable row so you can fix a bad run without re-running everything:

- **Rename** — click a detection's label to edit it inline (Enter saves, Esc cancels)
- **Delete** — the trash icon removes a single detection immediately (detections are regenerable, so there's no confirm)
- **Merge** — tick the checkboxes on two or more rows and click **Merge N → {label}** to combine them into one detection (the merged box covers all of them, and their masks are unioned); the result takes the first-selected row's label
- **Draw a box by hand** — the **Draw Box** toolbar button lets you drag a rectangle on the image, type a label, and add it as a detection. Tick **Refine with SAM** to have SAM 2 turn the box into a precise mask (GPU host only); without it the plain box is stored. Draw mode stays active so you can annotate several boxes in a row. Press Esc to exit.
- **Refine a mask** — the **Refine** button on any masked detection lets you click foreground points (left-click) and background points (right-click) to correct the mask, then **Apply** re-segments it with SAM 2 (GPU host only)
- **Crop from Detections** — seeds the crop tool with a box around the currently-visible detections (padded), ready to adjust and confirm

Hand-drawn, refined, and merged detections are tagged as `manual` so they're never wiped by an automatic re-run.

## Running & bulk-deleting detections

The **Detections** tab on the Bulk Edit page has two panels, both scoped by the page's shared Scope selector (all images / a subfolder / images without chosen quality flags / the current selection):

- **Run Detection** — run any detection model across the scope without leaving the page, the same way the gallery **Detect** dialog does (Florence-2, NudeNet, Grounded SAM 2.1 with a text prompt, or SAM 3; SAM 2 point prompts stay per-image on the detail page). Pick the model and prompt, optionally sync the watermark flag, and queue it — you can queue several runs back to back.
- **Delete Detections** — deletes detections across many images at once (the images themselves are untouched). Filter by detection **label**, by the **model** that produced them, and/or by a **score below** threshold (unscored and hand-made detections never match a score filter). A live count shows how many detections match before you commit, and a confirmation dialog guards the delete.

## Crop to detected subject

Batch-crop images to their detections — useful for turning full scenes into tight subject crops before training:

- **Detection labels** — chips select which labels drive the crop (none selected = all labels); images without a matching detection are skipped and reported
- **Crop box** — *Union of all matches* (one box covering every matching detection) or *Largest match* (the single biggest detection)
- **Padding %** — expands the box by a percentage of its own size on each side
- **Aspect ratio** — optional grow-only snap: the crop expands (never shrinks) toward the chosen ratio, clamped to the image; if the subject can't fit any rect of that ratio, the ratio bends rather than cutting the subject
- Same **Replace** / **New file** (`{stem}_crop{ext}`) output modes as upscaling. In **New file** mode you can also choose a **destination subfolder** for the cropped copies — *Same as source* (default), the dataset root, an existing subfolder, or a new one you name — making it easy to keep crops separate from originals (the choice is a logical label; no files are moved)
- A completion toast reports how many images were cropped, skipped (no detections), unchanged (detection already spans the full image), or failed

Available from: the **Crop** button in SelectionToolbar, the **Crop to Subject** tab on the Bulk Edit page, and the **Crop to Subject** button in the Image Detail page toolbar next to Crop/Upscale/LUT (single image; shown only when the image has detections, and its label chips show only that image's labels). When a crop **replaces** the image (any replace-mode crop — manual, replace + upscale, batch aspect crop, or Crop to Subject), the image's existing detections are automatically remapped into the new crop frame so their boxes/masks still line up; detections that fall entirely outside the crop are dropped. This remap is **geometric, not label-scoped**: the label you crop to only picks the crop rectangle — afterwards *every* detection that still overlaps the crop is kept, not just the one you cropped to. A whole-image detection (e.g. a `background` mask covering `[0,0]–[1,1]`) therefore survives as a full-frame detection on the crop; delete it by hand if you don't want it (detections are regenerable). **New-file** crops instead leave the original and its detections untouched and create a fresh image with **no** detections — re-run detection on it if you need boxes.
