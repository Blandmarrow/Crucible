"""Crossed style-vs-subject probes for the style-similarity default.

The companion to ``style_gate.py``, and the thing it cannot be. The gate scores
**separability**: do in-style candidates outrank out-of-style controls, with subject and
framing free to vary however they happen to. A signal that ranks on *content* wins that,
because references and positives almost always share subject matter as well as a look — so
every per-layer and per-blend number the gate and its sweeps produced could have been won
for the wrong reason, including the ones that chose ``DEFAULT_DINO_LAYER``.

These probes are **crossed**: each holds one of style/subject fixed while varying the other,
which is the only design that can say whether a signal ranks on *look* or on *content*.

    synthetic   one picture rendered under several restyles and several framings, so
                "different picture, same rendering" and "same picture, restyled" both
                exist as pair classes and a layer can be asked which it ranks higher
    pools       several real style pools that share a subject domain, with subject then
                held fixed by CLIP zero-shot bins
    combos      whether combining layers beats picking one (it does not — see below)
    refs        whether spreading references across subjects, at a fixed reference count,
                makes the score better (it does, once there are enough of them)

``synthetic`` and ``pools`` extract embeddings and cache them to ``.npz``; ``combos`` and
``refs`` read that cache and are numpy-only, so the analysis re-runs on a machine with no
torch. Extraction goes through ``dino_embed_offline.extract``/``extract_clip``, which mirror
``dino_scorer`` and ``aesthetic_scorer`` exactly, so a finding here is about the model rather
than about this harness.

**Nothing is written to ``dataset_manager.db``** — stdlib ``sqlite3`` over a
``file:…?mode=ro`` URI, the same guard ``style_gate.py`` uses — and **nothing is written into
the datasets tree**: generated variants go to ``--work``, which defaults to a temp directory.

Every metric is an **AUC over pair classes**, never a mean cosine or a threshold. That is
load-bearing: DINOv2's low layers compress every image into 0.90–0.99 while layer 12 spans
0.65, so any scale-carrying statistic compares layers by their spread rather than by their
ordering. Ranks are scale-free; on the compressed layers most pairs are genuinely tied, so
ties take the average rank.

Run from the repo root with the venv active::

    # Run A — the synthetic crossing (the numbers in the roadmap's § The crossed runs)
    python -m backend.scripts.style_crossed_probe synthetic \\
        --bases test5:18 --bases test3:12 --out /tmp/crossed_synth.npz

    # Run B — real pools sharing one subject domain, subject held fixed by CLIP bins
    python -m backend.scripts.style_crossed_probe pools \\
        --pool vhd=test2:150 --pool hell=test3 --pool sdxl=@data/final \\
        --pool photo=test5 --style-subset vhd,hell,sdxl --out /tmp/crossed_pools.npz

    # the two analyses, on the caches above — no torch needed
    python -m backend.scripts.style_crossed_probe combos --npz /tmp/crossed_synth.npz \\
        --natural /tmp/crossed_pools.npz
    python -m backend.scripts.style_crossed_probe refs --npz /tmp/crossed_pools.npz \\
        --targets vhd,hell,sdxl

A pool can also be split out of one dataset by filename prefix — ``--pool-from-prefix
test5`` makes one pool per creator, which is how the photographer test is run.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = REPO_ROOT / "dataset_manager.db"

# Layers the blend rows are reported at: the two shipped defaults, past and present, plus
# the ends of the band. Every individual layer is in the table above them regardless.
BLEND_LAYERS = (5, 7, 9, 12)
OLD_CLIP_WEIGHT, OLD_DINO_WEIGHT = 0.38, 0.62  # the pre-2026-08-04 blend, on the final CLS


# --------------------------------------------------------------------- the style axis
# Two families, and the split matters. Tone restyles change palette and contrast only;
# rendering restyles change how the picture is *drawn*, which is what the complaint these
# probes exist to check means by "art style". A conclusion that holds only on the tone
# family is a conclusion about colour grading.
def _g_orig(im):
    return im


def _g_gray(im):
    return ImageOps.grayscale(im).convert("RGB")


def _g_warm(im):
    r, g, b = im.split()
    r = r.point(lambda x: min(255, int(x * 1.15 + 14)))
    g = g.point(lambda x: min(255, int(x * 1.02 + 4)))
    b = b.point(lambda x: int(x * 0.78))
    return Image.merge("RGB", (r, g, b))


def _g_punch(im):
    return ImageEnhance.Color(ImageEnhance.Contrast(im).enhance(1.55)).enhance(1.75)


def _g_poster(im):
    """Flat limited-palette illustration look."""
    return ImageOps.posterize(im, 3).filter(ImageFilter.MedianFilter(5))


def _g_smear(im):
    """Painterly: a large median filter reads as brushwork, then edges come back."""
    return im.filter(ImageFilter.MedianFilter(9)).filter(ImageFilter.EDGE_ENHANCE_MORE)


def _g_sketch(im):
    """Pencil-sketch dodge blend — line art of the same picture."""
    gray = ImageOps.grayscale(im)
    blurred = ImageOps.invert(gray).filter(ImageFilter.GaussianBlur(12))
    g = np.asarray(gray, dtype=np.float32)
    b = np.asarray(blurred, dtype=np.float32)
    dodge = np.clip(g * 255.0 / np.maximum(255.0 - b, 1.0), 0, 255).astype(np.uint8)
    return Image.fromarray(dodge).convert("RGB")


GRADES = {"orig": _g_orig, "gray": _g_gray, "warm": _g_warm, "punch": _g_punch,
          "poster": _g_poster, "smear": _g_smear, "sketch": _g_sketch}
TONE_FAMILY = ("orig", "gray", "warm", "punch")
RENDER_FAMILY = ("orig", "poster", "smear", "sketch")

# --------------------------------------------------------------- the composition axis
# Reframes, not crops-to-subject: two 62% windows from opposite corners keep the grain,
# palette and rendering identical while moving what is where. They also *remove* content,
# which is why the reframe column is read as "layout and some content" and never as pure
# layout — stated in the roadmap's caveat list rather than papered over here.
FRAMINGS = {"full": None, "cropA": (0.0, 0.0, 0.62, 0.62), "cropB": (0.38, 0.38, 1.0, 1.0)}

# CLIP zero-shot bins for the subject control in `pools`. Deliberately coarse: the bin only
# has to be right often enough to remove the gross subject differences between pools.
SUBJECT_PROMPTS = (
    "a close-up of a face",
    "a full-body figure of a person",
    "an outdoor landscape or sky",
    "an interior room or building architecture",
    "an object or prop in close-up",
    "a crowd or group of several people",
)


# ------------------------------------------------------------------------ read-only DB
def open_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def dataset_files(conn: sqlite3.Connection, dataset: str) -> list[tuple[str, Path]]:
    """(filename, path) for one dataset, sorted, existing files only."""
    ds = conn.execute(
        "SELECT id, name FROM datasets WHERE id = ? OR name = ?", (dataset, dataset)
    ).fetchone()
    if ds is None:
        names = [r["name"] for r in conn.execute("SELECT name FROM datasets ORDER BY name")]
        raise SystemExit(f"No dataset matching {dataset!r}. Available: {', '.join(names)}")
    rows = conn.execute(
        "SELECT filename, file_path FROM images WHERE dataset_id = ? ORDER BY filename",
        (ds["id"],),
    ).fetchall()
    return [(r["filename"], Path(r["file_path"])) for r in rows
            if r["file_path"] and Path(r["file_path"]).is_file()]


def dir_files(path: Path) -> list[tuple[str, Path]]:
    from backend.media_types import IMAGE_EXTENSIONS
    files = sorted(p for p in path.iterdir()
                   if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
    return [(p.name, p) for p in files]


def spread(items: list, n: int | None) -> list:
    """Take ``n`` evenly spaced through ``items`` — never the first n, which for a film
    dataset is one scene and for a per-creator one is one creator."""
    if n is None or n >= len(items):
        return items
    idx = np.linspace(0, len(items) - 1, n).round().astype(int)
    return [items[i] for i in dict.fromkeys(idx.tolist())]


def resolve_source(conn, spec: str) -> tuple[str, list[tuple[str, Path]]]:
    """``dataset[:count]`` or ``@relative/dir[:count]`` -> (label, files)."""
    body, _, count = spec.partition(":")
    n = int(count) if count else None
    if body.startswith("@"):
        path = (REPO_ROOT / body[1:]).resolve()
        if not path.is_dir():
            raise SystemExit(f"Not a directory: {path}")
        return body[1:], spread(dir_files(path), n)
    return body, spread(dataset_files(conn, body), n)


# ------------------------------------------------------------------------- statistics
def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(a random positive outranks a random negative), ties counted as half.

    Rank-based on purpose — see the module docstring on why a mean cosine cannot compare
    two layers.
    """
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind="mergesort")
    ranks = np.empty(len(allv), dtype=float)
    ranks[order] = np.arange(1, len(allv) + 1, dtype=float)
    sorted_v = allv[order]
    i = 0
    while i < len(sorted_v):                       # average the ranks inside each tie run
        j = i
        while j + 1 < len(sorted_v) and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = ranks[order[i:j + 1]].mean()
        i = j + 1
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def zscore(v: np.ndarray) -> np.ndarray:
    return (v - v.mean()) / (v.std() + 1e-9)


def unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-8)


def pair_cosines(vecs: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    v = unit(vecs.astype(np.float32))
    return (v[a] * v[b]).sum(axis=1)


def signal_table(layers, cls, clip, a, b) -> dict[str, np.ndarray]:
    """Every signal the reports compare, as pairwise similarity vectors."""
    sig = {f"L{i + 1}": pair_cosines(layers[:, i, :], a, b) for i in range(layers.shape[1])}
    sig["final CLS"] = pair_cosines(cls, a, b)
    sig["CLIP"] = pair_cosines(clip, a, b)
    from backend.ml.similarity_scorer import STYLE_CLIP_WEIGHT, STYLE_DINO_WEIGHT
    for lay in BLEND_LAYERS:
        sig[f"blend @L{lay}"] = (STYLE_CLIP_WEIGHT * sig["CLIP"]
                                 + STYLE_DINO_WEIGHT * sig[f"L{lay}"])
    sig["blend @final (old)"] = OLD_CLIP_WEIGHT * sig["CLIP"] + OLD_DINO_WEIGHT * sig["final CLS"]
    return sig


# -------------------------------------------------------------------------- extraction
def embed(paths: list[str], model_id: str, batch: int):
    """(cls, layers, clip) for a path list, through the app's own extraction path."""
    import torch
    from backend.ml import device as _device
    from backend.scripts.dino_embed_offline import extract, extract_clip
    from transformers import AutoImageProcessor, AutoModel

    device = _device.get_device()
    processor = AutoImageProcessor.from_pretrained(model_id)
    model = AutoModel.from_pretrained(model_id, dtype=torch.float32).to(device).eval()
    print(f"{model_id}:")
    cls, layers = extract(model, processor, paths, device, batch)
    del model
    print("open_clip ViT-L-14/openai:")
    clip = extract_clip(paths, device, batch)
    return cls, layers, clip


# ============================================================ subcommand: synthetic
def build_variants(bases: list[tuple[str, Path]], work: Path) -> list[dict]:
    """One file per (base, grade, framing). Existing files are reused, never rewritten."""
    work.mkdir(parents=True, exist_ok=True)
    stems = [p.stem for _, p in bases]
    dupes = {s for s in stems if stems.count(s) > 1}
    if dupes:
        raise SystemExit(
            f"Base filenames collide across sources: {sorted(dupes)}. Variants are named "
            "by stem, so narrow --bases until the stems are unique."
        )
    rows: list[dict] = []
    for source, path in bases:
        with Image.open(path) as raw:
            img = ImageOps.exif_transpose(raw).convert("RGB")   # CLAUDE.md: transpose first
            w, h = img.size
            for fname, box in FRAMINGS.items():
                framed = img if box is None else img.crop(
                    (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
                for gname, fn in GRADES.items():
                    out = work / f"{path.stem}__{gname}__{fname}.jpg"
                    if not out.exists():
                        fn(framed).save(out, "JPEG", quality=95)
                    rows.append({"path": str(out), "base": path.stem, "source": source,
                                 "grade": gname, "framing": fname})
    return rows


def report_synthetic(d) -> None:
    layers, cls, clip = d["layers"], d["cls"], d["clip"]
    base, grade, framing, source = d["base"], d["grade"], d["framing"], d["source"]
    n = len(base)
    a, b = np.triu_indices(n, k=1)
    sig = signal_table(layers, cls, clip, a, b)

    same_base = base[a] == base[b]
    same_grade = grade[a] == grade[b]
    same_fram = framing[a] == framing[b]
    same_source = source[a] == source[b]

    print("""
style      = AUC(STYLE > NOTHING)    can it see a shared look across different pictures?
picture    = AUC(PICTURE > NOTHING)  can it see the same picture through a restyle?
reframe    = AUC(FRAMING > PICTURE)  BELOW 0.5 means a reframe costs more than a restyle
style>pic  = AUC(STYLE > PICTURE)    prefers a shared look to the same picture restyled""")

    groups = [("all bases", np.ones(len(a), dtype=bool))]
    for name in dict.fromkeys(source.tolist()):
        groups.append((f"{name} bases only", (source[a] == name) & same_source))

    for family_name, family in (("TONE", TONE_FAMILY), ("RENDERING", RENDER_FAMILY)):
        in_family = np.isin(grade[a], family) & np.isin(grade[b], family)
        for group_name, group in groups:
            m = in_family & same_source & group
            cls_masks = {
                "STYLE": m & ~same_base & same_grade,
                "PICTURE": m & same_base & same_fram & ~same_grade,
                "FRAMING": m & same_base & same_grade & ~same_fram,
                "NOTHING": m & ~same_base & ~same_grade,
            }
            counts = " ".join(f"{k} {int(v.sum())}" for k, v in cls_masks.items())
            print(f"\n=== {family_name} restyles — {group_name} ===\n    pairs: {counts}")
            print(f"{'signal':<20} {'style':>7} {'picture':>8} {'reframe':>8} {'style>pic':>10}")
            for name, s in sig.items():
                print(f"{name:<20} "
                      f"{auc(s[cls_masks['STYLE']], s[cls_masks['NOTHING']]):>7.3f} "
                      f"{auc(s[cls_masks['PICTURE']], s[cls_masks['NOTHING']]):>8.3f} "
                      f"{auc(s[cls_masks['FRAMING']], s[cls_masks['PICTURE']]):>8.3f} "
                      f"{auc(s[cls_masks['STYLE']], s[cls_masks['PICTURE']]):>10.3f}")


def cmd_synthetic(args) -> None:
    conn = open_ro(Path(args.db).resolve())
    bases: list[tuple[str, Path]] = []
    try:
        for spec in args.bases:
            label, files = resolve_source(conn, spec)
            bases += [(label, p) for _, p in files]
    finally:
        conn.close()
    if not bases:
        raise SystemExit("--bases selected no images")

    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="crossed-"))
    rows = build_variants(bases, work)
    print(f"{len(bases)} base pictures x {len(GRADES)} restyles x {len(FRAMINGS)} framings "
          f"= {len(rows)} variants in {work}")

    cls, layers, clip = embed([r["path"] for r in rows], args.model, args.batch)
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, layers=layers, cls=cls, clip=clip,
             base=np.array([r["base"] for r in rows]),
             source=np.array([r["source"] for r in rows]),
             grade=np.array([r["grade"] for r in rows]),
             framing=np.array([r["framing"] for r in rows]))
    print(f"wrote {out}")
    report_synthetic(np.load(out, allow_pickle=False))


# ================================================================ subcommand: pools
def report_pools(d, style_subset: list[str] | None) -> None:
    layers, cls, clip = d["layers"], d["cls"], d["clip"]
    pool, subj = d["pool"], d["subj"]
    n = len(pool)
    a, b = np.triu_indices(n, k=1)
    sig = signal_table(layers, cls, clip, a, b)

    same_pool = pool[a] == pool[b]
    same_subj = subj[a] == subj[b]
    subset = (np.isin(pool[a], style_subset) & np.isin(pool[b], style_subset)
              if style_subset else np.ones(len(a), dtype=bool))
    binned = subj[a] >= 0
    sub_label = ",".join(style_subset) if style_subset else "all pools"

    print(f"""
all pools    AUC(same pool > different pool) — includes any medium cue the pools carry
{sub_label:<12} the same, restricted to those pools
same bin     the same again, both images in one CLIP subject bin: subject held fixed,
             so only the rendering can carry the style label
subject leak AUC(same subject bin > different bin) among pairs of DIFFERENT style
             -> how strongly the signal ranks on content when the label says it should not
margin       style minus leak. Positive means style outranks subject at that depth.""")
    print(f"\n{'signal':<20} {'all pools':>10} {sub_label[:10]:>10} {'same bin':>9} "
          f"{'subj leak':>10} {'margin':>8}")
    for name, s in sig.items():
        subset_auc = auc(s[subset & same_pool], s[subset & ~same_pool])
        leak = auc(s[subset & ~same_pool & same_subj & binned],
                   s[subset & ~same_pool & ~same_subj & binned])
        print(f"{name:<20} "
              f"{auc(s[same_pool], s[~same_pool]):>10.3f} "
              f"{subset_auc:>10.3f} "
              f"{auc(s[subset & same_pool & same_subj & binned], s[subset & ~same_pool & same_subj & binned]):>9.3f} "
              f"{leak:>10.3f} "
              f"{subset_auc - leak:>+8.3f}")


def cmd_pools(args) -> None:
    conn = open_ro(Path(args.db).resolve())
    items: list[tuple[str, Path]] = []
    try:
        for spec in args.pool or []:
            label, _, body = spec.partition("=")
            if not body:
                raise SystemExit(f"--pool wants name=source, got {spec!r}")
            _, files = resolve_source(conn, body)
            items += [(label, p) for _, p in files]
        for dataset in args.pool_from_prefix or []:
            for filename, path in dataset_files(conn, dataset):
                items.append((filename.rsplit("_", 1)[0], path))
    finally:
        conn.close()
    if not items:
        raise SystemExit("no pools selected")

    pools = np.array([label for label, _ in items])
    for name in dict.fromkeys(pools.tolist()):
        print(f"  {name:<12} {int((pools == name).sum())}")

    cls, layers, clip = embed([str(p) for _, p in items], args.model, args.batch)

    if args.bins:
        import open_clip
        import torch
        from backend.ml import device as _device
        model, _, _ = open_clip.create_model_and_transforms("ViT-L-14", pretrained="openai")
        model = model.to(_device.get_device()).eval()
        tokenizer = open_clip.get_tokenizer("ViT-L-14")
        with torch.no_grad():
            text = model.encode_text(tokenizer(list(SUBJECT_PROMPTS)).to(_device.get_device()))
            text = (text / text.norm(dim=-1, keepdim=True)).float().cpu().numpy()
        subj = np.argmax(clip.astype(np.float32) @ text.T, axis=1)
        for i, prompt in enumerate(SUBJECT_PROMPTS):
            per = {p: int(((pools == p) & (subj == i)).sum())
                   for p in dict.fromkeys(pools.tolist())}
            print(f"  bin {i} {prompt:<45} n={int((subj == i).sum()):>3}  {per}")
    else:
        # -1 is "unbinned": the subject-controlled columns skip these rather than treating
        # every image as one giant bin, which would silently report the unbinned figure.
        subj = np.full(len(items), -1)

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, layers=layers, cls=cls, clip=clip, pool=pools, subj=subj,
             path=np.array([str(p) for _, p in items]))
    print(f"wrote {out}")
    subset = args.style_subset.split(",") if args.style_subset else None
    report_pools(np.load(out, allow_pickle=False), subset)


# =============================================================== subcommand: combos
COMBOS = (
    ("L7 alone", (7,), "raw"),
    ("L9 alone", (9,), "raw"),
    ("L12 alone", (12,), "raw"),
    ("all 12, plain mean", tuple(range(1, 13)), "raw"),
    ("all 12, standardised", tuple(range(1, 13)), "z"),
    ("L5-L9, plain mean", (5, 6, 7, 8, 9), "raw"),
    ("L5-L9, standardised", (5, 6, 7, 8, 9), "z"),
    ("L3-L7, standardised", (3, 4, 5, 6, 7), "z"),
    ("L1-L6, standardised", (1, 2, 3, 4, 5, 6), "z"),
)


def combine(per_layer: list[np.ndarray], idx: tuple[int, ...], mode: str) -> np.ndarray:
    xs = [per_layer[i - 1] for i in idx]
    return np.mean(xs if mode == "raw" else [zscore(x) for x in xs], axis=0)


def cmd_combos(args) -> None:
    d = np.load(Path(args.npz).resolve(), allow_pickle=False)
    layers = d["layers"]
    base, grade, framing, source = d["base"], d["grade"], d["framing"], d["source"]
    n = len(base)
    a, b = np.triu_indices(n, k=1)
    per_layer = [pair_cosines(layers[:, i, :], a, b) for i in range(layers.shape[1])]

    print("=== inter-layer redundancy: correlation of the per-layer pairwise similarities ===")
    corr = np.corrcoef(np.stack(per_layer))
    print("      " + "".join(f"L{j + 1:<5}" for j in range(len(per_layer))))
    for i in range(len(per_layer)):
        print(f"L{i + 1:<4} " + "".join(f"{corr[i, j]:>5.2f} " for j in range(len(per_layer))))
    ev = np.linalg.eigvalsh(corr)[::-1]
    k = int((np.cumsum(ev) / len(ev) < 0.95).sum()) + 1
    print(f"\neigenvalues: {np.round(ev, 2)}")
    print(f"-> {k} components carry 95% of the variance of all {len(ev)} layers; "
          f"the first alone carries {ev[0] / len(ev):.0%}")

    in_family = np.isin(grade[a], RENDER_FAMILY) & np.isin(grade[b], RENDER_FAMILY)
    m = in_family & (source[a] == source[b])
    same_base, same_grade, same_fram = base[a] == base[b], grade[a] == grade[b], framing[a] == framing[b]
    STYLE = m & ~same_base & same_grade
    PICTURE = m & same_base & same_fram & ~same_grade
    NOTHING = m & ~same_base & ~same_grade

    nat = None
    if args.natural:
        nd = np.load(Path(args.natural).resolve(), allow_pickle=False)
        pool = nd["pool"]
        na, nb = np.triu_indices(len(pool), k=1)
        nat = ([pair_cosines(nd["layers"][:, i, :], na, nb) for i in range(nd["layers"].shape[1])],
               pair_cosines(nd["clip"], na, nb), pool[na] == pool[nb])

    print("\n=== combinations, on the RENDERING family ===")
    head = f"{'combination':<22} {'style':>7} {'style>pic':>10}"
    print(head + (f" {'natural':>9} {'+CLIP':>7}" if nat else ""))
    for name, idx, mode in COMBOS:
        s = combine(per_layer, idx, mode)
        row = f"{name:<22} {auc(s[STYLE], s[NOTHING]):>7.3f} {auc(s[STYLE], s[PICTURE]):>10.3f}"
        if nat:
            nlayers, nclip, same = nat
            t = combine(nlayers, idx, mode)
            blended = 0.30 * (zscore(nclip) if mode == "z" else nclip) + 0.70 * t
            row += f" {auc(t[same], t[~same]):>9.3f} {auc(blended[same], blended[~same]):>7.3f}"
        print(row)
    print("\nA plain mean over all layers is dominated by whichever layers have the widest "
          "spread\n(the deep ones); standardising first fixes that but needs population "
          "statistics, so it\ncannot back a stored per-image score. See the roadmap's "
          "§ Why one layer and not all twelve.")


# ================================================================= subcommand: refs
def cmd_refs(args) -> None:
    d = np.load(Path(args.npz).resolve(), allow_pickle=False)
    layers, clip, pool, subj = d["layers"], d["clip"], d["pool"], d["subj"]
    if (subj < 0).all():
        raise SystemExit("this cache has no subject bins — re-run `pools` without --no-bins")
    targets = args.targets.split(",") if args.targets else list(dict.fromkeys(pool.tolist()))
    rng = np.random.default_rng(args.seed)
    from backend.ml.similarity_scorer import (
        DEFAULT_DINO_LAYER, STYLE_CLIP_WEIGHT, STYLE_DINO_WEIGHT,
    )
    signals = {f"L{lay}": layers[:, lay - 1, :] for lay in BLEND_LAYERS}
    signals["CLIP"] = clip
    in_scope = np.isin(pool, targets)

    def centroid_scores(vecs, refs, cands):
        """The app's own rule: mean of the reference embeddings, renormalise, cosine."""
        v = unit(vecs.astype(np.float32))
        mean_ref = v[refs].mean(axis=0)
        mean_ref /= np.linalg.norm(mean_ref) + 1e-8
        return v[cands] @ mean_ref

    def one_trial(target, k, diverse):
        own = np.flatnonzero(pool == target)
        bins = {b: own[subj[own] == b] for b in np.unique(subj[own])}
        bins = {b: v for b, v in bins.items() if len(v)}
        if diverse:
            order = sorted(bins, key=lambda b: -len(bins[b]))
            refs: list[int] = []
            guard = 0
            while len(refs) < k and guard < 1000:
                pick = int(rng.choice(bins[order[len(refs) % len(order)]]))
                if pick not in refs:
                    refs.append(pick)
                guard += 1
            if len(refs) < k:
                return None
            ref_idx = np.array(refs)
        else:
            biggest = max(bins, key=lambda b: len(bins[b]))
            if len(bins[biggest]) < k:
                return None
            ref_idx = rng.choice(bins[biggest], size=k, replace=False)
        cands = np.flatnonzero(in_scope & ~np.isin(np.arange(len(pool)), ref_idx))
        pos = pool[cands] == target
        out = {name: auc(*_split(centroid_scores(v, ref_idx, cands), pos))
               for name, v in signals.items()}
        blend = (STYLE_CLIP_WEIGHT * centroid_scores(clip, ref_idx, cands)
                 + STYLE_DINO_WEIGHT * centroid_scores(layers[:, DEFAULT_DINO_LAYER - 1, :],
                                                       ref_idx, cands))
        out[f"blend @L{DEFAULT_DINO_LAYER}"] = auc(*_split(blend, pos))
        return out

    print("AUC separating the reference style from the other pools in --targets.")
    print("NARROW = every reference from one subject bin · DIVERSE = spread across bins\n")
    for k in [int(x) for x in args.k.split(",")]:
        print(f"--- {k} references, {args.trials} trials " + "-" * 30)
        print(f"{'signal':<12} " + "".join(f"{t[:20]:>24}" for t in targets))
        print(f"{'':<12} " + "".join(f"{'narrow  diverse    gain':>24}" for _ in targets))
        means: dict[str, dict] = {}
        for target in targets:
            for diverse in (False, True):
                runs = [r for r in (one_trial(target, k, diverse) for _ in range(args.trials))
                        if r]
                for name in (runs[0] if runs else {}):
                    means.setdefault(name, {})[(target, diverse)] = float(
                        np.mean([r[name] for r in runs]))
        for name in list(signals) + [f"blend @L{DEFAULT_DINO_LAYER}"]:
            cells = ""
            for target in targets:
                narrow = means.get(name, {}).get((target, False))
                div = means.get(name, {}).get((target, True))
                cells += (f"{'—':>24}" if narrow is None or div is None
                          else f"{narrow:>8.3f}{div:>8.3f}{div - narrow:>+8.3f}")
            print(f"{name:<12} " + cells)
        print()


def _split(scores: np.ndarray, pos_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return scores[pos_mask], scores[~pos_mask]


# ============================================================================ driver
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Crossed style-vs-subject probes. Reads the DB read-only and never "
                    "writes an embedding into it.")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="opened read-only; never written")
    ap.add_argument("--model", default="facebook/dinov2-base",
                    help="any DINO checkpoint dino_embed_offline can load")
    ap.add_argument("--batch", type=int, default=8)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synthetic", help="one picture x several restyles x several framings")
    s.add_argument("--bases", action="append", required=True,
                   help="dataset[:count] or @dir[:count], repeatable; count is spread evenly")
    s.add_argument("--work", default=None,
                   help="where variants are written (default: a temp dir). Never the "
                        "datasets tree; existing files are reused.")
    s.add_argument("--out", required=True, help="npz cache to write")
    s.set_defaults(func=cmd_synthetic)

    p = sub.add_parser("pools", help="real style pools, subject held fixed by CLIP bins")
    p.add_argument("--pool", action="append", help="name=dataset[:count] or name=@dir[:count]")
    p.add_argument("--pool-from-prefix", action="append",
                   help="dataset whose filename prefixes (before the last _) are the pools")
    p.add_argument("--style-subset", default=None,
                   help="comma list of pool names to restrict the second column to — use it "
                        "to drop the medium cue, e.g. the drawn pools only")
    p.add_argument("--no-bins", dest="bins", action="store_false",
                   help="skip the CLIP subject binning (the subject-controlled columns then "
                        "report nothing rather than reporting the unbinned figure)")
    p.add_argument("--out", required=True, help="npz cache to write")
    p.set_defaults(func=cmd_pools)

    c = sub.add_parser("combos", help="does combining layers beat picking one?")
    c.add_argument("--npz", required=True, help="a `synthetic` cache")
    c.add_argument("--natural", default=None,
                   help="a `pools` cache, for the real-label column")
    c.set_defaults(func=cmd_combos)

    r = sub.add_parser("refs", help="narrow vs subject-diverse reference sets")
    r.add_argument("--npz", required=True, help="a `pools` cache, binned")
    r.add_argument("--targets", default=None,
                   help="comma list of pools in play (default: all). Each is used in turn "
                        "as the reference style, and together they are also the candidate "
                        "scope — so naming two pools asks a harder question than naming "
                        "four, and the AUCs are not comparable across different --targets.")
    r.add_argument("--k", default="2,4,8,16", help="reference counts to try")
    r.add_argument("--trials", type=int, default=200)
    r.add_argument("--seed", type=int, default=0)
    r.set_defaults(func=cmd_refs)

    args = ap.parse_args()
    args.func(args)
    print(f"\ntorch imported: {'yes' if 'torch' in sys.modules else 'no'}")


if __name__ == "__main__":
    main()
