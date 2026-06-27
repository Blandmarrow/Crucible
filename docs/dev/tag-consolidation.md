# Tag consolidation (semantic, dataset-wide)

Dataset-wide synonym consolidation (issue #44, Stage B): embed the unique comma-separated
segments of every caption, cluster by cosine similarity, let the user confirm a canonical
term per cluster, then rewrite every caption. Segments are individual tags for booru-style
captions or whole phrases/sentences for natural-language captions, so this works on prose
too (`all-MiniLM-L6-v2` is a sentence transformer). Complements the cheaper
subsumption cleanup (`subsume_tags()` in `backend/utils.py`), which is exposed as the
captioning `dedupe_tags` post-processing flag (see `docs/dev/captioning.md`) and as the
synchronous `subsume` endpoint behind the Consolidate page "Quick cleanup", the
`SelectionToolbar` "Merge tags" action, and the `ImageDetailPage` per-image button.

## Backend

**Model** — `backend/ml/tag_embedder.py` wraps `sentence-transformers/all-MiniLM-L6-v2`
(~90 MB, auto-downloads to the HF cache on first use). Registered with `model_manager`
as id `tag_embedder` (`load_tag_embedder` / `_load_tag_embedder_sync`, ~500 MB VRAM
estimate) and listed in `list_models()`. Eviction uses the standard `entry.model.cpu()`
path (SentenceTransformer is an `nn.Module`).
- `embed_texts_sync(texts, model_entry) -> np.ndarray` — L2-normalised float32 `(N, 384)`.
- `cluster_texts_sync(texts, embeddings, threshold) -> list[list[int]]` — union-find
  connected components over the cosine-similarity graph (cosine = dot product because
  embeddings are normalised); returns index clusters with more than one member.
- `MAX_VOCAB = 4000` bounds the n² similarity matrix; larger vocabularies are truncated
  to the most frequent tags by the service before embedding.

**Service** — `backend/services/tag_consolidation_service.py`:
- `analyze(db, dataset_id, threshold, subfolder, job_id)` — streams `caption_text`
  (reusing the comma-split vocabulary pattern from `caption_service.get_tag_stats`),
  builds `{tag: frequency}`, truncates to `MAX_VOCAB`, embeds + clusters, and for each
  cluster picks **canonical = longest tag, ties → highest frequency** (WD14 confidence
  is unavailable — tag styles store plain text in `caption_text`). Returns
  `{clusters: [{canonical, variants: [{tag, count}], min_sim}], vocab_size, image_count, truncated}`.
  `min_sim` is the cluster's minimum pairwise cosine similarity (computed from the
  embeddings via `np.triu_indices`) — the UI's "Needs review" sort orders by it ascending
  so the shakiest clusters surface first.
- `apply(db, dataset_id, mapping, subfolder, job_id)` — `mapping` is `{variant → canonical}`.
  For each image it splits tags, applies **whole-tag replacement (never substring**, so
  `car` never rewrites `carpet`), drops resulting duplicates preserving order, rejoins,
  writes the `.txt` sidecar (`caption_service._write_txt_sidecar`), and commits once.
  Identity mappings are dropped up front. Returns `{affected, skipped}`.
- `subsume(db, dataset_id, subfolder, dry_run, image_ids=None)` — deterministic
  per-caption subsumption cleanup (`subsume_tags()` from `backend/utils.py`); no model,
  no job. With `dry_run` True nothing is written — only the count of captions that
  *would* change is returned (powers the page's "Quick cleanup" preview). `image_ids`
  (selection / single image) takes precedence over `subfolder`. This replaces the former
  `dedupe_tags` bulk-edit operation, which has been removed.

**Router** — `backend/routers/tag_consolidation.py` (prefix `/tag-consolidation`,
registered in `main.py`). Both endpoints follow the `BackgroundJob` →
`job_queue.enqueue(job, _run)` → SSE pattern (see `docs/dev/captioning.md`):
- `POST /dataset/{id}/analyze` (`{threshold≥0.5≤1, subfolder?}`) — job type
  `tag_consolidate_analyze`; `_run` calls `analyze` and stores the proposal in
  `job.result_data`. Returns `{job_id, total}` (or `{job_id: null, message}` if no
  captioned images). The frontend reads the proposal via `GET /jobs/{id}` once the job
  completes — `JobOut.result_data` already exposes it; no extra endpoint.
- `POST /dataset/{id}/apply` (`{mapping, subfolder?}`) — job type
  `tag_consolidate_apply`; `_run` calls `apply` and stores `{affected, skipped}` in
  `result_data`.
- `POST /dataset/{id}/subsume` (`{subfolder?, dry_run, image_ids?}`) — **synchronous**
  (no job); returns `{affected, skipped}` directly. Powers the page's Quick cleanup
  section as well as the per-selection and per-image "Merge tags" actions (see below).

## Frontend

`frontend/src/pages/TagConsolidatePage.tsx` at `/datasets/:datasetId/consolidate`
(sidebar "Consolidate Tags"; wired into `App.tsx`, the pane system `PageRenderer`/
`PaneHeader`, and `PageType` in `PaneContext`). API client `api/tagConsolidation.ts`.
A single page-level **subfolder** select scopes both sections.

- **Quick cleanup** — deterministic subsumption. A `["subsume-preview", datasetId,
  subfolder]` query (`subsume(..., dry_run:true)`) shows "N of M captions affected"; the
  Run button calls `subsume(..., dry_run:false)` then invalidates caches. This is the
  one home for subsumption (it was removed from `BulkEditForm`).
- **Find synonyms** — threshold slider (persisted to `localStorage` under
  `TAG_CONSOLIDATE_WORKFLOW_KEY` via `loadPersisted`/`savePersisted`) → **Analyze** fires
  the job; a `useRef`-guarded effect fetches `jobsApi.get(jobId).result_data` once and
  seeds editable cluster state.
- **Cluster review** uses a **default-accept** model: every cluster's `accepted` defaults
  `true`; the user unchecks the few they disagree with, then Apply. The list is a
  **virtualized** (`@tanstack/react-virtual` `useVirtualizer`, dynamic row heights via
  `measureElement`) scroll container of dense one-line rows
  (`[✓] canonical ← variants · N uses · sim X.XX`) that expand to a canonical editor
  (`<input>` + `<datalist>` so the user can pick or type a new term) and per-variant
  exclude chips. A sticky toolbar adds a tag **search** filter, a **sort** select
  (Impact / Cluster size / Needs review (`min_sim` asc) / A–Z), **Accept all** / **Skip
  all**, and **Apply (K)**. Cluster mutations key off a stable `id`, not the filtered
  index. `mapping` = accepted, non-excluded, non-canonical variants.
- **Apply** fires the apply job; on completion `invalidateCaptionCaches()` (a
  `useCallback`) invalidates `["images", datasetId]` + the four stats keys + the
  per-image `["caption"]` / `["image"]` families + the `["subsume-preview"]` query, so an
  open `ImageDetailPage` and the Quick-cleanup count both refresh immediately. Then it
  clears the result view.

**Subsumption on other surfaces** (semantic clustering stays only on this page):
- `SelectionToolbar` — a **Merge tags** button runs `subsume(image_ids: selectedIds)` on
  the selection, invalidates the usual caches + `["caption"]`/`["image"]`, clears the
  selection, and toasts the affected count.
- `ImageDetailPage` — a per-image **Merge redundant tags** button under Save. It first
  persists the editor buffer if `captionDirty` (the backend operates on the stored
  caption), then runs `subsume(image_ids: [imageId])`; it resets `captionDirty` so the
  `["caption", imageId]` refetch refreshes the textarea with the cleaned tags.
