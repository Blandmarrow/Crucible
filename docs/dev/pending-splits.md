# Pending documentation splits

A handoff queue, not a topic file — it has no Documentation Map row and nothing reads it
to learn about a subsystem. One entry per doc that needs restructuring, naming the seam.

It exists because the two halves of a split want opposite conditions. **Choosing** the
seam is best done by whoever just worked the file and can see which section stopped
belonging — knowledge that evaporates when the session ends. **Executing** the split is
an exhaustive sweep (move sections, update every inbound reference, fix `§` pointers, add
a Map row) that wants a fresh context and goes wrong in a long one. So the seam is
recorded here at the end of the session that trips the budget, and executed at the start
of the next session that would append to that file.

Nothing here is ever a reason to compress prose to get back under budget. See the
`doc-maintenance` skill, § Splitting an oversized doc.

## How entries work

The heading is the file's repo-relative path, exactly as `scripts/check_docs.py` prints
it — the checker matches on it in both directions.

```markdown
## docs/dev/example.md

- **Moves:** § Section A, § Section B
- **New file:** docs/dev/example-detail.md (mirrors docs/example.md)
- **Why here:** the two sections are about the router; the rest is the service layer
- **Watch for:** § Section A is referenced from docs/dev/other.md
```

`Watch for` is the field worth spending time on — it carries what the next session cannot
cheaply rediscover. The checker catches broken paths and anchors after the fact; it
cannot tell you which seam was the right one.

**Write a proposed new filename as plain text, never in `backticks`.** The file does not
exist yet, and an inline-code path that does not resolve is a check FAIL — an entry that
breaks CI is worse than no entry.

Three bands govern an entry's lifetime. A file **over budget** with no entry is a WARN
(record one). A file with an entry that has since dropped **under 60%** of budget is a
WARN the other way (the split evidently happened — delete the entry). Between the two is
the working band: entry recorded, split pending, no warning. A file that is a dumping
ground while sitting *below* that 60% floor — so it would read as already-split — takes a
`## docs/dev/example.md (structural)` heading, whose trailing marker exempts it from the
staleness sweep while still recording the seam.

## Queue

### Seam recorded, split pending

`bulk-ops.md` and `versioning.md` are over budget today; the other three are queued because
they need restructuring, not because they are numerically over — the queue holds both. All
five carry `##` headings, so `scripts/check_docs.py`'s per-section breakdown prints the
counts quoted below and the seam is a measurement rather than a judgement call. The counts
are the sections' own; the file total is larger by its intro and heading lines.

## docs/dev/bulk-ops.md

- **Moves:** § Detection-driven cropping (879 w) and the three `###` subsections under
  § Bulk image operations — § Renumber's two-phase rename (287), § Batch resize and aspect
  crop (267), § Rebuilding thumbnails (566). 1,999 w together.
- **New file:** docs/dev/bulk-image-jobs.md
- **Why here:** the file holds two subjects that only share the word "bulk". What stays is
  **scope-filtered metadata editing** — `_apply_bulk_filters`, the endpoint table, caption
  find/replace/regex, `BulkEditPage`'s tabs — where the interesting content is *which rows
  are selected*. What moves is **bulk jobs that rewrite pixels or files**, where the
  interesting content is the per-image loop: PM-013 commit ordering, thumbnail-stem
  collisions, `remap_detections_for_crop`, the `thumbnails_stale` counter. A reader
  debugging one never wants the other.
- **Leaves:** 3,672 total → ~1,640 / ~2,000 (47% / 57%).
- **Watch for:** § Renumber's two-phase rename explains the `bulk-rename` table row, which
  stays behind — the row must gain a cross-file pointer, not a copy of the explanation.
  § Rebuilding thumbnails is the *repair* for the `thumbnails_stale` counter that
  `docs/dev/frontend-jobs.md` surfaces and that `lut.py`/`upscaling.py`/`images.py` write, so
  it travels with the jobs, not with the endpoint table that exposes it. § Detection-driven
  cropping is referenced from `docs/dev/detection.md`, which is itself queued below and would
  be the wrong place to absorb it — `detection.md` owns the *detector*, this owns the crop
  job that consumes its boxes. Four CLAUDE.md Documentation Map triggers point here
  (`BulkEditPage`, `CropToDetectionForm`, `bulk-*` endpoint, `detection_crop_rect`); the last
  two follow the new file.

## docs/dev/export.md

- **Moves:** § Provenance manifests (857 w) — `CREDITS.md` / `licenses.csv`, the untrusted-value
  neutralisers (`_md_inline`, `_md_link`, `_csv_cell`), the manifest lifecycle and the
  supersede rule
- **New file:** docs/dev/export-manifests.md
- **Why here:** it is the one section that is about a *document the export ships*, not about
  the export loop. The other three sections (shared loop 495, filters and license controls
  965, output options 797) are one subsystem read together.
- **Leaves:** 3,200 total today → ~2,340 / ~900. The remainder is still 67%, so this buys a
  little room, not a lot.
- **Watch for:** `docs/dev/provenance.md` already owns the license vocabulary and is itself
  at 91%, so it **cannot** absorb this — a third file is implied, which is why the new name
  is `export-manifests`, not a merge. § Filters and license controls (965 w) is the larger
  section but the worse seam: it is half backend `_is_excluded`, half `ExportPage` panel, and
  the two are described in each other's terms. This file's longest paragraph is 246 words
  against the 250 limit, so it has no compression headroom either.

## docs/dev/detection.md

- **Moves:** § ML inference (873 w) plus § Model and task matrix (437 w)
- **New file:** docs/dev/detection-inference.md
- **Why here:** the `backend/ml/` predictors are a different subsystem from the router that
  calls them — the SAM3 loader alone is a third of the file and changes on upstream's clock,
  not Crucible's. Router and endpoints (840) + Storage (37) + Frontend surfaces (623) is the
  `/detection` request path, read together.
- **Leaves:** 2,905 total → ~1,570 / ~1,320.
- **Watch for:** § Deferred work & upstream constraints lives inside § ML inference and is a
  *status note*, not reference — it dates fastest of anything in the file and must not travel
  into a new file unexamined. `docs/dev/ml-models.md` (2,425 w) is the sibling that already
  owns model loading and VRAM, so check whether a piece belongs there before minting the new
  file.

## docs/dev/statistics.md

- **Moves:** § Backend aggregation (860 w), and probably the `GET /images/` filter table that
  currently sits inside § Bucket drill-down (~600 w of that section's 736)
- **New file:** docs/dev/statistics-backend.md
- **Why here:** the Frontend/Backend boundary. § Frontend page (501), § CSV export (290) and
  § Category panels (428) are all `StatsPage.tsx`; `get_dataset_stats`, the validator-keyed
  cache and the `DatasetStats` schema are `dataset_service.py`.
- **Leaves:** 2,897 total → roughly 1,400 / 1,450, depending on where the filter table lands.
- **Watch for:** the seam is **not clean**, and that is the whole difficulty. The `GET /images/`
  filter table is backend content living under the drill-down that consumes it, so leaving it
  behind makes the new file incomplete and taking it makes § Bucket drill-down a stub that
  points elsewhere for its own filter params. Decide that before starting. The table's
  `license_filter` row also has to stay consistent with `docs/dev/export.md` and
  `docs/dev/provenance.md`, which describe the same param with different `""` handling.

## docs/dev/versioning.md

- **Moves:** § Backend's `### Service` (1,434 w) — `version_service.py`, chiefly
  `restore_snapshot`'s four passes
- **New file:** docs/dev/versioning-restore.md
- **Why here:** that one subsection is 40% of the file and is the only part of it that is
  hard. Everything else (Guards 310, Model and storage 430, `### Router` 232,
  `### Copy-on-write injection points` 431, Frontend 527, Provenance mirror and regression
  tests 161) is reference a reader skims; the restore passes are read line by line when
  something has gone wrong.
- **Leaves:** 3,594 total (over budget as of 2026-07-31) → ~2,160 / ~1,435. That is 62% for
  the remainder, marginally over the 60% target, so consider taking
  `### Copy-on-write injection points` with it (see Watch for) — that lands ~1,730 / ~1,865.
- **Watch for:** `### Copy-on-write injection points` is a table of *call sites* in other
  routers and belongs with whichever half documents the two hooks — the hooks themselves are
  in `### Service`, so it likely travels with it despite reading like router content. The
  CLAUDE.md invariant "Nothing fallible between an irreversible filesystem mutation and the
  `commit()`" and the DB-before-filesystem invariant both point at restore's Pass 2/Pass 3,
  so the new file is a cross-reference target from CLAUDE.md, not only from siblings.
