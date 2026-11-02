"""Exploratory: score test4 on positives Kaloscope cannot have trained on.

References are drawn from an artist's posts uploaded on or before Kaloscope's checkpoint
date (2026-10-02); positives are only that artist's posts uploaded after it; controls are
every other artist. 20 seeded draws of 4 references, the app's maths.

    python -m backend.scripts.style_research.post_cutoff_probe --runs <R> \
        --groups g4.json --manifest <build dir>/manifest.json
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from backend.ml.similarity_scorer import blend_scores, compute_style_similarity
from backend.scripts.style_crossed_probe import auc

CUTOFF = "2026-10-02"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--groups", required=True)
    ap.add_argument("--manifest", required=True, help="fetch_booru's manifest.json (upload dates)")
    a = ap.parse_args()
    # The import slugifies filenames, dropping parentheses; match the DB's spelling.
    src = {m["file"].replace("(", "").replace(")", ""): m
           for m in json.load(open(a.manifest, encoding="utf-8"))}
    groups = json.load(open(a.groups, encoding="utf-8"))

    def vecs(tag: str, layer: int | None = None) -> dict[str, bytes]:
        d = np.load(f"{a.runs}/test4_{tag}.npz")
        arr = d["cls"] if layer is None else d["layers"][:, layer]
        return {str(f): arr[i].tobytes() for i, f in enumerate(d["filenames"])}

    V = {"kalo": vecs("kalo"), "csd": vecs("csd"), "clip": vecs("clip"), "v2L7": vecs("v2", 6)}
    rng = np.random.default_rng(0)
    allf = [f for fs in groups.values() for f in fs]
    print(f"positives uploaded after {CUTOFF}; references from on or before it; 20 draws of 4\n")
    print(f"{'style':<22} {'n post':>6}   kalo    csd   baseline (0.30 CLIP + v2 L7)")
    for s, fs in groups.items():
        post = [f for f in fs if src[f]["created"] > CUTOFF]
        pre = [f for f in fs if f not in post]
        if len(post) < 3 or len(pre) < 4:
            print(f"{s:<22} {len(post):>6}   (too few)")
            continue
        res: dict[str, list[float]] = {"kalo": [], "csd": [], "base": []}
        for _ in range(20):
            refs = list(rng.choice(pre, 4, replace=False))
            cands = post + [f for f in allf if f not in fs]
            pos = np.array([f in post for f in cands])
            sc = {t: compute_style_similarity([V[t][f] for f in refs], [V[t][f] for f in cands]) for t in V}
            for t in ("kalo", "csd"):
                x = np.array(sc[t])
                res[t].append(auc(x[pos], x[~pos]))
            b = np.array(blend_scores(sc["clip"], sc["v2L7"], 0.30, 0.70))
            res["base"].append(auc(b[pos], b[~pos]))
        print(f"{s:<22} {len(post):>6}  " + "  ".join(f"{np.mean(v):.3f}" for v in res.values()))


if __name__ == "__main__":
    main()
