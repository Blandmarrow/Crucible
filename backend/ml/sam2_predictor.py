import asyncio
import functools
import json
import logging
import threading

import numpy as np

from backend.ml import device as _device

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
# Helpers
# ---------------------------------------------------------------------------

def _masks_to_polygons(masks: np.ndarray, img_w: int, img_h: int) -> list[list[list[float]]]:
    import cv2
    polygons = []
    for mask in masks:
        uint8 = (mask > 0).astype(np.uint8) * 255
        contours, _ = cv2.findContours(uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            epsilon = 0.005 * cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, epsilon, True)
            if len(approx) < 3:
                continue
            poly = [
                [round(float(pt[0][0]) / img_w, 4), round(float(pt[0][1]) / img_h, 4)]
                for pt in approx
            ]
            polygons.append(poly)
    return polygons


def _bbox_from_mask(mask: np.ndarray, img_w: int, img_h: int) -> list[float]:
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any():
        return [0.0, 0.0, 0.0, 0.0]
    y1, y2 = np.where(rows)[0][[0, -1]]
    x1, x2 = np.where(cols)[0][[0, -1]]
    return [
        round(float(x1) / img_w, 4),
        round(float(y1) / img_h, 4),
        round(float(x2) / img_w, 4),
        round(float(y2) / img_h, 4),
    ]


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

    text = text_prompt.lower().strip()
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
            try:
                masks, iou_scores, _ = predictor.predict(
                    box=box,
                    multimask_output=True,
                )
            except Exception as exc:
                logger.debug("SAM2 mask failed for box %s: %s", box, exc)
                continue
            # Pick the single best mask by IoU score.
            best_idx = int(np.argmax(iou_scores))
            bool_mask = masks[best_idx] > 0
            if not bool_mask.any():
                continue
            polys = _masks_to_polygons(np.array([bool_mask]), img_w, img_h)
            if not polys:
                continue
            results.append({
                "label": str(label) if label else "object",
                "bbox": _bbox_from_mask(bool_mask, img_w, img_h),
                "score": round(float(score), 4),
                "mask": json.dumps({"polygons": polys}),
            })

    return results



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
        polys = _masks_to_polygons(np.array([bool_mask]), img_w, img_h)
        if not polys:
            continue
        results.append({
            "label": "segment",
            "bbox": _bbox_from_mask(bool_mask, img_w, img_h),
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
) -> list[dict]:
    """Run Grounded SAM2 prediction on a single image.

    mode: "text_prompt" | "auto" | "points"
    Returns list of {label, bbox [x1,y1,x2,y2] norm., score, mask (polygon JSON)}.
    """
    from PIL import Image

    predictor = model_entry["predictor"]
    dev = model_entry["device"]

    img = Image.open(image_path).convert("RGB")
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

    logger.warning("SAM2: unknown mode %r", mode)
    return []


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
