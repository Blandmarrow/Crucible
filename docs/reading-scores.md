# Reading your scores

What a scoring run's numbers mean once you have them: the quality flags they set, when they go stale, and why scores from the two aesthetic models cannot be compared. Running a run, and what each scorer measures, is [Quality Scoring](scoring.md).

Available from: the **Score images** page, the gallery's flag filters and thumbnail badges, the image detail page's flag row, and the [Export](export.md) preview.

## Quality flags

Quality flags are set automatically when metrics cross thresholds (all configurable in [Settings](settings.md)):

| Flag | Default threshold |
|---|---|
| `is_blurry` | Laplacian variance < 100 |
| `is_noisy` | Noise std dev > 15 |
| `is_uniform` | Grayscale std dev < 12 |
| `has_watermark` | CLIP watermark score ≥ 0.6 |
| `is_duplicate` | pHash Hamming distance < 8 vs another image in the dataset |
| `is_nsfw` | NSFW classifier score ≥ 0.5 |

All six thresholds are configurable in Settings — changes take effect on the next scoring run.

A seventh quality flag, `has_ai_artifacts`, is not set by scoring and has no threshold. It is set automatically by the **captioning** pipeline when a generated caption contains thinking-blocks or hedging language (see [Captioning](captioning.md#the-ai-artifacts-flag)), and appears alongside the scoring flags in dataset flag counts and filters.

An eighth quality flag, `gate_rejected`, is set by an automated run's quality gate on images that fail its bar (the reasons are kept beside it). It is excluded from exports by default and appears in the gallery filter and the dataset flag counts. The flag is cleared when the image leaves the `rejects` folder (moved, copied to another dataset, or its folder renamed or deleted), so an image you move back counts again; see [Automated Runs](runs.md#the-gate).

The watermark score flags *that* an image has a watermark, not where it is — to locate the region, see [Locating watermarks](detection.md#locating-watermarks).

## Stale scores

Editing an image **in place** — resize, crop, upscale, a LUT grade, cropping to a detected subject, or re-extracting a video frame at full resolution — replaces its pixels but leaves its quality scores alone. Nothing is recomputed automatically, because scoring is a job you start, so the numbers now describe an image that no longer exists. Blur is the worst offender: it is measured against a fixed threshold and depends on resolution, so a score from a 1024 px triage frame says almost nothing about the 4K frame that replaced it.

Only images that **have** been scored are marked. Resizing or cropping a fresh upload before you have ever scored it leaves it clean — there is no measurement to invalidate, so there is nothing to warn about. (Images marked by an earlier version of Crucible, which marked every in-place edit, keep their mark until you score or edit them again.)

Crucible marks scored images rather than guessing:

- An amber clock badge appears on the gallery thumbnail.
- A **Scores stale** chip appears in the flag row on the image detail page.
- The [Export](export.md) preview warns how many images are affected and how many of them the current filters will actually ship.

The warning matters most at export, where **Exclude flagged** decides what to ship using flags derived from those scores — so a stale run can drop images worth keeping and ship ones you meant to drop.

**To clear it, run scoring again** with the same checks that produced the original numbers. The mark clears only when every score the image carries has been refreshed: a watermark-only pass will not clear an image that also has a blur score, and a run with only embeddings or DINOv2 ticked measures nothing at all, so it never clears anything. Style similarity is the one exception — it is scored separately and is neither refreshed by nor blocking this.

**Embeddings are removed, not marked.** The same in-place edits — and restoring a [snapshot](versioning.md) that puts an image's old pixels back — delete the image's CLIP and DINOv2 embeddings, whether or not it has scores. Embeddings describe the old pixels just as scores do, and [Style Similarity](style-similarity.md) would otherwise keep comparing them as if they were current. An edited image is skipped by style similarity until it has embeddings again. To get them back, click **Score what's missing** with **Style embeddings · CLIP** and **DINOv2 embeddings** ticked, plus **DINOv2 per-layer embeds** if you use a per-layer or all-layers mode: it fills in only the images that lack them.

## Choosing the aesthetic model

Ticking **Aesthetic score** reveals a model picker below the scorer grid, with two options:

| Model | What it is |
|---|---|
| **LAION (CLIP ViT-L/14)** | The original predictor: LAION's `sac+logos+ava1` head over CLIP. Shares its 2.1 GB backbone with Watermark detection and Style embeddings, so ticking those alongside costs nothing extra |
| **Aesthetic Predictor V2.5 (SigLIP)** | A newer predictor over a SigLIP-so400m backbone (2.0 GB, loaded separately). Rates photographic and illustrated images more evenly than LAION |

The choice is per run and is remembered across datasets and restarts — the next run uses whatever you picked last, and **Reset to defaults** in the panel header puts it back to LAION.

**The two scales are not comparable.** Both produce a number from 1 to 10 and that is all they share: a 6.2 from one says nothing about a 6.2 from the other. Crucible therefore records which model scored each image, and a line under the picker reports the split for the current scope — *"Aesthetic coverage: 1,970 scored — 1,204 by LAION (CLIP ViT-L/14), 766 by Aesthetic Predictor V2.5 (SigLIP)"*. Beside it, when some images were scored by a different model than the one selected, a **Re-score N with …** button brings just those onto the current model. It deliberately leaves never-scored images alone; *Run scoring* is the button for those.

Nothing forces you to re-score, but a mixed dataset weakens anything that reads the number as a single ranking:

- **[Duplicate resolution](duplicates.md)** refuses. A group holding two models' scores has its *Keep best* disabled, naming both models, because ranking across the two scales and then deleting the losers destroys images on a comparison that means nothing. Bulk *Keep best* skips those groups and says how many it skipped; *Keep first* reads no score and is unaffected.
- **Export** warns. The [Export](export.md) preview reports the split, and — if you are filtering on a minimum aesthetic score — that one threshold is being applied to both scales at once.
- **Statistics** shows the split under the Aesthetic tile in Score coverage, and the aesthetic histogram there mixes both scales into one distribution.

### After the CLIP fix (November 2026)

Until November 2026, Crucible built the CLIP model with a slightly wrong setting: the wrong
activation function, the small math step inside each layer. CLIP feeds the LAION score,
watermark detection and style embeddings, so all three were a little off. On a test library,
8% of watermark flags flipped and LAION scores moved by up to a point. The update that fixes
it changes your stored results once, when you start Crucible after updating:

- **Style embeddings (CLIP) are removed.** Re-make them with **Score what's missing** and
  **Style embeddings** ticked before you run a CLIP or CLIP + DINOv2 style comparison.
- **Watermark scores and their `has_watermark` flags are removed** from every image the
  watermark check had scored. Re-run them with **Score what's missing** and **Watermark**
  ticked. Flags set by a [detection scan](detection.md#locating-watermarks) are kept.
- **LAION scores are kept but re-labelled "LAION (old build — re-score)".** Select LAION in
  the picker and press **Re-score N with LAION** to bring them onto the fixed model. That
  button re-scores every image scored by a different model, so in a dataset that also holds
  V2.5 scores it re-scores those with LAION too.

The same change applies inside your saved [versions](versioning.md), so restoring an older
snapshot never brings the old results back. Crucible copies the database to `backups/` first,
and the startup log says how many results it changed.
