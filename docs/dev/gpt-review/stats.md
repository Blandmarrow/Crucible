# Statistics and calculation findings

These are the verified findings in the score bucketing, percentiles and tag counts. Read
`docs/dev/statistics.md` and `docs/dev/image-similarity.md` (the percentile contract) first. The
probe is `docs/dev/gpt-review/probes/backend/probe_stats.py`.
Conventions are in `docs/dev/gpt-review/README.md`.

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
