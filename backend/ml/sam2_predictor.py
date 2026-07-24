import asyncio
import functools
import json
import logging
import threading

import numpy as np

from backend.ml import device as _device
from backend.ml.image_utils import open_rgb
from backend.ml.mask_utils import bbox_from_mask, masks_to_polygons, polygons_to_mask_input

logger = logging.getLogger(__name__)

_SAM2_REPO = "facebook/sam2.1-hiera-large"
_GDINO_REPO = "IDEA-Research/grounding-dino-tiny"

# ---------------------------------------------------------------------------
# Grounding DINO — lazy module-level cache (loaded on first text_prompt call)
# ---------------------------------------------------------------------------

_gdino_lock = threading.Lock()
_gdino_cache: dict | None = None


def _ensure_gdino():
    """Load Grounding DINO on first call; cached for the process lifetime."""
    global _gdino_cache
    if _gdino_cache is not None:
        return _gdino_cache
    with _gdino_lock:
        if _gdino_cache is not None:
            return _gdino_cache
        from transformers import AutoProcessor, GroundingDinoForObjectDetection
        logger.info("Loading Grounding DINO from %s", _GDINO_REPO)
        dev = _device.get_device()
        processor = AutoProcessor.from_pretrained(_GDINO_REPO)
        model = GroundingDinoForObjectDetection.from_pretrained(_GDINO_REPO).to(dev).eval()
        _gdino_cache = {"model": model, "processor": processor, "device": dev}
        logger.info("Grounding DINO loaded.")
    return _gdino_cache


# ---------------------------------------------------------------------------
# SAM2 model loading
# ---------------------------------------------------------------------------

def _load_sam2_sync(job_id=None, loop=None, dataset_id=None):
    """Load SAM2ImagePredictor."""
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    from backend.ml.model_manager import ModelEntry
    from backend.ml.download_progress import emit_sync

    dev = _device.get_device()
    dev_str = str(dev)
    vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0

    if job_id and loop:
        emit_sync(job_id, loop, "Loading SAM2 + Grounding DINO (first run ~1 GB)...", -1.0, dataset_id)

    logger.info("Loading SAM2ImagePredictor from %s on device %s", _SAM2_REPO, dev_str)
    predictor = SAM2ImagePredictor.from_pretrained(_SAM2_REPO, device=dev_str)

    if job_id and loop:
        emit_sync(job_id, loop, "SAM2 loaded.", -1.0, dataset_id)

    vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
    delta_mb = (vram_after - vram_before) // (1024 * 1024)
    vram_used = max(900, delta_mb) if _device.is_gpu_available() else 0
    return ModelEntry(
        {"predictor": predictor, "device": dev},
        None,
        vram_mb=vram_used,
    )


# ---------------------------------------------------------------------------
# Prediction modes
# ---------------------------------------------------------------------------

def _predict_text(img_np: np.ndarray, img_w: int, img_h: int, predictor, text_prompt: str, gdino_threshold: float = 0.35) -> list[dict]:
    """Grounding DINO → pixel-space boxes → SAM2 mask per box."""
    import torch
    from PIL import Image as _Image

    gdino = _ensure_gdino()
    gdino_model = gdino["model"]
    gdino_processor = gdino["processor"]
    gdino_dev = gdino["device"]

    img_pil = _Image.fromarray(img_np)

    # Grounding DINO natively grounds multiple classes separated by " . " and
    # returns a per-phrase label for each box. Normalize comma-separated phrases
    # to that separator; a single phrase collapses to the old behavior.
    text = " . ".join(p.strip() for p in text_prompt.lower().split(",") if p.strip())
    if not text.endswith("."):
        text += "."

    inputs = gdino_processor(images=img_pil, text=text, return_tensors="pt").to(gdino_dev)
    with torch.no_grad():
        outputs = gdino_model(**inputs)

    detections = gdino_processor.post_process_grounded_object_detection(
        outputs,
        inputs.input_ids,
        threshold=gdino_threshold,
        text_threshold=max(0.01, gdino_threshold - 0.1),
        target_sizes=[(img_h, img_w)],
    )[0]

    boxes_px = detections["boxes"].cpu().numpy()   # [N, 4] xyxy pixel coords
    scores = detections["scores"].cpu().numpy()
    labels = detections["labels"]

    if len(boxes_px) == 0:
        logger.debug("Grounding DINO found no boxes for prompt: %r", text_prompt)
        return []

    results = []
    with torch.inference_mode():
        predictor.set_image(img_np)
        for box, score, label in zip(boxes_px, scores, labels):
            geom = _predict_box(img_np, img_w, img_h, predictor, box, set_image=False)
            if geom is None:
                continue
            results.append({
                "label": str(label) if label else "object",
                "bbox": geom["bbox"],
                "score": round(float(score), 4),
                "mask": geom["mask"],
            })

    return results


def _predict_box(
    img_np: np.ndarray,
    img_w: int,
    img_h: int,
    predictor,
    box_px,
    set_image: bool = True,
) -> dict | None:
    """SAM2 mask for a single pixel-space box; label-agnostic geometry only.

    ``box_px`` is ``[x1, y1, x2, y2]`` in pixels. Returns ``{bbox, score, mask}``
    (mask-derived normalized bbox, IoU score, polygon JSON) or ``None`` when SAM2
    produces no usable mask. When ``set_image`` is False the caller is expected to
    have already called ``predictor.set_image`` inside ``torch.inference_mode()``
    (the text-prompt loop reuses one embedding for many boxes).
    """
    import torch

    def _run() -> dict | None:
        try:
            masks, iou_scores, _ = predictor.predict(box=box_px, multimask_output=True)
        except Exception as exc:
            logger.debug("SAM2 mask failed for box %s: %s", box_px, exc)
            return None
        best_idx = int(np.argmax(iou_scores))
        bool_mask = masks[best_idx] > 0
        if not bool_mask.any():
            return None
        polys = masks_to_polygons(np.array([bool_mask]), img_w, img_h)
        if not polys:
            return None
        return {
            "bbox": bbox_from_mask(bool_mask, img_w, img_h),
            "score": round(float(iou_scores[best_idx]), 4),
            "mask": json.dumps({"polygons": polys}),
        }

    if set_image:
        with torch.inference_mode():
            predictor.set_image(img_np)
            return _run()
    return _run()



def _predict_points(
    img_np: np.ndarray,
    img_w: int,
    img_h: int,
    predictor,
    pts: np.ndarray,
    lbls: np.ndarray,
) -> list[dict]:
    import torch
    results = []
    with torch.inference_mode():
        predictor.set_image(img_np)
        masks, scores, _ = predictor.predict(
            point_coords=pts,
            point_labels=lbls,
            multimask_output=True,
        )
    for mask, score in zip(masks, scores):
        bool_mask = mask > 0
        if not bool_mask.any():
            continue
        polys = masks_to_polygons(np.array([bool_mask]), img_w, img_h)
        if not polys:
            continue
        results.append({
            "label": "segment",
            "bbox": bbox_from_mask(bool_mask, img_w, img_h),
            "score": round(float(score), 4),
            "mask": json.dumps({"polygons": polys}),
        })
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_sync(
    image_path: str,
    model_entry: dict,
    mode: str,
    text_prompt: str = "",
    point_prompts: list[list[float]] | None = None,
    point_labels: list[int] | None = None,
    gdino_threshold: float = 0.35,
    box_prompt: list[float] | None = None,
) -> list[dict]:
    """Run Grounded SAM2 prediction on a single image.

    mode: "text_prompt" | "points" | "box"
    Returns list of {label, bbox [x1,y1,x2,y2] norm., score, mask (polygon JSON)}.
    """
    predictor = model_entry["predictor"]

    img = open_rgb(image_path)
    img_w, img_h = img.size
    img_np = np.array(img)
    img.close()

    if mode == "text_prompt":
        if not text_prompt.strip():
            logger.warning("SAM2 text_prompt mode called with empty prompt")
            return []
        return _predict_text(img_np, img_w, img_h, predictor, text_prompt, gdino_threshold)

    if mode == "points":
        if not point_prompts:
            return []
        pts = np.array(
            [[p[0] * img_w, p[1] * img_h] for p in point_prompts], dtype=np.float32
        )
        lbls = np.array(point_labels or [1] * len(point_prompts), dtype=np.int32)
        return _predict_points(img_np, img_w, img_h, predictor, pts, lbls)

    if mode == "box":
        if not box_prompt or len(box_prompt) != 4:
            return []
        x1, y1, x2, y2 = box_prompt
        box_px = np.array(
            [x1 * img_w, y1 * img_h, x2 * img_w, y2 * img_h], dtype=np.float32
        )
        geom = _predict_box(img_np, img_w, img_h, predictor, box_px, set_image=True)
        if geom is None:
            return []
        return [{
            "label": "segment",
            "bbox": geom["bbox"],
            "score": geom["score"],
            "mask": geom["mask"],
        }]

    logger.warning("SAM2: unknown mode %r", mode)
    return []


def refine_sync(
    image_path: str,
    model_entry: dict,
    mask_json: str | None,
    bbox: list[float] | None,
    point_prompts: list[list[float]],
    point_labels: list[int],
) -> dict | None:
    """Refine an existing mask with point prompts, seeded by its low-res logits.

    Rasterizes the detection's polygons/bbox into a ``(1, 256, 256)`` logit map
    (``polygons_to_mask_input``) and passes it as ``mask_input`` alongside the
    click points, ``multimask_output=False``. Returns ``{bbox, score, mask}`` for
    the refined region, or ``None`` when there is nothing to seed from or SAM2
    yields an empty mask.

    SAM2-only (why refine is not a SAM3 path): point-refinement needs the
    ``predict(point_coords, point_labels, mask_input=…)`` interactive interface,
    which ``SAM2ImagePredictor`` exposes natively. SAM3 architecturally has a
    geometry/point-prompt path, but our SAM3 model is built with
    ``enable_inst_interactivity=False`` and the ungated 1038lab mirror checkpoint
    strips the geometry-encoder point-prompt weights (see
    ``sam3_predictor._is_expected_missing`` — ``geometry_encoder.points_*`` are
    treated as expected-missing), so those weights simply aren't present to run.
    The ``Sam3Processor`` we drive is text-prompt only (``set_text_prompt``).
    Revisit a SAM3 refine path once a checkpoint with the point-prompt + tracker
    weights is available from the official repo (then: build with
    ``enable_inst_interactivity=True`` and use SAM3's interactive predict API).
    """
    import torch

    predictor = model_entry["predictor"]

    mask_input = polygons_to_mask_input(mask_json, bbox)
    if mask_input is None:
        return None

    img = open_rgb(image_path)
    img_w, img_h = img.size
    img_np = np.array(img)
    img.close()

    pts = np.array(
        [[p[0] * img_w, p[1] * img_h] for p in point_prompts], dtype=np.float32
    )
    lbls = np.array(point_labels, dtype=np.int32)

    with torch.inference_mode():
        predictor.set_image(img_np)
        masks, scores, _ = predictor.predict(
            point_coords=pts,
            point_labels=lbls,
            mask_input=mask_input,
            multimask_output=False,
        )
    bool_mask = masks[0] > 0
    if not bool_mask.any():
        return None
    polys = masks_to_polygons(np.array([bool_mask]), img_w, img_h)
    if not polys:
        return None
    return {
        "bbox": bbox_from_mask(bool_mask, img_w, img_h),
        "score": round(float(scores[0]), 4),
        "mask": json.dumps({"polygons": polys}),
    }


async def predict_batch(
    image_paths: list[str],
    model_entry: dict,
    mode: str,
    text_prompt: str = "",
    point_prompts: list[list[float]] | None = None,
    point_labels: list[int] | None = None,
    job_id: str | None = None,
) -> list[list[dict]]:
    """Async batch wrapper — runs predict_sync in a thread executor per image."""
    loop = asyncio.get_event_loop()
    results = []
    for path in image_paths:
        fn = functools.partial(
            predict_sync, path, model_entry, mode, text_prompt, point_prompts, point_labels
        )
        preds = await loop.run_in_executor(None, fn)
        results.append(preds)
    return results
