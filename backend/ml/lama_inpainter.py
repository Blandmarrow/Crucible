"""LaMa inpainting — paint a masked region out of an image.

The weights are the TorchScript archive `big-lama.pt` published with
`simple-lama-inpainting`. TorchScript rather than a package or a bare state_dict
because both alternatives are unusable here: `simple-lama-inpainting` pins
`Pillow<10` and `numpy<2` (this repo runs Pillow 12 / numpy 2), `IOPaint` pins
`fastapi==0.108.0` plus gradio, and the HuggingFace `big-lama` mirror is a bare
state_dict that would require vendoring LaMa's FFC ResNet generator. A
TorchScript archive carries its own architecture, so `torch.jit.load` plus the
pre/post-processing below is the entire inference path — which is what
`simple-lama` does internally.

It **is** a pickle, which the SAM 3 loader refuses. That rule is SAM-3-specific
and stated in `sam3_predictor.py`'s own docstring, not repo-wide —
`scan_upscale_models` already loads arbitrary `*.pth` through spandrel. The
mitigation here is stronger than either: `_WEIGHTS_SHA256` pins the exact archive
and `_ensure_weights` deletes and refuses anything that does not match, so the
bytes `torch.jit.load` unpickles are the bytes that were reviewed.

LaMa's code is Apache-2.0 (`advimman/lama`). The weights are Places2-trained and
distributed through community mirrors.

`torch` is imported inside each function, never at module scope: a module-level
import would force every test that so much as imports this module to carry
`conftest.needs_torch` and become invisible to CI, which installs
`requirements-ci.txt` and will never have torch.
"""

import hashlib
import logging
from pathlib import Path

from backend.config import settings
from backend.ml import device as _device
from backend.utils import image_save_kwargs, normalize_image_format

logger = logging.getLogger(__name__)

_WEIGHTS_URL = (
    "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/"
    "v0.1.0/big-lama.pt"
)
_WEIGHTS_FILENAME = "big-lama.pt"
# SHA-256 of the archive at _WEIGHTS_URL. A mismatch is a hard refusal, not a
# warning: this file is unpickled, so the hash is the only thing standing between
# a mirror swap and arbitrary code execution.
_WEIGHTS_SHA256 = "7ba7aa7ac37a4d41fdbbeba3a2af7ead18058552997e3a3cd1a3b2210c9e6b4c"
_WEIGHTS_MB = 196

# LaMa is fully convolutional, so it accepts any size whose sides are a multiple
# of 8 — but it was trained at 512² and a 4K image would blow up VRAM. We run it
# on a padded crop around the mask instead and composite the result back, which
# bounds the cost independently of the source resolution and keeps every pixel
# outside the mask bit-identical to the original.
_PAD_DIVISOR = 8
_CROP_MARGIN_FRAC = 0.5   # expand the mask bbox by 50% of its size on each axis
_CROP_MARGIN_MIN = 64     # ...but never by less than this many pixels
_CROP_MAX_SIDE = 1536     # downscale the crop for inference above this


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _ensure_weights(
    job_id: str | None = None,
    loop=None,
    dataset_id: str | None = None,
) -> Path:
    """Return the local weights path, downloading and verifying it on first use.

    Modelled on `aesthetic_scorer.download_weights`, but over a plain GET rather
    than `hf_hub_download` — so progress goes through `emit_sync` directly.
    `progress_tqdm_patch` is a `huggingface_hub` monkeypatch and does not apply.
    """
    dest = settings.models_cache_dir / _WEIGHTS_FILENAME

    if dest.exists():
        actual = _sha256(dest)
        if actual == _WEIGHTS_SHA256:
            return dest
        # A truncated or tampered cache file is deleted rather than re-used: the
        # next call re-downloads instead of failing forever.
        logger.warning(
            "LaMa weights at %s have SHA-256 %s, expected %s — deleting and re-downloading",
            dest, actual, _WEIGHTS_SHA256,
        )
        dest.unlink(missing_ok=True)

    import urllib.request

    from backend.ml.download_progress import emit_sync

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    logger.info("Downloading LaMa weights (~%d MB) from %s", _WEIGHTS_MB, _WEIGHTS_URL)
    if job_id and loop:
        emit_sync(
            job_id, loop,
            f"Downloading LaMa inpainting weights (first run, ~{_WEIGHTS_MB} MB)...",
            0.0, dataset_id,
        )

    try:
        with urllib.request.urlopen(_WEIGHTS_URL, timeout=60) as resp:  # noqa: S310
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            next_emit = 0
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if job_id and loop and done >= next_emit:
                        # Emit roughly every 4 MB rather than every chunk.
                        next_emit = done + 4 * 1024 * 1024
                        pct = (done / total * 100.0) if total else -1.0
                        emit_sync(
                            job_id, loop,
                            "Downloading LaMa inpainting weights "
                            f"({done / 1_048_576:.0f} / {(total or 0) / 1_048_576:.0f} MB)",
                            pct, dataset_id,
                        )
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    actual = _sha256(tmp)
    if actual != _WEIGHTS_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Downloaded LaMa weights have SHA-256 {actual}, expected "
            f"{_WEIGHTS_SHA256}. Refusing to load — the archive is a pickle and "
            "an unverified one is arbitrary code. Delete "
            f"{dest.parent / (_WEIGHTS_FILENAME + '.part')} and retry, or check "
            f"{_WEIGHTS_URL}."
        )
    tmp.replace(dest)
    logger.info("LaMa weights saved to %s", dest)
    return dest


def _load_lama_sync(job_id=None, loop=None, dataset_id=None):
    """Load the TorchScript LaMa generator. Must be called from an executor thread."""
    import torch

    from backend.ml.download_progress import emit_sync
    from backend.ml.model_manager import ModelEntry

    # Resolve and verify the weights before touching the GPU: fail fast, no VRAM
    # spent on a download that turns out to be corrupt.
    weights = _ensure_weights(job_id, loop, dataset_id)

    dev = _device.get_device()
    vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0

    if job_id and loop:
        emit_sync(job_id, loop, "Loading LaMa inpainting model...", -1.0, dataset_id)

    model = None
    try:
        model = torch.jit.load(str(weights), map_location=dev)
        model.eval()
    except Exception:
        # The failure tail every loader in model_manager.py carries: get the
        # weights off the accelerator before the exception unwinds, or a partial
        # load leaks VRAM for the life of the process.
        if model is not None:
            try:
                model.cpu()
            except Exception:
                pass
            del model
        _device.empty_cache()
        raise

    if job_id and loop:
        emit_sync(job_id, loop, "LaMa loaded.", -1.0, dataset_id)

    vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
    delta_mb = (vram_after - vram_before) // (1024 * 1024)
    vram_used = max(2000, delta_mb) if _device.is_gpu_available() else 0
    return ModelEntry(model, None, vram_mb=vram_used)


def _crop_box(mask, img_w: int, img_h: int) -> tuple[int, int, int, int] | None:
    """The padded, 8-aligned box around the mask's white pixels, clamped to the image.

    Returns None when the mask is empty — nothing to paint, and running the model
    on the whole image would be a pure re-encode.
    """
    bbox = mask.getbbox()
    if bbox is None:
        return None
    x1, y1, x2, y2 = bbox
    mw, mh = x2 - x1, y2 - y1
    pad_x = max(_CROP_MARGIN_MIN, int(mw * _CROP_MARGIN_FRAC))
    pad_y = max(_CROP_MARGIN_MIN, int(mh * _CROP_MARGIN_FRAC))
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(img_w, x2 + pad_x)
    y2 = min(img_h, y2 + pad_y)
    # Grow the span to a multiple of 8 where there is room, shrink it where there
    # is not, so the tensor handed to the model always satisfies LaMa's stride.
    def _align(lo: int, hi: int, limit: int) -> tuple[int, int]:
        need = -(hi - lo) % _PAD_DIVISOR
        if not need:
            return lo, hi
        grow_hi = min(need, limit - hi)
        hi += grow_hi
        grow_lo = min(need - grow_hi, lo)
        lo -= grow_lo
        # The whole axis is shorter than the next multiple of 8: shrink instead.
        hi -= (hi - lo) % _PAD_DIVISOR
        return lo, hi
    x1, x2 = _align(x1, x2, img_w)
    y1, y2 = _align(y1, y2, img_h)
    if x2 - x1 < _PAD_DIVISOR or y2 - y1 < _PAD_DIVISOR:
        return None
    return x1, y1, x2, y2


def inpaint_image_sync(
    src: str,
    dest: str | None,
    mask_png_bytes: bytes,
    replace: bool,
) -> dict:
    """Paint the masked region of `src` out and write the result.

    `mask_png_bytes` is a PNG-encoded grayscale mask at the source's
    EXIF-transposed dimensions: white (255) marks what to paint over.

    replace=True  → overwrites src in place; dest is ignored.
    replace=False → writes to dest (must be provided).
    Returns {width, height, file_size_bytes, format, phash, out_path}.

    `out_path` is the path actually written, which is **not** the one asked for
    when `normalize_image_format` falls back to PNG (.bmp/.gif/.tiff/.avif, all
    of them ingestible). Every caller has to follow it — the row, the thumbnail
    and the file on disk must all name the same picture (PM-009).

    `phash` is returned and every caller must store it, unlike
    `upscale_image_sync`'s omission: inpainting changes pixels without changing
    geometry, and `phash` is the only thing duplicate detection keys on, so a
    stale one is the entire failure mode.
    """
    import io

    import imagehash
    import numpy as np
    import torch
    from PIL import Image as PilImage

    from backend.ml.image_utils import open_rgb
    from backend.ml.model_manager import model_manager

    # The resident entry, read under the registry's threading lock — this runs in
    # an executor thread, where `ModelManager.get` (a coroutine) is unusable. The
    # caller awaits `model_manager.load_lama()` once before the loop, which is
    # where eviction and the first-run download happen.
    with model_manager._sync_lock:
        entry = model_manager._registry.get("lama")
    if entry is None:
        raise RuntimeError(
            "LaMa model is not loaded — await model_manager.load_lama() before "
            "calling inpaint_image_sync"
        )
    model = entry.model
    device = _device.get_device()

    # open_rgb, never a bare Image.open: detection polygons are normalized
    # against the EXIF-transposed frame, so any other open misplaces the mask.
    img = open_rgb(src)
    img_w, img_h = img.size

    mask = PilImage.open(io.BytesIO(mask_png_bytes)).convert("L")
    if mask.size != (img_w, img_h):
        mask = mask.resize((img_w, img_h), PilImage.Resampling.NEAREST)

    box = _crop_box(mask, img_w, img_h)
    if box is None:
        img.close()
        mask.close()
        raise ValueError("Inpaint mask is empty — nothing to paint over")

    crop_rgb = img.crop(box)
    crop_mask = mask.crop(box)
    cw, ch = crop_rgb.size

    # Downscale the crop for inference when it is very large, then scale the
    # painted result back. LaMa was trained at 512²; a 3000px crop costs VRAM
    # without improving the fill.
    scale = min(1.0, _CROP_MAX_SIDE / max(cw, ch))
    if scale < 1.0:
        iw = max(_PAD_DIVISOR, (int(cw * scale) // _PAD_DIVISOR) * _PAD_DIVISOR)
        ih = max(_PAD_DIVISOR, (int(ch * scale) // _PAD_DIVISOR) * _PAD_DIVISOR)
        infer_rgb = crop_rgb.resize((iw, ih), PilImage.Resampling.LANCZOS)
        infer_mask = crop_mask.resize((iw, ih), PilImage.Resampling.NEAREST)
    else:
        infer_rgb, infer_mask = crop_rgb, crop_mask

    rgb_arr = np.asarray(infer_rgb, dtype=np.float32) / 255.0          # HWC [0,1]
    mask_arr = (np.asarray(infer_mask, dtype=np.float32) > 127.0).astype(np.float32)  # HW {0,1}

    # Close every PIL image the moment its pixels are in a tensor, before the
    # (slow) inference runs — the "Close PIL Images after preprocessing"
    # invariant. `crop_rgb` and `crop_mask` are still needed for the composite,
    # so only the inference-sized copies go here when they are distinct.
    if infer_rgb is not crop_rgb:
        infer_rgb.close()
        infer_mask.close()
    img.close()

    with torch.no_grad():
        rgb_t = torch.from_numpy(rgb_arr).permute(2, 0, 1).unsqueeze(0).to(device)
        mask_t = torch.from_numpy(mask_arr).unsqueeze(0).unsqueeze(0).to(device)
        out_t = model(rgb_t, mask_t)
        out_arr = out_t[0].permute(1, 2, 0).clamp(0, 1).cpu().numpy()

    painted = PilImage.fromarray((out_arr * 255).round().astype(np.uint8), mode="RGB")
    if painted.size != (cw, ch):
        painted = painted.resize((cw, ch), PilImage.Resampling.LANCZOS)

    # Composite through the mask rather than pasting the whole crop: every pixel
    # outside the mask stays bit-identical to the source instead of making a
    # round trip through the model.
    # Re-decode rather than hold the full-size image open across the inference:
    # the "Close PIL Images after preprocessing" invariant is about not paying
    # for a multi-MB pixel buffer during the slow part, and a second decode is
    # the cheaper half of that trade.
    result = open_rgb(src)
    result.paste(painted, (box[0], box[1]), crop_mask)
    painted.close()
    crop_rgb.close()
    crop_mask.close()
    mask.close()

    out_path = src if replace else dest
    fmt, out_path = normalize_image_format(Path(src).suffix, out_path)
    save_kwargs = image_save_kwargs(fmt)

    phash_str = str(imagehash.phash(result))
    result.save(out_path, format=fmt, **save_kwargs)
    w_out, h_out = result.size
    result.close()

    return {
        "width": w_out,
        "height": h_out,
        "file_size_bytes": Path(out_path).stat().st_size,
        "format": fmt,
        "phash": phash_str,
        "out_path": out_path,
    }
