# Style Similarity

Score how close each image is to a set of reference images, then read the result as a percentile on gallery cards and the image detail page.

Available from: the collapsible **Style similarity** section at the bottom of the **Score images** page, and the **Score** button in the gallery selection toolbar (scoped to the selection). The embeddings it compares are computed by a scoring run — see [Quality Scoring](scoring.md).

## Running a comparison

This section scores how close each image is to a set of reference images — the tool for keeping a training set stylistically consistent. Because it compares embeddings that already exist, it is CPU-only and runs immediately rather than queueing a job.

- **Embedding model** — see the table below. The picker opens on CLIP, but **CLIP + DINOv2 is the one to start with**: it is the steadiest of the three across different reference sets, and the two models are the only pair that genuinely disagree, so blending them is worth more than either alone. All three require the matching embeddings to have been computed first, by a [scoring run](scoring.md#running-a-scoring-run) with the relevant boxes ticked. On the **Score images** page the panel tells you when they are missing, and refuses to run rather than failing; the gallery toolbar's panel does not check first, so a selection with no embeddings fails with an error.
- **DINOv2 layer** — when using DINOv2 or the blend, pick which of the 12 transformer layers to compare on. Each layer in the list is named by where it sits in the stack and by what was measured about it: the low layers score almost every image alike, and from the middle up each layer leans a little further from the look toward the subject. **Layer 7 is the default**, because the middle of the stack separates styles best and the last layer is measurably the weakest — do not read a layer's raw score range as a guide to its usefulness. The deeper the layer, the more it ranks on *what is in the picture* rather than on how it is drawn, so **try layers 5–6 to weight the look more heavily and 9–10 to find more of the same subject and framing**. *Final embedding* is a separate option from *Layer 12*: it uses the standard DINOv2 embedding, which is a different vector from layer 12 and scores differently. Every numbered layer, and *All layers*, need per-layer embeddings. **All layers** scores every layer independently and stores the results side by side for comparison in the image detail view.
- **After the November 2026 CLIP fix**, every image's CLIP style embedding was removed, because the old ones came from a wrongly built model and must not be compared with new ones. Re-make them with a scoring run before using the CLIP or CLIP + DINOv2 modes — see [After the CLIP fix](scoring.md#after-the-clip-fix-november-2026). DINOv2 embeddings were not affected.
- **Reference images** — pick them from the dataset, or drag in local files from outside it. Local files are always embedded with CLIP, so choosing them restricts the run to the CLIP model.
- **Copy reference IDs** — beside *Score similarity*, copies the selected dataset references' image IDs to the clipboard as a comma-separated list, for pasting into a script or an API call. Dragged-in local files have no ID and are not included.

Scoring writes a `style_similarity_score` per image, which the gallery and Statistics page can then filter and chart on. After a run, a **Current scores** line in the panel reports the dataset's median score, the threshold its top 10% sits above, and the full range — the figures that let one run be compared against the last.

## Style similarity modes

| Mode | Description |
|---|---|
| `clip` | Cosine similarity of CLIP ViT-L-14 embeddings. Matches on lighting and palette. Strong on some reference sets and weak on others, more so than the DINOv2 modes |
| `dino` | Cosine similarity of DINOv2 embeddings, from a chosen layer. Spends a wider numeric range, leans toward subject and framing, and is steadier than CLIP across reference sets |
| `combined` | Weighted blend: 30% CLIP + 70% DINOv2, layer 7 by default. The most reliable of the three across varied references |
| `dino_all_layers` / `combined_all_layers` | Score each of the 12 DINOv2 layers independently and store all results |

## Reading the score

**The raw score is a cosine, and its scale depends entirely on what produced it.** On the same set of images, CLIP scores span roughly 0.53–0.93 while DINOv2 spans 0.05–0.70, a low-layer run compresses everything into a few hundredths, and the same mode at a different blend weight is a different scale again. A bare "0.62" therefore means something different in each case, and a fixed good/bad threshold would mean several things at once.

So Crucible reports a **percentile** instead — where an image falls among that dataset's own style scores. That is comparable no matter which mode ran, and it is what the meters below show:

- **On gallery cards** — a thin bar along the bottom edge of each thumbnail. Longer is a closer match to the references. Images with no style score show no bar at all, and the bar dims when the image's pixels have been [edited in place since scoring](scoring.md#stale-scores). Such an edit also removes the image's embeddings. The next style run skips the image and removes its old style score, so an old number never mixes with the new run's. Score its embeddings again to bring it back. The same goes for a reference image: one without embeddings is left out, and the message after the run says how many were. Switch it off in Settings → Gallery.
- **On the image detail page** — a **Style match** block below the score list, showing the meter, the percentile in words ("Top 12%"), the raw cosine, and a line naming the mode, the layer, the blend weights, how many references were used and when the run happened. Below that, thumbnails of the references themselves; if the image you are looking at was one of them, its tile is ringed — a reference scores close to 1.0 against its own set, so its high match is expected rather than meaningful.

Two caveats the page states rather than hides:

- **A run scored from a selection can leave older scores in place.** The rest of the dataset keeps its scores from an earlier run, so the percentile mixes two runs. The Style match block says so in amber — but only when scores from an earlier run really do survive, not merely because the run started from a selection. Selecting everything in the gallery and scoring it covers the whole dataset, and gets no note.
- **A dataset scored before Crucible recorded run details, or duplicated from another dataset, has no run to report.** The meter and the raw score still work — the percentile comes from the dataset's own scores either way — but the block says the mode and references are unknown. A run scored before Crucible started recording blend weights simply shows none.

A dataset with only one scored image, or with every score identical, gets no meter: there is nothing to rank against, so only the raw number is shown.

## How good is it, really?

Honest numbers, because the scale of the score does not carry them. Measured as "do the
in-style images outrank the out-of-style ones", across reference sets spanning
illustration, animation, photography, digital painting and 3D film:

- **Sorting one medium from another** — anime screencaps from painted illustration, say — is
  the easy case, and the default gets it nearly always right.
- **Matching a style *within* one medium** — telling six photographers' looks apart — is much
  harder, and the default gets it right roughly six times in seven. Treat the ranking as a
  strong hint to review, not a verdict.
- **No single mode is best.** CLIP was the clear winner against one reference set and the
  clear loser against a different set drawn from *the same images*. That is why the mode
  descriptions in the app say what each model pays attention to rather than quoting a score.
- **Part of what it matches is the subject, not the style.** Both figures above come from
  sets where in-style images also tend to share subject matter, so some of that accuracy is
  the picture rather than the look. Tested against images that deliberately hold the subject
  fixed and vary only the rendering, every setting drops to roughly two right calls in three.
  Choosing a lower layer shifts the balance toward the look; it does not remove the effect.

Layer 7 and the 30/70 blend replaced the older default (38/62 on the final embedding) in the
November 2026 update, and scores written before then are not comparable with scores written
after. The **Style match** block on the image detail page names the mode, the layer and the
weights for exactly this reason; a dataset scored under the old default is worth re-running.
