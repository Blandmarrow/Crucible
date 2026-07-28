# PM-020: bucket edges copied between two scores on different numeric ranges

### Symptom

Statistics → *Saturation* rendered a single bar, labelled `0–10`, holding every image in
the dataset — on every dataset, whatever it contained. Clicking the bar opened
`BucketPanel` with `saturation_score` bounds of 0 and 10, which every row satisfies, so
the click-through "filter" returned the whole dataset. The panel's edge editor was no
help: its defaults were the same wrong numbers, so a user had no way to see where the
values actually lay.

Nothing errored, no test failed, and the histogram looked like a legitimate finding — a
dataset whose images are all low-saturation.

### Root cause

`saturation_score` is mean HSV S normalised to 0–1 (`ml/technical_scorer.py`), while
`color_score` beside it is Hasler-Süsstrunk colorfulness on roughly 0–100. The two were
added together and the saturation histogram took `color_score`'s bucket edges verbatim —
`[10, 20, 40, 60]` with labels `0–10 … 60+` — in both `dataset_service` and `StatsPage`,
which duplicate every edge constant so a user edit can rebucket client-side.

Every saturation value is therefore below the first edge, and `_bucket` returns
`labels[0]` for anything under `edges[0]` rather than treating it as out of range:

```python
def _bucket(val, edges, labels):
    for i, edge in enumerate(edges):
        if val < edge:
            return labels[i]
    return labels[-1]
```

That is correct behaviour for an open first bucket — `blur_score` and `noise_score` rely
on it — so there is no place for the function to notice that a whole distribution has
collapsed into one end.

Brightness (`luminance_score`), added later on the same 0–1 shape, got fraction edges and
worked, which is what made the saturation panel visibly wrong by comparison.

### Generalizable rule

- **When a new score reuses another score's bucket edges, labels or histogram config,
  check that both produce values in the same numeric range.** Adjacency in a file is not
  evidence of a shared scale — `color_score` (0–100) and `saturation_score` (0–1) are
  declared on consecutive lines. The question to ask of any copied constant block is what
  range the *new* thing is normalised to, and the answer lives in the scorer, not next to
  the copy.
- **Any edge constant duplicated backend/frontend for one distribution needs a test
  pinning them equal**, or the two drift and the histogram jumps the first time a user
  edits the edges — the same rule already written as a comment on `lum_edges`.
- **A histogram that renders one bar across every dataset is a bug report, not a
  finding.** Treat a degenerate distribution as a scale mismatch until proven otherwise.
- More broadly: a bucketing helper that silently clamps out-of-range values into the
  terminal bucket cannot report a mis-scaled input, so the check has to happen where the
  edges are chosen.

### Why it wasn't caught the first time

No test asserted a saturation bucket boundary — or any score's. The distributions were
exercised only for shape (keys present, counts totalling the dataset), which a collapsed
histogram satisfies perfectly.

The one place the distribution was looked at with real spread was
`backend/scripts/bench_scaling.py`, whose synthetic generator emitted
`saturation_score = rng.random() * 100`. That encoded the same wrong assumption as the
bucket edges, so the benchmark's histogram spread convincingly across all five buckets —
the wrong data made the wrong edges look right.

Scoring for real needs cv2, which CI does not have, so no test could produce a genuine
saturation value; the fix works around that by writing the column directly.

### Fix

- `sat_edges` / `sat_labels` in `dataset_service._aggregate_dataset_stats`, and
  `SAT_EDGES` / `SAT_LABELS` / `DEFAULT_EDGES.saturation` in `StatsPage.tsx`, become
  `0.1, 0.2, 0.4, 0.6`. The thresholds are the originals rescaled by 100, not retuned —
  this corrects the scale without also making a judgement call about where saturation
  bands belong. Both sides carry a comment naming the range and its counterpart.
- The Stats lightbox renders `saturation_score` with `.toFixed(2)`, matching the other
  0–1 scores; one decimal on a 0–1 value was the same scale assumption in a third place.
- `bench_scaling.py` generates saturation on 0–1.
- `backend/tests/test_score_histogram_scales.py` pins **every** score's bucket against a
  value in its own range — blur, noise, uniformity, color, saturation, brightness,
  watermark, style similarity and the aesthetic three-band split — so the next scale
  mismatch between two scores sharing an edge shape fails in CI.

A dataset with custom saturation edges saved under
`stats-hist-edges-saturation-${datasetId}` keeps them; the panel's **Reset** button picks
up the corrected defaults. No storage-key bump: the panel was a single bar, so nobody
plausibly customised it. No cache concern either — `_stats_cache` is in-process and
validator-keyed, so a code change implies a restart.

### Status & date

MITIGATED — this instance is corrected and the new test closes the class for every score
that exists today, but nothing stops a *new* score from copying an ill-fitting edge block;
the test only fails once someone adds the score to it.
Last reviewed for staleness: 2026-07-31.
