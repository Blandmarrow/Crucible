# Dashboard pages: Datasets, Statistics, Settings, hardware stats, file browser & Booru lookup

This file covers the Datasets page (categories, import, duplicate), the Statistics page (histograms, CSV export, BucketPanel), the Settings page (tabs, thresholds), system hardware stats, the file browser, and the Booru tag lookup page.

### Datasets page

`DatasetsPage` uses `queryKey: ["datasets"]` with `staleTime: 0` so the list is always refetched on mount.

**Preview strip**: `GET /datasets/` (`DatasetOut`) accepts optional `skip` (default 0) and `limit` (default 0 = no limit) query params for pagination. Includes `preview_image_ids: list[str]` — up to 8 image IDs fetched in a single batch query alongside the datasets list. The card renders these as `<img src="/api/v1/images/{id}/thumbnail">` tiles. When a dataset has no images the strip falls back to deterministic colour gradients.

**Sort control**: a `<select>` in the page header lets the user sort the dataset list. Sorting is frontend-only (`useMemo` on the already-loaded list). Options: Newest (default) / Oldest / Recently updated / Name A→Z / Name Z→A / Most images / Fewest images / Largest / Smallest / Most captioned %. Sorting applies within each category section when grouped, not across sections.

**Layout is two independent axes**: *density* (card grid vs compact rows) × *grouping* (flat grid vs category sections). `renderItems(items)` picks `renderGrid` or `renderRows` by the `density` state and is the only call site both grouping paths use — never call `renderGrid` directly from a new code path. `renderRow` and `renderCard` both spread `datasetElementProps(ds)`, which supplies `data-dataset-id`, `draggable`, the `"dataset-id"` `onDragStart`, and the `usePaneNavigate().go()` `onClick`; this is what keeps drag, file-drop upload, and split-pane navigation identical in both densities. The six per-dataset action buttons live once in `renderDatasetActions(ds, variant)`.

**Persisted page UI** (`DATASETS_UI_KEY`, `constants/storage.ts`): `{ collapsed: string[], density, selectedCategory }`, read via `loadPersisted` into lazy `useState` and written by a 350ms-debounced effect. Every field is **coerced on read** — `loadPersisted` shallow-merges arbitrary parsed JSON. `collapsed` is deliberately not pruned to known categories (that would discard state for categories hidden by search), and a `selectedCategory` that no longer exists is handled by *derivation* (`effectiveSelected`), never by clearing it in an effect — on first paint `datasets` is `[]`, so eager cleanup would wipe a valid selection mid-load. `renameCategoryMutation`/`deleteCategoryMutation` remap both `selectedCategory` and the `collapsed` entry, or the user silently loses collapse state on rename. `DECLARED_CATEGORIES_KEY` stays on its own raw `localStorage` read/write — it stores a bare `string[]`, which `loadPersisted`'s object merge cannot represent.

**Category groups**: `Dataset` has a nullable `category: str` column (default `""`). When at least one dataset has a non-empty category, the page switches from a flat grid to a **folder-sectioned layout**:
- Each category renders a collapsible section: folder icon + name + count badge + chevron. Collapse state is persisted (see above); a "Collapse all"/"Expand all" toolbar button sets it wholesale.
- Section order comes from `sectionKeys`, which puts **"(Uncategorized)" first** — a new dataset has `category = ""` and must not be pushed below every named section. `sectionItems` buckets datasets by section key in one pass; do not reintroduce a per-section `.filter()` (that was O(categories × datasets) per render). Neither is wrapped in `useMemo`: building the Map mutates it, which React Compiler cannot preserve as manual memoization, and a `useMemo` there makes it skip optimizing the whole component.
- When all datasets have `category = ""` the flat grid is restored (rail hidden, density still applies).
- **New Category button**: a "New category" button in the toolbar creates a frontend-only empty category. Because `category` is a string on `Dataset` with no independent backend record, empty categories are persisted in `localStorage` under `DECLARED_CATEGORIES_KEY` (`string[]`). The `emptyCategories` state is merged into `existingCategories` (used by `CategoryPicker`) and into the grouped layout. Both `renameCategoryMutation.onSuccess` and `deleteCategoryMutation.onSuccess` also update `emptyCategories`. Empty-category sections are **suppressed when the search box is non-empty** (they would otherwise appear as phantom sections unrelated to the search term).
- **Category rail**: when `hasAnyCategory && sectionKeys.length >= 2`, a 180px `<aside>` (modelled on the GalleryPage subfolder sidebar) lists "All" + every section key with counts. It is a **filter, not a replacement** — selecting a category still renders through `renderCategorySection`, so collapse, inline rename, delete, and the drop target stay in one place. A filtered section is passed `{ forceExpanded: true }` (and hides its chevron), otherwise a persisted-collapsed category renders as a header over nothing. While `search` is non-empty the rail dims and `effectiveSelected` collapses to `null`, so a match can never hide behind an unselected category.
- **Drag-and-drop assignment**: when `hasAnyCategory` is true all dataset cards/rows become draggable. `onDragStart` uses `dataTransfer.setData("dataset-id", id)` to distinguish from file-upload drags (which use the `"Files"` type). Both `renderCategorySection` and each named rail row attach React `onDragOver`/`onDragLeave`/`onDrop` gated on `types.includes("dataset-id")`; `onDragOver` guards with `if (dropTargetCategory !== cat)` to avoid redundant re-renders. Dropping on a named section or rail row calls `moveCategoryMutation` (PATCH `dataset.category`); dropping on "(Uncategorized)" sets `category: ""`. Empty sections show a dashed placeholder that becomes the visual drop target. **Never put `data-dataset-id` on a rail row** — it is the file-upload target selector, so a category row carrying it would make `handleCardDrop` POST images to a category name treated as a dataset id. Every thumbnail `<img>` needs `draggable={false}`: a default-draggable image inside a draggable ancestor starts its own drag carrying no `"dataset-id"`, silently breaking category assignment.
- **New-dataset highlight**: `createMutation.onSuccess` clears search, drops the rail to "All" unless the new dataset's section is already selected, expands that section, and sets `highlightId`. An effect keyed on `[highlightId, datasets]` finds the node by `data-dataset-id` once the refetch renders it, calls `scrollIntoView`, and starts a 2s clear; a second 6s hard-expiry effect covers an id that never renders. The `ds-flash` keyframe (index.css) animates `box-shadow`, not `border`/`outline`, so nothing reflows mid-animation.
- **Rename category**: hover the section header to reveal a pencil button. Clicking enters an inline rename form (input + Enter/Escape). On confirm, `renameCategoryMutation` batch-PATCHes all affected datasets via `Promise.all`. Invalidates `["datasets"]` on both success and error (to recover from partial failures).
- **Delete category**: hover to reveal a trash button. `ConfirmDialog` → `deleteCategoryMutation` batch-PATCHes `category: ""` on all affected datasets. Invalidates `["datasets"]` on both success and error.
- The "Uncategorized" section has no rename/delete buttons.

**`CategoryPicker` component** (`DatasetsPage.tsx`, module-level): used in both Create and Edit modals. Renders a `<select>` showing existing categories + "(None)" + "New category…"; choosing "New category…" reveals a text input below. Because the component is always inside a conditionally-rendered modal it remounts on each open — `useState(!inExisting)` init is sufficient; no sync `useEffect` is needed or present.

**Import job tracking**: after starting an import (`POST /datasets/{id}/import`) `DatasetsPage` stores the returned `job_id` and watches it in `jobStore` via `useEffect`. On completion, `["datasets"]` is invalidated plus — when `dataset_id` is present on the job progress — `["images", dataset_id]` and all four stats queries, so image counts and distributions update after the import finishes. `TopBar`'s global import handler does the same as a safety net for navigation away before completion.

**Import modal** is the shared `ImportFolderModal` component (`frontend/src/components/common/ImportFolderModal.tsx`), used by both `DatasetsPage` (card + header "Import folder" buttons) and the `GalleryPage` toolbar ("Import folder" button). It takes `datasets: Dataset[]` (candidate targets), an optional `initialDatasetId`, `onStarted(jobId)`, and `onClose`. When more than one dataset is passed it renders a **target-dataset selector** (dropdown); with a single candidate it shows the fixed target name. The `DatasetsPage` header button passes the full list with no preselection (previously it hardcoded `datasets[0]`, giving no way to choose the target — fixed); card buttons and the gallery preselect via `initialDatasetId`. The folder path has a **"Browse…"** button that opens the shared in-app `DirPickerModal` (see below) instead of a native OS dialog; the manual path text input always remains available as an alternative. The form's options — `subfolder` (target logical subfolder, empty = root), `preserve_structure: bool` (recursively walk the source and map each subdirectory to a logical subfolder; when false everything lands in `subfolder`), and `import_captions: bool` (default true — read `.txt` sidecars next to source images) — are passed in the `POST /datasets/{id}/import` body as `DatasetImportWithOptions`. Whichever page owns the modal tracks the returned `job_id` in `jobStore` and invalidates its caches on completion.

**`DirPickerModal`** (`frontend/src/components/common/DirPickerModal.tsx`) is the shared in-app folder browser behind every "Browse…" button (import modal, caption import, and the Export page output folder). It browses the **server's** filesystem via `GET /filesystem/roots` (drive roots / `datasets_dir`) and `GET /filesystem/list` (subdirectories only) — the same endpoints as `FileBrowserPage` (§ File browser) — so it works identically on every OS with no native-dialog / window-focus issues. Props: `initialPath`, optional `title` and `confirmLabel` (default `"Select output folder"` / `"Select folder"`), `onConfirm(path)`, `onCancel`. It offers breadcrumb navigation, a drive-root switcher, "New folder" (`POST /filesystem/mkdir`), and a free-text path field, and returns an absolute path. **Nesting caveat:** it renders its own full-screen fixed overlay, so when it is opened from inside another modal, render it as a **sibling of** (not a child of) any backdrop that has an `onClick`-to-close handler — otherwise clicks inside the picker bubble up and close the parent. `ImportFolderModal` does this by wrapping its return in a fragment and placing `DirPickerModal` outside the `onClose` backdrop.

**Rescan & caption import**: each dataset card also has a "Rescan folder from disk" button (`POST /datasets/{id}/rescan`) and an "Import captions" button → modal with a folder-path input (with a "Browse…" button using the same `DirPickerModal`) (`POST /datasets/{id}/import-captions`). The gallery toolbar carries its own "Import folder" + "Rescan" buttons so both are reachable while viewing a dataset's images (see `docs/dev/gallery-and-images.md`). Both run as background jobs tracked in `jobStore`; on completion `DatasetsPage` invalidates the dataset caches (via a shared `invalidateDatasetCaches` helper) and fetches the job's `result_data` through `jobsApi.get(jobId)` to show a summary toast (rescan: `added` / `captions_updated` / `missing`; caption import: `matched` / `unmatched`). See `docs/dev/gallery-and-images.md` § Importing captions & folder rescan for the backend services and the drag-`.txt`-onto-image feature.

**Card navigation**: Dataset card clicks use `usePaneNavigate().go(url, view)` (not raw `useNavigate`) so that clicking a dataset inside a split pane updates that pane's view rather than the URL. Do not revert to `useNavigate` here. See `docs/dev/frontend-core.md` § Split view pane manager for the `usePaneNavigate()` hook.

**Drag-and-drop upload**: `GalleryPage` supports dropping onto the grid (`onDragEnter`/`onDragLeave`/`onDrop` on the scroll container). `DatasetsPage` supports dropping image files directly onto a dataset card (native `dragover`/`drop` via `useEffect` on `pageRef`, `data-dataset-id` attrs, `dragOverId` state); on success `handleCardDrop` invalidates `["datasets"]`, `["images", datasetId]`, and `["subfolders", datasetId]` so the gallery reflects the new images immediately. Note: **dataset-to-category** drag-and-drop (dragging a card into a category section) works correctly via React synthetic events on `renderCategorySection` — it uses `dataTransfer.setData("dataset-id", ...)` which does not interfere with file-upload detection (file drags expose `"Files"` in `dataTransfer.types`, not `"dataset-id"`).

**Dataset folder naming**: `create_dataset()` in `dataset_service.py` derives the folder name from the dataset name via `_name_to_slug()` (lowercase, spaces → underscores, special chars stripped, max 80 chars) rather than using the UUID. The UUID is still the DB primary key. If the slug folder already exists (name collision edge case), a `{slug}_{uuid8}` suffix is appended. Example: dataset named `"My Portraits"` creates `data/datasets/my_portraits/`.

**Dataset edit**: the pencil (Edit) button on each dataset card opens a modal for editing the name, description, and category. `PATCH /datasets/{id}` accepts `{ name?, description?, category? }`. When the name changes, `rename_dataset()` renames the folder on disk, bulk-updates all `Image.file_path`/`thumbnail_path` records via string prefix replacement, and updates `Dataset.folder_path`/`name` — all in one transaction. Returns 400 on name conflict. The Save button is enabled when any field differs from the current values.

**Dataset duplicate**: the copy icon button on each card opens a Duplicate modal. `POST /datasets/{id}/duplicate` is a background job that returns `{job_id}` immediately; `DatasetsPage` watches the job in `jobStore` and invalidates `["datasets"]` on completion. The new dataset inherits the source's description, category, and declared subfolders; blob columns (`clip_embedding`, `dino_embedding`, `dino_layer_embeddings`) are not copied. When versioning is enabled and the source dataset has branches, the modal shows a branch + version dropdown; choosing a specific snapshot copies from the object store (using the snapshot's `file_hash` if present, otherwise the current file path). `datasetsApi.duplicate(id, newName, sourceVersionId?)` in `frontend/src/api/datasets.ts`. Backend: `duplicate_dataset()` in `dataset_service.py`; `DatasetDuplicateRequest` schema (`new_name`, `source_version_id?`) in `backend/schemas/dataset.py`.

### Statistics page

`frontend/src/pages/StatsPage.tsx` renders the dataset analytics dashboard. A compact subfolder dropdown in the page header (shown only when subfolders exist) scopes all four queries to a specific subfolder.

**Panel organization**: Histograms are grouped into 5 collapsible `CategorySection` sections — *Summary*, *Aesthetic & Style*, *Technical Quality*, *Image Properties*, *Captions & Tags* — rendered below always-visible stat cards. A gear icon (`<Settings>`) in the page header opens a fixed right-side `SettingsDrawer` (zIndex 56, dimmer at 55) where per-category and per-item visibility can be toggled. State is persisted to `localStorage` under key `stats-visibility-v1` via the `useStatsVisibility` hook, which merges saved state with `defaultVisibility()` on load so newly added items default to visible. The `show(cat, item)` helper in `StatsPage` combines category + item visibility into a single boolean used in all render conditionals. Grid column counts for variable-visibility rows are computed by filtering the boolean results and using `repeat(N, 1fr)`.

It makes six queries:

| Query key | Source | Contents |
|---|---|---|
| `["subfolders", datasetId]` | `GET /datasets/{id}/subfolders` | Subfolder list for the dropdown |
| `["dataset-stats", datasetId, activeSubfolder]` | `GET /datasets/{id}/stats?subfolder=` | All distributions (see schema below) |
| `["tag-stats", datasetId, activeSubfolder]` | `GET /captions/dataset/{id}/tag-stats?subfolder=` | Top 500 tags with counts (computed on the fly by splitting `caption_text` on commas at query time — there is no longer a `tags` table; `TagStatItem` carries only `tag` + `count`, no category) |
| `["tag-cooccurrence", datasetId, activeSubfolder]` | `GET /datasets/{id}/tag-cooccurrence?limit=15&subfolder=` | Top-15 tag co-occurrence matrix |
| `["score-values", datasetId, activeSubfolder]` | `GET /datasets/{id}/score-values?subfolder=` | Raw float arrays for all 8 score fields + `megapixels`, `file_size_mb`, `caption_words`, `caption_tokens` — used for client-side histogram rebucketing |

**Live polling during active jobs**: `StatsPage` reads `useJobStore` to detect whether a `caption`, `caption_pipeline`, or `quality_score` job is currently running for the active dataset. When one is, all four stats queries above receive `refetchInterval: 5_000` so they poll every 5 seconds and distributions update in real-time. The interval stops automatically when the job completes (Zustand selector returns `false`); `TopBar`'s completion handler fires a final invalidation as a safety net. Polling only runs while the browser tab is focused (`refetchIntervalInBackground` defaults to `false`). The `LIVE_STATS_JOB_TYPES` module-level constant in `StatsPage.tsx` lists the watched job types.
| `["settings", "thresholds"]` | `GET /api/v1/settings/thresholds` | Live flag threshold values for the quality flag hint text; `staleTime: 60_000` |

All four stat endpoints accept `subfolder: str | None = Query(None)`. `activeSubfolder` is persisted per-dataset via `STATS_FILTERS_PREFIX` + `loadPersisted`/`savePersisted` (350ms-debounced) so the chosen subfolder survives navigation. On dataset change (without remount — pane mode) a `prevDatasetId` ref guard effect reloads the per-dataset blob and updates `activeSubfolder`. `BucketPanel` receives `subfolder` as a prop and passes it to `GET /images/`.

**Server-side stats aggregation** (`dataset_service.get_dataset_stats` / `get_score_values`): the async handler does only DB work (row fetch + coverage/flag/embedding count queries), then hands the pure-Python bucketing/aggregation to a thread executor (`run_in_executor`) via the module-level `_aggregate_dataset_stats` helper and a local `_collect` closure respectively, so a large dataset's per-row loop never blocks the event loop while Stats loads. Caption **token** counts come straight from the persisted `Image.caption_token_count` column (`caption_token_distribution`, `score-values.caption_tokens`) — no `tiktoken` call in the request path; **word** counts are still computed from `caption_text` (cheap `split()`). Response keys are unchanged (`StatsPage.tsx` depends on them).

**`DatasetStats` subfolder invariant**: When subfolder is not None, all out-of-row-scan queries in `get_dataset_stats()` must include `.where(Image.subfolder == subfolder)`: (a) embedding count, (b) score coverage (`func.count` per score column), (c) quality flag counts (`json_extract` + `SUM(CASE …)` per flag key). `total_size_mb` derives from the filtered `file_sizes_mb` list; `ds.total_size_bytes` only used for the all-images case.

**`DatasetStats` schema** (`backend/schemas/dataset.py`; all computed in one row-scan in `dataset_service.get_dataset_stats()`):

| Field | Description |
|---|---|
| `blur_distribution` | 6-bucket Laplacian variance |
| `noise_distribution` | 6-bucket smooth-region std dev |
| `uniformity_distribution` | 5-bucket grayscale std dev |
| `watermark_distribution` | 10 equal bins, 0–1 |
| `color_distribution` / `saturation_distribution` | Hasler-Süsstrunk buckets |
| `megapixel_distribution` | 7-bucket width×height/1M |
| `file_size_distribution` | 6-bucket MB ranges |
| `file_size_summary` | `{min_mb, median_mb, p95_mb, max_mb}` |
| `aspect_ratio_fine` | 8 common AR buckets |
| `caption_length_distribution` | 6-bucket word count |
| `caption_token_distribution` | 6-bucket GPT-2 BPE token count (edges: 1, 20, 40, 60, 77) read from the persisted `Image.caption_token_count` column (no `tiktoken` in the request path — see the token-count note above); the 77+ bucket flags CLIP-truncated captions |
| `style_similarity_distribution` | 10 equal bins, 0–1 |
| `quality_flag_counts` | `{blurry, noisy, uniform, watermarked, duplicate, nsfw, ai_artifacts}` |
| `score_coverage` | Per-score type computed count |

See `docs/dev/ml-models.md` § Object detection for the style-similarity scoring flow and embedding types that populate `style_similarity_distribution`.

Default bucket edges are defined as `DEFAULT_EDGES` in `StatsPage.tsx`. Edges on the backend (`dataset_service.py`) are used only for pre-computing the initial distributions returned by `/stats`; when the user customises edges, `rebucketValues()` runs entirely client-side against the raw `score-values` arrays — no backend call needed.

**CSV export**: Two buttons in the page header export data without any backend call — all data is already loaded in the component.

- **Export Stats CSV** (`downloadCsv`) — disabled while the `["score-values"]` query is loading (`svLoading`). Produces a two-column key-value CSV with labeled section headers (`## SECTION NAME,`). Sections: SUMMARY (dataset_id, dataset_name, image_count, captioned_count, caption_coverage_pct, total_size_mb, avg_width, avg_height), FILE SIZE SUMMARY (min_mb, median_mb, p95_mb, max_mb), QUALITY FLAGS, SCORE COVERAGE, MEAN SCORES (computed from `sv` arrays), and one section per histogram distribution (aesthetic, blur, noise, uniformity, watermark, color, saturation, style similarity, megapixels, file size, aspect ratio, format, caption word count, caption tokens). Aspect ratio falls back to `aspect_ratio_distribution` (coarse) when `aspect_ratio_fine` is empty — matches the chart's own fallback. Distribution key names are sanitized with `.replace(/[^a-z0-9]/gi, "_").replace(/^_+|_+$/g, "").toLowerCase()` (the final strip removes trailing underscores produced by labels ending in `+`, e.g. `"21:9+"` → `aspect_ratio_21_9` not `aspect_ratio_21_9_`). `dataset_name` is passed through `escapeCsv()` since it is a user-controlled string. Filename: `dataset-{name}-stats.csv` (name sanitized via `safeFilename()`; falls back to `{id}` if name is empty).
- **Export Tags CSV** (`downloadTagsCsv`) — disabled when `tagStats.length === 0`. Produces a tabular CSV with header row `tag,count`; tag values are run through `escapeCsv()` (quotes fields containing `,`, `"`, or newlines). Filename: `dataset-{name}-tags.csv` (same sanitization/fallback as stats export).

`escapeCsv(v)` wraps the value in double-quotes and escapes internal double-quotes (`"` → `""`) when the value contains any of `,`, `"`, `\n`, `\r`. Used for `dataset_name` in `downloadCsv` and for all tag values in `downloadTagsCsv`. `safeFilename(name)` strips characters illegal in filenames (`/\:*?"<>|`) before the name is used in a `triggerDownload` filename — use it whenever a user-supplied string appears in a download filename. `triggerDownload(csv, filename)` is the shared blob-download helper (Blob → `URL.createObjectURL` → synthetic `<a>` click → `URL.revokeObjectURL`).

**Editable histograms (HistPanel)**: Every score/metric histogram has a pencil icon that opens an inline edge editor. The user types comma-separated boundary values (e.g. `"4, 6"` for aesthetic score), presses Apply or Enter, and the chart immediately rebuckets using the raw value arrays. A "custom" badge appears in the panel title when non-default edges are active; Reset restores the defaults. Aspect ratio and file format histograms are non-editable (no raw values to rebucket). When a customised bar is clicked, `BucketPanel` still opens with the correct `min`/`max` filter derived from the custom edges. `HistPanel` accepts an optional `storageKey?: string` prop; when provided, the active edge string is persisted to that `localStorage` key on change and restored on mount (with a second `useEffect` that re-reads the key when `storageKey` changes to handle dataset switches). Score histograms pass a per-dataset key derived from `STATS_FILTERS_PREFIX` so custom bucketing survives navigation.

**Clickable bars → BucketPanel**: Every histogram bar carries a `filter` object in its chart-entry data. Clicking fires a `Bar.onClick` handler (recharts v3 pattern — use `Bar.onClick`, not `BarChart.onClick`) which opens a `BucketPanel` modal. The panel queries `GET /images/` with the filter params and shows up to 200 thumbnails. Quality flag cards are also clickable.

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
| `caption_tokens_min` / `caption_tokens_max` | `int` | GPT-2 BPE token count range — **pure SQL** over the persisted `func.coalesce(Image.caption_token_count, 0)` column (`min` inclusive, `max` exclusive), so ordinary `ORDER BY`/`OFFSET`/`LIMIT` paging applies; no `tiktoken` in the request path. See `docs/dev/gallery-and-images.md` § Gallery filters |

**Detections & Masks section** (`detections` category in `STATS_CONFIG`): a QA surface for detection/mask data, fed by a dedicated `GET /detection/stats/{dataset_id}?subfolder=` endpoint (in `backend/routers/detection.py`, kept out of `get_dataset_stats` so live-polling refetches just this query). The endpoint returns stat-card totals (`total_detections`, `images_with_detections`, `images_without_detections`, `distinct_labels`, `bbox_only_count`), a `label_distribution` (top 30), `model_distribution`, `score_histogram` (10 bins + an `"unscored"` bucket for NULL/manual scores), `coverage_histogram`, and `detections_per_image`. All aggregates join `Detection.image_id == Image.id` and honor the `DatasetStats` subfolder invariant (exact `Image.subfolder == subfolder`). **Coverage** = per-image `SUM(mask_area)` clamped to 1.0 — an approximation of the exported union mask (overlaps overcount), covering only images with ≥1 detection; it flags <2% / >95% outliers, not exact areas. The frontend query is `["detection-stats", datasetId, activeSubfolder]`; `"detection"` is in `LIVE_STATS_JOB_TYPES` so the section live-updates while a detection job runs. The coverage and detections-per-image histograms ship **fixed-edge** (`HistPanel` with no `rawValues`, so no pencil/rebucket editor); the model-breakdown bars are non-clickable (model is not a `GET /images/` filter). All other bars carry a `filter` and open `BucketPanel` via the params in the `detection_*` / `mask_coverage_*` table rows above. `downloadCsv` emits the detection summary + each distribution as CSV sections.

**ImageLightbox**: Clicking a thumbnail in `BucketPanel` opens a full-resolution lightbox with prev/next navigation, metadata footer, a "View Details →" link to `/datasets/:datasetId/image/:imageId`, and a two-step **Delete** button. Deleting an image removes it from the panel's TanStack Query cache via `queryClient.setQueryData` (no refetch) and invalidates `dataset-stats`, `detection-stats`, `tag-stats`, `score-values`, and `tag-cooccurrence` queries. A per-thumbnail ×-on-hover delete button with an inline confirm overlay provides the same action from the grid.

### Settings page

`frontend/src/pages/SettingsPage.tsx`, route `/settings`, sidebar nav item "Settings". Exposes all seven scoring thresholds — the six quality-flag thresholds plus the Grounding DINO box-confidence threshold — as editable number inputs.

**Backend**: `backend/routers/settings.py`, prefix `/settings`. Two endpoints:

| Endpoint | Behaviour |
|---|---|
| `GET /thresholds` | Returns current thresholds from the `threshold_settings` singleton row (id=1); if the row doesn't exist yet, returns in-memory defaults from `DEFAULTS` in `threshold_service.py` without writing anything |
| `PATCH /thresholds` | Creates the row on first save (upsert on id=1), updates only the fields present in the body, commits |

**Model**: `backend/models/threshold_settings.py` — `ThresholdSettings` table with a single row (`id=1`). Holds the quality-threshold `Float` columns (with `server_default` matching the constants in `technical_scorer.py`), the `versioning_mode` string, and the `auto_rescan_on_open` boolean (`server_default="0"`). It is the catch-all single-row table for app-wide server-side settings — add new global toggles here and to `ThresholdsOut`/`ThresholdsUpdate` in `routers/settings.py`. Defaults are canonically defined in `backend/services/threshold_service.py::DEFAULTS`.

**Frontend**: `useQuery({ queryKey: ["settings", "thresholds"], staleTime: 60_000 })` — shared key with `StatsPage` so both components see the same cached value. Save button is enabled only when at least one field differs from the loaded values (`isChanged`). Save sends only the changed fields via `PATCH`. "Reset to defaults" restores the local form state to the `DEFAULTS` constant without an API call.

The Settings page uses a **tab-based layout** with seven tabs. All localStorage-backed preferences take effect immediately (no Save button); the quality thresholds, versioning mode, and both ComfyUI fields require an explicit Save. The **ComfyUI tab** holds `comfyui_url` (with a *Test connection* button → `comfyApi.ping`) and `comfy_workflow_dir` (with a `DirPickerModal` Browse), both server-side `ThresholdSettings` columns — see `docs/dev/comfyui.md`.

**Gallery tab** — four immediate-save preferences:
- Images per page (`25 | 50 | 100 | 200`). Stored under `GALLERY_PAGE_SIZE_KEY`. Read by `GalleryPage` (gallery list limit) and `ImageDetailPage` (end-of-page detection + prefetch limit for cross-page arrow-key navigation). Parse and default via `getGalleryPageSize()`.
- Subfolder rename on move (`on | off`). Stored under `SUBFOLDER_RENAME_KEY`. Read by `SelectionToolbar`'s `moveSubfolderMutation` at mutation time; passed as `rename_on_move` to `POST /images/batch/move-subfolder`.
- **Gallery defaults** section: default sort (`GALLERY_DEFAULT_SORT_KEY`, index into `SORT_OPTIONS`), default caption filter (`GALLERY_DEFAULT_CAPTION_KEY`, `"all" | "captioned" | "uncaptioned"`), default quality filter (`GALLERY_DEFAULT_QUALITY_KEY`, flag key or `""`). Applied the first time you open a dataset's gallery, before any filter choices have been remembered for it. Once visited, per-dataset `gallery-state-*` state (persisted to `localStorage`) takes precedence — use the Reset filters button in the gallery toolbar to clear it and fall back to these defaults again. Helpers `getGalleryDefaultSort()`, `getGalleryDefaultCaptionFilter()`, `getGalleryDefaultQualityFilter()` in `storage.ts` are used at `useState` init time.

Constants defined in `docs/dev/frontend-core.md` § Frontend constants.

**Captioning tab** — seven immediate-save preferences (lazy-loads `["captioning-models"]` query only when tab is first opened). These are **first-run fallbacks**: they apply the first time you visit the Captioning page, or after clearing the remembered workflow configuration. Once the page has been used, the model, style, scope, and other settings are remembered automatically via `CAPTIONING_WORKFLOW_KEY` (see Persistent page state in `docs/dev/frontend-core.md`) and take precedence over these defaults.
- Default model (`CAPTION_DEFAULT_MODEL_KEY`). Applied if no workflow blob exists yet and `selectedModel` is `""`. If the remembered workflow model is not installed (uninstalled model / removed provider), the model-validation `useEffect` clears it and falls back to this key. Also corrects the remembered style if it is incompatible with the applied model.
- Default style (`CAPTION_DEFAULT_STYLE_KEY`, e.g. `"detailed" | "short" | "tags" | "promptgen" | "booru"`).
- Default scope (`CAPTION_DEFAULT_SCOPE_KEY`, `"uncaptioned" | "all"`).
- Default delimiter mode (`CAPTION_DEFAULT_DELIMITER_KEY`, `"overwrite" | "append" | "prepend"`).
- Strip refusals toggle (`CAPTION_DEFAULT_STRIP_REFS_KEY`, default `true`).
- Rename on caption toggle (`CAPTION_DEFAULT_RENAME_KEY`, default `false`).
- Save backup toggle (`CAPTION_DEFAULT_SAVE_BACKUP_KEY`, default `false`).
- **Reset remembered Captioning configuration** ghost button at the bottom of this section: calls `clearPersisted(CAPTIONING_WORKFLOW_KEY)` (global workflow only — per-dataset filter blobs are cleared from the on-page button per dataset). `toast.success` only, no confirm dialog.

**UI Behavior tab** — immediate-save preferences:
- Delete-confirmation default button (`cancel` / `confirm`). Stored under `CONFIRM_DEFAULT_KEY`. Read by `ConfirmDialog` on every mount when `danger=true` and no `defaultFocus` prop is provided.
- Branch snapshot behavior (`ask` / `auto`). Stored under `BRANCH_SNAPSHOT_KEY`. When `"ask"`, `BranchSelector` shows an inline prompt before checkout or branch creation letting the user choose whether to create a snapshot. When `"auto"`, snapshots are always created without prompting.
- **Auto-rescan dataset on open** (`auto_rescan_on_open`, default off). Unlike the two above, this is a *server-side* setting persisted on the `ThresholdSettings` row (not localStorage), but the toggle still saves immediately via `mutation.mutate({ auto_rescan_on_open })` rather than the page-level Save button. When on, opening a dataset gallery fires `POST /datasets/{id}/rescan` once per dataset open (`GalleryPage`, gated by the `settingsApi.getThresholds` query). See `docs/dev/gallery-and-images.md` § Importing captions & folder rescan.

**Quality Thresholds tab** — seven editable number inputs from the `FIELDS` array: blur, noise, uniformity, duplicate, watermark, NSFW, and DINO box confidence (`gdino_threshold`). Requires Save; the flag thresholds apply to the next scoring run, `gdino_threshold` to the next SAM2 detection run.

**Versioning tab** — version control mode radio (`off | manual | auto`) plus branch snapshot behavior radio. Requires Save for the version control mode; branch snapshot behavior is immediate (localStorage).

**LLM Providers tab** — manage OpenAI-compatible provider configurations (see OpenAI-compatible providers section). Add / edit / delete providers. Name and Base URL are required; changes are saved immediately per-mutation (no page-level Save). Provider mutations also invalidate `["captioning-models"]` so the model picker on CaptioningPage reflects changes immediately.

### System hardware stats

Router: `backend/routers/system.py`, two endpoints both mounted at `/api/v1/system`.

**`GET /system/gpu`** returns `{ name, used_mb, total_mb, utilization_pct }` by trying three external sources in priority order: (1) `nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits` for NVIDIA GPUs; (2) `rocm-smi --showmeminfo vram --csv` for AMD ROCm (ROCm 6.x CSV: `device,VRAM Total Memory (B),VRAM Total Used Memory (B)` — GPU name falls back to device ID e.g. `card0`); (3) `torch.mps.current_allocated_memory()` / `torch.mps.driver_allocated_memory()` for Apple Silicon (name = `"Apple Silicon (MPS)"`, `utilization_pct = null` since unified memory has no fixed GPU partition). Returns `{ name: null }` when all three fail. **Do not revert to `torch.cuda.memory_allocated()` or `torch.cuda.mem_get_info()` here** — both are per-process CUDA context reads that miss VRAM allocated by other processes (e.g. Ollama).

**`GET /system/cpu-ram`** returns `{ cpu_pct, ram_used_mb, ram_total_mb }` via `psutil` (`psutil>=5.9` in `requirements.txt`). Both `psutil.cpu_percent(interval=0.1)` and `psutil.virtual_memory()` are run together inside `asyncio.get_running_loop().run_in_executor()` to avoid blocking the event loop (both calls perform blocking I/O — `/proc/meminfo` on Linux, `GetPerformanceInfo` on Windows). Wrapped in `try/except`; returns `{ cpu_pct: 0.0, ram_used_mb: 0, ram_total_mb: 0 }` on any failure.

**Frontend**: The Sidebar footer (`frontend/src/components/layout/Sidebar.tsx`) renders three stacked hardware meters — CPU, RAM, and GPU — using a shared `MeterRow` helper component (defined in the same file). CPU and RAM are driven by `useCpuRamStats` (`frontend/src/hooks/useCpuRamStats.ts`); GPU is driven by `useGpuStats` (`frontend/src/hooks/useGpuStats.ts`). Both hooks poll every 5 s via TanStack Query with `retry: false`. In-loop SSE progress emitters (captioning, detection) use `_device.memory_reserved_mb()` from `backend/ml/device.py` — subprocess overhead is unacceptable inside the per-image inference loop, and those emitters only cover PyTorch-loaded models anyway.

### File browser

Router: `backend/routers/filesystem.py`, prefix `/api/v1/filesystem`, registered in `main.py`.

| Endpoint | Purpose |
|---|---|
| `GET /roots` | Windows drive roots (`C:\`, `D:\`, …) |
| `GET /list?path=` | Directory listing — dirs first, then files, both alphabetical; `is_image` flag for image extensions |
| `GET /preview?path=` | Serve image file directly (`FileResponse`) |
| `GET /image-meta?path=` | `{width, height, format, file_size_bytes, generation_metadata}` — reads file without touching DB |
| `POST /move` | Move file/dir; syncs `Image.file_path`, `Image.filename`, `Image.dataset_id` when path is inside a dataset folder |
| `POST /rename` | Rename in place; same DB sync |
| `POST /delete` | Delete file or directory (recursive); deletes files from filesystem first, then removes `Image` DB records — so a failed FS deletion leaves DB records intact |
| `POST /mkdir` | Create directory |

**DB sync**: `_find_dataset_for_path(path, session)` checks if `path` is inside any dataset's `folder_path` and returns the dataset. Move/rename/delete use this to keep `Image` records consistent without a separate import step.

**Path safety**: `_sanitize_path()` rejects null bytes and requires an absolute path. No further sandbox — this is a local desktop app with intentional full-filesystem access.

Frontend page: `FileBrowserPage.tsx`, route `/file-browser`, sidebar nav item "File Browser". Three-panel layout (`200px | 1fr | 280px`): left = drive roots + quick-access links, middle = breadcrumb + file list + context menu (rename/delete/import), right = image preview + `<GenerationMetadata>` panel.

API client: `frontend/src/api/filesystem.ts` — thin wrappers over all endpoints; `previewUrl(path)` returns a URL string for use in `<img src>`.

### Logs page

Frontend page: `frontend/src/pages/LogsPage.tsx`, route `/logs`, sidebar nav item "Logs" (with a red badge showing the unread error count when `errorConsoleStore.errors.length > 0`).

Two tabs rendered with the standard `.tabs` / `.tab` CSS classes:

**History tab** (`HistoryTab` component):
- Fetches `GET /api/v1/jobs/?limit=200` via TanStack Query (`queryKey: ["jobs"]`, `staleTime: 10_000`). The `limit` param was made configurable in `backend/routers/jobs.py` (`Query(50, ge=1, le=500)`) — `LogsPage` passes 200; existing callers (none pass `limit`) keep the former 50-record default.
- Client-side filter input searches across `label`, `job_type`, and `dataset_id` fields.
- Each row: `StatusBadge` (pending `--fg-mute` / running `--accent` / completed `--good` / failed `--bad` / cancelled `--fg-dim`), label (falls back to `job_type`), dataset ID chip (first 8 chars), relative timestamp (`Xm ago`) with absolute on `title`, duration (`finished_at − started_at`), and `done/total` counter when `total_items > 0`. Failed jobs show `error_msg` below the row in `--bad`.
- **Refresh** button re-invokes `refetch()`.

**Errors tab** (`ErrorsTab` component):
- Reads from `errorConsoleStore` (same data as the `ErrorConsole` overlay). See `docs/dev/frontend-core.md` § Error console for store details.
- Toolbar: error count summary, **Copy Errors** (calls `formatErrorsForCopy` → `navigator.clipboard.writeText`), **Clear** (calls `clearErrors()`).
- Each entry: timestamp, type badge (`error` / `rejection` / `render`), message, source file/line/col, collapsible stack trace `<details>`.
- Empty state: "No JS errors captured this session."

The Errors tab button in the tab bar shows a red pill badge when `errorCount > 0` — the same count drives the sidebar `NavItem` `tail` prop (with `tailColor="var(--bad)"`).

### Booru tag lookup page

A read-only tag-name/post-count lookup against external image boards. Nothing is persisted — it's a reference tool for finding correct booru tag spellings and gauging tag popularity while captioning.

**Router**: `backend/routers/booru.py`, prefix `/booru`. Two endpoints, no service-layer DB access:
- `GET /booru/search` — `q` (required, `min_length=1`), `source` (`safebooru` | `gelbooru`, regex-validated, default `safebooru`), `limit` (`1..100`, default 20). Dispatches to the matching service function.
- `POST /booru/autocomplete` — `AutocompleteRequest { prefix, source="safebooru", limit=10 }`. Same dispatch; intended for type-ahead (the current `BooruPage` doesn't wire it up — search is submit-driven).

**Service**: `backend/services/booru_service.py`. `search_safebooru(query, limit)` hits Danbooru's safe host `https://safebooru.donmai.us/tags.json` (`search[name_matches]=*query*`, ordered by count); `search_gelbooru(query, limit, api_key, user_id)` hits `https://gelbooru.com/index.php` (`s=tag` API). Gelbooru credentials come from `settings.gelbooru_api_key` / `settings.gelbooru_user_id` (env vars `GELBOORU_API_KEY` / `GELBOORU_USER_ID` via `config.py`); they're optional — omitted when blank, so anonymous requests still work but may be rate-limited. Both functions normalize results to `{tag, count, category, source}` dicts, mapping the numeric category id to a name (`0` general, `1` artist, `3` copyright, `4` character, `5` meta) via `_safebooru_category` / `_gelbooru_category`. Guardrails: an in-module 5-minute TTL cache (`_cache`, keyed `{source}:{query}:{limit}`, evicts expired entries on read), an `asyncio.Semaphore(2)` capping concurrent outbound requests, a 10-second per-request timeout, a 0.5s politeness delay before each Gelbooru call, and a blanket `except Exception: return []` so a booru outage never surfaces as a 500.

**Frontend**: `frontend/src/pages/BooruPage.tsx`, route `/booru` (`App.tsx`, also in `PageRenderer`/`PaneHeader` for split-view), sidebar nav "Booru Browser". API wrapper `frontend/src/api/booru.ts` (`booruApi.search` / `booruApi.autocomplete`), result type `BooruTag`. Search is submit-driven: the text input and Source/Limit selects update local state, and `handleSearch` (Enter or the Search button) copies `query` into a separate `search` state that is the actual query trigger — `useQuery({ queryKey: ["booru-search", search, source, limit], enabled: search.length > 0 })`. TanStack Query's default cache makes repeat searches instant (backed additionally by the backend 5-minute cache; the footer notes "Results cached for 5 minutes"). The `SOURCES` list includes `danbooru`/`e621` marked `supported: false` — selecting one and searching shows a toast ("… is not yet supported") and skips the request. Results render as a table (tag in category color, category `badge`, post count, and a **Copy** button that writes the tag to `navigator.clipboard` and toasts). Limit choices are 20/50/100 (default 50 in the UI).
