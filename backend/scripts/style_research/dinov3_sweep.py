"""Separability sweep for the DINOv3 test (plan step 4).

Reads npz caches written by `backend.scripts.dino_embed_offline` plus a groups file
({style: [filename, ...]}), and scores every signal with the app's own maths:
`similarity_scorer.compute_style_similarity` (centroid cosine, 4-dp rounding) and
`blend_scores`, AUC from `style_crossed_probe.auc` (ties as half).

One configuration = one style: R references drawn from the style (D seeded draws), the
rest of that style are positives, every image in the other styles is a control. Images in
no group are ignored.

Run from the worktree root (so `backend` resolves to the branch):
    python <this> --groups g.json --clip clip.npz --dino v2=v2.npz --dino v3b224=... \
        [--refs 4] [--draws 20] [--seed 0] [--csv out.csv]
    python <this> ... --fixed-refs style:fn1,fn2,fn3,fn4    # cross-check vs style_gate
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

from backend.ml.similarity_scorer import blend_scores, compute_style_similarity
from backend.scripts.style_crossed_probe import auc

WEIGHTS = (0.20, 0.30, 0.38)        # CLIP share of a blend; DINO gets the rest
BASELINE_TODAY = "0.38 CLIP + v2 final"
BASELINE_BRANCH = "0.30 CLIP + v2 L7"


def load_npz(path: str) -> dict:
    d = np.load(path, allow_pickle=False)
    out = {"filenames": [str(f) for f in d["filenames"]], "cls": d["cls"],
           "layers": d["layers"] if "layers" in d else None,
           "model": str(d["model"]) if "model" in d else "?",
           "size": int(d["size"]) if "size" in d else 0}
    dup = {f for f in out["filenames"] if out["filenames"].count(f) > 1}
    if dup:
        raise SystemExit(f"{path}: filenames are not unique in this dataset: {sorted(dup)[:5]}")
    return out


def build_signals(clip: dict, dinos: dict[str, dict], names: list[str]) -> dict[str, list[bytes]]:
    """Every alone-signal as a per-image list of float16 bytes, aligned to ``names``."""
    def rows(src: dict, arr: np.ndarray) -> list[bytes]:
        idx = {f: i for i, f in enumerate(src["filenames"])}
        missing = [n for n in names if n not in idx]
        if missing:
            raise SystemExit(f"{src['model']}: {len(missing)} grouped files missing, e.g. {missing[:3]}")
        return [arr[idx[n]].astype(np.float16).tobytes() for n in names]

    sig = {"CLIP": rows(clip, clip["cls"])}
    for tag, src in dinos.items():
        sig[f"{tag} final"] = rows(src, src["cls"])
        if src["layers"] is not None:
            for i in range(src["layers"].shape[1]):
                sig[f"{tag} L{i + 1}"] = rows(src, src["layers"][:, i, :])
    return sig


def configs(groups: dict[str, list[str]], names: list[str], k: int, draws: int, seed: int):
    """Yield (style, refs, cand_idx, pos_mask) per style and draw."""
    rng = np.random.default_rng(seed)
    pos_of = {n: s for s, fs in groups.items() for n in fs}
    for style, files in groups.items():
        own = [names.index(f) for f in files]
        if len(own) < k + 2:
            print(f"! {style}: {len(own)} images, need ≥ {k + 2} — skipped", file=sys.stderr)
            continue
        for _ in range(draws):
            refs = rng.choice(own, size=k, replace=False).tolist()
            cands = [i for i in range(len(names)) if i not in refs]
            pos = np.array([pos_of[names[i]] == style for i in cands])
            yield style, refs, cands, pos


def score_config(sig: dict, refs: list[int], cands: list[int], pos: np.ndarray) -> dict[str, float]:
    alone = {name: compute_style_similarity([v[i] for i in refs], [v[i] for i in cands])
             for name, v in sig.items()}
    out = {name: auc(np.array(s)[pos], np.array(s)[~pos]) for name, s in alone.items()}
    clip = alone["CLIP"]
    for name, s in alone.items():
        if name == "CLIP":
            continue
        for w in WEIGHTS:
            b = np.array(blend_scores(clip, s, w, round(1 - w, 2)))
            out[f"{w:.2f} CLIP + {name}"] = auc(b[pos], b[~pos])
    return out


def family(signal: str) -> str:
    """The DINO tag a signal belongs to ("CLIP" for CLIP alone), for leave-one-out."""
    return signal.split(" + ")[-1].split(" ")[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", required=True)
    ap.add_argument("--clip", required=True)
    ap.add_argument("--dino", action="append", required=True, help="tag=path.npz")
    ap.add_argument("--refs", type=int, default=4)
    ap.add_argument("--draws", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--fixed-refs", default=None, help="style:fn1,fn2,... — one config, no draws")
    args = ap.parse_args()

    groups = json.loads(Path(args.groups).read_text(encoding="utf-8"))
    names = [f for fs in groups.values() for f in fs]
    if len(set(names)) != len(names):
        raise SystemExit("a filename is in two groups")
    clip = load_npz(args.clip)
    dinos = {}
    for spec in args.dino:
        tag, _, path = spec.partition("=")
        dinos[tag] = load_npz(path)
        d = dinos[tag]
        shape = f"{d['layers'].shape[1]}×{d['layers'].shape[2]}" if d["layers"] is not None else "cls only"
        print(f"  {tag:<8} {d['model']} @ {d['size']}px · {shape}")
    sig = build_signals(clip, dinos, names)
    print(f"{len(groups)} styles · {len(names)} images · {len(sig)} alone-signals · "
          f"{len(sig) - 1} × {len(WEIGHTS)} blends\n")

    if args.fixed_refs:
        style, _, files = args.fixed_refs.partition(":")
        refs = [names.index(f) for f in files.split(",")]
        cands = [i for i in range(len(names)) if i not in refs]
        pos = np.array([n in groups[style] for n in (names[i] for i in cands)])
        res = score_config(sig, refs, cands, pos)
        for name in ("CLIP", "v2 L7", BASELINE_BRANCH,
                     *[s for s in res if s.endswith(" final")
                       and (not s.startswith("0.") or s.startswith("0.38"))]):
            if name in res:
                print(f"{name:<34} {res[name]:.4f}")
        return

    per_style: dict[str, dict[str, list[float]]] = {}
    for style, refs, cands, pos in configs(groups, names, args.refs, args.draws, args.seed):
        for name, v in score_config(sig, refs, cands, pos).items():
            per_style.setdefault(name, {}).setdefault(style, []).append(v)

    styles = list(next(iter(per_style.values())))
    table = {name: {s: float(np.mean(v[s])) for s in styles} for name, v in per_style.items()}
    mean = {n: float(np.mean(list(t.values()))) for n, t in table.items()}
    worst = {n: float(min(t.values())) for n, t in table.items()}

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["signal", "mean", "worst", *styles])
            for n in sorted(table, key=lambda n: -mean[n]):
                w.writerow([n, f"{mean[n]:.4f}", f"{worst[n]:.4f}", *(f"{table[n][s]:.4f}" for s in styles)])

    def row(n: str) -> str:
        return (f"{n:<34} {mean[n]:>7.4f} {worst[n]:>7.4f}  "
                + " ".join(f"{table[n][s]:>7.3f}" for s in styles))

    head = f"{'signal':<34} {'mean':>7} {'worst':>7}  " + " ".join(f"{s[:7]:>7}" for s in styles)
    print(f"AUC per style, mean of {args.draws} draws of {args.refs} references\n")
    print("=== baselines ===\n" + head)
    for n in ("CLIP", "v2 final", "v2 L7", BASELINE_TODAY, BASELINE_BRANCH):
        print(row(n))

    print("\n=== best per model: alone, and blended ===\n" + head)
    for tag in dinos:
        alone = [n for n in table if n.startswith(f"{tag} ")]
        blended = [n for n in table if n.split(" + ")[-1].startswith(f"{tag} ") and " + " in n]
        for pool in (alone, blended):
            print(row(max(pool, key=lambda n: mean[n])))

    print("\n=== top 15 overall ===\n" + head)
    for n in sorted(table, key=lambda n: -mean[n])[:15]:
        print(row(n))

    # Leave-one-out: choose a setting on the other styles, score it on the held-out one.
    print("\n=== leave-one-out (setting chosen without the held-out style) ===")
    fams = {"v2": [n for n in table if family(n) == "v2"]}
    for tag in dinos:
        if tag != "v2":
            fams[tag] = [n for n in table if family(n) == tag]
    v3_tags = [t for t in dinos if t.startswith("v3")]
    fams["all v3"] = [n for n in table if family(n) in v3_tags]
    for fam, pool in fams.items():
        if not pool:
            continue
        held, picks = [], []
        for s in styles:
            others = [x for x in styles if x != s]
            pick = max(pool, key=lambda n: np.mean([table[n][x] for x in others]))
            held.append(table[pick][s])
            picks.append(pick)
        print(f"{fam:<8} held-out mean {np.mean(held):.4f} · worst {min(held):.4f} · "
              f"picks: {', '.join(dict.fromkeys(picks))}")

    branch = mean[BASELINE_BRANCH]
    print(f"\nrule (a) bar: {BASELINE_BRANCH} mean {branch:.4f} + 0.03 = {branch + 0.03:.4f}")


if __name__ == "__main__":
    main()
