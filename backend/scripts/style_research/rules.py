"""Score the pre-registered rules of docs/dev/roadmap/style-model-runs/PREREGISTRATION.md.

Reuses ``dinov3_sweep``'s loaders and its seeded draws, so every setting is scored on the
same references per draw and the paired wins are paired. The maths is the app's:
``compute_style_similarity`` (centroid cosine, 4-dp), ``blend_scores``, and ``auc`` (ties
as half).

    python -m backend.scripts.style_research.rules --runs <R> --groups4 g4.json --groups3 g3.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from backend.ml.similarity_scorer import blend_scores, compute_style_similarity
from backend.scripts.style_crossed_probe import auc
from backend.scripts.style_research.dinov3_sweep import build_signals, configs, load_npz

BASE = "0.30 CLIP + v2 L7"
# name -> (signal a, signal b, weight on a); one signal means "alone".
SETTINGS = {
    BASE: ("CLIP", "v2 L7", 0.30),
    "0.38 CLIP + v2 final (main)": ("CLIP", "v2 final", 0.38),
    "K: kalo": ("kalo final",),
    "C: csd": ("csd final",),
    "P: 0.30 PE + v2 L7": ("pe final", "v2 L7", 0.30),
    "CLIP alone": ("CLIP",),
    "pe alone": ("pe final",),
    "0.30 CLIP + kalo": ("CLIP", "kalo final", 0.30),
    "0.30 CLIP + csd": ("CLIP", "csd final", 0.30),
    "kalo pooled 1536 (exploratory)": ("kalofeat final",),
    # Added after the review: the app builds CLIP with GELU, its weights expect QuickGELU.
    "CLIP-QuickGELU alone (expl.)": ("clipq final",),
    "0.30 CLIP-QuickGELU + v2 L7 (expl.)": ("clipq final", "v2 L7", 0.30),
}
PRIMARY = ("K: kalo", "C: csd", "P: 0.30 PE + v2 L7")


def score(runs: Path, tag: str, groups: dict[str, list[str]]) -> dict[str, dict[str, list[float]]]:
    """setting -> style -> AUC per draw (20 draws of 4 references)."""
    names = [f for fs in groups.values() for f in fs]
    dinos = {t: load_npz(str(runs / f"{tag}_{t}.npz"))
             for t in ("v2", "kalo", "kalofeat", "csd", "pe", "clipq")}
    sig = build_signals(load_npz(str(runs / f"{tag}_clip.npz")), dinos, names)
    out: dict[str, dict[str, list[float]]] = {s: {} for s in SETTINGS}
    for style, refs, cands, pos in configs(groups, names, 4, 20, 0):
        need = {x for spec in SETTINGS.values() for x in spec if isinstance(x, str)}
        alone = {n: compute_style_similarity([sig[n][i] for i in refs], [sig[n][i] for i in cands])
                 for n in need}
        for name, spec in SETTINGS.items():
            s = alone[spec[0]] if len(spec) == 1 else blend_scores(
                alone[spec[0]], alone[spec[1]], spec[2], round(1 - spec[2], 2))
            s = np.array(s)
            out[name].setdefault(style, []).append(auc(s[pos], s[~pos]))
    return out


def report(title: str, res: dict, lines: list[str]) -> dict[str, dict]:
    styles = list(res[BASE])
    mean = {n: float(np.mean([np.mean(v[s]) for s in styles])) for n, v in res.items()}
    base_style = {s: np.mean(res[BASE][s]) for s in styles}
    lines += [f"=== {title}: {len(styles)} styles ===",
              f"{'setting':<32} {'mean':>7} {'d base':>7} {'won':>5}  "
              + " ".join(f"{s[:9]:>9}" for s in styles)]
    summary = {}
    for n in SETTINGS:
        won = sum(np.mean(res[n][s]) > base_style[s] for s in styles)
        summary[n] = {"mean": mean[n], "d": mean[n] - mean[BASE], "won": int(won), "n": len(styles)}
        lines.append(f"{n:<32} {mean[n]:>7.4f} {mean[n] - mean[BASE]:>+7.4f} {won:>2}/{len(styles):<2}  "
                     + " ".join(f"{np.mean(res[n][s]):>9.3f}" for s in styles))
    lines.append("\npaired wins over the baseline per style (of 20 draws); SD over draws")
    for n in PRIMARY + ("0.30 CLIP + kalo", "0.30 CLIP + csd", "pe alone"):
        wins = " ".join(f"{s[:9]}:{sum(a > b for a, b in zip(res[n][s], res[BASE][s])):>2}" for s in styles)
        sds = [float(np.std(res[n][s])) for s in styles]
        lines.append(f"  {n:<30} {wins}   SD {min(sds):.3f}-{max(sds):.3f}")
    lines.append("")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--groups4", required=True)
    ap.add_argument("--groups3", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    runs = Path(a.runs)
    g4 = json.loads(Path(a.groups4).read_text(encoding="utf-8"))
    g3 = json.loads(Path(a.groups3).read_text(encoding="utf-8"))
    g4x = {k: v for k, v in g4.items() if k != "edomura_niniko"}

    lines: list[str] = []
    s4 = report("test4 (primary)", score(runs, "test4", g4), lines)
    s4x = report("test4 without edomura_niniko (sensitivity)", score(runs, "test4", g4x), lines)
    s3 = report("test3 (secondary)", score(runs, "test3", g3), lines)

    lines.append("=== verdicts (rule 1 on test4, rule 2 on test3) ===")
    for n in PRIMARY:
        r1 = s4[n]["d"] >= 0.03 and s4[n]["won"] * 2 > s4[n]["n"]
        r2 = s3[n]["d"] >= -0.03
        r1x = s4x[n]["d"] >= 0.03 and s4x[n]["won"] * 2 > s4x[n]["n"]
        verdict = "PASS" if r1 and r2 else "ANIME ONLY" if r1 else "FAIL"
        lines.append(f"  {n:<22} rule1 {'pass' if r1 else 'fail'} (d {s4[n]['d']:+.4f}, won "
                     f"{s4[n]['won']}/{s4[n]['n']}) · rule2 {'pass' if r2 else 'fail'} (d {s3[n]['d']:+.4f})"
                     f" → {verdict} · without edomura rule1 {'pass' if r1x else 'fail'}"
                     f"{'  [FRAGILE]' if r1x != r1 else ''}")

    def d(s, x, y):
        return s[x]["mean"] - s[y]["mean"]
    lines += ["", "=== hypotheses (reported, not deciding) ===",
              f"  H1 test4 K - C            {d(s4, 'K: kalo', 'C: csd'):+.4f}  (need >= +0.03)",
              f"  H2 test3 C - CLIP alone   {d(s3, 'C: csd', 'CLIP alone'):+.4f}  (need >= +0.03)",
              f"  H3 pe - CLIP alone        test4 {d(s4, 'pe alone', 'CLIP alone'):+.4f} · "
              f"test3 {d(s3, 'pe alone', 'CLIP alone'):+.4f}  (need >= +0.03 on both)",
              f"  H4 test4 blend - alone    kalo {d(s4, '0.30 CLIP + kalo', 'K: kalo'):+.4f} · "
              f"csd {d(s4, '0.30 CLIP + csd', 'C: csd'):+.4f}  (predicted < 0)"]
    text = "\n".join(lines)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
