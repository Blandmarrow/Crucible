# Style Similarity

Score how close each image is to a set of reference images — the tool for keeping a training set stylistically consistent. It is part of [Quality Scoring](scoring.md): the embeddings it compares are written by that page's scorers, and the section that drives it sits at the bottom of the same page.

Available from: the collapsible **Style similarity** section on the **Score images** page, and the same section inside the gallery selection toolbar's **Score** panel (scoped to the selection).

Because it compares embeddings that already exist, it is CPU-only and runs immediately rather than queueing a job.

## Running a style run

- **Embedding model** — see the mode table below. **Start with CLIP**: measured against a deliberately out-of-style control set, it separated in-style from out-of-style images best of the three and matched the *look* — lighting and palette — most closely, while DINOv2 leaned toward subject and framing. All three require the matching embeddings to have been computed first, by a [scoring run](scoring.md#running-a-scoring-run) with the relevant boxes ticked.
- **DINOv2 layer** — when using DINOv2 or the blend, pick which of the 12 transformer layers to compare on; each block captures increasingly abstract features. Layer 12 uses the standard embedding; the rest require per-layer embeddings. **All layers** scores every layer independently and stores the results side by side for comparison in the image detail view. Layers 1–8 pack every image into 0.90–0.99, so there is no cut point in them however sensible the ordering — usable spread only appears at layers 10–12.
- **Reference images** — pick them from the dataset, or drag in local files from outside it. Local files are always embedded with CLIP, so choosing them restricts the run to the CLIP model.
- **Copy reference IDs** — beside *Score similarity*, copies the selected dataset references' image IDs to the clipboard as a comma-separated list, for pasting into a script or an API call. Dragged-in local files have no ID and are not included.

Scoring writes a `style_similarity_score` per image, which the gallery and Statistics page can then filter and chart on. After a run, a **Current scores** line in the panel reports the dataset's median score, the threshold its top 10% sits above, and the full range — the figures that let one run be compared against the last.

## Style similarity modes

| Mode | Description |
|---|---|
| `clip` | Cosine similarity of CLIP ViT-L-14 embeddings. Separates in-style from out-of-style best of the three, and matches lighting and palette |
| `dino` | Cosine similarity of DINOv2 final-layer (or any of 12 layers) embeddings. Spends a wider numeric range but separates less well, and drifts toward subject and framing |
| `combined` | Weighted blend: 38% CLIP + 62% DINOv2. Tracks `dino` closely rather than giving a genuinely third opinion |
| `dino_all_layers` / `combined_all_layers` | Score each of the 12 DINOv2 layers independently and store all results |

## Reading the score

**The raw score is a cosine, and its scale depends entirely on which mode produced it.** On the same set of images, CLIP scores span roughly 0.53–0.93 while DINOv2 spans 0.05–0.70, and a per-layer run below layer 10 compresses everything into 0.90–0.99. A bare "0.62" therefore means something different in each mode, and a fixed good/bad threshold would mean five different things at once.

So Crucible reports a **percentile** instead — where an image falls among that dataset's own style scores. That is comparable no matter which mode ran, and it is what the meters below show:

- **On gallery cards** — a thin bar along the bottom edge of each thumbnail. Longer is a closer match to the references. Images with no style score show no bar at all, and the bar dims when the image's pixels have been [edited in place since scoring](scoring.md#stale-scores). Switch it off in Settings → Gallery.
- **On the image detail page** — a **Style match** block below the score list, showing the meter, the percentile in words ("Top 12%"), the raw cosine, and a line naming the mode, the layer, how many references were used and when the run happened. Below that, thumbnails of the references themselves; if the image you are looking at was one of them, its tile is ringed — a reference scores close to 1.0 against its own set, so its high match is expected rather than meaningful.

Two caveats the page states rather than hides:

- **A run scored from a selection can leave older scores in place.** The rest of the dataset keeps its scores from an earlier run, so the percentile mixes two runs. The Style match block says so in amber — but only when scores from an earlier run really do survive, not merely because the run started from a selection. Selecting everything in the gallery and scoring it covers the whole dataset, and gets no note.
- **A dataset scored before Crucible recorded run details, or duplicated from another dataset, has no run to report.** The meter and the raw score still work — the percentile comes from the dataset's own scores either way — but the block says the mode and references are unknown.

A dataset with only one scored image, or with every score identical, gets no meter: there is nothing to rank against, so only the raw number is shown.

Style similarity sits outside the [stale-score](scoring.md#stale-scores) machinery in one direction: it is neither refreshed by a re-scoring run nor blocking one.
