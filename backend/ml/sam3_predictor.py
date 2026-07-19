"""SAM 3 — native open-vocabulary text-prompt segmentation.

Unlike the SAM2 path (Grounding DINO boxes → SAM2 mask per box), SAM 3 finds and
masks every instance of a text phrase in a single pass.

Checkpoint policy: safetensors only, never .pt pickles. The checkpoint is loaded
from `settings.sam3_models_dir` (download sam3.safetensors from
https://huggingface.co/1038lab/sam3 — the ungated mirror). The mirror stores the
image model under a `detector_model.` prefix plus tracker weights we never use,
and — despite its README claiming sam3-package compatibility — uses the
HuggingFace-transformers Sam3 tensor naming (split q/k/v projections, stripped
cls position embedding, transposed CLIP text projection). `_rewrite_state_dict`
detects the format and converts HF-format weights back to the native sam3
package naming, logging match counts so a silent total mismatch under
strict=False is impossible.
"""

import contextlib
import json
import logging
from pathlib import Path

import numpy as np

from backend.config import settings
from backend.ml import device as _device
from backend.ml.image_utils import open_rgb
from backend.ml.mask_utils import bbox_from_mask, masks_to_polygons

logger = logging.getLogger(__name__)

_MIRROR_URL = "https://huggingface.co/1038lab/sam3"

# Checkpoint prefixes belonging to the video tracker — never used (we build with
# enable_inst_interactivity=False), always dropped before loading.
_TRACKER_PREFIXES = ("tracker_model.", "tracker_neck.")


def _resolve_checkpoint() -> Path:
    """Newest *.safetensors in settings.sam3_models_dir; never falls back to .pt."""
    ckpt_dir = settings.sam3_models_dir
    candidates = sorted(
        ckpt_dir.glob("*.safetensors"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            f"No SAM3 checkpoint found in {ckpt_dir}. Download sam3.safetensors "
            f"from {_MIRROR_URL} and place it in models/sam3/. "
            "(.pt checkpoints are not supported — safetensors only.)"
        )
    return candidates[0]


# Ordered native→HF key rename rules, taken verbatim from transformers'
# convert_sam3_to_hf.py (ORIGINAL_TO_CONVERTED_KEY_MAPPING). Applied
# sequentially per key — later rules operate on earlier rules' output, exactly
# like the official converter. Hardcoded patterns, so stdlib `re` is fine.
# fmt: off
_NATIVE_TO_HF_RULES: list[tuple[str, str]] = [
    # Vision Encoder - ViT trunk
    (r"^backbone\.vision_backbone\.trunk\.",                             r"vision_encoder.backbone."),
    (r"^vision_encoder\.backbone\.pos_embed",                            r"vision_encoder.backbone.embeddings.position_embeddings"),
    (r"^vision_encoder\.backbone\.patch_embed\.proj\.",                  r"vision_encoder.backbone.embeddings.patch_embeddings.projection."),
    (r"^vision_encoder\.backbone\.ln_pre\.",                             r"vision_encoder.backbone.layer_norm."),
    (r"^vision_encoder\.backbone\.blocks\.(\d+)\.norm1\.",               r"vision_encoder.backbone.layers.\1.layer_norm1."),
    (r"^vision_encoder\.backbone\.blocks\.(\d+)\.norm2\.",               r"vision_encoder.backbone.layers.\1.layer_norm2."),
    (r"^vision_encoder\.backbone\.blocks\.(\d+)\.attn\.qkv\.",           r"vision_encoder.backbone.layers.\1.attention.qkv."),
    (r"^vision_encoder\.backbone\.blocks\.(\d+)\.attn\.proj\.",          r"vision_encoder.backbone.layers.\1.attention.o_proj."),
    (r"^vision_encoder\.backbone\.blocks\.(\d+)\.attn\.freqs_cis",       r"vision_encoder.backbone.layers.\1.rotary_emb.rope_embeddings"),
    (r"^vision_encoder\.backbone\.blocks\.(\d+)\.mlp\.fc1\.",            r"vision_encoder.backbone.layers.\1.mlp.fc1."),
    (r"^vision_encoder\.backbone\.blocks\.(\d+)\.mlp\.fc2\.",            r"vision_encoder.backbone.layers.\1.mlp.fc2."),
    # Vision Encoder - FPN neck
    (r"^backbone\.vision_backbone\.neck\.fpn\.(\d+)\.",                  r"vision_encoder.neck.fpn_layers.\1."),
    (r"^backbone\.vision_backbone\.convs\.(\d+)\.dconv_2x2_0\.",         r"vision_encoder.neck.fpn_layers.\1.scale_layers.0."),
    (r"^backbone\.vision_backbone\.convs\.(\d+)\.dconv_2x2_1\.",         r"vision_encoder.neck.fpn_layers.\1.scale_layers.2."),
    (r"^backbone\.vision_backbone\.convs\.(\d+)\.dconv_2x2\.",           r"vision_encoder.neck.fpn_layers.\1.scale_layers.0."),
    (r"^backbone\.vision_backbone\.convs\.(\d+)\.maxpool_2x2\.",         r"vision_encoder.neck.fpn_layers.\1.scale_layers.0."),
    (r"^backbone\.vision_backbone\.convs\.(\d+)\.conv_1x1\.",            r"vision_encoder.neck.fpn_layers.\1.proj1."),
    (r"^backbone\.vision_backbone\.convs\.(\d+)\.conv_3x3\.",            r"vision_encoder.neck.fpn_layers.\1.proj2."),
    # Text Encoder (CLIP)
    (r"^backbone\.language_backbone\.encoder\.",                         r"text_encoder."),
    (r"^text_encoder\.token_embedding\.",                                r"text_encoder.text_model.embeddings.token_embedding."),
    (r"^text_encoder\.positional_embedding",                             r"text_encoder.text_model.embeddings.position_embedding.weight"),
    (r"^text_encoder\.ln_final\.",                                       r"text_encoder.text_model.final_layer_norm."),
    (r"^text_encoder\.text_projection",                                  r"text_encoder.text_projection.weight"),
    (r"^text_encoder\.transformer\.resblocks\.(\d+)\.attn\.in_proj_",    r"text_encoder.text_model.encoder.layers.\1.self_attn.in_proj_"),
    (r"^text_encoder\.transformer\.resblocks\.(\d+)\.attn\.out_proj\.",  r"text_encoder.text_model.encoder.layers.\1.self_attn.out_proj."),
    (r"^text_encoder\.transformer\.resblocks\.(\d+)\.ln_1\.",            r"text_encoder.text_model.encoder.layers.\1.layer_norm1."),
    (r"^text_encoder\.transformer\.resblocks\.(\d+)\.ln_2\.",            r"text_encoder.text_model.encoder.layers.\1.layer_norm2."),
    (r"^text_encoder\.transformer\.resblocks\.(\d+)\.mlp\.c_fc\.",       r"text_encoder.text_model.encoder.layers.\1.mlp.fc1."),
    (r"^text_encoder\.transformer\.resblocks\.(\d+)\.mlp\.c_proj\.",     r"text_encoder.text_model.encoder.layers.\1.mlp.fc2."),
    (r"^backbone\.language_backbone\.resizer\.",                         r"text_projection."),
    # Geometry Encoder
    (r"^geometry_encoder\.encode\.(\d+)\.cross_attn_image\.out_proj\.",  r"geometry_encoder.layers.\1.cross_attn.o_proj."),
    (r"^geometry_encoder\.encode\.(\d+)\.cross_attn_image\.",            r"geometry_encoder.layers.\1.cross_attn."),
    (r"^geometry_encoder\.encode\.(\d+)\.self_attn\.out_proj\.",         r"geometry_encoder.layers.\1.self_attn.o_proj."),
    (r"^geometry_encoder\.encode\.(\d+)\.self_attn\.",                   r"geometry_encoder.layers.\1.self_attn."),
    (r"^geometry_encoder\.encode\.(\d+)\.linear1\.",                     r"geometry_encoder.layers.\1.mlp.fc1."),
    (r"^geometry_encoder\.encode\.(\d+)\.linear2\.",                     r"geometry_encoder.layers.\1.mlp.fc2."),
    (r"^geometry_encoder\.encode\.(\d+)\.norm1\.",                       r"geometry_encoder.layers.\1.layer_norm1."),
    (r"^geometry_encoder\.encode\.(\d+)\.norm2\.",                       r"geometry_encoder.layers.\1.layer_norm2."),
    (r"^geometry_encoder\.encode\.(\d+)\.norm3\.",                       r"geometry_encoder.layers.\1.layer_norm3."),
    (r"^geometry_encoder\.img_pre_norm\.",                               r"geometry_encoder.vision_layer_norm."),
    (r"^geometry_encoder\.norm\.",                                       r"geometry_encoder.prompt_layer_norm."),
    (r"^geometry_encoder\.encode_norm\.",                                r"geometry_encoder.output_layer_norm."),
    # DETR Encoder
    (r"^transformer\.encoder\.layers\.(\d+)\.cross_attn_image\.out_proj\.", r"detr_encoder.layers.\1.cross_attn.o_proj."),
    (r"^transformer\.encoder\.layers\.(\d+)\.cross_attn_image\.",        r"detr_encoder.layers.\1.cross_attn."),
    (r"^transformer\.encoder\.layers\.(\d+)\.self_attn\.out_proj\.",     r"detr_encoder.layers.\1.self_attn.o_proj."),
    (r"^transformer\.encoder\.layers\.(\d+)\.self_attn\.",               r"detr_encoder.layers.\1.self_attn."),
    (r"^transformer\.encoder\.layers\.(\d+)\.cross_attn\.out_proj\.",    r"detr_encoder.layers.\1.cross_attn.o_proj."),
    (r"^transformer\.encoder\.layers\.(\d+)\.cross_attn\.",              r"detr_encoder.layers.\1.cross_attn."),
    (r"^transformer\.encoder\.layers\.(\d+)\.linear1\.",                 r"detr_encoder.layers.\1.mlp.fc1."),
    (r"^transformer\.encoder\.layers\.(\d+)\.linear2\.",                 r"detr_encoder.layers.\1.mlp.fc2."),
    (r"^transformer\.encoder\.layers\.(\d+)\.norm1\.",                   r"detr_encoder.layers.\1.layer_norm1."),
    (r"^transformer\.encoder\.layers\.(\d+)\.norm2\.",                   r"detr_encoder.layers.\1.layer_norm2."),
    (r"^transformer\.encoder\.layers\.(\d+)\.norm3\.",                   r"detr_encoder.layers.\1.layer_norm3."),
    # DETR Decoder
    (r"^transformer\.decoder\.query_embed\.",                            r"detr_decoder.query_embed."),
    (r"^transformer\.decoder\.reference_points\.",                       r"detr_decoder.reference_points."),
    (r"^transformer\.decoder\.instance_query_embed\.",                   r"detr_decoder.instance_query_embed."),
    (r"^transformer\.decoder\.instance_reference_points\.",              r"detr_decoder.instance_reference_points."),
    (r"^transformer\.decoder\.presence_token\.",                         r"detr_decoder.presence_token."),
    (r"^transformer\.decoder\.presence_token_head\.layers\.0\.",         r"detr_decoder.presence_head.layer1."),
    (r"^transformer\.decoder\.presence_token_head\.layers\.1\.",         r"detr_decoder.presence_head.layer2."),
    (r"^transformer\.decoder\.presence_token_head\.layers\.2\.",         r"detr_decoder.presence_head.layer3."),
    (r"^transformer\.decoder\.presence_token_out_norm\.",                r"detr_decoder.presence_layer_norm."),
    (r"^transformer\.decoder\.norm\.",                                   r"detr_decoder.output_layer_norm."),
    (r"^transformer\.decoder\.bbox_embed\.layers\.0\.",                  r"detr_decoder.box_head.layer1."),
    (r"^transformer\.decoder\.bbox_embed\.layers\.1\.",                  r"detr_decoder.box_head.layer2."),
    (r"^transformer\.decoder\.bbox_embed\.layers\.2\.",                  r"detr_decoder.box_head.layer3."),
    (r"^transformer\.decoder\.instance_bbox_embed\.layers\.0\.",         r"detr_decoder.instance_box_head.layer1."),
    (r"^transformer\.decoder\.instance_bbox_embed\.layers\.1\.",         r"detr_decoder.instance_box_head.layer2."),
    (r"^transformer\.decoder\.instance_bbox_embed\.layers\.2\.",         r"detr_decoder.instance_box_head.layer3."),
    (r"^transformer\.decoder\.ref_point_head\.layers\.0\.",              r"detr_decoder.ref_point_head.layer1."),
    (r"^transformer\.decoder\.ref_point_head\.layers\.1\.",              r"detr_decoder.ref_point_head.layer2."),
    (r"^transformer\.decoder\.boxRPB_embed_x\.layers\.0\.",              r"detr_decoder.box_rpb_embed_x.layer1."),
    (r"^transformer\.decoder\.boxRPB_embed_x\.layers\.1\.",              r"detr_decoder.box_rpb_embed_x.layer2."),
    (r"^transformer\.decoder\.boxRPB_embed_y\.layers\.0\.",              r"detr_decoder.box_rpb_embed_y.layer1."),
    (r"^transformer\.decoder\.boxRPB_embed_y\.layers\.1\.",              r"detr_decoder.box_rpb_embed_y.layer2."),
    (r"^transformer\.decoder\.layers\.(\d+)\.self_attn\.out_proj\.",     r"detr_decoder.layers.\1.self_attn.o_proj."),
    (r"^transformer\.decoder\.layers\.(\d+)\.self_attn\.",               r"detr_decoder.layers.\1.self_attn."),
    (r"^transformer\.decoder\.layers\.(\d+)\.ca_text\.out_proj\.",       r"detr_decoder.layers.\1.text_cross_attn.o_proj."),
    (r"^transformer\.decoder\.layers\.(\d+)\.ca_text\.",                 r"detr_decoder.layers.\1.text_cross_attn."),
    (r"^transformer\.decoder\.layers\.(\d+)\.cross_attn\.out_proj\.",    r"detr_decoder.layers.\1.vision_cross_attn.o_proj."),
    (r"^transformer\.decoder\.layers\.(\d+)\.cross_attn\.",              r"detr_decoder.layers.\1.vision_cross_attn."),
    (r"^transformer\.decoder\.layers\.(\d+)\.linear1\.",                 r"detr_decoder.layers.\1.mlp.fc1."),
    (r"^transformer\.decoder\.layers\.(\d+)\.linear2\.",                 r"detr_decoder.layers.\1.mlp.fc2."),
    (r"^transformer\.decoder\.layers\.(\d+)\.norm1\.",                   r"detr_decoder.layers.\1.vision_cross_attn_layer_norm."),
    (r"^transformer\.decoder\.layers\.(\d+)\.catext_norm\.",             r"detr_decoder.layers.\1.text_cross_attn_layer_norm."),
    (r"^transformer\.decoder\.layers\.(\d+)\.norm2\.",                   r"detr_decoder.layers.\1.self_attn_layer_norm."),
    (r"^transformer\.decoder\.layers\.(\d+)\.norm3\.",                   r"detr_decoder.layers.\1.mlp_layer_norm."),
    # Dot Product Scoring
    (r"^dot_prod_scoring\.prompt_mlp\.layers\.0\.",                      r"dot_product_scoring.text_mlp.layer1."),
    (r"^dot_prod_scoring\.prompt_mlp\.layers\.1\.",                      r"dot_product_scoring.text_mlp.layer2."),
    (r"^dot_prod_scoring\.prompt_mlp\.out_norm\.",                       r"dot_product_scoring.text_mlp_out_norm."),
    (r"^dot_prod_scoring\.prompt_proj\.",                                r"dot_product_scoring.text_proj."),
    (r"^dot_prod_scoring\.hs_proj\.",                                    r"dot_product_scoring.query_proj."),
    # Mask Decoder
    (r"^segmentation_head\.pixel_decoder\.conv_layers\.(\d+)\.",         r"mask_decoder.pixel_decoder.conv_layers.\1."),
    (r"^segmentation_head\.pixel_decoder\.norms\.(\d+)\.",               r"mask_decoder.pixel_decoder.norms.\1."),
    (r"^segmentation_head\.mask_embed\.layers\.(\d+)\.",                 r"mask_decoder.mask_embedder.layers.\1."),
    (r"^segmentation_head\.mask_predictor\.mask_embed\.layers\.(\d+)\.", r"mask_decoder.mask_embedder.layers.\1."),
    (r"^segmentation_head\.instance_seg_head\.",                         r"mask_decoder.instance_projection."),
    (r"^segmentation_head\.semantic_seg_head\.",                         r"mask_decoder.semantic_projection."),
    (r"^segmentation_head\.cross_attend_prompt\.out_proj\.",             r"mask_decoder.prompt_cross_attn.o_proj."),
    (r"^segmentation_head\.cross_attend_prompt\.",                       r"mask_decoder.prompt_cross_attn."),
    (r"^segmentation_head\.cross_attn_norm\.",                           r"mask_decoder.prompt_cross_attn_norm."),
]
# fmt: on

_POS_EMBED_HF_KEY = "vision_encoder.backbone.embeddings.position_embeddings"
_TEXT_PROJ_HF_KEY = "text_encoder.text_projection.weight"


def _is_expected_missing(native_key: str) -> bool:
    """Native keys known to be absent from HF-format checkpoints, harmlessly.

    - `attn.freqs_cis`: precomputed rotary buffers, rebuilt deterministically
      at model build time (HF computes them on the fly).
    - `geometry_encoder.points_*`: point-prompt projections dropped by the HF
      conversion; with text (or box) prompts _encode_points only ever sees a
      zero-length point sequence, so these weights never influence output.
    """
    return native_key.endswith(".attn.freqs_cis") or native_key.startswith(
        "geometry_encoder.points_"
    )


def _native_to_hf_key(key: str) -> str:
    import re
    for pattern, repl in _NATIVE_TO_HF_RULES:
        key = re.sub(pattern, repl, key)
    return key


def _fetch_hf_tensor(ckpt: dict, hf_key: str):
    """Fetch a tensor from the HF-format checkpoint for one native key.

    Reverses the transforms transformers' convert_sam3_to_hf.py applied:
    fuses split q/k/v projections back into fused qkv / in_proj tensors and
    re-transposes the CLIP text projection.
    """
    import torch

    if hf_key in ckpt:
        v = ckpt[hf_key]
        return v.T if hf_key == _TEXT_PROJ_HF_KEY else v
    # Native fused qkv (vision trunk): HF has attention.{q,k,v}_proj.*
    for fused, sep in ((".qkv.weight", ".weight"), (".qkv.bias", ".bias")):
        if hf_key.endswith(fused):
            base = hf_key[: -len(fused)]
            parts = [ckpt.get(f"{base}.{p}_proj{sep}") for p in ("q", "k", "v")]
            if all(p is not None for p in parts):
                return torch.cat(parts, dim=0)
    # Native nn.MultiheadAttention in_proj: HF has {q,k,v}_proj.*
    for fused, sep in (("in_proj_weight", ".weight"), ("in_proj_bias", ".bias")):
        if hf_key.endswith(fused):
            base = hf_key[: -len(fused)]
            parts = [ckpt.get(f"{base}{p}_proj{sep}") for p in ("q", "k", "v")]
            if all(p is not None for p in parts):
                return torch.cat(parts, dim=0)
    return None


def _rewrite_state_dict(raw: dict, model_sd: dict) -> dict:
    """Map the mirror checkpoint onto the native sam3 package key layout.

    Drops tracker weights, strips the wrapper prefix, and — when the inner
    names are HF-transformers format (as on the 1038lab/sam3 mirror) — converts
    them back to native names, fusing split projections. Returns a dict keyed
    by native model keys; anything unmapped is left to load_state_dict's
    missing-key report.
    """
    import torch

    model_keys = set(model_sd)
    filtered = {k: v for k, v in raw.items() if not k.startswith(_TRACKER_PREFIXES)}

    # Try plain prefix-strips first (covers a native-format checkpoint).
    best_prefix, best_sd, best_overlap = "", filtered, -1
    for prefix in ("detector_model.", "detector.", ""):
        sd = {
            (k[len(prefix):] if prefix and k.startswith(prefix) else k): v
            for k, v in filtered.items()
        }
        overlap = len(model_keys & sd.keys())
        if overlap > best_overlap:
            best_prefix, best_sd, best_overlap = prefix, sd, overlap
    if best_overlap >= 0.9 * len(model_keys):
        logger.info(
            "SAM3 checkpoint rewrite: native format, prefix strip %r matched %d/%d model keys",
            best_prefix or "(none)", best_overlap, len(model_keys),
        )
        return best_sd

    # HF-transformers format: resolve each native key through the official
    # naming map and undo the conversion-time tensor transforms.
    converted: dict = {}
    missing: list[str] = []
    shape_mismatch: list[str] = []
    for nk, expected in model_sd.items():
        hf_key = _native_to_hf_key(nk)
        v = _fetch_hf_tensor(best_sd, hf_key)
        if v is None:
            missing.append(nk)
            continue
        if hf_key == _POS_EMBED_HF_KEY and v.shape[1] == expected.shape[1] - 1:
            # The HF conversion strips the cls position embedding; the native
            # trunk discards that row anyway (get_abs_pos drops abs_pos[:, :1]),
            # so a zero row is exactly equivalent.
            v = torch.cat([torch.zeros_like(v[:, :1]), v], dim=1)
        if tuple(v.shape) != tuple(expected.shape):
            shape_mismatch.append(f"{nk} ckpt{tuple(v.shape)} != model{tuple(expected.shape)}")
            continue
        converted[nk] = v
    benign = [k for k in missing if _is_expected_missing(k)]
    real_missing = [k for k in missing if not _is_expected_missing(k)]
    logger.info(
        "SAM3 checkpoint rewrite: HF-format conversion mapped %d/%d model keys "
        "(%d expected-missing kept from build: rope buffers + point-prompt "
        "projections absent from HF checkpoints, unused for text prompts)",
        len(converted), len(model_keys), len(benign),
    )
    if real_missing or shape_mismatch:
        logger.warning(
            "SAM3 HF-format conversion left %d unresolved and %d shape-mismatched "
            "keys. Unresolved: %s | Shape mismatch: %s",
            len(real_missing), len(shape_mismatch),
            real_missing[:5], shape_mismatch[:5],
        )
    return converted


@contextlib.contextmanager
def _non_cuda_build_patch(dev_str: str):
    """Make build_sam3_image_model work off-CUDA.

    sam3 precomputes two torch.compile warm-up caches on a hardcoded
    device="cuda" (PositionEmbeddingSine's cache and TransformerDecoder's
    boxRPB coord cache), which crashes model *building* on CPU/MPS. Both
    caches fill lazily in forward() on the correct device, so skipping the
    PE precompute and building the coord cache on the target device is safe
    when we don't use compile.
    """
    if dev_str.startswith("cuda"):
        yield
        return
    from sam3.model import decoder as _dec
    from sam3.model import position_encoding as _pe

    orig_pe_init = _pe.PositionEmbeddingSine.__init__
    orig_get_coords = _dec.TransformerDecoder._get_coords

    def patched_pe_init(self, *args, **kwargs):
        kwargs["precompute_resolution"] = None
        orig_pe_init(self, *args, **kwargs)

    def patched_get_coords(H, W, device):
        return orig_get_coords(H, W, dev_str)

    _pe.PositionEmbeddingSine.__init__ = patched_pe_init
    _dec.TransformerDecoder._get_coords = staticmethod(patched_get_coords)
    try:
        yield
    finally:
        _pe.PositionEmbeddingSine.__init__ = orig_pe_init
        _dec.TransformerDecoder._get_coords = staticmethod(orig_get_coords)


_pin_memory_patched = False


def _disable_pin_memory_off_cuda() -> None:
    """No-op Tensor.pin_memory when CUDA is absent.

    sam3's geometry encoder calls .pin_memory() unconditionally at inference
    time, which raises on CPU-only hosts. Without CUDA, pinning always raises,
    so no other caller can legitimately depend on it — a no-op is safe.
    """
    global _pin_memory_patched
    import torch

    if _pin_memory_patched or torch.cuda.is_available():
        return
    torch.Tensor.pin_memory = lambda self, *args, **kwargs: self  # type: ignore[method-assign]
    _pin_memory_patched = True
    logger.info("SAM3: CUDA unavailable — Tensor.pin_memory patched to no-op")


def _load_sam3_sync(job_id=None, loop=None, dataset_id=None):
    """Build the SAM3 image model, load the safetensors checkpoint, wrap in Sam3Processor."""
    from safetensors.torch import load_file
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor
    from backend.ml.model_manager import ModelEntry
    from backend.ml.download_progress import emit_sync

    # Resolve the checkpoint before building the model: fail fast, no VRAM spent.
    ckpt_path = _resolve_checkpoint()

    dev = _device.get_device()
    dev_str = str(dev)
    vram_before = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0

    if job_id and loop:
        emit_sync(job_id, loop, "Loading SAM 3 (~3.4 GB checkpoint)...", -1.0, dataset_id)

    logger.info("Building SAM3 image model on device %s", dev_str)
    if not dev_str.startswith("cuda"):
        _disable_pin_memory_off_cuda()
    with _non_cuda_build_patch(dev_str):
        model = build_sam3_image_model(
            device=dev_str,
            checkpoint_path=None,
            load_from_HF=False,
            enable_segmentation=True,
            enable_inst_interactivity=False,
        )

    logger.info("Loading SAM3 checkpoint from %s", ckpt_path)
    raw = load_file(str(ckpt_path))
    state_dict = _rewrite_state_dict(raw, model.state_dict())
    missing, unexpected = model.load_state_dict(state_dict, strict=False)
    real_missing = [k for k in missing if not _is_expected_missing(k)]
    if real_missing or unexpected:
        logger.warning(
            "SAM3 load_state_dict: %d missing, %d unexpected keys. "
            "Sample missing: %s | Sample unexpected: %s",
            len(real_missing), len(unexpected), real_missing[:5], unexpected[:5],
        )
    else:
        logger.info(
            "SAM3 checkpoint loaded cleanly (%d expected-missing buffer/unused keys kept from build)",
            len(missing),
        )
    model = model.to(dev).eval()
    # Sam3Processor's device defaults to "cuda"; it must match the model device.
    processor = Sam3Processor(model, device=dev_str)

    if job_id and loop:
        emit_sync(job_id, loop, "SAM 3 loaded.", -1.0, dataset_id)

    vram_after = _device.memory_allocated_bytes() if _device.is_gpu_available() else 0
    delta_mb = (vram_after - vram_before) // (1024 * 1024)
    vram_used = max(3500, delta_mb) if _device.is_gpu_available() else 0
    return ModelEntry(
        {"processor": processor, "model": model, "device": dev},
        None,
        vram_mb=vram_used,
    )


def _to_numpy(t):
    if t is None:
        return None
    if isinstance(t, np.ndarray):
        return t
    try:
        t = t.detach().cpu()
        if t.is_floating_point():
            t = t.float()  # numpy has no bfloat16
        return t.numpy()
    except AttributeError:
        return np.asarray(t)


def predict_sync(
    image_path: str,
    model_entry: dict,
    text_prompt: str,
    score_threshold: float = 0.5,
) -> list[dict]:
    """Run SAM3 text-prompt segmentation on a single image.

    Returns list of {label, bbox [x1,y1,x2,y2] norm., score, mask (polygon JSON)}
    — shape-identical to sam2_predictor.predict_sync output.
    """
    import torch

    # Comma-separated multi-phrase: one job runs every phrase. A single phrase
    # is byte-identical to the old single-prompt path (one-element list).
    phrases = [p.strip() for p in text_prompt.split(",") if p.strip()]
    if not phrases:
        logger.warning("SAM3 called with empty prompt")
        return []

    processor = model_entry["processor"]
    # The processor filters instances internally with this threshold
    # (state["scores"] > confidence_threshold in _forward_grounding).
    processor.confidence_threshold = score_threshold

    img = open_rgb(image_path)
    img_w, img_h = img.size

    # sam3's fused ViT MLP kernel (perflib.fused.addmm_act) hard-casts to
    # bfloat16, so inference must run under bf16 autocast or the following
    # f32 linear raises a dtype mismatch. This matches official sam3 usage.
    dev_type = "cuda" if str(model_entry["device"]).startswith("cuda") else "cpu"
    results = []
    with torch.inference_mode(), torch.autocast(device_type=dev_type, dtype=torch.bfloat16):
        # Encode the image once, then re-prompt the reused state per phrase so
        # the expensive ViT pass is not repeated across phrases. set_image fully
        # consumes the pixels, so close the decoded buffer before the per-phrase
        # inference loop runs (finally: even if set_image raises).
        try:
            state = processor.set_image(img)
        finally:
            img.close()
        for phrase in phrases:
            phrase_state = processor.set_text_prompt(prompt=phrase, state=state)

            # state["masks"]: bool tensor [N, 1, H, W] already interpolated to
            # the original resolution; state["scores"]: [N] probabilities.
            masks = _to_numpy(phrase_state.get("masks"))
            scores = _to_numpy(phrase_state.get("scores"))
            if masks is None or len(masks) == 0:
                continue
            if scores is None:
                scores = np.ones(len(masks), dtype=np.float32)

            for mask, score in zip(masks, scores):
                if float(score) < score_threshold:
                    continue
                mask2d = np.squeeze(mask)
                if mask2d.ndim != 2:
                    logger.warning("SAM3 mask has unexpected shape %s; skipping", mask.shape)
                    continue
                bool_mask = mask2d > 0
                if bool_mask.shape != (img_h, img_w):
                    import cv2
                    bool_mask = cv2.resize(
                        bool_mask.astype(np.uint8), (img_w, img_h),
                        interpolation=cv2.INTER_NEAREST,
                    ).astype(bool)
                if not bool_mask.any():
                    continue
                polys = masks_to_polygons(np.array([bool_mask]), img_w, img_h)
                if not polys:
                    continue
                results.append({
                    "label": phrase,
                    "bbox": bbox_from_mask(bool_mask, img_w, img_h),
                    "score": round(float(score), 4),
                    "mask": json.dumps({"polygons": polys}),
                })
    return results
