"""Extract a candidate style model's embeddings for a dataset into a scratch ``.npz``.

The companion to ``backend/scripts/dino_embed_offline.py`` for models that are not DINO or
the app's CLIP. It reuses that script's read-only DB loader and its ``.npz`` writer, so the
sweep (``dinov3_sweep.py``) reads the output unchanged: L2-normalised float16 vectors in
``cls``, one row per image. **The DB is opened read-only and never written.**

    python -m backend.scripts.style_research.style_embed_offline --dataset test4 --model kalo --out k.npz
    python -m backend.scripts.style_research.style_embed_offline --dataset test4 --model csd --out c.npz
    python -m backend.scripts.style_research.style_embed_offline --dataset test4 --model pe --out p.npz
    # the Kaloscope pipeline check: top-5 artists for loose files by artists it was trained on
    python -m backend.scripts.style_research.style_embed_offline --kalo-check a.jpg b.jpg ...

Every checkpoint is pinned to a revision. No pickle is loaded with ``weights_only=False``:
CSD's checkpoint is a pickle whose only non-tensor globals are numpy scalar/dtype,
``_codecs.encode`` and ``argparse.Namespace`` (listed with ``pickletools`` before loading), so
exactly those are allowlisted.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from backend.scripts.dino_embed_offline import DEFAULT_DB, load_rows, save

KALO_REPO = "heathcliff01/Kaloscope3.0-preview"
KALO_REV = "5e4bfa229619b7c459ec126283827373df9de564"
KALO_FILE = "v1-artist-classifier/model.safetensors"
KALO_ARCH = "facebook/dinov3-vitb16-pretrain-lvd1689m"   # config.json only: the B/16 shape
CSD_REPO = "tomg-group-umd/CSD-ViT-L"
CSD_REV = "5bc26a6fb0487f3f00a2a7313135103a005b1b67"
PE_REPO = "timm/PE-Core-L-14-336"
PE_REV = "8eff41b3f687e50a323662c2dda5eb3588c6dd35"


# --------------------------------------------------------------------------- Kaloscope
def _kalo_state(path: str) -> tuple[dict, dict, dict, "object"]:
    """Map the export's DINOv3 hub-format keys onto transformers' ``DINOv3ViTModel``.

    Returns (backbone state, projector state, head state, rope periods). The export splits
    ``attn.qkv`` into one fused matrix; transformers keeps q/k/v apart and has no key bias,
    so the k slice of the fused bias must be zero for the mapping to be exact — checked.
    """
    import torch
    from safetensors.torch import load_file

    simple = {"attn.proj.weight": "attention.o_proj.weight", "attn.proj.bias": "attention.o_proj.bias",
              "ls1.gamma": "layer_scale1.lambda1", "ls2.gamma": "layer_scale2.lambda1",
              "mlp.fc1.weight": "mlp.up_proj.weight", "mlp.fc1.bias": "mlp.up_proj.bias",
              "mlp.fc2.weight": "mlp.down_proj.weight", "mlp.fc2.bias": "mlp.down_proj.bias",
              "norm1.weight": "norm1.weight", "norm1.bias": "norm1.bias",
              "norm2.weight": "norm2.weight", "norm2.bias": "norm2.bias"}
    sd = load_file(path)
    bb, proj, head, periods = {}, {}, {}, None
    for key, v in sd.items():
        if key.startswith("projector."):
            proj[key.removeprefix("projector.")] = v
        elif key.startswith("head."):
            head[key.removeprefix("head.")] = v
        elif not key.startswith("backbone."):
            continue                                   # bias, log_temperature: the training loss
        else:
            k = key.removeprefix("backbone.")
            if k == "cls_token":
                bb["embeddings.cls_token"] = v
            elif k == "mask_token":
                bb["embeddings.mask_token"] = v.reshape(1, 1, -1)
            elif k == "storage_tokens":
                bb["embeddings.register_tokens"] = v
            elif k.startswith("patch_embed.proj."):
                bb["embeddings.patch_embeddings." + k.rsplit(".", 1)[1]] = v
            elif k.startswith("norm."):
                bb[k] = v
            elif k == "rope_embed.periods":
                periods = v
            elif k.startswith("blocks."):
                _, i, rest = k.split(".", 2)
                p = f"layer.{i}.attention."
                if rest == "attn.qkv.weight":
                    q, kk, vv = v.chunk(3)
                    bb[p + "q_proj.weight"], bb[p + "k_proj.weight"], bb[p + "v_proj.weight"] = q, kk, vv
                elif rest == "attn.qkv.bias":
                    q, kk, vv = v.chunk(3)
                    if torch.count_nonzero(kk):
                        raise SystemExit(f"block {i}: non-zero key bias — the mapping would drop it")
                    bb[p + "q_proj.bias"], bb[p + "v_proj.bias"] = q, vv
                elif rest == "attn.qkv.bias_mask":
                    continue                           # the hub code's k-bias mask; see above
                elif rest in simple:
                    bb[f"layer.{i}." + simple[rest]] = v
                else:
                    raise SystemExit(f"unmapped Kaloscope key: {key}")
            else:
                raise SystemExit(f"unmapped Kaloscope key: {key}")
    return bb, proj, head, periods


def load_kalo(device):
    import torch
    from huggingface_hub import hf_hub_download
    from transformers import DINOv3ViTConfig, DINOv3ViTModel

    path = hf_hub_download(KALO_REPO, KALO_FILE, revision=KALO_REV)
    cfg = DINOv3ViTConfig.from_pretrained(KALO_ARCH)
    backbone = DINOv3ViTModel(cfg)
    bb, proj_sd, head_sd, periods = _kalo_state(path)
    backbone.load_state_dict(bb, strict=True)
    if periods is not None:
        # RoPE is recomputed, not loaded. The export stores the periods rounded to bf16, so
        # transformers' float32 periods must round to exactly those. Kaloscope trained through
        # transformers, so the float32 values are the ones it saw.
        own = (1.0 / backbone.rope_embeddings.inv_freq.float()).to(torch.bfloat16).float()
        if not torch.equal(own, periods.float()):
            raise SystemExit("RoPE periods disagree — the backbone config is not Kaloscope's")
        print("  RoPE: transformers periods round to the export's bf16 periods exactly")
    width = cfg.hidden_size * 2                        # CLS + mean of patches
    projector = torch.nn.Sequential(torch.nn.Linear(width, width), torch.nn.GELU(),
                                    torch.nn.Linear(width, proj_sd["2.weight"].shape[0]))
    projector.load_state_dict(proj_sd, strict=True)
    head = torch.nn.Linear(width, head_sd["weight"].shape[0])
    head.load_state_dict(head_sd, strict=True)
    mods = SimpleNamespace(backbone=backbone, projector=projector, head=head,
                           regs=cfg.num_register_tokens)
    for m in (backbone, projector, head):
        m.to(device).eval()
    return mods


def kalo_pixels(img):
    """Kaloscope's eval transform: short side 512, centre 512, uint8 → ImageNet mean/std."""
    import torch
    from torchvision import transforms

    t = transforms.Compose([transforms.Resize(512), transforms.CenterCrop(512)])
    x = torch.from_numpy(np.array(t(img), copy=True)).permute(2, 0, 1).float() / 255
    mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
    return (x - mean) / std


def kalo_forward(m, x):
    """(pooled 1536-d features, 256-d projection) — the export's own eval output."""
    import torch

    tokens = m.backbone(pixel_values=x).last_hidden_state
    feats = torch.cat((tokens[:, 0], tokens[:, 1 + m.regs:].mean(1)), dim=1)
    return feats, m.projector(feats)


# --------------------------------------------------------------------------- CSD and PE
def load_csd(device):
    """CSD: OpenAI CLIP ViT-L/14's vision tower, its projection replaced by the style head."""
    import argparse as _argparse
    import codecs
    import collections

    import open_clip
    import torch
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(CSD_REPO, "pytorch_model.bin", revision=CSD_REV)
    allowed = [collections.OrderedDict, codecs.encode, np.dtype, _argparse.Namespace,
               (np._core.multiarray.scalar, "numpy.core.multiarray.scalar")]
    allowed += [type(np.dtype(t)) for t in ("float64", "float32", "int64", "int32", "bool")]
    with torch.serialization.safe_globals(allowed):
        sd = torch.load(path, map_location="cpu", weights_only=True)
    if "model_state_dict" in sd:
        sd = sd["model_state_dict"]
    sd = {k.removeprefix("module."): v for k, v in sd.items()}
    visual = {k.removeprefix("backbone."): v for k, v in sd.items() if k.startswith("backbone.")}
    visual["proj"] = sd["last_layer_style"]
    # OpenAI CLIP uses QuickGELU; open_clip's plain "ViT-L-14" would silently use GELU.
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-L-14-quickgelu", pretrained=None)
    model.visual.load_state_dict(visual, strict=True)
    return model.visual.to(device).eval(), preprocess


def load_clipq(device):
    """The app's CLIP weights built with the QuickGELU they were trained with.

    ``model_manager`` builds ``ViT-L-14`` with ``pretrained="openai"``, which open_clip builds
    with plain GELU and warns about (the warning is silenced in ``main.py``). This is the
    same checkpoint and preprocessing with the activation corrected, to measure the gap.
    """
    import open_clip

    model, _, preprocess = open_clip.create_model_and_transforms("ViT-L-14-quickgelu", pretrained="openai")
    return model.to(device).eval(), preprocess


def load_pe(device):
    import open_clip
    from huggingface_hub import snapshot_download

    local = snapshot_download(PE_REPO, revision=PE_REV,
                              allow_patterns=["open_clip_config.json", "open_clip_model.safetensors"])
    model, _, preprocess = open_clip.create_model_and_transforms(f"local-dir:{local}")
    return model.to(device).eval(), preprocess


# --------------------------------------------------------------------------- driver
def embed(model_name: str, paths: list[str], device, batch: int) -> tuple[np.ndarray, np.ndarray | None]:
    """Return (vectors (N, D), Kaloscope's pooled features or None), each row L2-normalised."""
    import torch
    from backend.ml.image_utils import open_rgb

    if model_name == "kalo":
        m = load_kalo(device)
    elif model_name == "csd":
        enc, preprocess = load_csd(device)
    elif model_name == "clipq":
        enc, preprocess = load_clipq(device)
    else:
        enc, preprocess = load_pe(device)

    out, extra, started = [], [], time.monotonic()
    for start in range(0, len(paths), batch):
        imgs = [open_rgb(p) for p in paths[start:start + batch]]
        if model_name == "kalo":
            x = torch.stack([kalo_pixels(i) for i in imgs])
        else:
            x = torch.stack([preprocess(i) for i in imgs])
        for i in imgs:
            i.close()                                  # free decoded buffers before inference
        with torch.no_grad():
            x = x.to(device)
            if model_name == "kalo":
                feats, v = kalo_forward(m, x)
                extra.append(torch.nn.functional.normalize(feats.float(), dim=-1).cpu().numpy())
            elif model_name == "csd":
                v = enc(x)
            elif model_name == "clipq":
                v = enc.encode_image(x)
            else:
                v = enc.encode_image(x)
            out.append(torch.nn.functional.normalize(v.float(), dim=-1).cpu().numpy())
        done = min(start + batch, len(paths))
        print(f"\r  {done}/{len(paths)} images ({done / max(time.monotonic() - started, 1e-6):.1f}/s)",
              end="", flush=True)
    print()
    vecs = np.concatenate(out).astype(np.float16)
    return vecs, (np.concatenate(extra).astype(np.float16) if extra else None)


def self_check(model_name: str, path: str, device) -> None:
    """The same image twice must give cosine 1.000; every vector must have norm 1."""
    a, _ = embed(model_name, [path, path], device, 2)
    a = a.astype(np.float32)
    print(f"  self-check: cos(same, same) {float(a[0] @ a[1]):.6f} · norms {np.linalg.norm(a, axis=1).round(4)}")


def kalo_check(files: list[str], device) -> None:
    import csv

    import torch
    from huggingface_hub import hf_hub_download
    from backend.ml.image_utils import open_rgb

    mapping = hf_hub_download(KALO_REPO, "v1-artist-classifier/class_mapping.csv", revision=KALO_REV)
    names = [r["class_name"] for r in csv.DictReader(open(mapping, encoding="utf-8"))]
    m = load_kalo(device)
    for f in files:
        img = open_rgb(f)
        x = kalo_pixels(img)[None].to(device)
        img.close()
        with torch.no_grad():
            feats, _ = kalo_forward(m, x)
            # The card's head input: L2-normalise the pooled features, then scale by √1536.
            logits = m.head(torch.nn.functional.normalize(feats.float(), dim=-1) * feats.shape[1] ** 0.5)
            p = logits.softmax(-1)[0]
        top = torch.topk(p, 5)
        print(f"{Path(f).name:<40} " + " · ".join(f"{names[i]} {s:.2f}" for s, i in
                                                     zip(top.values.tolist(), top.indices.tolist())))


def main() -> None:
    ap = argparse.ArgumentParser(description="Candidate style-model embeddings into an .npz. "
                                             "The DB is opened read-only and never written.")
    ap.add_argument("--dataset")
    ap.add_argument("--model", choices=("kalo", "csd", "pe", "clipq"))
    ap.add_argument("--out")
    ap.add_argument("--feat-out", help="kalo only: also write the pooled 1536-d features here")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="stop after N images (smoke test)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="opened read-only; never written")
    ap.add_argument("--kalo-check", nargs="+", metavar="IMAGE")
    args = ap.parse_args()

    from backend.ml import device as _device
    device = _device.get_device()
    if args.kalo_check:
        kalo_check(args.kalo_check, device)
        return
    if not (args.dataset and args.model and args.out):
        ap.error("--dataset, --model and --out are required")

    dataset_name, rows = load_rows(Path(args.db).resolve(), args.dataset)
    rows = [r for r in rows if r["file_path"] and Path(r["file_path"]).is_file()]
    if args.limit:
        rows = rows[: args.limit]
    print(f"# {args.model} — {dataset_name} ({len(rows)} images) on {device}")
    paths, ids = [r["file_path"] for r in rows], [r["id"] for r in rows]
    self_check(args.model, paths[0], device)
    vecs, feats = embed(args.model, paths, device, args.batch)
    size = {"kalo": 512, "csd": 224, "pe": 336, "clipq": 224}[args.model]
    save(SimpleNamespace(out=args.out, model=args.model, size=size), ids, rows, vecs, None)
    if feats is not None and args.feat_out:
        save(SimpleNamespace(out=args.feat_out, model="kalo-feat", size=size), ids, rows, feats, None)


if __name__ == "__main__":
    sys.exit(main())
