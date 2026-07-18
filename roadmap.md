# Object Detection & Masks — Roadmap

Working plan for the detection/masks improvement arc. Kept up to date as iterations
land; review and adjust between iterations. Branch: `feature/detection-masks`.

**Last updated:** 2026-07-18

## Motivation

Detection/masks is the subsystem with the largest gap between data produced and value
extracted. SAM2/GDINO, Florence-2, and NudeNet produce boxes and polygon masks, but
downstream they only render as overlays and drive one gallery filter. Masks feed
neither export nor cropping; individual detections cannot be corrected or deleted.
This roadmap closes that gap: better detection first (SAM 3), then consumers for the
data (mask export, cropping, refinement).

## Status legend

`[ ]` planned · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Iteration 1 — SAM 3 as a selectable detection model  `[x]`

Add SAM 3 alongside SAM2+GDINO / Florence-2 / NudeNet, selectable per detection run.
SAM 3 does native open-vocabulary text-prompt segmentation (all instances of a phrase
in one pass), replacing the two-stage GDINO → SAM2 pipeline for text mode.

### Decisions (agreed 2026-07-18)

- **SAM 3.0 checkpoint now, 3.1 when upstream unblocks.** The official
  `sam3.1_multiplex.pt` checkpoint uses an internal Meta key-naming scheme and does
  **not** load through any public `build_*` code path for images
  ([facebookresearch/sam3#526](https://github.com/facebookresearch/sam3/issues/526),
  still open as of 2026-07-18; only the video predictor works). SAM 3.0 (`sam3.pt`)
  is confirmed to load cleanly, and 3.1's improvements are video-tracking focused
  (Object Multiplex, VOS) — marginal for our image-only use. Architecture keeps the
  checkpoint swappable so 3.1 is a drop-in once Meta fixes the loader or ships
  re-exported weights.
- **Safetensors only.** We do not download or load `.pt` pickle checkpoints.
  Weights load via `safetensors.torch.load_file` → `load_state_dict`.
  **Key layout (discovered at smoke test 2026-07-18):** the 1038lab mirror is
  *not* a verbatim repack of the original `sam3.pt` — inner tensors use the
  **HF-transformers Sam3 naming** (`vision_encoder.*`, `detr_decoder.*`, split
  q/k/v projections, cls-stripped pos-embed, transposed CLIP text projection)
  under a `detector_model.` wrapper, plus `tracker_model.`/`tracker_neck.`
  weights we drop. `sam3_predictor._rewrite_state_dict` converts HF naming back
  to the native sam3-package layout by inverting transformers'
  `convert_sam3_to_hf.py` mapping (1096/1134 model keys mapped; the rest are
  rebuilt rope buffers and point-prompt projections unused by text prompts).
- **Checkpoint source:** [1038lab/sam3](https://huggingface.co/1038lab/sam3)
  `sam3.safetensors` (3.44 GB, ungated, HF scanner clean) → `models/sam3/`
  (gitignored). Once the user's `facebook/sam3` access appeal succeeds, official
  gated download via `HF_TOKEN` becomes an alternative source; local file wins.
- **Points mode stays on SAM2** for this iteration. SAM 3's interactive head
  (`enable_inst_interactivity=True`) is deferred — see Deferred section.

### Tasks

- [x] Locate safetensors mirror of the SAM 3.0 checkpoint (1038lab/sam3)
- [x] Download checkpoint to `models/sam3/`
- [x] Optional SAM3 install step in `manage.sh` + `manage.ps1` (setup and update,
      mirroring the SAM2 step). Installed with `--no-deps` + explicit deps
      (`iopath ftfy pycocotools "setuptools<81"`) because sam3 pins `numpy<2`
      and `ftfy==6.1.1`, conflicting with requirements.txt
- [x] `backend/ml/mask_utils.py` — move `_masks_to_polygons` / `_bbox_from_mask`
      out of `sam2_predictor.py`; both predictors import from here
- [x] `backend/ml/sam3_predictor.py` — `model_manager` entry `"sam3"`;
      `build_sam3_image_model(load_from_HF=False)` + our safetensors state-dict load;
      `Sam3Processor.set_image` / `set_text_prompt` under bf16 autocast (sam3's
      fused ViT MLP kernel hard-casts to bf16); outputs → existing normalized
      polygon-JSON `Detection` format
- [x] Checkpoint resolution: scan `models/sam3/*.safetensors`; clear error message
      with mirror URL if missing (no HF download path)
- [x] Router: `"sam3"` in `_ALLOWED_MODELS`, task `text_prompt`; typed
      `custom_prompt` only (**no caption-as-prompt for sam3** — decided 2026-07-18);
      `sam3_threshold` row in `threshold_settings` (SAM 3 has its own confidence
      scores; `gdino_threshold` does not apply)
- [x] Frontend: model choice in Detect modal (`SelectionToolbar`, now offering
      both sam2 and sam3) and `ImageDetailPage` text-prompt flow — "SAM 2 +
      Grounding DINO" vs "SAM 3"; SAM 3 confidence threshold in Settings
- [x] Smoke test in container: state-dict keys load, end-to-end text-prompt
      detection on a real dataset image (CPU, bf16 autocast)
- [~] VRAM measurement for `model_manager` eviction sizing (848M params;
      expect ~2 GB bf16 / ~3.5 GB f32 + activations). Registered at 3500 MB
      (`max(3500, measured_delta)`); real measurement deferred to the Windows
      GPU host — container is CPU-only
- [x] Docs: `docs/dev/ml-models.md` (detection section), README prerequisites,
      `docs/features.md`, settings docs for the new threshold

### Upstream watch

- [!] `facebook/sam3` / `facebook/sam3.1` HF access appeal — pending
- [!] [sam3#526](https://github.com/facebookresearch/sam3/issues/526) — 3.1 image
      loading broken on public main; re-check before starting any 3.1 upgrade

---

## Iteration 2 — Mask export for masked training loss  `[x]`

The highest-leverage consumer of mask data: kohya and ai-toolkit both support masked
loss via conditioning-image masks, and `Detection.mask` already stores clean
normalized polygons.

### Decisions (agreed 2026-07-18)

- **Trainer conventions confirmed:** both trainers match masks to images by
  filename stem and want grayscale white=train/black=ignore. kohya:
  `conditioning_data_dir` in a TOML dataset config + `--masked_loss` (R channel
  read). ai-toolkit: `mask_path` per dataset, tries `.jpg/.jpeg/.png/.webp`,
  converts to `L`, has its own `mask_min_value`/`invert_mask`. → one artifact
  serves both: sibling folder of grayscale PNGs (`{concept}_mask/`, plain:
  `masks/`), stems matching the exported images.
- **Bbox fallback included:** Florence-2/NudeNet detections (no polygons)
  rasterize as filled rectangles, so all four detection models drive masks.
- **No-detection policy is a UI choice:** full-white mask (default; count
  reported as `masks_full_white`) or skip the image (`excluded_no_mask`).

- [x] Rasterize per-image polygon masks (union of selected labels) to PNG at export
      (`mask_utils.rasterize_detections` + `_write_mask` in `export_service.py`;
      unit-tested in `backend/tests/test_mask_rasterize.py`)
- [x] Export format wiring: kohya `conditioning_data_dir`, ai-toolkit mask folder
      conventions (confirmed per trainer docs/source before implementing)
- [x] Label selection UI in `ExportPage` (chips from new
      `GET /detection/labels/{dataset_id}`; empty selection = all labels;
      invert option for background masking)
- [x] Images without detections: policy select (full-white mask / skip) + live
      `images_without_detections` count in the export preview
- [x] Respect export resize — `_write_image` returns final dims; masks
      rasterized at exactly those dimensions (EXIF-orientation-aware fast path)

## Iteration 3 — Detection-driven cropping  `[x]`

### Decisions (agreed 2026-07-18)

- **Both output modes**, mirroring batch upscale: Replace (in-place, COW-protected)
  and New file (`{stem}_crop{ext}`, new Image row).
- **"Largest" = single largest-area detection** overall (not largest-per-label).
- **Grow-only aspect snap**: the crop expands toward the target ratio, clamped to
  the image; if no rect of that ratio can contain the padded subject, the ratio
  bends rather than cutting the subject (`detection_crop_rect` in `mask_utils.py`,
  unit-tested).

- [x] Batch "crop to detected subject" op: bbox (union or largest) + padding % +
      aspect-ratio snap, reusing `crop_image_to_dest` (`POST /detection/crop`,
      job type `crop_to_detection`, counts in `result_data`)
- [x] COW protection (`protect_file_before_overwrite`) for replace-mode crops
- [x] `SelectionToolbar` (Crop modal) + `BulkEditPage` ("Crop to Subject" tab)
      + `ImageDetailPage` DETECTIONS panel ("Crop to Subject", single-image,
      per-image label chips) surfaces via shared `CropToDetectionForm`; label
      chips from `GET /detection/labels/{dataset_id}`

## Iteration 4 — Mask refinement & detection management  `[ ]`

- [ ] Per-detection delete and relabel endpoints (`DELETE /detection/{id}`,
      `PATCH /detection/{id}`) + UI on the ImageDetailPage detections panel
- [ ] Point-edit an existing SAM mask (add fg/bg points to refine rather than re-run
      from scratch)
- [ ] Merge/union masks across detections
- [ ] Prefill the ImageDetailPage interactive crop tool from detections: compute
      the detection rect (union/largest + padding, reusing `detection_crop_rect`
      semantics) and seed `react-easy-crop` with it
      (`initialCroppedAreaPixels` on entering crop mode) so the user can
      visually preview/adjust before applying — complements the Iteration 3
      "Crop to Subject" modal, which applies without preview

## Iteration 5 — Watermark locate & remove (candidate)  `[ ]`

`has_watermark` flags existence but not location. Ground "watermark"/"text"/"logo"
through the text-prompt detector to localize, then crop-away or mask-out.

- [ ] Scope after Iterations 2–3; may fold into detection-driven cropping

---

## Deferred / out of scope

- SAM 3 interactive points mode (`enable_inst_interactivity=True`) — revisit after
  Iteration 1 proves SAM 3 quality; SAM2 points flow keeps working regardless
- SAM 3.1 upgrade — blocked upstream (see Upstream watch)
- Video segmentation/tracking — Crucible is image-only
