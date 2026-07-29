# Statistics page

This file covers `StatsPage`: its queries and live polling, server-side stats aggregation and the `DatasetStats` schema, editable histograms, CSV export, the `GET /images/` filter extensions that back click-through, and the Detections & Masks and Licenses panels.


`frontend/src/pages/StatsPage.tsx` renders the dataset analytics dashboard. A compact subfolder dropdown in the page header (shown only when subfolders exist) scopes all five subfolder-aware queries to a specific subfolder.

## Frontend page

**Panel organization**: Histograms are grouped into 5 collapsible `CategorySection` sections — *Summary*, *Aesthetic & Style*, *Technical Quality*, *Image Properties*, *Captions & Tags* — rendered below always-visible stat cards. A gear icon (`<Settings>`) in the page header opens a fixed right-side `SettingsDrawer` (zIndex 56, dimmer at 55) where per-category and per-item visibility can be toggled. State is persisted to `localStorage` under key `stats-visibility-v1` via the `useStatsVisibility` hook, which merges saved state with `defaultVisibility()` on load so newly added items default to visible. The `show(cat, item)` helper in `StatsPage` combines category + item visibility into a single boolean used in all render conditionals. Grid column counts for variable-visibility rows are computed by filtering the boolean results and using `repeat(N, 1fr)`.

It makes seven queries:

| Query key | Source | Contents |
|---|---|---|
| `["subfolders", datasetId]` | `GET /datasets/{id}/subfolders` | Subfolder list for the dropdown |
| `["dataset-stats", datasetId, activeSubfolder]` | `GET /datasets/{id}/stats?subfolder=` | All distributions (see schema below) |
| `["tag-stats", datasetId, activeSubfolder]` | `GET /captions/dataset/{id}/tag-stats?subfolder=` | Top 500 tags with counts (computed on the fly by splitting `caption_text` on commas at query time — there is no longer a `tags` table; `TagStatItem` carries only `tag` + `count`, no category) |
| `["tag-cooccurrence", datasetId, activeSubfolder]` | `GET /datasets/{id}/tag-cooccurrence?limit=15&subfolder=` | Top-15 tag co-occurrence matrix |
| `["score-values", datasetId, activeSubfolder]` | `GET /datasets/{id}/score-values?subfolder=` | Raw float arrays for all 9 score fields + `megapixels`, `file_size_mb`, `caption_words`, `caption_tokens` — used for client-side histogram rebucketing |
| `["detection-stats", datasetId, activeSubfolder]` | `GET /detection/stats/{dataset_id}?subfolder=` | Aggregates for the "Detections & Masks" section; see `docs/dev/detection.md` |
| `["settings", "thresholds"]` | `GET /api/v1/settings/thresholds` | Live flag threshold values for the quality flag hint text; `staleTime: 60_000` |

**Live polling during active jobs**: `StatsPage` reads `useJobStore` to detect whether a `caption`, `caption_pipeline`, `quality_score`, or `detection` job is currently running for the active dataset. When one is, five of the queries above (`dataset-stats`, `tag-stats`, `tag-cooccurrence`, `score-values`, `detection-stats` — not `subfolders` or `settings`/`thresholds`) receive `refetchInterval: 5_000` so they poll every 5 seconds and distributions update in real-time. The interval stops automatically when the job completes (Zustand selector returns `false`); `TopBar`'s completion handler fires a final invalidation as a safety net. Polling only runs while the browser tab is focused (`refetchIntervalInBackground` defaults to `false`). The `LIVE_STATS_JOB_TYPES` module-level constant in `StatsPage.tsx` lists the watched job types.

**Query error states**: each query surfaces its own failure rather than rendering silently — the main `dataset-stats` query shows a full-page error block with a `refetch()` Retry button (before the `!stats → "No data"` branch), while `tag-stats`, `tag-cooccurrence`, `score-values`, and `detection-stats` each render a one-line `var(--bad)` notice inside their consuming panel (a `svError` also disables **Export Stats CSV**).

All five subfolder-aware stat endpoints accept `subfolder: str | None = Query(None)`. `activeSubfolder` is persisted per-dataset via `STATS_FILTERS_PREFIX` + `loadPersisted` / `useDebouncedPersist` so the chosen subfolder survives navigation. On dataset change (without remount — pane mode) a `prevDatasetId` ref guard effect reloads the per-dataset blob and updates `activeSubfolder`. `BucketPanel` receives `subfolder` as a prop and passes it to `GET /images/`.

## Backend aggregation

**Server-side stats aggregation** (`dataset_service.get_dataset_stats` / `get_score_values`): the async handler does only DB work (row fetch + coverage/flag/embedding count queries), then hands the pure-Python bucketing/aggregation to a thread executor (`run_in_executor`) via the module-level `_aggregate_dataset_stats` helper and a local `_collect` closure respectively, so a large dataset's per-row loop never blocks the event loop while Stats loads. Caption **token** counts come straight from the persisted `Image.caption_token_count` column (`caption_token_distribution`, `score-values.caption_tokens`) — no `tiktoken` call in the request path; **word** counts are still computed from `caption_text` (cheap `split()`). Response keys are unchanged (`StatsPage.tsx` depends on them).

**Validator-keyed stats cache** (`dataset_service.py`, `_stats_cache` + `_stats_cache_get`/`_stats_cache_put`/`_image_validator`): both functions above pull *every* image row in scope into Python, and StatsPage live-polls them, so an idle Stats tab re-read the whole table every few seconds. Each call now first runs a cheap validator query — `SELECT COUNT(id), MAX(updated_at)` under the same dataset/subfolder filter, plus `Dataset.updated_at` for `get_dataset_stats` (whose `license_breakdown` coalesces over the dataset default) — and serves the previous payload when the validator is unchanged. Keys are `(dataset_id, subfolder, "stats" | "scores")`; the cache is a module-level dict capped at `_STATS_CACHE_MAX` (64) entries, dropping oldest-inserted. Staleness is bounded to writes that leave `(count, max updated_at)` identical, which no ORM or Core write can do — `Image.updated_at` carries an `onupdate`. Two consequences for callers: the returned dict **is** the cached object, so never mutate a stats/score-values payload in place; and a new column that affects the payload but lives on `Dataset` (not `Image`) needs `Dataset.updated_at` to be bumped when it changes, or the cache will not notice. Covered by `backend/tests/test_stats_cache_http.py`. SQL-side bucketing was considered and rejected — it would duplicate the ~60 bucket-edge definitions that already exist as `DEFAULT_EDGES` in `StatsPage.tsx`, and would not help `get_score_values` at all.

**`DatasetStats` subfolder invariant**: When subfolder is not None, all out-of-row-scan queries in `get_dataset_stats()` must include `.where(Image.subfolder == subfolder)`: (a) embedding count, (b) score coverage (`func.count` per score column), (c) quality flag counts (`json_extract` + `SUM(CASE …)` per flag key). `total_size_mb` derives from the filtered `file_sizes_mb` list; `ds.total_size_bytes` only used for the all-images case.

**`DatasetStats` schema** (`backend/schemas/dataset.py`; all computed in one row-scan in `dataset_service.get_dataset_stats()`):

| Field | Description |
|---|---|
| `blur_distribution` | 6-bucket Laplacian variance |
| `noise_distribution` | 6-bucket smooth-region std dev |
| `uniformity_distribution` | 5-bucket grayscale std dev |
| `watermark_distribution` | 10 equal bins, 0–1 |
| `color_distribution` / `saturation_distribution` | Hasler-Süsstrunk buckets |
| `luminance_distribution` | 5-bucket mean grayscale, edges `0.15, 0.3, 0.5, 0.7`. Panel title **Brightness**, in the *Technical Quality* category (a fifth panel in a fixed `repeat(2, 1fr)` grid — it wraps). The edges must stay numerically identical to `DEFAULT_EDGES.luminance` in `StatsPage.tsx`, or the histogram jumps the first time a user edits them |
| `megapixel_distribution` | 7-bucket width×height/1M |
| `file_size_distribution` | 6-bucket MB ranges |
| `file_size_summary` | `{min_mb, median_mb, p95_mb, max_mb}` |
| `aspect_ratio_fine` | 8 common AR buckets |
| `caption_length_distribution` | 6-bucket word count |
| `caption_token_distribution` | 6-bucket GPT-2 BPE token count (edges: 1, 20, 40, 60, 77) read from the persisted `Image.caption_token_count` column (no `tiktoken` in the request path — see the token-count note above); the 77+ bucket flags CLIP-truncated captions |
| `style_similarity_distribution` | 10 equal bins, 0–1 |
| `quality_flag_counts` | `{blurry, noisy, uniform, watermarked, duplicate, nsfw, ai_artifacts}` |
| `score_coverage` | Per-score type computed count. `score_coverage["technical"]` counts **`blur_score` only**, so it over-reports for every technical column added since: a dataset last scored before quality-v2 (color/saturation) or before the video arc (luminance) reports 100% technical next to an empty histogram for that column. `HistPanel`'s `footer` prop carries the re-score hint that explains it, passed by `rescoreHint(entries)` in `StatsPage` on the six panels the technical scorer writes (blur, noise, uniformity, brightness, color richness, saturation) and deliberately **not** on watermark, aesthetic or style similarity, whose own coverage counts make an empty histogram honest |
| `license_breakdown` | `{effective license id: count}`. The one field **not** from the row scan: it is aggregated in SQL as `COALESCE(NULLIF(images.license, ''), <dataset default>, '')` with the dataset's default inlined as a literal (the scope is a single dataset, so no join is needed), grouped and scoped by the same `_base_where` as everything else — including the subfolder invariant. `""` is a real key meaning "no license recorded". **Bounded**: only the top `LICENSE_BREAKDOWN_LIMIT` (20) buckets come back by id; the rest are summed under the synthetic key `LICENSE_BREAKDOWN_OTHER_KEY` (`"__other_licenses__"`) so the counts still total the dataset. `other:<free text>` is scraper-sourced and unbounded, so an uncapped response is one bucket per image. That key is **not** a license id — any per-license listing must exclude it and report it separately |

See `docs/dev/scoring.md` for the style-similarity scoring flow and embedding types that populate `style_similarity_distribution`.

Default bucket edges are defined as `DEFAULT_EDGES` in `StatsPage.tsx`. Edges on the backend (`dataset_service.py`) are used only for pre-computing the initial distributions returned by `/stats`; when the user customises edges, `rebucketValues()` runs entirely client-side against the raw `score-values` arrays — no backend call needed.

## CSV export

**CSV export**: Two buttons in the page header export data without any backend call — all data is already loaded in the component.

- **Export Stats CSV** (`downloadCsv`) — disabled while the `["score-values"]` query is loading (`svLoading`). Produces a two-column key-value CSV with labeled section headers (`## SECTION NAME,`). Sections: SUMMARY (dataset_id, dataset_name, image_count, captioned_count, caption_coverage_pct, total_size_mb, avg_width, avg_height), FILE SIZE SUMMARY (min_mb, median_mb, p95_mb, max_mb), QUALITY FLAGS, SCORE COVERAGE, MEAN SCORES (computed from `sv` arrays), and one section per histogram distribution (aesthetic, blur, noise, uniformity, watermark, color, saturation, brightness, style similarity, megapixels, file size, aspect ratio, format, caption word count, caption tokens). Aspect ratio falls back to `aspect_ratio_distribution` (coarse) when `aspect_ratio_fine` is empty — matches the chart's own fallback. Distribution key names are sanitized with `.replace(/[^a-z0-9]/gi, "_").replace(/^_+|_+$/g, "").toLowerCase()` (the final strip removes trailing underscores produced by labels ending in `+`, e.g. `"21:9+"` → `aspect_ratio_21_9` not `aspect_ratio_21_9_`). `dataset_name` is passed through `escapeCsv()` since it is a user-controlled string. Filename: `dataset-{name}-stats.csv` (name sanitized via `safeFilename()`; falls back to `{id}` if name is empty).
- **Export Tags CSV** (`downloadTagsCsv`) — disabled when `tagStats.length === 0`. Produces a tabular CSV with header row `tag,count`; tag values are run through `escapeCsv()` (quotes fields containing `,`, `"`, or newlines). Filename: `dataset-{name}-tags.csv` (same sanitization/fallback as stats export).

`escapeCsv(v)` wraps the value in double-quotes and escapes internal double-quotes (`"` → `""`) when the value contains any of `,`, `"`, `\n`, `\r`. Used for `dataset_name` in `downloadCsv` and for all tag values in `downloadTagsCsv`. `safeFilename(name)` strips characters illegal in filenames (`/\:*?"<>|`) before the name is used in a `triggerDownload` filename — use it whenever a user-supplied string appears in a download filename. `triggerDownload(csv, filename)` is the shared blob-download helper (Blob → `URL.createObjectURL` → synthetic `<a>` click → `URL.revokeObjectURL`).

## Bucket drill-down

**Editable histograms (HistPanel)**: Every score/metric histogram has a pencil icon that opens an inline edge editor. The user types comma-separated boundary values (e.g. `"4, 6"` for aesthetic score), presses Apply or Enter, and the chart immediately rebuckets using the raw value arrays. A "custom" badge appears in the panel title when non-default edges are active; Reset restores the defaults. Aspect ratio and file format histograms are non-editable (no raw values to rebucket). When a customised bar is clicked, `BucketPanel` still opens with the correct `min`/`max` filter derived from the custom edges. `HistPanel` accepts an optional `storageKey?: string` prop; when provided, the active edge string is persisted to that `localStorage` key on change and restored on mount (with a second `useEffect` that re-reads the key when `storageKey` changes to handle dataset switches). Score histograms pass a per-dataset key derived from `STATS_FILTERS_PREFIX` so custom bucketing survives navigation.

**Clickable bars → BucketPanel**: Charts are hand-rolled — `CssHist` renders each bar as a `hist-bar` div (no charting library). Every histogram bar carries a `filter` object in its chart-entry data; the bar's own `onClick={() => e.filter && onBarClick?.(e)}` opens a `BucketPanel` modal. The panel queries `GET /images/` with the filter params and shows up to 200 thumbnails. Quality flag cards are also clickable.

**`GET /images/` filter extensions** (in `backend/routers/images.py`):

| Param | Type | Effect |
|---|---|---|
| `search` | `str` | Case-insensitive LIKE filter across `original_filename` and `caption_text` (OR logic) |
| `score_field` | `str` | Which score column `min_score`/`max_score` apply to (whitelist-validated; defaults to `aesthetic_score`) |
| `score_is_null` | `bool` | Filter images where `score_field IS NULL` (used for "unscored" bucket) |
| `score_filters` | `str` (JSON) | JSON-encoded array of `{field, min?, max?}` objects; each entry adds an AND condition; fields validated against `_ALLOWED_SCORE_FIELDS` whitelist |
| `quality_flag` | `str` | Filter by JSON flag key in `quality_flags` (e.g. `is_blurry`) |
| `file_size_min` / `file_size_max` | `int` | `file_size_bytes` range (bytes) |
| `mp_min` / `mp_max` | `float` | `width × height` megapixel range |
| `ar_min` / `ar_max` | `float` | Aspect ratio `width / height` range |
| `format_filter` | `str` | Exact `Image.format` match (e.g. `PNG`) |
| `detection_label` | `str` | `EXISTS` subquery: only images that have at least one detection with `label ILIKE '%...%'` (substring — gallery search) |
| `detection_label_exact` | `str` | Exact `Detection.label == value`; combined with the score params below into **one** `EXISTS` subquery so all conditions apply to the same detection row (Detections & Masks label bar click-through) |
| `detection_score_min` / `detection_score_max` / `detection_score_null` | `float` / `bool` | Detection-score range (min inclusive, max exclusive) or NULL-score ("unscored") match, folded into the same `EXISTS` subquery as `detection_label_exact` |
| `mask_coverage_min` / `mask_coverage_max` | `float` | Per-image `SUM(Detection.mask_area)` clamped to 1.0 (`case`), min inclusive / max exclusive; also requires ≥1 detection (`EXISTS`) so the population matches the coverage histogram |
| `detection_count_min` / `detection_count_max` | `int` | Correlated `COUNT(Detection.id)` coalesced to 0 (so the "0" bucket = images with no detections works), both bounds inclusive |
| `caption_words_min` / `caption_words_max` | `int` | Word count range — SQL approximation via `length(trim(text)) - length(replace(trim(text), ' ', '')) + 1`; `min` is inclusive, `max` is exclusive |
| `license_filter` | `str` (JSON) | JSON-encoded **array** of effective license ids (never comma-separated — an `other:<free text>` id may contain commas), parsed by `utils.parse_license_filter_param`. Matched against `COALESCE(NULLIF(images.license,''), datasets.license, '')` over a join. Any array containing a blank entry (`[""]`, `["  "]`, or a mixed `["CC-BY-4.0", ""]`) is **rejected with 400** — use `license_missing` for unlicensed images; an empty array (`[]`) is no filter |
| `source_video_id` | `str` | Frame lineage: only images extracted from that video, wherever curation has since filed them. A plain indexed equality on `Image.source_video_id` (no join, no `EXISTS`), applied right after the subfolder block. Truthiness-gated, so `""` is no filter; no allowlist — the value is an opaque uuid and an unknown one correctly returns zero rows. See `docs/dev/gallery.md` § Gallery filters and `docs/dev/video-ui.md` |
| `license_missing` | `bool` | `true` = only images whose effective license is `""`; `false` = only images that have one. This is the Licenses panel's unlicensed click-through |
| `caption_tokens_min` / `caption_tokens_max` | `int` | GPT-2 BPE token count range — **pure SQL** over the persisted `func.coalesce(Image.caption_token_count, 0)` column (`min` inclusive, `max` exclusive), so ordinary `ORDER BY`/`OFFSET`/`LIMIT` paging applies; no `tiktoken` in the request path. See `docs/dev/gallery.md` § Gallery filters |

## Category panels

**Detections & Masks section** (`detections` category in `STATS_CONFIG`): a QA surface for detection/mask data, fed by a dedicated `GET /detection/stats/{dataset_id}?subfolder=` endpoint (in `backend/routers/detection.py`, kept out of `get_dataset_stats` so live-polling refetches just this query). The endpoint returns stat-card totals (`total_detections`, `images_with_detections`, `images_without_detections`, `distinct_labels`, `bbox_only_count`), a `label_distribution` (top 30), `model_distribution`, `score_histogram` (10 bins + an `"unscored"` bucket for NULL/manual scores), `coverage_histogram`, and `detections_per_image`. All aggregates join `Detection.image_id == Image.id` and honor the `DatasetStats` subfolder invariant (exact `Image.subfolder == subfolder`). **Coverage** = per-image `SUM(mask_area)` clamped to 1.0 — an approximation of the exported union mask (overlaps overcount), covering only images with ≥1 detection; it flags <2% / >95% outliers, not exact areas. The frontend query is `["detection-stats", datasetId, activeSubfolder]`; `"detection"` is in `LIVE_STATS_JOB_TYPES` so the section live-updates while a detection job runs. The coverage and detections-per-image histograms ship **fixed-edge** (`HistPanel` with no `rawValues`, so no pencil/rebucket editor); the model-breakdown bars are non-clickable (model is not a `GET /images/` filter). All other bars carry a `filter` and open `BucketPanel` via the params in the `detection_*` / `mask_coverage_*` table rows above. `downloadCsv` emits the detection summary + each distribution as CSV sections.

**Licenses panel** (`licenses` in the `summary` category of `STATS_CONFIG`): renders `DatasetStats.license_breakdown` as a table — License / Commercial use / Images / Share — sorted by count, with labels and commercial-use verdicts from `constants/licenses.ts` (so an `other:<free text>` id shows its free text and an unknown commercial verdict). The `__other_licenses__` key (see the stats table above) is **excluded from the table** and reported as a footnote beneath it ("+ N image(s) across smaller license buckets, not listed individually"), because it is a count, not a license id. A warning badge in the panel header shows the unlicensed count when `license_breakdown[""] > 0`, and the panel then also renders an explainer under it: those images have no license at either level, exports include them by default, and they are listed as unlicensed in `CREDITS.md`. Every row is a click-through into `BucketPanel`: a normal license sends `license_filter=["<id>"]`, the "No license" row sends `license_missing=true` (see the filter table above for why those are two different params). See `docs/dev/provenance.md` for the inheritance model behind the effective license.

**ImageLightbox**: Clicking a thumbnail in `BucketPanel` opens a full-resolution lightbox with prev/next navigation, metadata footer, a "View Details →" link to `/datasets/:datasetId/image/:imageId`, and a two-step **Delete** button. Deleting an image removes it from the panel's TanStack Query cache via `queryClient.setQueryData` (no refetch) and invalidates `dataset-stats`, `detection-stats`, `tag-stats`, `score-values`, and `tag-cooccurrence` queries. A per-thumbnail ×-on-hover delete button with an inline confirm overlay provides the same action from the grid.

