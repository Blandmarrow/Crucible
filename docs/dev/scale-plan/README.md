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

## Stage 1 — A unique tie-breaker for the gallery sort

**Problem.** `_apply_image_ordering` (`backend/routers/images.py:419-446`) sorts by one
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

**Problem.** `sweep_orphan_dataset_folders` (`backend/services/dataset_service.py:132-210`)
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
3. Copy `_score_all_layers_paginated` (`quality.py:800`), which already does keyset paging
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

**Approach.** Return fixed bins built in SQL. SQLite has no `width_bucket`, so use
`floor((x - lo) / w)` plus `GROUP BY`, as the July report suggests. Pin the bin edges in
tests, and read PM-020 first (a histogram drawn on the wrong scale). Then update the
StatsPage consumers of `ScoreValues` (`frontend/src/api/datasets.ts`). For the word count,
use an SQL expression if tests show it buckets the same as `str.split()`. If not, add a
`caption_word_count` column that follows `caption_token_count` exactly: derived by the same
ORM listener and listed in `NOT_MIRRORED` in `backend/tests/test_video_lineage_mirrors.py`.
The payload drops to a few KB, so the review's memory-aware cache is no longer needed.

**Overlap.** M4 adds `rating_distribution` to `_aggregate_dataset_stats` and a column to
`get_dataset_stats`'s select. M8 stage 7 (StatsPage into files) comes after this stage.

## Stage 5 — Page the single-layer style similarity branch

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

Extend `bench_scaling.py` before deciding anything structural:

- Seed `dino_layer_embeddings` (18,432 bytes) at realistic coverage.
- Build the scratch DB with `alembic upgrade head` instead of `create_all`, so the column
  order matches a real install.
- Re-run stats, pagination and similarity at 100k, 250k and 1M. Capture `EXPLAIN QUERY PLAN`
  for every gallery sort (with stage 1's tie-breaker), each with and without
  `PRAGMA optimize`.
- Record the numbers as a dated addendum to `scaling_bottlenecks_report.md`, with hardware
  and settings.

M4 also edits `bench_scaling.py` (three lines). Whichever lands second resolves it.

## Stage 7 — Move the embeddings into their own table (only if stage 6 says so)

**Decide first.** Stage 6 must show the overflow cost, and the ruling gets its own decision
entry. The July report deferred this split "until DB-file bloat is a concrete operational
pain". The column-order finding is new evidence for that question, not an answer to it.

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

Once stage 3 makes scoring resumable, "recovery" is mostly re-running a job. Add a Retry
action on failed and interrupted jobs that resubmits the stored `BackgroundJob.config`
(written by `run_job_config`). Start with `quality_score`, whose config rebuilds the request
completely (`body.model_dump()`). Leave out job types whose config does not, until each is
checked. Automated runs already reconcile on their own (`reconcile_interrupted_runs`). The
user doc for the jobs list gains a line.

## Interaction with the other milestones

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
