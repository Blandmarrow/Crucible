"""Phase 0 style-similarity gate — offline, read-only ranking harness.

Answers one question before any learned style head is built: **does the existing centroid
baseline already order images by style well enough that style never needs a head of its
own?** ``compute_style_similarity`` *is* that baseline (stack references → mean →
L2-renormalise → cosine), so this harness calls the production function rather than a
lookalike, and the verdict is therefore a verdict about the shipped code.

Run from the repo root with the venv active:

    python -m backend.scripts.style_gate --dataset style-gate --refs a1b2,c3d4

Why a script and not the app: ``POST /quality/style-similarity`` writes one shared
``style_similarity_score`` column with no record of which ``embedding_type`` produced it,
so comparing clip / dino / combined through the UI means three destructive overwrites and
manual snapshotting between them. This computes all three from **one** in-memory read and
never writes anything.

**The real ``dataset_manager.db`` is read and NEVER written.** That is enforced
structurally rather than by discipline: stdlib ``sqlite3`` over a ``file:…?mode=ro`` URI,
no SQLAlchemy session, no engine, no write path to get wrong.

Outputs a self-contained HTML contact sheet (thumbnails inlined as data URIs) plus a JSON
sidecar of the full rankings, so a verdict can be revisited without recomputing. The
findings go in ``backend/scripts/style_gate_report.md``.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import html
import json
import os
import sqlite3
import statistics
import sys
import tempfile
from pathlib import Path

from backend.ml.similarity_scorer import (
    compute_combined_similarity,
    compute_style_similarity,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = REPO_ROOT / "dataset_manager.db"
MODES = ("clip", "dino", "combined")

# Column each mode needs on both the references and a candidate. A row missing one is
# excluded from *that mode only* and counted as coverage — never scored as zero, per the
# failure contract in docs/dev/scoring.md.
MODE_COLUMNS = {
    "clip": ("clip_embedding",),
    "dino": ("dino_embedding",),
    "combined": ("clip_embedding", "dino_embedding"),
}


# --------------------------------------------------------------------------- read-only DB
def connect_ro(db_path: Path) -> sqlite3.Connection:
    """Open the real DB read-only. A write attempt raises rather than corrupting anything."""
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def resolve_dataset(conn: sqlite3.Connection, spec: str) -> tuple[str, str]:
    """Accept a dataset id or name; return (id, name)."""
    row = conn.execute(
        "SELECT id, name FROM datasets WHERE id = ? OR name = ?", (spec, spec)
    ).fetchone()
    if row is None:
        names = [r["name"] for r in conn.execute("SELECT name FROM datasets ORDER BY name")]
        raise SystemExit(f"No dataset matching {spec!r}. Available: {', '.join(names) or '(none)'}")
    return row["id"], row["name"]


def load_rows(conn: sqlite3.Connection, dataset_id: str) -> list[dict]:
    """One read pulls everything all three modes need."""
    cur = conn.execute(
        """
        SELECT id, filename, thumbnail_path, aesthetic_score,
               clip_embedding, dino_embedding, dino_layer_embeddings
        FROM images WHERE dataset_id = ? ORDER BY filename
        """,
        (dataset_id,),
    )
    return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------- selection
def parse_spec(spec: str | None) -> list[str]:
    """Comma list, or ``@path`` for one entry per line. Entries are ids, filenames or globs."""
    if not spec:
        return []
    if spec.startswith("@"):
        text = Path(spec[1:]).read_text(encoding="utf-8")
        entries = text.splitlines()
    else:
        entries = spec.split(",")
    return [e.strip() for e in entries if e.strip() and not e.strip().startswith("#")]


def match_rows(rows: list[dict], entries: list[str], label: str) -> set[str]:
    """Resolve id / filename / filename-glob entries to a set of image ids."""
    by_id = {r["id"] for r in rows}
    matched: set[str] = set()
    unmatched: list[str] = []
    for entry in entries:
        if entry in by_id:
            matched.add(entry)
            continue
        hits = {
            r["id"] for r in rows
            if r["filename"] == entry or fnmatch.fnmatch(r["filename"], entry)
        }
        if hits:
            matched |= hits
        else:
            unmatched.append(entry)
    if unmatched:
        print(f"  ! {label}: no image in this dataset matched {', '.join(unmatched)}")
    return matched


# --------------------------------------------------------------------------- scoring
def score_mode(mode: str, refs: list[dict], cands: list[dict]) -> dict:
    """Rank ``cands`` for one mode. Returns the ranking plus its coverage and spread.

    Rows missing an embedding this mode needs are excluded and reported, never scored.
    """
    cols = MODE_COLUMNS[mode]
    usable_refs = [r for r in refs if all(r[c] for c in cols)]
    usable_cands = [c for c in cands if all(c[col] for col in cols)]
    skipped = len(cands) - len(usable_cands)

    if not usable_refs or not usable_cands:
        return {
            "mode": mode,
            "skipped": True,
            "reason": (
                f"{len(usable_refs)}/{len(refs)} references and "
                f"{len(usable_cands)}/{len(cands)} candidates carry {' + '.join(cols)}"
            ),
            "refs_used": len(usable_refs),
            "candidates": 0,
            "excluded": skipped,
            "ranking": [],
        }

    if mode == "combined":
        scores = compute_combined_similarity(
            [r["clip_embedding"] for r in usable_refs],
            [c["clip_embedding"] for c in usable_cands],
            [r["dino_embedding"] for r in usable_refs],
            [c["dino_embedding"] for c in usable_cands],
        )
    else:
        col = cols[0]
        scores = compute_style_similarity(
            [r[col] for r in usable_refs], [c[col] for c in usable_cands]
        )

    ranking = [
        {
            "id": c["id"],
            "filename": c["filename"],
            "score": s,
            "aesthetic_score": c["aesthetic_score"],
            "control": c["_control"],
        }
        for c, s in zip(usable_cands, scores)
    ]
    ranking.sort(key=lambda r: r["score"], reverse=True)

    return {
        "mode": mode,
        "skipped": False,
        "refs_used": len(usable_refs),
        "candidates": len(usable_cands),
        "excluded": skipped,
        "ranking": ranking,
        **spread(scores),
    }


def spread(scores: list[float]) -> dict:
    """min / median / max / stdev. The spread matters as much as the ordering: a mode whose
    top and bottom differ by 0.02 has discriminated nothing, however good the pictures look."""
    return {
        "min": round(min(scores), 4),
        "median": round(statistics.median(scores), 4),
        "max": round(max(scores), 4),
        "stdev": round(statistics.pstdev(scores), 4) if len(scores) > 1 else 0.0,
        "range": round(max(scores) - min(scores), 4),
    }


def layer_sweep(refs: list[dict], cands: list[dict], top: int) -> list[dict]:
    """Optional per-DINOv2-layer sweep. Torch-free by default: ``dino_scorer`` imports torch
    at module top, so ``slice_layer_embedding`` is imported *here* and nowhere else."""
    from backend.ml.dino_scorer import slice_layer_embedding

    usable_refs = [r for r in refs if r["dino_layer_embeddings"]]
    usable_cands = [c for c in cands if c["dino_layer_embeddings"]]
    if not usable_refs or not usable_cands:
        print(
            f"  ! layers: {len(usable_refs)}/{len(refs)} refs and "
            f"{len(usable_cands)}/{len(cands)} candidates carry dino_layer_embeddings — skipped"
        )
        return []

    out = []
    for layer in range(1, 13):
        scores = compute_style_similarity(
            [slice_layer_embedding(r["dino_layer_embeddings"], layer) for r in usable_refs],
            [slice_layer_embedding(c["dino_layer_embeddings"], layer) for c in usable_cands],
        )
        ranking = sorted(
            (
                {"id": c["id"], "filename": c["filename"], "score": s, "control": c["_control"]}
                for c, s in zip(usable_cands, scores)
            ),
            key=lambda r: r["score"],
            reverse=True,
        )
        out.append({
            "layer": layer,
            "candidates": len(usable_cands),
            "top_ids": [r["id"] for r in ranking[:top]],
            "ranking": ranking,
            **spread(scores),
        })
    return out


def agreement(results: list[dict], top: int) -> list[dict]:
    """Spearman rho + top-N overlap for each pair of scored modes.

    If the three modes agree almost perfectly they are not the free A/B the roadmap assumes
    — which is a finding in itself, not a null result."""
    try:
        from scipy.stats import spearmanr
    except ImportError:  # pragma: no cover - scipy is a declared dependency
        print("  ! scipy missing — skipping cross-mode agreement")
        return []

    scored = [r for r in results if not r["skipped"]]
    pairs = []
    for i, a in enumerate(scored):
        for b in scored[i + 1:]:
            a_by_id = {r["id"]: r["score"] for r in a["ranking"]}
            b_by_id = {r["id"]: r["score"] for r in b["ranking"]}
            shared = [i_ for i_ in a_by_id if i_ in b_by_id]
            if len(shared) < 3:
                continue
            rho = spearmanr([a_by_id[i_] for i_ in shared], [b_by_id[i_] for i_ in shared]).statistic
            top_a = {r["id"] for r in a["ranking"][:top]}
            top_b = {r["id"] for r in b["ranking"][:top]}
            bot_a = {r["id"] for r in a["ranking"][-top:]}
            bot_b = {r["id"] for r in b["ranking"][-top:]}
            pairs.append({
                "modes": [a["mode"], b["mode"]],
                "n": len(shared),
                "spearman": round(float(rho), 4),
                "top_overlap": len(top_a & top_b),
                "bottom_overlap": len(bot_a & bot_b),
            })
    return pairs


# --------------------------------------------------------------------------- report
def thumb_data_uri(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    return "data:image/webp;base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def _card(row: dict, klass: str) -> str:
    tag = ' <span class="ctl">control</span>' if row.get("control") else ""
    score = row.get("score")
    return (
        f'<figure class="card"><div class="thumb {klass}"></div>'
        f'<figcaption><b>{"" if score is None else f"{score:.4f}"}</b>{tag}<br>'
        f'<span class="fn">{html.escape(row["filename"])}</span></figcaption></figure>'
    )


def build_html(ctx: dict) -> str:
    """Self-contained page: every thumbnail is emitted once as a CSS rule keyed by image id,
    so a picture appearing in several modes is not base64'd several times."""
    used: dict[str, dict] = {}

    def register(rows: list[dict]) -> None:
        for r in rows:
            used.setdefault(r["id"], r)

    register(ctx["refs"])
    for res in ctx["results"]:
        register(res["ranking"][: ctx["top"]])
        register(res["ranking"][-ctx["top"]:])

    rules = []
    for img_id, row in used.items():
        uri = ctx["thumbs"].get(img_id)
        if uri:
            rules.append(f".t-{img_id} {{ background-image: url({uri}); }}")

    def row_html(rows: list[dict]) -> str:
        return '<div class="row">' + "".join(_card(r, f"t-{r['id']}") for r in rows) + "</div>"

    parts = [
        "<h1>Style-similarity gate — contact sheet</h1>",
        f'<p class="meta">dataset <b>{html.escape(ctx["dataset_name"])}</b> · '
        f'{ctx["n_images"]} images · {len(ctx["refs"])} references · '
        f'{ctx["n_control"]} control · top/bottom {ctx["top"]} · '
        f'generated by <span class="mono">backend/scripts/style_gate.py</span> '
        f'(read-only; <span class="mono">style_similarity_score</span> untouched)</p>',
        "<h2>References</h2>",
        row_html(ctx["refs"]),
        "<h2>Spread</h2>",
        '<table><tr><th>mode</th><th>candidates</th><th>excluded</th><th>min</th>'
        "<th>median</th><th>max</th><th>range</th><th>stdev</th></tr>",
    ]
    for res in ctx["results"]:
        if res["skipped"]:
            parts.append(
                f'<tr><td>{res["mode"]}</td><td colspan="7">skipped — {html.escape(res["reason"])}</td></tr>'
            )
        else:
            parts.append(
                f'<tr><td>{res["mode"]}</td><td>{res["candidates"]}</td><td>{res["excluded"]}</td>'
                f'<td>{res["min"]}</td><td>{res["median"]}</td><td>{res["max"]}</td>'
                f'<td><b>{res["range"]}</b></td><td>{res["stdev"]}</td></tr>'
            )
    parts.append("</table>")

    if ctx["agreement"]:
        parts.append("<h2>Cross-mode agreement</h2>")
        parts.append(
            '<table><tr><th>pair</th><th>n</th><th>Spearman &rho;</th>'
            f'<th>top-{ctx["top"]} overlap</th><th>bottom-{ctx["top"]} overlap</th></tr>'
        )
        for p in ctx["agreement"]:
            parts.append(
                f'<tr><td>{" vs ".join(p["modes"])}</td><td>{p["n"]}</td><td>{p["spearman"]}</td>'
                f'<td>{p["top_overlap"]}/{ctx["top"]}</td><td>{p["bottom_overlap"]}/{ctx["top"]}</td></tr>'
            )
        parts.append("</table>")

    for res in ctx["results"]:
        parts.append(f'<h2>{res["mode"]}</h2>')
        if res["skipped"]:
            parts.append(f'<p class="meta">skipped — {html.escape(res["reason"])}</p>')
            continue
        n_ctl_bottom = sum(1 for r in res["ranking"][-ctx["top"]:] if r["control"])
        parts.append(
            f'<p class="meta">{res["candidates"]} candidates, {res["excluded"]} excluded for a '
            f'missing embedding · {res["refs_used"]} references · '
            f'{n_ctl_bottom}/{ctx["n_control"]} control images in the bottom {ctx["top"]}</p>'
        )
        parts.append(f'<h3>Top {ctx["top"]}</h3>' + row_html(res["ranking"][: ctx["top"]]))
        parts.append(f'<h3>Bottom {ctx["top"]}</h3>' + row_html(res["ranking"][-ctx["top"]:]))

    if ctx["layers"]:
        parts.append("<h2>DINOv2 per-layer sweep</h2>")
        base_top = {r["id"] for r in next(
            (x["ranking"][: ctx["top"]] for x in ctx["results"] if x["mode"] == "dino" and not x["skipped"]),
            [],
        )}
        parts.append(
            '<table><tr><th>layer</th><th>min</th><th>median</th><th>max</th><th>range</th>'
            f'<th>stdev</th><th>top-{ctx["top"]} overlap with final-layer dino</th></tr>'
        )
        for lay in ctx["layers"]:
            overlap = len(base_top & set(lay["top_ids"])) if base_top else "-"
            parts.append(
                f'<tr><td>{lay["layer"]}</td><td>{lay["min"]}</td><td>{lay["median"]}</td>'
                f'<td>{lay["max"]}</td><td><b>{lay["range"]}</b></td><td>{lay["stdev"]}</td>'
                f'<td>{overlap}</td></tr>'
            )
        parts.append("</table>")

    css = """
    body { font: 14px/1.5 system-ui, sans-serif; margin: 24px; background: #14161a; color: #e6e8ea; }
    h1 { font-size: 20px; } h2 { font-size: 17px; margin-top: 28px; border-bottom: 1px solid #2a2f36; padding-bottom: 4px; }
    h3 { font-size: 14px; color: #9aa4b2; margin: 14px 0 6px; text-transform: uppercase; letter-spacing: .06em; }
    .meta { color: #9aa4b2; } .mono { font-family: ui-monospace, monospace; }
    .row { display: flex; flex-wrap: wrap; gap: 8px; }
    .card { margin: 0; width: 150px; }
    .thumb { width: 150px; height: 100px; background-size: cover; background-position: center;
             background-color: #22262c; border-radius: 4px; }
    figcaption { font-size: 11px; color: #c3c9d1; margin-top: 3px; }
    .fn { color: #7f8894; word-break: break-all; }
    .ctl { background: #7a3d12; color: #ffd9b0; border-radius: 3px; padding: 0 4px; margin-left: 4px; }
    table { border-collapse: collapse; margin-top: 8px; }
    th, td { border: 1px solid #2a2f36; padding: 4px 10px; text-align: right; }
    th:first-child, td:first-child { text-align: left; }
    """
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Style-similarity gate</title><style>"
        + css + "\n" + "\n".join(rules)
        + "</style></head><body>" + "\n".join(parts) + "</body></html>"
    )


# --------------------------------------------------------------------------- driver
def run(args) -> None:
    db_path = Path(args.db).resolve()
    conn = connect_ro(db_path)
    try:
        dataset_id, dataset_name = resolve_dataset(conn, args.dataset)
        rows = load_rows(conn, dataset_id)
    finally:
        conn.close()

    if not rows:
        raise SystemExit(f"Dataset {dataset_name!r} has no images.")

    print(f"# Style-similarity gate — {dataset_name} ({dataset_id})")
    print(f"DB (read-only): {db_path}")
    print(f"{len(rows)} images; embeddings present: "
          f"clip {sum(1 for r in rows if r['clip_embedding'])}, "
          f"dino {sum(1 for r in rows if r['dino_embedding'])}, "
          f"layers {sum(1 for r in rows if r['dino_layer_embeddings'])}")

    ref_ids = match_rows(rows, parse_spec(args.refs), "--refs")
    if not ref_ids:
        raise SystemExit("No references resolved — pass ids or filenames via --refs.")
    control_ids = match_rows(rows, parse_spec(args.control), "--control")

    for r in rows:
        r["_control"] = r["id"] in control_ids
    refs = [r for r in rows if r["id"] in ref_ids]
    cands = [r for r in rows if r["id"] not in ref_ids]
    print(f"{len(refs)} references (excluded from the ranking), {len(cands)} candidates, "
          f"{sum(1 for c in cands if c['_control'])} of them control")

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = [m for m in modes if m not in MODES]
    if unknown:
        raise SystemExit(f"Unknown mode(s): {', '.join(unknown)}. Known: {', '.join(MODES)}")

    results = [score_mode(m, refs, cands) for m in modes]
    for res in results:
        if res["skipped"]:
            print(f"  {res['mode']}: skipped — {res['reason']}")
        else:
            print(f"  {res['mode']}: {res['candidates']} scored, {res['excluded']} excluded · "
                  f"min {res['min']} median {res['median']} max {res['max']} "
                  f"range {res['range']} stdev {res['stdev']}")

    pairs = agreement(results, args.top)
    for p in pairs:
        print(f"  {p['modes'][0]} vs {p['modes'][1]}: rho {p['spearman']} · "
              f"top {p['top_overlap']}/{args.top} · bottom {p['bottom_overlap']}/{args.top}")

    layers = layer_sweep(refs, cands, args.top) if args.layers else []

    thumbs: dict[str, str] = {}
    for r in rows:
        uri = thumb_data_uri(r["thumbnail_path"])
        if uri:
            thumbs[r["id"]] = uri
    missing_thumbs = sum(1 for r in rows if r["id"] not in thumbs)
    if missing_thumbs:
        print(f"  ! {missing_thumbs} images have no readable thumbnail — rendered as blanks")

    ctx = {
        "dataset_name": dataset_name,
        "n_images": len(rows),
        "n_control": len(control_ids),
        "refs": [{"id": r["id"], "filename": r["filename"], "score": None,
                  "control": r["_control"]} for r in refs],
        "results": results,
        "agreement": pairs,
        "layers": layers,
        "thumbs": thumbs,
        "top": args.top,
    }

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(ctx), encoding="utf-8")
    sidecar = out.with_suffix(".json")
    sidecar.write_text(
        json.dumps(
            {
                "dataset": {"id": dataset_id, "name": dataset_name, "images": len(rows)},
                "db": str(db_path),
                "refs": [r["id"] for r in refs],
                "control": sorted(control_ids),
                "top": args.top,
                "results": results,
                "agreement": pairs,
                "layers": layers,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nHTML: {out}\nJSON: {sidecar}")
    print(f"torch imported: {'yes' if 'torch' in sys.modules else 'no'}"
          f"{' (expected — --layers)' if args.layers else ''}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Read-only style-similarity gate: rank a dataset against reference "
                    "images in all three embedding modes and emit an HTML contact sheet."
    )
    ap.add_argument("--dataset", required=True, help="dataset name or id")
    ap.add_argument("--refs", required=True,
                    help="comma list of image ids or filenames (globs allowed), or @file "
                         "with one per line. References are excluded from the ranking.")
    ap.add_argument("--control", default=None,
                    help="same syntax as --refs; images flagged as an out-of-style control")
    ap.add_argument("--modes", default=",".join(MODES), help=f"default {','.join(MODES)}")
    ap.add_argument("--top", type=int, default=20, help="rows shown in the top/bottom bands")
    ap.add_argument("--layers", action="store_true",
                    help="also sweep the 12 DINOv2 layers (imports torch)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="opened read-only; never written")
    default_out = os.path.join(
        os.environ.get("CLAUDE_SCRATCH", tempfile.gettempdir()), "style_gate.html"
    )
    ap.add_argument("--out", default=default_out, help=f"HTML path (default {default_out})")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
