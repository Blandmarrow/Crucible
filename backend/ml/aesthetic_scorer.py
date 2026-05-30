import asyncio
import functools
import logging
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from backend.ml import device as _device

logger = logging.getLogger(__name__)


class AestheticMLP(nn.Module):
    def __init__(self, input_size: int) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_size, 1024),
            nn.Dropout(0.2),
            nn.Linear(1024, 128),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.Dropout(0.1),
            nn.Linear(64, 16),
            nn.Linear(16, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


def download_weights(dest: Path) -> None:
    from huggingface_hub import hf_hub_download
    logger.info("Downloading aesthetic predictor weights...")
    dest.parent.mkdir(parents=True, exist_ok=True)
    path = hf_hub_download(
        repo_id="camenduru/improved-aesthetic-predictor",
        filename="sac+logos+ava1-l14-linearMSE.pth",
    )
    import shutil
    shutil.copy(path, dest)
    logger.info("Aesthetic predictor weights saved to %s", dest)


def score_image_sync(image_path: str, model_entry: dict) -> float:
    from PIL import Image
    clip_model = model_entry["clip"]
    mlp = model_entry["mlp"]
    preprocess = model_entry["preprocess"]

    img = Image.open(image_path).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(_device.get_device())
    img.close()

    with torch.no_grad(), _device.autocast_ctx():
        features = clip_model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
        score = mlp(features.float()).item()

    return round(max(1.0, min(10.0, score)), 3)


WATERMARK_PROMPTS = [
    "an image with text overlay, watermark, or logo",
    "a clean image without any text or watermark",
]
def _watermark_threshold() -> float:
    from backend.config import settings
    return settings.watermark_threshold


def _precompute_watermark_text_features(model_entry: dict) -> torch.Tensor:
    import open_clip
    tokenizer = open_clip.get_tokenizer("ViT-L-14")
    tokens = tokenizer(WATERMARK_PROMPTS).to(_device.get_device())
    with torch.no_grad():
        text_feats = model_entry["clip"].encode_text(tokens)
        text_feats = text_feats / text_feats.norm(dim=-1, keepdim=True)
    return text_feats


def score_watermark_sync(image_path: str, model_entry: dict,
                          text_features: torch.Tensor,
                          watermark_threshold: float = 0.6) -> dict:
    from PIL import Image as PILImage
    img = PILImage.open(image_path).convert("RGB")
    tensor = model_entry["preprocess"](img).unsqueeze(0).to(_device.get_device())
    img.close()
    with torch.no_grad(), _device.autocast_ctx():
        img_feats = model_entry["clip"].encode_image(tensor)
        img_feats = img_feats / img_feats.norm(dim=-1, keepdim=True)
        logits = (img_feats @ text_features.T) * 100.0
        probs = logits.softmax(dim=-1)[0]
    score = float(probs[0].item())
    return {"watermark_score": round(score, 4), "has_watermark": score >= watermark_threshold}


async def score_images_watermark(
    image_paths: list[str],
    model_entry_dict: dict,
    job_id: str | None = None,
    watermark_threshold: float = 0.6,
) -> list[dict]:
    from backend.workers.progress import broadcaster

    loop = asyncio.get_event_loop()
    text_feats = _precompute_watermark_text_features(model_entry_dict)
    results = []
    total = len(image_paths)

    for i, path in enumerate(image_paths):
        try:
            fn = functools.partial(score_watermark_sync, path, model_entry_dict, text_feats, watermark_threshold)
            r = await loop.run_in_executor(None, fn)
        except Exception:
            r = {"watermark_score": 0.0, "has_watermark": False}
        results.append(r)

        if job_id and i % 10 == 0:
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "quality_score",
                "status": "running", "done": i + 1, "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "message": f"Watermark check {i + 1}/{total}",
            })

    return results


def extract_clip_embedding_sync(image_path: str, model_entry: dict) -> bytes:
    """Returns L2-normalized float16 numpy bytes, shape (768,) for ViT-L-14."""
    from PIL import Image as PILImage
    img = PILImage.open(image_path).convert("RGB")
    tensor = model_entry["preprocess"](img).unsqueeze(0).to(_device.get_device())
    img.close()
    with torch.no_grad():
        feats = model_entry["clip"].encode_image(tensor)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].cpu().numpy().astype(np.float16).tobytes()


def extract_clip_embedding_from_bytes_sync(image_bytes: bytes, model_entry: dict) -> bytes:
    """Returns L2-normalized float16 numpy bytes from raw image bytes."""
    import io
    from PIL import Image as PILImage
    img = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
    tensor = model_entry["preprocess"](img).unsqueeze(0).to(_device.get_device())
    img.close()
    with torch.no_grad():
        feats = model_entry["clip"].encode_image(tensor)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].cpu().numpy().astype(np.float16).tobytes()


async def extract_clip_embeddings_batch(
    image_paths: list[str],
    model_entry_dict: dict,
    job_id: str | None = None,
) -> list[bytes | None]:
    from backend.workers.progress import broadcaster

    loop = asyncio.get_event_loop()
    results = []
    total = len(image_paths)

    for i, path in enumerate(image_paths):
        try:
            fn = functools.partial(extract_clip_embedding_sync, path, model_entry_dict)
            r = await loop.run_in_executor(None, fn)
        except Exception:
            r = None
        results.append(r)

        if job_id and i % 10 == 0:
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "quality_score",
                "status": "running", "done": i + 1, "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "message": f"CLIP embeddings {i + 1}/{total}",
            })

    return results


async def score_images_batch(
    image_paths: list[str],
    model_entry_dict: dict,
    job_id: str | None = None,
) -> list[float]:
    from backend.workers.progress import broadcaster

    loop = asyncio.get_event_loop()
    scores = []
    total = len(image_paths)

    for i, path in enumerate(image_paths):
        try:
            score = await loop.run_in_executor(None, score_image_sync, path, model_entry_dict)
        except Exception:
            score = 0.0
        scores.append(score)

        if job_id and i % 10 == 0:
            await broadcaster.emit(job_id, {
                "type": "progress", "job_id": job_id, "job_type": "quality_score",
                "status": "running", "done": i + 1, "total": total,
                "percent": round((i + 1) / total * 100, 1),
                "message": f"Scoring image {i + 1}/{total}",
            })

    return scores
