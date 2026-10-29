# Style match — the run descriptor, the percentile and the meters

This file covers how a stored `style_similarity_score` is made readable: the `StyleSimilarityRun` descriptor, `GET /quality/style-similarity/{dataset_id}` and its quantiles, the client-side percentile contract in `frontend/src/utils/percentile.ts`, and the surfaces that render it — the gallery card meter, `StyleMatchPanel` and `DinoLayerBreakdown`. How the score is produced, and the gate measurements that motivate all of this, are `docs/dev/quality/style-similarity.md`. Part of the Quality family; the hub, `docs/dev/quality.md`, routes from a code path to the file that owns it.

## Making the score readable — the run descriptor and the percentile

The gate in `docs/dev/quality/style-similarity.md` § What the modes are actually worth is
also the argument for everything in this section: a stored
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
the card rather than hiding the feature. The predicate lives client-side in
`percentile.ts::isPartialScopeRun` and is two parts, **not** `scoped_image_count !== null`
alone: gallery *Select all matching filters* sends every image id, so a run covering
everything is recorded as scoped and the one-part test put a caveat about nothing on every
image. The second part is `run.scored_count < distribution.scored` — the run wrote fewer
scores than the dataset now carries, so something older survives to mix with. It is a
freshness test over the current payload rather than a server-computed boolean for the same
reason the count is not one: it re-evaluates on every read and so self-corrects as images
come and go, where a stored flag would go stale the next time one is deleted.

### `GET /quality/style-similarity/{dataset_id}`

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

### The client-side percentile contract

`frontend/src/utils/percentile.ts` is pure and has three call sites. There is **no frontend
unit-test runner in this repo**, so every degenerate input is handled defensively there and
the shapes that produce them are generated from the backend side by
`backend/tests/test_style_distribution_http.py` (one scored image → 21 identical
breakpoints; three scores → repeated values). `frontend/e2e/percentile.spec.ts` feeds the
backend's tied shape through the real module: a node-only Playwright spec that imports
`../src/utils/percentile` and never opens a page, so the module must stay free of anything
but type imports and plain constant modules.

| Case | `percentileOf` returns |
|---|---|
| unscored image, or quantiles not loaded (`length < 2`) | `null` → render nothing. A zero-length bar reads as "0th percentile", not "not measured" |
| all scores identical, including `scored === 1` | `null` → fall back to the raw cosine, no meter |
| score equal to an interior run of repeated breakpoints (fewer scored images than breakpoints) | the run's **low edge**: equality is tested first, against the first breakpoint *at or above* the score. Over `[0, 0.5, 1]`, 0.5 fills q30–q70 and reads 30. Walking to the first breakpoint *above* the score lands on the high edge (70), which is what the code did until GPT-019 |
| score strictly between two breakpoints | linear interpolation between them; the span is never zero there |
| score at or below q0 / at or above q100 | clamp to 0 / 100. The clamp wins over the low-edge rule, so the dataset's maximum reads 100 even when it fills a flat run at the top |

`styleMatchTitle({percentile, score, run, stale})` is one shared tooltip builder so the card
and the detail block cannot drift on the caveats (mode, reference count, mixed scope, stale
pixels).

### The rendered surfaces

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
  *expected* state. `+N more` counts against the tiles **actually rendered**, not against the
  pre-filter slice, so a hidden tile moves into the chip instead of disappearing from both;
  it covers overflow past the strip and the `REFERENCE_IDS_STORED_MAX` truncation alike. If the image being viewed was itself a reference its tile is ringed: a
  reference scores ~1.0 against its own centroid and would otherwise look like a
  suspiciously perfect match.
- **The gallery preference** is `GALLERY_STYLE_METER_KEY`, **on** by default and therefore
  read with `!== "false"` (the `CAPTION_DEFAULT_STRIP_REFS_KEY` precedent). It lives in
  `uiPrefsStore` rather than `useState` because a Settings pane and a gallery pane are
  routinely open side by side, and it gates `useStyleDistribution`'s `enabled` so a user with
  it off never fires the request.

### `DinoLayerBreakdown` — the axis it used to lie about

`ImageDetailPage`'s per-layer bar chart normalised each bar to `maxScore` *within the image*,
so one bar always read 100% however poor the match — and since layers 1–8 compress every
image into 0.90–0.99, it rendered twelve near-full bars for a picture matching nothing. It
now uses a **fixed 0–1 axis** (`clamp(score) * 100`) with stated `0` / `1.0` end labels,
de-emphasises layers 1–9 (0.45 opacity, `--fg-mute` fill) with a caption explaining the
compression, keeps `--accent` for layers 10–12, and marks layer 12 `stored` — it is the one
written to `style_similarity_score`. Layers 1–9 are dimmed, never hidden: the numbers are
stored data and this is the only place to see them.

### Invalidation

Both style-run call sites (`QualityPage`, `SelectionToolbar`) run
`invalidateDatasetContentScope(qc, datasetId)` plus `["image"]`. This also fixed a
pre-existing bug: the lone `["images", datasetId]` they used to issue never invalidated
`["score-values"]`, so the Stats page's style histogram sat stale after every run.
`["style-distribution", datasetId]` is **inside** that shared scope rather than a line each
call site remembers: the payload is an aggregate over the image rows, so a delete or an
import moves every card's percentile too, and a third style-run caller would otherwise have
to know about the extra line.

### The shared mode copy

`frontend/src/constants/styleModes.ts` (`STYLE_MODES`, `STYLE_MODE_NOTE`, `DINO_LAYER_NOTE`,
`styleModeLabel`) exists for the reason `aestheticModels.ts` states verbatim: the picker copy
was duplicated between `QualityPage` and `SelectionToolbar` and had already drifted. Both
now `.map()` over it. `styleModeLabel` also maps `dino_all_layers` / `combined_all_layers`,
which a descriptor can carry but the picker cannot select, and renders an unknown value
verbatim. The corrected copy states the gate's measurements rather than what the modes sound
like — the old wording ("CLIP for general images; DINOv2 for object-shape similarity")
implied DINOv2 was the upgrade.
