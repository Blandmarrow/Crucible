"""Aesthetic Predictor V2.5 — the second producer of `Image.aesthetic_score`.

SigLIP-so400m-patch14-384 plus a linear head, loaded by
`model_manager.load_aesthetic_v2_5`. Only the per-image inference lives here: the
batch loop, its SSE cadence, its cancellation check and its None-on-failure
contract stay in `aesthetic_scorer.score_images_batch`, which picks between the
two `*_sync` callables above its loop. Duplicating the loop would duplicate three
things this repo has already had to fix once each.

The `_scorer` suffix is load-bearing: `backend/tests/test_ml_image_opens.py`
fails CI for any `*_scorer` module absent from both its `INFERENCE_MODULES` list
and its `NOT_INFERENCE` set, and the AST walk then enforces `open_rgb` here for
free.

The score is clamped to [1, 10] to match LAION's, so the column's declared range
holds whichever model wrote a row. That is the *only* thing the two scales have
in common — they are not comparable within the range, which is why every stored
score carries an `Image.aesthetic_model` marker.
"""

import logging

import torch

from backend.ml import device as _device

logger = logging.getLogger(__name__)


def score_image_v2_5_sync(image_path: str, model_entry) -> float:
    """One image → a 1–10 aesthetic score. `model_entry` is the plain
    `ModelEntry` from `load_aesthetic_v2_5`: `.model` is the predictor and
    `.processor` its SigLIP image processor."""
    from backend.ml.image_utils import open_rgb

    model = model_entry.model
    processor = model_entry.processor

    img = open_rgb(image_path)
    pixel_values = processor(images=img, return_tensors="pt").pixel_values
    # Freed before the (slow) inference below rather than after it — a decoded
    # 4K RGB buffer is ~25 MB, and holding it across the forward pass is what
    # accumulates over a large batch.
    img.close()

    pixel_values = pixel_values.to(_device.get_device(), dtype=next(model.parameters()).dtype)

    with torch.no_grad():
        score = model(pixel_values).logits.squeeze().float().item()

    return round(max(1.0, min(10.0, score)), 3)
