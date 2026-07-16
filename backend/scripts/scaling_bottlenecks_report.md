# Scaling bottleneck findings — 100k → 1M images per dataset

**Date:** 2026-07-16
**Harness:** `backend/scripts/bench_scaling.py`
**Question:** Can Crucible scale to 500k–1M images per dataset, and if not, what actually breaks?

## TL;DR

The bottlenecks are **algorithmic / access-pattern problems, not the SQLite engine**.
SQLite handled 1M indexed rows fine; the failures are in how we *use* it. One path
(duplicate detection) is an O(N²) hard wall that makes datasets past a few hundred
thousand images unusable; the rest are linear-with-a-heavy-constant and fixable with
indexes and query changes. **Migrating databases would fix none of these.**

## Method

- Measured the **service layer / pure functions directly** — no HTTP, no ML inference,
  no real image files. Only the DB/aggregation/dedup/similarity-math paths scale with
  row count; per-image inference cost is orthogonal.
- Seeded synthetic `Image` rows into an **isolated scratch SQLite DB** via Core bulk
  insert (bypasses the ORM `caption_text` tiktoken listener; `caption_token_count` set
  explicitly). The real `dataset_manager.db` was never touched.
- Benchmarks call the **real production functions**: `find_duplicates_sync`,
  `get_dataset_stats` / `get_score_values`, the `images` router query shape, and
  `compute_style_similarity`.
- Sweep at 100k / 250k / 500k / 1M. Embeddings seeded only ≤200k (reduced N, ~0.8 GB)
  and extrapolated. ~5% of rows seeded as near-duplicates so dedup does real work.
- Environment caveat: run in a headless dev container on WSL2 (single-user SQLite,
  `cv2` stubbed in the harness only — production untouched). **Absolute times will
  differ on real hardware; the *shape* of each curve and the relative ordering are the
  findings, not the millisecond values.**

## Results

| N | dedup | stats | scores | page shallow | page **deep offset** | page keyset | sim load / calc |
|---|---|---|---|---|---|---|---|
| 100k | **120.9 s** | 2.48 s | 1.02 s | 158 ms | 1.51 s | 154 ms | 0.64 s / 0.95 s |
| 250k | **762.8 s** | 5.57 s | 2.42 s | 420 ms | 4.29 s | 407 ms | — (capped) |
| 500k | ~3020 s\* | 11.65 s | 4.74 s | 951 ms | 9.14 s | 918 ms | — |
| 1M | ~12100 s\* (~3.4 h) | 24.87 s | 10.01 s | 2.25 s | **20.12 s** | 2.24 s | — |

Peak process RSS stayed ≤ 1.1 GB throughout (embeddings capped at 200k). Thumbnail glob
(flat vs sharded, capped at 100k files) was ~120 ms either way — no measurable difference.

\* Dedup at 500k/1M **projected, not run.** The O(N²) model fit from the 100k point
predicts 250k at **756 s** vs **763 s measured — within 1%**, so the run was stopped
rather than spend ~4 more hours confirming a curve that already fits.

## Findings, ranked

### 1. Duplicate detection — O(N²) hard wall (CRITICAL)
`find_duplicates_sync` ([technical_scorer.py](../ml/technical_scorer.py)) is vectorized but
still compares each phash against all later ones. 2.5× data → 6.3× time; the quadratic
model predicts the 250k point to within 1%. Extrapolates to ~50 min at 500k and **~3.4 h
at 1M**. This alone makes large datasets unusable.

### 2. Gallery pagination — linear, no covering index (HIGH)
Deep OFFSET is the worst (**20 s at 1M**, ~9× shallow) — the offset walks every skipped
row. **But** shallow and keyset *also* scale linearly (~2.2 s at 1M), because the default
`created_at` sort and the `(dataset_id, id)` keyset have **no covering index**, so SQLite
full-scans+sorts regardless of depth. Correction to an earlier assumption: **keyset alone
does not fix this — it needs the composite index too.**

### 3. Stats aggregation — linear with a punishing constant (MEDIUM)
`get_dataset_stats` + `get_score_values` load the whole dataset into Python. ~2.1× time
per 2× data (O(N), not quadratic), but the constant lands at **~35 s to open the Stats
page at 1M** (24.9 s + 10.0 s).

### 4. Similarity — memory concern, not a time wall (LOW / conditional)
At 100k: 1.6 s total, ~1.1 GB RSS, linear. The current mean-reference approach scales
fine on time; blob-load RAM is the thing to watch only if true k-NN search is added later.

### 5. Thumbnail directory layout — no measured penalty yet (DEFER)
Flat vs sharded glob were identical (~120 ms) at 100k. No evidence to act on.

## Recommended actions (evidence-ranked)

1. **Replace dedup with a BK-tree (exact) or LSH (approximate) neighbor search.** The only
   hours-scale problem; gates everything else. Caveat: preserve the current greedy
   grouping semantics (first unassigned row = group root → `duplicate_of`) or pin the new
   behavior with a golden test — the chosen "keep" image can change otherwise.
2. **Add composite indexes for the gallery sort + keyset cursor, then switch to keyset
   pagination.** Turns 2–20 s page loads into near-constant time. Cheap, high-impact. The
   index must ship *with* the keyset change — one without the other buys nothing. Trade-off:
   keyset loses "jump to page N"/clickable page numbers (next/prev or infinite scroll only).
3. **Move stats histograms SQL-side and/or cache them.** Trims a 35 s page to sub-second.
   Lower urgency (already linear). Caveat: SQLite lacks `width_bucket`; emulate binning
   with `floor((x-min)/w)` + `GROUP BY`, and pin bucket boundaries with tests.
4. **Chunk/stream the similarity blob load** (pure win, bounds RAM). Defer splitting
   embeddings into their own table until DB-file bloat is a concrete operational pain.
5. **Do not shard thumbnails** until a measurement shows a penalty — sharding also
   collides with the documented flat-dir `glob("*.webp")` collision-handling invariant.

**Do not migrate to Postgres.** It solves none of findings 1–3 (all algorithmic) and
adds a service dependency that contradicts the single-user local-app model.

## Re-running

```
python -m backend.scripts.bench_scaling --sweep 100000,250000,500000,1000000
python -m backend.scripts.bench_scaling --sweep 500000,1000000 --only stats   # target one path
```
Flags: `--only`, `--embed-cap`, `--embed-dim`, `--thumb-cap`, `--dup-prob`, `--keep-db`,
`--db-path`. Use it to prove each fix flattens its curve (e.g. re-run `--only dedup` after
the BK-tree change).
