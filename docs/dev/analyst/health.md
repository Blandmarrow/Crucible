# The health report

`backend/services/health_service.py`, served at `GET /datasets/{id}/health` by
`backend/routers/health.py`. A deterministic list of findings about one dataset, each with
its numbers, the images it is about and the page that fixes it. The family hub is
[../analyst.md](../analyst.md); notes and dismissals as stored rows are [notes.md](notes.md).

## A list, never a score

The report is a list of `Finding`s plus a count per severity (`error`, `warn`, `info`). There
is no 0–100 score, by [decision 21](../decisions/21-dataset-analyst.md): one number hides the
reason, and what is healthy depends on the dataset's goal. Findings are sorted by severity,
then id.

A `Finding` (`backend/schemas/health.py`) carries:

- `id`, stable: dismissals, snapshot summaries and (from phase 2) the LLM refer to it.
- `unit`: what `count` and `total` count. Most findings count `images`; `tags_once` counts
  distinct tags and `duplicates` counts groups.
- `numbers`: the evidence, including the threshold that applied.
- `filter_spec`: a `FilterSpec`, the closed subset of `ImageFilterParams` that selects the
  affected images. `None` when no single filter does (aspect-ratio spread, tags used once, a
  tag in nearly every caption).
- `fix`: a dataset page (a pane `PageType`) and button text.
- `fingerprint`: `{count, bucket}`, stored with a dismissal.
- `was`, `dismissal`, `resurfaced`: see below.

**A finding's filter must select exactly its count.** The Health page's "Show images" sends
`filter_spec` to `GET /images/` as it is. `test_every_filter_spec_selects_exactly_its_count`
in `backend/tests/test_health_findings.py` sends each one to `GET /images/count` and compares;
`test_filter_spec_fields_exist_on_image_filter_params` fails on a `FilterSpec` field the
images route does not know, which it would otherwise silently ignore and show every image.

## The catalogue

Images only: videos are sources and get no findings. An empty dataset reports only
`no_goal`.

| Id | Severity | Fires when | Threshold |
|---|---|---|---|
| `no_goal` | info | `Dataset.goal` is blank | — |
| `small_dataset` | info | fewer images than the minimum | `SMALL_DATASET` (20) |
| `uncaptioned` | warn | `caption_text` is `""`, as the `captioned=false` filter reads it | — |
| `short_captions` | info | 1 to 4 tokens | `SHORT_CAPTION_TOKENS` (5) |
| `long_captions` | warn | 77 tokens or more | `CLIP_TOKEN_LIMIT` (77) |
| `low_resolution` | warn | short side under the target | `target_resolution`, else `DEFAULT_TARGET_SHORT_SIDE` (1024) |
| `aspect_ratio_spread` | info | at least 3 images in buckets each under 5% of the images | `AR_SPARSE_BUCKET_FRACTION`, `AR_SPARSE_MIN_IMAGES` |
| `blurry`, `noisy`, `uniform`, `watermarked`, `nsfw`, `ai_artifacts` | warn or info | the stored quality flag is set | the matching setting, reported in `numbers` |
| `duplicates` | warn | a duplicate group exists | the duplicate setting, reported |
| `unscored_aesthetic`, `unscored_technical` | info | no `aesthetic_score` / `blur_score` | — |
| `stale_scores` | warn | `scores_stale` is set | — |
| `style_outliers` | info | a style run exists and a score is under Q1 − 1.5 × IQR | `STYLE_OUTLIER_IQR_K` |
| `no_license` | info | no effective licence, as `license_missing` reads it | — |
| `tag_everywhere` | info | a tag in 90% of tag-style captions | `TAG_EVERYWHERE_FRACTION` |
| `tags_once` | info | tags used once, once there are 20 distinct tags | `TAGS_ONCE_MIN_DISTINCT` |
| `mixed_caption_styles` | info | the smaller of tag-style and prose captions is 10% or more | `MIXED_CAPTIONS_FRACTION` |

**The quality-flag findings read the stored flags**, which the thresholds produced when the
images were scored. Changing a threshold does not change their counts until a re-score; the
finding reports the threshold now set, so the two can disagree for a while, and that is
correct. `duplicates` counts distinct `duplicate_of` roots over flagged rows: a root is never
flagged itself (`docs/dev/quality/duplicate-detection.md`).

**Counted to match the filter, not taken from the Stats payload.** `uncaptioned` and
`no_license` have near relatives there, and both disagree with the filter a finding links
through: the Stats captioned count strips whitespace where `captioned=false` does not, and
the Stats licence breakdown folds every licence past its 20th into one bucket, which can hide
the unlicensed one. Both are counted in `_health_counts_stmt` with the filter's own
expression instead.

**Tag-style versus prose** uses `similar_captions_service.is_tag_like`, the rule the similar
captions search uses, so the two never disagree about a caption; a whitespace-only caption is
neither. Tag counts are taken over tag-style captions only, once per image, as
`caption_service.get_tag_stats` counts. `get_tag_stats` itself is not used: it returns the top
500 tags, which cannot count the tags used once. **Ties are broken by the tag**, never by
`Counter.most_common`: the counts are fed from sets, whose order varies with the hash seed,
so a tie would name a different tag after a restart (and once made the test flaky).

`tag_everywhere` has no `filter_spec`: `search` is a substring `ILIKE` over captions and
filenames, with `_` a wildcard, so `search=long_hair` selects more than the tag counts.

`aspect_ratio_spread` reads the Stats page's `aspect_ratio_fine` buckets, whose edges are
inclusive above (`_ar_fine_bucket`) where `ar_max` is exclusive, so it has no `filter_spec`
rather than one that is wrong at a bucket edge.

## Cost and the cache

The report reuses `get_dataset_stats` (cached) and adds two statements, both built through
`dataset_service._stats_scan` so a dataset-wide scan carries the Stats index pin:
`_health_counts_stmt` (the counts the Stats payload lacks, in one pass) and
`_health_captions_stmt` (every non-empty caption). Both are in `SCANS` in
`backend/tests/test_stats_plan.py`, and `test_every_scan_the_health_report_sends_is_pinned`
captures what the endpoint really sends. `get_style_distribution`'s own reads are unpinned
and index-covered; they run only when a style run exists.

Two slots in the Stats cache (`docs/dev/statistics.md`), each counting toward
`_STATS_CACHE_MAX`:

- `"health_captions"`, keyed on the image validator alone.
- `"health"`, the whole report, keyed on the image validator, `Dataset.updated_at` (goal and
  target), the six threshold values (`ThresholdSettings` has no `updated_at`), the dismissal
  validator (count and newest `updated_at` of the dataset's dismissal notes, deleted ones
  included, so a removal changes it), the style run's `updated_at`, and the current branch's
  head version id (a new snapshot changes the baseline).

The cached report is shared: never mutate it.

## The card badge

`GET /datasets/` carries `health_summary`, the severity counts of the last report built for
that dataset, so the Datasets page can show a badge without building a report per card. It is
written by `get_health` when the counts change, and is NULL until a report has been built:
it can lag until the Health page is next opened.

**The write must not bump `Dataset.updated_at`**, which is part of both the Stats and the
health validators; a bump would evict both caches on every read. `_write_summary` uses a Core
`UPDATE ... SET updated_at = updated_at`: a Core update still applies the column's
`onupdate` unless the column is set explicitly. It then refreshes the attributes rather than
setting them, since a set marks the row dirty and a later ORM flush would bump the column.
`test_card_summary_is_written_without_touching_updated_at` pins both halves. `get_health`
commits for this write, so a caller inside a larger transaction passes `write_back=False`.

**The write is best effort.** A manual snapshot job holds SQLite's write lock for its whole
file copy, and a GET that waited it out and raised would turn a built report into a 500; a
failed write is rolled back and logged, and the badge catches up on the next read.

## Dismissals and resurfacing

`POST /datasets/{id}/health/dismiss` `{finding_id, reason, permanent}` writes a dismissal note
(`notes.md` § A dismissal is a note), replacing any live one for the same finding. The report
moves a dismissed finding to `dismissed`, with its `dismissal` (the reason, and the count
then). **It comes back** into `findings`, with `resurfaced: true`, when it is not permanent
and its count has more than doubled since; removing the note undoes the dismissal.

## The delta against the last snapshot

`create_snapshot` stores `summary_for_snapshot` on `DatasetVersion.findings_summary` as
`[{id, count, severity}]`, dismissed findings included, since the delta is about the
dataset's state. It is read-only and never fatal: an exception logs and stores NULL, because
a snapshot that failed over a report would cost the user their safety net.

**It runs first, before `create_snapshot` writes anything**, and through the report cache
(`get_health(write_back=False)`), so a warm cache makes it nearly free. With cold caches it
runs the Stats scans, and holding the write lock for those would stall every other writer;
callers that flush before calling (`create_branch`) still hold it, an accepted cost.

The report's `baseline` is the current branch's head version (`DatasetBranch.head_version_id`).
Each finding's `was` is its count there (0 if absent); `resolved` lists findings the baseline
had and the dataset no longer does. A head with a NULL summary (taken before this column, or
whose report failed) sets `baseline.missing`, and every `was` stays NULL: "no baseline".
