import asyncio
import logging

import numpy as np
import torch

from backend.ml import device as _device

logger = logging.getLogger(__name__)

_LAYER_BLOB_SIZE = 12 * 768 * 2  # 12 layers × 768 dims × float16


def slice_layer_embedding(blob: bytes, layer: int) -> bytes:
    """Return the 768-dim float16 bytes for a specific DINOv2 layer (1-indexed)."""
    if not (1 <= layer <= 12):
        raise ValueError(f"layer must be 1–12, got {layer}")
    if len(blob) != _LAYER_BLOB_SIZE:
        raise ValueError(f"Expected {_LAYER_BLOB_SIZE}-byte layer blob, got {len(blob)}")
    offset = (layer - 1) * 768 * 2
    return blob[offset : offset + 768 * 2]


def extract_dino_embedding_sync(image_path: str, model_entry) -> bytes:
    """Returns L2-normalized float16 numpy bytes for DINOv2-base CLS token, shape (768,)."""
    from PIL import Image as PILImage

    model = model_entry.model
    processor = model_entry.processor

    img = PILImage.open(image_path).convert("RGB")
    inputs = processor(images=img, return_tensors="pt")
    img.close()
    inputs = {k: v.to(_device.get_device()) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)
        cls_token = outputs.last_hidden_state[:, 0, :]  # CLS token
        cls_token = cls_token / cls_token.norm(dim=-1, keepdim=True)

    return cls_token[0].cpu().numpy().astype(np.float16).tobytes()


def extract_dino_layer_embeddings_sync(image_path: str, model_entry) -> bytes:
    """Returns all 12 transformer-layer CLS tokens as float16 bytes, shape (12, 768).

    Layer N (1-indexed) is at byte offset (N-1) * 768 * 2.
    Each row is independently L2-normalized.
    """
    from PIL import Image as PILImage

    model = model_entry.model
    processor = model_entry.processor

    img = PILImage.open(image_path).convert("RGB")
    inputs = processor(images=img, return_tensors="pt")
    img.close()
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


async def extract_layer_embeddings_dino(
    image_paths: list[str],
    model_entry,
    job_id: str | None = None,
) -> list[bytes | None]:
    from backend.workers.progress import broadcaster
    from backend.workers.job_queue import job_queue

    loop = asyncio.get_event_loop()
    results = []
    total = len(image_paths)

    for i, path in enumerate(image_paths):
        if job_id and job_queue.cancel_requested(job_id):
            break
        try:
            r = await loop.run_in_executor(
                None, extract_dino_layer_embeddings_sync, path, model_entry
            )
        except Exception:
            logger.warning("DINOv2 per-layer embedding failed for %s", path)
            r = None
        results.append(r)

        if job_id and i % 10 == 0:
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "quality_score",
                "status": "running", "done": i + 1, "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "message": f"DINOv2 layer embeddings {i + 1}/{total}",
            })

    return results


async def extract_embeddings_dino(
    image_paths: list[str],
    model_entry,
    job_id: str | None = None,
) -> list[bytes | None]:
    from backend.workers.progress import broadcaster
    from backend.workers.job_queue import job_queue

    loop = asyncio.get_event_loop()
    results = []
    total = len(image_paths)

    for i, path in enumerate(image_paths):
        if job_id and job_queue.cancel_requested(job_id):
            break
        try:
            r = await loop.run_in_executor(
                None, extract_dino_embedding_sync, path, model_entry
            )
        except Exception:
            logger.warning("DINOv2 embedding failed for %s", path)
            r = None
        results.append(r)

        if job_id and i % 10 == 0:
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "quality_score",
                "status": "running", "done": i + 1, "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "message": f"DINOv2 embeddings {i + 1}/{total}",
            })

    return results
