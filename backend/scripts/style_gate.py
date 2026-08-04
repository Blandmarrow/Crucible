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

# Modes scored as the 0.38/0.62 CLIP blend rather than a plain cosine, mapped to the column
# they blend CLIP *with*. A `--extra-embeddings` set registers one of these too, so a rival
# checkpoint is compared on both axes the app offers rather than only the raw one.
COMBINED_SOURCES = {"combined": "dino_embedding"}

# Per-layer blob geometry for each sweepable mode: (column, n_layers, dim). The app stores
# 12 × 768 float16 because that is DINOv2-base's shape; an extra set declares its own, so a
# checkpoint with a different depth or width sweeps correctly instead of failing a length
# assertion written for the shipped one.
LAYER_SOURCES = {"dino": ("dino_layer_embeddings", 12, 768)}


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
def score_mode(mode: str, refs: list[dict], cands: list[dict], top: int = 20) -> dict:
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

    if mode in COMBINED_SOURCES:
        dino_col = COMBINED_SOURCES[mode]
        scores = compute_combined_similarity(
            [r["clip_embedding"] for r in usable_refs],
            [c["clip_embedding"] for c in usable_cands],
            [r[dino_col] for r in usable_refs],
            [c[dino_col] for c in usable_cands],
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
        **separation(ranking, top),
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


def separation(ranking: list[dict], top: int) -> dict:
    """How cleanly a mode's scores tell a real frame from an out-of-style control.

    This is the falsifiable half of the gate and the only half that ranks one mode above
    another, so it lives in the script rather than being derived by hand from the JSON
    sidecar afterwards — the numbers in ``style_gate_report.md``'s separation table were,
    which is why that table could not be regenerated for a new mode.

    - ``auc`` — probability a randomly chosen frame outranks a randomly chosen control
      (0.5 = coin flip, 1.0 = perfect), by the Mann-Whitney identity with ties at half a
      point. Ties matter here: on the compressed early layers most pairs *are* tied, and
      counting a tie as a win would report a separation that does not exist.
    - ``best_threshold`` / ``accuracy`` — the best single cut point, since the score is
      ultimately a thing users filter on.
    - ``worst_control_rank`` / ``frames_below_best_control`` — the shape of the failure.
      A mode can hold a good AUC while one control lands near the top; the rank says so and
      the mean does not.

    Returns ``{}`` when the ranking has no controls — there is nothing to separate.
    """
    controls = [r["score"] for r in ranking if r["control"]]
    frames = [r["score"] for r in ranking if not r["control"]]
    if not controls or not frames:
        return {}

    wins = sum(
        1.0 if f > c else 0.5 if f == c else 0.0
        for f in frames for c in controls
    )
    auc = wins / (len(frames) * len(controls))

    best_acc, best_t = 0.0, None
    for cut in sorted({r["score"] for r in ranking}):
        # "score >= cut ⇒ frame" — the direction the UI sorts and filters in.
        correct = sum(1 for f in frames if f >= cut) + sum(1 for c in controls if c < cut)
        acc = correct / len(ranking)
        if acc > best_acc:
            best_acc, best_t = acc, cut

    best_control = max(controls)
    worst_rank = next(i for i, r in enumerate(ranking, 1) if r["control"] and r["score"] == best_control)

    return {
        "auc": round(auc, 4),
        "best_threshold": best_t,
        "accuracy": round(best_acc, 4),
        "controls_in_top": sum(1 for r in ranking[:top] if r["control"]),
        "controls_in_bottom": sum(1 for r in ranking[-top:] if r["control"]),
        "n_control": len(controls),
        "worst_control_rank": worst_rank,
        "frames_below_best_control": sum(1 for f in frames if f < best_control),
        "n_frames": len(frames),
    }


def _slice_layer(blob: bytes, layer: int, n_layers: int, dim: int) -> bytes:
    """One layer's float16 bytes out of a stacked per-layer blob.

    For the shipped 12 × 768 geometry this defers to ``dino_scorer.slice_layer_embedding``,
    so the gate keeps exercising production code on the production column; an extra
    embedding set with a different shape gets the same arithmetic without loosening that
    function's bounds check. Torch-free by default: ``dino_scorer`` imports torch at module
    top, so the import stays inside this function and nowhere else."""
    if (n_layers, dim) == (12, 768):
        from backend.ml.dino_scorer import slice_layer_embedding

        return slice_layer_embedding(blob, layer)

    expected = n_layers * dim * 2
    if len(blob) != expected:
        raise ValueError(f"Expected {expected}-byte layer blob, got {len(blob)}")
    if not (1 <= layer <= n_layers):
        raise ValueError(f"layer must be 1–{n_layers}, got {layer}")
    offset = (layer - 1) * dim * 2
    return blob[offset : offset + dim * 2]


def layer_sweep(refs: list[dict], cands: list[dict], top: int, source: str = "dino") -> list[dict]:
    """Per-layer sweep of one mode's stacked CLS blobs."""
    column, n_layers, dim = LAYER_SOURCES[source]

    usable_refs = [r for r in refs if r.get(column)]
    usable_cands = [c for c in cands if c.get(column)]
    if not usable_refs or not usable_cands:
        print(
            f"  ! layers[{source}]: {len(usable_refs)}/{len(refs)} refs and "
            f"{len(usable_cands)}/{len(cands)} candidates carry {column} — skipped"
        )
        return []

    out = []
    for layer in range(1, n_layers + 1):
        scores = compute_style_similarity(
            [_slice_layer(r[column], layer, n_layers, dim) for r in usable_refs],
            [_slice_layer(c[column], layer, n_layers, dim) for c in usable_cands],
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
            "source": source,
            "layer": layer,
            "candidates": len(usable_cands),
            "top_ids": [r["id"] for r in ranking[:top]],
            "ranking": ranking,
            **spread(scores),
            **separation(ranking, top),
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


# --------------------------------------------------------- extra (non-DB) embedding sets
def attach_extra_embeddings(rows: list[dict], spec: str) -> list[str]:
    """Load ``name=path.npz`` sets from ``backend/scripts/dino_embed_offline.py`` onto ``rows``.

    A rival checkpoint's embeddings cannot go in the DB to be compared: ``dino_embedding``
    holds exactly one model's output with no column saying which, so writing DINOv3 there
    would destroy the DINOv2 vectors this report's published numbers came from. They ride
    on the in-memory rows instead, under column names the mode table then refers to, and the
    read-only guarantee stays intact.

    Registers two modes per set — the raw cosine (``<name>``) and the 0.38/0.62 CLIP blend
    (``combined_<name>``) — plus its per-layer geometry, read from the file rather than
    assumed, so a checkpoint of a different depth or width is swept correctly.

    Returns the mode names registered, in the order given.
    """
    added: list[str] = []
    by_id = {r["id"]: r for r in rows}

    for entry in [e.strip() for e in spec.split(",") if e.strip()]:
        if "=" not in entry:
            raise SystemExit(f"--extra-embeddings wants name=path.npz, got {entry!r}")
        name, _, path = entry.partition("=")
        name, path = name.strip(), Path(path.strip()).expanduser()
        if not name.isidentifier():
            raise SystemExit(f"--extra-embeddings name {name!r} must be a plain identifier")
        if name in MODE_COLUMNS:
            raise SystemExit(f"--extra-embeddings name {name!r} collides with a built-in mode")
        if not path.is_file():
            raise SystemExit(f"--extra-embeddings: no such file {path}")

        import numpy as np  # local: the DB-only path stays numpy-free

        data = np.load(path, allow_pickle=False)
        ids = [str(i) for i in data["ids"]]
        cls = data["cls"]
        # CLIP sets carry no per-layer blob — the app stores only the final image feature —
        # so the sweep is skipped for them rather than faked with a one-layer stack.
        layers = data["layers"] if "layers" in data else None

        cls_col, layer_col = f"{name}_embedding", f"{name}_layers"
        for r in rows:
            r[cls_col] = None
            r[layer_col] = None
        hits = 0
        for i, image_id in enumerate(ids):
            row = by_id.get(image_id)
            if row is None:
                continue  # an image in the npz but not this dataset — silently not ours
            row[cls_col] = cls[i].tobytes()
            if layers is not None:
                row[layer_col] = layers[i].tobytes()
            hits += 1

        MODE_COLUMNS[name] = (cls_col,)
        MODE_COLUMNS[f"combined_{name}"] = ("clip_embedding", cls_col)
        COMBINED_SOURCES[f"combined_{name}"] = cls_col
        if layers is not None:
            LAYER_SOURCES[name] = (layer_col, int(layers.shape[1]), int(layers.shape[2]))
        added += [name, f"combined_{name}"]

        model = str(data["model"]) if "model" in data else "?"
        size = int(data["size"]) if "size" in data else 0
        shape = (f"{layers.shape[1]} layers × {layers.shape[2]} dims" if layers is not None
                 else f"{cls.shape[1]} dims, no per-layer blob")
        print(f"  + {name}: {hits}/{len(rows)} images matched from {path.name} "
              f"({model} @ {size}px · {shape})")
        if hits == 0:
            raise SystemExit(
                f"--extra-embeddings {name}: not one id in {path.name} is in this dataset — "
                "the npz was almost certainly extracted from a different one"
            )

    return added


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

    scored_sep = [r for r in ctx["results"] if not r["skipped"] and r.get("auc") is not None]
    if scored_sep:
        parts.append("<h2>Separation — does the control set sink?</h2>")
        parts.append(
            '<p class="meta">AUC is the probability a randomly chosen frame outranks a randomly '
            'chosen control (0.5 = coin flip). Range and separation are different properties: a '
            'mode can spend a wide band and still order badly.</p>'
        )
        parts.append(
            '<table><tr><th>mode</th><th>AUC</th><th>best threshold</th><th>acc</th>'
            f'<th>control in bottom {ctx["top"]}</th><th>control in top {ctx["top"]}</th>'
            "<th>worst control rank</th><th>frames below best control</th></tr>"
        )
        for res in scored_sep:
            parts.append(
                f'<tr><td>{res["mode"]}</td><td><b>{res["auc"]}</b></td>'
                f'<td>{res["best_threshold"]}</td><td>{res["accuracy"]}</td>'
                f'<td>{res["controls_in_bottom"]}/{res["n_control"]}</td>'
                f'<td>{res["controls_in_top"]}/{res["n_control"]}</td>'
                f'<td>{res["worst_control_rank"]} of {res["candidates"]}</td>'
                f'<td>{res["frames_below_best_control"]} of {res["n_frames"]}</td></tr>'
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

    for source in dict.fromkeys(lay.get("source", "dino") for lay in ctx["layers"]):
        rows_for_source = [lay for lay in ctx["layers"] if lay.get("source", "dino") == source]
        parts.append(f"<h2>{html.escape(source)} per-layer sweep</h2>")
        base_top = {r["id"] for r in next(
            (x["ranking"][: ctx["top"]] for x in ctx["results"]
             if x["mode"] == source and not x["skipped"]),
            [],
        )}
        parts.append(
            '<table><tr><th>layer</th><th>min</th><th>median</th><th>max</th><th>range</th>'
            f'<th>stdev</th><th>AUC</th><th>top-{ctx["top"]} overlap with {html.escape(source)} '
            "final layer</th></tr>"
        )
        for lay in rows_for_source:
            overlap = len(base_top & set(lay["top_ids"])) if base_top else "-"
            parts.append(
                f'<tr><td>{lay["layer"]}</td><td>{lay["min"]}</td><td>{lay["median"]}</td>'
                f'<td>{lay["max"]}</td><td><b>{lay["range"]}</b></td><td>{lay["stdev"]}</td>'
                f'<td>{lay.get("auc", "-")}</td><td>{overlap}</td></tr>'
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

    extra_modes = attach_extra_embeddings(rows, args.extra_embeddings) if args.extra_embeddings else []

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

    modes = [m.strip() for m in args.modes.split(",") if m.strip()] + extra_modes
    unknown = [m for m in modes if m not in MODE_COLUMNS]
    if unknown:
        raise SystemExit(
            f"Unknown mode(s): {', '.join(unknown)}. Known: {', '.join(MODE_COLUMNS)}"
        )

    results = [score_mode(m, refs, cands, args.top) for m in modes]
    for res in results:
        if res["skipped"]:
            print(f"  {res['mode']}: skipped — {res['reason']}")
            continue
        print(f"  {res['mode']}: {res['candidates']} scored, {res['excluded']} excluded · "
              f"min {res['min']} median {res['median']} max {res['max']} "
              f"range {res['range']} stdev {res['stdev']}")
        if res.get("auc") is not None:
            print(f"      AUC {res['auc']} · best t {res['best_threshold']} acc {res['accuracy']} · "
                  f"control in bottom {res['controls_in_bottom']}/{res['n_control']} "
                  f"top {res['controls_in_top']}/{res['n_control']} · "
                  f"worst control rank {res['worst_control_rank']}/{res['candidates']} · "
                  f"{res['frames_below_best_control']}/{res['n_frames']} frames below it")

    pairs = agreement(results, args.top)
    for p in pairs:
        print(f"  {p['modes'][0]} vs {p['modes'][1]}: rho {p['spearman']} · "
              f"top {p['top_overlap']}/{args.top} · bottom {p['bottom_overlap']}/{args.top}")

    layers = []
    if args.layers:
        for source in ["dino"] + [m for m in extra_modes if m in LAYER_SOURCES]:
            layers += layer_sweep(refs, cands, args.top, source)

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
    ap.add_argument("--extra-embeddings", default=None,
                    help="comma list of name=path.npz from dino_embed_offline.py. Each adds "
                         "a '<name>' and a 'combined_<name>' mode (and a per-layer sweep "
                         "under --layers) without any embedding entering the DB.")
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
