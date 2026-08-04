# Phase 0 style-similarity gate — findings

**Date:** 2026-08-02
**Harness:** `backend/scripts/style_gate.py`
**Question:** Does the existing centroid baseline already order images by *style* well enough
that style never needs a learned head of its own?

## Status

**Gate run and closed.** Dataset `test3`: 98 frames from one film (*Vampire Hunter D:
Bloodlust*) plus **20 out-of-style controls** — bright painted illustrations of forests,
beaches and meadows, copied in from `test` and auto-renamed to `image_005/024/100–117.png`.
Full CLIP + DINOv2 + per-layer coverage on all 118.

The first pass ran without those controls and its answer to question 2 was correspondingly
soft; it is not preserved here because the second pass supersedes it on every number. The
one thing worth carrying from it: with only *within-film* controls (daylight frames of the
same film) CLIP put 0 of 6 in its bottom band, which read as a weakness and was not one —
**against genuinely foreign images CLIP separates best of the three modes**. A control set
drawn from inside the population under test can invert the ranking of the thing you are
testing.

## Method

- **The baseline under test is the shipped one.** `compute_style_similarity` in
  `backend/ml/similarity_scorer.py` *is* the centroid baseline the learned-head spec
  proposes — stack references → mean → L2-renormalise → cosine — so the harness calls that
  function and `compute_combined_similarity` directly rather than a lookalike. The 0.38/0.62
  blend and the 4-decimal rounding come along with them. A verdict here is therefore a
  verdict about production code.
- **The real `dataset_manager.db` is opened read-only and never written.** stdlib `sqlite3`
  over a `file:…?mode=ro` URI, no SQLAlchemy session and no engine — there is no write path
  to get wrong. `style_similarity_score` is untouched by design, which is the point of doing
  this offline at all: `POST /quality/style-similarity` writes that one shared column for
  every mode with no record of which produced it, so comparing `clip` / `dino` / `combined`
  through the app means three destructive overwrites plus manual snapshotting between them.
  The bottom-20 is also unreachable in the UI — `SORT_OPTIONS` offers only
  *Style similarity ↓*, and `style_similarity_score` is absent from
  `_NULLS_LAST_SORT_FIELDS`, so a naive `order=asc` returns unscored rows first under SQLite.
- **One read, three modes.** A single query pulls `clip_embedding`, `dino_embedding`,
  `aesthetic_score` and the thumbnail path for the dataset; all three rankings are computed
  from that in-memory set.
- **Missing embeddings are reported, never scored.** A row lacking a column a mode needs is
  excluded *from that mode only* and counted, per the failure contract in
  `docs/dev/scoring.md`. A mode with no usable references or candidates is reported as
  skipped with the counts that made it so — never as a run that scored nothing.
- **Torch-free by default.** `dino_scorer` imports torch at module top, so
  `slice_layer_embedding` is imported lazily inside the `--layers` branch alone; the script
  prints whether `torch` ended up in `sys.modules` so a default run can be checked.
- **The gate dataset is small and the run is CPU.** ~100 frames plus ~20 out-of-style
  controls moved into a `style-gate` dataset. **Absolute cosines are not portable** — they
  depend entirely on the reference set — so the finding is the *ordering*, the *spread*, and
  the control images' position, never the numbers themselves.

### Reproducing

```bash
# 1. In the app: create the gate dataset, move ~100 frames into it, plus ~20 images
#    of a visibly different look as an out-of-style control.
# 2. QualityPage → Score images with Embeddings (CLIP) + DINOv2 ticked (add per-layer only
#    for --layers). Then pick references in the style picker and hit "Copy reference IDs".
# 3. From the repo root, venv active — the exact command behind the numbers below:
python -m backend.scripts.style_gate --dataset test3 \
  --refs image_017.jpg,image_018.jpg,image_019.jpg,image_029.jpg,image_030.jpg,image_032.jpg,image_033.jpg,image_038.jpg \
  --control '*.png' --top 20 --layers --out style_gate.html
```

`--control '*.png'` works because the frames are `.jpg` and every control is `.png` — the
move renumbered the filenames into the destination's scheme but left the extension alone.

**The reference set was chosen to make the run falsifiable**: eight frames of one visually
unmistakable cluster — the red lava-lit interior with D in profile. If the baseline works at
all, the top band should be dark red-lit interiors and the bottom band should be the bright
daylight/snow exteriors. That is a prediction with a wrong answer available, which "pick
eight nice-looking frames" is not.

`--refs` and `--control` each accept image ids, filenames, filename globs, or `@file` with
one entry per line. References are excluded from the ranking. The script writes the HTML
contact sheet plus a JSON sidecar of the full rankings, so a verdict can be revisited
without recomputing.

**The control set is what makes the gate falsifiable.** Without it the sheet is 100 frames
of one film in one style and "the ordering looks plausible" cannot be disproved. With it
there is a real question with a wrong answer available: do the controls sink to the bottom?

## Coverage

| | images | clip | dino | layers |
|---|---|---|---|---|
| `test3` | 118 | 118 | 118 | 118 |

8 references (excluded from the ranking) · 110 candidates · 20 of them control · **0 excluded
from any mode** for a missing embedding. Blob geometry checked before the run: 1536 B CLIP,
1536 B DINOv2, 18432 B per-layer — i.e. 768 / 768 / 12×768 float16, as expected.

## Spread

The spread matters as much as the ordering. Cosines to a mean reference cluster in a narrow
positive band, and **a mode whose top and bottom differ by 0.02 has discriminated nothing**
regardless of how the pictures look — there would be no threshold to filter or sort on.

| mode | candidates | excluded | min | median | max | range | stdev |
|---|---|---|---|---|---|---|---|
| clip | 110 | 0 | 0.5309 | 0.7804 | 0.9304 | 0.3995 | 0.0985 |
| dino | 110 | 0 | 0.0516 | 0.3457 | 0.6958 | **0.6442** | 0.1589 |
| combined | 110 | 0 | 0.2449 | 0.5107 | 0.7849 | 0.5400 | 0.1298 |

**CLIP's floor is 0.53 and DINOv2's is 0.05.** The narrow-band worry is CLIP's alone: it
uses 0.53–0.93 with the frames' median at 0.78, so the usable part of its axis is ~0.2 wide
and a threshold cuts a dense middle. DINOv2 spends the full 0.05–0.70 and puts its median at
0.35 — the only mode whose numbers read like a distribution rather than a band. As the next
section shows, that does **not** make it the better separator.

## Separation — does the control set actually sink?

This is the falsifiable half. AUC is the probability that a randomly chosen frame outranks a
randomly chosen control (0.5 = coin flip, 1.0 = perfect); the accuracy column is the best
single threshold on that mode's scores.

| mode | AUC | best threshold | acc | controls in bottom 20 | controls in top 20 | worst control rank |
|---|---|---|---|---|---|---|
| clip | **0.9733** | 0.6744 | 0.936 | 16/20 | 0/20 | **75 of 110** |
| dino | 0.9417 | 0.1311 | 0.909 | 14/20 | 0/20 | 44 of 110 |
| combined | 0.9650 | 0.3568 | 0.945 | 17/20 | 0/20 | 54 of 110 |

**CLIP is the cleanest separator despite the narrower band.** Range and separation are
different properties, and CLIP has less of the first and more of the second: all 20 of its
controls sit in the bottom third (ranks 75–109) and only 15 of 90 frames score below its
best-scoring control, versus 46 of 90 for DINOv2. Not one mode put a single control in its
top 20.

The one control that resists every mode is `image_104.png` — the dark, red-lit portrait of a
red-eyed woman (clip rank 75, dino 44, combined 54). It is a different illustration style
but the *same* palette and lighting as the references, so ranking it mid-pack is arguably
the correct call rather than an error. Excluding it, DINOv2's worst control rank moves from
44 to 81.

## Cross-mode agreement

| pair | Spearman ρ | top-20 overlap | bottom-20 overlap |
|---|---|---|---|
| clip vs dino | 0.7525 | 10/20 | 13/20 |
| clip vs combined | 0.8457 | 12/20 | 16/20 |
| dino vs combined | **0.9844** | 18/20 | 17/20 |

The three modes are **not** the free three-way A/B roadmap §3 assumed. `combined` is DINOv2
with a slight CLIP tilt — ρ 0.98, 18 of 20 top images shared — so there are **two**
independent signals here, not three. CLIP and DINOv2 do genuinely disagree (ρ 0.75), and
that is the only axis worth A/B-ing anything on.

## DINOv2 per-layer sweep — the early layers rank weakly and cannot be thresholded

| layer | range | median | AUC vs controls |
|---|---|---|---|
| 1 | 0.041 | 0.987 | 0.676 |
| 2–4 | 0.042 – 0.076 | 0.981 – 0.990 | 0.808 – 0.864 |
| 5–8 | 0.027 – 0.092 | 0.969 – 0.993 | 0.908 – 0.944 |
| 9 | 0.163 | 0.922 | 0.943 |
| 10 | 0.254 | 0.896 | 0.923 |
| 11 | 0.414 | 0.854 | 0.911 |
| 12 | **0.654** | 0.478 | **0.961** |

Two separate things, worth not conflating. **Ordering**: the early layers are weak but not
noise — layer 1 is close to useless (AUC 0.68) and layers 5–9 rank surprisingly well
(0.91–0.94), though none beats the final layer's 0.961. **Thresholding**: every candidate
scores 0.90–0.99 on layers 1–8, so whatever ordering exists is compressed into a band a few
hundredths wide with no cut point in it, and the 4-decimal rounding in
`compute_style_similarity` is spending most of its resolution on the constant part. Usable
spread only appears at layers 10–12.

The per-layer picker on QualityPage presents all twelve as equals. For the ranking-then-
filtering job the UI is built around, the bottom two-thirds are not equals.

## Verdict

Answering the four questions from the sheet. The pictures were inspected; the numbers are
the script's.

1. **Do the top-20 look like the references?** Yes, and CLIP is unambiguous about it: all
   twenty are dark, high-contrast interiors of D with sword or red-light highlights —
   visually a continuation of the reference set, with no control anywhere near it. DINOv2's
   top 20 mixes in eight bright close-ups of the blonde character (`image_095`, `image_075`,
   `image_078`, `image_080`…), which match on *subject and framing* rather than on the
   references' palette. If style means the lighting-and-palette look, CLIP is more faithful;
   if it means shot construction, DINOv2 is. Both are defensible; they are not the same
   question.
2. **Do the controls sink?** **Yes, decisively.** 16/20 (clip), 14/20 (dino), 17/20
   (combined) land in the bottom 20, none in any top 20, and CLIP's *worst* control still
   ranks 75th of 110. The four controls outside CLIP's bottom band are displaced by dark
   near-empty frames (a sparkler, a light streak, the moon) that are not the reference style
   either. AUC 0.94–0.97 across all three modes.
3. **Is the spread wide enough to threshold on?** Yes for all three, and best where it
   matters: a single threshold classifies frame-vs-control at 93.6% (clip, t=0.6744), 90.9%
   (dino), 94.5% (combined).
4. **Which mode wins?** **CLIP**, for this notion of style — highest AUC (0.9733), the
   tightest top band, and the fewest frames falling below its best control (15 of 90 vs
   DINOv2's 46). `combined` is a close second but is not an independent option, and DINOv2
   alone drifts toward subject matter. The reversal is worth stating: on range alone DINOv2
   looks twice as good, and on the job the score is actually for it is the weakest of the
   three.

**Does style need a head of its own? No.** The shipped baseline separates a style cluster
from a foreign one at AUC 0.97, with the top band clean at 20 deep, no training, no labels
and no new column. Its failure mode is a *choice between two defensible notions of style*,
not an inability to order. Nothing here suggests a learned head would repay a trainer and a
labelling UI. Style stays on the centroid baseline; the aesthetic-rating head in roadmap
§§1–3 remains about *aesthetic quality*, which this run says nothing about.

Three findings carried into `docs/dev/style-similarity.md`, all cheap to act on: CLIP is the
default worth recommending for style matching; the three modes are two signals, not three;
and the DINOv2 per-layer picker offers nine layers that cannot be thresholded.
