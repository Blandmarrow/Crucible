# Statistics and calculation findings

These are the verified findings in the score bucketing, percentiles and tag counts. Read
`docs/dev/statistics.md` and `docs/dev/image-similarity.md` (the percentile contract) first. The
probe is `docs/dev/gpt-review/probes/backend/probe_stats.py`; its `pct_run.mjs` transpiles the real
`percentile.ts` into the OS temp dir. GPT-018's browser half is `docs/dev/gpt-review/probes/frontend/gpt018.spec.ts`.
Conventions are in `docs/dev/gpt-review/README.md`.

## GPT-018 — [P3, GPT said P2] Histogram click-through also includes values on the bucket's upper edge

**Verdict:** CONFIRMED (API and browser). NEW. PM-020 is the same family, but it concerned a scale
mismatch, not edge inclusivity.

**Mechanism.** The comparisons on each side disagree:
- **Server bucketing** (`dataset_service._bucket`, ~265-269) is half-open: `val < edge`.
- **The client** (`StatsPage.tsx` `scoreEntries`, ~120-137) sends the bucket's upper edge as
  `max_score`.
- **The list filter** (`routers/images.py` ~209-217) applies `<=`.

`AESTHETIC_FILTER_MAP` (`StatsPage.tsx:91-95`) overlaps at 4 and 6 the same way.

**Observed:**
- `test_gpt018_saturation_boundary_click_through`: saturation values NULL, 0.2, 0.4, 0.6 and 1.0.
  Stats reports `0.2–0.4: 1, 0.4–0.6: 1`, while the clicks return 2 and 2.
- `gpt018.spec.ts`: the bar "0.2–0.4: 1" opens a panel saying "2 images".
- `test_gpt018_aesthetic_edges` shows the overlap on the aesthetic map.
- Controls: `test_gpt018_nonboundary_control`, and `test_gpt018_other_filters_use_strict_upper`.

**Affected:**
- Every `scoreEntries` histogram: blur, noise, uniformity, color, saturation and luminance.
- Watermark (`wmEntries`) and style similarity (`ssimEntries`).
- File size, in principle.

**Not affected:** megapixels, aspect ratio, and caption word/token counts, whose filters already use
strict `<`.

Only a score exactly on an edge is double-counted. Scores are stored rounded to 3–4 decimals, so
this is rare but reachable.

**Fix notes.** Do not change the general `max_score` semantics, which users type by hand. Instead,
add an exclusive-upper parameter (for example `max_score_exclusive`, or `max_lt`) to
`ImageFilterParams` (`docs/dev/image-filters.md`) and use it for histogram and band click-through.
The last bucket stays open-ended.

## GPT-019 — [P3] Percentile ties use the opposite edge from the documented convention

**Verdict:** CONFIRMED. **The code contradicts the docs.**

**Mechanism.** `percentileOf` (`frontend/src/utils/percentile.ts:55`) returns on the first
`score < quantiles[i]`. A score equal to a run of repeated breakpoints therefore lands on the run's
**high** edge.

The `span > 0 ? … : 0` low-edge branch (~:59) is dead code. Reaching index `i` already forces
`span > 0`.

The comments at `percentile.ts:34-37` and `:57-58`, at `dataset_service.py:321-324`, and in
`docs/dev/image-similarity.md` (the low-edge `span > 0` guard) all promise the low edge.

**Observed** (`test_gpt019_percentile_ties`): for backend quantiles of [0, 0.5, 1], 0.5 sits at
indices 6–14. Scoring 0.5 returns **70**, where the documented answer is 30. Interpolated points
and the extremes are correct.

This needs fewer than about 21 scored images, or exactly repeated scores.

**Fix notes.** Decide on a convention: low edge, high edge, or midpoint. Then implement equality
explicitly (for example, find the first index where `quantiles[i] >= score`). Fix the comments and
the doc to match, and add a frontend unit test using backend-produced tied quantiles.

## GPT-035 — [P3, GPT said P2] Repeated tags inflate the co-occurrence heatmap and tag-stats counts

**Verdict:** CONFIRMED. NEW.

**Mechanism.** `dataset_service` tag co-occurrence (~2208-2237) splits the caption on commas and
strips whitespace, but does not dedupe within a caption. Every duplicate index is incremented, so a
tag that appears k times contributes k² to its diagonal cell. `StatsPage.tsx:827` labels the cells
"N images". The sibling endpoint `caption_service` tag-stats (~262-267) counts repeats the same way.

**Observed** (`test_gpt035_cooccurrence`): the caption `cat, cat, blue` produces
`[[4,2],[2,1]]`. The control `cat, blue` produces `[[1,1],[1,1]]`.

**Fix notes.**
- Dedupe tags per image, preserving order, before counting, in both endpoints.
- Decide whether the counts should be case-normalized, and stay consistent with `dedupe_tags`
  (`docs/dev/tag-consolidation.md`).
- Add a multi-image regression test.
