# Object detection — the router, the predictors and the jobs that consume detections

Detection finds boxes and masks in an image and stores them as `Detection` rows, which two
jobs later consume: one crops an image down to what was found, the other paints it out.
This file is the family's entry point: the tables below route from a code path to whichever
file in `docs/dev/detection/` owns it. Detection models are registered and VRAM-managed like
every other model — `docs/dev/model-registry.md` for their entries,
`docs/dev/model-residency.md` for eviction and leases, `docs/dev/ml-models.md` for loading.
The detection remap every replace-mode crop runs is `docs/dev/bulk-image-jobs.md`, the
LaMa model the paint job calls is `docs/dev/image-processing-models.md` § LaMa inpainting,
and the snapshot mirror of detections is `docs/dev/versioning/mirrors.md`. The user doc is
`docs/detection.md`.

## The family

| File | Covers |
|---|---|
| [endpoints.md](detection/endpoints.md) | The `/detection` router and its management routes, `DetectionJobRequest`, per-model overwrite scoping and its `inference_ok` gate, `model="manual"` rows, `mask_area`, watermark flag sync, storage, and the frontend surfaces (`DetectionsPanel`, the Detect modals, `DETECTION_MODELS`) |
| [inference.md](detection/inference.md) | The predictors in `backend/ml/`: the task matrix per model family, multi-phrase prompts, Florence-2/NudeNet/SAM2/SAM3 inference, mask refine and merge geometry, the SAM 3 safetensors loader and its upstream limits |
| [crop.md](detection/crop.md) | Crop to Subject: `POST /detection/crop`, the `crop_to_detection` job, `detection_crop_rect`, `dest_subfolder` and `CropToDetectionForm` |
| [inpaint.md](detection/inpaint.md) | Watermark removal: `POST /inpaint/run`, the `batch_inpaint` job, the paint mask and `_dilate`, the stored-form `contained_path` gate, replace mode's `has_watermark` rule, copy mode and `InpaintForm` |

## Where things live

Each row names the file that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the code it owns.

| Editing… | Read |
|---|---|
| `backend/routers/detection.py` (everything but `crop_to_detection`) | `docs/dev/detection/endpoints.md` |
| `backend/routers/detection.py::crop_to_detection` | `docs/dev/detection/crop.md` |
| `backend/models/detection.py` (the `mask_area` listeners) | `docs/dev/detection/endpoints.md` § Router and endpoints |
| `backend/services/detection_service.py` | `docs/dev/detection/endpoints.md` § Storage for the snapshot helpers; + `docs/dev/bulk-image-jobs.md` for `remap_detections_for_crop` |
| `backend/ml/sam2_predictor.py`, `backend/ml/sam3_predictor.py`, `backend/ml/nudenet_scorer.py`, `backend/ml/florence_captioner.py` (its detection entry) | `docs/dev/detection/inference.md` |
| `backend/ml/mask_utils.py` | `docs/dev/detection/inference.md` for refine and merge geometry; + `docs/dev/detection/crop.md` for `detection_crop_rect`, + `docs/dev/detection/endpoints.md` for `detection_mask_area` |
| `backend/routers/inpaint.py` | `docs/dev/detection/inpaint.md` |
| `frontend/src/constants/detectionModels.ts`, `frontend/src/api/detection.ts` | `docs/dev/detection/endpoints.md` § Frontend surfaces |
| `frontend/src/components/detection/` (`DetectionsPanel`, `DetectionRunForm`, `DetectionBulkDeleteForm`) | `docs/dev/detection/endpoints.md` § Frontend surfaces |
| `frontend/src/utils/detectionCrop.ts` | `docs/dev/detection/endpoints.md` § Frontend surfaces; + `docs/dev/image-detail.md` for the crop box it seeds |
| `frontend/src/components/crop/CropToDetectionForm.tsx` | `docs/dev/detection/crop.md` |
| `frontend/src/components/inpaint/InpaintForm.tsx`, `frontend/src/api/inpaint.ts` | `docs/dev/detection/inpaint.md` |
