"""Extract DINO CLS + per-layer embeddings for a dataset into a scratch ``.npz``.

The companion to ``style_gate.py`` for comparing a *different* DINO checkpoint against the
shipped one. ``POST /quality/style-similarity`` writes one shared ``style_similarity_score``
column and ``Image.dino_embedding`` holds exactly one model's output with no discriminator
saying which — so evaluating DINOv3 through the app would overwrite the DINOv2 embeddings
that the gate's published numbers were computed from, irreversibly and silently. This writes
to a file instead.

**The real ``dataset_manager.db`` is read and NEVER written**, enforced the same way
``style_gate.py`` enforces it: stdlib ``sqlite3`` over a ``file:…?mode=ro`` URI, no
SQLAlchemy session, no engine, no write path to get wrong.

Run from the repo root with the venv active::

    # validate the harness against the checkpoint that produced the stored blobs
    python -m backend.scripts.dino_embed_offline --dataset test3 \
        --model facebook/dinov2-base --out /tmp/dinov2_224.npz --verify

    # the run this exists for (gated repo — needs HF_TOKEN)
    python -m backend.scripts.dino_embed_offline --dataset test3 \
        --model facebook/dinov3-vitb16-pretrain-lvd1689m --out /tmp/dinov3_224.npz

``--verify`` re-scores the extraction against the ``dino_embedding`` column already in the
DB and prints the cosine agreement. Against ``facebook/dinov2-base`` that must come out at
~1.0; anything less means this harness and ``dino_scorer`` disagree about preprocessing, and
every number downstream of it is measuring the harness rather than the checkpoint.

The output layout is byte-compatible with what the app stores — float16, L2-normalised,
(N, D) CLS and (N, L, D) per-layer — so ``compute_style_similarity`` and
``slice_layer_embedding`` consume it unchanged when D is 768 and L is 12.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = REPO_ROOT / "dataset_manager.db"


# --------------------------------------------------------------------------- read-only DB
def load_rows(db_path: Path, dataset: str) -> tuple[str, list[dict]]:
    """Return (dataset_name, rows). Opened read-only; a write attempt raises."""
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        ds = conn.execute(
            "SELECT id, name FROM datasets WHERE id = ? OR name = ?", (dataset, dataset)
        ).fetchone()
        if ds is None:
            names = [r["name"] for r in conn.execute("SELECT name FROM datasets ORDER BY name")]
            raise SystemExit(
                f"No dataset matching {dataset!r}. Available: {', '.join(names) or '(none)'}"
            )
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, filename, file_path, dino_embedding, clip_embedding
                FROM images WHERE dataset_id = ? ORDER BY filename
                """,
                (ds["id"],),
            )
        ]
        return ds["name"], rows
    finally:
        conn.close()


# --------------------------------------------------------------------------- preprocessing
def scale_processor(processor, target: int) -> int:
    """Rescale the processor's resize/crop geometry to ``target``, preserving its shape.

    DINOv3 is patch-16 with RoPE, so unlike DINOv2's interpolated position embeddings it
    takes any multiple of 16 as a first-class input rather than as a stretch of what it was
    trained on. Testing that means changing the *output* size while keeping the transform's
    shape — the same resize-then-centre-crop with the same ratio between the two — otherwise
    a high-res run also silently changes the aspect handling and the comparison measures two
    things at once. Every numeric field in ``size``/``crop_size`` is scaled by one factor
    derived from the processor's own baseline, so this works whether the config is expressed
    as ``shortest_edge`` or as ``height``/``width``.

    Returns the baseline the factor was derived from (for reporting). A ``target`` equal to
    the baseline is a no-op.
    """
    def _dims(d) -> list[int]:
        return [v for v in (d or {}).values() if isinstance(v, int)]

    crop = getattr(processor, "crop_size", None)
    size = getattr(processor, "size", None)
    baseline_dims = _dims(crop) or _dims(size)
    if not baseline_dims:
        raise SystemExit(
            "Cannot determine this processor's baseline resolution — refusing to guess. "
            f"size={size!r} crop_size={crop!r}"
        )
    baseline = min(baseline_dims)
    if target == baseline:
        return baseline
    factor = target / baseline

    for attr in ("size", "crop_size"):
        d = getattr(processor, attr, None)
        if not isinstance(d, dict):
            continue
        setattr(processor, attr, {
            k: (int(round(v * factor)) if isinstance(v, int) else v) for k, v in d.items()
        })
    return baseline


# --------------------------------------------------------------------------- extraction
def extract(model, processor, paths: list[str], device, batch: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (cls (N, D) float16, layers (N, L, D) float16), both L2-normalised per row.

    Mirrors ``backend/ml/dino_scorer.py`` exactly — ``open_rgb`` for the EXIF-transposed
    frame every predictor shares, ``last_hidden_state[:, 0, :]`` for CLS, and
    ``hidden_states[1:]`` for the per-block sweep. DINOv3 carries 4 register tokens between
    CLS and the patches, which does not move CLS: it is index 0 in both families.
    """
    import torch
    from backend.ml.image_utils import open_rgb

    cls_out: list[np.ndarray] = []
    layer_out: list[np.ndarray] = []
    started = time.monotonic()

    for start in range(0, len(paths), batch):
        chunk = paths[start : start + batch]
        imgs = [open_rgb(p) for p in chunk]
        inputs = processor(images=imgs, return_tensors="pt")
        for img in imgs:
            img.close()  # free the decoded buffers before inference, per CLAUDE.md
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            out = model(**inputs, output_hidden_states=True)
            cls = out.last_hidden_state[:, 0, :]
            cls = cls / cls.norm(dim=-1, keepdim=True)
            cls_out.append(cls.float().cpu().numpy().astype(np.float16))

            # hidden_states[0] is the patch embedding, 1..L are the transformer blocks.
            blocks = torch.stack([h[:, 0, :] for h in out.hidden_states[1:]], dim=1)
            blocks = blocks / blocks.norm(dim=-1, keepdim=True)
            layer_out.append(blocks.float().cpu().numpy().astype(np.float16))

        done = min(start + batch, len(paths))
        rate = done / max(time.monotonic() - started, 1e-6)
        print(f"\r  {done}/{len(paths)} images ({rate:.1f}/s)", end="", flush=True)

    print()
    return np.concatenate(cls_out), np.concatenate(layer_out)


def extract_clip(paths: list[str], device, batch: int) -> np.ndarray:
    """CLIP ViT-L-14 image features, mirroring ``aesthetic_scorer.extract_clip_embedding_sync``.

    The shipped default for style matching is CLIP, so leaving it out of a comparison run
    would rank the rivals against each other and not against the thing in use. There are no
    per-layer blobs here: the app stores only the final image feature, and CLIP's
    intermediate blocks are not what ``dino_layer_embeddings`` means.
    """
    import open_clip
    import torch
    from backend.ml.image_utils import open_rgb

    model, _, preprocess = open_clip.create_model_and_transforms("ViT-L-14", pretrained="openai")
    model = model.to(device).eval()

    out: list[np.ndarray] = []
    started = time.monotonic()
    for start in range(0, len(paths), batch):
        chunk = paths[start : start + batch]
        tensors = []
        for p in chunk:
            img = open_rgb(p)
            tensors.append(preprocess(img))
            img.close()
        stacked = torch.stack(tensors).to(device)
        with torch.no_grad():
            feats = model.encode_image(stacked)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            out.append(feats.float().cpu().numpy().astype(np.float16))
        done = min(start + batch, len(paths))
        print(f"\r  {done}/{len(paths)} images ({done / max(time.monotonic() - started, 1e-6):.1f}/s)",
              end="", flush=True)
    print()
    return np.concatenate(out)


def verify_against_db(ids: list[str], cls: np.ndarray, rows: list[dict], column: str) -> None:
    """Cosine of each fresh CLS against the ``dino_embedding`` already stored for that image.

    A near-1.0 mean is the only thing that licenses trusting a *different* checkpoint's
    numbers from this harness: it says the preprocessing and token selection here match the
    production path, so a later disagreement is the model and not the plumbing.
    """
    stored = {r["id"]: r[column] for r in rows if r[column]}
    sims = []
    for i, image_id in enumerate(ids):
        blob = stored.get(image_id)
        if not blob:
            continue
        ref = np.frombuffer(blob, dtype=np.float16).astype(np.float32)
        if ref.shape != cls[i].shape:
            print(f"  ! {image_id}: stored dim {ref.shape[0]} vs fresh {cls[i].shape[0]} — "
                  "different checkpoint family, verification is meaningless")
            return
        got = cls[i].astype(np.float32)
        sims.append(float(ref @ got / (np.linalg.norm(ref) * np.linalg.norm(got) + 1e-8)))

    if not sims:
        print(f"  ! no stored {column} to verify against")
        return
    print(f"  verify: {len(sims)} compared · mean cos {np.mean(sims):.6f} · "
          f"min {min(sims):.6f} · max {max(sims):.6f}")
    if np.mean(sims) < 0.999:
        # Only a *same-checkpoint* run asserts anything here. Two different checkpoints have
        # unrelated embedding spaces, so ~0 is the expected reading and not a fault — saying
        # otherwise turns the one check that validates the harness into noise everyone learns
        # to scroll past.
        print("  → near 0 means the stored column came from a different checkpoint, which is "
              "expected unless --model is the one that produced it. Re-run against that model: "
              "anything below 0.999 THERE means this harness disagrees with dino_scorer's "
              "preprocessing, and every number downstream measures the harness, not the model.")


# --------------------------------------------------------------------------- driver
def run(args) -> None:
    import torch
    from backend.ml import device as _device
    from transformers import AutoImageProcessor, AutoModel

    db_path = Path(args.db).resolve()
    dataset_name, rows = load_rows(db_path, args.dataset)
    if not rows:
        raise SystemExit(f"Dataset {dataset_name!r} has no images.")

    missing = [r for r in rows if not r["file_path"] or not Path(r["file_path"]).is_file()]
    if missing:
        print(f"! {len(missing)} of {len(rows)} images have no readable file — skipped")
        rows = [r for r in rows if r not in missing]
    if args.limit:
        rows = rows[: args.limit]

    device = _device.get_device()
    paths = [r["file_path"] for r in rows]
    ids = [r["id"] for r in rows]

    if args.model == "clip":
        print(f"# open_clip ViT-L-14/openai — {dataset_name} ({len(rows)} images)")
        print(f"DB (read-only): {db_path}")
        cls = extract_clip(paths, device, args.batch)
        layers = None
        if args.verify:
            verify_against_db(ids, cls, rows, "clip_embedding")
        save(args, ids, rows, cls, layers)
        return

    print(f"# {args.model} @ {args.size}px — {dataset_name} ({len(rows)} images)")
    print(f"DB (read-only): {db_path}")

    processor = AutoImageProcessor.from_pretrained(args.model)
    baseline = scale_processor(processor, args.size)
    print(f"  processor baseline {baseline}px → {args.size}px "
          f"(size={getattr(processor, 'size', None)}, crop={getattr(processor, 'crop_size', None)})")

    model = AutoModel.from_pretrained(args.model, dtype=torch.float32)
    device = _device.get_device()
    model = model.to(device).eval()
    cfg = model.config
    print(f"  {type(model).__name__} on {device} · hidden {cfg.hidden_size} · "
          f"layers {cfg.num_hidden_layers} · patch {getattr(cfg, 'patch_size', '?')}")

    cls, layers = extract(model, processor, paths, device, args.batch)

    if args.verify:
        verify_against_db(ids, cls, rows, "dino_embedding")

    save(args, ids, rows, cls, layers)


def save(args, ids: list[str], rows: list[dict], cls: np.ndarray, layers: np.ndarray | None) -> None:
    """Write the npz. ``layers`` is None for CLIP, which has no per-layer blob to store."""
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ids": np.array(ids),
        "filenames": np.array([r["filename"] for r in rows]),
        "cls": cls,
        "model": np.array(args.model),
        "size": np.array(args.size),
    }
    if layers is not None:
        payload["layers"] = layers
    np.savez(out, **payload)
    print(f"  cls {cls.shape} {cls.dtype}"
          + (f" · layers {layers.shape}" if layers is not None else " · no per-layer blob"))
    print(f"\nNPZ: {out}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract DINO CLS + per-layer embeddings for a dataset into an .npz. "
                    "The DB is opened read-only and never written."
    )
    ap.add_argument("--dataset", required=True, help="dataset name or id")
    ap.add_argument("--model", default="facebook/dinov3-vitb16-pretrain-lvd1689m",
                    help="HF model id (default the DINOv3 ViT-B/16; gated, needs HF_TOKEN), or "
                         "the literal 'clip' for the app's open_clip ViT-L-14/openai")
    ap.add_argument("--size", type=int, default=224,
                    help="target square resolution; the processor's resize/crop geometry is "
                         "scaled to it, preserving the transform's shape (default 224)")
    ap.add_argument("--batch", type=int, default=8, help="images per forward pass")
    ap.add_argument("--limit", type=int, default=0, help="stop after N images (smoke test)")
    ap.add_argument("--verify", action="store_true",
                    help="cosine-check the extraction against the stored dino_embedding column")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="opened read-only; never written")
    default_out = os.path.join(
        os.environ.get("CLAUDE_SCRATCH", tempfile.gettempdir()), "dino_embed.npz"
    )
    ap.add_argument("--out", default=default_out, help=f"output .npz (default {default_out})")
    run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
