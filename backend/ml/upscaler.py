import logging
import re
from pathlib import Path

from backend.utils import normalize_image_format, image_save_kwargs
from backend.ml import device as _device

logger = logging.getLogger(__name__)

_extra_arches_installed = False

# The leading boundary stays strict on purpose: it is what keeps `Model_v1x`,
# `my1xmodel` and `Box4` from matching. `HAT-L_SRx4_ImageNet-pretrain` therefore
# returns None, which is accepted — relaxing the boundary would make `Box4`
# detect as 4x. The trailing side is only a "not another digit" lookahead so a
# letter may follow the x (`4xNomos8kSCHAT-L`, `RealESRGAN_x4plus`).
_SCALE_RE = re.compile(
    r"(?:^|[_\-\s])([1-8])x(?![0-9])"    # 4x-, _4x_, 4xNomos8k, 1xDeJPG
    r"|(?:^|[_\-\s])x([1-8])(?![0-9])",  # _x4, _x4plus, _X4_ (IGNORECASE)
    re.IGNORECASE,
)


def _detect_scale(name: str) -> int | None:
    m = _SCALE_RE.search(name)
    if m:
        val = m.group(1) or m.group(2)
        return int(val)
    return None


def scan_upscale_models(directory: Path) -> list[dict]:
    """Return [{name, path, scale}] for every .pth/.safetensors in directory."""
    if not directory.exists():
        return []
    results = []
    for ext in ("*.pth", "*.safetensors"):
        for p in sorted(directory.glob(ext)):
            if p.is_file():
                results.append({
                    "name": p.stem,
                    "path": str(p),
                    "scale": _detect_scale(p.stem),
                })
    return results


def upscale_image_sync(
    src: str,
    dest: str | None,
    model_path: str,
    replace: bool,
    target_width: int | None,
    target_height: int | None,
) -> dict:
    """
    Load (or reuse) an upscale model and upscale src.

    replace=True  → overwrites src in place; dest is ignored.
    replace=False → writes to dest (must be provided).
    Returns {width, height, file_size_bytes, format, out_path}.

    `out_path` is the path actually written, which is **not** the one asked for
    when `normalize_image_format` falls back to PNG (.bmp/.gif/.tiff/.avif, all
    of them ingestible). Every caller has to follow it — the row, the thumbnail
    and the file on disk must all name the same picture (PM-009).
    """
    import torch
    import numpy as np
    from PIL import Image, ImageOps

    model_id = f"upscale:{model_path}"
    device = _device.get_device()

    # Load or reuse cached model
    entry = _ensure_upscaler_loaded(model_id, model_path, device)
    upscale_model = entry.model
    scale = entry.processor  # we store detected scale in processor slot

    with torch.no_grad():
        img = Image.open(src)
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")

        arr = np.array(img, dtype=np.float32) / 255.0  # HWC [0,1]
        tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(device)  # NCHW

        h, w = tensor.shape[2], tensor.shape[3]
        TILE = 512
        OVERLAP = 64

        if h <= TILE and w <= TILE:
            out_tensor = upscale_model(tensor).clamp(0, 1)
        else:
            out_tensor = _tile_upscale(upscale_model, tensor, scale or 4, TILE, OVERLAP, device)

        img.close()
        out_arr = out_tensor.squeeze(0).permute(1, 2, 0).cpu().numpy()
        out_arr = (out_arr * 255).clip(0, 255).astype(np.uint8)
        result_img = Image.fromarray(out_arr)

    # Optional post-resize
    if target_width or target_height:
        result_img = _resize_to_target(result_img, target_width, target_height)

    out_path = src if replace else dest
    fmt, out_path = normalize_image_format(Path(src).suffix, out_path)
    save_kwargs = image_save_kwargs(fmt)

    result_img.save(out_path, format=fmt, **save_kwargs)
    stat = Path(out_path).stat()
    w_out, h_out = result_img.size
    result_img.close()

    return {
        "width": w_out,
        "height": h_out,
        "file_size_bytes": stat.st_size,
        "format": fmt,
        "out_path": out_path,
    }


def _ensure_upscaler_loaded(model_id: str, model_path: str, device: str):
    """Synchronous load — must be called from an executor thread."""
    global _extra_arches_installed
    from backend.ml.model_manager import model_manager, ModelEntry

    with model_manager._sync_lock:
        if model_id in model_manager._registry:
            entry = model_manager._registry[model_id]
            entry.last_used = __import__("time").time()
            return entry

    # Estimate VRAM: 3 GB for 4x models, 2 GB for 2x — conservative
    estimated_mb = 3000
    model_manager._evict_lru(estimated_mb)

    vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0

    from spandrel import ModelLoader
    if not _extra_arches_installed:
        try:
            import spandrel_extra_arches
            spandrel_extra_arches.install()
        except Exception as e:
            logger.debug("spandrel_extra_arches install skipped: %s", e)
        _extra_arches_installed = True

    upscale_model = None
    try:
        descriptor = ModelLoader().load_from_file(model_path)
        upscale_model = descriptor.model.eval()
        if device != "cpu":
            upscale_model = upscale_model.to(device)
        detected_scale = getattr(descriptor, "scale", None)
    except Exception:
        if upscale_model is not None:
            try:
                upscale_model.cpu()
            except Exception:
                pass
        del upscale_model
        _device.empty_cache()
        raise

    vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
    vram_used = max(estimated_mb, (vram_after - vram_before) // (1024 * 1024))
    entry = ModelEntry(upscale_model, detected_scale, vram_mb=vram_used)

    with model_manager._sync_lock:
        if model_id in model_manager._registry:
            # Another thread loaded it while we were loading — discard ours
            try:
                upscale_model.cpu()
            except Exception:
                pass
            _device.empty_cache()
            return model_manager._registry[model_id]
        model_manager._registry[model_id] = entry

    logger.info("Loaded upscaler %s (scale=%s, vram=%d MB)", model_path, detected_scale, vram_used)
    return entry


def _tile_upscale(model, tensor, scale: int, tile_size: int, overlap: int, device: str):
    """Process image in overlapping tiles and blend seams with linear ramp weights."""
    import torch

    _, c, h, w = tensor.shape
    out_h, out_w = h * scale, w * scale
    output = torch.zeros(1, c, out_h, out_w, device=device)
    weight = torch.zeros(1, 1, out_h, out_w, device=device)

    step = tile_size - overlap
    ys = list(range(0, h - tile_size, step)) + [max(0, h - tile_size)]
    xs = list(range(0, w - tile_size, step)) + [max(0, w - tile_size)]
    ys = sorted(set(ys))
    xs = sorted(set(xs))

    # 1-D linear ramp for blending: 1 at center, fades to 0 at edges over overlap px
    def ramp(size: int) -> "torch.Tensor":
        r = torch.ones(size, device=device)
        if overlap > 0:
            fade = torch.linspace(0, 1, overlap, device=device)
            r[:overlap] = fade
            r[-overlap:] = fade.flip(0)
        return r

    for y in ys:
        for x in xs:
            y2 = min(y + tile_size, h)
            x2 = min(x + tile_size, w)
            tile = tensor[:, :, y:y2, x:x2]
            with torch.no_grad():
                out_tile = model(tile).clamp(0, 1)

            oy, ox = y * scale, x * scale
            oy2, ox2 = oy + out_tile.shape[2], ox + out_tile.shape[3]

            ry = ramp(out_tile.shape[2])
            rx = ramp(out_tile.shape[3])
            w2d = (ry.unsqueeze(1) * rx.unsqueeze(0)).unsqueeze(0).unsqueeze(0)  # 1,1,H,W

            output[:, :, oy:oy2, ox:ox2] += out_tile * w2d
            weight[:, :, oy:oy2, ox:ox2] += w2d

    weight = weight.clamp(min=1e-6)
    return output / weight


def _resize_to_target(img, target_width: int | None, target_height: int | None):
    """Resize to fit within target dimensions, maintaining aspect ratio."""
    from PIL import Image
    w, h = img.size
    if target_width and target_height:
        scale = min(target_width / w, target_height / h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    elif target_width:
        img = img.resize((target_width, round(h * target_width / w)), Image.LANCZOS)
    elif target_height:
        img = img.resize((round(w * target_height / h), target_height), Image.LANCZOS)
    return img
