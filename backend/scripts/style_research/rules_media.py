"""Score the pre-registered rules of docs/dev/roadmap/style-media-runs/PREREGISTRATION.md.

``rules.py``'s scoring and report, unchanged, on test6-test8 and flickr6; only the verdicts
differ. Groups come from each set's ``MANIFEST.json`` (style = its ``artist`` field).

    python -m backend.scripts.style_research.rules_media --runs <R> --final <final dir> --out rules.txt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from backend.scripts.style_research.rules import BASE, PRIMARY, report, score

MEDIA = ("test6", "test7", "test8")
UPPER_BOUND = {("K: kalo", "test7")}   # Kaloscope was trained on these artists
# Build step 5's sensitivity runs: a style that splits with a file property (test6: Shahn's
# 35 mm aspect, Lange's half-resolution downloads), or test7's content confound.
SENSITIVITY = {"test6": ("shahn", "lange"), "test7": ("tenjin_hidetaka",)}


def groups(final: Path, tag: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in json.loads((final / tag / "MANIFEST.json").read_text(encoding="utf-8")):
        # flickr6's August manifest names the fields filename/creator
        out.setdefault(m.get("artist") or m["creator"], []).append(m.get("file") or m["filename"])
    return out


def call(s: dict) -> str:
    if s["d"] >= 0.03 and s["won"] * 2 > s["n"]:
        return "helps"
    return "hurts" if s["d"] <= -0.03 else "tie"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--final", required=True)
    ap.add_argument("--sets", default="test6,test7,test8,flickr6")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    runs, final = Path(a.runs), Path(a.final)
    sets = a.sets.split(",")

    lines: list[str] = []
    summ: dict[str, dict] = {}
    for tag in sets:
        g = groups(final, tag)
        role = "exploratory" if tag not in MEDIA else "primary"
        summ[tag] = report(f"{tag} ({role})", score(runs, tag, g), lines)
        for style in SENSITIVITY.get(tag, ()):
            gx = {k: v for k, v in g.items() if k != style}
            summ[f"{tag}-{style}"] = report(f"{tag} without {style} (sensitivity)",
                                            score(runs, tag, gx), lines)

    media = [t for t in MEDIA if t in summ]

    def verdict(n: str, calls: dict[str, str]) -> str:
        counted = [c for t, c in calls.items() if not ((n, t) in UPPER_BOUND and c == "helps")]
        if len(calls) < 3:
            return "INCOMPLETE"
        if "hurts" in calls.values():
            return "NOT GENERAL"
        return "GENERAL" if counted.count("helps") >= 2 else "NEUTRAL OUTSIDE DRAWN ART"

    lines.append("=== verdicts (per medium: helps / tie / hurts; then over the media) ===")
    for n in PRIMARY:
        calls = {t: call(summ[t][n]) for t in media}
        parts = []
        for t in media:
            ub = " (upper bound)" if (n, t) in UPPER_BOUND else ""
            parts.append(f"{t} {calls[t]}{ub} (d {summ[t][n]['d']:+.4f}, won "
                         f"{summ[t][n]['won']}/{summ[t][n]['n']})")
        v = verdict(n, calls)
        lines.append(f"  {n:<22} " + " · ".join(parts) + f" → {v}")
        for tag in media:   # the sensitivity runs: does the verdict survive without the style?
            for style in SENSITIVITY.get(tag, ()):
                cx = call(summ[f"{tag}-{style}"][n])
                vx = verdict(n, {**calls, tag: cx})
                lines.append(f"      without {tag} {style}: {tag} {cx} → {vx}"
                             f"{'  [FRAGILE]' if vx != v else ''}")

    if "test6" in summ:   # exploratory, not pre-registered: drop each photographer in turn
        g6 = groups(final, "test6")
        lines += ["", "=== test6 leave-one-out (exploratory, after the review) ==="]
        for style in g6:
            res = score(runs, "test6", {k: v for k, v in g6.items() if k != style})
            styles = list(res[BASE])
            base = np.mean([np.mean(res[BASE][s]) for s in styles])
            row = []
            for n in PRIMARY:
                m = np.mean([np.mean(res[n][s]) for s in styles])
                won = sum(np.mean(res[n][s]) > np.mean(res[BASE][s]) for s in styles)
                c = call({"d": m - base, "won": won, "n": len(styles)})
                vx = verdict(n, {**{t: call(summ[t][n]) for t in media}, "test6": c})
                row.append(f"{n.split(':')[0]} {m - base:+.4f} {won}/{len(styles)} {c} → {vx}")
            lines.append(f"  without {style:<10} " + " · ".join(row))

    def d(t, x, y):
        return summ[t][x]["mean"] - summ[t][y]["mean"]
    main_ = "0.38 CLIP + v2 final (main)"
    lines += ["", "=== hypotheses (reported, not deciding) ==="]
    if "test6" in summ:
        lines.append(f"  H5 test6 K - base          {d('test6', 'K: kalo', BASE):+.4f}  "
                     f"(predicted: not 'helps', i.e. < +0.03 or half the styles not won) "
                     f"→ K is '{call(summ['test6']['K: kalo'])}'")
    if "test7" in summ:
        lines.append(f"  H6 test7 C - base          {d('test7', 'C: csd', BASE):+.4f}  "
                     f"(predicted 'helps') → C is '{call(summ['test7']['C: csd'])}'")
    h7 = " · ".join(f"{t} {d(t, BASE, main_):+.4f}" for t in media)
    lines.append(f"  H7 base - main             {h7}  (predicted >= -0.03 on each)")
    text = "\n".join(lines)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
