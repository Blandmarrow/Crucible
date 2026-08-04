# Style similarity

This file covers scoring an image against a set of *reference* images — the CLIP/DINOv2 cosine flow behind `POST /quality/style-similarity`, the five `embedding_type` modes and what each is measurably worth, the `StyleSimilarityRun` descriptor that says which mode wrote the column, the percentile contract that makes a raw cosine readable, and the three surfaces that render it. Its sibling `docs/dev/image-similarity.md` covers the other between-images comparison, pHash duplicate grouping, which shares nothing with this but the word *similarity*. The per-image scores this leans on — `clip_embedding`, `dino_embedding`, `dino_layer_embeddings` and the scoring run that writes them — are `docs/dev/scoring.md`; the CLIP and DINOv2 models themselves load and evict through the shared model manager (`docs/dev/ml-models.md`). The user-facing half is `docs/scoring.md` § Style similarity.

**Style similarity flow**: (1) run scoring with the desired embedding flags — `run_embeddings=True` stores `clip_embedding`; `run_dino=True` stores `dino_embedding` (independent of `run_embeddings`); `run_dino_layers=True` (requires `run_dino=True`) stores `dino_layer_embeddings`. (2) call `POST /quality/style-similarity` with `reference_image_ids` and/or `reference_embeddings` (base64 float16 bytes, CLIP-only). The `embedding_type` field selects the scoring mode:

| `embedding_type` | `dino_layer` | Column(s) written | Description |
|---|---|---|---|
| `"clip"` | — | `style_similarity_score` | Cosine similarity of CLIP embeddings |
| `"dino"` | `null` | `style_similarity_score` | Cosine similarity of the post-layernorm `dino_embedding` |
| `"dino"` | 1–12 | `style_similarity_score` | Cosine similarity using a specific DINOv2 transformer layer (from `dino_layer_embeddings`) |
| `"combined"` | `null` | `style_similarity_score` | `0.30 × clip_sim + 0.70 × dino_sim` (post-layernorm embedding) |
| `"combined"` | 1–12 | `style_similarity_score` | `0.30 × clip_sim + 0.70 × dino_layer_sim` (specific layer) |
| `"dino_all_layers"` | — | `dino_layer_scores` (JSON) + `style_similarity_score` | Scores each of the 12 DINOv2 layers independently; writes `{"1": score, …, "12": score}` and sets `style_similarity_score` to `DEFAULT_DINO_LAYER`'s value |
| `"combined_all_layers"` | — | `dino_layer_scores` (JSON) + `style_similarity_score` | Blended score (0.30 CLIP + 0.70 DINOv2) for each of the 12 layers; writes `{"1": score, …, "12": score}` and sets `style_similarity_score` to `DEFAULT_DINO_LAYER`'s value |

**`dino_layer: null` is a sentinel, not "unspecified".** It selects the `dino_embedding`
column — DINOv2's **post**-layernorm CLS token — while `dino_layer_embeddings`' layer 12 is
`hidden_states[12]`, **pre**-layernorm. They are different vectors and they score
differently (0.9417 vs 0.9611 on one sweep). `StyleSimilarityRequest.dino_layer` therefore
keeps its `None` pydantic default however the picker's default moves: defaulting it to
`DEFAULT_DINO_LAYER` would silently repoint every existing API client, `style_gate.py`
among them, onto a column it never asked for. The picker's default lives in the frontend.

**The three numbers that decide what a run means** — the layer, and the two blend weights —
live in `backend/ml/similarity_scorer.py` as `DEFAULT_DINO_LAYER`, `STYLE_CLIP_WEIGHT` and
`STYLE_DINO_WEIGHT`, and are the single source for both implementations of the blend (see
§ Where the blend lives). `frontend/src/constants/styleModes.ts` carries its own copy of the
layer — it must, since a picker cannot read Python — and
`backend/tests/test_similarity_scorer.py` reads that file and asserts the two agree, because
nothing at runtime couples them and a drift's only symptom is a number quietly disagreeing
with itself.

Local reference files can be embedded on-the-fly via `POST /quality/embed-references` (multipart upload → returns base64 CLIP embeddings). External refs are CLIP-only; `"combined"`, `"dino"`, and `"dino_all_layers"` / `"combined_all_layers"` modes require dataset images as references. No job queue — all similarity computation is CPU-only numpy and runs synchronously in the request. `StyleSimilarityRequest` accepts an optional `image_ids: list[str] | None` field; when set, only those images are scored (candidate queries in all embedding-type branches are filtered accordingly). `QualityPage` omits `image_ids` (scores the whole dataset); `SelectionToolbar` passes the current selection.

**Getting the reference ids out of the picker.** `StyleReferencePicker`'s selection is React state on `QualityPage` (`selectedRefIds`) and the ids are never rendered, so nothing outside the page can consume the chosen set. The Action row's secondary **Copy reference IDs** button writes `Array.from(selectedRefIds).join(",")` to the clipboard for that reason — it exists to feed offline harnesses such as `backend/scripts/style_gate.py`, and covers dataset references only, since dragged-in local files are embedded on the fly and have no `Image.id`.

## What the modes are actually worth

Two measurement campaigns, and the second revised the first. `backend/scripts/style_gate.py`
is a read-only offline harness that calls these same production functions; its report
(`backend/scripts/style_gate_report.md`) is the 2026-08-02 run, against 98 frames of one
animated film scored from eight references of one lighting cluster with 20 out-of-style
controls mixed in. Its conclusion stands and is the reason no learned head is planned for
style: **the centroid baseline separates well enough** (AUC 0.94–0.97, no control in any
mode's top 20).

What did not stand is anything read off that *single reference configuration*. A 2026-08-04
sweep of twelve configurations across illustration, animation and photography rewrote three
of its recommendations, and the shape of the correction is worth more than any of the
numbers:

- **The last layer is the weakest layer.** DINOv2's post-layernorm `dino_embedding` — what
  the app scored on until 2026-08-04 — averaged **0.7166** mean AUC over six configurations
  and fell to **0.4066**, worse than a coin flip, on one of them. Reading layer 9 instead
  takes the same model to 0.8645, and `combined` at 0.30/0.70 on layer 9 to **0.9244** from
  the old blend's 0.8246. Layer 12 being worst replicated on every model and every dataset
  tested, including photographs, and is the most robust result of the campaign. The robust
  unit is the *band*, roughly layers 5–11, not any single index.
- **No single AUC is a property of a mode.** CLIP scored 0.9733 on the gate's reference
  cluster and **0.6144** on an opposing cluster drawn from *the same 118 images*, and 0.7399
  on photographs. That is why `styleModes.ts` no longer quotes a figure in any mode
  description — the rule is stated in that file's module doc, because the copy had already
  been taken back twice.
- **"Layers 1–8 cannot be thresholded" was the wrong conclusion from a real observation.**
  The compression is real (every image lands in 0.90–0.99 on DINOv2's low layers) but it
  does not follow that the band is unusable: a narrow range with clean ordering thresholds
  fine, and a mid-layer configuration with a range of 0.129 reached 0.9909 best-threshold
  accuracy. Range and separability are separate properties that coincided on one dataset.
- **`combined` is worth more than either half, and is not just DINOv2 with a tilt.** The
  gate's ρ 0.9844 against `dino` was measured at the old weights on the final embedding.
  CLIP and the DINO family remain the only pair that genuinely disagree (ρ 0.7525), and at a
  mid layer the blend beats both components alone.

**What to promise.** ~0.98 AUC is the ceiling for sorting *across* media — anime screencaps
from painterly illustration — and **~0.85 is the realistic figure for style matching inside
one medium**. The photograph test (six photographers, style label = the photographer) was
pre-registered at ~0.93 and failed at 0.8220 for the shipped configuration. Every signal
dropped there, including the old one, so it is a property of the task rather than of any
model: telling six photographers apart is a far finer discrimination than telling two media
apart. UI copy promising more is promising the easy case.

The full method, the leave-one-out validation and the untested corners (DINOv3, dense
features) are in `docs/dev/roadmap/style-embedding.md` while that file exists.

### Where the blend lives

The weights have **two** implementations and always did: `compute_combined_similarity` in
`backend/ml/similarity_scorer.py`, and the per-layer loop in `routers/quality.py`, which
hoists the CLIP score out of the layer loop for speed and so cannot call it. Both now route
their arithmetic tail through `blend_scores` — the embeddings seam is not where the
duplication lived — and `backend/tests/test_style_similarity_run_http.py` asserts the two
agree end to end.

`slice_layer_embedding` and `_LAYER_BLOB_SIZE` live in `similarity_scorer` rather than
`dino_scorer`, and `similarity_scorer` **must stay numpy-only** (a structural AST test pins
it). `dino_scorer` is `import torch` at module scope, and while the slicer lived there the
quality router's combined branch imported torch on every `combined` request — so in CI that
branch was an ImportError → 500, and the weights, the branch with the most arithmetic in
it, had no coverage of any kind. `dino_scorer` re-exports both names for existing callers.

`style_similarity_score` is the one score column a re-scoring run cannot refresh, and so the sole member of `_UNREFRESHABLE_SCORE_COLUMNS` — see `docs/dev/scores-stale.md` § The clear predicate for why that is a boundary rather than an oversight.

**All-layers scoring is vectorized and RAM-bounded** (`dino_all_layers` / `combined_all_layers`): rather than re-slicing every reference blob per candidate per layer (the old `slice_layer_embedding` inner loops), the per-layer normalized mean reference is computed once via `_mean_layer_refs()` (stack refs → mean per layer → L2-normalize), and each candidate blob is decoded whole with `_decode_dino_layers()` (`np.frombuffer(...).reshape(12, 768)`); scoring is one matmul per layer (`cand[:, l, :] @ mean_refs[l]`), matching `compute_style_similarity`'s normalize-then-dot exactly. In combined mode the CLIP score doesn't depend on the layer, so it's computed once per chunk. Candidates are **keyset-paginated** (`WHERE Image.id > last ORDER BY Image.id LIMIT 2000`) through the shared local `_score_all_layers_paginated()` helper so the ~18 KB per-layer blobs are never all resident at once (~1.8 GB at 100k images). Output shape and rounding are byte-identical to the pre-vectorization loop (combined rounds the blended score to 4 decimals; both round each per-layer cosine to 4).

## Making the score readable — the run descriptor and the percentile

The gate above is also the argument for everything in this section: a stored
`style_similarity_score` is a raw cosine, and nothing about it says which of the five modes,
which layer or which references produced it. The same "0.62" is a mediocre CLIP match, a
strong DINOv2 one, or meaningless on a layer-3 run where every image lands in 0.90–0.99.

**`StyleSimilarityRun` (`backend/models/style_run.py`) is the missing descriptor.** One row
per dataset, `dataset_id` unique, overwritten by every successful run — it describes the
values *currently* in the column, not a history. It holds the mode, the layer, the blend
weights, up to `REFERENCE_IDS_STORED_MAX = 64` reference ids, the true reference counts, the
scored/skipped counts and `scoped_image_count`.

**Every recorded field comes from the run's `result` dict, not from `body`.** Each of the
six success returns carries `dino_layer` / `clip_weight` / `dino_weight` alongside `updated`
and `skipped`, and `_record_style_run` reads them from there — the `aesthetic_marker`
pattern in `score_quality`, and for the same reason: the recorded value and the computation
become one decision rather than two reads that can drift. It is not cosmetic here.
`dino_layer` means **"the layer whose value is in `style_similarity_score`"**, and an
`*_all_layers` request carries no layer at all while its stored headline comes from
`DEFAULT_DINO_LAYER`; reading `body.dino_layer` would record `null` for a run that did pick
a layer.

**`clip_weight`/`dino_weight` (migration `c2b8e4f6a1d7`) are nullable, and NULL carries two
meanings** — "predates the columns" and "this mode does not blend". Safe rather than sloppy
because `embedding_type` sits in the same row and disambiguates: NULL on a `clip` row is the
only correct value, NULL on a `combined` row is a pre-migration run. No backfill, for the
same reason as the table itself — a `combined` row already present *was* scored at
0.38/0.62, but writing that in claims a record where there is only an inference. The
migration deliberately shipped **one commit before** the retune, so no run exists whose
weights are unrecorded and a bisect cannot land between the two.

Three decisions worth not re-deriving:

- **A table, not columns on `datasets`.** `Dataset.updated_at` is half the
  `get_dataset_stats` cache validator, so a descriptor written onto that row would evict the
  whole Stats aggregation on every style run; `DatasetOut` is also hand-built field by field
  in `list_datasets`, the trap `CLAUDE.md` names for `video_count`. The `VersionImageState`
  mirroring rule does **not** reach it: that guard derives its universe from
  `Image.__table__.columns`, and this is a new table describing dataset-level state — which
  also holds for a column added to it later, so the weights needed no mirror and no
  `NOT_MIRRORED` entry.
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

## `GET /quality/style-similarity/{dataset_id}`

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

## `GET /quality/embedding-coverage/{dataset_id}`

Style similarity is the one workflow whose prerequisite is *another run*, and every branch
answers 400 when the column it reads is empty — so until this existed the only way to find
out was to press the button. One query, four counts (`clip_embedding`, `dino_embedding`,
`dino_layer_embeddings`, total) within the Score page's own `subfolder` scope. `func.count`
over a `deferred=True` LargeBinary is a column expression evaluated in SQLite, so counting
the 18 KB per-layer blobs loads none of them.

A dedicated endpoint for the same reason as its neighbour `aesthetic_coverage`, whose
docstring carries the argument: it needs the page's subfolder scope and a refetch on every
finished scoring job, and pulling the whole `get_dataset_stats` aggregation onto the Score
page for four counts is the expensive way round. Deliberately **not** a field on
`GET /style-similarity/{id}` — that one is on the gallery render path via `ImageCard`, and
this is a Score-page question.

`describeEmbeddingGap` in `styleModes.ts` turns the counts into a sentence. For the
two-column `combined` modes it takes the **minimum** of the two counts, an upper bound on
the rows carrying both rather than the exact figure: exact where it has to be (`covered === 0`
iff the intersection is empty, which is the case that disables *Score similarity*, a
guaranteed 400) and approximate only in the advisory partial warning. A partial gap warns
and does not block — a scoped run over the covered images is legitimate, and the response
already reports `skipped`. `"embedding-coverage"` is in `DATASET_CONTENT_SCOPE`
(`constants/queryKeys.ts`), or the warning outlives the very run that fixes it.

## The layer picker has three values, not two

`DinoLayerChoice` is `number | "final" | "all"`. Both pickers used to normalise "Layer 12" to
`null`, which the backend reads as the post-layernorm `dino_embedding` — so the app could
never score DINOv2's actual layer 12, and the picker was quietly lying about which vector it
had chosen. "Final embedding" is now its own option; `1..12` sends `dino_layer`; `"all"`
selects the corresponding `*_all_layers` mode. The backend needed no change.

`normalizeDinoLayer` is applied at the load boundary because `loadPersisted` **shallow-merges**
onto the defaults: a `QUALITY_WORKFLOW` blob written before this type existed carries
`dinoLayer: null` straight through the merge and would render an empty `<select>`. `null`
normalises to `"final"` — exactly what that stored value used to score — and anything
unrecognised to `DEFAULT_DINO_LAYER`. `QualityPage`'s only load boundary for it is the
`useState` initialiser; the pane-mode dataset-change reload re-reads the *filters* blob,
which does not carry the layer.

Ticking **DINOv2 embeddings** auto-ticks **per-layer embeds**, on the false→true edge only
(a `prevRunDino` ref), because every layer except "final" reads a column the plain DINOv2
run does not write. Auto-ticked rather than defaulted on, so nobody who ignores style
similarity pays for a 1.2 GB model, and unticking it afterwards sticks.

## The client-side percentile contract

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

## The rendered surfaces

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

## `DinoLayerBreakdown` — the axis it used to lie about

`ImageDetailPage`'s per-layer bar chart normalised each bar to `maxScore` *within the image*,
so one bar always read 100% however poor the match — and since the low layers compress every
image into a narrow band, it rendered twelve near-full bars for a picture matching nothing.
It uses a **fixed 0–1 axis** (`clamp(score) * 100`) with stated `0` / `1.0` end labels.

**No layer is dimmed, and that is a correction.** An earlier version greyed out everything
below layer 10 on the strength of the 0.90–0.99 compression; the wider sweep found the
middle of the stack separates *best*, so the panel would have been arguing against the
layer the app now scores on. Range is not separability.

The `stored` badge takes a **`storedLayer` prop** from `run.dino_layer` rather than
hardcoding `12`. Falling back to `DEFAULT_DINO_LAYER` when the descriptor is missing would
lie about an old row whose headline genuinely was layer 12; null renders no badge at all,
matching `StyleMatchPanel`'s "the run details were not recorded" pattern.

## Invalidation

Both style-run call sites (`QualityPage`, `SelectionToolbar`) run
`invalidateDatasetContentScope(qc, datasetId)` plus `["image"]`. This also fixed a
pre-existing bug: the lone `["images", datasetId]` they used to issue never invalidated
`["score-values"]`, so the Stats page's style histogram sat stale after every run.
`["style-distribution", datasetId]` is **inside** that shared scope rather than a line each
call site remembers: the payload is an aggregate over the image rows, so a delete or an
import moves every card's percentile too, and a third style-run caller would otherwise have
to know about the extra line.

## The shared mode copy

`frontend/src/constants/styleModes.ts` (`STYLE_MODES`, `STYLE_MODE_NOTE`, `DINO_LAYER_NOTE`,
`styleModeLabel`) exists for the reason `aestheticModels.ts` states verbatim: the picker copy
was duplicated between `QualityPage` and `SelectionToolbar` and had already drifted. Both
now `.map()` over it. `styleModeLabel` also maps `dino_all_layers` / `combined_all_layers`,
which a descriptor can carry but the picker cannot select, and renders an unknown value
verbatim. The corrected copy states the gate's measurements rather than what the modes sound
like — the old wording ("CLIP for general images; DINOv2 for object-shape similarity")
implied DINOv2 was the upgrade.
