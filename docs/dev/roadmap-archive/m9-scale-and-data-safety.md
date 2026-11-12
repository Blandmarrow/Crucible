# M9 — Scale and data safety

**Status: archive (landed 2026-11-12).** *Record for the M9 line in the
[roadmap](../roadmap.md#landed).* Merged stage by stage between 2026-11-08 and 2026-11-12,
PRs #124 to #143. Its plan, [scale-plan/README.md](../scale-plan/README.md), stays in
place: it holds the designs that were parked, each with the trigger that reopens it.

Came before every other open milestone
([decision 13](../decisions/13-scale-work-first.md)). Gate: stages 1–5 and 9–11 merged with
their tests and docs, and stage 6's numbers recorded; stage 7 then built or ruled out by a
decision. Stage 7's side table was ruled out for now by
[decision 15](../decisions/15-big-columns-stay-in-the-row.md), and its plan guard and index
pin were built, which met the gate. M9 did not wait for M4, which lands second and
resolves the overlap
([scale-plan/README.md § Interaction with the other milestones](../scale-plan/README.md#interaction-with-the-other-milestones)).

- [x] Stage 1: a unique tie-breaker (`Image.id`) for every gallery sort. Merged 2026-11-08
  as PR #124. It cost the score sorts their index (PM-036); stage 9 fixed that.
- [x] Stage 2: the startup orphan sweep moves folders into `.orphaned/` instead of deleting
  them. Merged 2026-11-08 as PR #125: only a tree with no file is deleted (the user's call),
  and dot-named folders are skipped. Detail: `docs/dev/dataset-folders.md` § The sweep moves data
  aside and never deletes it.
- [x] Stage 3: quality scoring commits per chunk within each check, with an "only unscored"
  resume option. Merged 2026-11-08 as PR #126: the option takes missing-or-stale rows and
  is a *Score what's missing* button (the user's calls). Detail:
  `docs/dev/quality/scoring-job.md` § The chunked job loop.
- [x] Stage 4: fetch the Stats page's raw value lists only when something needs them (saved
  custom edges or an open pencil editor), and return the means from `/stats`. Shrunk on
  2026-11-10 by decision 13's amendment. Merged 2026-11-11 as PR #139: the CSV export needs no list at all, and a dataset's
  custom edges no longer leak to the next dataset viewed. No migration. The full design
  (bins built in SQL, the server owning the edges) is parked in the plan until a dataset
  nears 50k to 100k images. Detail: `docs/dev/scale-plan/README.md` § Stage 4 as built.
- [x] Stage 5: page the style similarity branches. Merged 2026-11-08 as PR #128: every
  branch pages, not just the one the plan named, and the one-score branches write after the
  scan so the write lock stays short. Detail: `docs/dev/quality/style-similarity.md` § Paged and all-layers scoring.
- [x] Stage 6: extend `bench_scaling.py` and re-measure, in two halves. **6a** builds the
  harness (the migrated column order, layer blobs, `generation_metadata`, query plans) and
  comes before stage 4. **6b** re-measures after stages 4 and 11 and records the numbers. A
  partial run by hand on 2026-11-09 is an addendum of
  `backend/scripts/scaling_bottlenecks_report.md`.
  - [x] 6a, merged 2026-11-10 as PR #137: the harness times the
    app's endpoints and replays each statement for its plan, with a smoke test in CI. Its
    "before" baseline (100k and 250k photo, 20k ComfyUI) is the report's 2026-11-10
    addendum. Detail: `docs/dev/scale-plan/README.md` § Stage 6.
  - [x] 6b, merged 2026-11-11 as PR #140: 6a's runs again, plus 1M warm, 1M cold Stats and
    similarity.
    It found stage 11's index slowing the Stats scans, and fixed a `--compare` crash. The
    numbers are the report's 2026-11-11 addendum. Detail: `docs/dev/scale-plan/README.md`
    § Stage 6.
- [x] Stage 7: decide on, then maybe build, a table of their own for the embeddings and for
  `generation_metadata`, which is the larger of the two on ComfyUI images. It also decides
  the Stats scans' index and adds their plan guard (`docs/dev/scale-plan/README.md` § Stage 7).
  - [x] Measure: `backend/scripts/bench_split_layout.py`, at 20k, 100k and 1M, warm and
    cold, on two machines (2026-11-11; the report's stage 7 addendum). Merged 2026-11-11
    as PR #141.
  - [x] Decide ([decision 15](../decisions/15-big-columns-stay-in-the-row.md), 2026-11-11):
    no side table now, reopened when a dataset nears 50,000 photo-shaped or 20,000
    ComfyUI-shaped images; the dataset-wide Stats scans pinned to `ix_images_dataset_id`.
    Merged with the measurement, as PR #141.
  - [x] Build the plan guard for the Stats statements and the pin, with the pin's
    documentation. Merged 2026-11-12 as PR #143: `_stats_scan` pins the
    seven dataset-wide scans, the validator and a subfolder-scoped call stay unpinned (the
    second after a timing), and `backend/tests/test_stats_plan.py` reads every plan.
- [x] Stage 8: Retry for interrupted jobs. Merged 2026-11-08 as PR #127, for `quality_score`
  (a retry resumes; failed and cancelled jobs; none for a run's child, by the user's calls),
  hand-checked through the API and the page. Detail:
  `docs/dev/backend-infrastructure.md` § Retrying a stopped job.
- [x] Stage 9: give the gallery's score sorts their index back (PM-036), with `count(*)` for
  the gallery count and a guard test that reads the query plan. Merged 2026-11-09 as
  PR #134: every sort breaks ties on `rowid` in its own direction, `GET /images/` picks a
  page's ids before it loads the rows, and Renumber follows the same order. Detail:
  `docs/dev/gallery/filters.md`.
- [x] Stage 10: make an automated run's paid-call cap count caption calls (PM-037). Merged
  2026-11-10 as PR #135: the caption job writes `done_items` after
  every call, and a resumed caption stage captions only what its model has not captioned
  yet. A call in flight when the process dies is not counted (both the user's calls).
  Detail: `docs/dev/comfyui/run-budgets.md` § Who writes the paid-call counts.
- [x] Stage 11: hot-path quick wins. Merged 2026-11-10 as PR #138: an index for the Stats cache check (migration `d5f1a8c3e7b2`), a
  `has_generation_metadata` flag in place of the JSON in the gallery list, narrow reads in
  the thumbnail and file routes, the live gallery refetch throttled to one per 2 s, and
  `ImageCard` memoized with a per-card selector. The cache header is **parked** (the user's
  call): edit jobs move `v` before they redraw the thumbnail. Detail:
  `docs/dev/scale-plan/README.md` § Stage 11 as built.

## Left open

None of this was part of the gate. Each item is listed so it is not found again later.

- **Parked, each with a trigger** in [scale-plan/README.md](../scale-plan/README.md):
  - the side table for the embeddings and `generation_metadata`, until a dataset nears
    50,000 photo-shaped or 20,000 ComfyUI-shaped images (decision 15);
  - stage 4's full design (bins built in SQL), until a dataset nears 50,000 to 100,000
    images;
  - the score sort inside a small subfolder, until a dataset nears 20,000 images with small
    subfolders;
  - stage 11's cache header.
- **Not checked by hand:** stage 11's index and gallery changes in a browser, stage 4's
  polling against a real job (it is tested against a simulated one), and the Stats page
  under the pin in a browser (the two endpoints' payloads were compared instead).
- **Found by stage 7's measuring, and not fixed:** style scoring's keyset page sorts the
  whole dataset for every page of 2,000 (a `TEMP B-TREE` behind
  `ix_images_dataset_updated_at`), and no test reads that plan. A likely fix is to page on
  `rowid`, which `ix_images_dataset_id` serves with no sort. **Parked on 2026-11-12, by the
  user's call**, with a trigger in `docs/dev/scale-plan/README.md` § Stage 7.
- **Reported and not verified:** nothing clears an image's embeddings when its pixels are
  rewritten in place, and the embedding checks only fill NULL rows, so a cropped or
  upscaled image may keep vectors from its old pixels. **Fixed on 2026-11-12, after this
  record closed.** Half true: a scored row is marked stale and revisited, but a row with
  vectors and no score was not, and a restore had the same gap. Both now clear the
  vectors (`docs/dev/quality/scores-stale.md` § Embeddings are cleared, not marked).
