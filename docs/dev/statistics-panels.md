# Statistics page: panels, exports and drill-down

This file covers the browser side of `StatsPage`: how a histogram panel (`HistPanel`) asks for the raw `score-values` lists only when it needs them, editable histogram edges, the two CSV exports, bar click-through into `BucketPanel` and its half-open filter rule, and the Detections & Masks and Licenses panels. The page's queries, live polling, the server-side aggregation and the `DatasetStats` schema these panels read are in `docs/dev/statistics.md`; the `GET /images/` params the clicks send are in `docs/dev/image-filters.md`; the detection endpoint behind the Detections & Masks section is in `docs/dev/detection.md`.

## The raw lists load on demand

**`score-values` is enabled only while a panel on the page needs it** (M9 stage 4). The
lists hold one entry per image, thirteen times over, and the page used to load them on
every visit and re-poll them every 5 s during a job, though the default histograms, the
means and the CSV all come from `/stats`. Two things need them: a `HistPanel` with saved
custom edges, and one whose edge editor is open (fetched as it opens, so Apply has them).

Each `HistPanel` reports its own need through `onNeedRaw(storageKey, needs)`, and reports
`false` when it unmounts, so a hidden or collapsed panel asks for nothing. `StatsPage` keeps
the keys in `rawNeeders` and sets `enabled: !!datasetId && needRaw`. `needRaw` counts only
keys ending in the current dataset id: on a dataset switch the old panels' keys are still in
the set for one render, and counting them fetched the new dataset's lists for nothing.

Three rules follow, each pinned by `frontend/e2e/stats.spec.ts`:

- **`canEdit` is configuration, not loaded data** (`defaultEdgeStr`, `fb` and `onNeedRaw`
  all passed). It used to require `rawValues !== undefined`, which would now hide every
  pencil, and with it the only thing that asks for the lists. `onNeedRaw` is in the test
  because an editable panel without that wire would wait on a list nobody fetches.
- **A custom panel with no list says so.** It shows "Loading custom buckets…" or, on
  `rawError`, a `var(--bad)` line. It never falls back to `entries`, which would draw the
  server's default buckets under the "custom" badge. The badge follows `usingCustom` (saved
  edges that parse), so it is on exactly when the bars are custom or pending.
- **A failed load is reported in every state, not only the empty one** (`rawNotice`). A
  failed refetch keeps the previous list, so a custom panel goes on drawing bars that have
  stopped following the dataset; it gets a "may be out of date" line under them. An open
  editor with no list gets a line saying custom buckets cannot be drawn yet. The page-level
  banner that used to cover both is gone, since the lists are no longer the page's.
- **`HistPanel` is keyed by its `storageKey`** (the wrapper around `HistPanelBody`). One
  instance carried across a dataset switch wrote the old dataset's edges under the new
  dataset's key before re-reading them. It needed the destination's stats already cached,
  since otherwise the page unmounts while they load.

The nine `invalidateQueries(["score-values", …])` call sites elsewhere in the app stay. An
invalidated query that is disabled refetches when it is next enabled.

## CSV export

**CSV export**: Two buttons in the page header export data without any backend call — all data is already loaded in the component.

- **Export Stats CSV** (`downloadCsv`) — never disabled: it reads `stats` and `detStats` alone, not the `["score-values"]` lists. Produces a two-column key-value CSV with labeled section headers (`## SECTION NAME,`). Sections: SUMMARY (dataset_id, dataset_name, image_count, captioned_count, caption_coverage_pct, total_size_mb, avg_width, avg_height), FILE SIZE SUMMARY (min_mb, median_mb, p95_mb, max_mb), QUALITY FLAGS, SCORE COVERAGE, MEAN SCORES (from `stats.score_means`, four decimals), and one section per histogram distribution (aesthetic, blur, noise, uniformity, watermark, color, saturation, brightness, style similarity, megapixels, file size, aspect ratio, format, caption word count, caption tokens). Aspect ratio falls back to `aspect_ratio_distribution` (coarse) when `aspect_ratio_fine` is empty — matches the chart's own fallback. Distribution key names are sanitized with `.replace(/[^a-z0-9]/gi, "_").replace(/^_+|_+$/g, "").toLowerCase()` (the final strip removes trailing underscores produced by labels ending in `+`, e.g. `"21:9+"` → `aspect_ratio_21_9` not `aspect_ratio_21_9_`). `dataset_name` is passed through `escapeCsv()` since it is a user-controlled string. Filename: `dataset-{name}-stats.csv` (name sanitized via `safeFilename()`; falls back to `{id}` if name is empty).
- **Export Tags CSV** (`downloadTagsCsv`) — disabled when `tagStats.length === 0`. Produces a tabular CSV with header row `tag,count`; tag values are run through `escapeCsv()` (quotes fields containing `,`, `"`, or newlines). Filename: `dataset-{name}-tags.csv` (same sanitization/fallback as stats export).

`escapeCsv(v)` wraps the value in double-quotes and escapes internal double-quotes (`"` → `""`) when the value contains any of `,`, `"`, `\n`, `\r`. Used for `dataset_name` in `downloadCsv` and for all tag values in `downloadTagsCsv`. `safeFilename(name)` strips characters illegal in filenames (`/\:*?"<>|`) before the name is used in a `triggerDownload` filename — use it whenever a user-supplied string appears in a download filename. `triggerDownload(csv, filename)` is the shared blob-download helper (Blob → `URL.createObjectURL` → synthetic `<a>` click → `URL.revokeObjectURL`).

## Bucket drill-down

**Editable histograms (HistPanel)**: Every score/metric histogram has a pencil icon that opens an inline edge editor. The user types comma-separated boundary values (e.g. `"4, 6"` for aesthetic score), presses Apply or Enter, and the chart immediately rebuckets using the raw value arrays (loaded as the editor opens: § The raw lists load on demand). A "custom" badge appears in the panel title when non-default edges are active; Reset restores the defaults. Aspect ratio and file format histograms are non-editable (no raw values to rebucket). When a customised bar is clicked, `BucketPanel` still opens with the correct `min`/`max` filter derived from the custom edges. `HistPanel` accepts an optional `storageKey?: string` prop; when provided, the active edge string is persisted to that `localStorage` key on change and restored on mount (a dataset switch remounts the panel, which is keyed by `storageKey`). Score histograms pass a literal `stats-hist-edges-{metric}-${datasetId}` key — thirteen of them — so custom bucketing survives navigation and one metric's edges never leak to another. **Not** derived from `STATS_FILTERS_PREFIX`, which is the filters blob alone; these are a fourth class of component-local key (`docs/dev/persistence.md`).

**Clickable bars → BucketPanel**: Charts are hand-rolled — `CssHist` renders each bar as a `hist-bar` div (no charting library). Every histogram bar carries a `filter` object in its chart-entry data; the bar's own `onClick={() => e.filter && onBarClick?.(e)}` opens a `BucketPanel` modal. The panel queries `GET /images/` with the filter params and shows up to 200 thumbnails. Quality flag cards are also clickable.

**A bar's filter must be as half-open as its bucket.** Server bucketing (`dataset_service._bucket`, `_watermark_bucket`) and `rebucketValues` both place `edge <= val < next_edge`, so every score bar sends its upper edge as the exclusive `max_score_lt`, never the inclusive `max_score`; file size sends `ceil(edge × 1 MiB) - 1` bytes; and the watermark and style-similarity "0.9–1.0" bar leaves its max open, because the server folds 1.0 into it (the `detScoreEntries` rule). An inclusive edge lists a value sitting on it under two bars while the counts show it under one (GPT-018). `backend/tests/test_stats_click_through_edges.py` and `frontend/e2e/stats.spec.ts` pin it.

**`GET /images/` filter extensions** — the params every clickable bar sends are documented in
`docs/dev/image-filters.md`; the Statistics-specific ones are the `detection_*`,
`mask_coverage_*`, `caption_words_*`, `caption_tokens_*` and `license_missing` rows.

## Category panels

**Detections & Masks section** (`detections` category in `STATS_CONFIG`): a QA surface for detection/mask data, fed by a dedicated `GET /detection/stats/{dataset_id}?subfolder=` endpoint (in `backend/routers/detection.py`, kept out of `get_dataset_stats` so live-polling refetches just this query). The endpoint returns stat-card totals (`total_detections`, `images_with_detections`, `images_without_detections`, `distinct_labels`, `bbox_only_count`), a `label_distribution` (top 30), `model_distribution`, `score_histogram` (10 bins + an `"unscored"` bucket for NULL/manual scores), `coverage_histogram`, and `detections_per_image`. All aggregates join `Detection.image_id == Image.id` and honor the `DatasetStats` subfolder invariant (`docs/dev/statistics.md` § Backend aggregation; exact `Image.subfolder == subfolder`). **Coverage** = per-image `SUM(mask_area)` clamped to 1.0 — an approximation of the exported union mask (overlaps overcount), covering only images with ≥1 detection; it flags <2% / >95% outliers, not exact areas. The frontend query is `["detection-stats", datasetId, activeSubfolder]`; `"detection"` is in `LIVE_STATS_JOB_TYPES` so the section live-updates while a detection job runs. The coverage and detections-per-image histograms ship **fixed-edge** (`HistPanel` with no `rawValues`, so no pencil/rebucket editor); the model-breakdown bars are non-clickable (model is not a `GET /images/` filter). All other bars carry a `filter` and open `BucketPanel` via the `detection_*` / `mask_coverage_*` params in `docs/dev/image-filters.md`. `downloadCsv` emits the detection summary + each distribution as CSV sections.

**Licenses panel** (`licenses` in the `summary` category of `STATS_CONFIG`): renders `DatasetStats.license_breakdown` as a table — License / Commercial use / Images / Share — sorted by count, with labels and commercial-use verdicts from `constants/licenses.ts` (so an `other:<free text>` id shows its free text and an unknown commercial verdict). The `__other_licenses__` key (see the `license_breakdown` row of the `DatasetStats` table in `docs/dev/statistics.md`) is **excluded from the table** and reported as a footnote beneath it ("+ N image(s) across smaller license buckets, not listed individually"), because it is a count, not a license id. A warning badge in the panel header shows the unlicensed count when `license_breakdown[""] > 0`, and the panel then also renders an explainer under it: those images have no license at either level, exports include them by default, and they are listed as unlicensed in `CREDITS.md`. Every row is a click-through into `BucketPanel`: a normal license sends `license_filter=["<id>"]`, the "No license" row sends `license_missing=true` (see `docs/dev/image-filters.md` for why those are two different params — the shared listing-contract table moved there). See `docs/dev/provenance.md` for the inheritance model behind the effective license.

**ImageLightbox**: Clicking a thumbnail in `BucketPanel` opens a full-resolution lightbox with prev/next navigation, metadata footer, a "View Details →" link to `/datasets/:datasetId/image/:imageId`, and a two-step **Delete** button. Deleting an image removes it from the panel's TanStack Query cache via `queryClient.setQueryData` (no refetch) and invalidates `dataset-stats`, `detection-stats`, `tag-stats`, `score-values`, and `tag-cooccurrence` queries. A per-thumbnail ×-on-hover delete button with an inline confirm overlay provides the same action from the grid.

