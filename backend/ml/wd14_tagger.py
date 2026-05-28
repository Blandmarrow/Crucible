import csv
import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Static list of supported WD14 variants (all SmilingWolf on HuggingFace, public weights)
_VARIANTS = {
    "eva02_large": {
        "id": "eva02_large",
        "name": "WD Eva02 Large v3 (best quality, ~2 GB)",
        "repo": "SmilingWolf/wd-eva02-large-tagger-v3",
    },
    "vit_large": {
        "id": "vit_large",
        "name": "WD ViT Large v3",
        "repo": "SmilingWolf/wd-vit-large-tagger-v3",
    },
    "swinv2": {
        "id": "swinv2",
        "name": "WD SwinV2 v3 (fastest)",
        "repo": "SmilingWolf/wd-v1-4-swinv2-tagger-v3",
    },
}

# Module-level cache: variant_id -> (session, tag_names)
_cache: dict[str, tuple] = {}
_cache_lock = threading.Lock()


def list_wd14_models() -> list[dict]:
    return [{"id": f"wd14:{v['id']}", "name": v["name"]} for v in _VARIANTS.values()]


def _load_model(variant_id: str) -> tuple:
    with _cache_lock:
        if variant_id in _cache:
            return _cache[variant_id]

    info = _VARIANTS.get(variant_id)
    if info is None:
        raise ValueError(f"Unknown WD14 variant: {variant_id}")

    try:
        import onnxruntime as ort
    except ImportError:
        raise RuntimeError("onnxruntime not installed; run: pip install onnxruntime>=1.18")

    from huggingface_hub import hf_hub_download

    logger.info("Downloading WD14 model %s ...", info["repo"])
    model_path = hf_hub_download(repo_id=info["repo"], filename="model.onnx")
    tags_path = hf_hub_download(repo_id=info["repo"], filename="selected_tags.csv")

    # Load tag names from CSV (columns: tag_id, name, category)
    tag_names: list[str] = []
    with open(tags_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tag_names.append(row["name"])

    sess_options = ort.SessionOptions()
    sess_options.log_severity_level = 3  # suppress verbose ONNX logs
    session = ort.InferenceSession(model_path, sess_options=sess_options, providers=["CPUExecutionProvider"])

    with _cache_lock:
        if variant_id not in _cache:  # double-check after acquiring lock
            _cache[variant_id] = (session, tag_names)
            logger.info("WD14 model loaded: %d tags", len(tag_names))
    return _cache[variant_id]


def _preprocess(image_path: str, size: int = 448) -> np.ndarray:
    img = Image.open(image_path).convert("RGB")
    img = ImageOps.exif_transpose(img)

    # Pad to square then resize
    w, h = img.size
    max_side = max(w, h)
    padded = Image.new("RGB", (max_side, max_side), (255, 255, 255))
    padded.paste(img, ((max_side - w) // 2, (max_side - h) // 2))
    padded = padded.resize((size, size), Image.Resampling.BICUBIC)
    img.close()

    # WD14 models expect BGR float32 in [0, 255] (not normalised to [0,1])
    arr = np.array(padded, dtype=np.float32)
    padded.close()
    arr = arr[:, :, ::-1]  # RGB -> BGR
    arr = np.expand_dims(arr, axis=0)  # (1, H, W, C)
    return arr


def tag_image_sync(image_path: str, variant_id: str, threshold: float = 0.35) -> str:
    session, tag_names = _load_model(variant_id)
    arr = _preprocess(image_path)
    input_name = session.get_inputs()[0].name
    preds = session.run(None, {input_name: arr})[0][0]  # shape (num_tags,)

    pairs = sorted(
        ((tag_names[i], float(preds[i])) for i in range(len(tag_names)) if float(preds[i]) >= threshold),
        key=lambda x: x[1],
        reverse=True,
    )
    return ", ".join(name for name, _ in pairs)
