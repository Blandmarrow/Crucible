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

## Iteration 4 — Mask refinement & detection management  `[x]`

### Decisions (agreed 2026-07-18)

- **Manual editing in tiers, cheapest first.** (1) SAM point-refinement of an
  existing mask — feed the current mask back as SAM2's `mask_input` prompt
  alongside new fg/bg points so refinement refines instead of restarting.
  (2) Manual detection drawing — draw a bbox on the ImageDetailPage overlay,
  assign a label, optionally run SAM2 box-prompted on it for a polygon; covers
  the case where every detector missed the subject and gives loss-mask export a
  manual fallback. (3) Freehand brush editing is **deferred** (see Deferred
  section — polygon storage has no hole/ring support; would need RLE/raster).
- **Mask review/QA lives on the Stats page, not ExportPage** (Iteration 5).
  ExportPage keeps only its existing `images_without_detections` count; a true
  export preview would depend on the form's label/invert state and is redundant
  once Stats has coverage histograms with BucketPanel click-through.
- **Re-run `overwrite` is scoped to the running model** (implemented): each
  `/run` branch deletes only its own model's rows (`nudenet`/`sam2`/`sam3`
  literals, `body.model` for Florence), so re-running one model preserves the
  others' rows and all `model="manual"` rows. No append/replace toggle beyond
  the existing `overwrite` flag was added.
- **Refined/merged/hand-drawn rows are `model="manual"`** so automatic re-runs
  never wipe them; provenance is recorded in `task`: `"manual"` (drawn box),
  `"box_prompt"` (drawn box + SAM), `"refine"` (point-refined), `"merge"`. No DB
  migration — `Detection.model` is `String(128)` and holds `"manual"`.

### Tasks

- [x] Per-detection delete and relabel endpoints (`DELETE /detection/{id}`,
      `PATCH /detection/{id}`) + UI on the ImageDetailPage detections panel
      (`DetectionsPanel`)
- [x] Bulk detection delete-by-filter (dataset-level): by label, model, and/or
      score-below-threshold (`POST /detection/bulk-delete`, dry-run count) — Bulk
      Edit "Detections" tab
- [x] Re-run policy: `overwrite` now scopes its delete to the running model
      (per-model replace) so repeated runs don't stack duplicate rows
- [x] Show `Detection.score` in the detections panel (per-detection rows)
- [x] Manual detection drawing: draw bbox + assign label on the ImageDetailPage
      overlay; optional "refine with SAM" runs SAM2 box-prompted (`predict_sync`
      `mode="box"`); plain bbox row otherwise (`POST /detection/manual`)
- [x] Point-edit an existing SAM mask (`POST /detection/{id}/refine`,
      `refine_sync` + `polygons_to_mask_input` seeding SAM2 `mask_input`)
- [x] Merge/union masks across detections (`POST /detection/merge`,
      `merge_detection_geometry`)
- [x] Prefill the ImageDetailPage interactive crop tool from detections
      (`utils/detectionCrop.ts`, `initialCroppedAreaPixels` on fresh Cropper mount)

## Iteration 5 — Detection stats & mask QA (Stats page)  `[ ]`

Audit surface for mask data before it feeds export/training. StatsPage's
existing pattern (histograms → clickable bars → `BucketPanel` thumbnails via
`GET /images/` filters) is exactly the right shape; add a detections section.

- [ ] Persist mask coverage: `Detection.mask_area` (float fraction of image
      area, nullable) computed at write time — shoelace sum over polygons,
      bbox area when no polygon — plus backfill for existing rows. Stats must
      read a column, not parse polygon JSON per request
- [ ] New "Detections & Masks" `CategorySection` on StatsPage: label
      distribution, detection-score histogram, mask-coverage histogram,
      images-without-detections stat card
- [ ] `GET /images/` filter params for BucketPanel click-through:
      `detection_label`, `detection_score_min/max`, `mask_coverage_min/max`
      (EXISTS subqueries on `detections`) — e.g. click the "<2%" or ">95%"
      coverage bucket to see suspicious masks and jump to the detail page

## Iteration 6 — Watermark locate & remove (candidate)  `[ ]`

`has_watermark` flags existence but not location. Ground "watermark"/"text"/"logo"
through the text-prompt detector to localize, then crop-away or mask-out.

- [ ] Scope after Iterations 2–3; may fold into detection-driven cropping

---

## Deferred / out of scope

- Freehand brush mask editing — feasible without a storage change (rasterize
  polygon client-side, paint, re-polygonize via `mask_utils`), but the
  Douglas-Peucker round-trip smooths fine brushwork and the polygon format has
  no hole/ring support, so bg-brush "cut a hole" edits can't be represented.
  Revisit only if Iteration 4's point-refinement + manual bbox drawing prove
  insufficient; at that point storage should grow an RLE/raster mask option
- SAM 3 interactive points mode (`enable_inst_interactivity=True`) — covers both a
  SAM 3 points-detection mode and a SAM 3 **mask-refine** backend (Iteration 4's
  refine is SAM2-only; see `sam2_predictor.refine_sync` docstring). Blocked today
  by the checkpoint: the ungated 1038lab mirror strips the geometry-encoder
  point-prompt + tracker weights (`sam3_predictor._is_expected_missing`), so points
  have no trained weights to run. Revisit once a checkpoint with those weights is
  available from the official repo. SAM2 points/refine flow keeps working regardless
- SAM 3 multi-concept prompt in one run — today `sam3_predictor.predict_sync`
  passes the whole prompt as a single concept and labels every mask with that
  string, so multiple concepts require one run each (with overwrite off to stack
  them, since re-run overwrite is now scoped per-model). If the official `sam3`
  package's `set_text_prompt` accepts a batched/list prompt, loop or batch the
  phrases in `predict_sync` and label each mask by its own phrase — giving the
  SAM2/Grounding DINO `cat. dog. car.` multi-phrase experience natively on SAM3.
  (Package not installable in the CPU container to confirm the API — verify on the
  GPU host / against the official repo first.)
- SAM 3.1 upgrade — blocked upstream (see Upstream watch)
- Video segmentation/tracking — Crucible is image-only
