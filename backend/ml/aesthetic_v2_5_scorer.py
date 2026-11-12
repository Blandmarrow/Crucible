"""Aesthetic Predictor V2.5 — the second producer of `Image.aesthetic_score`.

SigLIP-so400m-patch14-384 plus a linear head, loaded by
`model_manager.load_aesthetic_v2_5`. Only the per-image work lives here, in its
CPU and GPU halves: the batch loop, its SSE cadence, its cancellation check and
its None-on-failure contract are `aesthetic_scorer.score_images_batch` and
`prefetch.run_prefetched`, which pick between the two producers' halves above the
loop. Duplicating the loop would duplicate three things this repo has already had
to fix once each.

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


def prepare_v2_5(image_path: str, processor) -> torch.Tensor:
    """CPU half (M10 T5, `backend/ml/prefetch.py`): the SigLIP processor's
    `pixel_values`, still float32 and on the CPU. The cast to the model's dtype
    reads the model, so it belongs to the GPU half."""
    from backend.ml.image_utils import open_rgb

    img = open_rgb(image_path)
    try:
        return processor(images=img, return_tensors="pt").pixel_values
    finally:
        # Freed before the (slow) inference rather than after it — a decoded 4K
        # RGB buffer is ~25 MB, and holding it across the forward pass is what
        # accumulates over a large batch.
        img.close()


def infer_v2_5(pixel_values: torch.Tensor, model_entry) -> float:
    """GPU half. `model_entry` is the plain `ModelEntry` from
    `load_aesthetic_v2_5`: `.model` is the predictor."""
    model = model_entry.model
    pixel_values = pixel_values.to(_device.get_device(), dtype=next(model.parameters()).dtype)

    with torch.no_grad():
        score = model(pixel_values).logits.squeeze().float().item()

    return round(max(1.0, min(10.0, score)), 3)


def score_image_v2_5_sync(image_path: str, model_entry) -> float:
    """One image → a 1–10 aesthetic score. `model_entry` is the plain
    `ModelEntry` from `load_aesthetic_v2_5`: `.model` is the predictor and
    `.processor` its SigLIP image processor."""
    return infer_v2_5(prepare_v2_5(image_path, model_entry.processor), model_entry)
