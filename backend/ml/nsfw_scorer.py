import asyncio
import functools
import logging

import torch

from backend.ml import device as _device

logger = logging.getLogger(__name__)

_MODEL_NAME = "Marqo/nsfw-image-detection-384"
_INPUT_SIZE = 384


def score_image_sync(image_path: str, model_entry: dict) -> float:
    """Return NSFW probability (0–1) for the image at image_path."""
    from backend.ml.image_utils import open_rgb

    processor = model_entry["processor"]
    model = model_entry["model"]
    nsfw_idx = model_entry["nsfw_idx"]

    img = open_rgb(image_path)
    inputs = processor(images=img, return_tensors="pt")
    img.close()

    inputs = {k: v.to(_device.get_device()) for k, v in inputs.items()}

    with torch.no_grad(), _device.autocast_ctx():
        logits = model(**inputs).logits
        probs = logits.softmax(dim=-1)[0]

    return round(float(probs[nsfw_idx].item()), 4)


async def score_images_nsfw_batch(
    image_paths: list[str],
    model_entry: dict,
    threshold: float,
    job_id: str | None = None,
) -> list[dict]:
    """Score a batch of images for NSFW content, emitting SSE progress.

    Returns a list of dicts with keys: nsfw_score (float), is_nsfw (bool).
    """
    from backend.workers.progress import broadcaster
    from backend.workers.job_queue import job_queue

    loop = asyncio.get_event_loop()
    results = []
    total = len(image_paths)

    for i, path in enumerate(image_paths):
        if job_id and job_queue.cancel_requested(job_id):
            break
        try:
            fn = functools.partial(score_image_sync, path, model_entry)
            score = await loop.run_in_executor(None, fn)
        except Exception:
            logger.warning("NSFW scoring failed for %s", path, exc_info=True)
            score = 0.0
        results.append({"nsfw_score": score, "is_nsfw": score >= threshold})

        if job_id and i % 10 == 0:
            await broadcaster.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "job_type": "quality_score",
                "status": "running",
                "done": i + 1,
                "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "message": f"NSFW scoring {i + 1}/{total}",
            })

    return results
