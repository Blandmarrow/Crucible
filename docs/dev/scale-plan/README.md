# M9 — Scale and data safety

**Status: living.** The design plan for M9. Its place in the order is in
[../roadmap.md](../roadmap.md); why it comes first, and what it deliberately does not build,
is [decision 13](../decisions/13-scale-work-first.md).

On 2026-11-08 Crucible received an outside source review ("Crucible-improvements.md",
written against `7ec1763`) aimed at datasets near one million images. Each claim was checked
against the code. This plan keeps what held up, corrects what did not, and adds three
problems the review missed. One principle runs through every stage: **a large operation is
incremental, bounded in memory, and recoverable.** It writes as it goes, holds one chunk at
a time, and leaves finished work in place when it stops for any reason.

Line numbers below are as of 2026-11-08 (`main` at `27483a2`). Trust the function name over
the number.

## Evidence base

`backend/scripts/bench_scaling.py` seeds synthetic rows into a scratch SQLite DB and times
the hot paths across a size sweep. `backend/scripts/scaling_bottlenecks_report.md` records
its July run: at 1M images, the Stats page took about 35 s (`get_dataset_stats` 24.9 s plus
`get_score_values` 10.0 s), and a gallery page took about 2.25 s even at page 1 (20 s at a
deep offset). Its critical finding, O(N²) duplicate detection, has since been fixed (212 s
at 1M, near-linear).

The index `ix_images_dataset_created_at` already existed during that run (migration
`a1b2c3d4e5f6`, May). On today's dev DB, `EXPLAIN QUERY PLAN` shows the gallery's default
and aesthetic sorts using their indexes. The app never runs `ANALYZE` or `PRAGMA optimize`,
so the planner has no table statistics.

Two gaps make the July numbers optimistic:

- **No layer blobs.** The bench seeds `clip_embedding` and `dino_embedding` but never
  `dino_layer_embeddings`, the 18,432-byte one.
- **The wrong column order.** It builds its DB with `Base.metadata.create_all`
  (`bench_scaling.py:93`), which follows the model's declaration order. A real database is
  built by migrations, and SQLite's `ADD COLUMN` appends. In the dev DB the three embedding
  blobs are columns 27–29 of 48 (counting from 1; about 21 KB per row on 4 KB pages), and 19 columns come after
  them: `subfolder`, `sort_order`, `caption_token_count`, `license`, `luminance_score`,
  `scores_stale`, `aesthetic_model` and others. SQLite stores the part of a row that does
  not fit on its page in a linked chain of overflow pages. To read a column past the blob,
  it walks that chain, so a scan that reads those columns also reads every embedding. The
  estimate is about 20 GB touched for a cold stats scan at 1M rows with layer embeddings.
  That figure comes from the layout, not a timing. Stage 6 measures it.

### The 2026-11-09 run

A review on 2026-11-09 closed both gaps by hand for 100k rows: a scratch DB built from a
migrated database's own DDL, with all three blobs seeded. Every number is in the last
addendum of `backend/scripts/scaling_bottlenecks_report.md`, with the method and caveats
(raw SQLite, warm cache, one dataset). What it changed in this plan:

- **Stage 1 shipped a regression.** A score sort went from 1.4 ms to 323 ms on page 1, and
  to 4.5 s at a deep offset. Stage 9 fixed it (PR #134); PM-036 is the record.
- **The overflow cost is real and measured.** A pass that reads a column after the blobs
  costs 1.0–1.4 s per 100k rows, against about 0.3 s for one that does not. Stage 7 has its
  evidence, for a warm cache at least.
- **`generation_metadata` is the larger blob.** On the dev DB's ComfyUI dataset it averages
  54,895 bytes a row, against 21,504 for the three embeddings together. It sits right after
  them in the row, it is not deferred, and the gallery list returns it. Stages 7 and 11
  now cover it.
- **Stage 4's SQL bins do not speed the scan**, though they still fix the memory and the
  payload. One pass in place of nine is what saves time. Stage 4 is amended below.
- **Three cheap fixes** turned up on the way (`count(*)`, an index for the Stats cache
  check, a narrower thumbnail read). They are stages 9 and 11.

## Stage 1 — A unique tie-breaker for the gallery sort

**Done 2026-11-08** (PR #124), as below; `bulk_rename` gained the same `id` key.

**Regression found 2026-11-09.** The pages are disjoint, as the acceptance test proves, but
the score sorts lost their index: with `created_at ASC, id ASC` after the sort column,
SQLite scans the dataset through an unrelated index and sorts all of it for every page. The
"USE TEMP B-TREE FOR RIGHT PART OF ORDER BY" line this stage expected appears only for the
`created_at` sort. A score sort gets `USE TEMP B-TREE FOR ORDER BY`, a full sort. The
`EXPLAIN QUERY PLAN` check below was the right instinct, but nothing ran it at a row count
where it shows. [Stage 9](#stage-9--give-the-score-sorts-their-index-back) replaced the
tail described below with `rowid` in the sort's own direction, and
`docs/dev/postmortems/PM-036-tie-breaker-cost-the-sort-index.md` is the record.

**Problem.** `_apply_image_ordering` (`backend/routers/images.py:420-456`) sorts by one
column. Its `sort_order` and nulls-last branches break ties with `created_at`, which is not
unique either. When values tie, SQLite may return rows in a different order on each query.
Score sorts tie often, because unscored rows are all NULL. So with `OFFSET` paging, an image
can show up on two pages or on none, and the detail view's arrows can jump.

**Approach.** Append `Image.id` as the last key in all three branches. The one function
also feeds `GET /images/ids` (the select-all order) and the detail-view navigation, so the
fix reaches all three. Check `EXPLAIN QUERY PLAN` afterwards. A "USE TEMP B-TREE FOR RIGHT
PART OF ORDER BY" line is expected and cheap under a `LIMIT`. Add a
`(dataset_id, created_at, id)` index only if stage 6 or a timing shows it pays. `Image.id`
is a String primary key, not the rowid, so no existing index carries it.

**Acceptance.** A test seeds rows with tied and NULL sort values and pages through them
under each sort: the pages are disjoint and together cover every row.

**Overlap.** M4 rewrites the comment in the nulls-last branch and adds `aesthetic_rating`
to the nulls-last sorts. That's a trivial conflict, which M4 resolves.

## Stage 2 — The orphan sweep moves folders instead of deleting them

**Done 2026-11-08** (PR #125), as below, plus two rulings: a folder
whose tree holds no file is still deleted, by the user's choice, and every dot-named child is
skipped. The user doc went into `docs/datasets.md`, which already described the sweep, not
`docs/server.md`. The as-built rules: `docs/dev/database.md` § The sweep moves data aside and
never deletes it.

**Problem.** `sweep_orphan_dataset_folders` (`backend/services/dataset_service.py:176-337`)
runs at every startup and calls `rmtree` on any folder under `datasets_dir` with no dataset
row. Its guards stop it when the whole tree looks wrong: rows outside the root, no row's
folder existing, or no rows beside real datasets. Nothing stops it when **one** folder is
unknown. Restoring an older database backup, which the app makes itself
(`backend/services/db_maintenance.py`), leaves every dataset created since then without a
row, and the next start deletes those folders permanently. A dataset folder copied in by
hand goes the same way. Other people run Crucible, so this is the worst failure on the list.

**Approach.** Rename each orphan into `{datasets_dir}/.orphaned/<name>-<UTC stamp>` with
`utils.rename_retrying` (Windows file locks; see PM-021), and log the move. Nothing deletes
automatically. A same-volume rename is instant whatever the folder's size. The sweep must
skip `.orphaned` itself. Grep every other site that lists `datasets_dir`'s children (the file
browser, the import directory picker) for the same skip. A recovery UI and a retention
policy can come later, once someone needs them.

**Docs.** The sweep is described in `docs/dev/database.md`, whose paragraph on dataset-folder
deletion and the startup orphan sweep says it auto-deletes without confirmation and removes
every unknown child directory. Rewrite that paragraph in the same change. The change is user-visible
(folders appear in `.orphaned`, and the user deletes them by hand), so a user doc gains a
short section. `docs/server.md` is the likely home.

**Acceptance.** `backend/tests/test_orphan_sweep.py` asserts the folder moved with its
contents intact, and that a second startup leaves `.orphaned` alone.

## Stage 3 — Chunked quality scoring, stage by stage

**Done 2026-11-08** (PR #126), as below, plus two rulings by the user: "only unscored" picks, per check, rows missing that check's output **or marked stale**,
and it is a separate *Score what's missing* button rather than a saved checkbox. Two
departures from the approach: the request reads ids only and the job reads each chunk's
paths itself, and progress goes through an optional `progress` callback on the seven
scorers. The as-built rules: `docs/dev/quality/scoring.md` § The chunked job loop. Peak
memory is not yet measured; that is stage 6's harness.

**Hand-checked 2026-11-08** on a throwaway instance:

- **CPU crash.** 2,500 images, Technical only. A server hard-killed mid-run kept the first
  1,000 scores, and the job read *Interrupted*. *Score what's missing* then scored exactly
  the other 1,500, and a second press answered *Nothing left to score*.
- **GPU, every scorer** on 1,200 images, two chunks per check. Each model loaded once, every
  column was written with the aesthetic marker intact, the percent was monotone (skipping
  the loader's `-1.0` "indeterminate" events), and all four models auto-unloaded.
- **GPU cancel** through `DELETE /jobs/{id}`, partway into the aesthetic check's second
  chunk. It kept 1,080 scores, never started NSFW, and unloaded the models.

**Problem.** `start_score_job` (`backend/routers/quality.py:167-474`) loads every matching
row as a full ORM object inside the request (line 199), only to read ids and paths. Its job
then runs each check across the whole set, all aesthetic scores, then technical, watermark,
CLIP, DINOv2 and NSFW. It holds every result in memory, layer embeddings included (18 KB
per image), and writes everything in one loop of `session.get` calls with a single commit
at line 420. A cancel saves the finished work (line 422). A crash, a Restart, or a shutdown
that outlives `JobQueue.stop()`'s grace saves **nothing**. `mark_interrupted_jobs` then
fails the row at the next start, so hours of scoring can vanish.

**Approach.** Chunk inside each check, not across checks. Chunking across checks (every
check on chunk 1, then chunk 2) would swap models in and out of VRAM for every chunk,
because `model_manager` evicts the least recently used model. So:

1. The request selects only `(Image.id, Image.file_path)`.
2. For each ticked check, the job loads its model once, under the existing one-lease-per-stage
   rule. Then it loops over chunks of about 1,000 ids: it runs the scorer on the chunk's
   paths, selects the columns the write depends on (`scores_stale`, the current
   `_JOB_SCORE_COLUMNS`, `quality_flags`), builds one dict per row, writes them with a bulk
   `update(Image)`, commits, and calls `job_queue.raise_if_cancelled`.
3. Copy `_score_all_layers_paginated` (`quality.py:800`; `_score_paginated` since stage 5), which already does keyset paging
   plus a bulk `update(Image), updates` write.
4. Add an "only unscored" option, a `score IS NULL` filter in the style of
   `only_mismatched`. It is the resume path: re-running the same job picks up where the
   last one stopped, so the rows themselves are the checkpoint. Expose it on QualityPage,
   and describe it in `docs/scoring.md`.

**Keep exactly as they are:** the auto-unload `finally` and its scoping to `loaded_ids`;
`_flag_duplicates` after the technical check, since it is a whole-dataset pass; the
signature of `start_score_job`, which `backend/services/run_service.py` calls for automated
runs; the aesthetic-marker invariant (score non-NULL ⇔ marker non-NULL); the
`scores_stale` clear predicate, now computed per row per chunk (the comment block at lines
391-419 explains it); the copy-then-reassign rule for `quality_flags`; and the AST guard in
`backend/tests/test_scores_stale.py`. Progress must count against the job's total across
all chunks and checks (`backend/workers/progress.py`), not restart for each check.

**Known side effect.** Until stage 4 lands, each chunk commit moves `max(updated_at)`, so a
Stats page left open recomputes from cold on every poll during a scoring job. That's about
2.5 s per poll at 100k images. It's acceptable for now, and stage 4 removes most of the cost.

**Acceptance.** A test stops a job partway (a cancel, plus a simulated crash between chunks)
and finds the finished chunks' scores in the DB. Peak memory stays flat as the dataset grows
(check it with stage 6's harness). The "only unscored" re-run touches only the rows left.

## Stage 4 — Server-side histograms for the Stats page

**Problem.** The review's diagnosis is partly wrong: stats already read plain columns, not
ORM objects, and counts, flags and licenses are already aggregated in SQL
(`dataset_service.py:1680-1790`). The real cost is `get_score_values`
(`dataset_service.py:1836-1896`). It returns **every raw value**, 13 lists with one entry per
image, and the browser bins them. At 1M images that's roughly 80–100 MB of JSON, and about
400 MB of Python floats held in `_stats_cache` for each entry. `get_dataset_stats` also
reads `caption_text` for every row, only to count words.

**Approach (amended 2026-11-09).** Return bins built in SQL, and read PM-020 first (a
histogram drawn on the wrong scale). The first version of this approach said
`floor((x - lo) / w)` plus `GROUP BY`. Five things in it did not hold up:

1. **Most bins are uneven.** Only watermark and style similarity have ten equal bins. The
   other eleven metrics use hand-picked edges (blur is `20, 40, 80, 150, 300`), which
   `floor()` cannot express. Use one count per edge pair instead:
   `sum(x >= lo AND x < hi)`, half-open like `_bucket` and `rebucketValues`, with the top
   bin open. `docs/dev/statistics.md` § Bucket drill-down states the half-open rule that
   every bar's click-through filter depends on.
2. **The pencil editor needs the raw lists today.** Every score histogram can be rebucketed
   with the user's own edges, kept per dataset in `stats-hist-edges-{metric}-{datasetId}`,
   and `rebucketValues()` does that in the browser from the `score-values` arrays. Without
   the arrays the feature dies unless the edges go to the server. So the endpoint takes
   optional edges per metric, and they join the cache key.
3. **Two edge tables is what was rejected before.** `docs/dev/statistics.md` § Backend
   aggregation records SQL-side bucketing as "considered and rejected", because it would
   duplicate `DEFAULT_EDGES` from `StatsPage.tsx`. Make the server the only owner: each
   histogram comes back as its edges plus its counts, and the page reads the defaults from
   the response. `DEFAULT_EDGES`, `SAT_EDGES` and the "must stay numerically identical"
   comments in `dataset_service.py` all go, and with them PM-020's failure class. Rewrite
   that sentence in `statistics.md` in the same change.
4. **Two consumers take means from the raw lists**: the CSV export's MEAN SCORES section
   and the "Mean aesthetic" card. Return `AVG` per metric. `file_size_summary`'s median and
   p95 need the sorted sizes; fetch that one integer column, or use `ORDER BY … LIMIT 1
   OFFSET n`.
5. **Binning in SQL does not make the scan faster.** At 100k rows the aggregate query took
   1,377 ms against 1,232 ms for fetching the rows, because the cost is reaching the
   columns after the blobs. What it does fix is the Python loop, the 13 lists held in
   `_stats_cache` and the JSON payload. The time saving is **one pass in place of nine**:
   today the validator runs twice and seven more queries scan the same rows (about 5.3 s
   of SQL at 100k); every count, flag sum, histogram and mean fits in one `SELECT` (about
   1.4 s). Keep the license group as its own query. Merge `get_dataset_stats` and
   `get_score_values` behind one scan, so a poll costs one pass, not two.

For the word count, use an SQL expression if tests show it buckets the same as
`str.split()`. If not, add a `caption_word_count` column that follows `caption_token_count`
exactly: derived by the same ORM listener and listed in `NOT_MIRRORED` in
`backend/tests/test_video_lineage_mirrors.py`. The payload drops to a few KB, so the
review's memory-aware cache is no longer needed. `HistPanel`'s unmemoized
`rebucketValues` call goes away with the arrays.

**Acceptance.** For every metric, the SQL bins equal `_bucket`'s on a fixture with a value
on each edge, for default and for custom edges. The existing
`backend/tests/test_stats_click_through_edges.py` and `frontend/e2e/stats.spec.ts` still
pass. The CSV's means match the old ones on a fixture.

**Overlap.** M4 adds `rating_distribution` to `_aggregate_dataset_stats` and a column to
`get_dataset_stats`'s select. M8 stage 7 (StatsPage into files) comes after this stage.

## Stage 5 — Page the single-layer style similarity branch

**Done 2026-11-08** (PR #128), wider than below: **every**
branch now pages through the helper, renamed `_score_paginated`, with the page size in
`_STYLE_PAGE_SIZE`. The plain `dino` branch at a layer read the 18 KB blob whole too and was
missed here; `clip` and `dino` on the final embedding read small blobs but grew without
bound, so they page as well. Those four score page by page but write after the last page
(`defer_write`), so they hold SQLite's write lock only for the writes, as before; writing
page by page would have held it through the whole scan, which an Opus review caught. The
optional SQL `substr` slice was not done: paging already caps the raw blobs at about 36 MB a
page (about two pages at peak, since the last page lives on while the next is read), and
`substr` would skip `slice_layer_embedding`'s length check. Stage 6 can revisit it with
numbers. The as-built
rules: `docs/dev/quality/style-similarity.md` § Paged and all-layers scoring.

**Hand-checked 2026-11-08** on scratch copies of the dev DB, calling
`_score_style_similarity` directly with `main`'s code and the branch's: dataset `test2`
(804 images, all three embeddings), eight modes, whole and scoped to every third image. The
responses matched exactly. Of 8,576 stored scores, 1 differed at the default page size and 7
at a page of 37, each by 0.0001. The cause is float32 rounding, not the change: the same
rows scored by the same code flip that digit when only their order changes, and five of the
seven came from the all-layers branches, whose code did not change. So the acceptance
criterion holds to one step of stored precision, not bit for bit.

**Problem.** The single-layer and final-layer branch of `_score_style_similarity`
(`quality.py:999-1031`) fetches every candidate's full blob in one result set, inside an
HTTP request, just to slice out one layer. That's about 1.8 GB at 100k images. The
all-layers branches beside it were already moved onto `_score_all_layers_paginated` for
exactly this reason.

**Approach.** Route this branch through `_score_all_layers_paginated`. Optionally fetch only
the needed layer in SQL. `slice_layer_embedding` (`backend/ml/similarity_scorer.py:50`)
stores the layers in order, numbered from 1, at 1,536 bytes each, so the expression is
`func.substr(Image.dino_layer_embeddings, (layer - 1) * 1536 + 1, 1536)`. That cuts Python
memory, though SQLite still walks the overflow chain to reach the slice.

**Acceptance.** On a small fixture, scores match the current path to the stored precision.

## Stage 6 — Measure what is left

Extend `bench_scaling.py` before deciding anything structural. Since 2026-11-09 the stage
has two halves, so that stages 4 and 11 are measured before and after.

**6a, the harness, before stage 4:**

- Seed `dino_layer_embeddings` (18,432 bytes) at realistic coverage, and
  `generation_metadata` at two sizes: a few hundred bytes (photos) and about 55 KB (ComfyUI
  output).
- Build the scratch DB with `alembic upgrade head` instead of `create_all`, so the column
  order matches a real install. The 2026-11-09 run copied the `images` DDL out of a
  migrated database's `sqlite_master`, which gives the same order with no Alembic run.
- Capture `EXPLAIN QUERY PLAN` for every gallery sort, each with and without
  `PRAGMA optimize`. Add a cold-cache mode if the platform allows one.
- Time the query shapes the 2026-11-09 run timed, so its addendum is the baseline: each
  gallery sort at page 1 and at a deep offset, the count, the Stats validator, each Stats
  pass, and a lookup by id.

**6b, the numbers, after stages 4 and 11:**

- Re-run stats, pagination and similarity at 100k, 250k and 1M.
- Record the numbers as a dated addendum to `scaling_bottlenecks_report.md`, with hardware
  and settings. Keep owner-specific hardware out of the tracked file.

M4 also edits `bench_scaling.py` (three lines). Whichever lands second resolves it.

## Stage 7 — Move the embeddings into their own table (only if stage 6 says so)

**Decide first.** Stage 6 must show the overflow cost, and the ruling gets its own decision
entry. The July report deferred this split "until DB-file bloat is a concrete operational
pain". The column-order finding is new evidence for that question, not an answer to it.

**Evidence so far (2026-11-09, 100k rows, warm cache).** With the three blobs in a table of
their own, the one-pass Stats aggregate went from 1,377 ms to 528 ms, and to 325 ms in
table order. Stage 6b still owes the cold-cache and 1M numbers.

**`generation_metadata` belongs in the same decision.** It is the larger blob on ComfyUI
images (54,895 bytes a row on average in the dev DB, against 21,504 for the embeddings),
it sits right after them in the row, and unlike them it is not `deferred`. So every
`select(Image)` and `db.get(Image, …)` loads it, and every read of a later column walks
past it. On a ComfyUI-shaped scratch DB the Stats pass cost 35 µs a row against 14 µs.
Stage 11 takes it out of the gallery list, which needs no schema change. Whether it also
moves out of the row (into this table, or a sibling one) is for this decision.

**If yes:** an `image_embeddings(image_id PK → images.id ON DELETE CASCADE, clip, dino,
dino_layers)` table, and a migration that copies the blobs and drops the columns. Run it
while databases are still small, since it rewrites every row. Grep every reader of the three
columns. The blobs are already in `NOT_MIRRORED`, so snapshots are unaffected. The decision
must say whether copy, move and duplicate carry embeddings or let them be recomputed.

**Backups.** `db_maintenance.py` keeps `BACKUP_KEEP = 5` full copies. At 1M images with
layer embeddings, that's about 100 GB of data that can be regenerated from the pixels. A
separate attached SQLite file would keep the blobs out of the backups, but SQLite cannot
enforce foreign keys across files, so deletes would need a cleanup sweep. Weigh that in
the same decision.

## Stage 8 — Retry for interrupted jobs

**Done 2026-11-08** (PR #127), for `quality_score` only, with three rulings by
the user: a retry **resumes** (it turns on `only_unscored`, except for an `only_mismatched`
job, which is resubmitted as it was); Retry shows on **failed and cancelled** jobs; and a job
an automated run owns gets **no** Retry. The as-built rules: `docs/dev/backend-infrastructure.md`
§ Retrying a stopped job.

**Hand-checked 2026-11-08** on a throwaway instance (:8299, scratch DB): 2,500 images,
Technical only, CPU. A server hard-killed mid-run kept 1,000 scores, and after the restart the
job read *Interrupted* with `retryable` true. Retry queued `Retry: Quality: technical — 1500
images`, which scored exactly the other 1,500. A second Retry while it ran was a 409, and one
after it finished answered *Nothing left to score*. A second run drove the real page with
Playwright (3,000 images, 1,000 kept): Logs showed one Retry, the click toasted *Retry
queued*, a gallery tab open on the dataset showed the retry in its TopBar and refetched its
images once the retry ended, and afterwards only the original row kept its button.

**Found on the way:** no job but an automated run's steps writes
`BackgroundJob.done_items`, so every Logs row read `0/N` whatever it finished. The Logs page
now shows `N/N` for a completed job and `N items` for any other row without a count
(`itemCount` in `LogsPage.tsx`); nothing stored changed. The same gap leaves the run's
paid-caption cap counting nothing (`_spent` sums `done_items` over `caption` children), which
is left for its own change. That change is stage 10: the caption job now writes the column.

Once stage 3 makes scoring resumable, "recovery" is mostly re-running a job. Add a Retry
action on failed and interrupted jobs that resubmits the stored `BackgroundJob.config`
(written by `run_job_config`). Start with `quality_score`, whose config rebuilds the request
completely (`body.model_dump()`). Leave out job types whose config does not, until each is
checked. Automated runs already reconcile on their own (`reconcile_interrupted_runs`). The
user doc for the jobs list gains a line.

## Stage 9 — Give the score sorts their index back

Added 2026-11-09. It fixes stage 1's regression and waits on nothing. All times are from the
report's 2026-11-09 addendum: 100k rows, warm cache, raw SQLite.

**Done 2026-11-09** (PR #134), as below except where
this note says otherwise. Detail: `docs/dev/gallery/filters.md`.

- **Ids first for every sort, not only the unindexed ones** (item 4). On an indexed sort
  it costs what the one-statement read did, so `list_images` has one code path:
  `_page_ids_query`, then the rows by `IN`. One statement with an `IN (subquery)` was
  tried and dropped: it re-sorts the page's whole rows, 6 to 10 ms slower on ComfyUI rows.
- **`sort_order` reads its index** (item 3). `sort_order ASC NULLS LAST, rowid ASC` has no
  sort step. The two lineage sorts have no usable index and stay a full sort, over ids.
  `filename` takes the `rowid` tail like the rest; the plan is the same.
- **`bulk_rename` takes the `rowid` tail too** (item 3). It is not paged and never needed
  the index, so the first cut left its `created_at, id` key alone. A review caught what
  that broke: Renumber is offered from the custom-order grid, whose unpositioned tail now
  ties on `rowid`, so the files were numbered in an order the grid did not show wherever
  `created_at` tied. One import ties many rows on Windows, where the clock is coarse.
- **A rebuild keeps the order** (item 2). `VACUUM` renumbered nothing on SQLite 3.49.1,
  so a vacuum test cannot fail there. The test that can is a table rebuild, the shape of
  an Alembic batch migration, which does renumber and keeps the ties in order.
- **The `ORDER BY` tail is asserted from the SQL.** With no tie-breaker at all, every
  ascending paging test still passed, because an index walk returns ties in `rowid` order
  by itself.
- **`count(*)` pays only where the count is alone** (item 5). `GET /images/count` and the
  `/ids` total gain. `_image_validator` and `refresh_stats` were changed and gain nothing
  yet, since `max(updated_at)` and `sum(file_size_bytes)` still read every row. Stage 11's
  index is what pays there. The other `count(Image.id)` sites were not touched. Which
  covering index a bare count reads is the planner's pick among the `dataset_id` ones: the
  narrow similarity index on a migrated database, the wide caption index on a
  `create_all` one. Nothing pins it, so 1.7 ms is not a promise for long captions.
- **The "slightly worse" case below does not happen.** The report timed `subfolder = ?`.
  The route's filter is `sql_subtree`, an `OR` over `substr`, which no index serves, so
  `main` scanned the dataset there too. Raw SQLite, a subfolder of 100 rows in 100,000:
  57 ms against 59 ms.

Through the ORM, on scratch databases built to the report's method (best of three, warm
cache, `LIMIT 100`; `main`'s statement against this branch's two):

| Query | `main` | Stage 9 |
|---|---|---|
| Aesthetic desc, page 1 (photo, 100k rows) | 287 ms | 3.0 ms |
| Aesthetic desc, `OFFSET 60000` | 2,908 ms | 5.0 ms |
| Created desc, page 1 | 1.2 ms | 2.4 ms |
| Created desc, `OFFSET 90000` | 693 ms | 6.9 ms |
| Width desc (no index), `OFFSET 60000` | 2,886 ms | 155 ms |
| Aesthetic desc, page 1, one subfolder of 20 | 324 ms | 10.2 ms |
| `count`, whole dataset | 268 ms | 1.7 ms |
| Aesthetic desc, `OFFSET 10000` (ComfyUI, 20k rows) | 7,056 ms | 11.1 ms |
| Created desc, page 1 (ComfyUI) | 9.8 ms | 10.8 ms |

Page 1 of the default sort is the one request that got slower, by the cost of the second
statement. Stage 6a's harness repeats these when it lands.

**Problem.** Since stage 1, a sort on an indexed score column ends
`created_at ASC, id ASC`. No index carries those two keys, and with them the planner stops
using the score's index at all: it reaches the dataset's rows through whichever
`dataset_id` index it picks and sorts every row, whole rows included, for each page.
Aesthetic "best first" went from 1.4 ms to 323 ms on page 1, and to 4.5 s at
`OFFSET 60000`. On a ComfyUI-shaped dataset of 20,000 rows, page 100 took 6.9 s, since the
sorter carries each row's 55 KB of `generation_metadata`. `ANALYZE` does not change the
choice, and neither does a wider index for a descending sort, whose `DESC, ASC, ASC` matches
no index in either direction.

**Approach.**

1. **Break ties on `rowid`, in the sort's own direction.** `Image.id` is a string key, so
   `images` has SQLite's hidden integer `rowid`, and every index entry already ends with
   it. `ORDER BY aesthetic_score DESC, rowid DESC` is then exactly the index's order read
   backwards: no sort step, no new index, 1.5 ms on page 1 and 4.5 ms at `OFFSET 60000`.
   A deep offset over an index only steps index entries, which is why
   [decision 13](../decisions/13-scale-work-first.md)'s rejected cursor paging stays
   rejected. SQLAlchemy has no attribute for it; use `literal_column("images.rowid")`.
2. **Know what `rowid` promises.** It is unique at any moment, which is all paging needs
   between two requests. It is not permanent: `VACUUM` and a table rebuild in a migration
   may renumber it. Both copy the table in `rowid` order, so the *order* of ties should
   survive; confirm that with a test that vacuums a fixture. Ties fall in insertion order,
   which is `created_at` order for nearly every row.
   State that in the function's docstring, which today promises `created_at ASC, id ASC`.
3. **The other branches.** The `sort_order` and nulls-last branches keep a full sort unless
   their `NULLS LAST` can follow an index; check each with `EXPLAIN QUERY PLAN` and leave
   them be if not, since both are rare and small. `filename` needs no tie-breaker at all:
   `uq_dataset_filename` makes it unique within a dataset. `bulk_rename` took stage 1's
   `id` key as well; give it the same review.
4. **Sorts with no index** (`updated_at`, `file_size_bytes`, `width`, `height`,
   `nsfw_score`, and the noise, uniformity, watermark, color, saturation and luminance
   scores) still sort the dataset. Pick the page's ids first in a narrow inner query, then
   load those rows and re-apply the order to them. That took a deep page from 4,184 ms to
   314 ms, because the sorter then carries ids and not whole rows. Add an index for one of
   these only if stage 6b shows it pays.
5. **`count(*)`, not `count(Image.id)`.** `func.count(Image.id)` makes SQLite visit the
   table for every row (292 ms); `func.count()` is answered from an index alone (3.4 ms).
   It appears 28 times outside the tests, eleven of them in `dataset_service.py`. Change
   it where the query has no join that could multiply rows: `GET /images/count` first (it
   runs on every gallery page), then `_image_validator` and `refresh_stats`.
6. **A guard test on the plan.** For each indexed sort, in both directions, run
   `EXPLAIN QUERY PLAN` on the statement `_apply_image_ordering` builds and fail on
   `USE TEMP B-TREE FOR ORDER BY`. The plan does not depend on the row count, so the test
   needs no large fixture. It is torch-free and belongs in the ordinary suite.

**One case gets slightly worse.** With a narrow subfolder filter, the planner walks the
score index and filters (27.3 ms), where today it takes the subfolder's rows and sorts them
(21.8 ms). Accepted; record it in the test file.

**Acceptance.** Stage 1's disjoint-pages test still passes under every sort. The guard
test passes. Stage 6a's harness shows page 1 and a deep page of each indexed sort within a
few milliseconds at 100k rows. PM-036 moves from LIVE to MITIGATED in the same change.

**Overlap.** M4 edits the same function (it adds `aesthetic_rating` to the nulls-last
sorts) and lands second, so it resolves this too.

## Stage 10 — The paid-call cap counts caption calls

Added 2026-11-09; found during stage 8 and until now recorded only in the roadmap's prose.

**Done 2026-11-10** (PR #135), as below except where this note
says otherwise. Detail: `docs/dev/comfyui/run-budgets.md` § Who writes the paid-call
counts; the incident is PM-037.

- **`done_items`, not a `result_data` count.** It is the column `_spent` already reads, so
  `_spent` did not change. The caption job writes it after every call, whatever the call
  returned, before the caption is saved, and commits it per image. It equals the job's SSE
  `done`, which keeps PM-008's one-unit rule. The Logs page shows a stopped caption job's
  `k/N` with no frontend change (`itemCount` already had the branch).
- **Written after the call, not before it** (the user's ruling, 2026-11-10). A call in
  flight when the process dies, or still running when shutdown's grace runs out, is not
  counted: at most one per stop, as for the rewrite job. Writing ahead would have put the
  row one ahead of the live count and counted calls that never left the machine.
- **A caption stage captions only what is left** (the user's ruling, 2026-11-10). This was
  not in the plan below. Counting alone caps the spend and then wastes it: the stage trims
  its list to the calls left *in order*, and after a stopped job the first images in order
  are the ones already captioned. `_stage_images` now skips images whose `captioned_by` is
  the stage's model, for billed and free models alike.
- **What a stopped caption job leaves behind**: `done_items` for every call made, and
  nothing else new. A cancelled job still writes no `result_data` (PM-023's known gap).
- **`/pipeline` is not touched.** A run never starts it, and its counter is image × step.
- **The acceptance test** is `backend/tests/test_run_paid_calls.py`: four images, a cap of
  five, a job that fails after one call, a job cancelled from outside after three more
  (one a failed call), and a last resume with one call for two images. Five calls reach
  the fake provider. The stubbed test in `test_run_extend.py` now goes through the real
  `_spent`, over rows built by hand: it checks the reserve, not the writer.
- **Left open**, in `docs/dev/comfyui/run-budgets.md` § Not yet: the rewrite's reserve
  reads failed caption calls as captions made. Found by the review of this change.
- **Closed by a follow-up the same day** (the user's call): a caption edited by hand
  between attempts is stamped `"manual"`, and a resume captioned that image again. The
  stage now skips hand-written captions as well as its own model's.

**Problem.** An automated run's paid-call cap reads what it has spent from its child jobs
(`run_service._spent`). The rewrite half reads `result_data["calls"]`, which the rewrite
job writes. The caption half sums `done_items` over the run's `caption` children, and no
caption job writes `done_items`: only `run_service.py`'s own steps do. So a run that
captions through a billed provider counts zero caption calls and never stops at its cap.
Only a stubbed `_Spent` is tested (`backend/tests/test_run_extend.py`), which is how it
passed.

**Approach.** Its own change. Either the caption job writes `done_items` as it goes, or it
records a call count in `result_data` as the rewrite job does; pick the one that also
counts a call that failed after it was sent, since the provider billed it. Writing
`done_items` would also give the Logs page its real `N/M` (stage 8 papered over that with
`itemCount`). Check what a cancelled or interrupted caption job leaves behind, because the
cap must count those calls too.

**Acceptance.** A test over real `BackgroundJob` rows, not a stub: a run with a billed
caption provider and a cap of N stops after N caption calls, with a failed and a cancelled
caption job among them.

## Stage 11 — Hot-path quick wins

Added 2026-11-09. Each item is small, independent and measured or read in the code that
day. It sits after stage 4 only so that stage 6b measures both together.

- **An index for the Stats cache check.** `_image_validator` runs
  `count(id), max(updated_at)` on every poll of the Stats page (every 5 s while a job runs,
  twice per poll), and no index carries `updated_at`: 294 ms. With
  `(dataset_id, updated_at)` and `count(*)` it is 7.1 ms. It is a migration, so M4
  re-parents onto it. The index is one more to maintain on every image write; that cost is
  why it is here and not in stage 9.
- **`generation_metadata` out of the gallery list.** `ImageListItem` returns the whole
  JSON for every row, 5.6 MB for a page of 100 ComfyUI images, and the card only asks
  whether it exists (`ImageCard`'s button; `GalleryPage` then shows it in a modal). Send a
  boolean and fetch the JSON when the modal opens. `GET /images/{id}` already returns it.
  Select the list's columns, or mark the column `deferred` and `undefer` it where an entity
  reader needs it. The second is the `source_meta` trap (`MissingGreenlet` on an async
  session), so the first is safer.
- **The thumbnail and file routes read two columns.** `serve_thumbnail` and `serve_file`
  call `db.get(Image, image_id)`, which loads every non-deferred column to read one path:
  once per tile, per page. Select `thumbnail_path` and `file_path`. Keep the entity load for
  `serve_thumbnail`'s generate-on-demand branch, which writes the row.
- **A cache header on versioned thumbnails.** The routes return a bare `FileResponse`.
  Starlette 1.10.0 sets `ETag` and `Last-Modified` on it but no `Cache-Control`, and its
  `FileResponse` never answers 304, so a revalidation resends the file. The card's URL
  already carries `?v=` from `updated_at` (`thumbnailUrlVersioned`). Send
  `Cache-Control: public, max-age=31536000, immutable` **only when `v` is present**: two
  callers still use the unversioned `thumbnailUrl`. First check that every path that
  rewrites a thumbnail also moves `updated_at`
  (`docs/dev/bulk-image-jobs.md` covers the rebuild's bump), or a stale tile becomes
  permanent. Fewer refetches also means fewer open handles on Windows (PM-021).
- **The live gallery refetch.** `TopBar` invalidates `["images", datasetId]` each time a
  caption, ComfyUI or extract job's `done` count advances, so a job over N images refetches
  the open gallery page up to N times, payload and all. Throttle it (one refetch per few
  seconds, plus one at the end). `docs/dev/job-invalidation.md` owns the pattern.
- **`ImageCard` re-renders.** Each card calls `useSelectionStore()` with no selector and
  is not wrapped in `React.memo`, so one selection change re-renders all 100 to 200 tiles.
  Give the card a per-id selector and memoize it. The grid is not windowed; leave that
  until this is measured, since the page size tops out at 200.

**Acceptance.** Stage 6b's numbers for the validator and a ComfyUI gallery page; an e2e
check that the generation-metadata modal still opens; a test that an unversioned thumbnail
URL gets no `immutable` header.

## Interaction with the other milestones

- **Stages 9 and 11 overlap M4 again**: `_apply_image_ordering`, `GalleryPage.tsx`,
  `schemas/image.py` and one more migration (stage 11's index) for M4 to re-parent onto.
- **M10** (job throughput, `docs/dev/throughput-plan/README.md`) shares no file with this
  plan. It comes after M9 by order, not by dependency
  ([decision 14](../decisions/14-job-throughput-is-its-own-milestone.md)).
- **M4** (held on `feat/m4-rating-port`) overlaps stages 1, 4 and 6: `_apply_image_ordering`,
  `get_dataset_stats` and `_aggregate_dataset_stats`, `StatsPage.tsx`, `GalleryPage.tsx`,
  `bench_scaling.py` and `models/image.py`, plus two guards M9 relies on:
  `backend/tests/test_video_lineage_mirrors.py` (stage 4's possible `NOT_MIRRORED` entry) and
  `backend/tests/test_scores_stale.py` (stage 3). M4 does not touch `routers/quality.py`, so
  stages 3 and 5 have no conflict with it. M4 lands second and resolves the rest, as
  decision 5 set for M7. If M9 adds migrations (stages 4 and 7 may), M4's first migration
  `c3b8e1d7a52f` re-parents onto M9's last one. M4's `aesthetic_rating` is one more column
  that lands after the blobs, which stage 7 makes moot.
- **M8**: the review's "split oversized routers" item is already M8 stage 8 (`images.py`).
  Stage 4 here goes before M8 stage 7 (StatsPage into files).
- **M7**: no overlap. It comes after M9 by order, not by dependency.
