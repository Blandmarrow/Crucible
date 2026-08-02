# Image-to-image similarity — duplicates and style

This file covers the two comparisons Crucible makes *between* images rather than about one: pHash duplicate grouping (and the same-source annotation the video arc added to it) and the CLIP/DINOv2 style-similarity scoring flow. The per-image scores these lean on — `phash` and the technical scorer that computes it, `clip_embedding`/`dino_embedding`, and the `duplicate_threshold` setting — are `docs/dev/scoring.md`. The CLIP and DINOv2 models themselves are loaded and evicted through the shared model manager: see `docs/dev/ml-models.md`.

## Duplicate detection

**Duplicate detection** (`technical_scorer.find_duplicates_sync`) runs after technical scoring: it greedily groups images whose phash Hamming distance is `< duplicate_threshold` (first unassigned image is the group root and claims every *later* unassigned image within the threshold, members in input order; each image is claimed once). `find_duplicates_sync` is a **dispatcher over two exact, output-identical implementations** — only speed differs, never results:

- `_find_duplicates_indexed` (the path at scale): a pigeonhole multi-index chunk search, ~linear in N. Each hash is split into 4 chunks; any pair within distance d must agree on ≥1 chunk up to ⌊d/4⌋ bit flips, so probing 4 chunk tables with the chunk value XOR every ≤⌊d/4⌋-bit mask is guaranteed to surface every true neighbor as a candidate, which is then verified with the exact `dist < duplicate_threshold` popcount comparison. **Do not "simplify" the chunk count or radius derivation** — an undershoot silently drops duplicate pairs.
- `_find_duplicates_bruteforce`: the O(N²) vectorized all-pairs scan (numpy + module-level 256-entry `POPCNT` table). Semantically frozen — it is the reference implementation the golden tests compare against, and the fallback when `n < MIN_INDEX_N` (2048), the hash is shorter than 4 bytes, the threshold is so large the index would probe more than `CANDIDATE_FRACTION_CUTOFF` (0.25) of all rows per query (≥ ~21 for 64-bit hashes; practical thresholds are 4–12), or the total probe volume exceeds `n // PROBE_COST_DIVISOR` (8) — probes are pure-Python dict lookups, far costlier each than a vectorized scan row, so the index must be clearly cheaper before it engages (at 64-bit hashes, thresholds 13–20 need n ≳ 22k–80k).

Both paths are length-generic (no 64-bit assumption; the chunk-key fold must stay **unsigned** — a signed int64 fold wraps 8-byte-chunk keys negative and silently drops pairs whose probe crosses bit 63). The dispatcher and the golden tests both derive the chunk split and probe radius from the shared `_chunk_plan()` helper, so the tested plan cannot drift from the production one. `backend/tests/test_find_duplicates.py` pins the byte-identical-output property (groups, roots, member order) across sizes, thresholds (incl. floats — `threshold_settings.duplicate_threshold` is a Float column, though the quality router currently truncates it to `int` before calling, so algorithm-level float support is future-proofing), and hash lengths up to 256-bit; it runs in CI via `.github/workflows/backend-tests.yml` (cv2 is imported lazily inside `score_technical_sync`, so *these* tests need no cv2 and no stub — the workflow installs it anyway, for the video suite: see `docs/dev/video-tests.md` § cv2 in CI, and the skip convention). The O(N²) path was the critical scaling wall found in `backend/scripts/scaling_bottlenecks_report.md` (~3.4 h projected at 1M images); re-verify with `python -m backend.scripts.bench_scaling --only dedup` after touching this code. The consumer `_flag_duplicates` (`routers/quality.py`) then loads the flagged images with a single chunked `select(...).where(Image.id.in_(...))` (≤10k ids per chunk) rather than per-row `session.get`, and follows the copy-then-reassign `quality_flags` invariant. `backend/tests/test_quality_flags_persistence_http.py` pins that invariant at request level: duplicate-resolve keep and empty-caption artifact clearing must persist their flag changes to a fresh session.

### Same-source duplicate groups

`GET /quality/duplicates/{dataset_id}` annotates every member row with `source_video_id`, `source_timestamp_ms`, `source_shot_index` and a resolved `source_video_name`. Frames from one video — held animation cels, recycled footage, a locked-off shot — land inside Hamming 8 legitimately, get grouped, and *Keep best* deletes them with nothing on screen saying they share a source.

**The annotation lives in the read path, not in `_flag_duplicates`.** Teaching the scan to skip same-source pairs would be wrong twice: `is_duplicate` feeds bulk filters, the Stats flagged counts, export exclusions and the gallery badge, so changing what the scan flags has a blast radius the fix does not need — and two frames from one shot often *are* duplicates the user wants gone. The defect is silence, not the grouping.

The endpoint already used `select(Image)`, so lineage is loaded; the names come from one chunked `select(Video.id, Video.filename)` over the distinct non-NULL ids, resolved **before** any row is built so it covers the separately-fetched root as well as the flagged members. There is no `relationship()` from `Image` to `Video` and none should be added — a lazy load on an async session raises `MissingGreenlet`. The response stays a **list of lists**: promoting a group to an object with a `same_source` field would break `DuplicateGroup` and every assertion in `test_duplicate_groups_http.py` for a boolean the client derives in one line.

`QualityPage` derives that boolean in `sharedSourceVideo(group)` — non-null only when every member carries the same non-NULL id — and renders a warn-toned banner naming the video above the thumbnails; a *mixed* group gets per-thumbnail video labels instead, because the banner would be false there. Each thumbnail shows `formatFramePosition(source_timestamp_ms)` and its shot index.

**Not `formatDuration`** — that helper answers "how long is this clip?" at second resolution, which is the right granularity for a length and the wrong one for a frame. Frames cut from one held shot sit tens of milliseconds apart, so two of them both render as `0:01` and a panel showing timestamps *so the user can tell them apart* says nothing. Found by running the real app: a group at 600 ms and 760 ms rendered two identical labels. `formatFramePosition` (its sibling in `frontend/src/utils/duration.ts`, same null contract) prints `0:00.760`. Use it wherever a `source_timestamp_ms` is shown.

Two things changed about the buttons at the same time:

- **"Keep best" goes through a `ConfirmDialog` for same-source groups**, naming the video and listing the timestamps being deleted. Refusing outright would break the legitimate case and push users to work around it in the gallery. **"Keep first" stays one click** — it keeps the scan's own choice rather than a score-driven one.
- **The null sort was backwards.** `(b.aesthetic_score ?? 0) - (a.aesthetic_score ?? 0)` ranks an unscored frame below a 0.1 and so deletes it preferentially, when unscored means *unknown*, not bad. `rankForKeepBest` is an explicit nulls-last comparator, and the button is disabled with an explanatory `title` when no member has a score — "best" is meaningless there, and the old code silently kept whichever came first and called it best.

### Bulk resolution

A scoring run over a large dataset produces a panel with a hundred-odd groups, and clearing them one card at a time is not a workflow. The panel's top row carries filter chips and two bulk buttons — *Keep best in N groups* and *Keep first in N groups* — plus per-group buttons unchanged.

**The chips are a partition, not overlapping predicates.** `DupFilter` is `"all" | "video" | "other"`, where `video` is `shared != null` (the `sharedSourceVideo` boolean above) and `other` is its complement, so the two counts always sum to the total and running a bulk action over each in turn touches every group exactly once. The row renders only when at least one group is same-source — three chips over a partition with an empty half are noise — and the filter the panel actually applies is **derived** rather than corrected: `activeDupFilter` falls back to `"all"` whenever `videoGroupCount === 0`, so switching datasets cannot strand the panel on a chip that is no longer rendered. Deriving it rather than resetting the stored value in an effect buys two things: there is no render in which the panel is filtered by a chip it is not showing (an effect corrects only *after* that render), and `dupFilter` keeps its value, so the chip re-applies by itself when video groups reappear. **The chip state is deliberately not persisted**: it is ephemeral triage state, reset by the next refetch anyway, and putting it in the dataset-scoped `QUALITY_FILTERS` blob would pull in the `constants/storage.ts` registry rules for no benefit.

**The count in a bulk button is the filter's count, never the page's.** Group cards are paged 25 at a time (`DUP_PAGE_SIZE`, a *Show 25 more (N remaining)* button below the list; the paging resets on both a dataset change and a chip change, but from neither an effect of its own nor one effect covering both — the dataset half rides the existing pane-`datasetId` effect that reloads the persisted filters blob, `QualityPage.tsx:316`, guarded by `prevDatasetId`, and the chip half is a plain `setVisibleGroups` in the `pickDupFilter` click handler), but the bulk plans are built from the whole filtered set. This is the same superset trap fixed for gallery select-all in `fa4f5c4`: the label and the confirm dialog must both state the group count the action really covers.

**Bulk *Keep best* skips a group with nothing scored — it never falls back to first.** That is the same rule the per-group button enforces by disabling itself, for the same reason (`rankForKeepBest` sorts nulls last because the old code silently kept whichever image came first and called it best). The skipped count is surfaced twice: in the button's `title` when some groups are skipped, and in the confirm dialog's copy. When every filtered group is unscored the button disables entirely.

**A group holding two aesthetic models is skipped the same way, counted separately.** `DupGroupMeta.mixedModels` is true when a group's scored members carry more than one `aesthetic_model` marker; per-group *Keep best* disables with a title naming both models, and `bulkPlans` filters those groups out into a `skippedMixed` counter beside `skipped`, so the confirm dialog can state which reason applied. **The bulk bar skips rather than disabling itself** — killing it because 1 of 300 groups is mixed pushes users to resolve by hand in the gallery, which is the same argument the same-source confirm dialog makes. `rankForKeepBest` itself is untouched: it is a pure, null-safe sort and is correct *within* one scale, so the refusal belongs at the caller alongside the unscored one. Thumbnails in a mixed group carry a small producer label under the score chip, so the disabled button explains itself outside a tooltip. See `docs/dev/scoring.md` § The marker is a safety device.

**Bulk always confirms**, even with no same-source group in the plan — it is a many-image irreversible delete over groups the user has not read, whereas a per-group resolve confirms only when the group is same-source (unchanged). Both paths share one `resolveConfirmCopy`, over a `PendingResolve` union of a `"group"` and a `"bulk"` variant. Both variants carry `plans: DuplicateImage[][]` — one ordered member array per group, `plans[i][0]` surviving — so the mutation takes one shape and a single-group resolve is just a plan of one. The survivor clause (*keeps the highest-scoring one* / *keeps the one the duplicate scan picked, which is not necessarily the best*) is one expression used by both; a second hardcoded message block would drift the caveat.

**The mutation batches `RESOLVE_BATCH_GROUPS` (40) groups per `POST /quality/duplicates/resolve` call.** Each group is independent, so partial application is coherent — this is not a transaction that must land whole — and batching keeps the request bounded, since the endpoint's versioning hook copies the bytes of every deleted row into the object store when versioning is on. It also makes progress reportable: the button label becomes `Resolving 80/138…` while a run is in flight, and a failure partway raises a `PartialResolveError` carrying the completed count so the toast says `Resolved 80 of 138 groups before failing` rather than a bare error. `onSettled` invalidates `["duplicates", datasetId]` and calls `invalidateDatasetContentScope` on both outcomes — a partial run deleted real rows. The shared scope, not the lone `["images", datasetId]` this carried when the endpoint resolved one group at a time: a bulk run deletes hundreds of rows, which leaves the sidebar image count, the `DatasetsPage` card and the four stats queries exactly as stale as the gallery list, and nothing else would refetch them for the 30 s `staleTime`. See `docs/dev/frontend-core.md` § `constants/` for the helper.

**Progress state belongs to the run that owns it.** The mutation variable is `{ plans, bulk }`, and `setBulkProgress` is written **only when `bulk`** — the label it drives sits on the two top-row buttons, so a single card's *Keep first* writing it relabelled both of them `Resolving 0/1…` for a run they do not cover. One `ConfirmDialog` serves both paths, so `bulk` comes off `pendingResolve.kind` rather than off which button opened it. Per-group buttons stay disabled by `resolveBusy` throughout — that is a genuine busy state, not a claim about whose run it is.

`resolve_duplicates` needed three scale fixes to match, since it had only ever been called with a handful of ids: the `select` over `delete_ids`, the `delete(Image)`, and the keep-side flag clear are all `chunked()` loops now (the mandatory form for a batched `IN` — see `docs/dev/shared-utilities.md`), and the keep loop is one batched `select` per chunk instead of a `db.get` per id. The copy-then-reassign `quality_flags` mutation is unchanged, and the step ordering is unchanged: gate → versioning hook → row delete → commit → unlink → `refresh_stats`.

**Card layout.** Three fixes travel with the bulk bar, all about the same failure — a 36-member group pushing its action column off the right edge of the pane, unreachable exactly when the group is biggest. The card grid is `minmax(0, 1fr) auto`, because a bare `1fr` track refuses to shrink below its content; the thumbnail row is `flexWrap: "wrap"`; and a group longer than `DUP_COLLAPSE_AT` (10) renders its first ten members plus a tile-sized `+26 more` / *Show fewer* toggle, keyed in an `expandedGroups` set by `rootId` (`group[0].id` — stable across refetch, unique per group, and the React key as well; the array index was neither). The kept root is `group[0]`, so a collapsed card always shows the survivor. Thumbnails are `loading="lazy"`, which is what stops a full panel firing several hundred requests on render.

## Style similarity

**Style similarity flow**: (1) run scoring with the desired embedding flags — `run_embeddings=True` stores `clip_embedding`; `run_dino=True` stores `dino_embedding` (independent of `run_embeddings`); `run_dino_layers=True` (requires `run_dino=True`) stores `dino_layer_embeddings`. (2) call `POST /quality/style-similarity` with `reference_image_ids` and/or `reference_embeddings` (base64 float16 bytes, CLIP-only). The `embedding_type` field selects the scoring mode:

| `embedding_type` | `dino_layer` | Column(s) written | Description |
|---|---|---|---|
| `"clip"` | — | `style_similarity_score` | Cosine similarity of CLIP embeddings |
| `"dino"` | `null` | `style_similarity_score` | Cosine similarity of DINOv2 final-layer embeddings |
| `"dino"` | 1–12 | `style_similarity_score` | Cosine similarity using a specific DINOv2 transformer layer (from `dino_layer_embeddings`) |
| `"combined"` | `null` | `style_similarity_score` | `0.38 × clip_sim + 0.62 × dino_sim` (final layer) |
| `"combined"` | 1–12 | `style_similarity_score` | `0.38 × clip_sim + 0.62 × dino_layer_sim` (specific layer) |
| `"dino_all_layers"` | — | `dino_layer_scores` (JSON) + `style_similarity_score` | Scores each of the 12 DINOv2 layers independently; writes `{"1": score, …, "12": score}` and sets `style_similarity_score` to layer 12's value |
| `"combined_all_layers"` | — | `dino_layer_scores` (JSON) + `style_similarity_score` | Blended score (0.38 CLIP + 0.62 DINOv2) for each of the 12 layers; writes `{"1": score, …, "12": score}` and sets `style_similarity_score` to layer 12's value |

Local reference files can be embedded on-the-fly via `POST /quality/embed-references` (multipart upload → returns base64 CLIP embeddings). External refs are CLIP-only; `"combined"`, `"dino"`, and `"dino_all_layers"` / `"combined_all_layers"` modes require dataset images as references. No job queue — all similarity computation is CPU-only numpy and runs synchronously in the request. `StyleSimilarityRequest` accepts an optional `image_ids: list[str] | None` field; when set, only those images are scored (candidate queries in all embedding-type branches are filtered accordingly). `QualityPage` omits `image_ids` (scores the whole dataset); `SelectionToolbar` passes the current selection.

**Getting the reference ids out of the picker.** `StyleReferencePicker`'s selection is React state on `QualityPage` (`selectedRefIds`) and the ids are never rendered, so nothing outside the page can consume the chosen set. The Action row's secondary **Copy reference IDs** button writes `Array.from(selectedRefIds).join(",")` to the clipboard for that reason — it exists to feed offline harnesses such as `backend/scripts/style_gate.py`, and covers dataset references only, since dragged-in local files are embedded on the fly and have no `Image.id`.

### What the modes are actually worth

Measured on 2026-08-02 by `backend/scripts/style_gate.py` (a read-only offline harness that calls these same production functions), against 98 frames of one animated film scored from eight references of one lighting cluster, with **20 out-of-style controls** — bright painted illustrations — mixed in. Full method, tables and verdict in `backend/scripts/style_gate_report.md`. This was Phase 0 of the aesthetic-rating roadmap, and its conclusion is that **the centroid baseline is good enough that style matching does not need a learned head**: AUC 0.94–0.97 separating frames from controls, no control in any mode's top 20.

Three things that change what to recommend, none of them visible from the code:

- **CLIP is the best mode for style matching, and range is a misleading proxy for that.** CLIP's scores occupy a narrow band (0.53–0.93 vs DINOv2's 0.05–0.70) yet it separates best — AUC 0.9733 vs 0.9417, its worst control ranked 75th of 110, and only 15 of 90 frames scored below its best-scoring control against DINOv2's 46. DINOv2 drifts toward *subject and framing*: its top band filled with close-ups of one character that share the reference shots' composition but none of their palette. Both are defensible readings of "style"; the picker's copy should not imply DINOv2 is the more advanced choice.
- **`combined` is not an independent third opinion.** Spearman ρ 0.9844 against `dino`, sharing 18 of 20 top images — it is DINOv2 with a slight CLIP tilt. CLIP vs DINOv2 (ρ 0.7525) is the only real disagreement, so anything wanting an A/B across "the three modes" is really testing two.
- **Per-layer scoring below layer ~10 cannot be thresholded.** Every candidate scored 0.90–0.99 on layers 1–8: some ordering survives there (AUC 0.68 at layer 1 rising to ~0.94 by layers 5–9, none beating layer 12's 0.961) but it is compressed into a few hundredths, most of which the 4-decimal rounding spends on the constant part. Usable spread appears only at layers 10–12 (range 0.25 → 0.65). The `DINO_LAYER_LABELS` picker offers all twelve as equals; for filtering they are not.

`style_similarity_score` is the one score column a re-scoring run cannot refresh, and so the sole member of `_UNREFRESHABLE_SCORE_COLUMNS` — see `docs/dev/scores-stale.md` § The clear predicate for why that is a boundary rather than an oversight.

**All-layers scoring is vectorized and RAM-bounded** (`dino_all_layers` / `combined_all_layers`): rather than re-slicing every reference blob per candidate per layer (the old `slice_layer_embedding` inner loops), the per-layer normalized mean reference is computed once via `_mean_layer_refs()` (stack refs → mean per layer → L2-normalize), and each candidate blob is decoded whole with `_decode_dino_layers()` (`np.frombuffer(...).reshape(12, 768)`); scoring is one matmul per layer (`cand[:, l, :] @ mean_refs[l]`), matching `compute_style_similarity`'s normalize-then-dot exactly. In combined mode the CLIP score doesn't depend on the layer, so it's computed once per chunk. Candidates are **keyset-paginated** (`WHERE Image.id > last ORDER BY Image.id LIMIT 2000`) through the shared local `_score_all_layers_paginated()` helper so the ~18 KB per-layer blobs are never all resident at once (~1.8 GB at 100k images). Output shape and rounding are byte-identical to the pre-vectorization loop (combined rounds the blended score to 4 decimals; both round each per-layer cosine to 4).

### Making the score readable — the run descriptor and the percentile

The gate above is also the argument for everything in this section: a stored
`style_similarity_score` is a raw cosine, and nothing about it says which of the five modes,
which layer or which references produced it. The same "0.62" is a mediocre CLIP match, a
strong DINOv2 one, or meaningless on a layer-3 run where every image lands in 0.90–0.99.

**`StyleSimilarityRun` (`backend/models/style_run.py`) is the missing descriptor.** One row
per dataset, `dataset_id` unique, overwritten by every successful run — it describes the
values *currently* in the column, not a history. It holds the mode, the layer, up to
`REFERENCE_IDS_STORED_MAX = 64` reference ids, the true reference counts, the scored/skipped
counts and `scoped_image_count`.

Three decisions worth not re-deriving:

- **A table, not columns on `datasets`.** `Dataset.updated_at` is half the
  `get_dataset_stats` cache validator, so a descriptor written onto that row would evict the
  whole Stats aggregation on every style run; `DatasetOut` is also hand-built field by field
  in `list_datasets`, the trap `CLAUDE.md` names for `video_count`. The `VersionImageState`
  mirroring rule does **not** reach it: that guard derives its universe from
  `Image.__table__.columns`, and this is a new table describing dataset-level state.
- **No backfill, and none is possible.** An already-scored dataset could have used any of
  five modes against any reference set, and neither is recoverable from the column. Existing
  datasets get no row and the UI says *"the run details were not recorded"*.
  `duplicate_dataset` deliberately does not carry the descriptor either — the clone's
  `reference_image_ids` would point at the *source* dataset's images — so a clone lands in
  the same state and gets the same message.
- **The route is a thin wrapper, and that is what makes the write success-only.**
  `@router.post("/style-similarity")` awaits `_score_style_similarity(body, db)` and then
  calls `_record_style_run`. The scoring function has **six** success returns, one per
  branch, so an inline write would be six drift-prone call sites; and every failure path in
  it raises `HTTPException`, so the wrapper's line is never reached on a failure. That
  matters: a run that raised wrote no scores, and must not relabel the values already in the
  column with a mode that never ran. `_record_style_run` is select-then-write (no
  `on_conflict` construct exists anywhere in `backend/`) and swallows-and-logs, per
  `CLAUDE.md`'s post-commit epilogue rule — the scoring has already committed, so a failure
  here costs the run's *description*, not the run.

**A scoped run overwrites anyway.** `image_ids` set (the `SelectionToolbar` path) records
`scoped_image_count` rather than declining to write: it is the most recent run and its
references do describe the most recently written values. The count is what lets consumers
qualify the rest — the UI shows an amber note on the detail block and a tooltip clause on
the card rather than hiding the feature. Consumers test `scoped_image_count !== null`
client-side, **not** a server-computed boolean, which would go stale the next time an image
is deleted.

#### `GET /quality/style-similarity/{dataset_id}`

Declared right after the POST, returns a plain dict (this router uses no response models),
and like its sibling `aesthetic_coverage` returns an **empty payload rather than 404** for
an unknown dataset — the caller is a gallery card, not a navigation.

```
{ scored, total, quantiles: number[21], quantile_step: 5, run: {…} | null }
```

**Why a percentile and not a threshold.** A fixed good/warn/bad cut means five different
things in five modes — that is the defect, not the fix. A percentile over the dataset's own
scores is mode-invariant by construction, so the meter's *length* carries the meaning and
its colour stays a single `--accent` fill. The second reason not to band by colour: a low
style match means *different*, not *defective*, and the card already spends red/amber on
NSFW, blurry, near-uniform and aesthetic.

**Dataset-wide, with no `subfolder` parameter.** The same image must read the same in a
filtered pane, an unfiltered pane and on the detail page. `StyleSimilarityRequest` has no
subfolder concept either, and `ix_images_dataset_similarity` makes the ordered scan covering.

**21 breakpoints, every 5th percentile** — ±2.5 pp worst case for ~200 bytes. 101 is more
resolution than a 4-px strip can spend, and deciles are visibly chunky exactly where style
matching cares (the top of the range).

The service half is in `backend/services/dataset_service.py` — it must be, to reach
`_stats_cache` / `_image_validator` without exporting them — and the router imports
`get_style_distribution` as it already does `refresh_stats`:

- `_style_quantiles(sorted_vals)` sits beside `_p95` and is **nearest-rank scaled by
  `n - 1`**, so q0 and q100 are exactly min and max. That is the contract the client's clamp
  is written against. Deliberately **not** unified with `_p95`, which scales by `n` (a
  different, also-correct convention) and has its own pinned tests.
- `_style_validator(db, dataset_id)` → `(*_image_validator(…), style-run updated_at)`. The
  image half alone would suffice — a style run writes through `db.execute(update(Image),
  [...])`, and that executemany *does* apply `Image.updated_at`'s Python-side `onupdate` per
  row — but the payload embeds a row from another table, and watching it too is one cheap
  `max()`.
- `get_style_distribution` caches under a third `_stats_cache` slot, `"style"`, beside
  `"stats"` and `"scores"`. See `docs/dev/statistics.md` for the `_STATS_CACHE_MAX` budget
  effect; do not change the constant to compensate.

#### The client-side percentile contract

`frontend/src/utils/percentile.ts` is pure and has three call sites. There is **no frontend
unit-test runner in this repo**, so every degenerate input is handled defensively there and
the shapes that produce them are generated from the backend side by
`backend/tests/test_style_distribution_http.py` (one scored image → 21 identical
breakpoints; three scores → repeated values).

| Case | `percentileOf` returns |
|---|---|
| unscored image, or quantiles not loaded (`length < 2`) | `null` → render nothing. A zero-length bar reads as "0th percentile", not "not measured" |
| all scores identical, including `scored === 1` | `null` → fall back to the raw cosine, no meter |
| fewer scored images than breakpoints | repeated breakpoints; the `span > 0` guard answers with the low edge of the flat run |
| score below q0 / above q100 | clamp to 0 / 100 |

`styleMatchTitle({percentile, score, run, stale})` is one shared tooltip builder so the card
and the detail block cannot drift on the caveats (mode, reference count, mixed scope, stale
pixels).

#### The rendered surfaces

- **`ImageCard`** — a 4-px strip pinned `left/right: 0, bottom: 0, zIndex: 2` inside the
  thumbnail div. It clears the flag cluster, checkbox and aesthetic badge (all `z: 3`, all
  inset 8 px) and sits correctly under the caption-drop overlay (`inset: 0, z: 4`). Fill
  width is `Math.max(2, percentile)%` so a bottom-percentile image still shows a sliver;
  when `scores_stale`, the fill desaturates to `--fg-mute` at 0.5 opacity rather than
  earning a seventh badge — the flag cluster already carries that bit. No
  `pointerEvents: "none"`, or the `title` dies. `data-testid="style-meter"` +
  `data-style-percentile`.
- **`StyleMatchPanel`** (`frontend/src/components/image/StyleMatchPanel.tsx`) — replaces the
  bare `Style match 62%` row that used to sit in the detail page's flat scores grid, and
  mounts below that grid because it needs three rows, a meter and a thumbnail strip. The raw
  cosine stays visible throughout: this work makes the number readable, it does not hide it.
  Reference tiles hide themselves `onError` — references can be deleted after a run and
  `reference_image_ids` is deliberately not kept in sync, so a broken-image icon would be the
  *expected* state. `+N more` covers both overflow and the `REFERENCE_IDS_STORED_MAX`
  truncation. If the image being viewed was itself a reference its tile is ringed: a
  reference scores ~1.0 against its own centroid and would otherwise look like a
  suspiciously perfect match.
- **The gallery preference** is `GALLERY_STYLE_METER_KEY`, **on** by default and therefore
  read with `!== "false"` (the `CAPTION_DEFAULT_STRIP_REFS_KEY` precedent). It lives in
  `uiPrefsStore` rather than `useState` because a Settings pane and a gallery pane are
  routinely open side by side, and it gates `useStyleDistribution`'s `enabled` so a user with
  it off never fires the request.

#### `DinoLayerBreakdown` — the axis it used to lie about

`ImageDetailPage`'s per-layer bar chart normalised each bar to `maxScore` *within the image*,
so one bar always read 100% however poor the match — and since layers 1–8 compress every
image into 0.90–0.99, it rendered twelve near-full bars for a picture matching nothing. It
now uses a **fixed 0–1 axis** (`clamp(score) * 100`) with stated `0` / `1.0` end labels,
de-emphasises layers 1–9 (0.45 opacity, `--fg-mute` fill) with a caption explaining the
compression, keeps `--accent` for layers 10–12, and marks layer 12 `stored` — it is the one
written to `style_similarity_score`. Layers 1–9 are dimmed, never hidden: the numbers are
stored data and this is the only place to see them.

#### Invalidation

Both style-run call sites (`QualityPage`, `SelectionToolbar`) now run
`invalidateDatasetContentScope(qc, datasetId)` plus `["image"]` and
`["style-distribution", datasetId]`. This also fixed a pre-existing bug: the lone
`["images", datasetId]` they used to issue never invalidated `["score-values"]`, so the
Stats page's style histogram sat stale after every run.

#### The shared mode copy

`frontend/src/constants/styleModes.ts` (`STYLE_MODES`, `STYLE_MODE_NOTE`, `DINO_LAYER_NOTE`,
`styleModeLabel`) exists for the reason `aestheticModels.ts` states verbatim: the picker copy
was duplicated between `QualityPage` and `SelectionToolbar` and had already drifted. Both
now `.map()` over it. `styleModeLabel` also maps `dino_all_layers` / `combined_all_layers`,
which a descriptor can carry but the picker cannot select, and renders an unknown value
verbatim. The corrected copy states the gate's measurements rather than what the modes sound
like — the old wording ("CLIP for general images; DINOv2 for object-shape similarity")
implied DINOv2 was the upgrade.
