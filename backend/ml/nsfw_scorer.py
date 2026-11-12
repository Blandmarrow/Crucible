import functools
import logging
from collections.abc import Awaitable, Callable

import torch

from backend.ml import device as _device

logger = logging.getLogger(__name__)

_MODEL_NAME = "Marqo/nsfw-image-detection-384"
_INPUT_SIZE = 384


def prepare_nsfw(image_path: str, processor) -> dict[str, torch.Tensor]:
    """CPU half (M10 T5, `backend/ml/prefetch.py`): the processor's inputs, still
    on the CPU."""
    from backend.ml.image_utils import open_rgb

    img = open_rgb(image_path)
    try:
        return dict(processor(images=img, return_tensors="pt"))
    finally:
        img.close()


def infer_nsfw(inputs: dict[str, torch.Tensor], model_entry: dict) -> float:
    """GPU half: the NSFW probability (0–1)."""
    model = model_entry["model"]
    nsfw_idx = model_entry["nsfw_idx"]

    inputs = {k: v.to(_device.get_device()) for k, v in inputs.items()}

    with torch.no_grad(), _device.autocast_ctx():
        logits = model(**inputs).logits
        probs = logits.softmax(dim=-1)[0]

    return round(float(probs[nsfw_idx].item()), 4)


def score_image_sync(image_path: str, model_entry: dict) -> float:
    """Return NSFW probability (0–1) for the image at image_path."""
    return infer_nsfw(prepare_nsfw(image_path, model_entry["processor"]), model_entry)


async def score_images_nsfw_batch(
    image_paths: list[str],
    model_entry: dict,
    threshold: float,
    job_id: str | None = None,
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> list[dict]:
    """Score a batch of images for NSFW content, emitting SSE progress.

    Returns a list of dicts with keys: nsfw_score (float | None), is_nsfw (bool).
    `nsfw_score` is None for an image that could not be scored — NULL, not 0.0,
    the technical scorer's failure contract (docs/dev/quality/scoring.md § The
    failure contract). A 0.0 asserted the image was confidently safe about a file
    no decoder ever read, which is the wrong direction to guess in, and it counted
    toward `score_coverage` so nothing flagged the gap.
    """
    from backend.ml.prefetch import run_prefetched

    scores = await run_prefetched(
        image_paths,
        functools.partial(prepare_nsfw, processor=model_entry["processor"]),
        functools.partial(infer_nsfw, model_entry=model_entry),
        job_id=job_id, progress=progress,
        message="NSFW scoring", failure="NSFW scoring", log=logger,
    )
    return [
        {
            "nsfw_score": score,
            # False rather than None: routers/quality.py folds this into a JSON
            # flags dict where None is not expressible. An unmeasured image is
            # simply not flagged.
            "is_nsfw": score is not None and score >= threshold,
        }
        for score in scores
    ]
