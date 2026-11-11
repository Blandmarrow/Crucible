"""Scaling benchmark harness (M9 stage 6).

Builds a scratch SQLite database with a real install's schema, seeds it in a realistic
shape, and times the **app's own endpoints** against it, with a per-statement breakdown
and each statement's query plan. No server is started, no port is bound, no ML model is
loaded, and the real ``dataset_manager.db`` is never opened: the scratch path is checked
against it before anything is written.

Run from the repo root with the venv active:

    python -m backend.scripts.bench_scaling --profile photo --sweep 100000,250000
    python -m backend.scripts.bench_scaling --profile comfy --sweep 20000 --optimize
    python -m backend.scripts.bench_scaling --profile photo --sweep 100000 \
        --reuse-db --json .claude/local/bench/after.json --compare .claude/local/bench/before.json

What it does, per size N:

1. **Schema.** ``alembic upgrade head`` in a subprocess builds a template database once
   per head revision (``--schema alembic``, the default), because that is the file a real
   install has. A migrated database appends each later column *after* the three embedding
   blobs, and reaching those columns is most of what a Stats pass costs. ``--schema
   create_all`` follows the model's declaration order instead. On 2026-11-10 both put the
   blobs at columns 27-29 of 48, but different columns follow them: a migrated file has
   ``subfolder``, ``sort_order``, ``caption_token_count``, ``license``, ``nsfw_score``,
   ``luminance_score`` and ``aesthetic_model`` after the blobs, ``create_all`` before. The
   report lists the columns after the blobs, so the difference is visible.
2. **Seed** (``--profile photo|comfy``): 70% of rows scored and carrying all three blobs
   (1,536 + 1,536 + 18,432 bytes), 80% captioned at ~270 characters, and
   ``generation_metadata`` at ~400 bytes (photo) or ~55 KB (ComfyUI). A few subfolders,
   one of exactly 20 rows. ``--reuse-db`` keeps a seeded file and, on the next run, upgrades
   it to the current head before measuring, so a later stage's index is measured on the
   same rows (6a's numbers against 6b's).
3. **Endpoints**, through ``httpx.ASGITransport`` with ``get_db`` overridden, the way
   ``backend/tests/conftest.py::api_env`` drives them: every gallery sort at page 1 and
   60% deep, the counts, the two Stats endpoints cold and as a cached poll, lookups by id
   and thumbnails. Each case reports its first and best time, its payload, and Python's
   peak allocation (``tracemalloc``, an untimed extra call).
4. **Statements.** Every SQL statement a case issues is captured, then replayed on a raw
   ``sqlite3`` connection (fetching every row) for its own time and its
   ``EXPLAIN QUERY PLAN``. Replay times are floors: no ORM, no Pydantic, no HTTP. The
   difference from the endpoint's time is what those layers cost.
5. ``--optimize`` re-measures after ``PRAGMA optimize=0x10002`` (which runs ``ANALYZE``),
   then drops ``sqlite_stat1`` again, because the app never analyzes.
6. ``--cold`` evicts the database file from the OS cache before every request (each one
   of a batch case too) and before every replayed statement, where the platform allows
   it, and opens a fresh connection each time. Eviction is not timed, and a failed one is
   counted and reported rather than passed off as cold.

``--json`` writes every number; ``--compare`` prints the change against an earlier JSON.
Keep result files out of the repo (``.claude/local/bench/`` is gitignored): the JSON
records the machine it ran on.

The legacy benches stay available through ``--only``: ``dedup`` (``find_duplicates_sync``),
``similarity`` (load every DINO blob, then score) and ``thumb`` (a flat folder glob against a
sharded one).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import platform
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, event, insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import backend.models  # noqa: F401 — registers every table for `--schema create_all`
from backend.config import settings
from backend.database import Base
from backend.models.dataset import Dataset
from backend.models.image import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

# Bump when the seeded data changes shape, so `--reuse-db` reseeds instead of measuring
# rows the current code would not have written.
SEED_VERSION = 2

DATASET_ID = "be7c0000-0000-4000-8000-000000000001"
NEIGHBOUR_ID = "be7c0000-0000-4000-8000-000000000002"

PROFILES = {
    # generation_metadata bytes per row. 55 KB is the dev DB's ComfyUI dataset
    # (54,895 B on average, 2026-11-09); photos carry a few hundred bytes of EXIF.
    "photo": {"gen_meta_bytes": 400, "format": "JPEG", "ext": "jpg"},
    "comfy": {"gen_meta_bytes": 55_000, "format": "PNG", "ext": "png"},
}
SCORED_FRACTION = 0.70
CAPTIONED_FRACTION = 0.80
CAPTION_CHARS = 270
LICENSED_FRACTION = 0.10
EMBED_DIM = 768  # CLIP ViT-L/14 and DINOv2-base, float16 → 1,536 bytes each
DINO_LAYERS = 12  # `dino_layer_embeddings`: 12 × 768 float16 = 18,432 bytes
BLOB_POOL = 256  # blobs are reused cyclically: their bytes do not matter, their size does
# Rough on-disk bytes per row, for the disk preflight (the 2026-11-09 run: 1.75 GB for
# 100k photo rows, 1.45 GB for 20k ComfyUI rows).
ROW_OVERHEAD_BYTES = 2_500

SUBFOLDERS = [("", 0.40), ("set_a", 0.30), ("set_b", 0.20), ("set_c", 0.10)]
SMALL_SUBFOLDER = "small"
SMALL_SUBFOLDER_ROWS = 20

# The sorts with an index, as `backend/tests/test_image_sort_plan.py` pins them. Used only
# to label the report; the plan column says what SQLite actually did.
INDEXED_SORTS = frozenset({
    "aesthetic_score", "blur_score", "style_similarity_score", "created_at",
    "caption_token_count", "sort_order", "filename",
    # `ix_images_dataset_updated_at`, added for the Stats cache check (M9 stage 11).
    "updated_at",
})

ENDPOINT_GROUPS = ("gallery", "counts", "stats", "lookups")
LEGACY_GROUPS = ("dedup", "similarity", "thumb")
DEFAULT_ONLY = ",".join(ENDPOINT_GROUPS)

WORDS = (
    "a portrait of a woman standing in a field at golden hour soft light shallow depth "
    "of field cinematic film grain detailed face looking at viewer wind in hair blue sky "
    "clouds city street night neon reflections rain umbrella mountain lake forest path "
    "autumn leaves cat sitting on a windowsill vintage camera bokeh highly detailed"
).split()


# --------------------------------------------------------------------------- safety
def _real_db_path() -> Path | None:
    database = make_url(str(settings.database_url)).database
    return Path(database).resolve() if database else None


def check_scratch_path(db_path: Path) -> None:
    """Refuse to touch the app's own database, whatever path the caller passed."""
    real = _real_db_path()
    if real is not None and Path(db_path).resolve() == real:
        raise SystemExit(
            f"Refusing to run: {db_path} is the app's database ({real}). "
            "The harness only ever writes to a scratch file."
        )


# --------------------------------------------------------------------------- memory
def rss_peak_mb() -> float:
    """The process's peak resident memory in MB, on any platform."""
    try:
        import psutil

        info = psutil.Process().memory_info()
        if sys.platform == "win32":
            return info.peak_wset / 2**20
    except Exception:  # noqa: BLE001 — a missing meter is not a failed benchmark
        info = None
    try:
        import resource  # Unix only

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 2**20 if sys.platform == "darwin" else peak / 1024
    except ImportError:
        return info.rss / 2**20 if info is not None else 0.0


# --------------------------------------------------------------------------- cold cache
def evict_file_cache(path: Path) -> bool:
    """Best-effort: drop one file's pages from the OS cache. True if it was attempted.

    Windows: opening the file with FILE_FLAG_NO_BUFFERING and closing it purges its
    cached pages, with no admin rights, provided nothing else holds it open. Measured on
    2026-11-10: random 4 KB reads ran ~25x slower afterwards (sequential reads on NVMe
    could not tell the difference). Linux: needs root, for /proc/sys/vm/drop_caches.
    """
    if not path.exists():
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes as w

            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateFileW.restype = w.HANDLE
            k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID,
                                      w.DWORD, w.DWORD, w.HANDLE]
            k.CloseHandle.argtypes = [w.HANDLE]
            # GENERIC_READ, share read|write|delete, OPEN_EXISTING, FILE_FLAG_NO_BUFFERING
            h = k.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x20000000, None)
            if h is None or h == w.HANDLE(-1).value or h == -1:
                return False
            k.CloseHandle(h)
            return True
        except Exception:  # noqa: BLE001
            return False
    if sys.platform.startswith("linux"):
        try:
            os.sync()
            Path("/proc/sys/vm/drop_caches").write_text("3\n")
            return True
        except OSError:
            return False
    return False


class Evictions:
    """Counts cold-cache evictions that did not happen, so a run that silently stayed
    warm (another process holding the file, say) says so in its report."""

    def __init__(self) -> None:
        self.tried = 0
        self.failed = 0

    def evict(self, db_path: Path) -> None:
        self.tried += 1
        if not evict_file_cache(db_path):
            self.failed += 1
        evict_file_cache(Path(f"{db_path}-wal"))  # usually empty after the checkpoint


def cold_cache_supported(probe: Path) -> bool:
    if sys.platform == "win32":
        return evict_file_cache(probe)
    if sys.platform.startswith("linux"):
        return os.access("/proc/sys/vm/drop_caches", os.W_OK)
    return False


# --------------------------------------------------------------------------- schema
def alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()


def alembic_upgrade(db_path: Path) -> None:
    """`alembic upgrade head` on `db_path`, in a child process.

    `env.py` takes its URL from `settings.database_url`, which is read once when
    `backend.config` is first imported — already done in this process. So the URL goes to
    a child through `DATABASE_URL`, and this process's settings never change.
    """
    check_scratch_path(db_path)
    # The datasets folder goes to the child too, so a migration that ever touches files
    # finds the scratch tree and not the user's.
    env = dict(os.environ,
               DATABASE_URL=f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}",
               DATASETS_DIR=str((db_path.parent / "datasets").resolve()))
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"alembic upgrade head failed on {db_path}:\n{proc.stderr[-4000:]}")


def build_template(work_dir: Path, schema: str) -> Path:
    """An empty database with the chosen schema, built once and copied per size."""
    if schema == "alembic":
        template = work_dir / f"template-{alembic_head()}.db"
        if not template.exists():
            tmp = template.with_suffix(".building")
            tmp.unlink(missing_ok=True)
            print(f"building template: alembic upgrade head -> {template.name}", flush=True)
            alembic_upgrade(tmp)
            tmp.replace(template)
        return template
    template = work_dir / "template-create_all.db"
    template.unlink(missing_ok=True)
    eng = create_engine(f"sqlite:///{template}")
    Base.metadata.create_all(eng)
    eng.dispose()
    return template


def images_column_order(db_path: Path) -> list[str]:
    with contextlib.closing(sqlite3.connect(db_path)) as con:
        return [r[1] for r in con.execute("PRAGMA table_info(images)")]


# --------------------------------------------------------------------------- seeding
def _comfy_metadata(rng: random.Random, target: int) -> dict:
    """A ComfyUI-shaped prompt graph whose JSON is about `target` bytes."""
    kinds = ["KSampler", "CLIPTextEncode", "CheckpointLoaderSimple", "VAEDecode",
             "EmptyLatentImage", "LoraLoader", "SaveImage", "ControlNetApply"]
    nodes: dict[str, dict] = {}
    size = 0
    i = 1
    while size < target:
        nodes[str(i)] = {
            "class_type": rng.choice(kinds),
            "inputs": {
                "seed": rng.getrandbits(48), "steps": rng.randint(20, 40),
                "cfg": round(rng.uniform(3, 9), 1), "model": [str(max(1, i - 1)), 0],
                "text": " ".join(rng.choices(WORDS, k=60)),
            },
            "_meta": {"title": f"node {i}"},
        }
        size = len(json.dumps({"prompt": nodes}))
        i += 1
    return {"prompt": nodes}


def _photo_metadata(rng: random.Random, target: int) -> dict:
    meta = {
        "Make": rng.choice(["Canon", "Nikon", "Sony", "Fujifilm"]),
        "Model": f"Model {rng.randint(1, 99)}",
        "LensModel": f"{rng.choice([24, 35, 50, 85])}mm F{rng.choice([1.4, 1.8, 2.8])}",
        "FNumber": rng.choice([1.4, 2.0, 2.8, 4.0, 8.0]),
        "ExposureTime": f"1/{rng.choice([60, 125, 250, 500, 1000])}",
        "ISOSpeedRatings": rng.choice([100, 200, 400, 800, 1600]),
        "DateTimeOriginal": f"2023:0{rng.randint(1, 9)}:1{rng.randint(0, 9)} 12:00:00",
        "Software": "Adobe Lightroom 13.0",
    }
    pad = target - len(json.dumps(meta)) - len(', "UserComment": ""')
    meta["UserComment"] = " ".join(rng.choices(WORDS, k=200))[: max(0, pad)]
    return meta


def _caption(rng: random.Random) -> str:
    words: list[str] = []
    length = 0
    while length < CAPTION_CHARS:
        w = rng.choice(WORDS)
        words.append(w)
        length += len(w) + 2
    return ", ".join(words)[:CAPTION_CHARS]


class RowFactory:
    """Builds `images` rows in the realistic shape. Every row carries the same keys, which
    Core's executemany requires."""

    def __init__(self, profile: str, datasets_dir: Path, n: int, dup_prob: float, seed: int = 1234):
        self.rng = random.Random(seed)
        self.p = PROFILES[profile]
        self.dup_prob = dup_prob
        self.root = datasets_dir / "bench"
        nprng = np.random.default_rng(seed)

        def pool(shape):
            return [
                (lambda a: (a / np.linalg.norm(a, axis=-1, keepdims=True)).astype(np.float16).tobytes())(
                    nprng.standard_normal(shape).astype(np.float32)
                )
                for _ in range(BLOB_POOL)
            ]

        self.clip = pool((EMBED_DIM,))
        self.dino = pool((EMBED_DIM,))
        self.layers = pool((DINO_LAYERS, EMBED_DIM))
        maker = _comfy_metadata if profile == "comfy" else _photo_metadata
        self.gen_meta = [maker(self.rng, self.p["gen_meta_bytes"]) for _ in range(16)]
        self.captions = [_caption(self.rng) for _ in range(64)]
        self.small = set(self.rng.sample(range(n), min(SMALL_SUBFOLDER_ROWS, n)))
        self.bases: list[int] = []
        self.base_time = datetime(2024, 1, 1)

    def _phash(self) -> str:
        if self.bases and self.rng.random() < self.dup_prob:
            v = self.bases[self.rng.randrange(len(self.bases))]
            for _ in range(self.rng.randint(1, 3)):
                v ^= 1 << self.rng.randrange(64)
        else:
            v = self.rng.getrandbits(64)
            self.bases.append(v)
        return f"{v:016x}"

    def _subfolder(self, i: int) -> str:
        if i in self.small:
            return SMALL_SUBFOLDER
        r = self.rng.random()
        for name, weight in SUBFOLDERS:
            if r < weight:
                return name
            r -= weight
        return SUBFOLDERS[-1][0]

    def row(self, i: int, dataset_id: str) -> dict:
        rng = self.rng
        scored = rng.random() < SCORED_FRACTION
        captioned = rng.random() < CAPTIONED_FRACTION
        sub = self._subfolder(i) if dataset_id == DATASET_ID else ""
        ext = self.p["ext"]
        folder = self.root / "images" / sub if sub else self.root / "images"
        created = self.base_time + timedelta(seconds=7 * i)
        caption = self.captions[i % len(self.captions)] if captioned else ""
        k = i % BLOB_POOL

        def score(scale: float) -> float | None:
            return round(rng.random() * scale, 4) if scored else None

        return {
            "id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "dataset_id": dataset_id,
            "filename": f"img_{i:08d}.{ext}",
            "original_filename": f"orig_{i}.{ext}",
            "subfolder": sub,
            "file_path": str(folder / f"img_{i:08d}.{ext}"),
            "thumbnail_path": str(self.root / "thumbnails" / f"img_{i:08d}.webp"),
            "is_auto_named": False,
            "width": rng.choice([768, 832, 1024, 1216, 1536, 2048, 4000]),
            "height": rng.choice([768, 832, 1024, 1216, 1536, 2048, 3000]),
            "file_size_bytes": rng.randint(50_000, 8_000_000),
            "format": self.p["format"],
            "phash": self._phash(),
            "created_at": created,
            "updated_at": created + timedelta(seconds=rng.randint(0, 30 * 86400)) if scored else created,
            "nsfw_score": score(1),
            "aesthetic_score": score(10),
            "aesthetic_model": "laion" if scored else None,
            "blur_score": score(300),
            "noise_score": score(30),
            "uniformity_score": score(30),
            "watermark_score": score(1),
            "color_score": score(100),
            "saturation_score": score(1),  # mean HSV S, 0–1 (PM-020)
            "luminance_score": score(1),
            "style_similarity_score": score(1),
            "clip_embedding": self.clip[k] if scored else None,
            "dino_embedding": self.dino[k] if scored else None,
            "dino_layer_embeddings": self.layers[k] if scored else None,
            # `dino_layer_scores`, `source_meta` and `processing_history` are left out on
            # purpose: a `None` given to a JSON column through Core is stored as the
            # text 'null', and a real row has SQL NULL there.
            "quality_flags": {
                "is_blurry": rng.random() < 0.10,
                "is_noisy": rng.random() < 0.05,
                "is_uniform": rng.random() < 0.04,
                "has_watermark": rng.random() < 0.03,
                "is_nsfw": rng.random() < 0.02,
                "is_duplicate": False,
            } if scored else {},
            "generation_metadata": self.gen_meta[i % len(self.gen_meta)],
            "source_name": None,
            "source_url": None,
            "license": "cc-by-4.0" if rng.random() < LICENSED_FRACTION else None,
            "attribution": None,
            "source_video_id": None,
            "source_timestamp_ms": None,
            "source_shot_index": None,
            "scores_stale": False,
            "sort_order": None,
            # Core insert bypasses the tiktoken listener, so the count is set here.
            "caption_text": caption,
            "caption_token_count": len(caption.split()) + caption.count(",") if captioned else 0,
            "caption_style": "tags" if captioned else "",
            "captioned_by": "bench" if captioned else "",
            "captioned_at": created if captioned else None,
        }


def estimate_bytes(n: int, profile: str) -> int:
    blobs = (2 * EMBED_DIM + DINO_LAYERS * EMBED_DIM) * 2
    per_row = ROW_OVERHEAD_BYTES + SCORED_FRACTION * blobs + PROFILES[profile]["gen_meta_bytes"]
    return int(n * per_row)


def _models_ddl_hash() -> str:
    """A fingerprint of the models' tables. `--reuse-db` upgrades a migrated file to head,
    but a `create_all` file can only be rebuilt, so a model change must not match it."""
    from sqlalchemy.schema import CreateTable

    ddl = "\n".join(str(CreateTable(t).compile(dialect=create_engine("sqlite://").dialect))
                    for t in Base.metadata.sorted_tables)
    return hashlib.sha1(ddl.encode()).hexdigest()[:12]


def _meta_for(args, n: int) -> dict:
    meta = {"seed_version": SEED_VERSION, "n": n, "profile": args.profile,
            "neighbour_rows": args.neighbour_rows, "schema": args.schema,
            "dup_prob": args.dup_prob}
    if args.schema == "create_all":
        meta["models"] = _models_ddl_hash()
    return meta


def read_meta(db_path: Path) -> dict | None:
    if not db_path.exists():
        return None
    try:
        with contextlib.closing(sqlite3.connect(db_path)) as con:
            row = con.execute("SELECT value FROM bench_meta WHERE key = 'seed'").fetchone()
        return json.loads(row[0]) if row else None
    except sqlite3.Error:
        return None


def seed_db(db_path: Path, args, n: int, datasets_dir: Path) -> None:
    """Insert the main dataset's `n` rows (rowids 1..n), then any neighbour rows."""
    eng = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA synchronous=OFF")  # seeding only; reads use the app's NORMAL
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    with eng.begin() as c:
        c.exec_driver_sql("PRAGMA journal_mode=WAL")  # what init_db sets on a real install
        for ds_id, name, count in ((DATASET_ID, "bench", n), (NEIGHBOUR_ID, "neighbour", args.neighbour_rows)):
            if ds_id == NEIGHBOUR_ID and not count:
                continue
            c.execute(insert(Dataset).values(
                id=ds_id, name=name, folder_path=str(datasets_dir / name), description="",
                category="", declared_subfolders=[], image_count=count,
                captioned_count=int(count * CAPTIONED_FRACTION), total_size_bytes=0,
            ))

    factory = RowFactory(args.profile, datasets_dir, n, args.dup_prob)
    batch = 2_000 if args.profile == "comfy" else 5_000
    for ds_id, count, offset in ((DATASET_ID, n, 0), (NEIGHBOUR_ID, args.neighbour_rows, n)):
        done = 0
        while done < count:
            m = min(batch, count - done)
            rows = [factory.row(offset + done + j, ds_id) for j in range(m)]
            with eng.begin() as c:
                c.execute(insert(Image), rows)
            done += m
            if done % (batch * 10) == 0 or done == count:
                print(f"    seeded {done:,}/{count:,} ({'main' if ds_id == DATASET_ID else 'neighbour'})", flush=True)

    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE IF NOT EXISTS bench_meta (key TEXT PRIMARY KEY, value TEXT)")
        c.exec_driver_sql(
            "INSERT OR REPLACE INTO bench_meta VALUES ('seed', ?)", (json.dumps(_meta_for(args, n)),)
        )
    with eng.connect() as c:
        c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    eng.dispose()


def _db_files(db_path: Path) -> list[Path]:
    return [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]


def prepare_db(args, n: int, work_dir: Path) -> tuple[Path, Path, bool, float]:
    """The seeded database for size `n`: reused when `--reuse-db` finds a matching one."""
    name = f"bench-{args.profile}-{n}-nb{args.neighbour_rows}-{args.schema}"
    db_path = work_dir / f"{name}.db"
    datasets_dir = (work_dir / "datasets" / name).resolve()
    check_scratch_path(db_path)

    if args.reuse_db and read_meta(db_path) == _meta_for(args, n):
        if args.schema == "alembic":
            alembic_upgrade(db_path)  # picks up any migration merged since the seed
        print(f"    reusing {db_path.name}", flush=True)
        return db_path, datasets_dir, False, 0.0

    from backend.utils import InsufficientDiskSpaceError, format_bytes, require_free_space

    need = estimate_bytes(n + args.neighbour_rows, args.profile)
    print(f"    about {format_bytes(need)} of scratch disk needed", flush=True)
    try:
        require_free_space(work_dir, need)
    except InsufficientDiskSpaceError as exc:
        raise SystemExit(str(exc)) from exc

    for p in _db_files(db_path):
        p.unlink(missing_ok=True)
    shutil.rmtree(datasets_dir, ignore_errors=True)
    template = build_template(work_dir, args.schema)
    shutil.copyfile(template, db_path)
    t0 = time.perf_counter()
    seed_db(db_path, args, n, datasets_dir)
    return db_path, datasets_dir, True, time.perf_counter() - t0


# --------------------------------------------------------------------------- measuring
class Capture:
    """Records each statement the app's engine sends, with its DBAPI parameters."""

    def __init__(self) -> None:
        self.on = False
        self.stmts: list[tuple[str, tuple]] = []

    def hook(self, conn, cursor, statement, parameters, context, executemany):
        if self.on and not executemany:
            self.stmts.append((statement, tuple(parameters or ())))


def make_engine(db_path: Path, capture: Capture, cold: bool):
    kwargs = {"poolclass": NullPool} if cold else {}
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", connect_args={"check_same_thread": False}, **kwargs
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn, _rec):  # the app's per-connection pragmas (backend/database.py)
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    event.listen(engine.sync_engine, "before_cursor_execute", capture.hook)
    return engine


@contextlib.asynccontextmanager
async def app_client(Session, datasets_dir: Path):
    """The app on an in-process transport, with its DB dependency pointed at `Session`.
    No lifespan (it would open the real DB) and no job worker: every case is a read."""
    import httpx

    import backend.database as database
    import backend.workers.job_queue as job_queue_mod
    from backend.database import get_db
    from backend.main import app

    async def _override_get_db():
        async with Session() as session:
            yield session

    # Everything that can reach a session is pointed at the scratch file, as `api_env`
    # does: the request dependency, and the module-level factories a code path or a job
    # body opens for itself. A read route that opened `AsyncSessionLocal()` would
    # otherwise read the real database without a sign.
    prev_datasets_dir = settings.datasets_dir
    prev_session_local = database.AsyncSessionLocal
    prev_worker_session_local = job_queue_mod.AsyncSessionLocal
    settings.datasets_dir = datasets_dir
    database.AsyncSessionLocal = Session
    job_queue_mod.AsyncSessionLocal = Session
    app.dependency_overrides[get_db] = _override_get_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        settings.datasets_dir = prev_datasets_dir
        database.AsyncSessionLocal = prev_session_local
        job_queue_mod.AsyncSessionLocal = prev_worker_session_local


def _one_line(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _preview(params: tuple) -> str:
    s = repr(params)
    return s if len(s) <= 200 else s[:197] + "..."


def replay(db_path: Path, stmts: list[tuple[str, tuple]], reps: int,
           evictions: Evictions | None) -> list[dict]:
    """Each distinct SELECT on a raw sqlite3 connection: its plan and its best time.
    With `evictions` (a cold run), the file is evicted before every replay."""
    out: list[dict] = []
    seen: set = set()

    def connect():
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    con = connect()
    try:
        for sql, params in stmts:
            if (sql, params) in seen:
                continue
            seen.add((sql, params))
            entry = {"sql": _one_line(sql), "params": _preview(params)}
            if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
                entry.update(replay_ms=None, plan=[], temp_btree=False)
                out.append(entry)
                continue
            plan = [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, params)]
            times = []
            for _ in range(max(1, reps)):
                if evictions is not None:
                    con.close()
                    evictions.evict(db_path)
                    con = connect()
                t0 = time.perf_counter()
                con.execute(sql, params).fetchall()
                times.append((time.perf_counter() - t0) * 1000)
            entry.update(replay_ms=round(min(times), 3), plan=plan,
                         temp_btree=any("TEMP B-TREE" in step for step in plan))
            out.append(entry)
    finally:
        con.close()
    return out


def build_cases(groups: set[str], n: int, ids: list[str], thumb_ids: list[str]) -> list[dict]:
    from backend.routers.images import _ALLOWED_SORT_FIELDS

    cases: list[dict] = []
    base = {"dataset_id": DATASET_ID}
    deep_page = max(1, int(0.6 * n) // 100 + 1)

    if "gallery" in groups:
        for sort in sorted(_ALLOWED_SORT_FIELDS):
            for order in ("desc", "asc"):
                for label, page in (("p1", 1), ("deep", deep_page)):
                    params = {**base, "sort": sort, "order": order, "page": page, "limit": 100}
                    cases.append({"name": f"gallery {sort} {order} {label}", "group": "gallery",
                                  "sort": sort, "order": order, "depth": label, "page": page,
                                  "reqs": [("/api/v1/images/", params)]})
        cases.append({
            "name": f"gallery aesthetic_score desc p1 subfolder={SMALL_SUBFOLDER}",
            "group": "gallery", "sort": "aesthetic_score", "order": "desc",
            "depth": f"p1 in '{SMALL_SUBFOLDER}'", "page": 1,
            "reqs": [("/api/v1/images/", {**base, "sort": "aesthetic_score", "order": "desc",
                                          "page": 1, "limit": 100, "subfolder": SMALL_SUBFOLDER})],
        })

    if "counts" in groups:
        cases.append({"name": "count dataset", "group": "counts",
                      "reqs": [("/api/v1/images/count", base)]})
        for sub in ("set_a", SMALL_SUBFOLDER):
            cases.append({"name": f"count subfolder={sub}", "group": "counts",
                          "reqs": [("/api/v1/images/count", {**base, "subfolder": sub})]})

    if "stats" in groups:
        for ep in ("stats", "score-values"):
            for cache in ("cold", "warm"):
                label = "cached poll" if cache == "warm" else "cold"
                cases.append({"name": f"{ep} {label}", "group": "stats", "cache": cache,
                              "reqs": [(f"/api/v1/datasets/{DATASET_ID}/{ep}", None)]})

    if "lookups" in groups:
        if ids:
            cases.append({"name": f"GET /images/{{id}} x{len(ids)}", "group": "lookups",
                          "reqs": [(f"/api/v1/images/{i}", None) for i in ids]})
        if thumb_ids:
            cases.append({"name": f"thumbnail x{len(thumb_ids)}", "group": "lookups",
                          "reqs": [(f"/api/v1/images/{i}/thumbnail", None) for i in thumb_ids]})
    return cases


def sample_lookups(db_path: Path, n: int, k_ids: int, k_thumbs: int, datasets_dir: Path):
    """Ids to look up, and ids whose thumbnail gets a real 1 KB file to serve.

    In the sampled (random) order, not rowid order: `rowid IN (...)` returns rows in
    table order, and walking the table in order would flatter a cold run's readahead."""
    rng = random.Random(99)
    rowids = rng.sample(range(1, n + 1), min(n, max(k_ids, k_thumbs)))
    with contextlib.closing(sqlite3.connect(db_path)) as con:
        found: dict[int, tuple[str, str]] = {}
        for i in range(0, len(rowids), 900):
            chunk = rowids[i:i + 900]
            for rowid, image_id, thumb in con.execute(
                f"SELECT rowid, id, thumbnail_path FROM images WHERE dataset_id = ? "
                f"AND rowid IN ({','.join('?' * len(chunk))})",
                (DATASET_ID, *chunk),
            ):
                found[rowid] = (image_id, thumb)
    rows = [found[r] for r in rowids if r in found]
    ids = [r[0] for r in rows[:k_ids]]
    thumb_ids = []
    blob = b"RIFF" + bytes(1020)
    for image_id, thumb in rows[:k_thumbs]:
        p = Path(thumb)
        if not p.resolve().is_relative_to(datasets_dir):
            continue  # the work dir moved since seeding; the route would answer 403
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(blob)
        thumb_ids.append(image_id)
    return ids, thumb_ids


async def _issue(client, case: dict, capture: Capture | None, before=None) -> tuple[int, int, float]:
    """Send a case's requests; return (status, payload bytes, ms spent in requests).

    Captures statements for the first request only. `before` is awaited ahead of every
    request and is not timed: a cold run evicts there, so each request of a batch
    starts cold, not just the first."""
    nbytes = 0
    elapsed = 0.0
    for idx, (path, params) in enumerate(case["reqs"]):
        if before is not None:
            await before()
        if capture is not None:
            capture.on = idx == 0
        t0 = time.perf_counter()
        r = await client.get(path, params=params)
        elapsed += time.perf_counter() - t0
        if capture is not None:
            capture.on = False
        if r.status_code != 200:
            return r.status_code, nbytes, elapsed * 1000
        nbytes += len(r.content)
    return 200, nbytes, elapsed * 1000


async def measure_cases(db_path: Path, datasets_dir: Path, cases: list[dict], args) -> tuple[dict, dict | None]:
    """Every case through the app; returns the results and, for a cold run, the tally
    of evictions tried and failed."""
    from backend.services import dataset_service

    capture = Capture()
    engine = make_engine(db_path, capture, args.cold)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    results: dict[str, dict] = {}
    evictions = Evictions() if args.cold else None

    async def prepare(case: dict) -> None:
        if case.get("cache") == "cold":
            dataset_service._stats_cache.clear()

    async def evict() -> None:
        await engine.dispose()  # NullPool already closed it; nothing may hold the file
        evictions.evict(db_path)

    before = evict if args.cold else None

    try:
        async with app_client(Session, datasets_dir) as client:
            for case in cases:
                if case.get("cache") == "warm":
                    dataset_service._stats_cache.clear()
                    await _issue(client, case, None)  # fill the cache; the poll is what is timed
                times: list[float] = []
                status, nbytes = 200, 0
                capture.stmts = []
                for rep in range(1 + args.reps):
                    await prepare(case)
                    status, nbytes, dt = await _issue(
                        client, case, capture if rep == 0 else None, before
                    )
                    if status != 200:
                        break
                    times.append(dt)
                res = {k: v for k, v in case.items() if k != "reqs"}
                res["requests"] = len(case["reqs"])
                res["status"] = status
                if status != 200:
                    print(f"    {case['name']}: HTTP {status} (route gone or changed?)", flush=True)
                    results[case["name"]] = res
                    continue
                res["first_ms"] = round(times[0], 3)
                res["best_ms"] = round(min(times[1:] or times), 3)
                res["payload_bytes"] = nbytes
                stmts = list(capture.stmts)
                if args.mem:
                    await prepare(case)
                    tracemalloc.start()
                    await _issue(client, {**case, "reqs": case["reqs"][:1]}, None)
                    res["py_peak_mb"] = round(tracemalloc.get_traced_memory()[1] / 2**20, 2)
                    tracemalloc.stop()
                res["statements"] = replay(db_path, stmts, args.reps, evictions)
                res["sql_ms"] = round(sum(s["replay_ms"] or 0 for s in res["statements"]), 3)
                results[case["name"]] = res
                if case["group"] != "gallery" or case.get("depth") == "deep":
                    print(f"    {case['name']}: best {res['best_ms']:.1f} ms "
                          f"(first {res['first_ms']:.1f}, sql {res['sql_ms']:.1f})", flush=True)
    finally:
        await engine.dispose()
    tally = None
    if evictions is not None:
        tally = {"tried": evictions.tried, "failed": evictions.failed}
        if evictions.failed:
            print(f"    WARNING: {evictions.failed} of {evictions.tried} evictions failed; "
                  "those calls ran on a warm cache", flush=True)
    return results, tally


def analyze(db_path: Path) -> bool:
    with contextlib.closing(sqlite3.connect(db_path)) as con:
        con.execute("PRAGMA optimize=0x10002")
        con.commit()
        try:
            return bool(con.execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0])
        except sqlite3.OperationalError:
            return False


def drop_analysis(db_path: Path) -> None:
    """Back to what the app sees: it never runs ANALYZE, so it never has statistics."""
    with contextlib.closing(sqlite3.connect(db_path)) as con:
        for table in ("sqlite_stat1", "sqlite_stat4"):
            try:
                con.execute(f"DROP TABLE IF EXISTS {table}")
            except sqlite3.OperationalError:
                con.execute(f"DELETE FROM {table}")
        con.commit()


# --------------------------------------------------------------------------- legacy benches
def bench_dedup(db_path: Path) -> dict:
    from backend.ml.technical_scorer import DUPLICATE_THRESHOLD, find_duplicates_sync

    with contextlib.closing(sqlite3.connect(db_path)) as con:
        phashes = con.execute(
            "SELECT id, phash FROM images WHERE dataset_id = ? AND phash IS NOT NULL", (DATASET_ID,)
        ).fetchall()
    t0 = time.perf_counter()
    groups = find_duplicates_sync(phashes, DUPLICATE_THRESHOLD)
    return {"dedup_s": round(time.perf_counter() - t0, 3), "groups": len(groups),
            "dup_images": sum(len(g) - 1 for g in groups)}


def bench_similarity(db_path: Path) -> dict:
    from backend.ml.similarity_scorer import compute_style_similarity

    with contextlib.closing(sqlite3.connect(db_path)) as con:
        t0 = time.perf_counter()
        cand = con.execute(
            "SELECT id, dino_embedding FROM images WHERE dataset_id = ? AND dino_embedding IS NOT NULL",
            (DATASET_ID,),
        ).fetchall()
        load = time.perf_counter() - t0
    if len(cand) < 2:
        return {"sim_load_s": round(load, 3), "sim_calc_s": None, "sim_n": len(cand)}
    t0 = time.perf_counter()
    compute_style_similarity([cand[0][1], cand[1][1]], [c[1] for c in cand], EMBED_DIM)
    return {"sim_load_s": round(load, 3), "sim_calc_s": round(time.perf_counter() - t0, 3),
            "sim_n": len(cand)}


def bench_thumb_glob(tmp_root: Path, count: int) -> dict:
    flat = tmp_root / "flat"
    flat.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (flat / f"img_{i:08d}.webp").touch()
    t0 = time.perf_counter()
    _ = len(list(flat.glob("*.webp")))
    flat_dt = time.perf_counter() - t0

    sharded = tmp_root / "sharded"
    for i in range(count):
        d = sharded / f"{i % 256:02x}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"img_{i:08d}.webp").touch()
    t0 = time.perf_counter()
    _ = len(list(sharded.glob("*/*.webp")))
    shard_dt = time.perf_counter() - t0

    shutil.rmtree(flat, ignore_errors=True)
    shutil.rmtree(sharded, ignore_errors=True)
    return {"thumb_n": count, "thumb_flat_ms": round(flat_dt * 1000, 1),
            "thumb_shard_ms": round(shard_dt * 1000, 1)}


# --------------------------------------------------------------------------- driver
def machine_info() -> dict:
    info = {"platform": platform.platform(), "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version, "cpu": platform.processor(),
            "cpu_count": os.cpu_count()}
    try:
        import psutil

        info["ram_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:  # noqa: BLE001
        pass
    return info


async def run_size(args, n: int, work_dir: Path, groups: set[str]) -> dict:
    print(f"--- {args.profile}, N={n:,} ---", flush=True)
    db_path, datasets_dir, seeded, seed_s = prepare_db(args, n, work_dir)
    cols = images_column_order(db_path)
    run: dict = {
        "profile": args.profile, "n": n, "neighbour_rows": args.neighbour_rows,
        "schema": args.schema, "seeded": seeded, "seed_s": round(seed_s, 1),
        "db_bytes": db_path.stat().st_size, "cold": args.cold,
        "head": alembic_head() if args.schema == "alembic" else None,
        "blob_columns_at": {c: cols.index(c) + 1 for c in
                            ("clip_embedding", "dino_embedding", "dino_layer_embeddings") if c in cols},
        "columns": len(cols),
        "after_blobs": cols[cols.index("dino_layer_embeddings") + 1:] if "dino_layer_embeddings" in cols else [],
    }
    if seeded:
        print(f"    seeded in {seed_s:.1f}s, {run['db_bytes'] / 2**30:.2f} GB", flush=True)
    print(f"    blobs at columns {sorted(run['blob_columns_at'].values())} of {len(cols)}", flush=True)

    try:
        endpoint_groups = groups & set(ENDPOINT_GROUPS)
        if endpoint_groups:
            ids, thumb_ids = ([], [])
            if "lookups" in endpoint_groups:
                ids, thumb_ids = sample_lookups(db_path, n, args.lookups, args.thumbs, datasets_dir)
            cases = build_cases(endpoint_groups, n, ids, thumb_ids)
            drop_analysis(db_path)  # a reused file must not carry an earlier --optimize
            run["cases"], run["evictions"] = await measure_cases(db_path, datasets_dir, cases, args)
            if args.optimize:
                print("    PRAGMA optimize, then the same cases again", flush=True)
                try:
                    run["analyzed"] = analyze(db_path)
                    run["optimized"], run["evictions_analyzed"] = await measure_cases(
                        db_path, datasets_dir, cases, args
                    )
                finally:
                    drop_analysis(db_path)
        legacy: dict = {}
        if "dedup" in groups:
            legacy.update(bench_dedup(db_path))
        if "similarity" in groups:
            legacy.update(bench_similarity(db_path))
        if "thumb" in groups:
            tmp = Path(tempfile.mkdtemp(prefix="bench_thumbs_", dir=work_dir))
            try:
                legacy.update(bench_thumb_glob(tmp, min(n, args.thumb_cap)))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        if legacy:
            run["legacy"] = legacy
            print(f"    legacy: {legacy}", flush=True)
    finally:
        if not (args.keep_db or args.reuse_db):
            for p in _db_files(db_path):
                try:
                    p.unlink(missing_ok=True)
                except OSError as exc:  # a Windows handle still closing; not a failed run
                    print(f"    could not remove {p.name}: {exc}", flush=True)
            shutil.rmtree(datasets_dir, ignore_errors=True)
    run["peak_rss_mb"] = round(rss_peak_mb(), 1)
    return run


async def run(args) -> dict:
    groups = {g.strip() for g in args.only.split(",") if g.strip()}
    unknown = groups - set(ENDPOINT_GROUPS) - set(LEGACY_GROUPS)
    if unknown:
        raise SystemExit(f"--only: unknown group(s) {sorted(unknown)}")
    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    if args.cold and not cold_cache_supported(ALEMBIC_INI):
        raise SystemExit(
            "--cold: this platform cannot evict a file from the OS cache "
            "(Linux needs root for /proc/sys/vm/drop_caches; macOS is not supported)."
        )
    sizes = [int(x) for x in args.sweep.split(",") if x.strip()]
    report = {"started": datetime.now().isoformat(timespec="seconds"), "machine": machine_info(),
              "args": {k: v for k, v in vars(args).items() if k not in ("json", "compare")},
              "runs": []}
    print(f"# Scaling benchmark: profile={args.profile}, schema={args.schema}, "
          f"SQLite {sqlite3.sqlite_version}, work dir {work_dir}\n", flush=True)
    for n in sizes:
        report["runs"].append(await run_size(args, n, work_dir, groups))
    return report


# --------------------------------------------------------------------------- reporting
def _f(x, nd=1) -> str:
    return "-" if x is None else f"{x:,.{nd}f}"


def _plan_word(res: dict | None) -> str:
    if not res or not res.get("statements"):
        return "-"
    return "TEMP B-TREE" if res["statements"][0]["temp_btree"] else "index"


def print_markdown(report: dict) -> None:
    for run in report["runs"]:
        cases = run.get("cases", {})
        opt = run.get("optimized")
        print(f"\n## {run['profile']}, N={run['n']:,}"
              f"{' (cold)' if run['cold'] else ''}\n")
        print(f"Schema {run['schema']} {run.get('head') or ''}, "
              f"blobs at columns {sorted(run['blob_columns_at'].values())} of {run['columns']}, "
              f"DB {run['db_bytes'] / 2**30:.2f} GB. "
              f"After the blobs: {', '.join(run['after_blobs'])}.\n")
        print(f"Process peak RSS so far: {run['peak_rss_mb']:.0f} MB. It is the whole "
              "process's high-water mark, seeding and any earlier size included, so it is not "
              "a per-run figure; `Py peak MB` below is per case.\n")
        for label in ("evictions", "evictions_analyzed"):
            ev = run.get(label)
            if ev:
                print(f"Cold cache: {ev['tried'] - ev['failed']} of {ev['tried']} evictions "
                      f"succeeded{' (analyzed pass)' if label.endswith('analyzed') else ''}.\n")

        gallery = [c for c in cases.values() if c["group"] == "gallery"]
        if gallery:
            print("### Gallery (`GET /images/`, limit 100)\n")
            head = "| Sort | Order | Page | Best ms | First ms | Ids SQL ms | Plan | Page KB |"
            sep = "|---|---|---|---|---|---|---|---|"
            if opt:
                head += " Best ms, analyzed | Plan, analyzed |"
                sep += "---|---|"
            print(head)
            print(sep)
            for c in gallery:
                ids_ms = c["statements"][0]["replay_ms"] if c.get("statements") else None
                line = (f"| {c['sort']}{' *' if c['sort'] in INDEXED_SORTS else ''} | {c['order']} | "
                        f"{c['depth']} | {_f(c.get('best_ms'))} | {_f(c.get('first_ms'))} | "
                        f"{_f(ids_ms)} | {_plan_word(c)} | {_f((c.get('payload_bytes') or 0) / 1024)} |")
                if opt:
                    o = opt.get(c["name"], {})
                    line += f" {_f(o.get('best_ms'))} | {_plan_word(o)} |"
                print(line)
            print("\n`*` marks a sort with an index. Ids SQL is the page's id statement alone, "
                  "replayed on raw sqlite3.\n")

        others = [c for c in cases.values() if c["group"] != "gallery"]
        if others:
            print("### Counts, Stats and lookups\n")
            head = "| Case | Best ms | First ms | Per request ms | SQL ms | Rest ms | Payload KB | Py peak MB |"
            sep = "|---|---|---|---|---|---|---|---|"
            if opt:
                head += " Best ms, analyzed |"
                sep += "---|"
            print(head)
            print(sep)
            for c in others:
                if c.get("status") != 200:
                    print(f"| {c['name']} | HTTP {c.get('status')} | | | | | | |")
                    continue
                per = c["best_ms"] / c["requests"]
                # Cold replays evict before every statement, the endpoint once per call,
                # so their sum is not comparable with the endpoint's time.
                rest = None if run["cold"] else per - c["sql_ms"]
                line = (f"| {c['name']} | {_f(c['best_ms'])} | {_f(c['first_ms'])} | {_f(per, 2)} | "
                        f"{_f(c['sql_ms'], 2)} | {_f(rest, 2)} | {_f(c['payload_bytes'] / 1024)} | "
                        f"{_f(c.get('py_peak_mb'), 2)} |")
                if opt:
                    line += f" {_f(opt.get(c['name'], {}).get('best_ms'))} |"
                print(line)
            print("\nSQL is the sum of the case's statements replayed on raw sqlite3 (for a "
                  "batch, one request's). Rest is the per-request time beyond that: ORM, "
                  "Pydantic, JSON and the transport. A cold run evicts before each replayed "
                  "statement, so its SQL column is per statement cold and Rest is not given.\n")

            print("### Statements\n")
            print("| Case | ms | Plan | SQL |")
            print("|---|---|---|---|")
            for c in others:
                for s in c.get("statements", []):
                    plan = "; ".join(s["plan"]).replace("|", "\\|")
                    sql = s["sql"] if len(s["sql"]) <= 140 else s["sql"][:137] + "..."
                    sql = sql.replace("|", "\\|")
                    print(f"| {c['name']} | {_f(s['replay_ms'], 2)} | `{plan}` | `{sql}` |")
            print()

        if run.get("legacy"):
            print("### Legacy benches\n")
            for k, v in run["legacy"].items():
                print(f"- {k}: {v}")
            print()


def print_compare(before: dict, after: dict) -> None:
    print("\n## Compared with the earlier run\n")

    def key(r: dict) -> tuple:
        # Everything that changes what a number means. A cold run against a warm one,
        # or one schema against the other, is not a before-and-after.
        return (r["profile"], r["n"], r["schema"], r["cold"], r.get("neighbour_rows", 0))

    keyed = {key(r): r for r in before["runs"]}
    for run in after["runs"]:
        old = keyed.get(key(run))
        if not old:
            print(f"- {run['profile']} N={run['n']:,} (schema {run['schema']}, "
                  f"cold {run['cold']}): no matching run in the earlier file")
            continue
        print(f"### {run['profile']}, N={run['n']:,}\n")
        print("| Pass | Case | Before ms | After ms | Change |")
        print("|---|---|---|---|---|")
        # `field`, not `key`: the loop variable must not shadow `key()` above, or the
        # second run of a sweep calls a string.
        for label, field in (("default", "cases"), ("analyzed", "optimized")):
            for name, c in (run.get(field) or {}).items():
                o = (old.get(field) or {}).get(name)
                if not o or "best_ms" not in o or "best_ms" not in c:
                    continue
                ratio = c["best_ms"] / o["best_ms"] if o["best_ms"] else float("nan")
                print(f"| {label} | {name} | {_f(o['best_ms'])} | {_f(c['best_ms'])} | x{ratio:.2f} |")
        print()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Scaling benchmark harness (M9 stage 6)")
    ap.add_argument("--sweep", default="100000", help="comma-separated row counts")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="photo",
                    help="row shape: photo (~400 B metadata) or comfy (~55 KB)")
    ap.add_argument("--schema", choices=["alembic", "create_all"], default="alembic",
                    help="build the schema the way a real install has it (default) or from the models")
    ap.add_argument("--neighbour-rows", type=int, default=0,
                    help="rows in a second dataset, so dataset filters really filter")
    default_work = os.path.join(os.environ.get("CLAUDE_SCRATCH", tempfile.gettempdir()), "crucible-bench")
    ap.add_argument("--work-dir", default=default_work, help="scratch folder for databases and files")
    ap.add_argument("--keep-db", action="store_true", help="keep the seeded database after the run")
    ap.add_argument("--reuse-db", action="store_true",
                    help="reuse a matching seeded database (upgraded to head first); implies --keep-db")
    ap.add_argument("--only", default=DEFAULT_ONLY,
                    help=f"comma-separated groups: {', '.join(ENDPOINT_GROUPS + LEGACY_GROUPS)} "
                         f"(default {DEFAULT_ONLY})")
    ap.add_argument("--reps", type=int, default=3, help="timed repeats after the first call; best is reported")
    ap.add_argument("--lookups", type=int, default=2000, help="ids for the GET /images/{id} case")
    ap.add_argument("--thumbs", type=int, default=500, help="ids for the thumbnail case")
    ap.add_argument("--no-mem", dest="mem", action="store_false",
                    help="skip the tracemalloc pass (it adds one untimed call per case)")
    ap.add_argument("--optimize", action="store_true",
                    help="also measure after PRAGMA optimize (ANALYZE), then drop the statistics")
    ap.add_argument("--cold", action="store_true",
                    help="evict the database from the OS cache before every request and replay")
    ap.add_argument("--dup-prob", type=float, default=0.05, help="fraction of near-duplicate phashes")
    ap.add_argument("--thumb-cap", type=int, default=100_000, help="files for the legacy thumb glob")
    ap.add_argument("--json", help="write every result to this JSON file")
    ap.add_argument("--compare", help="an earlier --json file to print the change against")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    report = asyncio.run(run(args))
    print_markdown(report)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, default=str))
        print(f"results written to {out}")
    if args.compare:
        print_compare(json.loads(Path(args.compare).read_text()), report)


if __name__ == "__main__":
    main()
