# Roadmap — the style-similarity embedding: which model, which layer, which blend

Measured on 2026-08-04 against `test3` (118 images), `test4` (24) and `test5` (90 real
photographs), then again the same day (**2026-08-04b**) against a crossed set built from
those plus `test2` and `data/final` — see § The crossed runs. Stages 1 and 2a have shipped
and are deleted per the lifecycle below; everything still described as a stage is
unimplemented. This file supersedes `roadmap.md` § 5 (DINOv3),
which deferred the question on the assumption that adopting DINOv3 meant swapping the
backbone; the measurements say the backbone is the least interesting half of the change.

**The photograph test was run and its pre-registered acceptance criterion FAILED** — see
§ The photograph test. The direction of every finding replicated; the *magnitude* did not.
Read that section before acting on any number above it, because the headline figures in the
next few sections are from illustration and animation only.

**Lifecycle**: transient, same as its sibling `roadmap.md`. When a stage lands, move its
durable rationale into `docs/dev/style-similarity.md` and delete the stage from here; delete
the file when everything is done. It sits in this subfolder deliberately —
`scripts/check_docs.py` globs `docs/dev/*.md` non-recursively, so files here carry no
Documentation Map row and no word budget.

Read `docs/dev/style-similarity.md` and `backend/scripts/style_gate_report.md` first. The
published numbers in both remain correct for the configuration they were measured in; what
follows shows that configuration was narrower than it looked.

## The one-sentence version

The app ranks style with the *worst* signal measured, because it reads DINOv2's final
embedding — the last layer is the weakest layer of every model tested, on every dataset
tested. Reading a **middle** layer instead, from **DINOv3**, blended 20/80 with CLIP, takes
mean AUC from **0.7904 to 0.9065** across twelve reference configurations spanning
illustration, animation and photography, and the plumbing for a CLIP-plus-arbitrary-layer
blend already exists in the app. The gain is much larger on cross-medium sorting (0.82 →
0.96) than on style matching within photography (0.76 → 0.85), and that gap is the honest
description of the feature.

## What was measured

Six reference configurations across two datasets, each scoring "do the in-style candidates
outrank the out-of-style controls" as AUC:

| Dataset | Configurations | Character |
|---|---|---|
| `test3` (118) | 2 — the published lava-lit reference cluster, plus an opposing cluster of the 8 frames least like it | One film, one visual domain, 20 painted-illustration controls |
| `test4` (24) | 4 — anime screencaps, painterly nature, painterly creatures, gothic interiors | Deliberately varied; groups assigned by *rendering style*, not subject |

Signals compared: CLIP ViT-L-14 (the shipped default), DINOv2-base, and DINOv3 ViT-B/16 at
224 px and 512 px — each at its final embedding and at all 12 transformer layers, then every
layer × weight combination of a CLIP blend.

### Trusting the harness

Two things make these numbers about the models rather than about the harness.

`backend/scripts/dino_embed_offline.py` re-extracts embeddings offline and its `--verify`
flag cosine-checks them against the column the app already stores. Against
`facebook/dinov2-base` it returns **mean cos 1.000000** over all 118 images, and against the
`open_clip` CLIP path likewise — so the extractor reproduces `dino_scorer` and
`aesthetic_scorer` bit for bit, and a later disagreement is the checkpoint.

`backend/scripts/style_gate.py` reproduces its own published separation table exactly
(AUC 0.9733 / 0.9417 / 0.9650, same thresholds and accuracies). Feeding DINOv2 back through
the *new* offline path as an extra mode scores identically to the DB's own (ρ 1.0, 20/20 top
and bottom shared), so the extra-embeddings path adds no distortion.

Both scripts open `dataset_manager.db` read-only over a `file:…?mode=ro` URI. **No embedding
was written to the database**; `test4`'s embedding columns are still empty.

## Findings

### 1. The shipped `dino` mode is the weakest signal measured

DINOv2's final embedding — what `Image.dino_embedding` holds and what the `DINOv2` mode
ranks on — averages **0.7166** across the six configurations and drops to **0.4066**, worse
than a coin flip, on `test4`'s painterly-creature references. Concretely: given four
painterly nature references it placed a photo-real portrait of an old woman, a control, at
rank 3.

It is not uniformly bad. It is *good at fine discrimination inside one visual domain*
(0.9417 and 0.9803 on the two `test3` configurations, both film frames) and falls apart
across varied content. Crucible's datasets are arbitrary, so the varied case is the one that
describes real use.

### 2. The layer matters more than the model

Every model tested is substantially better read from a middle layer than from its end. Mean
AUC over the six configurations, using one **fixed** layer chosen once and applied
everywhere — not a per-configuration best, so no post-hoc selection inflates it:

| Signal | Mean AUC | Worst configuration |
|---|---|---|
| DINOv3 @224, layer 7 | **0.9427** | 0.7692 |
| DINOv3 @512, layer 11 | 0.9338 | **0.8583** |
| CLIP (final) | 0.8730 | 0.6144 |
| DINOv2, layer 9 | 0.8645 | 0.6099 |
| DINOv3 @224, final | 0.8568 | 0.6917 |
| DINOv3 @512, final | 0.8537 | 0.6333 |
| DINOv2, final — **the shipped mode** | 0.7166 | 0.4066 |

DINOv2 gains 0.15 from the move (0.7166 → 0.8645). DINOv3 gains less because it starts
higher, and lands higher still. Layer 12 is DINOv3's *worst* region, which inverts DINOv2's
profile — Gram anchoring, DINOv3's headline training change, exists precisely to stop
mid-stack features degrading late in training.

The optimum shifts with resolution and dataset (7 at 224 px, 11 at 512 px, 6 in the blend
below), so **the robust unit is the band, roughly layers 5–11, not any single index.**

### 3. This revises a published claim

`docs/dev/style-similarity.md` and `styleModes.ts` both state that per-layer scoring below
layer ~10 cannot be thresholded, because layers 1–8 compress every image into 0.90–0.99.
That is true of DINOv2 and does not generalise. DINOv3 @512 layer 7 has a range of 0.129 —
narrow — and a best-threshold accuracy of **0.9909**. A narrow band with clean ordering
thresholds fine. In DINOv2 the two properties happened to coincide; they are not the same
property.

### 4. CLIP's crown was reference-dependent

The gate report's headline — CLIP separates best, AUC 0.9733 — held for the lava-lit
reference cluster. On the opposing cluster from the same 118 images CLIP **collapsed to
0.6144**, worse than everything else measured, with 62 of 90 frames below its best-scoring
control. DINOv2 moved the other way (0.9417 → 0.9803).

This is not a DINOv3 finding and would have been true a year ago. It is the strongest
argument in this file for never trusting a single reference configuration, including the
ones below.

### 5. Blending CLIP with a mid layer beats either alone

DINOv3 @512 layer 6 alone scores 0.8988. CLIP alone scores 0.8730. Blended they reach
**0.9802** — a real complementarity, not an averaging artefact, and consistent with the gate
report's own observation that CLIP and the DINO family are the only pair that genuinely
disagree.

| Option | Mean | Worst |
|---|---|---|
| **App today** — 0.38 CLIP + 0.62 DINOv2-final | 0.8246 | 0.6374 |
| 30% CLIP + 70% DINOv2 layer 9 — *no new model* | 0.9244 | 0.8756 |
| 20% CLIP + 80% DINOv3 @224 layer 8 | 0.9608 | 0.9341 |
| **20% CLIP + 80% DINOv3 @512 layer 6** | **0.9802** | **0.9341** |

Per configuration, the shipped blend against the proposed one:

| | test3/A | test3/B | t4 anime | t4 nature | t4 creatures | t4 gothic |
|---|---|---|---|---|---|---|
| App today | 0.965 | 0.948 | 1.000 | 0.714 | 0.637 | 0.683 |
| Proposed | 0.992 | 0.994 | 1.000 | 0.978 | 0.934 | 0.983 |

Note the weight moves **down**, to 20% CLIP from the current 38%.

### 6. The selection risk was tested, not waved away

The winning combination was chosen by searching 1,188 combinations (3 blend styles × 3
sources × 12 layers × 11 weights) against the same six configurations. Leave-one-out — pick
the setting on five configurations, score the held-out sixth — chose **the same setting in
all six folds**, held-out mean 0.9844. A choice that does not move as the data varies is not
being driven by the selection.

One variant scored marginally higher: z-scoring both components before a 50/50 blend, 0.9844.
It is **not** recommended. Z-scoring is computed across the candidate population, so every
image's score shifts when the dataset gains or loses images, and `style_similarity_score` is
stored as a stable per-image value. The raw blend gives up 0.004 and keeps that property.

## What is not established

- ~~**No photographs were tested.**~~ **Closed 2026-08-04** — see § The photograph test.
  What replaces it: the photograph set is six photographers from one platform (Flickr, via
  Openverse), and *which* photographers was a human choice made by looking at six sample
  images each. The premise that a photographer's body of work shares one look proved false
  for two rejected candidates, so it is not a free label — only a cheaper and less
  self-serving one than picking individual images.
- **232 images across three datasets.** `test4`'s configurations have small pair counts —
  its anime configuration is 2 positives × 19 controls, so its 1.0000 scores are 38 pairs
  and carry no precision.
- **No dataset here mixes photographs with illustration.** Every configuration separates
  within one medium or between two synthetic ones. A user dataset holding both is untested.
- **The `test4` style groupings were a human judgement** made from a contact sheet. A
  different grouping is a different experiment.
- ~~**No dataset here is *crossed*, so style and composition are never separated.**~~
  **Addressed 2026-08-04b** — see § The crossed runs, which holds subject fixed two
  different ways and moved the default to layer 7 as a result. It remains true of every
  number *above* that section: they all measure separability with subject and framing free
  to vary, so any one of them can be won on content. What replaces the bullet: the crossed
  runs control subject by construction (Run A) or by binning (Run B), never both, and
  neither separates layout from subject within "content". The set that would remove the
  remaining caveats is in § The dataset that would settle it properly.
- **The six configurations are not independent** — they draw from two image pools.
- **No GPU numbers.** Everything ran on CPU: DINOv3 @224 is 10.7 img/s, @512 is 2.0 img/s,
  DINOv2 @224 is 8.1 img/s. VRAM and GPU throughput are unmeasured.
- **Nothing about dense features.** DINOv3's headline advantage is patch-level features for
  segmentation and correspondence. Crucible uses only the CLS token, so that advantage is
  untested here and remains a separate opportunity (prompt-free subject masks for export
  loss masks, crop-to-subject).

## The photograph test — pre-registered, and it failed

`test5`: 90 CC-licensed photographs from Openverse/Flickr, 15 each from six photographers,
staged in `data/photo-style-import/` with a `MANIFEST.json` carrying per-image creator,
licence and source URL. **The style label is the photographer** — an external fact, not a
read of the pixels — and the 15 images per photographer exclude the 6 used to decide which
photographers to include, so the test members were never inspected. Six configurations: 4
references, 11 positives, 75 controls.

The criterion written below before the data existed was "holds above ~0.93 mean, best layer
stays in the 5–11 band". Of those two, the first failed and the second passed.

| | illustration/anime (6 configs) | photographs (6 configs) |
|---|---|---|
| 20% CLIP + 80% DINOv3@512 **L6** — the pre-registered setting | 0.9802 | **0.8165** |
| 20% CLIP + 80% DINOv3@512 **L7** — best pooled | 0.9587 | 0.8542 |
| 30% CLIP + 70% DINOv2 L9 — the no-new-model option | 0.9244 | 0.8220 |
| App today | 0.8246 | 0.7562 |

### What replicated

- **Layer 12 is the worst layer of all three models on photographs too** — 0.6079 (DINOv2),
  0.5823 (DINOv3@224), 0.5971 (DINOv3@512), against best-layer figures of 0.76–0.82. This
  now holds on three datasets of two very different classes and is the most robust result in
  this file.
- **The best layer stayed in the band**, at 7 for all three models.
- **The ordering held**: DINOv3@512 > DINOv3@224 > DINOv2 at the mid layers, mid layers over
  the final embedding, and the proposed blend over the shipped one by ~0.10 AUC.
- Pooled over all twelve configurations, leave-one-out picked **`v3@512` layer 7 at 20%
  CLIP in all twelve folds** — mean 0.9065, worst 0.8055, against the shipped blend's 0.7904
  and 0.6374.

### What did not

The absolute quality. Every signal drops on photographs, including the shipped one, so this
is a property of the task rather than of DINOv3: telling six photographers' looks apart is a
far finer discrimination than telling anime screencaps from painterly illustration. The
earlier datasets were measuring *medium* discrimination and this one measures *style*
discrimination, which is the thing the feature actually claims to do.

**Treat 0.98 as the ceiling for cross-medium sorting and ~0.85 as the realistic figure for
style matching inside one medium.** Any UI copy promising more is promising the easy case.

### Style versus subject is still leaking

Every one of the six reference sets ranked its own photographer top — 6/6, a real success —
but the margins are small, and the structure of the errors is informative. `barnyz`'s
nearest neighbour is Giuseppe Milo (0.921 against its own 0.923); `@Doug88888`'s is
Nouhailler (0.918 against 0.923). Those are exactly the two pairs that share a *subject*
(architecture, flowers) while differing sharply in treatment — the deliberate probe built
into the set. The signal is still partly a subject matcher, which no change in this file
fixes.

### Does layer 9 make it *more* of a composition matcher? — **answered 2026-08-04b**

Raised in review on 2026-08-04, on the strength of `DINO_LAYER_LABELS` calling layer 9
"Scene composition": if that is what the layer encodes, then moving the default there turns
style similarity into "which image is framed most like the references" — and the original
complaint about the final embedding was that it *drifts toward subject and framing*, so the
fix would be doubling down on the failure mode.

**The label is not evidence.** `frontend/src/constants/dinoLabels.ts` arrived in `16ca82f`
("Bug & efficiency fixes") with no citation, and `docs/dev/frontend-core.md` describes it
only as "a human-readable description". All twelve strings are a gloss on the generic
low-level→semantic depth intuition. Nothing in either campaign measured a layer and
concluded "composition". See the trap below — the label should not survive in that form.

**The indirect argument that was all this file had.** The strongest available
counter-evidence was the *worst* configuration rather than the mean, because `test4`'s groups
were assigned by **rendering style, not subject** — painterly nature, painterly creatures,
gothic interiors, with content varying inside each group. A signal that had become more
compositional should get worse there. It did the opposite (0.8246 → 0.9244 mean, 0.6374 →
0.8756 worst), and the same direction held on `test5` (0.7562 → 0.8220). Both are
*aggregate* results on sets never designed to separate style from composition, so they ruled
out "layer 9 is largely a composition matcher" and said nothing about the sharper version.

**Now answered directly** — see § The crossed runs. The answer is in two parts and only the
first is the question as asked:

1. **The retune did not move toward composition; it moved away.** Layer 12 is the
   framing-locked layer, not layer 9. Under layer 12 a reframe costs far more than a
   complete restyle (0.274); under layer 9 the two are nearer even (0.660). On every crossed
   measure layer 9 sits between layer 12 and the mid band, never past layer 12.
2. **The underlying worry is real anyway, for layer 9 and for everything deeper.** At layer
   9 the signal ranks same-subject pairs above same-style pairs; layers 5–6 do the reverse;
   the crossover is about layer 7. So the complaint was right about the *feature* while
   being wrong about the *change*.

**The default moved to 7 on the strength of it** (2026-08-04b) — Stage 2a's layer, retuned
once more. That is the whole of the action taken; the blend weights did not move.

## The crossed runs — what a separability sweep could not see

Run on **2026-08-04b**, after the retune, to answer the section above. Everything earlier in
this file scores **separability**: can the signal put in-style candidates above out-of-style
controls, with subject and framing free to vary however they happen to. A layer can win that
on content alone, because references and positives share subject matter as well as a look.
That is the § What is not established bullet on the crossed set, and it applied to every
number in this file — including the ones that picked the default.

Two runs, each holding one of style/subject fixed while varying the other. They disagree
about nothing.

### Run A — synthetic crossing

30 base pictures (18 from `test5`, 12 from `test3`) × 7 restyles × 3 framings = 630 images,
embedded through the same path the app uses (`dino_embed_offline.extract`, DINOv2 per-layer
+ CLIP). The restyles come in two families: **tone** (grayscale, warm grade, contrast punch)
and **rendering** (posterised, painterly median smear, pencil-sketch dodge). The second
family is the one that stands in for art style. Framings are the full frame and two 62%
reframes from opposite corners.

That yields four pair classes, always within one medium: STYLE (different picture, same
restyle), PICTURE (same picture, same framing, restyled), FRAMING (same picture, same
restyle, reframed), NOTHING. Every metric is an AUC, which is what makes them comparable
across layers whose raw cosine ranges differ by an order of magnitude — a mean or a
threshold is not.

Rendering restyles, all 30 bases:

| signal | sees a shared style | sees the same picture | reframe vs restyle | style over same picture |
|---|---|---|---|---|
| L5 | 0.792 | 0.760 | 0.823 | 0.474 |
| L7 | 0.721 | 0.903 | 0.825 | 0.308 |
| L9 | 0.618 | 0.960 | 0.660 | 0.105 |
| L12 | 0.517 | 0.996 | **0.274** | 0.007 |
| final embedding | 0.535 | 0.997 | 0.291 | 0.004 |
| CLIP | 0.585 | 0.944 | 0.516 | 0.106 |
| 0.30/0.70 @L7 | 0.658 | 0.969 | 0.684 | 0.138 |
| 0.30/0.70 @L9 | 0.618 | 0.980 | 0.608 | 0.076 |
| 0.38/0.62 @final | 0.560 | 0.998 | 0.311 | 0.004 |

Column 3 is the direct layout probe: **below 0.5 means a reframe costs more than a complete
restyle**. It is monotone in depth and layer 12 is its floor, which is the first half of the
answer above. Column 4 is the product question — given "the same picture re-rendered" versus
"a different picture in the reference style", how often does the reference style win? At the
shipped setting, 8% of the time.

On the 12 illustration bases alone the mid band is much stronger: layer 7 reaches 0.865 /
0.639 on columns 1 and 4 — the only setting measured anywhere that prefers a shared style to
the same picture more often than not — against layer 9's 0.709 / 0.295 and layer 12's 0.548
/ 0.033.

### Run B — real pools, subject controlled by binning

Four pools whose style label is external (the source), no synthetic edits anywhere:
`vhd` 149 frames (*Vampire Hunter D: Bloodlust*, from `test2`), `hell` 118 (`test3`), `sdxl`
126 SDXL illustrations (`data/final`), `photo` 90 (`test5`). The first three are the point:
all three are **dark gothic vampire imagery in three different renderings**, so the subject
domain is shared and the rendering is what varies. Subject is then held fixed explicitly by
binning every image with CLIP zero-shot ("a close-up of a face", "an outdoor landscape", …)
and re-scoring inside one bin.

| signal | all 4 pools | 3 drawn only | faces bin only | subject leak |
|---|---|---|---|---|
| L5 | 0.729 | 0.707 | 0.654 | **0.674** |
| L6 | 0.738 | **0.716** | 0.651 | 0.693 |
| L7 | 0.733 | 0.704 | 0.654 | 0.720 |
| L9 | 0.735 | 0.682 | 0.654 | 0.737 |
| L12 | 0.678 | 0.643 | 0.637 | 0.710 |
| CLIP | 0.745 | 0.696 | 0.655 | 0.721 |
| 0.30/0.70 @L7 | 0.762 | **0.726** | 0.659 | 0.742 |
| 0.30/0.70 @L9 | 0.763 | 0.717 | 0.660 | 0.757 |
| 0.38/0.62 @final | 0.730 | 0.677 | 0.647 | 0.751 |

*3 drawn only* removes the medium cue. *faces bin only* additionally fixes the subject
(4,606 same-style / 6,272 cross-style pairs). *subject leak* is the mirror: among pairs the
style label calls **different**, how strongly does the signal still rank same-subject pairs
top? The margin between the last two columns is the whole finding:

| | L5 | L6 | L7 | L9 | L12 | CLIP | 0.30/0.70 @L9 |
|---|---|---|---|---|---|---|---|
| style − subject | **+0.033** | **+0.023** | −0.016 | −0.055 | −0.067 | −0.025 | −0.040 |

### What the two runs establish

- **The crossover is about layer 7.** Below it style outranks subject; above it subject wins
  and keeps winning to the end of the stack. Both runs put it in the same place, from
  opposite directions.
- **Layer 12 is the composition-locked layer, not layer 9.** The label was pointing at the
  wrong end of the stack.
- **Moving 9 → 7 is close to free where a published number exists.** Re-running the gate's
  own configuration reproduces its per-layer table exactly and puts layer 7 at **0.9442**
  against layer 9's **0.9428**. On `test5` the blend gives up 0.011 (0.726 against 0.737) —
  on a test that rewards subject matching, since a photographer repeats subjects. Its
  subject-controlled subsets invert that ordering (flowers: L7 0.615, L9 0.583).
- **The absolute ceiling with subject held fixed is ~0.66**, for every layer, CLIP included.
  Three unmistakably different art styles, matched subject, and nothing measured does better
  than two calls in three. The layer argument is over a small part of a weak signal, and
  § What to promise in `docs/dev/style-similarity.md` now says so.

### Why one layer and not all twelve

Asked in the same review: if each layer encodes something different, why not combine them?
Measured on the same two runs, the premise is mostly false and the naive answer is harmful.

The layers are not twelve independent measurements. Correlating each layer's pairwise
similarity vector with every other's gives 0.84–0.96 between neighbours; the first principal
component carries **54%** of the variance across all twelve and three components carry 85%.
A residual stream guarantees this — layer N+1 is layer N plus an update.

| combination | shared style | style over same picture | illustration only | `test5` | +CLIP |
|---|---|---|---|---|---|
| L7 alone | 0.721 | **0.308** | **0.639** | 0.689 | 0.726 |
| all 12, plain mean | 0.596 | 0.029 | 0.128 | 0.669 | 0.727 |
| all 12, standardised mean | 0.725 | 0.179 | 0.398 | 0.676 | 0.721 |
| L5–L9, plain mean | 0.697 | 0.210 | 0.487 | 0.690 | 0.727 |
| L5–L9, standardised mean | 0.732 | 0.275 | 0.553 | 0.685 | 0.721 |
| L3–L7, standardised mean | **0.790** | 0.420 | 0.591 | 0.651 | 0.699 |

Three things follow. **A plain mean of all twelve is worse than any single mid layer** —
early layers compress every image into 0.90–0.99 and so contribute a near-constant offset,
which hands the ranking to whichever layers have the widest spread, i.e. the deep
content-locked ones. "Use everything" is approximately "use layer 11–12". **Standardising
first fixes that but breaks the stored score** — it needs population statistics, so an
image's score would move when the dataset gains or loses images, which is the same objection
that rejected the z-scored blend in § 6. **And it does not escape the tradeoff**: L3–L7 is
the best style-preferring combination measured and simultaneously the worst on `test5`.
Combining layers buys a weighted point on the same style-versus-subject line a single layer
sits on. The one genuine complementarity found anywhere in this work is CLIP × DINO, and the
app already ships it.

### What these runs do not settle

- **Run A's restyles are PIL filters**, not an artist's hand, and the sketch/smear renderings
  are somewhat out of distribution for the backbone.
- **Run A's reframes remove content as well as change layout**, so "framing" there is not
  purely framing.
- **Run B's pools still differ in content** — each film has its own characters and palette,
  so even the faces bin holds *different faces*. The subject control is a binning, not a
  design, and it can only understate the confound.
- **CLIP zero-shot bins are noisy**, and `test3` carries ~20 painted-illustration controls
  that dilute the `hell` pool.
- **One backbone.** Everything here is DINOv2-base; whether DINOv3's crossover sits at the
  same depth is unmeasured, and Stage 2 should re-run both probes rather than assume it.
- **Neither run separates subject from composition.** "Same picture" is the same subject
  *and* the same layout; only the reframe column probes layout alone. The finding is
  style-versus-**content**; composition is the part of content that column isolates.

### The dataset that would settle it properly

The crossed photo set specified below broke style against subject for photography by hand.
The illustration equivalent no longer needs curation, because the app can generate it:
**one ComfyUI plan, 6 fixed subject prompts × 5 style tokens or style LoRAs × 4 seeds = 120
images**, where style and subject are crossed *by construction* and neither label is
inferred. That removes every caveat in the list above except the backbone one. It is the
natural next experiment and it costs GPU time rather than judgement — see
`docs/dev/comfyui.md` for plans and pinned params.

## The photo dataset that settled it

**Built and run on 2026-08-04 — see § The photograph test above.** The spec below is kept
because it is the recipe for the next such set, and because the acceptance criterion it
records is the one the run failed.

Structure matters more than raw count. **The confound to break is style versus subject**:
throughout this work, signals that looked like style matchers turned out to be partly
subject matchers, and a set where each style is also a distinct subject cannot tell the two
apart.

So the ask is a **crossed** set — every style represented across several subjects, and every
subject shot in several styles:

- **5 styles × 3 subjects × 4–6 photos each = 60–90 images.**
- *Styles* must differ in look, not content: e.g. flash-lit night, golden-hour natural
  light, high-key studio on white, flat overcast documentary, high-grain black and white.
- *Subjects* crossed through all of them: a person, a place, a close-up object.
- **60 is the floor, 90 is comfortable.** At 5 styles this yields configurations of ~4
  references, 8–14 positives and ~48–72 controls, where the AUC standard error is roughly
  0.04–0.055. That resolves "does the blend hold near 0.98 or collapse below 0.85" at
  several sigma, which is the question; it does **not** resolve 0.98 versus 0.96, and no
  affordable set would.
- **Worth including if cheap**: 5–10 near-duplicate pairs (same scene, two frames) as a
  retrieval check, and a handful of the same scene shot in two different styles — the
  sharpest available probe of finding 5's style-versus-subject question.

Real photographs only. Anything AI-generated re-tests what `test4` already covers.

Once the images are in a dataset, the run needs no new code:

```bash
# extract (no DB writes; HF_TOKEN must have gated-repo read scope)
python -m backend.scripts.dino_embed_offline --dataset <name> --model clip --out /tmp/p_clip.npz
python -m backend.scripts.dino_embed_offline --dataset <name> --model facebook/dinov2-base --out /tmp/p_v2.npz
python -m backend.scripts.dino_embed_offline --dataset <name> \
  --model facebook/dinov3-vitb16-pretrain-lvd1689m --size 512 --batch 4 --out /tmp/p_v3.npz

# score, one configuration per style group
python -m backend.scripts.style_gate --dataset <name> --refs <4 of one style> \
  --control <the other styles> --top 10 --layers \
  --extra-embeddings v3=/tmp/p_v3.npz,v2=/tmp/p_v2.npz
```

**Acceptance**: the proposed blend holds above ~0.93 mean across the photo configurations,
and the chosen layer stays inside the 5–11 band. If it does not, the finding is
dataset-class-specific and the default should not move.

## Proposed stages

Deliberately ordered so the cheapest, least committal win comes first. **Stages 1 and 2a
shipped together on 2026-08-04 and are deleted from here per this file's lifecycle** —
`combined` is now 30% CLIP + 70% DINOv2, both `*_all_layers` modes read
`DEFAULT_DINO_LAYER`, and no mode description quotes an AUC. `DEFAULT_DINO_LAYER` was
retuned once more the same day, from 9 to **7**, on § The crossed runs; the blend weights
did not move. Their durable rationale is in
`docs/dev/style-similarity.md` and, for users, `docs/style-similarity.md`. They shipped
together because a default the UI argued against would have been worse than either alone.

### Stage 2 — DINOv3 as a second embedding source

Store DINOv3 layer blobs and default the picker to **layer 7**, the pooled winner and the
best layer on photographs for all three models. Geometry is identical (12 × 768 float16),
which is exactly why this needs a discriminator — see the trap below.

Worth **0.7904 → 0.9065** pooled, but the honest split is 0.96 on cross-medium sorting and
0.85 on within-medium style matching. Ship the copy against the second number.

### Stage 2b — stop the layer labels asserting things nobody measured

Cheap, independent of Stage 2, and the only item here that is a *defect* rather than an
improvement. `DINO_LAYER_LABELS` gives all twelve layers a confident semantic name — "Object
parts", "Scene composition", "Abstract semantics" — none of which came from a measurement.
It renders in the layer picker on two screens and against every bar in `DinoLayerBreakdown`,
directly beside the layer the app now recommends, so it is doing real persuasive work in
both directions: in review it made a reader distrust the new default for a reason that turns
out to be unfounded, and it could as easily make the next reader trust it for one.

**The crossed runs make this worse, not better.** "Scene composition" on layer 9 is now
measurably pointing at the wrong end of the stack — layer **12** is the framing-locked one
(reframe-vs-restyle 0.274 against layer 9's 0.660), and the label sits two rows above it.
`DINO_LAYER_NOTE` has meanwhile gained a *measured* per-depth claim (deeper leans to subject,
5–6 for the look, 9–10 for the scene), so the picker now shows a sourced statement and an
unsourced one side by side, in the same idiom.

Two options, in order of preference:

1. **Replace the semantics with what was actually measured** — the low band's compression
   (every image lands in a few hundredths of each other) and the mid band separating best —
   phrased as depth position plus an observed property, not as a claim about features.
2. **Drop to bare layer numbers.** No information is lost that the breakdown chart does not
   already show, and the honest state of knowledge is that the app does not know what any
   individual layer represents.

Whichever, the labels must stop implying a per-layer semantics the evidence does not carry.
Touchpoints: `frontend/src/constants/dinoLabels.ts`, its line in
`docs/dev/frontend-core.md`, and `frontend/e2e/quality.spec.ts` if a label is asserted on.

### Stage 3 — dense features

Untested and separate: patch-level features for prompt-free subject masks (export loss
masks, crop-to-subject). This is where DINOv3's actual headline advantage lives, and none of
the above measures it.

## Traps

- **The blob-geometry trap, restated from `roadmap.md` § 5 because it is now closer.**
  DINOv3 ViT-B/16 is 12 × 768, byte-identical to DINOv2-base, so `_LAYER_BLOB_SIZE`'s length
  guard in `backend/ml/dino_scorer.py` passes for both. Mixed old and new blobs would be
  cosine-compared — unrelated embedding spaces — producing meaningless rankings with no
  error and no visibly wrong number. `StyleSimilarityRun` records the mode and the layer but
  **not the model**. A discriminator on the embedding columns is load-bearing, not a nicety.
- ~~**`dino_embedding` is not layer 12.**~~ **Closed 2026-08-04.** The layer picker is
  three-valued now (`number | "final" | "all"`), so the two are separately selectable rather
  than one being folded into the other; `docs/dev/style-similarity.md` carries the fact.
- ~~**`dino_all_layers` writes layer 12's value**~~ **Closed 2026-08-04** — both all-layers
  modes write `DEFAULT_DINO_LAYER`'s. Still worth re-checking when a second model lands:
  the constant is DINOv2's best band, and DINOv3's optimum sits elsewhere.
- **An unsourced label is indistinguishable from a finding once it is on screen.**
  `DINO_LAYER_LABELS` is twelve confident semantic claims with no measurement behind any of
  them, sitting in the picker beside a default that *was* measured — so a reader has no way
  to tell which of the two is evidence. It is the reason § Does layer 9 make it more of a
  composition matcher? had to be written at all. The general form: anything this file's
  findings get rendered next to must be checkable back to a measurement, or it will be read
  as one. Stage 2b is the fix.
- **A separability sweep cannot see the subject confound, and reads as though it can.**
  Every per-layer and per-blend number above § The crossed runs answers "do in-style
  candidates outrank out-of-style controls" on sets where in-style images also share subject
  matter — so a signal that ranks on content scores well and looks like a style matcher. The
  first campaign chose a default that way, and the crossed runs moved it one layer. The
  general form: when a metric's positives share two properties, it measures neither until
  one of them is held fixed. Any future sweep here states which of style/subject it fixed,
  or it is measuring the same thing again.
- **Licensing and gating are unchanged.** All `facebook/dinov3-*` repos are `gated: manual`
  under a custom DINOv3 Licence, not Apache-2.0, and a fine-grained token additionally needs
  gated-repo read scope or the fetch 403s. `transformers>=4.56` is required; the floor in
  `backend/requirements.txt` is `>=4.36`.
- **512 px costs 5×** the CPU time of 224 px for roughly 0.02 AUC. The 224 px blend is the
  better default with 512 as an option.

## Touchpoints when a stage lands

`model_name` in `backend/ml/model_manager.py`; `_LAYER_BLOB_SIZE`, `slice_layer_embedding`
and the three constants (`STYLE_CLIP_WEIGHT`, `STYLE_DINO_WEIGHT`, `DEFAULT_DINO_LAYER`) in
`backend/ml/similarity_scorer.py` — they moved there out of `dino_scorer` so the router's
combined branch stays torch-free; the layer table and findings in
`docs/dev/style-similarity.md`; the scorer row in `docs/dev/scoring.md`; `STYLE_MODES`,
`STYLE_MODE_NOTE`, `DINO_LAYER_NOTE` and the frontend's own `DEFAULT_DINO_LAYER` in
`frontend/src/constants/styleModes.ts` (a backend test asserts the last agrees with
Python's); the per-layer picker in `QualityPage` and `SelectionToolbar`; and
`frontend/e2e/quality.spec.ts`.

A **model** discriminator is the one descriptor field `StyleSimilarityRun` still lacks —
`clip_weight`/`dino_weight` landed with Stage 1, so the pattern and the migration shape are
now established.

## State of the tooling

Both harness scripts are **uncommitted working-tree changes** as of 2026-08-04:

- `backend/scripts/dino_embed_offline.py` — new. Offline embedding extraction to `.npz` for
  any DINO checkpoint plus the app's CLIP path, with `--verify` against the stored column.
- `backend/scripts/style_gate.py` — gained `--extra-embeddings name=path.npz` and the
  separation metrics (AUC, best threshold, accuracy, control positions, worst-control rank).
  Those metrics were previously computed by hand from the JSON sidecar, which is why the
  report's headline table could not be regenerated for a new mode.

The crossed-run harness (2026-08-04b) is **not committed anywhere** — it lived in a session
scratchpad and is gone. It was four short scripts and is cheap to rebuild from § The crossed
runs, which states every parameter: the restyle and reframe definitions, the four pair
classes, the four pools, and the CLIP zero-shot bin prompts. Both runs call
`dino_embed_offline.extract` / `extract_clip` for embeddings, so the only original code is
variant generation with Pillow and a rank-based AUC over pair masks. If the ComfyUI crossed
set gets built, that harness is what scores it and it should land in `backend/scripts/`
rather than a scratchpad the second time.
