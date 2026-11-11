# Statistics page

This file covers `StatsPage`'s page shell: its queries and live polling, and server-side stats aggregation with the `DatasetStats` schema. The panels that draw that data (editable histograms, the raw lists, CSV export, bar click-through into `BucketPanel`, the Detections & Masks and Licenses panels) are `docs/dev/statistics-panels.md`. The `GET /images/` params those clicks send are `docs/dev/image-filters.md`.


`frontend/src/pages/StatsPage.tsx` renders the dataset analytics dashboard. A compact subfolder dropdown in the page header (shown only when subfolders exist) scopes all five subfolder-aware queries to a specific subfolder.

## Frontend page

**Panel organization**: Histograms are grouped into 6 collapsible `CategorySection` sections — *Summary*, *Aesthetic & Style*, *Technical Quality*, *Image Properties*, *Captions & Tags*, *Detections & Masks* — rendered below always-visible stat cards. A gear icon (`<Settings>`) in the page header opens a fixed right-side `SettingsDrawer` (zIndex 56, dimmer at 55) where per-category and per-item visibility can be toggled. State is persisted to `localStorage` under key `stats-visibility-v1` via the `useStatsVisibility` hook, which merges saved state with `defaultVisibility()` on load so newly added items default to visible. The `show(cat, item)` helper in `StatsPage` combines category + item visibility into a single boolean used in all render conditionals. Grid column counts for variable-visibility rows are computed by filtering the boolean results and using `repeat(N, 1fr)`.

It makes seven queries:

| Query key | Source | Contents |
|---|---|---|
| `["subfolders", datasetId]` | `GET /datasets/{id}/subfolders` | Subfolder list for the dropdown |
| `["dataset-stats", datasetId, activeSubfolder]` | `GET /datasets/{id}/stats?subfolder=` | All distributions (see schema below) |
| `["tag-stats", datasetId, activeSubfolder]` | `GET /captions/dataset/{id}/tag-stats?subfolder=` | Top 500 tags with counts (computed on the fly by splitting `caption_text` on commas at query time, each tag counted once per caption so a count is a number of images (GPT-035) — there is no longer a `tags` table; `TagStatItem` carries only `tag` + `count`, no category) |
| `["tag-cooccurrence", datasetId, activeSubfolder]` | `GET /datasets/{id}/tag-cooccurrence?limit=15&subfolder=` | Top-15 tag co-occurrence matrix; each cell counts images, with a tag repeated in one caption counted once, as in `tag-stats` |
| `["score-values", datasetId, activeSubfolder]` | `GET /datasets/{id}/score-values?subfolder=` | Raw float arrays for all 9 score fields + `megapixels`, `file_size_mb`, `caption_words`, `caption_tokens` — used for client-side histogram rebucketing. **Fetched on demand**, see `docs/dev/statistics-panels.md` § The raw lists load on demand |
| `["detection-stats", datasetId, activeSubfolder]` | `GET /detection/stats/{dataset_id}?subfolder=` | Aggregates for the "Detections & Masks" section; see `docs/dev/detection.md` |
| `["settings", "thresholds"]` | `GET /api/v1/settings/thresholds` | Live flag threshold values for the quality flag hint text; `staleTime: 60_000` |

**Live polling during active jobs**: `StatsPage` reads `useJobStore` to detect whether a `caption`, `caption_pipeline`, `quality_score`, or `detection` job is currently running for the active dataset. When one is, five of the queries above (`dataset-stats`, `tag-stats`, `tag-cooccurrence`, `score-values`, `detection-stats` — not `subfolders` or `settings`/`thresholds`) receive `refetchInterval: 5_000` so they poll every 5 seconds and distributions update in real-time. `score-values` polls only while it is enabled, since a disabled query ignores its interval. The interval stops automatically when the job completes (Zustand selector returns `false`); `TopBar`'s completion handler fires a final invalidation as a safety net. Polling only runs while the browser tab is focused (`refetchIntervalInBackground` defaults to `false`). The `LIVE_STATS_JOB_TYPES` module-level constant in `StatsPage.tsx` lists the watched job types.

**Query error states**: each query surfaces its own failure rather than rendering silently — the main `dataset-stats` query shows a full-page error block with a `refetch()` Retry button (before the `!stats → "No data"` branch), while `tag-stats`, `tag-cooccurrence`, `score-values`, and `detection-stats` each render a one-line `var(--bad)` notice inside their consuming panel. For `score-values` that panel is each `HistPanel` that needs the lists (its `rawError` prop; the three forms are in `docs/dev/statistics-panels.md` § The raw lists load on demand); nothing else on the page reads the lists, so a failure there no longer touches the page header or **Export Stats CSV**.

All five subfolder-aware stat endpoints accept `subfolder: str | None = Query(None)`. `activeSubfolder` is persisted per-dataset via `STATS_FILTERS_PREFIX` + `loadPersisted` / `useDebouncedPersist` so the chosen subfolder survives navigation. On dataset change (without remount — pane mode) a `prevDatasetId` ref guard effect reloads the per-dataset blob and updates `activeSubfolder`. `BucketPanel` receives `subfolder` as a prop and passes it to `GET /images/`.

## Backend aggregation

**Server-side stats aggregation** (`dataset_service.get_dataset_stats` / `get_score_values`): the async handler does only DB work (row fetch + coverage/flag/embedding count queries), then hands the pure-Python bucketing/aggregation to a thread executor (`run_in_executor`) via the module-level `_aggregate_dataset_stats` helper and a local `_collect` closure respectively, so a large dataset's per-row loop never blocks the event loop while Stats loads. Caption **token** counts come straight from the persisted `Image.caption_token_count` column (`caption_token_distribution`, `score-values.caption_tokens`) — no `tiktoken` call in the request path; **word** counts are still computed from `caption_text` (cheap `split()`). That move changed no response key (`StatsPage.tsx` depends on them).

**Validator-keyed stats cache** (`dataset_service.py`, `_stats_cache` + `_stats_cache_get`/`_stats_cache_put`/`_image_validator`): both functions above pull *every* image row in scope into Python, and StatsPage live-polls them, so an idle Stats tab re-read the whole table every few seconds. Each call now first runs a cheap validator query — `SELECT count(*), MAX(updated_at)` under the same dataset/subfolder filter (`_image_validator_stmt`; the dataset-wide form reads only the covering index `ix_images_dataset_updated_at`, 294 ms → 7.1 ms at 100k rows, M9 stage 11, guarded in `backend/tests/test_image_sort_plan.py`), plus `Dataset.updated_at` for `get_dataset_stats` (whose `license_breakdown` coalesces over the dataset default) — and serves the previous payload when the validator is unchanged. Keys are `(dataset_id, subfolder, "stats" | "scores" | "style")`; the cache is a module-level dict capped at `_STATS_CACHE_MAX` (64) entries, dropping oldest-inserted. Staleness is bounded to writes that leave `(count, max updated_at)` identical, which no ORM or Core write can do — `Image.updated_at` carries an `onupdate`. Two consequences for callers: the returned dict **is** the cached object, so never mutate a stats/score-values payload in place; and a new column that affects the payload but lives on `Dataset` (not `Image`) needs `Dataset.updated_at` to be bumped when it changes, or the cache will not notice. Covered by `backend/tests/test_stats_cache_http.py`. SQL-side bucketing was considered and rejected — it would duplicate the thirteen metrics' bucket-edge definitions that already exist as `DEFAULT_EDGES` in `StatsPage.tsx`, and would not help `get_score_values` at all.

**The third slot, `"style"`, and what it costs.** `get_style_distribution` (see
`docs/dev/quality/style-match.md`) caches here too. With three payload kinds per scope rather
than two, `_STATS_CACHE_MAX`'s 64 entries covers a working set of about 21 datasets instead
of about 32. That is the accepted cost and **not** a reason to raise the constant: the style
payload is ~200 bytes against the other two's per-row arrays, so the entries it evicts are
the cheapest in the cache by a wide margin. Its validator is `_style_validator` rather than
`_image_validator` — the image half plus the dataset's `StyleSimilarityRun.updated_at`,
because that payload embeds a row from another table. It is also the one slot with no
subfolder dimension, always keyed `(dataset_id, None, "style")`, so an image reads the same
percentile in a filtered pane as in an unfiltered one.

**`DatasetStats` subfolder invariant**: When subfolder is not None, all out-of-row-scan queries in `get_dataset_stats()` must include `.where(Image.subfolder == subfolder)`: (a) embedding count, (b) score coverage (`func.count` per score column), (c) quality flag counts (`json_extract` + `SUM(CASE …)` per flag key). `total_size_mb` derives from the filtered `file_sizes_mb` list; `ds.total_size_bytes` only used for the all-images case.

**Scope: images only.** `DatasetStats` carries no video field and `get_dataset_stats` scans `images` alone, so a dataset holding 40 videos shows nothing about them anywhere on this page — the counts, every histogram, and `total_size_mb` (which falls back to `ds.total_size_bytes`, itself images-only by design) all describe the dataset's images. `Dataset.video_count`/`video_size_bytes` are separate columns, surfaced on the dataset card rather than here; see `docs/dev/video/storage.md` § Stats.

**`DatasetStats` schema** (`backend/schemas/dataset.py`; all computed in one row-scan in `dataset_service.get_dataset_stats()`):

| Field | Description |
|---|---|
| `blur_distribution` | 6-bucket Laplacian variance |
| `noise_distribution` | 6-bucket smooth-region std dev |
| `uniformity_distribution` | 5-bucket grayscale std dev |
| `watermark_distribution` | 10 equal bins, 0–1 |
| `color_distribution` | 5-bucket Hasler-Süsstrunk colorfulness, 0–100, edges `10, 20, 40, 60` |
| `saturation_distribution` | 5-bucket mean HSV S, **0–1**, edges `0.1, 0.2, 0.4, 0.6`. The two are not one row: saturation shipped with `color_distribution`'s 0–100 edges and rendered a single bar (`docs/dev/postmortems/PM-020-saturation-histogram-scale.md`). The edges must stay numerically identical to `SAT_EDGES` in `StatsPage.tsx`, which rebuckets client-side once a user edits them |
| `luminance_distribution` | 5-bucket mean grayscale, edges `0.15, 0.3, 0.5, 0.7`. Panel title **Brightness**, in the *Technical Quality* category (a fifth panel in a fixed `repeat(2, 1fr)` grid — it wraps). The edges must stay numerically identical to `DEFAULT_EDGES.luminance` in `StatsPage.tsx`, or the histogram jumps the first time a user edits them |
| `megapixel_distribution` | 7-bucket width×height/1M |
| `file_size_distribution` | 6-bucket MB ranges |
| `file_size_summary` | `{min_mb, median_mb, p95_mb, max_mb}` |
| `aspect_ratio_fine` | 8 common AR buckets |
| `caption_length_distribution` | 6-bucket word count |
| `caption_token_distribution` | 6-bucket GPT-2 BPE token count (edges: 1, 20, 40, 60, 77) read from the persisted `Image.caption_token_count` column (no `tiktoken` in the request path — see the token-count note above); the 77+ bucket flags CLIP-truncated captions |
| `style_similarity_distribution` | 10 equal bins, 0–1 |
| `quality_flag_counts` | `{blurry, noisy, uniform, watermarked, duplicate, nsfw, ai_artifacts}` |
| `score_coverage` | Per-score type computed count. `score_coverage["technical"]` counts **`blur_score` only**, so it over-reports for every technical column added since: a dataset last scored before quality-v2 (color/saturation) or before the video arc (luminance) reports 100% technical next to an empty histogram for that column. `HistPanel`'s `footer` prop carries the re-score hint that explains it, passed by `rescoreHint(entries)` in `StatsPage` on the six panels the technical scorer writes (blur, noise, uniformity, brightness, color richness, saturation) and deliberately **not** on watermark, aesthetic or style similarity, whose own coverage counts make an empty histogram honest. An image the technical scorer could not read records NULL for all six scores rather than 0.0, so it is *absent* from every technical histogram and counts as unscored here — see `docs/dev/quality/scoring.md` for that failure contract |
| `score_means` | `{score column: mean}` over the nine columns in `_STATS_SCORE_FIELDS`, the list `get_score_values` reads too. Summed in the row loop, in row order from 0.0, which is how the page summed the raw lists before stage 4, so a mean reads as it did and costs no extra scan. NULL is skipped; a column nothing has scored is **absent** (the page shows a dash, the CSV an empty cell). Unrounded. Feeds the "Mean aesthetic" card and the CSV's MEAN SCORES (`docs/dev/statistics-panels.md` § CSV export). `backend/tests/test_stats_score_means_http.py` holds it to the raw lists |
| `license_breakdown` | `{effective license id: count}`. The one field **not** from the row scan: it is aggregated in SQL as `COALESCE(NULLIF(images.license, ''), <dataset default>, '')` with the dataset's default inlined as a literal (the scope is a single dataset, so no join is needed), grouped and scoped by `_stats_scan` like every other statement here — including the subfolder invariant. `""` is a real key meaning "no license recorded". **Bounded**: only the top `LICENSE_BREAKDOWN_LIMIT` (20) buckets come back by id; the rest are summed under the synthetic key `LICENSE_BREAKDOWN_OTHER_KEY` (`"__other_licenses__"`) so the counts still total the dataset. `other:<free text>` is scraper-sourced and unbounded, so an uncapped response is one bucket per image. That key is **not** a license id — any per-license listing must exclude it and report it separately |

See `docs/dev/quality/style-similarity.md` for the style-similarity scoring flow and embedding types that populate `style_similarity_distribution`.

Default bucket edges are defined as `DEFAULT_EDGES` in `StatsPage.tsx`. Edges on the backend (`dataset_service.py`) are used only for pre-computing the initial distributions returned by `/stats`; when the user customises edges, `rebucketValues()` runs entirely client-side against the raw `score-values` arrays — no rebucketing call to the backend, only the one fetch of the arrays themselves (`docs/dev/statistics-panels.md` § The raw lists load on demand).

## The Stats scans and their index pin

Past the cache validator, a Stats load reads every row of its scope seven times: six statements for `/stats` (the row scan, the coverage counts, the `aesthetic_model` group, the flag sums, the embedding count, the license group) and one for `/score-values`. Each is built by a `_stats_*_stmt` function in `dataset_service.py` (`_score_values_rows_stmt` for the last), and every one goes through `_stats_scan`, which scopes the statement and decides its index.

**A dataset-wide scan is pinned to `ix_images_dataset_id`** ([decision 15](decisions/15-big-columns-stay-in-the-row.md); the mechanism is `indexed_by`, `docs/dev/database.md` § SQLite engine and schema conventions). Left to choose, SQLite reads these through `ix_images_dataset_updated_at`, the index M9 stage 11 added for the validator, and so visits the dataset's rows in `updated_at` order: scattered across the file, a random read a row. `ix_images_dataset_id` lists them in table order. On the row scan that took a cold load from 13.7 s to 4.4 s at 100,000 rows and from 95 s to 14 s at 1M, and every scan was faster or the same warm (the stage 7 addendum of `backend/scripts/scaling_bottlenecks_report.md`).

**Two things are not pinned, on purpose.** The validator reads `ix_images_dataset_updated_at` as a covering index and touches no row; under any other index it reads them all. A subfolder-scoped scan is served by `ix_images_dataset_subfolder`, which lists that subfolder's rows in table order already. Pinned to the dataset's index instead, it would read every row of the dataset to answer for one folder: at 100,000 rows the row scan of a subfolder holding 30% of them took 512 ms against 278 ms warm, and that of a 20-row subfolder 455 ms against under a millisecond (5.1 s against 19 ms cold).

**The pin moves one number in its last digits, and then holds it still.** The rows a pinned scan returns are the same rows; their order is not, and `score_means` is a float sum in row order. Against the unpinned scan each mean differed at about the 14th significant digit on the 100,000-row bench file (mean aesthetic 5.003639352915172 against 5.003639352915125). The page prints a mean with `toFixed`, so nothing a user reads changed, and nothing stores the value. The old order was the less stable one: through `ix_images_dataset_updated_at` it changed whenever any row was written, so editing one caption could shift a mean's last digits with no score changed. Table order changes only when rows come and go. `test_stats_plan.py` holds both the order and a mean that does not move under an unrelated edit.

**Build a new Stats statement through `_stats_scan`.** `backend/tests/test_stats_plan.py` reads the plan of every builder, then calls both endpoints and reads every statement the driver is sent, and fails on an images statement that is neither the validator nor pinned. Time the pin again when an index on `images` is added or dropped, when one of these statements changes shape, or when the row layout is reopened: `python -m backend.scripts.bench_split_layout --db <kept bench file> --layouts row --cold` compares SQLite's own choice with this index.

## Panels, exports and drill-down

The browser-side half is in `docs/dev/statistics-panels.md`: § The raw lists load on demand, § CSV export, § Bucket drill-down (editable histograms, clickable bars) and § Category panels (Detections & Masks, Licenses, `ImageLightbox`).
