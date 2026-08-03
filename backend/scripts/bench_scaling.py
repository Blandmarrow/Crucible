"""Scaling bottleneck benchmark harness.

Measures the suspected 500k-1M scaling bottlenecks *directly against the service layer /
pure functions* — no HTTP server, no ML inference, no real image files. It seeds synthetic
Image rows into an isolated scratch SQLite DB and times each hot path across a size sweep,
so optimization effort goes only where a curve actually bends (linear vs quadratic).

Run from the repo root with the venv active:

    python -m backend.scripts.bench_scaling --sweep 100000,250000,500000,1000000

The real ``dataset_manager.db`` is NEVER touched: this builds its own async engine against
a scratch DB (default under the session scratchpad / a temp file) and passes its own session
into ``get_dataset_stats`` etc. See docs/dev discussion + the approved plan for context.

Bottlenecks under test (hypotheses):
  1. dedup      — find_duplicates_sync  (O(N^2) suspect)
  2. stats      — get_dataset_stats / get_score_values  (loads whole dataset into Python)
  3. pagination — deep OFFSET vs keyset  (offset walks skipped rows)
  4. similarity — load all embedding blobs + cosine  (blob load is the suspected wall)
  5. thumb glob — flat dir glob("*.webp") vs sharded  (one dir with ~1M files)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import resource
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import event, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.ml.similarity_scorer import compute_style_similarity
from backend.ml.technical_scorer import DUPLICATE_THRESHOLD, find_duplicates_sync
from backend.models.dataset import Dataset
from backend.models.image import Image
from backend.services.dataset_service import get_dataset_stats, get_score_values

DATASET_ID = "bench0001-0000-0000-0000-000000000000"
ID_PREFIX = "bench001"  # zero-padded row ids sort lexicographically == numerically


# --------------------------------------------------------------------------- utils
def rss_peak_mb() -> float:
    """Process peak RSS high-water mark in MB (ru_maxrss is KB on Linux)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def make_scratch_engine(db_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=OFF")  # scratch DB — durability irrelevant
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def _gen_phash(rng: random.Random, bases: list[int], dup_prob: float) -> int:
    """64-bit phash. With prob dup_prob, a near-dup of an earlier base (1-3 bit flips,
    < DUPLICATE_THRESHOLD Hamming) so dedup grouping does real work; else a fresh hash."""
    if bases and rng.random() < dup_prob:
        v = bases[rng.randrange(len(bases))]
        for _ in range(rng.randint(1, 3)):
            v ^= 1 << rng.randrange(64)
        return v
    v = rng.getrandbits(64)
    bases.append(v)
    return v


# --------------------------------------------------------------------------- seeding
async def reset_schema(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def seed(
    Session,
    n: int,
    with_embeddings: bool,
    embed_dim: int,
    batch: int = 10_000,
    dup_prob: float = 0.05,
) -> None:
    """Bulk-insert n Image rows via Core executemany (bypasses the ORM caption_text
    tiktoken listener; caption_token_count set explicitly)."""
    rng = random.Random(1234)
    np.random.seed(1234)
    bases: list[int] = []
    base_time = datetime(2024, 1, 1)
    dims = [512, 640, 768, 1024, 1280, 1536, 2048]

    async with Session() as s:
        await s.execute(
            insert(Dataset).values(
                id=DATASET_ID, name="bench", folder_path="/tmp/bench",
                description="", category="", declared_subfolders=[],
                image_count=n, captioned_count=n, total_size_bytes=0,
            )
        )
        await s.commit()

    inserted = 0
    while inserted < n:
        m = min(batch, n - inserted)
        rows = []
        for j in range(m):
            i = inserted + j
            v = _gen_phash(rng, bases, dup_prob)
            row = {
                "id": f"{ID_PREFIX}-{i:08d}",
                "dataset_id": DATASET_ID,
                "filename": f"img_{i:08d}.jpg",
                "original_filename": f"orig_{i}.jpg",
                "subfolder": "",
                "file_path": f"/tmp/bench/images/img_{i:08d}.jpg",
                "thumbnail_path": f"/tmp/bench/thumbnails/img_{i:08d}.webp",
                "is_auto_named": False,
                "width": rng.choice(dims),
                "height": rng.choice(dims),
                "file_size_bytes": rng.randint(50_000, 5_000_000),
                "format": "JPEG",
                "phash": f"{v:016x}",
                "created_at": base_time + timedelta(seconds=i),
                "updated_at": base_time,
                "aesthetic_score": round(rng.random() * 10, 4),
                # Written alongside the score, never omitted:
                # `GET /rating/scorer-agreement` groups by this marker.
                "aesthetic_model": "laion",
                "blur_score": round(rng.random() * 300, 4),
                "noise_score": round(rng.random() * 30, 4),
                "uniformity_score": round(rng.random() * 30, 4),
                "watermark_score": round(rng.random(), 4),
                "color_score": round(rng.random() * 100, 4),
                # mean HSV S, 0–1 — not the 0–100 range color_score uses
                "saturation_score": round(rng.random(), 4),
                "quality_flags": {
                    "is_blurry": rng.random() < 0.10,
                    "is_noisy": rng.random() < 0.05,
                    "is_uniform": rng.random() < 0.04,
                    "has_watermark": rng.random() < 0.03,
                    "is_nsfw": rng.random() < 0.02,
                    "is_duplicate": False,
                },
                "caption_text": f"a synthetic benchmark caption for image number {i}",
                "caption_token_count": rng.randint(5, 40),
                "caption_style": "tags",
                "captioned_by": "bench",
            }
            if with_embeddings:
                row["clip_embedding"] = np.random.rand(embed_dim).astype(np.float16).tobytes()
                row["dino_embedding"] = np.random.rand(embed_dim).astype(np.float16).tobytes()
            rows.append(row)
        async with Session() as s:
            await s.execute(insert(Image), rows)
            await s.commit()
        inserted += m


# --------------------------------------------------------------------------- benchmarks
async def bench_dedup(Session) -> tuple[float, int, int]:
    async with Session() as s:
        res = await s.execute(
            select(Image.id, Image.phash).where(
                Image.dataset_id == DATASET_ID, Image.phash.isnot(None)
            )
        )
        phashes = [(r.id, r.phash) for r in res.all()]
    t0 = time.perf_counter()
    groups = find_duplicates_sync(phashes, DUPLICATE_THRESHOLD)
    dt = time.perf_counter() - t0
    dup_images = sum(len(g) - 1 for g in groups)
    return dt, len(groups), dup_images


async def bench_stats(Session) -> tuple[float, float]:
    async with Session() as s:
        t0 = time.perf_counter()
        await get_dataset_stats(s, DATASET_ID)
        d_stats = time.perf_counter() - t0
    async with Session() as s:
        t0 = time.perf_counter()
        await get_score_values(s, DATASET_ID)
        d_scores = time.perf_counter() - t0
    return d_stats, d_scores


async def bench_pagination(Session, n: int, limit: int = 50) -> tuple[float, float, float]:
    async with Session() as s:
        base = (
            select(Image)
            .where(Image.dataset_id == DATASET_ID)
            .order_by(Image.created_at.desc())
        )
        t0 = time.perf_counter()
        (await s.execute(base.offset(0).limit(limit))).scalars().all()
        shallow = time.perf_counter() - t0

        deep_off = max(0, n - limit)
        t0 = time.perf_counter()
        (await s.execute(base.offset(deep_off).limit(limit))).scalars().all()
        deep = time.perf_counter() - t0

        # Keyset equivalent: PK index range scan, depth-independent. Ordered by id
        # (unique, zero-padded so lexical == numeric); cursor computed directly.
        cursor = f"{ID_PREFIX}-{deep_off:08d}"
        kq = (
            select(Image)
            .where(Image.dataset_id == DATASET_ID, Image.id > cursor)
            .order_by(Image.id.asc())
            .limit(limit)
        )
        t0 = time.perf_counter()
        (await s.execute(kq)).scalars().all()
        keyset = time.perf_counter() - t0
    return shallow, deep, keyset


async def bench_similarity(Session) -> tuple[float, float, int]:
    async with Session() as s:
        t0 = time.perf_counter()
        res = await s.execute(
            select(Image.id, Image.dino_embedding).where(
                Image.dataset_id == DATASET_ID, Image.dino_embedding.isnot(None)
            )
        )
        cand = [(r[0], r[1]) for r in res.all()]
        load = time.perf_counter() - t0
    ref = [cand[0][1], cand[1][1]]
    t0 = time.perf_counter()
    compute_style_similarity(ref, [c[1] for c in cand])
    calc = time.perf_counter() - t0
    return load, calc, len(cand)


def bench_thumb_glob(tmp_root: Path, count: int) -> tuple[float, float]:
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
    return flat_dt, shard_dt


# --------------------------------------------------------------------------- driver
def _ms(x: float) -> str:
    return f"{x * 1000:.1f}"


def _s(x: float) -> str:
    return f"{x:.3f}"


async def run(args) -> None:
    sizes = [int(x) for x in args.sweep.split(",") if x.strip()]
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()

    thumb_root = Path(tempfile.mkdtemp(prefix="bench_thumbs_"))
    engine = make_scratch_engine(db_path)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    results: list[dict] = []
    print(f"# Scaling bottleneck sweep  (DUPLICATE_THRESHOLD={DUPLICATE_THRESHOLD})")
    print(f"scratch DB: {db_path}\n")

    try:
        for n in sizes:
            with_emb = n <= args.embed_cap
            print(f"--- N={n:,}  (embeddings={'yes' if with_emb else 'no'}) ---", flush=True)

            await reset_schema(engine)
            t0 = time.perf_counter()
            await seed(Session, n, with_emb, args.embed_dim, dup_prob=args.dup_prob)
            seed_dt = time.perf_counter() - t0
            print(f"    seeded in {seed_dt:.1f}s", flush=True)

            row: dict = {"n": n, "seed": seed_dt}

            if args.only in (None, "dedup"):
                row["dedup"], row["groups"], row["dupimg"] = await bench_dedup(Session)
                print(f"    dedup {row['dedup']:.3f}s  ({row['groups']} groups, {row['dupimg']} dup imgs)", flush=True)
            if args.only in (None, "stats"):
                row["stats"], row["scores"] = await bench_stats(Session)
                print(f"    stats {row['stats']:.3f}s  scores {row['scores']:.3f}s", flush=True)
            if args.only in (None, "pagination"):
                row["pg_shallow"], row["pg_deep"], row["pg_keyset"] = await bench_pagination(Session, n)
                print(f"    page shallow {_ms(row['pg_shallow'])}ms  deep {_ms(row['pg_deep'])}ms  keyset {_ms(row['pg_keyset'])}ms", flush=True)
            if args.only in (None, "similarity"):
                if with_emb:
                    row["sim_load"], row["sim_calc"], row["sim_n"] = await bench_similarity(Session)
                    print(f"    sim load {row['sim_load']:.3f}s  calc {row['sim_calc']:.3f}s  ({row['sim_n']} embeds)", flush=True)
                else:
                    print("    sim skipped (embeddings not seeded above --embed-cap)", flush=True)
            if args.only in (None, "thumb"):
                tc = min(n, args.thumb_cap)
                row["thumb_flat"], row["thumb_shard"] = bench_thumb_glob(thumb_root, tc)
                row["thumb_n"] = tc
                print(f"    thumb glob flat {_ms(row['thumb_flat'])}ms  sharded {_ms(row['thumb_shard'])}ms  (n={tc:,})", flush=True)

            row["peak_rss"] = rss_peak_mb()
            print(f"    peak RSS (process high-water): {row['peak_rss']:.0f} MB\n", flush=True)
            results.append(row)
    finally:
        await engine.dispose()
        shutil.rmtree(thumb_root, ignore_errors=True)
        if not args.keep_db:
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(db_path) + suffix)
                if p.exists():
                    p.unlink()

    _print_table(results, args)


def _print_table(results: list[dict], args) -> None:
    print("\n## Results (markdown)\n")
    header = (
        "| N | seed s | dedup s | groups | stats s | scores s | "
        "page shallow ms | page deep ms | keyset ms | sim load s | sim calc s | "
        "thumb flat ms | thumb shard ms | peak RSS MB |"
    )
    print(header)
    print("|" + "---|" * 15)
    for r in results:
        print(
            "| {n:,} | {seed} | {dedup} | {groups} | {stats} | {scores} | "
            "{ps} | {pd} | {pk} | {sl} | {sc} | {tf} | {ts} | {rss:.0f} |".format(
                n=r["n"],
                seed=_s(r.get("seed", 0)),
                dedup=_s(r["dedup"]) if "dedup" in r else "-",
                groups=r.get("groups", "-"),
                stats=_s(r["stats"]) if "stats" in r else "-",
                scores=_s(r["scores"]) if "scores" in r else "-",
                ps=_ms(r["pg_shallow"]) if "pg_shallow" in r else "-",
                pd=_ms(r["pg_deep"]) if "pg_deep" in r else "-",
                pk=_ms(r["pg_keyset"]) if "pg_keyset" in r else "-",
                sl=_s(r["sim_load"]) if "sim_load" in r else "-",
                sc=_s(r["sim_calc"]) if "sim_calc" in r else "-",
                tf=_ms(r["thumb_flat"]) if "thumb_flat" in r else "-",
                ts=_ms(r["thumb_shard"]) if "thumb_shard" in r else "-",
                rss=r.get("peak_rss", 0),
            )
        )

    # Flag super-linear growth between consecutive sizes.
    print("\n## Growth analysis (time ratio vs N ratio; >1.3x N-ratio => super-linear)\n")
    for key, label in [("dedup", "dedup"), ("stats", "stats"), ("scores", "scores"),
                       ("pg_deep", "page deep"), ("sim_load", "sim load")]:
        pts = [(r["n"], r[key]) for r in results if key in r and r[key] is not None]
        if len(pts) < 2:
            continue
        notes = []
        for (n0, t0), (n1, t1) in zip(pts, pts[1:]):
            if t0 <= 0:
                continue
            n_ratio = n1 / n0
            t_ratio = t1 / t0
            tag = "  <== SUPER-LINEAR" if t_ratio > 1.3 * n_ratio else ""
            notes.append(f"    {n0:,}->{n1:,}: N x{n_ratio:.2f}, time x{t_ratio:.2f}{tag}")
        print(f"  {label}:")
        print("\n".join(notes))


def main() -> None:
    ap = argparse.ArgumentParser(description="Scaling bottleneck benchmark harness")
    ap.add_argument("--sweep", default="100000,250000,500000,1000000",
                    help="comma-separated row counts")
    default_db = os.path.join(
        os.environ.get("CLAUDE_SCRATCH", tempfile.gettempdir()), "bench_scaling.db"
    )
    ap.add_argument("--db-path", default=default_db, help="scratch DB path (NOT the real DB)")
    ap.add_argument("--keep-db", action="store_true", help="keep scratch DB after run")
    ap.add_argument("--embed-cap", type=int, default=200_000,
                    help="seed embeddings only when N <= this (default 200k, ~0.8GB)")
    ap.add_argument("--embed-dim", type=int, default=1024, help="embedding dimensionality (float16)")
    ap.add_argument("--thumb-cap", type=int, default=100_000,
                    help="max real .webp files to create for the glob benchmark")
    ap.add_argument("--dup-prob", type=float, default=0.05,
                    help="fraction of rows seeded as near-duplicates")
    ap.add_argument("--only", choices=["dedup", "stats", "pagination", "similarity", "thumb"],
                    default=None, help="run a single benchmark")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
