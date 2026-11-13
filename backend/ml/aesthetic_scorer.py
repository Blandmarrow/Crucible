import functools
import logging
from collections.abc import Awaitable, Callable
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


def build_clip_vit_l14(*, quick_gelu: bool = True):
    """Build OpenAI's CLIP ViT-L/14 the way its weights were trained: the one place
    the app (and every script that must match its stored vectors) builds it.

    OpenAI trained these weights with QuickGELU. open_clip's plain `"ViT-L-14"`
    config uses `nn.GELU`, so `create_model_and_transforms("ViT-L-14",
    pretrained="openai")` silently runs the wrong activation in every layer of
    both towers. `force_quick_gelu=True` fixes that on every open_clip version
    `requirements.txt` allows, and loads the same checkpoint file.

    The build is then checked, not trusted: a future open_clip that ignored the
    flag would otherwise reintroduce the mismatch with nothing to show for it.

    `quick_gelu=False` rebuilds the old, wrong model. It exists only for
    `scripts/clip_build_delta.py`, which measures what the fix changed; nothing
    in the app may pass it.

    Returns `(model, preprocess)` on the CPU in eval mode; the caller moves it.
    """
    import open_clip

    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-L-14", pretrained="openai", force_quick_gelu=quick_gelu,
    )
    kinds = {type(m).__name__ for m in model.modules()}
    if quick_gelu and ("QuickGELU" not in kinds or "GELU" in kinds):
        raise RuntimeError(
            "open_clip built CLIP ViT-L/14 without QuickGELU despite force_quick_gelu=True; "
            "its weights expect QuickGELU, so every CLIP embedding, LAION score and "
            "watermark score would be wrong. Check the installed open_clip_torch version."
        )
    if not quick_gelu and "QuickGELU" in kinds:
        raise RuntimeError("expected the plain-GELU CLIP build, but open_clip used QuickGELU")
    return model.eval(), preprocess


# ── Each check in two halves (M10 T5) ───────────────────────────────────────────
#
# `prepare_*` is the CPU half and runs on the default pool, a few images ahead
# (`backend/ml/prefetch.py`). It takes only the transform, never the model entry,
# and returns a CPU tensor: no `.to(device)` and no model, which
# `backend/tests/test_prefetch_halves.py` holds. `infer_*` is the GPU half and runs
# on the GPU thread. The `*_sync` functions compose the two for the callers that
# score one image at a time.


def prepare_clip(image_path: str, preprocess) -> torch.Tensor:
    """CPU half shared by the aesthetic, watermark and CLIP embedding checks:
    the open_clip `preprocess` of the image, batched to (1, 3, 224, 224)."""
    from backend.ml.image_utils import open_rgb
    img = open_rgb(image_path)
    try:
        return preprocess(img).unsqueeze(0)
    finally:
        img.close()


def prepare_clip_bytes(image_bytes: bytes, preprocess) -> torch.Tensor:
    """`prepare_clip` from raw bytes (`POST /quality/embed-references`)."""
    from backend.ml.image_utils import open_rgb_bytes
    img = open_rgb_bytes(image_bytes)
    try:
        return preprocess(img).unsqueeze(0)
    finally:
        img.close()


def infer_aesthetic(tensor: torch.Tensor, model_entry: dict) -> float:
    clip_model = model_entry["clip"]
    mlp = model_entry["mlp"]
    tensor = tensor.to(_device.get_device())

    with torch.no_grad(), _device.autocast_ctx():
        features = clip_model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
        score = mlp(features.float()).item()

    return round(max(1.0, min(10.0, score)), 3)


def score_image_sync(image_path: str, model_entry: dict) -> float:
    return infer_aesthetic(prepare_clip(image_path, model_entry["preprocess"]), model_entry)


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


def infer_watermark(tensor: torch.Tensor, model_entry: dict,
                    text_features: torch.Tensor,
                    watermark_threshold: float = 0.6) -> dict:
    tensor = tensor.to(_device.get_device())
    with torch.no_grad(), _device.autocast_ctx():
        img_feats = model_entry["clip"].encode_image(tensor)
        img_feats = img_feats / img_feats.norm(dim=-1, keepdim=True)
        logits = (img_feats @ text_features.T) * 100.0
        probs = logits.softmax(dim=-1)[0]
    score = float(probs[0].item())
    return {"watermark_score": round(score, 4), "has_watermark": score >= watermark_threshold}


def score_watermark_sync(image_path: str, model_entry: dict,
                          text_features: torch.Tensor,
                          watermark_threshold: float = 0.6) -> dict:
    return infer_watermark(
        prepare_clip(image_path, model_entry["preprocess"]), model_entry,
        text_features, watermark_threshold,
    )


async def score_images_watermark(
    image_paths: list[str],
    model_entry_dict: dict,
    job_id: str | None = None,
    watermark_threshold: float = 0.6,
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> list[dict]:
    from backend.ml.prefetch import run_prefetched

    # A CUDA `encode_text`, so it goes to the GPU thread like every other GPU call
    # (PM-035). It used to run here on the event loop's thread, which built a
    # second set of CUDA handles and blocked the loop while it ran.
    text_feats = await _device.run_on_gpu(_precompute_watermark_text_features, model_entry_dict)
    results = await run_prefetched(
        image_paths,
        functools.partial(prepare_clip, preprocess=model_entry_dict["preprocess"]),
        functools.partial(
            infer_watermark, model_entry=model_entry_dict,
            text_features=text_feats, watermark_threshold=watermark_threshold,
        ),
        job_id=job_id, progress=progress,
        message="Watermark check", failure="Watermark scoring", log=logger,
    )
    # "Nothing measured", not a zero — the technical scorer's contract
    # (docs/dev/quality/scoring.md § The failure contract). A 0.0 here claimed the
    # image was confidently watermark-free about a file no decoder read, and it
    # inflated `score_coverage`. The flag stays False rather than None because
    # routers/quality.py folds it into a JSON flags dict where None is not
    # expressible.
    return [
        r if r is not None else {"watermark_score": None, "has_watermark": False}
        for r in results
    ]


def infer_clip_embedding(tensor: torch.Tensor, model_entry: dict) -> bytes:
    """Returns L2-normalized float16 numpy bytes, shape (768,) for ViT-L-14."""
    tensor = tensor.to(_device.get_device())
    with torch.no_grad():
        feats = model_entry["clip"].encode_image(tensor)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].cpu().numpy().astype(np.float16).tobytes()


def extract_clip_embedding_sync(image_path: str, model_entry: dict) -> bytes:
    """Returns L2-normalized float16 numpy bytes, shape (768,) for ViT-L-14."""
    return infer_clip_embedding(prepare_clip(image_path, model_entry["preprocess"]), model_entry)


def extract_clip_embedding_from_bytes_sync(image_bytes: bytes, model_entry: dict) -> bytes:
    """Returns L2-normalized float16 numpy bytes from raw image bytes."""
    return infer_clip_embedding(
        prepare_clip_bytes(image_bytes, model_entry["preprocess"]), model_entry,
    )


async def extract_clip_embeddings_batch(
    image_paths: list[str],
    model_entry_dict: dict,
    job_id: str | None = None,
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> list[bytes | None]:
    from backend.ml.prefetch import run_prefetched

    return await run_prefetched(
        image_paths,
        functools.partial(prepare_clip, preprocess=model_entry_dict["preprocess"]),
        functools.partial(infer_clip_embedding, model_entry=model_entry_dict),
        job_id=job_id, progress=progress,
        message="CLIP embeddings", failure="CLIP embedding", log=logger,
    )


async def score_images_batch(
    image_paths: list[str],
    model_handle,
    job_id: str | None = None,
    model: str = "laion",
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> list[float | None]:
    """Score a batch with either aesthetic producer.

    One loop for both, chosen above it rather than duplicated: the SSE cadence,
    the `cancel_requested` check and the None-on-failure contract have each had
    to be fixed once already, and a second copy would be a second place to fix
    them. `model` is the marker that will be stored on every row this batch
    scores, so the value written and the code path run are one decision.

    `model_handle` is per-model, because the two loaders return different entry
    shapes on purpose: for "laion" it is `aesthetic`'s three-tenant
    `{"clip", "mlp", "preprocess"}` dict, for "v2_5" the plain `ModelEntry`
    itself (`.model` + `.processor`). The default keeps every existing caller
    unchanged.
    """
    from backend.ml.prefetch import run_prefetched

    if model == "v2_5":
        # Imported inside the branch so a LAION run never imports the package.
        from backend.ml.aesthetic_v2_5_scorer import infer_v2_5, prepare_v2_5
        prepare = functools.partial(prepare_v2_5, processor=model_handle.processor)
        infer = functools.partial(infer_v2_5, model_entry=model_handle)
    else:
        prepare = functools.partial(prepare_clip, preprocess=model_handle["preprocess"])
        infer = functools.partial(infer_aesthetic, model_entry=model_handle)

    # A failure is NULL, not 0.0 — the technical scorer's failure contract
    # (docs/dev/quality/scoring.md § The failure contract). `aesthetic_score` is a
    # 1–10 column, so 0.0 was not merely a wrong measurement but an out-of-range
    # one, and it sorted the failure to the bottom of every "worst first" view as
    # though it had been judged.
    return await run_prefetched(
        image_paths, prepare, infer,
        job_id=job_id, progress=progress,
        message="Scoring image", failure="Aesthetic scoring", log=logger,
    )
