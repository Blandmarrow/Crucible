"""Measure what the CLIP QuickGELU fix changes: old (plain-GELU) build vs the fixed one.

Run from the repo root with the venv active:

    python -m backend.scripts.clip_build_delta [--sample 300] [--seed 0] [--db PATH]

OpenAI trained CLIP ViT-L/14 with QuickGELU; the app built it with plain GELU until the
2026-11 fix (`aesthetic_scorer.build_clip_vit_l14`). This script scores the same images
under both builds, through the app's own `prepare_clip` / `infer_*` functions, and reports:

- CLIP vectors: cosine between the two builds' `clip_embedding` for each image;
- LAION aesthetic score: mean and max |delta|, and Spearman rank correlation;
- watermark score: mean and max |delta|, and how many `has_watermark` flags flip at
  the threshold (and at two neighbours, to show how close to the line the scores sit).

It only reads the database (opened read-only) and the image files; it writes nothing.
The builds are run one after the other, so only one CLIP is resident at a time.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import time
from collections import defaultdict

import numpy as np


def _rank(a: np.ndarray) -> np.ndarray:
    r = np.empty(len(a))
    r[np.argsort(a, kind="stable")] = np.arange(len(a))
    return r


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return float("nan")
    return float(np.corrcoef(_rank(a), _rank(b))[0, 1])


def _sample(db_path: str, n: int, seed: int) -> list[tuple[str, str]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select d.name, i.file_path from images i join datasets d on d.id = i.dataset_id"
        ).fetchall()
    finally:
        con.close()
    rows = [(name, path) for name, path in rows if path]
    random.Random(seed).shuffle(rows)
    return rows[:n]


def _score_all(items: list[tuple[str, str]], quick_gelu: bool, weights_path) -> dict[str, list]:
    import torch

    from backend.ml import device as _device
    from backend.ml.aesthetic_scorer import (
        AestheticMLP,
        _precompute_watermark_text_features,
        build_clip_vit_l14,
        infer_aesthetic,
        infer_clip_embedding,
        infer_watermark,
        prepare_clip,
    )

    dev = _device.get_device()
    clip, preprocess = build_clip_vit_l14(quick_gelu=quick_gelu)
    clip = clip.to(dev).eval()
    mlp = AestheticMLP(768)
    mlp.load_state_dict(torch.load(weights_path, map_location="cpu"))
    mlp = mlp.to(dev).eval()
    entry = {"clip": clip, "mlp": mlp, "preprocess": preprocess}
    text = _precompute_watermark_text_features(entry)

    out: dict[str, list] = {"emb": [], "aes": [], "wm": [], "ok": []}
    started = time.monotonic()
    for i, (_, path) in enumerate(items):
        try:
            t = prepare_clip(path, preprocess)
        except Exception as exc:  # unreadable file: skip it in both builds
            print(f"\n  ! skip {path}: {exc}", file=sys.stderr)
            out["ok"].append(False)
            out["emb"].append(None)
            out["aes"].append(None)
            out["wm"].append(None)
            continue
        out["ok"].append(True)
        out["emb"].append(np.frombuffer(infer_clip_embedding(t, entry), dtype=np.float16).astype(np.float32))
        out["aes"].append(infer_aesthetic(t, entry))
        out["wm"].append(infer_watermark(t, entry, text)["watermark_score"])
        if (i + 1) % 25 == 0 or i + 1 == len(items):
            rate = (i + 1) / max(time.monotonic() - started, 1e-6)
            print(f"\r  {i + 1}/{len(items)} ({rate:.1f}/s)", end="", flush=True)
    print()

    del clip, mlp, entry, text
    _device.empty_cache()
    return out


def _report(label: str, old: dict, new: dict, idx: list[int], thresholds: list[float]) -> None:
    if not idx:
        return
    cos = np.array([float(old["emb"][i] @ new["emb"][i]
                          / (np.linalg.norm(old["emb"][i]) * np.linalg.norm(new["emb"][i]) + 1e-8))
                    for i in idx])
    a_old = np.array([old["aes"][i] for i in idx])
    a_new = np.array([new["aes"][i] for i in idx])
    w_old = np.array([old["wm"][i] for i in idx])
    w_new = np.array([new["wm"][i] for i in idx])
    da = np.abs(a_new - a_old)
    dw = np.abs(w_new - w_old)

    print(f"\n## {label} ({len(idx)} images)")
    print(f"  CLIP vector cosine old vs new : mean {cos.mean():.4f}  min {cos.min():.4f}")
    print(f"  LAION score  mean old {a_old.mean():.3f} new {a_new.mean():.3f}  "
          f"(new-old mean {np.mean(a_new - a_old):+.3f})")
    print(f"  LAION |delta|                : mean {da.mean():.3f}  max {da.max():.3f}  "
          f"Spearman {_spearman(a_old, a_new):.4f}")
    print(f"  Watermark |delta|            : mean {dw.mean():.4f}  max {dw.max():.4f}  "
          f"Spearman {_spearman(w_old, w_new):.4f}")
    for t in thresholds:
        f_old = w_old >= t
        f_new = w_new >= t
        on = int((~f_old & f_new).sum())
        off = int((f_old & ~f_new).sum())
        print(f"  has_watermark @ {t:.2f}         : flagged old {int(f_old.sum())} new {int(f_new.sum())}  "
              f"flips {on + off} (+{on} / -{off})")


def main() -> int:
    from backend.config import settings

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--db", default=settings.database_url.split(":///", 1)[1])
    ap.add_argument("--threshold", type=float, default=settings.watermark_threshold)
    args = ap.parse_args()

    weights_path = settings.models_cache_dir / "laion_aesthetic_sac_logos_ava1_l14.pth"
    if not weights_path.exists():
        print(f"LAION head not found at {weights_path}; run one aesthetic scoring job first.")
        return 1

    items = _sample(args.db, args.sample, args.seed)
    print(f"# CLIP build delta — {len(items)} images from {args.db} (seed {args.seed})")

    print("old build (plain GELU):")
    old = _score_all(items, quick_gelu=False, weights_path=weights_path)
    print("new build (QuickGELU):")
    new = _score_all(items, quick_gelu=True, weights_path=weights_path)

    ok = [i for i in range(len(items)) if old["ok"][i] and new["ok"][i]]
    thresholds = sorted({round(args.threshold - 0.1, 2), args.threshold, round(args.threshold + 0.1, 2)})
    _report("all", old, new, ok, thresholds)
    by_ds: dict[str, list[int]] = defaultdict(list)
    for i in ok:
        by_ds[items[i][0]].append(i)
    for name, idx in sorted(by_ds.items()):
        _report(f"dataset {name}", old, new, idx, [args.threshold])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
