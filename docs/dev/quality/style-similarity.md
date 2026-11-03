# Style similarity — the modes and what they are worth

This file covers the CLIP/DINOv2 style-similarity flow: the embeddings a scoring run stores for it, `POST /quality/style-similarity` and its `embedding_type` modes, the three constants that decide what a run means, what each mode is measurably worth and why the default layer is 7, the models measured and not adopted, `GET /quality/embedding-coverage/{dataset_id}`, the three-valued layer picker, and the vectorized all-layers path. Making a stored score readable — the `StyleSimilarityRun` descriptor, `GET /quality/style-similarity/{dataset_id}`, the percentile and the rendered meters — is `docs/dev/quality/style-match.md`. The scorers that write the embedding columns are `docs/dev/quality/scoring.md`; the CLIP and DINOv2 models are loaded through the shared model manager (`docs/dev/ml-models.md`). Part of the Quality family; the hub, `docs/dev/quality.md`, routes from a code path to the file that owns it.

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
  the app scored on before this port — averaged **0.7166** mean AUC over six configurations
  and fell to **0.4066**, worse than a coin flip, on one of them. Reading layer 9 instead
  takes the same model to 0.8645, and `combined` at 0.30/0.70 on layer 9 to **0.9244** from
  the old blend's 0.8246. Layer 12 being worst replicated on every model and every dataset
  tested, including photographs, and is the most robust result of the campaign. The robust
  unit is the *band*, roughly layers 5–11, not any single index — which is why the default
  moved again, to 7, without any of these numbers changing. See § Which layer, and why it
  moved twice.
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

The full method, the leave-one-out validation and the open stages (the layer labels, dense
features) are in `docs/dev/style-embedding-plan/README.md` while that folder exists.

### Which layer, and why it moved twice

Both moves were measured on 2026-08-04: the final embedding → layer 9 on the sweep above,
then layer 9 → **layer 7** on a second pair of runs that asked a different question.

Every sweep before the second pair scored *separability* — can the signal tell in-style
candidates from out-of-style controls, with subject and framing free to vary however they
happen to. A layer can win that on content alone, because references and positives usually
share subject matter as well as a look. Two **crossed** runs hold one of the two fixed.
Both are `backend/scripts/style_crossed_probe.py`, the offline companion to
`style_gate.py`, and both read the DB read-only:

- **Synthetically crossed**: 30 base pictures × 7 restyles (grayscale, warm grade, contrast
  punch, and the three that change the rendering itself — posterised, painterly smear,
  pencil sketch) × 3 framings, asking whether a layer ranks *different picture, same
  rendering* above *same picture, restyled*.
- **Real pools**: three drawn styles that share one subject domain — two anime films and an
  SDXL set, all gothic vampire imagery — plus photographs, with subject then held fixed by
  CLIP zero-shot subject bins.

Both put the crossover at about layer 7. On the real pools, layer 9 ranks same-subject pairs
above same-style pairs (0.737 against 0.682), layers 5–6 do the reverse (0.674 against 0.707
at layer 5), and layer 7 sits near the balance point; layer 12 is furthest into subject on
both runs. That is what the picker copy means by suggesting 5–6 for a look and 9–10 for more
of the same scene.

Two things this does not claim. **The move is close to free, not a gain**: on the gate's own
published configuration layer 7 scores 0.9442 against layer 9's 0.9428, and on the
photographer test the blend gives up 0.011 — on a test that itself rewards subject matching,
since a photographer repeats subjects. And **with subject genuinely held fixed, every
configuration measured lands at 0.65–0.66 AUC**, so the layer choice is arguing over a small
part of a weak signal. The design that would separate style from subject by construction
rather than by binning is specified in the plan folder.

The later model runs (below) re-tested the layer-7 blend on five more sets — seven
illustrators, an anime set, photographs, paintings and 3D films — and it beat the old
default on every one. That is what [decision 6](../decisions/06-layer-7-default-and-no-new-style-model.md)
rests on.

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

## Other models were measured and not adopted

### DINOv3

Tested offline on 2026-11-02, in two pre-registered runs, with no app code changed. The full
write-up and raw tables are on branch `exp/style-media`, in docs/dev/roadmap/style-embedding.md
and the `dinov3-runs/` folder beside it.
- **Two anime styles.** DINOv3 (ViT-B/16 and ViT-L/16, 224 and 512 px) beat DINOv2 at the
  same relative depth. But part of that lead was a file confound: one style was all JPEG,
  the other all PNG.
- **Seven public-domain illustrators, as identical 1024 px JPEGs.** All four DINOv3 settings
  **tied** DINOv2 layer 7 (−0.003 to −0.010 mean AUC). Size and resolution made no difference.

**Why a newer model does not help here.** DINO models are trained to give the same answer for
a picture whatever its colour shift, blur or crop, so a stronger one ignores palette and
texture *more*. Those are much of what "style" means. DINOv3's headline gain is in its
per-patch features, which this flow never reads.

Two findings carry over regardless of model:
- **Reading DINOv2 at a middle layer (7) instead of its final embedding beat the old
  `combined` default** on both sets, by +0.085 to +0.223 AUC.
- **CLIP's value is set-dependent.** On the illustrators, a 30% CLIP blend beat every DINO
  layer alone by about 0.06. On the anime pair it did not.

### Kaloscope, CSD and PE-Core

Neither CLIP nor DINO was trained to see style, so on 2026-11-03 three models that might were
tested against `0.30 CLIP + v2 L7`, each in a pre-registered run: Kaloscope 3.0 (a DINOv3-B
fine-tune for Danbooru artist retrieval), CSD ViT-L (Contrastive Style Descriptors) and
PE-Core L14-336. IntroStyle and Gram matrices were dropped at the desk stage.

- **On drawn art they helped.** On a new set of seven Danbooru artists Kaloscope never trained
  on (`test4`, 204 images — a new set reusing the name of August's 24-image one), Kaloscope scored 0.999 against 0.832, and 0.906 against 0.791 on
  the illustrators (`test3`). CSD scored 0.888 and 0.842. PE-Core tied.
- **No candidate is general.** On photographs (five FSA photographers, greyscale), digital
  paintings (eight Danbooru painters, all known to Kaloscope) and 3D films (ten Blender open
  films), all three were "neutral outside drawn art" under the pre-registered rules: none hurt
  on a medium, and none helped on two. Kaloscope helped only on the paintings it was trained
  on, and its photo verdict flips to "hurts" without one photographer. CSD was slightly
  positive everywhere (+0.006 to +0.021) and never cleared the bar.

On this evidence the owner chose the layer-7 default and no new model
([decision 6](../decisions/06-layer-7-default-and-no-new-style-model.md)). Reopening it needs
a superseding entry, then an adoption plan: licence, its own embedding column and
discriminator, residency. The records are on branch `exp/style-media` (pushed, never merged),
in docs/dev/roadmap/style-model-runs/README.md and docs/dev/roadmap/style-media-runs/README.md,
with the pre-registrations, raw tables and an Opus review each.

**A side finding: the app builds CLIP with plain GELU**, while its weights expect QuickGELU. A
correct build scored about +0.015 higher on both drawn-art sets. Fixing it changes every
stored `clip_embedding` and the aesthetic scorer's input, so old and new vectors must never
mix; it is its own roadmap item, not part of the layer change.

### How to test the next candidate

What the runs taught, so the next one is not re-learned:

1. **Pre-register** fixed, named settings and a pass bar before downloading data. A best
   setting chosen on the same data is exploratory, never the verdict.
2. **At least five styles, about 30 images each, in one medium.** The two-style test
   overstated DINOv3 by about 0.07 AUC. Subjects crossed across styles are better still.
3. **Make the files identical.** Use one format, size and aspect, and check before scoring
   that no file property splits with the style label. Remove duplicate scans with a
   perceptual hash and by eye.
4. **Prove the harness** against the app's stored vectors (`--verify`, cosine ≥ 0.999).
   Cross-check one configuration against `style_gate.py`.
5. **Report the spread**: per-style AUCs, paired wins over reference draws, and the SD over
   draws. Gaps under 0.03 are ties.
6. **Get a second-opinion review** of the write-up against the raw tables before recording a
   verdict.

The harness on `main` is `dino_embed_offline.py`, `style_gate.py` and
`style_crossed_probe.py` in `backend/scripts/`. The sweep driver and the Wikimedia Commons
test-set builders (`backend/scripts/style_research/`) stay on `exp/style-media`, whose
`MANIFEST.json` lists each `test3` image's Commons page and licence so the set can be rebuilt.
A new candidate needs an extractor that writes the same `.npz` shape (`ids`, `filenames`,
`cls`, and optionally `layers`), after which the sweep compares it unchanged.

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

## All-layers scoring

**All-layers scoring is vectorized and RAM-bounded** (`dino_all_layers` / `combined_all_layers`): rather than re-slicing every reference blob per candidate per layer (the old `slice_layer_embedding` inner loops), the per-layer normalized mean reference is computed once via `_mean_layer_refs()` (stack refs → mean per layer → L2-normalize), and each candidate blob is decoded whole with `_decode_dino_layers()` (`np.frombuffer(...).reshape(12, 768)`); scoring is one matmul per layer (`cand[:, l, :] @ mean_refs[l]`), matching `compute_style_similarity`'s normalize-then-dot exactly. In combined mode the CLIP score doesn't depend on the layer, so it's computed once per chunk. Candidates are **keyset-paginated** (`WHERE Image.id > last ORDER BY Image.id LIMIT 2000`) through the shared local `_score_all_layers_paginated()` helper so the ~18 KB per-layer blobs are never all resident at once (~1.8 GB at 100k images). Output shape and rounding are byte-identical to the pre-vectorization loop (combined rounds the blended score to 4 decimals; both round each per-layer cosine to 4).
