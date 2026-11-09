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

> **Update 2026-07-16:** finding 1 (dedup) is fixed — 1M images now dedup in
> ~3.5 min measured, not ~3.4 h. See the addendum at the end of this file.

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

## Addendum 2026-07-16 — Finding 1 (dedup) FIXED

`find_duplicates_sync` now dispatches to an exact **pigeonhole multi-index chunk
search** (4 chunk tables + multi-probe + popcount verification) instead of the
all-pairs scan; brute force remains as the fallback (small N, short hashes,
extreme thresholds, or probe volume not well under N) and as the golden-test
reference. Output is byte-identical
by construction — `backend/tests/test_find_duplicates.py` pins groups, roots,
and member order against the frozen brute-force implementation (runs in CI).
Same environment caveats as the original run.

| N | dedup before | dedup after | speedup | peak RSS after |
|---|---|---|---|---|
| 100k | 120.9 s | **4.96 s** | 24× | 227 MB |
| 250k | 762.8 s | **19.7 s** | 39× | 302 MB |
| 1M | ~12 100 s\* (~3.4 h, projected) | **212.5 s (measured)** | ~57× | 721 MB |

Growth is no longer quadratic but not perfectly linear either (2.5× N → 4.0×
time): the index leaves a residual O(N²/2¹⁶) verification term (16-bit chunk
buckets fill up ∝ N) plus O(N) probe overhead. If a future measurement at
larger N makes even this too slow, the documented follow-ups are, in order: a
CSR index layout (numpy argsort + bincount offset arrays instead of Python dict
tables — measured ~7× faster index build and ~2× lower peak RSS at 1M,
byte-identical buckets), then batch-vectorized candidate generation — not a
different algorithm. At 1M the dedup pass now
costs ~3.5 min inside a quality-scoring job whose per-image ML inference
dominates by orders of magnitude, so finding 1 is closed and the next
bottlenecks are findings 2 (pagination indexes) and 3 (stats aggregation).

## Addendum 2026-11-09 — a partial stage 6 run, by hand

A review of the roadmap timed the hot query shapes ahead of M9 stage 6
(`docs/dev/scale-plan/README.md`). It is **partial**: raw SQL through Python's `sqlite3`
(SQLite 3.49.1), with no SQLAlchemy, Pydantic or HTTP on top, so every number is a floor for
what a request costs. The scripts were scratch files and are not in the repo. Stage 6a
rebuilds them into `bench_scaling.py` from the method below.

**Method.**

- The scratch DB takes its `images` DDL (table and all 13 indexes) from a migrated
  database's `sqlite_master`, not from `create_all`. So the three embedding blobs are
  columns 27–29 of 48, with 19 columns after them, as in a real install.
- **Photo-shaped:** 100,000 rows in one dataset, 1.75 GB. 70% of rows are scored and carry
  all three blobs (1,536 + 1,536 + 18,432 bytes). 80% have a caption of about 270
  characters. `generation_metadata` is 400 bytes.
- **ComfyUI-shaped:** 20,000 rows, 1.45 GB, the same but with a 55,000-byte
  `generation_metadata`. The dev DB's ComfyUI dataset averages 54,895 bytes a row (largest
  58,921).
- **Split layout:** the photo DB with the three blobs moved to a table of their own and the
  file vacuumed. It stands in for stage 7.
- Each time is the best of three runs. Python's default page cache applies (2 MB), which is
  what the app runs with.

**Caveats.** The disk cache was warm, so a cold start is worse, and worst for the scans
that walk overflow pages. One dataset holds every row. No `ANALYZE` had run unless a row
says so, which matches the app (it never runs one). The machine is the owner's Windows dev
box; compare rows with each other, not with the July tables.

### Gallery sorts and deep pages

`TODAY` is the order `_apply_image_ordering` builds since stage 1 (`created_at ASC, id ASC`
after the sort column). `ROWID` breaks ties on `rowid` in the sort's own direction. Each
query selects every non-deferred column, `LIMIT 100`.

| Query (photo DB, 100k rows) | TODAY | ROWID |
|---|---|---|
| Aesthetic desc, page 1 | 323.3 ms | 1.5 ms |
| Aesthetic desc, `OFFSET 60000` | 4,508.6 ms | 4.5 ms |
| Aesthetic asc, page 1 (30k NULL ties first) | 311.4 ms | 0.5 ms |
| Blur desc, `OFFSET 60000` | not run | 4.2 ms |
| Created desc, page 1 | 0.6 ms | 0.5 ms |
| Created desc, `OFFSET 90000` | 713.2 ms | 5.8 ms |
| Aesthetic desc, page 1, one subfolder of 20 | 21.8 ms | 27.3 ms |

| Query (ComfyUI DB, 20k rows) | TODAY | ROWID |
|---|---|---|
| Aesthetic desc, page 1 | 211.4 ms | not run |
| Aesthetic desc, `OFFSET 10000` | 6,902.3 ms | 5.2 ms |

- **The plan.** `TODAY` on a score sort reads
  `SEARCH images USING INDEX ix_images_dataset_similarity (dataset_id=?)` then
  `USE TEMP B-TREE FOR ORDER BY`: it ignores the aesthetic index and sorts the whole
  dataset, full rows included. A copy of the real dev DB picks the same plan. `ROWID` reads
  `SEARCH images USING INDEX ix_images_dataset_aesthetic` with no sort step.
- **Before stage 1** (`ORDER BY aesthetic_score DESC` alone): 1.4 ms, on the aesthetic index.
- **`ANALYZE` does not fix it.** With `analysis_limit=1000` the plan and time were unchanged
  (327.4 ms). After a full `ANALYZE` the planner switched to the `created_at` index and
  still sorted everything (148.6 ms on page 1, 4,183.9 ms at `OFFSET 60000`).
- **A wider index does not fix the descending sort either.** With
  `(dataset_id, aesthetic_score, created_at, id)` the ascending sort took 0.5 ms and the
  descending one stayed at 146–148 ms, because `DESC, ASC, ASC` matches the index in neither
  direction.
- **Forcing the index** (`INDEXED BY ix_images_dataset_aesthetic`, today's order): 1.5 ms
  descending, 72.7 ms ascending. So the planner's choice is the cost, not the index.
- **Ids first, then rows** (`WHERE id IN (SELECT id … ORDER BY … LIMIT … OFFSET …)`), with
  today's order: aesthetic at `OFFSET 60000` 314.3 ms (after `ANALYZE`); created at
  `OFFSET 90000` 138.2 ms. With a covering index for the inner query: 12.0 ms and 15.3 ms.
  This is the fallback for the sort columns that have no index.
- **The one case `ROWID` loses**: with a narrow subfolder filter the planner still walks
  the aesthetic index and filters (27.3 ms against 21.8 ms).

### Counts and the Stats cache check

| Query (photo DB, 100k rows) | Time |
|---|---|
| `count(id)`, one dataset | 291.9 ms |
| `count(*)`, one dataset | 3.4 ms |
| `count(id)`, one subfolder | 14.8 ms |
| `count(*)`, one subfolder | 0.1 ms |
| Validator `count(id), max(updated_at)` | 294.0 ms |
| Validator as `count(*), max(updated_at)` with an index on `(dataset_id, updated_at)` | 7.1 ms |

`count(id)` reads the table once per row, because `id` is in no index the planner picks.
`count(*)` is answered from the index alone (`USING COVERING INDEX`).

### The Stats page's passes

| Pass (100k rows) | Today's layout | Split layout |
|---|---|---|
| Validator (runs twice per page open) | 294.0 ms | 249.9 ms |
| `get_dataset_stats` 15-column row fetch | 1,231.6 ms | 460.7 ms |
| `get_score_values` 14-column row fetch | 1,218.6 ms | 456.0 ms |
| Coverage counts (columns before the blobs) | 295.2 ms | 251.8 ms |
| `aesthetic_model` group (a column after the blobs) | 1,034.3 ms | 269.2 ms |
| Eight flag sums (`json_extract`) | 333.8 ms | 288.0 ms |
| Embedding count | 298.9 ms | 252.1 ms |
| License group | 320.6 ms | 271.1 ms |
| **All nine, as the page runs them** | **about 5.3 s** | **about 2.7 s** |
| One aggregate query: every histogram, count, mean and flag | 1,377.1 ms | 528.3 ms |
| The same, forced to table order (`NOT INDEXED`) | 1,105.3 ms | 325.4 ms |

- **Binning in SQL is not faster than fetching the rows** (1,377 ms against 1,232 ms). The
  cost is reaching the columns that sit after the blobs: a pass that reads only columns
  before them takes about 0.3 s, one that reads any column after them about 1.0–1.4 s.
- **One pass against nine** is the saving that needs no schema change: about 5.3 s to 1.4 s.
- **The split layout** takes that one pass to 0.33–0.53 s.
- **ComfyUI DB, 20k rows:** the one aggregate pass took 707.6 ms, about 35 µs a row against
  14 µs on the photo DB, because each row's overflow chain is about 77 KB long.

### Row width on the read paths

| Measure | Value |
|---|---|
| One gallery page of 100 ComfyUI rows, text columns | 5.6 MB |
| `json.loads` of that page's JSON columns | 4.3 ms (0.3 ms without `generation_metadata`) |
| 2,000 lookups by id, every non-deferred column, photo DB | 86.0 ms |
| The same, ComfyUI DB | 136.7 ms |
| 2,000 lookups by id, `thumbnail_path, file_path` only, either DB | 55–58 ms |

### What this run does not cover

Cold-cache times, 250k and 1M rows, several datasets of mixed size, filters beyond one
subfolder, the ORM and HTTP layers, and peak memory. Those are stage 6a and 6b.
