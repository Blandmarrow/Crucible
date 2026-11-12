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

The harness was rebuilt for M9 stage 6a (2026-11-10). It now builds the schema with
`alembic upgrade head`, seeds the realistic shape of the 2026-11-09 addendum below, and
times the app's own endpoints through `httpx.ASGITransport`. Each statement a case issues
is replayed on raw `sqlite3` for its time and its query plan. The module docstring says
what each step does and why.

```
python -m backend.scripts.bench_scaling --profile photo --sweep 100000,250000
python -m backend.scripts.bench_scaling --profile comfy --sweep 20000 --optimize
python -m backend.scripts.bench_scaling --profile photo --sweep 100000 --only stats   # one group
python -m backend.scripts.bench_scaling --profile photo --sweep 100000 --reuse-db \
    --json .claude/local/bench/after.json --compare .claude/local/bench/before.json
```

| Flag | What it does |
|---|---|
| `--profile photo\|comfy` | `generation_metadata` at ~400 B or ~55 KB a row |
| `--schema alembic\|create_all` | the migrated layout (default) or the model's declaration order |
| `--neighbour-rows N` | a second dataset, so the dataset filter really filters |
| `--only` | groups: `gallery`, `counts`, `stats`, `lookups` (the default set), and the legacy `dedup`, `similarity`, `thumb` |
| `--optimize` | measure again after `PRAGMA optimize=0x10002`, then drop the statistics |
| `--cold` | evict the DB from the OS cache before every request and every replayed statement (Windows, or Linux as root); a failed eviction is counted in the report |
| `--reuse-db` | keep the seeded file and reuse it; it is upgraded to `head` first |
| `--json`, `--compare` | write every number; print the change against an earlier file |
| `--reps`, `--lookups`, `--thumbs`, `--no-mem` | repeats, sample sizes, and skipping the `tracemalloc` pass |

The old `--embed-cap`, `--embed-dim` and `--db-path` are gone: every scored row carries all
three blobs at their real sizes, and the files live in `--work-dir`. Keep result files out
of the repo (`.claude/local/` is gitignored), since the JSON records the machine.
`backend/tests/test_bench_scaling_smoke.py` runs the harness at a few hundred rows in CI, so
a route that moves breaks a test instead of the next measurement.

`backend/scripts/bench_split_layout.py` (M9 stage 7) is a second tool that starts where
this one stops. It takes a file `--reuse-db` kept, rebuilds it with the big columns
somewhere else (compacted only, big columns last, embeddings in a side table,
`generation_metadata` moved too), and replays the app's captured Stats statements on each
copy, warm and with `--cold`, under SQLite's own index choice and a forced one. Its module
docstring has the method, and `backend/tests/test_bench_split_layout_smoke.py` keeps it
running. Its numbers are the 2026-11-11 stage 7 addendum.

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

## Addendum 2026-11-10 — stage 6a baseline, before stages 4 and 11

M9 stage 6a's harness, run as § Re-running describes. These are the "before" numbers that
stage 6b compares against once stages 4 and 11 land (`docs/dev/scale-plan/README.md`
§ Stage 6). `main` was at `e2131aa`, migration head `c3e7a9d1f5b2`.

**Method.**

- Three runs: `--profile photo --sweep 100000,250000 --optimize`,
  `--profile comfy --sweep 20000 --optimize`, and `--profile photo --sweep 100000 --cold`.
  All with `--reuse-db`; the seeded files and the JSON are kept outside the repo for 6b.
- The schema comes from `alembic upgrade head`: the blobs are columns 27–29 of 48, and 19
  columns follow them, as in the 2026-11-09 run. One dataset, no neighbour rows.
- Seeded as that run's method says: 70% scored with all three blobs, 80% captioned at ~270
  characters, `generation_metadata` ~400 B (photo, 1.71 GB at 100k, 4.29 GB at 250k) or
  ~55 KB (ComfyUI, 1.36 GB at 20k). `sort_order` is NULL on every row.
- Every time is the best of three after a first call, through the app's own endpoints
  (`httpx.ASGITransport`, no network). "SQL" is the statement replayed on raw `sqlite3`.
  "Analyzed" is after `PRAGMA optimize=0x10002`; the statistics were dropped afterwards.
  "Cold" evicts the file from the OS cache before every request (11,393 evictions, none
  failed).
- The owner's Windows dev box, Python 3.12, SQLite 3.49.1. Compare rows with each other and
  with the 2026-11-09 addendum, not with the July tables.

### Gallery (`GET /images/`, limit 100), in ms

A deep page is 60% in: page 601 at 100k, 1,501 at 250k, 121 for ComfyUI.

| Sort | Photo 100k | Photo 250k | ComfyUI 20k | 100k analyzed | 250k analyzed | 100k cold |
|---|---|---|---|---|---|---|
| aesthetic desc, page 1 | 6.9 | 7.0 | 37.8 | 6.9 | 7.0 | 139.0 |
| aesthetic desc, deep | 10.8 | 16.7 | 41.9 | 10.7 | 16.6 | 259.3 |
| created desc, page 1 | 6.0 | 6.3 | 40.8 | 6.3 | 6.5 | 42.0 |
| created desc, deep | 11.3 | 18.5 | 42.3 | 10.8 | 19.1 | 222.5 |
| filename asc, deep | 10.5 | 14.6 | 45.1 | 9.9 | 15.1 | 192.3 |
| sort_order asc, page 1 | 6.1 | 6.2 | 40.8 | **238.2** | **600.2** | 41.5 |
| color desc, page 1 (no index) | 142.2 | 350.1 | 86.9 | 242.6 | 616.9 | 8,106.2 |
| color desc, deep | 281.8 | 1,065.0 | 98.4 | 385.9 | 1,361.8 | 8,582.1 |
| width desc, deep | 191.8 | 485.5 | 86.7 | 309.1 | 828.8 | 8,357.1 |
| updated_at desc, deep | 335.9 | 1,162.9 | 94.4 | 452.9 | 1,598.8 | 8,754.5 |
| luminance desc, page 1 (after the blobs) | 662.8 | 1,699.2 | 481.3 | 815.1 | 2,004.9 | 4,787.8 |
| luminance desc, deep | 821.0 | 2,504.0 | 492.6 | 943.4 | 2,865.2 | 4,830.4 |
| nsfw desc, deep (after the blobs) | 856.6 | 2,500.0 | 515.2 | 943.8 | 2,773.1 | 4,879.5 |
| aesthetic desc, page 1, 20-row subfolder | **687.3** | **1,793.6** | 409.1 | 684.7 | 1,865.8 | **10,910.8** |

- **The indexed sorts hold.** Every one read its index with no sort step, at both sizes:
  about 6 ms for page 1 and 10–19 ms for a deep page. The id statement of the aesthetic
  deep page took 4.1 ms of SQL at 100k, against 4.5 ms for the 2026-11-09 `ROWID` row. That
  is the reproduction check, and it agrees.
- **An unindexed sort costs about 140 ms at 100k and 340 ms at 250k for page 1**, nearly all
  of it SQL. If the column sits after the blobs (`luminance_score`, `nsfw_score`), it costs
  five times that: 0.66 s and 1.7 s.
- **A score sort inside a small subfolder is the worst gallery case: 0.69 s at 100k, 1.8 s at
  250k, 10.9 s cold.** SQLite walks the score's index and reads each row's `subfolder` to
  filter it. On a migrated file that column is after the blobs. With 20 matching rows a
  page of 100 never fills, so the walk covers the whole dataset. The cost falls with the
  subfolder's share: page 1 took 668 ms for 20 rows, 8.5 ms for a subfolder of 9,916 rows
  and 1.8 ms for 29,671 (raw SQL, 100k). The 2026-11-09 row (27 ms, "one subfolder of 20")
  fits a subfolder of about a twentieth of the dataset, so it measured the middle of this
  range rather than its end. The equality form `subfolder = ?` takes the same plan and the
  same time, so the app's subtree filter is not the cause.
- **`ANALYZE` makes the gallery worse.** After it, the planner reads every unindexed sort,
  and `sort_order` too, through `ix_images_dataset_caption_tokens` and then sorts. `sort_order`
  loses its index (6 ms becomes 238 ms at 100k), and each unindexed sort gets about 100 ms
  slower at 100k and 250 ms slower at 250k. The app never runs it, and these numbers are a
  reason to keep it that way. `sort_order` being NULL on every seeded row may matter here.
- **A ComfyUI page costs 38–45 ms on any indexed sort, against 6 ms for photos**, and is
  5.3 MB against 131 KB. That is `generation_metadata` in the list response, stage 11's
  item.
- **Cold, the indexed pages cost 40–260 ms and the unindexed ones 5–9 s.**

### Counts, Stats and lookups, in ms

| Case | Photo 100k | Photo 250k | ComfyUI 20k | 100k analyzed | 100k cold |
|---|---|---|---|---|---|
| `GET /images/count` | 5.6 | 12.7 | 1.9 | 5.8 | 189.4 |
| The same, one subfolder (30%) | 9.1 | 21.5 | 2.8 | 9.2 | 202.2 |
| Stats, cold cache entry | 4,200.3 | 10,584.4 | 1,600.0 | 3,451.7 | 20,816.2 |
| Stats, cached poll | 229.7 | 591.6 | 55.6 | 129.3 | 10,209.9 |
| Score values, cold cache entry | 2,142.7 | 5,543.4 | 743.3 | 1,930.0 | 18,778.4 |
| Score values, cached poll | 653.5 | 1,692.3 | 136.2 | 552.1 | 10,697.0 |
| `GET /images/{id}`, per request (2,000) | 2.69 | 2.80 | 3.14 | 2.70 | 6.71 |
| Thumbnail, per request (500) | 2.07 | 2.19 | 2.24 | 2.07 | 5.80 |

| Stats statement (100k, SQL ms) | 2026-11-09 | 2026-11-10 |
|---|---|---|
| Validator `count(*), max(updated_at)` | 294.0 | 226.0 |
| 15-column row fetch | 1,231.6 | 996.5 |
| Coverage counts | 295.2 | 229.2 |
| `aesthetic_model` group | 1,034.3 | 804.2 |
| Eight flag sums | 333.8 | 279.0 |
| Embedding count | 298.9 | 230.9 |
| License group | 320.6 | 310.8 |

- **The Stats statements reproduce the 2026-11-09 shape** at about 20% less time each: the
  statements that read a column after the blobs cost 3.5 to 4.4 times the ones that do not. The endpoint adds
  about 1.1 s of Python at 100k on top of 3.1 s of SQL.
- **A cached poll is not cheap.** Stats spends its 230 ms (590 ms at 250k) on the validator
  alone, which is stage 11's index. Score values spends 654 ms (1.7 s), because the cached
  payload is still 7.6 MB of JSON (19.1 MB at 250k), serialized again on every poll. That is
  stage 4's.
- **Memory:** a cold Stats call peaked at 83 MB of Python allocation at 100k and 209 MB at
  250k (`tracemalloc`), and score values at 82 MB and 204 MB. That is about 0.83 KB per
  image, or roughly 0.8 GB at 1M, the figure stage 4 removes.
- **Cold, the validator alone takes about 10 s at 100k**, so opening the Stats page on a cold
  cache costs 21 s.
- **A lookup by id costs 2.7 ms, of which under 0.1 ms is SQL.** The rest is the ORM, the
  response model and the transport. A ComfyUI lookup returns 53 KB.

### What this run does not cover

1M rows (6b), several datasets of mixed size (`--neighbour-rows` was 0), a dataset with a
real manual order, and the legacy `dedup`, `similarity` and `thumb` groups, which 6b runs.
Cold numbers swing with how SQLite walks the file. Read them as an order of magnitude, not a
precise figure.

## Addendum 2026-11-11 — stage 6b, after stages 4 and 11

M9 stage 6b: 6a's runs again, after stage 4 (PR #139) and stage 11 (PR #138), plus the first
1M run and the similarity group (`docs/dev/scale-plan/README.md` § Stage 6). `main` was at
`fc23246`, migration head `d5f1a8c3e7b2` (stage 11's index).

**Method.**

- 6a's three runs with the same flags, each with `--compare` against 6a's JSON:
  `--profile photo --sweep 100000,250000 --optimize`, `--profile comfy --sweep 20000
  --optimize`, and `--profile photo --sweep 100000 --cold --no-mem`.
- Three new runs: `--profile photo --sweep 1000000` (warm), the same with
  `--cold --no-mem --only counts,stats --reps 1`, and `--only similarity` at 100k, 250k
  and 1M.
- All with `--reuse-db`. The 6a files were reused, not reseeded, and the upgrade to `head`
  built `ix_images_dataset_updated_at` on the full table, so that index is compact on disk.
  The 1M file was seeded fresh at `head` (215 s, 18.5 GB), its index built row by row like
  the others.
- Seeding, timing and machine as in 6a's addendum: best of three after a first call (one
  for the 1M cold run), the owner's Windows dev box, Python 3.12, SQLite 3.49.1. The cold
  runs made 11,393 and 32 evictions, none failed.
- The second run of a multi-size `--compare` crashed (`'str' object is not callable`) after
  its JSON was written. That was a harness bug, fixed with a test on the same branch; these
  numbers come from the JSON, not the printed comparison.

### Gallery (`GET /images/`, limit 100), before → after, in ms

A deep page is 60% in, as in 6a: page 6,001 at 1M. The 1M column has no "before".

| Sort | Photo 100k | Photo 250k | ComfyUI 20k | 100k cold | 1M (new) |
|---|---|---|---|---|---|
| aesthetic desc, page 1 | 6.9 → 6.8 | 7.0 → 6.5 | 37.8 → 9.4 | 139.0 → 132.0 | 7.9 |
| aesthetic desc, deep | 10.8 → 10.3 | 16.7 → 16.3 | 41.9 → 9.7 | 259.3 → 261.2 | 45.4 |
| created desc, page 1 | 6.0 → 5.9 | 6.3 → 6.1 | 40.8 → 8.8 | 42.0 → 42.2 | 6.1 |
| created desc, deep | 11.3 → 11.1 | 18.5 → 18.9 | 42.3 → 9.8 | 222.5 → 216.7 | 57.1 |
| filename asc, deep | 10.5 → 9.7 | 14.6 → 14.3 | 45.1 → 9.4 | 192.3 → 190.6 | 41.5 |
| sort_order asc, page 1 | 6.1 → 5.8 | 6.2 → 6.2 | 40.8 → 9.3 | 41.5 → 43.5 | 6.4 |
| **updated_at desc, page 1** (stage 11's index) | 141.6 → 6.5 | 342.3 → 6.7 | 86.0 → 9.6 | 7,819.2 → 122.3 | 6.7 |
| **updated_at desc, deep** | 335.9 → 9.8 | 1,162.9 → 13.6 | 94.4 → 11.3 | 8,754.5 → 189.0 | 53.7 |
| color desc, page 1 (no index) | 142.2 → 137.1 | 350.1 → 329.5 | 86.9 → 50.6 | 8,106.2 → 8,134.0 | 1,378.8 |
| color desc, deep | 281.8 → 279.8 | 1,065.0 → 1,116.1 | 98.4 → 60.5 | 8,582.1 → 8,678.4 | 6,346.7 |
| width desc, deep | 191.8 → 187.1 | 485.5 → 465.3 | 86.7 → 50.5 | 8,357.1 → 8,290.4 | 1,979.2 |
| luminance desc, page 1 (after the blobs) | 662.8 → 656.9 | 1,699.2 → 1,606.3 | 481.3 → 426.3 | 4,787.8 → 4,776.8 | 6,670.2 |
| luminance desc, deep | 821.0 → 815.4 | 2,504.0 → 2,430.5 | 492.6 → 434.4 | 4,830.4 → 4,830.5 | 13,432.4 |
| nsfw desc, deep (after the blobs) | 856.6 → 800.4 | 2,500.0 → 2,435.8 | 515.2 → 458.8 | 4,879.5 → 4,891.0 | 13,599.7 |
| aesthetic desc, page 1, 20-row subfolder | 687.3 → 656.4 | 1,793.6 → 1,751.2 | 409.1 → 389.8 | 10,910.8 → 11,132.9 | 7,609.9 |
| Payload of a page, KB | 136 → 99 | 135 → 98 | 5,306 → 98 | 136 → 99 | 99 |

- **The run reproduces 6a.** Every untouched sort in this table is within 9% of 6a warm
  and 5% cold, at both photo sizes. Across all 81 gallery cases, untouched ones moved by
  up to 7% at 250k and by −15% to +17% at 100k; every swing past 9% is on a time under
  15 ms.
  Six of 6a's cold cases came out at warm speed (`uniformity_score desc` page 1 took
  156 ms, against 7.7 s for its `asc` twin; the `style_similarity` sorts took 8–14 ms), so
  6a's eviction did not take for them and they do not compare. 6b's cold run has no such
  case.
- **Stage 11's index gave the `updated_at` sort its index**: a deep page went from 336 ms to
  10 ms at 100k, from 1.2 s to 14 ms at 250k, and from 8.8 s to 0.19 s cold.
- **A ComfyUI page went from about 40 ms (37–45) to about 10 ms on any indexed sort**, and
  from 5,306 KB to 98 KB, now that the list sends `has_generation_metadata` instead of the
  JSON. Its unindexed and after-blob sorts gained 34–58 ms each for the same reason, and
  the small-subfolder page 19 ms. A photo page dropped from
  136 KB to 99 KB. Stage 11's acceptance for the ComfyUI page is met.
- **1M: the indexed sorts hold.** Page 1 costs 6–8 ms and a deep page 41–57 ms. That is the
  figure decision 13's cursor-paging amendment asked for: numbered pages still cost tens of
  milliseconds at 1M.
- **1M: everything without an index is slow.** An unindexed sort costs 1.4 s for page 1 and
  2–6 s deep. One on a column after the blobs costs 6.7 s and 13.4–13.6 s. The score sort
  inside a 20-row subfolder costs 7.6 s.
- **`ANALYZE` now also reaches for stage 11's index.** After `PRAGMA optimize`, the
  unindexed sorts and `sort_order` filter by dataset through `ix_images_dataset_updated_at`
  and then sort, and come out about 1.2–2.1× slower than in 6a's analyzed pass, the deep
  pages worst (width deep: 309 → 450 ms at 100k, 829 → 1,579 ms at 250k). The after-blob
  sorts, already slow, moved 1.06–1.15×. The app never runs `ANALYZE`; this is one
  more reason to keep it that way.

### Counts, Stats and lookups, before → after, in ms

| Case | Photo 100k | Photo 250k | ComfyUI 20k | 100k cold | 1M (new) | 1M cold (new) |
|---|---|---|---|---|---|---|
| `GET /images/count` | 5.6 → 5.5 | 12.7 → 12.5 | 1.9 → 1.6 | 189.4 → 25.9 | 76.9 | 2,675.7 |
| The same, one subfolder (30%) | 9.1 → 9.1 | 21.5 → 22.2 | 2.8 → 2.2 | 202.2 → 200.2 | 83.4 | 1,936.3 |
| Stats, cold cache entry | 4,200.3 → 4,508.7 | 10,584.4 → 11,737.3 | 1,600.0 → 1,689.7 | 20,816.2 → 18,041.1 | 50,454.0 | 184,248.8 |
| Stats, cached poll | 229.7 → 13.3 | 591.6 → 31.3 | 55.6 → 3.4 | 10,209.9 → 27.0 | 146.7 | 2,673.8 |
| Score values, cold cache entry (on demand since stage 4) | 2,142.7 → 1,921.9 | 5,543.4 → 4,928.4 | 743.3 → 777.9 | 18,778.4 → 14,852.4 | 20,840.7 | 147,646.0 |
| Score values, cached poll | 653.5 → 408.3 | 1,692.3 → 1,024.9 | 136.2 → 79.3 | 10,697.0 → 416.5 | 4,247.1 | 6,622.2 |
| `GET /images/{id}`, per request (2,000) | 2.69 → 2.55 | 2.80 → 2.53 | 3.14 → 3.04 | 6.71 → 6.59 | 2.70 | - |
| Thumbnail, per request (500) | 2.07 → 1.90 | 2.19 → 1.90 | 2.24 → 2.03 | 5.80 → 5.24 | 2.03 | - |

- **The Stats cache check costs almost nothing now.** The validator reads
  `ix_images_dataset_updated_at` as a covering index: 226 ms of SQL becomes 8 ms at 100k,
  and a cached poll costs 13 ms (31 ms at 250k, 147 ms at 1M). Cold, a cached poll went
  from 10.2 s to 27 ms at 100k. Stage 11's acceptance for the validator is met. The score
  values' cached poll fell by the same validator time; its 7,649 KB payload did not
  change.
- **A Stats page load is now the `stats` line alone.** Stage 4 fetches the score values
  only for a saved custom edge, an open pencil editor, or not at all, so their 2–5 s at
  100k–250k (21 s at 1M) is no longer part of a plain load.
- **`stats` itself costs 6–11% more warm**, from two causes. The time beyond SQL rose by
  about 15% (1.12 s → 1.29 s at 100k, 2.78 s → 3.18 s at 250k), which is `score_means`
  summing nine columns in the row loop. The SQL rose 5% at 100k and 10% at 250k, although
  the validator inside it got 0.2–0.6 s cheaper: the scans now walk stage 11's index (next
  section). In the analyzed pass the scans read the table straight through
  (`SCAN images`), and there the SQL fell by 10%.
- **At 1M, opening Stats costs 50 s warm and about 3 minutes cold.** It holds 836 MB of
  Python allocation at its peak (score values: 816 MB and a 76,457 KB payload), about
  0.84 KB per image, as 6a projected.
- **The thumbnail route got 8–13% faster** per request after it began reading one column.
  The lookup by id, which no stage changed and which still loads the whole row, got 2–10%
  faster too, so only part of the thumbnail's gain can be put down to the narrow read. A
  cold count got faster
  (189 → 26 ms) because it now reads the new index, which the upgrade built compact; a
  fragmented one would gain less.

### A new cost: the Stats scans walk stage 11's index

With no `ANALYZE`, SQLite now picks `ix_images_dataset_updated_at` for every Stats
statement filtered by dataset, not just the validator. A scan that reads every row of the
dataset visits them in `updated_at` order, which is random order in the table. The index
it used before, `ix_images_dataset_caption_tokens`, has long runs of equal keys (every
uncaptioned row, and clustered counts) that walk the table in rowid order. Warm, each Stats
row scan on the photo files got 8–36% slower; on the ComfyUI file only the row fetch moved
(+18%). Cold, the row fetch went from 8.2 s to 14.6 s and the
`aesthetic_model` group from 8.0 s to 14.2 s at 100k. A cached poll, which runs only the
validator, is far cheaper everywhere. A full Stats computation is cheaper cold at 100k
(20.8 s → 18.0 s), because the validator's 10 s saving outweighs the slower scans, but
warm it is 5–10% dearer in SQL, as above.

Forcing each index with `INDEXED BY` on the 100k file (the last two rows on the 1M file,
whose index was built row by row), the same statement, cold (evicted first) and warm (best
of two), in ms:

| Statement | `caption_tokens` (6a) | `updated_at` (now) | `ix_images_dataset_id` |
|---|---|---|---|
| Row fetch, cold | 7,909 | 14,073 | 4,625 |
| Row fetch, warm | 978 | 1,082 | 829 |
| Coverage counts, cold | 9,924 | 10,130 | 7,612 |
| Coverage counts, warm | 235 | 293 | 133 |
| `aesthetic_model` group, cold | 7,559 | 13,512 | 4,538 |
| `aesthetic_model` group, warm | 775 | 897 | 641 |
| Row fetch at 1M, cold | 78,174 | 138,667 | 45,204 |
| Row fetch at 1M, warm | 9,814 | 11,531 | 8,353 |

`ix_images_dataset_id` holds the dataset's rows in rowid order, so it reads the table nearly
in sequence and beats both. Pinning it is left to stage 7, which times the scans again with
the blobs moved out (`docs/dev/scale-plan/README.md` § Stage 7).

### Similarity (legacy group, direct numpy)

`bench_similarity` loads every final DINO vector with raw `sqlite3` and scores two
references against them. It does not control the cache, so the load is reported warm: a
second run in a row at 100k and 1M, and at 250k a file that run A had read and nothing had
evicted since (one run only):

| | 100k | 250k | 1M |
|---|---|---|---|
| Vectors | 70,099 | 175,395 | 700,464 |
| Load, warm | 0.5 s | 1.3 s | 5.8 s |
| Score | 0.23 s | 0.57 s | 2.36 s |

Both grow linearly. A load straight after a cold run, with the file mostly out of the
cache, took 19.6 s at 100k and 198 s at 1M; read that as an order of magnitude, since the
group does not evict.

### For stage 7

- **Warm, the overflow cost holds at 1M.** The row fetch and the `aesthetic_model` group,
  which read a non-NULL column after the blobs on every scored row, take 11.8 s and 9.7 s.
  The coverage counts, the embedding count and the flag sums, which read nothing past the
  blobs, take 3.6–4.1 s. The license group sits between (4.5 s warm, 132 s cold):
  `license` is after the blobs but set on only 10% of seeded rows, and SQLite reads a NULL
  from the row's header without walking its overflow chain.
- **Cold, every scan pays it.** At 1M the coverage counts and the embedding count took
  107 s each and the flag sums 109 s, against 142–144 s for the row fetch and the model
  group. A photo row is about 18 KB, so each row fills a leaf page of its own, and
  any scan of the dataset touches one page per row. Moving the blobs out of the row is what
  would put many rows on a page; that is the question stage 7 decides.
- The side-table experiment itself (the 2026-11-09 by-hand run, at 1M and cold) is left to
  stage 7. The kept 1M file is in the bench work folder for it.

### What this run does not cover

`--optimize` and the cold gallery at 1M, several datasets of mixed size, a real manual
order, the legacy `dedup` and `thumb` groups, and a fragmented `ix_images_dataset_updated_at`
at 100k–250k (only the 1M file has one).

## Addendum 2026-11-11 — stage 7, the row-layout experiment

M9 stage 7's measurement (`docs/dev/scale-plan/README.md` § Stage 7): what the Stats
statements would cost with the big columns somewhere else. Run with
`backend/scripts/bench_split_layout.py`, new for this stage, on `main` at `0bb7544`,
migration head `d5f1a8c3e7b2`.

```
python -m backend.scripts.bench_split_layout --db <work dir>/bench-photo-100000-nb0-alembic.db \
    --cold --cold-reps 3 --json <out>.json
```

**Method.**

- The script takes a seeded file that `bench_scaling.py --reuse-db` kept, and builds five
  copies of it beside the original (`row`):
  - `row_vacuum`: the same rows after a `VACUUM`. **The control.** Every other copy is
    vacuumed, which also rebuilds each index compact, so a layout is compared with this.
  - `blobs_last`: no side table; `images` rebuilt with the four big columns at the end.
  - `split3`: the three embedding blobs in a table of their own, `image_embeddings`,
    filled in the images table's rowid order (the file straight after a migration).
  - `split3_shuffled`: the same, filled in id order, random against rowid (a file whose
    embeddings were all written by later scoring runs).
  - `split4`: `generation_metadata` moved out too, to a second side table.
- The statements are the app's own, captured once with their parameters: everything the
  two Stats endpoints issue, the id statement of a few gallery pages, and one page's row
  load. Style scoring's keyset pages are compiled from the construct `_score_paginated`
  builds. Each is replayed on raw `sqlite3`, fetching every row.
- The Stats statements run under three index choices: SQLite's own ("own" below),
  `INDEXED BY ix_images_dataset_id` ("forced"), and `NOT INDEXED`. The last came out within
  a few percent of "forced" everywhere, because these files hold one dataset; it is not in
  the tables, and it is no option for an install with several datasets.
- A statement that names a moved column joins the side table (the embedding count, the
  similarity pages, the page row load on `split4`). Nothing was skipped.
- Every copy is built before any is measured, with a 300 s wait in between. Warm is the
  best of three after a first call. Cold evicts the file before every statement: best of
  three at 20k and 100k, one run at 1M.
- Before a statement is timed its rows are counted and checksummed, and each layout's big
  columns are checksummed whole through the joins. Every layout and index choice returned
  the same rows in all eight runs (two on the Windows machine; three warm-only and three
  cold on the Linux one). 2,730 evictions, none failed.
- Two machines. The Windows dev box of the earlier addenda (a SATA SSD, SQLite 3.49.1) ran
  the ComfyUI 20k and photo 100k files. A Linux machine (an NVMe SSD, SQLite 3.53.1, about
  three times faster warm) ran both again and the photo 1M file. **Every 1M figure is the
  Linux machine's.** Its files were seeded there with the same fixed seed. Both machines
  chose the same plans. Compare a layout with another on the same machine, never a time
  across the two.
- A copy is built with the journal off, so the build times below are a floor for a real
  migration, not an estimate of one.

### What each layout looks like

`dbstat` on the Linux machine (its SQLite has the table; the Windows one does not):

| Layout | Photo 1M: GB | Rows per leaf page | Overflow pages | ComfyUI 20k: rows per leaf page | Build at 1M, s |
|---|---|---|---|---|---|
| `row` | 17.19 | 1.38 | 3,502,320 | 1.31 | - |
| `row_vacuum` | 17.10 | 1.38 | 3,502,320 | 1.31 | copy 2, vacuum 14 |
| `blobs_last` | 17.10 | 1.38 | 3,502,320 | 1.31 | copy 21, rebuild 108, vacuum 64 |
| `split3` | 16.62 | 2.81 | 0 | 1.00 | copy 23, split 142, vacuum 44 |
| `split3_shuffled` | 16.62 | 2.81 | 0 | 1.00 | copy 22, split 254, vacuum 31 |
| `split4` | 16.61 | 4.29 | 0 | 4.39 | copy 35, split 80, vacuum 15 |

- **Stage 6b's "a row fills a leaf page of its own" was close: it is 1.38 rows a page**,
  with five overflow pages behind each scored row.
- **Even with both moved out, about four rows share a page.** The caption and the two file
  paths leave a row near 1 KB.
- **On ComfyUI rows, moving the embeddings alone leaves one row a page**: the 55 KB of
  `generation_metadata` still overflows.

### Stats statements, photo 1M, warm, ms

| Statement | Index | row | row_vacuum | blobs_last | split3 | split3_shuffled | split4 |
|---|---|---|---|---|---|---|---|
| validator | own | 59 | 55 | 55 | 55 | 55 | 57 |
| row fetch (stats) | own | 3,333 | 3,244 | 2,055 | 1,913 | 1,913 | 1,857 |
| coverage counts | own | 839 | 810 | 811 | 649 | 653 | 597 |
| `aesthetic_model` group | own | 2,171 | 2,061 | 887 | 728 | 733 | 676 |
| flag sums | own | 1,156 | 1,128 | 1,119 | 963 | 980 | 914 |
| embedding count | own | 828 | 796 | 802 | 2,039 | 2,118 | 1,980 |
| license group | own | 1,109 | 1,069 | 940 | 783 | 786 | 731 |
| row fetch (score-values) | own | 3,220 | 3,140 | 1,945 | 1,804 | 1,797 | 1,734 |
| validator | forced | 338 | 332 | 333 | 157 | 158 | 119 |
| row fetch (stats) | forced | 2,614 | 2,595 | 1,633 | 1,462 | 1,458 | 1,414 |
| coverage counts | forced | 351 | 346 | 348 | 175 | 174 | 137 |
| `aesthetic_model` group | forced | 1,419 | 1,402 | 421 | 248 | 249 | 206 |
| flag sums | forced | 667 | 674 | 662 | 485 | 492 | 441 |
| embedding count | forced | 339 | 333 | 343 | 882 | 1,514 | 836 |
| license group | forced | 592 | 590 | 474 | 302 | 304 | 262 |
| row fetch (score-values) | forced | 2,495 | 2,487 | 1,525 | 1,349 | 1,343 | 1,292 |

### Stats statements, photo 1M, cold, ms

| Statement | Index | row | row_vacuum | blobs_last | split3 | split3_shuffled | split4 |
|---|---|---|---|---|---|---|---|
| validator | own | 1,608 | 63 | 53 | 64 | 62 | 52 |
| row fetch (stats) | own | 144,219 | 94,578 | 51,669 | 3,785 | 2,989 | 2,728 |
| coverage counts | own | 60,040 | 44,565 | 50,832 | 2,888 | 1,921 | 1,731 |
| `aesthetic_model` group | own | 143,516 | 93,610 | 50,966 | 2,987 | 1,999 | 1,840 |
| flag sums | own | 60,154 | 44,665 | 50,986 | 3,006 | 2,160 | 1,950 |
| embedding count | own | 60,009 | 44,566 | 50,867 | 22,522 | 19,320 | 21,551 |
| license group | own | 76,093 | 54,260 | 51,007 | 3,036 | 2,050 | 1,895 |
| row fetch (score-values) | own | 144,254 | 94,450 | 51,633 | 3,688 | 2,907 | 2,622 |
| validator | forced | 59,697 | 16,502 | 15,161 | 1,825 | 1,080 | 1,306 |
| row fetch (stats) | forced | 23,414 | 14,114 | 15,570 | 2,635 | 1,975 | 2,138 |
| coverage counts | forced | 59,708 | 16,595 | 15,208 | 1,830 | 1,058 | 1,319 |
| `aesthetic_model` group | forced | 22,516 | 12,737 | 15,764 | 1,938 | 1,146 | 1,412 |
| flag sums | forced | 59,708 | 16,807 | 15,189 | 1,957 | 1,268 | 1,452 |
| embedding count | forced | 59,675 | 16,596 | 15,178 | 19,118 | 17,947 | 18,833 |
| license group | forced | 57,906 | 15,050 | 15,356 | 1,996 | 1,183 | 1,456 |
| row fetch (score-values) | forced | 23,309 | 13,959 | 15,541 | 2,523 | 1,891 | 2,048 |

- **The run reproduces 6b.** The row fetch on `row` under SQLite's own index took 144 s
  cold, against 139–144 s for 6b on the other machine and another disk.
- **The control matters at 1M and not below.** `VACUUM` alone took the cold row fetch
  from 144 s to 95 s, and the validator from 1.6 s to 63 ms: the `row` file's
  `ix_images_dataset_updated_at` was built row by row and is scattered on disk. At 20k and
  100k `row` and `row_vacuum` are within a few percent.
- **Moving the embeddings out is worth 15–31× cold and 1.2–2.8× warm on the scans**,
  against the control: 95 s → 3.8 s and 3.2 s → 1.9 s for the row fetch, 2.1 s → 0.73 s for
  the `aesthetic_model` group. `split4` adds a few percent on photo rows, whose metadata is
  400 bytes.
- **`blobs_last` fixes the warm cost of reading past the blobs and little else.** The two
  statements that read a non-NULL column after them gain warm (the `aesthetic_model` group
  2.1 s → 0.9 s, the row fetch 3.2 s → 2.1 s); cold, every scan still costs 51 s, because
  the rows are as large as before. And the seed flatters it (see what this run does not
  cover).
- **Forcing `ix_images_dataset_id` wins on every Stats scan in every layout**, the
  validator apart (it reads `ix_images_dataset_updated_at` as a covering index and must not
  be forced). On the control: 95 s → 14 s cold, 3.2 s → 2.6 s warm for the row fetch. After
  the split the gap is still there but small in absolute terms: 3.8 s → 2.6 s cold,
  1.9 s → 1.5 s warm, and 728 ms → 248 ms warm for the `aesthetic_model` group.
- **The split's cost is the join.** The embedding count is 2.6× slower warm (0.80 s →
  2.0 s; forced, 0.33 s → 0.88 s) and is the one statement the split leaves slow cold
  (19–23 s). Filling the side table in id order makes it 1.7× slower again warm under the
  forced index (0.88 s → 1.5 s) and changes nothing else.
- **Cold, on the untouched file, a scan that skips the blobs is slower than one that reads
  them**: forced, the coverage counts took 60 s and the row fetch 23 s. The Windows machine
  shows the same at 100k (8.0 s against 4.8 s). A guess at why, not checked: reading the
  overflow chains turns the scan into a sequential read of the file, and the leaf pages
  alone are one page in six.

### The slower disk: photo 100k on the Windows machine, cold, ms

| Statement | Index | row | row_vacuum | blobs_last | split3 | split3_shuffled | split4 |
|---|---|---|---|---|---|---|---|
| row fetch (stats) | own | 13,886 | 13,745 | 9,033 | 1,284 | 1,275 | 951 |
| coverage counts | own | 10,094 | 10,407 | 8,604 | 1,083 | 1,124 | 748 |
| `aesthetic_model` group | own | 13,502 | 13,351 | 8,583 | 1,106 | 1,105 | 771 |
| embedding count | own | 10,181 | 10,432 | 8,509 | 7,498 | 7,772 | 6,168 |
| row fetch (stats) | forced | 4,759 | 4,440 | 5,815 | 666 | 633 | 550 |
| coverage counts | forced | 8,000 | 6,437 | 5,437 | 473 | 474 | 345 |
| `aesthetic_model` group | forced | 4,795 | 4,385 | 5,468 | 483 | 503 | 355 |
| embedding count | forced | 8,011 | 6,462 | 5,232 | 4,844 | 6,135 | 4,280 |

- It reproduces 6b's forced-index table: 13.9 s and 4.8 s for the row fetch, against 14.1 s
  and 4.6 s.
- Warm, the row fetch goes 1,152 ms → 421 ms with the embeddings out (the control against
  `split3`), and the embedding count 296 ms → 673 ms.
- The Linux machine gives the same order on the same file: 13.8 s → 0.34 s cold for the row
  fetch, 320 ms → 171 ms warm.

### ComfyUI 20k on the Windows machine, SQLite's own index, ms

| Statement | Pass | row | row_vacuum | blobs_last | split3 | split3_shuffled | split4 |
|---|---|---|---|---|---|---|---|
| row fetch (stats) | warm | 578 | 590 | 98 | 482 | 485 | 74 |
| `aesthetic_model` group | warm | 455 | 468 | 64 | 345 | 340 | 40 |
| embedding count | warm | 50 | 51 | 52 | 103 | 99 | 90 |
| page rows (100 by id) | warm | 4.0 | 3.9 | 2.6 | 3.2 | 3.4 | 2.0 |
| row fetch (stats) | cold | 6,577 | 6,347 | 3,125 | 4,704 | 4,705 | 161 |
| `aesthetic_model` group | cold | 7,010 | 6,765 | 2,964 | 5,963 | 6,050 | 121 |
| embedding count | cold | 2,131 | 2,115 | 2,115 | 3,442 | 3,446 | 871 |
| page rows (100 by id) | cold | 121 | 117 | 130 | 116 | 120 | 141 |

- **On ComfyUI rows the embeddings are the smaller blob, and moving them alone does
  little**: 6.3 s → 4.7 s cold for the row fetch. Moving `generation_metadata` as well takes
  it to 0.16 s, and 590 ms → 74 ms warm.
- The page row load, the one statement here that reads `generation_metadata`, does not
  care where it lives. The Linux machine agrees on all of it (5.6 s → 5.2 s → 60 ms cold
  for the row fetch).

### Gallery and style similarity, photo 1M, ms

| Statement | Pass | row | row_vacuum | blobs_last | split3 | split3_shuffled | split4 |
|---|---|---|---|---|---|---|---|
| `aesthetic_score` desc, deep (indexed) | warm | 14 | 13 | 16 | 16 | 16 | 16 |
| `color_score` desc, deep | warm | 1,310 | 1,304 | 1,299 | 1,108 | 1,111 | 1,058 |
| `luminance_score` desc, page 1 | warm | 1,326 | 1,198 | 298 | 170 | 171 | 135 |
| `aesthetic_score` desc, page 1, 20-row subfolder | warm | 1,689 | 1,628 | 878 | 767 | 766 | 713 |
| keyset page of 2,000, clip + dino, first | warm | 998 | 973 | 974 | 2,249 | 2,331 | 2,194 |
| keyset page of 2,000, layers, first | warm | 1,386 | 1,348 | 1,356 | 2,642 | 2,734 | 2,609 |
| `color_score` desc, deep | cold | 59,944 | 16,898 | 15,842 | 2,325 | 1,680 | 1,846 |
| `luminance_score` desc, page 1 | cold | 22,392 | 12,613 | 15,212 | 1,836 | 1,097 | 1,314 |
| `aesthetic_score` desc, page 1, 20-row subfolder | cold | 121,185 | 81,237 | 50,460 | 20,235 | 14,848 | 13,933 |
| keyset page of 2,000, clip + dino, first | cold | 61,173 | 45,491 | 51,919 | 24,744 | 20,985 | 23,590 |
| keyset page of 2,000, layers, first | cold | 63,406 | 46,740 | 53,489 | 27,365 | 22,403 | 25,786 |

- **An unindexed gallery sort gains what a Stats scan gains**: the `luminance_score` page
  goes 1.2 s → 0.17 s warm and 12.6 s → 1.8 s cold with the embeddings out. An indexed sort
  does not move.
- **A style similarity page is 2.3× slower warm behind the join** (0.97 s → 2.2 s) and
  about twice as fast cold.
- **That page is slow in every layout, for a reason of its own.** Its plan is
  `SEARCH images USING INDEX ix_images_dataset_updated_at (dataset_id=?)` then
  `USE TEMP B-TREE FOR ORDER BY`: every page of 2,000 reads and sorts the whole dataset,
  so a scoring pass over 1M rows pays about a second 350 times over. Whether stage 11's
  index brought that plan in was not checked. It is a finding for the plan guard, not for
  the layout.
- One figure from the first, warm-only 1M run did not repeat and is left out: the legacy
  one-statement similarity load took 15.6 s on `split4`, and 2.8 s in the second run, the
  same as `split3`.

### A subfolder-scoped Stats call, photo 100k on the Windows machine, ms

Added on the same day, for decision 15's condition on the pin: the two Stats endpoints
again, scoped to one subfolder, on the `row` layout only. `set_a` holds 29,671 of the
100,000 rows and `small` holds 20. 249 evictions, none failed; the same rows under every
index.

| Statement | Subfolder | Own, warm | Forced, warm | Own, cold | Forced, cold |
|---|---|---|---|---|---|
| validator | `set_a` | 59 | 454 | 3,746 | 5,107 |
| row fetch (stats) | `set_a` | 278 | 512 | 4,897 | 5,137 |
| coverage counts | `set_a` | 59 | 456 | 3,686 | 5,138 |
| `aesthetic_model` group | `set_a` | 224 | 460 | 4,858 | 5,100 |
| flag sums | `set_a` | 73 | 468 | 3,812 | 5,138 |
| embedding count | `set_a` | 61 | 462 | 3,761 | 5,108 |
| license group | `set_a` | 81 | 461 | 4,322 | 5,111 |
| validator | `small` | <0.1 | 456 | 6 | 5,156 |
| row fetch (stats) | `small` | <0.1 | 455 | 19 | 5,131 |
| coverage counts | `small` | <0.1 | 454 | 6 | 5,109 |

- **SQLite's own choice wins on every statement, so a subfolder-scoped call is not
  pinned.** Its plan is
  `SEARCH images USING INDEX ix_images_dataset_subfolder (dataset_id=? AND subfolder=?)`,
  which reads that subfolder's rows and no others, in table order.
- **Forced, every statement costs what a scan of the whole dataset costs**, about 455 ms
  warm and 5.1 s cold whatever the subfolder's size, because `ix_images_dataset_id` knows
  nothing of subfolders.
- A 1M run of the same cases was started and stopped before its forced pass, so the
  subfolder case is timed at 100k only. The result does not rest on size: a forced index
  must read the dataset to answer for one folder.

### The pin as built, through the app, photo 100k on the Windows machine, ms

`bench_scaling.py --only stats --reuse-db` with the pin in the code, compared with stage
6b's JSON (`--compare`): the endpoints themselves, ORM and Pydantic included. The cold
run made 61 evictions, none failed.

| Case | 6b, warm | Pinned, warm | 6b, cold | Pinned, cold |
|---|---|---|---|---|
| Stats, cold cache entry | 4,508.7 | 3,525.1 | 18,041.1 | 7,449.9 |
| Stats, cached poll | 13.3 | 13.8 | 27.0 | 28.6 |
| Score values, cold cache entry | 1,921.9 | 1,747.1 | 14,852.4 | 5,648.1 |
| Score values, cached poll | 408.3 | 417.1 | 416.5 | 437.8 |

- **A Stats load is 22% cheaper warm and 59% cheaper cold**, and a score-values load 9%
  and 62%. Every scan's plan reads `SEARCH images USING INDEX ix_images_dataset_id`.
- **A cached poll does not move**: it runs the validator alone, which is not pinned and
  still reads `ix_images_dataset_updated_at` as a covering index.
- The pinned scan returns the same rows in table order, so `score_means`, a float sum in
  row order, differs from the unpinned value at about its 14th significant digit
  (`docs/dev/statistics.md` § The Stats scans and their index pin).

### What this run does not cover

- **Rows of real, varied size.** The seed's rows are nearly all one size, so `blobs_last`
  keeps every small column on the leaf page. Real rows vary, and some would push those
  columns onto the first overflow page. Its numbers are a best case.
- **A real migration's cost.** The builds ran with the journal off and were followed by a
  `VACUUM`, which a migration cannot run inside its transaction.
- **The app's endpoints on a split file.** The models still have the columns, so only raw
  statements were replayed: no ORM, no Pydantic.
- **Other shapes for the side table**: a count that only asks whether a side row exists,
  which SQLite could answer from the primary key's index alone, and the two final-layer
  vectors apart from the 18 KB layer blob.
- **A subfolder-scoped Stats call at 1M.** It was timed at 100k only (the section above).
- 1M on the slower disk, 250k, several datasets in one file, and a ComfyUI-shaped file
  larger than 20k.
