# Style Similarity

Score how close each image is to a set of reference images — the tool for keeping a training set stylistically consistent. It is part of [Quality Scoring](scoring.md): the embeddings it compares are written by that page's scorers, and the section that drives it sits at the bottom of the same page.

Available from: the collapsible **Style similarity** section on the **Score images** page, and the same section inside the gallery selection toolbar's **Score** panel (scoped to the selection).

Because it compares embeddings that already exist, it is CPU-only and runs immediately rather than queueing a job.

## Running a style run

- **Embedding model** — see the mode table below. **Start with CLIP + DINOv2**, the default: it is the steadiest of the three across different reference sets, and the two models are the only pair that genuinely disagree, so blending them is worth more than either alone. All three require the matching embeddings to have been computed first, by a [scoring run](scoring.md#running-a-scoring-run) with the relevant boxes ticked — the panel tells you when they are missing, and refuses to run rather than failing.
- **DINOv2 layer** — when using DINOv2 or the blend, pick which of the 12 transformer layers to compare on; each block captures increasingly abstract features. **Layer 9 is the default**, because the middle of the stack separates styles best and the last layer is measurably the weakest — do not read a layer's raw score range as a guide to its usefulness. *Final embedding* is a separate option from *Layer 12*: it uses the standard `dino_embedding`, which is a different vector from layer 12 and scores differently. Every numbered layer, and *All layers*, need per-layer embeddings. **All layers** scores every layer independently and stores the results side by side for comparison in the image detail view.
- **Reference images** — pick them from the dataset, or drag in local files from outside it. Local files are always embedded with CLIP, so choosing them restricts the run to the CLIP model.
- **Copy reference IDs** — beside *Score similarity*, copies the selected dataset references' image IDs to the clipboard as a comma-separated list, for pasting into a script or an API call. Dragged-in local files have no ID and are not included.

Scoring writes a `style_similarity_score` per image, which the gallery and Statistics page can then filter and chart on. After a run, a **Current scores** line in the panel reports the dataset's median score, the threshold its top 10% sits above, and the full range — the figures that let one run be compared against the last.

## Style similarity modes

| Mode | Description |
|---|---|
| `clip` | Cosine similarity of CLIP ViT-L-14 embeddings. Matches on lighting and palette. Strong on some reference sets and weak on others, more so than the DINOv2 modes |
| `dino` | Cosine similarity of DINOv2 embeddings, from a chosen layer. Spends a wider numeric range, leans toward subject and framing, and is steadier than CLIP across reference sets |
| `combined` | Weighted blend: 30% CLIP + 70% DINOv2, layer 9 by default. The most reliable of the three across varied references |
| `dino_all_layers` / `combined_all_layers` | Score each of the 12 DINOv2 layers independently and store all results |

## Reading the score

**The raw score is a cosine, and its scale depends entirely on what produced it.** On the same set of images, CLIP scores span roughly 0.53–0.93 while DINOv2 spans 0.05–0.70, a low-layer run compresses everything into a few hundredths, and the same mode at a different blend weight is a different scale again. A bare "0.62" therefore means something different in each case, and a fixed good/bad threshold would mean several things at once.

So Crucible reports a **percentile** instead — where an image falls among that dataset's own style scores. That is comparable no matter which mode ran, and it is what the meters below show:

- **On gallery cards** — a thin bar along the bottom edge of each thumbnail. Longer is a closer match to the references. Images with no style score show no bar at all, and the bar dims when the image's pixels have been [edited in place since scoring](scoring.md#stale-scores). Switch it off in Settings → Gallery.
- **On the image detail page** — a **Style match** block below the score list, showing the meter, the percentile in words ("Top 12%"), the raw cosine, and a line naming the mode, the layer, how many references were used and when the run happened. Below that, thumbnails of the references themselves; if the image you are looking at was one of them, its tile is ringed — a reference scores close to 1.0 against its own set, so its high match is expected rather than meaningful.

Two caveats the page states rather than hides:

- **A run scored from a selection can leave older scores in place.** The rest of the dataset keeps its scores from an earlier run, so the percentile mixes two runs. The Style match block says so in amber — but only when scores from an earlier run really do survive, not merely because the run started from a selection. Selecting everything in the gallery and scoring it covers the whole dataset, and gets no note.
- **A dataset scored before Crucible recorded run details, or duplicated from another dataset, has no run to report.** The meter and the raw score still work — the percentile comes from the dataset's own scores either way — but the block says the mode and references are unknown. The same applies to the blend weights on a run scored before Crucible started recording them.

A dataset with only one scored image, or with every score identical, gets no meter: there is nothing to rank against, so only the raw number is shown.

## How good is it, really?

Honest numbers, because the scale of the score does not carry them. Measured as "do the
in-style images outrank the out-of-style ones", across twelve reference sets spanning
illustration, animation and photography:

- **Sorting one medium from another** — anime screencaps from painted illustration, say — is
  the easy case, and the default gets it nearly always right.
- **Matching a style *within* one medium** — telling six photographers' looks apart — is much
  harder, and the default gets it right roughly six times in seven. Treat the ranking as a
  strong hint to review, not a verdict.
- **No single mode is best.** CLIP was the clear winner against one reference set and the
  clear loser against a different set drawn from *the same images*. That is why the mode
  descriptions in the app say what each model pays attention to rather than quoting a score.

Layer 9 and the 30/70 blend replaced an older default on 2026-08-04, and scores written
before then are not comparable with scores written after. The **Style match** block on the
image detail page names the mode, the layer and the weights for exactly this reason; a
dataset scored under the old default is worth re-running.

Style similarity sits outside the [stale-score](scoring.md#stale-scores) machinery in one direction: it is neither refreshed by a re-scoring run nor blocking one.
