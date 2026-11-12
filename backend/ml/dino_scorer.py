import functools
import logging
from collections.abc import Awaitable, Callable

import numpy as np
import torch

from backend.ml import device as _device

# Re-exported, not defined here. Both moved to `similarity_scorer` (numpy-only)
# because this module is `import torch` at module scope, and the quality router
# needs the slicer on every `combined` request — including on a torch-free
# runner, where importing it from here was an ImportError → 500.
from backend.ml.similarity_scorer import _LAYER_BLOB_SIZE, slice_layer_embedding  # noqa: F401

logger = logging.getLogger(__name__)


def prepare_dino(image_path: str, processor) -> dict[str, torch.Tensor]:
    """CPU half shared by both DINOv2 passes (M10 T5, `backend/ml/prefetch.py`):
    the processor's inputs, still on the CPU."""
    from backend.ml.image_utils import open_rgb

    img = open_rgb(image_path)
    try:
        return dict(processor(images=img, return_tensors="pt"))
    finally:
        img.close()


def infer_dino(inputs: dict[str, torch.Tensor], model_entry) -> bytes:
    """Returns L2-normalized float16 numpy bytes for DINOv2-base CLS token, shape (768,)."""
    model = model_entry.model
    inputs = {k: v.to(_device.get_device()) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)
        cls_token = outputs.last_hidden_state[:, 0, :]  # CLS token
        cls_token = cls_token / cls_token.norm(dim=-1, keepdim=True)

    return cls_token[0].cpu().numpy().astype(np.float16).tobytes()


def infer_dino_layers(inputs: dict[str, torch.Tensor], model_entry) -> bytes:
    """Returns all 12 transformer-layer CLS tokens as float16 bytes, shape (12, 768).

    Layer N (1-indexed) is at byte offset (N-1) * 768 * 2.
    Each row is independently L2-normalized.
    """
    model = model_entry.model
    inputs = {k: v.to(_device.get_device()) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs, output_hidden_states=True)
        # hidden_states is a tuple of 13: index 0 is patch embed, 1-12 are transformer blocks
        layers = []
        for i in range(1, 13):
            cls = outputs.hidden_states[i][:, 0, :]  # (1, 768)
            cls = cls / cls.norm(dim=-1, keepdim=True)
            layers.append(cls[0].cpu().numpy().astype(np.float16))

    stacked = np.stack(layers)  # (12, 768)
    return stacked.tobytes()


def extract_dino_embedding_sync(image_path: str, model_entry) -> bytes:
    """Returns L2-normalized float16 numpy bytes for DINOv2-base CLS token, shape (768,)."""
    return infer_dino(prepare_dino(image_path, model_entry.processor), model_entry)


def extract_dino_layer_embeddings_sync(image_path: str, model_entry) -> bytes:
    """Returns all 12 transformer-layer CLS tokens as float16 bytes, shape (12, 768)."""
    return infer_dino_layers(prepare_dino(image_path, model_entry.processor), model_entry)


async def extract_layer_embeddings_dino(
    image_paths: list[str],
    model_entry,
    job_id: str | None = None,
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> list[bytes | None]:
    from backend.ml.prefetch import run_prefetched

    return await run_prefetched(
        image_paths,
        functools.partial(prepare_dino, processor=model_entry.processor),
        functools.partial(infer_dino_layers, model_entry=model_entry),
        job_id=job_id, progress=progress,
        message="DINOv2 layer embeddings", failure="DINOv2 per-layer embedding", log=logger,
    )


async def extract_embeddings_dino(
    image_paths: list[str],
    model_entry,
    job_id: str | None = None,
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> list[bytes | None]:
    from backend.ml.prefetch import run_prefetched

    return await run_prefetched(
        image_paths,
        functools.partial(prepare_dino, processor=model_entry.processor),
        functools.partial(infer_dino, model_entry=model_entry),
        job_id=job_id, progress=progress,
        message="DINOv2 embeddings", failure="DINOv2 embedding", log=logger,
    )
