# Style similarity — the modes, the constants and the blend

This file covers the CLIP/DINOv2 style-similarity flow: the embeddings a scoring run stores for it, `POST /quality/style-similarity` and its `embedding_type` modes, the three constants that decide what a run means, where the blend lives, `GET /quality/embedding-coverage/{dataset_id}`, the three-valued layer picker, and the vectorized all-layers path. What each mode is measurably worth, why the default layer is 7 and the models measured and not adopted are `docs/dev/quality/style-evaluation.md`. Making a stored score readable — the `StyleSimilarityRun` descriptor, `GET /quality/style-similarity/{dataset_id}`, the percentile and the rendered meters — is `docs/dev/quality/style-match.md`. The scorers that write the embedding columns are `docs/dev/quality/scoring.md`; the CLIP and DINOv2 models are loaded through the shared model manager (`docs/dev/ml-models.md`). Part of the Quality family; the hub, `docs/dev/quality.md`, routes from a code path to the file that owns it.

## Style similarity

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

Local reference files can be embedded on-the-fly via `POST /quality/embed-references` (multipart upload → returns base64 CLIP embeddings). External refs are CLIP-only; `"combined"`, `"dino"`, and `"dino_all_layers"` / `"combined_all_layers"` modes require dataset images as references. Both pickers enforce that by forcing `clip` whenever local files are present; the API does not — a `dino` request with `reference_embeddings` and `dino_layer: null` scores CLIP vectors as if they were DINOv2 ones, and with a numbered layer `slice_layer_embedding` raises an uncaught `ValueError` (a 500). No job queue — all similarity computation is CPU-only numpy and runs synchronously in the request. `StyleSimilarityRequest` accepts an optional `image_ids: list[str] | None` field; when set, only those images are scored (candidate queries in all embedding-type branches are filtered accordingly). `QualityPage` omits `image_ids` (scores the whole dataset); `SelectionToolbar` passes the current selection.

**Getting the reference ids out of the picker.** `StyleReferencePicker`'s selection is React state on `QualityPage` (`selectedRefIds`) and the ids are never rendered, so nothing outside the page can consume the chosen set. The Action row's secondary **Copy reference IDs** button writes `Array.from(selectedRefIds).join(",")` to the clipboard (via `utils/clipboard.ts::copyText`) for that reason — it exists to feed offline harnesses such as `backend/scripts/style_gate.py`, and covers dataset references only, since dragged-in local files are embedded on the fly and have no `Image.id`.

`style_similarity_score` is the one score column a re-scoring run cannot refresh, and so the sole member of `_UNREFRESHABLE_SCORE_COLUMNS` — see `docs/dev/quality/scores-stale.md` § The clear predicate for why that is a boundary rather than an oversight.

## Where the blend lives

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


## `GET /quality/embedding-coverage/{dataset_id}`

Style similarity is the one workflow whose prerequisite is *another run*, and every branch
answers 400 when the column it reads is empty — so until this existed the only way to find
out was to press the button. One query, four counts (`clip_embedding`, `dino_embedding`,
`dino_layer_embeddings`, total), with an optional `subfolder`. `QualityPage` asks
**without** one, although its other panels follow the page's subfolder scope: the style run
scores the whole dataset, so a subfolder count would disable a run that can succeed, or stay
quiet about images it will skip. `func.count`
over a `deferred=True` LargeBinary is a column expression evaluated in SQLite, so counting
the 18 KB per-layer blobs loads none of them.

A dedicated endpoint for the same reason as its neighbour `aesthetic_coverage`, whose
docstring carries the argument: it needs a refetch on every finished scoring job, and pulling the whole `get_dataset_stats` aggregation onto the Score
page for four counts is the expensive way round. Deliberately **not** a field on
`GET /style-similarity/{id}` — that one is on the gallery render path via `ImageCard`, and
this is a Score-page question.

`describeEmbeddingGap` in `styleModes.ts` turns the counts into a sentence. For the
two-column `combined` modes it takes the **minimum** of the two counts, an upper bound on
the rows carrying both rather than the exact figure: exact where it has to be (`covered === 0`
iff the intersection is empty, which is the case that disables *Score similarity*, a
guaranteed 400) and approximate only in the advisory partial warning. A partial gap warns
and does not block — a scoped run over the covered images is legitimate, and the response
already reports `skipped`. `SelectionToolbar` does no coverage check, so a selection with no
embeddings still gets the 400. `"embedding-coverage"` is in `DATASET_CONTENT_SCOPE`
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
which does not carry the layer. `SelectionToolbar` persists nothing, so it starts at
`DEFAULT_DINO_LAYER` every time.

Ticking **DINOv2 embeddings** auto-ticks **per-layer embeds**, on the false→true edge only
(a `prevRunDino` ref), because every layer except "final" reads a column the plain DINOv2
run does not write. Auto-ticked rather than defaulted on, so nobody who ignores style
similarity pays for a 1.2 GB model, and unticking it afterwards sticks.

## Paged and all-layers scoring

**All-layers scoring is vectorized and RAM-bounded** (`dino_all_layers` / `combined_all_layers`): rather than re-slicing every reference blob per candidate per layer (the old `slice_layer_embedding` inner loops), the per-layer normalized mean reference is computed once via `_mean_layer_refs()` (stack refs → mean per layer → L2-normalize), and each candidate blob is decoded whole with `_decode_dino_layers()` (`np.frombuffer(...).reshape(12, 768)`); scoring is one matmul per layer (`cand[:, l, :] @ mean_refs[l]`), matching `compute_style_similarity`'s normalize-then-dot exactly. In combined mode the CLIP score doesn't depend on the layer, so it's computed once per chunk. Candidates are **keyset-paginated** (`WHERE Image.id > last ORDER BY Image.id LIMIT _STYLE_PAGE_SIZE`, 2,000) through the shared local `_score_paginated()` helper so the ~18 KB per-layer blobs are never all resident at once (~1.8 GB at 100k images). Output shape and rounding are byte-identical to the pre-vectorization loop (combined rounds the blended score to 4 decimals; both round each per-layer cosine to 4).

**Every branch pages, not only these two** (M9 stage 5). `clip`, `dino` on the final embedding, `dino` at a layer and `combined` in both forms go through the same `_score_paginated()`, each with a small `score_chunk` that scores one page. The scan runs inside the HTTP request, so a branch that selects its candidates whole holds the dataset's blobs at once; `dino` at a layer did exactly that with the 18 KB blob until stage 5. Paging moves a score by at most one step in its 4th decimal: each candidate is scored against the reference mean on its own, but a float32 matrix product's last bits depend on the batch's shape and row order, so a value on a rounding boundary can flip. The whole-set SELECT before it had no ORDER BY, so it had the same wobble. A hand check on 804 real images found 1 flip in 8,576 scores at the default page size. The layer is sliced in Python with `slice_layer_embedding` rather than with SQL `substr`, which would skip its length check and still walk the row's overflow chain. `backend/tests/test_style_similarity_paging.py` shrinks the page to 2 and checks, per mode, that every candidate SELECT has a `LIMIT`, that no scorer call sees more than one page, that no UPDATE runs before the last page is read, and that every score matches one computed straight from the seeded blobs.

**When the UPDATEs run decides how long SQLite's one write lock is held.** The first UPDATE takes it until the commit, and other writers (a scoring job's chunk commit, a caption save) wait the driver's default 5 s and then fail with "database is locked". The commit comes once, at the end, so a run that raises leaves the column as it was, which the route's success-only descriptor write relies on. The four one-score branches pass `defer_write=True`: they keep only `(id, score)` per row while scanning and write everything after the last page, so the lock is held for the writes alone, as it was when they selected their candidates whole. The two `*_all_layers` branches write page by page, because a 12-entry `dino_layer_scores` dict per row is too big to hold for a whole dataset. They hold the lock for the whole scan, and always have.
