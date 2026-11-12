"""Time how much of a GPU scoring check is image decoding (M10 T4).

M10's T5 would decode and preprocess the next images on the default pool while the GPU
thread runs the current one. Decision 14 builds it only if a timing on a real card shows
that decoding is a large share of a run, and no CI gate runs torch, so this is that
timing, run by hand. Plan: ``docs/dev/throughput-plan/README.md`` § T4.

Every GPU check's ``*_sync`` function does four things per image, all inside one
``run_on_gpu`` call: decode the file (``open_rgb``), preprocess it (an open_clip transform
or an HF processor), move the tensor to the card, and run the model. This script times
those four stages separately, for each check:

=================  ==============================================================
``aesthetic``      ``aesthetic_scorer.score_image_sync`` (LAION, the default)
``aesthetic_v2_5`` ``aesthetic_v2_5_scorer.score_image_v2_5_sync``
``watermark``      ``aesthetic_scorer.score_watermark_sync``
``clip``           ``aesthetic_scorer.extract_clip_embedding_sync``
``dino``           ``dino_scorer.extract_dino_embedding_sync``
``dino_layers``    ``dino_scorer.extract_dino_layer_embeddings_sync``
``nsfw``           ``nsfw_scorer.score_image_sync``
=================  ==============================================================

How it stays honest about production:

- **The app's own models.** Each model comes from ``model_manager.load_*``, so its dtype,
  autocast and device are the ones a scoring job uses, and it is unloaded when its checks
  are done.
- **The app's GPU thread.** Every per-image call goes through ``device.run_on_gpu``, so
  the CUDA handles are the ones production uses (PM-035).
- **Instrumented twins, checked against the real thing.** Each check has a twin that
  repeats the app function's steps with a timer between stages, and synchronizes the
  device after the copy, so an asynchronous transfer's cost does not land in the forward
  pass. Before timing, the first ``--parity`` images of every set run through both the
  twin and the app function with hooks on each model. The tensor into each model, the
  tensor out, whether autocast was on, and the returned value must all be identical. The
  returned value alone would not do: three checks round theirs, so a twin that lost an
  autocast could still round to the same number. A mismatch aborts the run, since the
  twin would be timing something production does not do. **If an app function changes,
  its twin here must change too.**
- **The real function is timed beside its twin.** For every image the unmodified app
  function is timed on the GPU thread as well, the two taking turns at going first, and
  the report shows the twin's total next to it.
- **Warm-up.** The first ``--warmup`` images of each check are timed but left out of the
  numbers, which absorbs CUDA and cuDNN start-up.

The file cache: by default every file is read once before timing, so decode is measured
without disk I/O, as on a second run over a dataset. That only holds while the set fits
in RAM. ``--cold`` instead evicts each file from the OS cache before each timed call
(``evict_one_file``: ``posix_fadvise`` on Linux, no root needed; ``bench_scaling``'s
eviction on Windows), which shows a first run off a disk or a network share. Neither can
prove the pages left, so compare a cold run's decode with a warm one's before trusting it.

Run from the repo root with the venv active, **with nothing else using the card** (the
models load into VRAM beside whatever is resident)::

    # synthetic fixtures, generated once and reused (gitignored by default)
    python -m backend.scripts.bench_score_decode --synthetic

    # a short live run first: the parity guard must pass on every check
    python -m backend.scripts.bench_score_decode --synthetic --jpegs 50 --pngs 5 --warmup 5

    # a real dataset, read through a read-only SQLite connection
    python -m backend.scripts.bench_score_decode --db path/to/dataset_manager.db \\
        --dataset my-dataset --limit 2000 --json .claude/local/bench/t4-real.json

The synthetic sets are the sizes T1 and T3 were timed on: 1024x768 JPEGs and 4096x4096
PNGs, the PNGs written at ComfyUI's compression level. The pixels are smooth gradients
plus mild noise, so they compress like art rather than like pure noise. The generator is
seeded, so two machines build the same recipe (pixel-identical up to float rounding in
``np.sin``).

The report, per check and image set: each stage's mean, median and p90 in milliseconds
and its share of the total, images per second, the twin-against-app difference, and
**T5's speed-up**. With CPU part c = decode + preprocess and GPU part g = transfer +
forward, ``simulate_t5`` replays T5's pipeline over the measured per-image times: k
threads prepare images in order, the one GPU thread takes them in order, and at most
``T5_DEPTH`` wait ahead of it. The mean-based bound (c + g) / max(c / k, g) is reported
beside it. Both are *upper bounds*: they ignore GIL contention with the GPU thread's own
Python, and part of preprocess already runs on torch's own thread pool, which T5's
workers would share (``--torch-threads 1`` shows how much). A **full-run line** per set
treats every check a run with every box ticked performs as one run: T5's speed-up across
it, and what one decode shared across the checks could save.

``--json`` writes every number plus a machine header (GPU, VRAM, torch, CUDA, cores).
**Keep result files out of the repo** (``.claude/local/bench/`` is gitignored): the header
names the hardware it ran on.

torch is imported inside functions only, as in ``backend/ml/device.py``, so the CI smoke
test (``backend/tests/test_bench_score_decode_smoke.py``) can import this module without
torch.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES = REPO_ROOT / ".claude" / "local" / "bench" / "t4-fixtures"

STAGES = ("decode", "preprocess", "transfer", "forward")
CPU_STAGES = ("decode", "preprocess")
GPU_STAGES = ("transfer", "forward")

CHECKS = ("aesthetic", "aesthetic_v2_5", "watermark", "clip", "dino", "dino_layers", "nsfw")

# The GPU checks a scoring run with every box ticked performs, one full pass each
# (`routers/quality.py::_score_checks`). The aesthetic check runs one producer, so V2.5 is
# not in it; the technical check runs on the CPU and is not timed here.
FULL_RUN = ("aesthetic", "watermark", "clip", "dino", "dino_layers", "nsfw")

# One load per model, shared by the checks that use it, in the order a run loads them.
MODEL_GROUPS = (
    ("aesthetic", ("aesthetic", "watermark", "clip")),
    ("aesthetic_v2_5", ("aesthetic_v2_5",)),
    ("dino", ("dino", "dino_layers")),
    ("nsfw", ("nsfw",)),
)

T5_WORKERS = (1, 4)
T5_DEPTH = 4  # images prepared ahead of the GPU: the plan's "a few"

# Bump when the fixture recipe changes, so an old fixture folder is rebuilt, not reused.
FIXTURE_VERSION = 1
JPEG_SIZE = (1024, 768)
PNG_SIZE = (4096, 4096)
JPEG_QUALITY = 90
PNG_COMPRESS_LEVEL = 4  # ComfyUI's SaveImage default


# --------------------------------------------------------------------------- fixtures
def _art(rng: np.random.Generator, w: int, h: int) -> np.ndarray:
    """Smooth colour gradients plus mild noise: compresses like a picture, not like static."""
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    channels = []
    for _ in range(3):
        fx, fy = rng.uniform(0.5, 4.0, 2).astype(np.float32)
        phase = np.float32(rng.uniform(0.0, 2 * np.pi))
        channels.append(127.5 + 100.0 * np.sin(2 * np.pi * (fx * x + fy * y) + phase))
    pixels = np.stack(channels, axis=-1)
    pixels += rng.standard_normal(pixels.shape, dtype=np.float32) * 6.0
    return np.clip(pixels, 0, 255).astype(np.uint8)


def make_fixtures(
    root: Path,
    jpegs: int,
    pngs: int,
    seed: int = 0,
    jpeg_size: tuple[int, int] = JPEG_SIZE,
    png_size: tuple[int, int] = PNG_SIZE,
) -> dict[str, list[Path]]:
    """Build (or reuse) the synthetic sets under `root`. Returns {set name: paths}.

    A `manifest.json` records the recipe; a folder whose manifest matches is reused as it
    is, anything else is rebuilt. Only the `jpeg/` and `png/` folders this writes are ever
    cleared.
    """
    want = {
        "version": FIXTURE_VERSION, "seed": seed, "jpegs": jpegs, "pngs": pngs,
        "jpeg_size": list(jpeg_size), "png_size": list(png_size),
    }
    jpeg_dir, png_dir = root / "jpeg", root / "png"
    jpeg_paths = [jpeg_dir / f"img_{i:05d}.jpg" for i in range(jpegs)]
    png_paths = [png_dir / f"img_{i:05d}.png" for i in range(pngs)]
    manifest = root / "manifest.json"

    have = None
    if manifest.is_file():
        try:
            have = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            have = None
    if have == want and all(p.is_file() for p in jpeg_paths + png_paths):
        return _fixture_sets(jpeg_paths, png_paths, jpeg_size, png_size)
    # Rebuilding clears jpeg/ and png/, so never in a folder this did not build.
    if have is None and root.is_dir() and any(root.iterdir()):
        raise SystemExit(
            f"{root} is not empty and has no fixture manifest; refusing to clear it. "
            "Point --synthetic at an empty or new folder."
        )

    for d in (jpeg_dir, png_dir):
        if d.is_dir():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    manifest.unlink(missing_ok=True)

    for kind, paths, (w, h) in ((0, jpeg_paths, jpeg_size), (1, png_paths, png_size)):
        for i, path in enumerate(paths):
            rng = np.random.default_rng([seed, kind, i])
            with Image.fromarray(_art(rng, w, h), "RGB") as img:
                if kind == 0:
                    img.save(path, "JPEG", quality=JPEG_QUALITY)
                else:
                    img.save(path, "PNG", compress_level=PNG_COMPRESS_LEVEL)
    manifest.write_text(json.dumps(want, indent=2), encoding="utf-8")
    return _fixture_sets(jpeg_paths, png_paths, jpeg_size, png_size)


def _fixture_sets(jpeg_paths, png_paths, jpeg_size, png_size) -> dict[str, list[Path]]:
    sets = {}
    if jpeg_paths:
        sets[f"jpeg-{jpeg_size[0]}x{jpeg_size[1]}"] = jpeg_paths
    if png_paths:
        sets[f"png-{png_size[0]}x{png_size[1]}"] = png_paths
    return sets


# --------------------------------------------------------------------------- read-only DB
def ro_connect(db_path: Path) -> sqlite3.Connection:
    """A connection that cannot write: SQLite refuses every write on a `mode=ro` URI."""
    return sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)


def load_dataset_paths(
    db_path: Path, dataset: str, limit: int | None = None,
) -> tuple[str, list[Path], int]:
    """(dataset name, image paths that exist, count of rows whose file is missing).

    Matched by id or name, ordered by filename so two runs read the same images. Opened
    read-only through stdlib `sqlite3`, as `dino_embed_offline.load_rows` does: no
    SQLAlchemy session and no engine, so there is no write path to get wrong.
    """
    if not Path(db_path).is_file():
        raise SystemExit(f"DB not found: {db_path}")
    conn = ro_connect(db_path)
    try:
        ds = conn.execute(
            "SELECT id, name FROM datasets WHERE id = ? OR name = ?", (dataset, dataset)
        ).fetchone()
        if ds is None:
            names = [r[0] for r in conn.execute("SELECT name FROM datasets ORDER BY name")]
            raise SystemExit(
                f"No dataset matching {dataset!r}. Available: {', '.join(names) or '(none)'}"
            )
        sql = "SELECT file_path FROM images WHERE dataset_id = ? ORDER BY filename"
        params: tuple = (ds[0],)
        if limit:
            sql += " LIMIT ?"
            params += (limit,)
        stored = [r[0] for r in conn.execute(sql, params)]
    finally:
        conn.close()
    paths = [Path(p) for p in stored if p and Path(p).is_file()]
    return ds[1], paths, len(stored) - len(paths)


# --------------------------------------------------------------------------- statistics
def summarize(samples: list[dict[str, float]], e2e: list[float] | None = None) -> dict:
    """Summarize per-image stage timings (seconds) into milliseconds and shares.

    Shares are each stage's part of the summed total, which is what decides throughput.
    T5's ceiling is (c + g) / max(c / k, g) for each k in `T5_WORKERS`, with c and g the
    mean CPU and GPU parts. `e2e` is the unmodified app function's time per image.
    """
    if not samples:
        return {"n": 0}
    ms = {s: np.array([x[s] for x in samples], dtype=np.float64) * 1000.0 for s in STAGES}
    totals = sum(ms[s] for s in STAGES)
    total_sum = float(totals.sum())
    out: dict = {
        "n": len(samples),
        "stages": {
            s: {
                "mean_ms": float(ms[s].mean()),
                "median_ms": float(np.median(ms[s])),
                "p90_ms": float(np.percentile(ms[s], 90)),
                "share": float(ms[s].sum() / total_sum) if total_sum else 0.0,
            }
            for s in STAGES
        },
        "total": {
            "mean_ms": float(totals.mean()),
            "median_ms": float(np.median(totals)),
            "p90_ms": float(np.percentile(totals, 90)),
        },
        "images_per_s": len(samples) / (total_sum / 1000.0) if total_sum else 0.0,
    }
    c = sum(float(ms[s].mean()) for s in CPU_STAGES)
    g = sum(float(ms[s].mean()) for s in GPU_STAGES)
    out["cpu_ms"], out["gpu_ms"] = c, g
    out["t5_ceiling"] = {str(k): t5_ceiling(c, g, k) for k in T5_WORKERS}
    cs = (ms["decode"] + ms["preprocess"]).tolist()
    gs = (ms["transfer"] + ms["forward"]).tolist()
    out["t5_sim"] = {str(k): simulate_t5(cs, gs, k, T5_DEPTH) for k in T5_WORKERS}
    if e2e:
        e2e_mean = float(np.mean(e2e)) * 1000.0
        out["e2e_mean_ms"] = e2e_mean
        out["overhead"] = out["total"]["mean_ms"] / e2e_mean - 1.0 if e2e_mean else 0.0
    return out


def t5_ceiling(cpu_ms: float, gpu_ms: float, workers: int) -> float:
    """The best speed-up overlap could give: serial cost over the slower of the two halves.

    Exact for an unbounded queue and identical images; `simulate_t5` is the tighter figure.
    """
    bound = max(cpu_ms / workers, gpu_ms)
    return (cpu_ms + gpu_ms) / bound if bound else 1.0


def simulate_t5(cpu_ms: list[float], gpu_ms: list[float], workers: int, depth: int) -> float:
    """Speed-up of T5's pipeline replayed over the measured per-image times.

    `workers` threads prepare images in order (the CPU half); the one GPU thread takes
    them in order (the GPU half). Image i cannot start its CPU half until image i - depth
    has reached the GPU, which is the bounded queue the plan asks for. So a slow 4K PNG
    among small JPEGs stalls the pipe here as it would for real, where the mean-based
    `t5_ceiling` averages it away. Still an upper bound: each CPU half is taken at its
    serial cost, with no GIL contention and no cores shared with torch's own thread pool.
    """
    import heapq

    n = len(cpu_ms)
    if n == 0:
        return 1.0
    free = [0.0] * workers  # when each worker is next idle
    heapq.heapify(free)
    gpu_start = [0.0] * n
    gpu_end = 0.0
    for i in range(n):
        worker_free = heapq.heappop(free)
        start = max(worker_free, gpu_start[i - depth] if i >= depth else 0.0)
        ready = start + cpu_ms[i]
        heapq.heappush(free, ready)
        gpu_start[i] = max(ready, gpu_end)
        gpu_end = gpu_start[i] + gpu_ms[i]
    serial = sum(cpu_ms) + sum(gpu_ms)
    return serial / gpu_end if gpu_end else 1.0


def full_run(by_check: dict[str, dict]) -> dict | None:
    """Every `FULL_RUN` check measured for one image set, as one scoring run.

    Each check is its own pass and decodes every file again. Two figures follow:

    - **T5 across the run**: each check's serial time over its simulated pipelined time,
      summed, since the checks run one after another. Averaging the per-check speed-ups
      would weight a fast check like a slow one.
    - **One shared decode**: what decoding once instead of len(checks) times would save,
      as a part of the run. A lower bound for an image-major rewrite: it counts decode
      only, not the technical check's own `cv2` decode, not the open_clip tensor three
      checks build alike, and not a forward pass two checks repeat. Such a rewrite also
      needs every model resident at once, which M9 stage 3 chose against.
    """
    checks = [c for c in FULL_RUN if by_check.get(c, {}).get("n")]
    if not checks:
        return None
    decodes = [by_check[c]["stages"]["decode"]["mean_ms"] for c in checks]
    totals = [by_check[c]["total"]["mean_ms"] for c in checks]
    total = sum(totals)
    saving = sum(decodes) - float(np.mean(decodes))
    t5 = {}
    for k in T5_WORKERS:
        piped = sum(t / by_check[c]["t5_sim"][str(k)] for c, t in zip(checks, totals))
        t5[str(k)] = total / piped if piped else 1.0
    return {
        "checks": checks,
        "decode_ms_per_image": sum(decodes),
        "total_ms_per_image": total,
        "decode_share": sum(decodes) / total if total else 0.0,
        "t5_sim": t5,
        "shared_saving_ms": saving,
        "shared_saving_share": saving / total if total else 0.0,
    }


# --------------------------------------------------------------------------- GPU side
def _sync(dev: str) -> None:
    import torch

    if dev == "cuda":
        torch.cuda.synchronize()
    elif dev == "mps":
        torch.mps.synchronize()


# Each twin repeats its app function's steps exactly and returns (output, stage times).
# The one reordering is `img.close()`, moved from after the copy to before it so the
# close is charged to preprocessing; it changes no pixel the model sees.
def _twin_aesthetic(path: str, handle: dict) -> tuple:
    import torch

    from backend.ml import device as _device
    from backend.ml.image_utils import open_rgb

    dev = _device.get_device()
    t0 = time.perf_counter()
    img = open_rgb(path)
    t1 = time.perf_counter()
    cpu = handle["preprocess"](img).unsqueeze(0)
    img.close()
    t2 = time.perf_counter()
    tensor = cpu.to(dev)
    _sync(dev)
    t3 = time.perf_counter()
    with torch.no_grad(), _device.autocast_ctx():
        features = handle["clip"].encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
        score = handle["mlp"](features.float()).item()
    out = round(max(1.0, min(10.0, score)), 3)
    t4 = time.perf_counter()
    return out, _stages(t0, t1, t2, t3, t4)


def _twin_aesthetic_v2_5(path: str, entry) -> tuple:
    import torch

    from backend.ml import device as _device
    from backend.ml.image_utils import open_rgb

    dev = _device.get_device()
    t0 = time.perf_counter()
    img = open_rgb(path)
    t1 = time.perf_counter()
    pixel_values = entry.processor(images=img, return_tensors="pt").pixel_values
    img.close()
    t2 = time.perf_counter()
    pixel_values = pixel_values.to(dev, dtype=next(entry.model.parameters()).dtype)
    _sync(dev)
    t3 = time.perf_counter()
    with torch.no_grad():
        score = entry.model(pixel_values).logits.squeeze().float().item()
    out = round(max(1.0, min(10.0, score)), 3)
    t4 = time.perf_counter()
    return out, _stages(t0, t1, t2, t3, t4)


def _twin_watermark(path: str, handle: dict, text_features, threshold: float = 0.6) -> tuple:
    import torch

    from backend.ml import device as _device
    from backend.ml.image_utils import open_rgb

    dev = _device.get_device()
    t0 = time.perf_counter()
    img = open_rgb(path)
    t1 = time.perf_counter()
    cpu = handle["preprocess"](img).unsqueeze(0)
    img.close()
    t2 = time.perf_counter()
    tensor = cpu.to(dev)
    _sync(dev)
    t3 = time.perf_counter()
    with torch.no_grad(), _device.autocast_ctx():
        img_feats = handle["clip"].encode_image(tensor)
        img_feats = img_feats / img_feats.norm(dim=-1, keepdim=True)
        logits = (img_feats @ text_features.T) * 100.0
        probs = logits.softmax(dim=-1)[0]
    score = float(probs[0].item())
    out = {"watermark_score": round(score, 4), "has_watermark": score >= threshold}
    t4 = time.perf_counter()
    return out, _stages(t0, t1, t2, t3, t4)


def _twin_clip(path: str, handle: dict) -> tuple:
    import torch

    from backend.ml import device as _device
    from backend.ml.image_utils import open_rgb

    dev = _device.get_device()
    t0 = time.perf_counter()
    img = open_rgb(path)
    t1 = time.perf_counter()
    cpu = handle["preprocess"](img).unsqueeze(0)
    img.close()
    t2 = time.perf_counter()
    tensor = cpu.to(dev)
    _sync(dev)
    t3 = time.perf_counter()
    with torch.no_grad():
        feats = handle["clip"].encode_image(tensor)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    out = feats[0].cpu().numpy().astype(np.float16).tobytes()
    t4 = time.perf_counter()
    return out, _stages(t0, t1, t2, t3, t4)


def _twin_dino(path: str, entry) -> tuple:
    import torch

    from backend.ml import device as _device
    from backend.ml.image_utils import open_rgb

    dev = _device.get_device()
    t0 = time.perf_counter()
    img = open_rgb(path)
    t1 = time.perf_counter()
    inputs = entry.processor(images=img, return_tensors="pt")
    img.close()
    t2 = time.perf_counter()
    inputs = {k: v.to(dev) for k, v in inputs.items()}
    _sync(dev)
    t3 = time.perf_counter()
    with torch.no_grad():
        outputs = entry.model(**inputs)
        cls_token = outputs.last_hidden_state[:, 0, :]
        cls_token = cls_token / cls_token.norm(dim=-1, keepdim=True)
    out = cls_token[0].cpu().numpy().astype(np.float16).tobytes()
    t4 = time.perf_counter()
    return out, _stages(t0, t1, t2, t3, t4)


def _twin_dino_layers(path: str, entry) -> tuple:
    import torch

    from backend.ml import device as _device
    from backend.ml.image_utils import open_rgb

    dev = _device.get_device()
    t0 = time.perf_counter()
    img = open_rgb(path)
    t1 = time.perf_counter()
    inputs = entry.processor(images=img, return_tensors="pt")
    img.close()
    t2 = time.perf_counter()
    inputs = {k: v.to(dev) for k, v in inputs.items()}
    _sync(dev)
    t3 = time.perf_counter()
    with torch.no_grad():
        outputs = entry.model(**inputs, output_hidden_states=True)
        layers = []
        for i in range(1, 13):
            cls = outputs.hidden_states[i][:, 0, :]
            cls = cls / cls.norm(dim=-1, keepdim=True)
            layers.append(cls[0].cpu().numpy().astype(np.float16))
    out = np.stack(layers).tobytes()
    t4 = time.perf_counter()
    return out, _stages(t0, t1, t2, t3, t4)


def _twin_nsfw(path: str, handle: dict) -> tuple:
    import torch

    from backend.ml import device as _device
    from backend.ml.image_utils import open_rgb

    dev = _device.get_device()
    t0 = time.perf_counter()
    img = open_rgb(path)
    t1 = time.perf_counter()
    inputs = handle["processor"](images=img, return_tensors="pt")
    img.close()
    t2 = time.perf_counter()
    inputs = {k: v.to(dev) for k, v in inputs.items()}
    _sync(dev)
    t3 = time.perf_counter()
    with torch.no_grad(), _device.autocast_ctx():
        logits = handle["model"](**inputs).logits
        probs = logits.softmax(dim=-1)[0]
    out = round(float(probs[handle["nsfw_idx"]].item()), 4)
    t4 = time.perf_counter()
    return out, _stages(t0, t1, t2, t3, t4)


def _stages(t0: float, t1: float, t2: float, t3: float, t4: float) -> dict[str, float]:
    return {"decode": t1 - t0, "preprocess": t2 - t1, "transfer": t3 - t2, "forward": t4 - t3}


def _app_function(check: str):
    """The production `*_sync` function for `check`, imported only when it runs."""
    if check == "aesthetic":
        from backend.ml.aesthetic_scorer import score_image_sync
        return score_image_sync
    if check == "aesthetic_v2_5":
        from backend.ml.aesthetic_v2_5_scorer import score_image_v2_5_sync
        return score_image_v2_5_sync
    if check == "watermark":
        from backend.ml.aesthetic_scorer import score_watermark_sync
        return score_watermark_sync
    if check == "clip":
        from backend.ml.aesthetic_scorer import extract_clip_embedding_sync
        return extract_clip_embedding_sync
    if check == "dino":
        from backend.ml.dino_scorer import extract_dino_embedding_sync
        return extract_dino_embedding_sync
    if check == "dino_layers":
        from backend.ml.dino_scorer import extract_dino_layer_embeddings_sync
        return extract_dino_layer_embeddings_sync
    if check == "nsfw":
        from backend.ml.nsfw_scorer import score_image_sync
        return score_image_sync
    raise ValueError(check)


TWINS = {
    "aesthetic": _twin_aesthetic,
    "aesthetic_v2_5": _twin_aesthetic_v2_5,
    "watermark": _twin_watermark,
    "clip": _twin_clip,
    "dino": _twin_dino,
    "dino_layers": _twin_dino_layers,
    "nsfw": _twin_nsfw,
}


def _timed(fn, *args):
    """Run on the GPU thread: time the call itself, not the executor hand-off."""
    t = time.perf_counter()
    out = fn(*args)
    return out, time.perf_counter() - t


def _parity_modules(check: str, handle) -> list:
    """The modules whose inputs and outputs the parity guard compares, per check.

    The returned values alone are weak evidence: three checks round theirs, so a twin
    that lost an autocast could still round to the same number. The tensors going into
    and out of each model, and whether autocast was on, are not rounded.
    """
    if check in ("aesthetic", "watermark", "clip"):
        clip = handle["clip"]
        mods = [getattr(clip, "visual", clip)]
        return mods + [handle["mlp"]] if check == "aesthetic" else mods
    if check == "nsfw":
        return [handle["model"]]
    return [handle.model]  # aesthetic_v2_5, dino, dino_layers


def _first_tensor(obj):
    import torch

    if isinstance(obj, torch.Tensor):
        return obj
    items = obj.values() if isinstance(obj, dict) else obj if isinstance(obj, (list, tuple)) else ()
    for v in items:
        t = _first_tensor(v)
        if t is not None:
            return t
    return None


def _captured(fn, args: tuple, modules: list) -> tuple:
    """Run on the GPU thread: call `fn` and record each hooked module's input and output.

    Returns (fn's output, records), each record (module index, autocast on, input, output)
    with the tensors copied to the CPU.
    """
    import torch

    from backend.ml import device as _device

    dev = _device.get_device()
    records: list = []
    pending: dict = {}

    def _autocast_on() -> tuple:
        # The app's device and the CPU both: `autocast_ctx` is CPU autocast on a CPU box.
        try:
            return tuple(bool(torch.is_autocast_enabled(d)) for d in sorted({dev, "cpu"}))
        except TypeError:  # an older torch takes no device type
            return (bool(torch.is_autocast_enabled()),)

    def pre(idx):
        def hook(_m, a, kw):
            t = _first_tensor((list(a), kw))
            pending[idx] = (_autocast_on(), None if t is None else t.detach().cpu().clone())
        return hook

    def post(idx):
        def hook(_m, _a, _kw, output):
            t = _first_tensor(output)
            autocast, tin = pending.pop(idx, (None, None))
            records.append((idx, autocast, tin, None if t is None else t.detach().cpu().clone()))
        return hook

    handles = []
    try:
        for i, m in enumerate(modules):
            handles.append(m.register_forward_pre_hook(pre(i), with_kwargs=True))
            handles.append(m.register_forward_hook(post(i), with_kwargs=True))
        out = fn(*args)
    finally:
        for h in handles:
            h.remove()
    return out, records


def same_records(a: list, b: list) -> tuple[bool, str]:
    """Bit-for-bit equality of two `_captured` record lists."""
    import torch

    if len(a) != len(b):
        return False, f"{len(a)} model calls against {len(b)}"
    for (ia, aca, ina, outa), (ib, acb, inb, outb) in zip(a, b):
        if ia != ib:
            return False, "the models were called in a different order"
        if aca != acb:
            return False, f"model {ia}: autocast state {aca} against {acb}"
        for what, x, y in (("input", ina, inb), ("output", outa, outb)):
            if (x is None) != (y is None):
                return False, f"model {ia} {what}: a tensor against none"
            if x is None:
                continue
            if x.dtype != y.dtype or x.shape != y.shape:
                return False, f"model {ia} {what}: {x.dtype}{tuple(x.shape)} against {y.dtype}{tuple(y.shape)}"
            if not torch.equal(x, y):
                diff = float((x.float() - y.float()).abs().max())
                return False, f"model {ia} {what} differs, max abs diff {diff:.3g}"
    return True, ""


def same_output(a, b) -> tuple[bool, str]:
    """Exact equality, with a readable difference when it fails."""
    if a == b:
        return True, ""
    if isinstance(a, bytes) and isinstance(b, bytes) and len(a) == len(b):
        x = np.frombuffer(a, dtype=np.float16).astype(np.float32)
        y = np.frombuffer(b, dtype=np.float16).astype(np.float32)
        return False, f"embedding differs, max abs diff {float(np.abs(x - y).max()):.3g}"
    return False, f"{a!r} != {b!r}"


# --------------------------------------------------------------------------- the run
async def _load(model_id: str):
    from backend.ml.model_manager import model_manager

    loader = {
        "aesthetic": model_manager.load_aesthetic,
        "aesthetic_v2_5": model_manager.load_aesthetic_v2_5,
        "dino": model_manager.load_dino,
        "nsfw": model_manager.load_nsfw,
    }[model_id]
    return await loader()


def _handle(check: str, entry):
    """What the router hands each check's batch function (`routers/quality.py::_run`)."""
    if check in ("aesthetic", "watermark", "clip", "nsfw"):
        return entry.model
    return entry  # aesthetic_v2_5, dino and dino_layers take the plain ModelEntry


async def _measure(check: str, args_for, paths: list[Path], opts, evictions) -> dict:
    from backend.ml import device as _device

    app_fn, twin = _app_function(check), TWINS[check]
    modules = _parity_modules(check, args_for("")[1])
    failed = 0

    # Parity: the twin must hand each model the same tensor, under the same autocast, get
    # the same tensor back, and return the same value as the app function. An unreadable
    # file is skipped here as it is below, and the next image takes its place.
    checked = 0
    for path in paths:
        if checked >= opts.parity:
            break
        p = str(path)
        try:
            a, rec_a = await _device.run_on_gpu(_captured, app_fn, args_for(p), modules)
            (b, _), rec_b = await _device.run_on_gpu(_captured, twin, args_for(p), modules)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"    {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        ok, why = same_records(rec_a, rec_b)
        if ok:
            ok, why = same_output(a, b)
        if not ok:
            _, rec_c = await _device.run_on_gpu(_captured, app_fn, args_for(p), modules)
            if not same_records(rec_a, rec_c)[0]:
                why += " (the app does not even match itself: a nondeterministic kernel)"
            raise SystemExit(
                f"PARITY FAILED for {check} on {path.name}: {why}. Fix the twin, or the "
                f"cause above, before trusting any timing."
            )
        checked += 1

    samples: list[dict[str, float]] = []
    e2e: list[float] = []
    warmup = min(opts.warmup, max(len(paths) - 1, 0))
    for i, path in enumerate(paths):
        p = str(path)
        # Alternate which goes first, so neither always finds the file and the allocator
        # warmed by the other: production's previous call was a different image.
        try:
            for which in ((0, 1) if i % 2 == 0 else (1, 0)):
                if evictions is not None:
                    evictions.evict(path)
                if which == 0:
                    _, app_s = await _device.run_on_gpu(_timed, app_fn, *args_for(p))
                else:
                    _, stages = await _device.run_on_gpu(twin, *args_for(p))
        except Exception as exc:  # noqa: BLE001 — a bad file is counted, as the scorers do
            failed += 1
            print(f"    {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if i >= warmup:
            samples.append(stages)
            e2e.append(app_s)
    out = summarize(samples, e2e)
    out["parity_images"] = checked
    out["failed"] = failed
    out["warmup"] = warmup
    return out


def evict_one_file(path: Path) -> bool:
    """Drop one file's pages from the OS cache. True if it was attempted.

    Linux: `posix_fadvise(DONTNEED)` on that file alone, with no root, rather than
    `bench_scaling`'s `drop_caches`, which needs root and empties the whole machine's
    cache (a production install's included) twice per image. Elsewhere it is
    `bench_scaling.evict_file_cache`. Neither proves the pages left: a network share
    may cache on its own, and on Windows a successful open is all that is checked. So
    compare a cold run's decode with a warm one's before trusting it.
    """
    if hasattr(os, "posix_fadvise"):
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return False
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)
    from backend.scripts.bench_scaling import evict_file_cache

    return evict_file_cache(path)


class _FileEvictions:
    """Per-file eviction, counting the ones that did not happen."""

    def __init__(self) -> None:
        self.tried = 0
        self.failed = 0

    def evict(self, path: Path) -> None:
        self.tried += 1
        if not evict_one_file(path):
            self.failed += 1


def _machine() -> dict:
    import torch

    from backend.ml import device as _device

    info = {
        "device": _device.get_device(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "logical_cores": os.cpu_count(),
        "torch_threads": torch.get_num_threads(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    if info["device"] == "cuda":
        props = torch.cuda.get_device_properties(0)
        info["gpu"] = props.name
        info["vram_gb"] = round(props.total_memory / 2**30, 1)
    return info


def _vram() -> dict:
    """Torch's own reserve, and the whole card's use (which counts other processes too)."""
    import torch

    from backend.ml import device as _device

    out = {"reserved_mb": _device.memory_reserved_mb()}
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        out["card_used_mb"] = round((total - free) / 2**20)
    return out


async def run(opts) -> dict:
    from backend.ml import device as _device
    from backend.ml.model_manager import model_manager

    sets: dict[str, list[Path]] = {}
    if opts.synthetic:
        print(f"Fixtures: {opts.synthetic} (building any that are missing)...")
        sets.update(make_fixtures(Path(opts.synthetic), opts.jpegs, opts.pngs, opts.seed))
    if opts.dataset:
        name, paths, missing = load_dataset_paths(Path(opts.db), opts.dataset, opts.limit)
        if missing:
            print(f"Dataset {name}: {missing} rows skipped, their file is missing.")
        if paths:
            sets[f"dataset:{name}"] = paths
    sets = {k: v for k, v in sets.items() if v}
    if not sets:
        raise SystemExit("No images to time.")

    evictions = None
    if opts.cold:
        probe = next(iter(sets.values()))[0]
        if not evict_one_file(probe):
            raise SystemExit("--cold: this platform cannot evict a file from the OS cache.")
        evictions = _FileEvictions()
    else:
        print("Warming the file cache (one read of every file)...")
        for paths in sets.values():
            for p in paths:
                p.read_bytes()

    if opts.torch_threads:
        import torch

        torch.set_num_threads(opts.torch_threads)  # process-wide, so the GPU thread's too
    machine = await _device.run_on_gpu(_machine)
    vram = {"start": await _device.run_on_gpu(_vram)}
    print("Machine:", ", ".join(f"{k}={v}" for k, v in machine.items()))
    print("Sets:", ", ".join(f"{k} ({len(v)})" for k, v in sets.items()))

    results: dict[str, dict[str, dict]] = {name: {} for name in sets}
    for model_id, group in MODEL_GROUPS:
        wanted = [c for c in group if c in opts.checks]
        if not wanted:
            continue
        print(f"\nLoading {model_id}...")
        entry = await _load(model_id)
        try:
            text_features = None
            if "watermark" in wanted:
                from backend.ml.aesthetic_scorer import _precompute_watermark_text_features

                text_features = await _device.run_on_gpu(
                    _precompute_watermark_text_features, entry.model
                )
            for check in wanted:
                handle = _handle(check, entry)
                if check == "watermark":
                    def args_for(p, h=handle, t=text_features):
                        return (p, h, t)
                else:
                    def args_for(p, h=handle):
                        return (p, h)
                for set_name, paths in sets.items():
                    print(f"  {check} on {set_name}...", flush=True)
                    results[set_name][check] = await _measure(
                        check, args_for, paths, opts, evictions
                    )
        finally:
            del entry, text_features
            await model_manager.unload(model_id)
        vram[f"after_{model_id}"] = await _device.run_on_gpu(_vram)

    report = {
        "machine": machine,
        "options": {
            "cold": bool(opts.cold), "warmup": opts.warmup, "parity": opts.parity,
            "torch_threads": opts.torch_threads, "t5_depth": T5_DEPTH,
            "checks": list(opts.checks), "sets": {k: len(v) for k, v in sets.items()},
        },
        "results": results,
        "full_run": {name: full_run(by_check) for name, by_check in results.items()},
        "vram": vram,
    }
    if evictions is not None:
        report["evictions"] = {"tried": evictions.tried, "failed": evictions.failed}
    return report


# --------------------------------------------------------------------------- output
def format_report(report: dict) -> str:
    lines = []
    head = (f"{'check':<15}{'n':>6}{'decode':>9}{'prep':>8}{'xfer':>8}{'fwd':>8}"
            f"{'total':>9}{'app':>9}{'ovh':>7}{'img/s':>8}{'cpu%':>6}"
            + "".join(f"{f'T5 k={k}':>9}" for k in T5_WORKERS)
            + f"{'bound':>8}")
    for set_name, by_check in report["results"].items():
        lines.append(f"\n== {set_name}  (mean ms per image; cpu% = decode + preprocess share)")
        lines.append(head)
        for check, r in by_check.items():
            if not r.get("n"):
                lines.append(f"{check:<15}{0:>6}  (no images measured, {r.get('failed', 0)} failed)")
                continue
            st = r["stages"]
            cpu_share = st["decode"]["share"] + st["preprocess"]["share"]
            lines.append(
                f"{check:<15}{r['n']:>6}"
                f"{st['decode']['mean_ms']:>9.2f}{st['preprocess']['mean_ms']:>8.2f}"
                f"{st['transfer']['mean_ms']:>8.2f}{st['forward']['mean_ms']:>8.2f}"
                f"{r['total']['mean_ms']:>9.2f}{r.get('e2e_mean_ms', 0.0):>9.2f}"
                f"{r.get('overhead', 0.0) * 100:>6.1f}%{r['images_per_s']:>8.1f}"
                f"{cpu_share * 100:>5.0f}%"
                + "".join(f"{r['t5_sim'][str(k)]:>8.2f}x" for k in T5_WORKERS)
                + f"{r['t5_ceiling'][str(T5_WORKERS[-1])]:>7.2f}x"
                + (f"   ({r['failed']} failed)" if r.get("failed") else "")
            )
        fr = report["full_run"].get(set_name)
        if fr:
            lines.append(
                f"full run ({len(fr['checks'])} checks): {fr['decode_ms_per_image']:.1f} ms of "
                f"decode in {fr['total_ms_per_image']:.1f} ms per image "
                f"({fr['decode_share'] * 100:.0f}%); T5 "
                + ", ".join(f"k={k} {fr['t5_sim'][str(k)]:.2f}x" for k in T5_WORKERS)
                + f"; one shared decode would save {fr['shared_saving_ms']:.1f} ms "
                f"({fr['shared_saving_share'] * 100:.0f}%)"
            )
    lines.append(
        f"\nT5 k=N: the pipeline replayed over these per-image times, N prepare threads and "
        f"{T5_DEPTH} images queued ahead.\n'bound' is the mean-based ceiling for "
        f"k={T5_WORKERS[-1]}. All are upper bounds: no GIL contention, no cores shared with "
        f"torch's own threads.\n'ovh' is twin minus app, mostly noise: the twin's own cost "
        f"is four timer reads and a sync."
    )
    if "evictions" in report:
        ev = report["evictions"]
        lines.append(f"Cold cache: {ev['tried']} evictions, {ev['failed']} failed "
                     "(a failed one was timed warm).")
    lines.append("VRAM: " + "; ".join(
        f"{k} " + ", ".join(f"{kk}={vv}" for kk, vv in v.items())
        for k, v in report["vram"].items()
    ))
    return "\n".join(lines)


def _checks(value: str) -> tuple[str, ...]:
    picked = tuple(c.strip() for c in value.split(",") if c.strip())
    unknown = [c for c in picked if c not in CHECKS]
    if unknown or not picked:
        raise argparse.ArgumentTypeError(
            f"unknown check(s) {unknown or value!r}; choose from {', '.join(CHECKS)}"
        )
    return picked


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backend.scripts.bench_score_decode",
        description="Time decode, preprocess, transfer and forward for each GPU scoring check.",
    )
    p.add_argument("--synthetic", nargs="?", const=str(DEFAULT_FIXTURES), default=None,
                   metavar="DIR", help=f"time generated fixtures (default dir {DEFAULT_FIXTURES})")
    p.add_argument("--jpegs", type=int, default=2000, help="synthetic JPEGs at 1024x768")
    p.add_argument("--pngs", type=int, default=100, help="synthetic PNGs at 4096x4096")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--db", default=str(REPO_ROOT / "dataset_manager.db"),
                   help="the database to read a --dataset from, opened read-only")
    p.add_argument("--dataset", help="also time a real dataset, by name or id")
    p.add_argument("--limit", type=int, default=None, help="at most this many of its images")
    p.add_argument("--checks", type=_checks, default=CHECKS,
                   help=f"comma-separated, from {','.join(CHECKS)} (default all)")
    p.add_argument("--warmup", type=int, default=20, help="images left out at each check's start")
    p.add_argument("--parity", type=int, default=8, help="images checked twin-vs-app per set")
    p.add_argument("--cold", action="store_true", help="evict each file before each timed call")
    p.add_argument("--torch-threads", type=int, default=None,
                   help="torch.set_num_threads first: how much of preprocess leans on "
                        "torch's own thread pool, which T5's workers would share")
    p.add_argument("--json", help="write every number here (keep it out of the repo)")
    return p


def main(argv: list[str] | None = None) -> None:
    # As `main.py` sets it, before anything imports torch.
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    opts = build_parser().parse_args(argv)
    if not opts.synthetic and not opts.dataset:
        raise SystemExit("Nothing to time: pass --synthetic, --dataset, or both.")
    report = asyncio.run(run(opts))
    print(format_report(report))
    if opts.json:
        out = Path(opts.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
