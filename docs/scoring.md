# Quality Scoring

Score every image across aesthetic, technical, watermark, NSFW, and style-similarity metrics, then filter and curate on the results.

Available from: the **Score images** sidebar item on any dataset page, and the **Score** button in the gallery selection toolbar (scoped to the selection).

## Running a scoring run

Tick the scorers you want and start the run — they execute together in one background job, so scoring for several metrics at once costs one pass over the images rather than several.

| Scorer | Cost |
|---|---|
| **Aesthetic score** — a learned aesthetic predictor (1–10), trained on human ratings; pick which model runs it, see below | GPU · 2.0–2.1 GB |
| **Technical · OpenCV** — blur, noise, near-uniform, color richness, saturation, brightness, duplicates | CPU only |
| **Watermark detection** — CLIP zero-shot classification for text overlays and logos | GPU · 2.1 GB |
| **Style embeddings · CLIP** — required for the style-similarity workflow below | GPU · 2.1 GB |
| **DINOv2 embeddings** — object-aware embedding; usable alone or alongside CLIP | GPU · 1.2 GB |
| **DINOv2 per-layer embeds** — stores all 12 transformer layer CLS tokens; enables per-layer style similarity (only offered when DINOv2 embeddings is ticked) | GPU · 1.2 GB |
| **NSFW detection · Marqo** — ViT classifier, sets the `is_nsfw` flag | GPU · 1.0 GB |

The CLIP-based costs are not additive. Watermark, Style embeddings and the *LAION* aesthetic
model are three uses of a single CLIP ViT-L/14 load, so ticking all three costs that 2.1 GB
once, not three times over. Picking **Aesthetic Predictor V2.5** instead takes the aesthetic
score off that shared backbone and onto its own SigLIP one, so it adds roughly 2 GB on top
whenever Watermark or Style embeddings is also ticked. DINOv2 and NSFW are separate models,
so those do add to the total.

A **subfolder** dropdown in the page header (shown only when subfolders exist) scopes the run, so you can score one subset at a time without touching the rest of the dataset. An optional **job label** field names the run in the queue and in [Logs](workspace.md#logs).

Embeddings are a prerequisite, not a score: CLIP and DINOv2 embedding scorers write vectors that the style-similarity workflow consumes afterwards. Run them first, or nothing will be there to compare against.

## What each scorer produces

| Scorer | Metrics |
|---|---|
| **Technical** | Blur (Laplacian variance), noise (smooth-region std dev), uniformity (grayscale std dev), color, saturation, brightness (mean grayscale, 0–1) — then duplicate grouping across the dataset |
| **Aesthetic** | Aesthetic score 1–10, plus a record of which model produced it |
| **Watermark** | Watermark score 0–1 (CLIP zero-shot), flag `has_watermark` |
| **Style embeddings** | 768-dim CLIP ViT-L/14 embedding per image |
| **DINOv2** | 768-dim final-layer embedding + all 12 transformer-layer CLS tokens for per-layer style analysis |
| **NSFW** | NSFW score 0–1 (Marqo `nsfw-image-detection-384` ViT classifier), flag `is_nsfw` |

The Technical scorer has gained metrics over time, and a score is only recorded by the
run that computed it. A dataset scored before a metric existed keeps every other value
but has nothing for that one, so its histogram on [Statistics](statistics.md) is empty
and its gallery filter and sort return nothing — even though the page still reports the
dataset as fully scored, because coverage is counted on blur alone. Brightness is the
current case: it arrived with video frame extraction, so anything scored before that
needs a Technical re-run. The Statistics panel says as much in place of a bare "No data".

An image whose file cannot be read — corrupt, truncated, or a format the decoder does
not handle — records **no** technical scores rather than zeros. It appears as unscored,
which is what it is; previously it was stored as a pitch-black, perfectly uniform,
out-of-focus image and counted toward the dataset's technical coverage. The same now holds
for Aesthetic, Watermark and NSFW: an image one of them could not score is left blank
instead of being recorded as a confident 0.

**Photo datasets scored before this release may need re-scoring.** Until 2026-07-30 the
GPU scorers — Aesthetic, Watermark, NSFW and the CLIP/DINOv2 embeddings — read the stored
pixels without applying a photo's EXIF *rotation* tag, while the Technical scorer always
applied it. On a library of camera or phone photos that means those scores and embeddings
describe a sideways image, and disagree with the technical scores computed in the same run.
Generated images (PNG/WebP out of ComfyUI) carry no rotation tag and are unaffected. If your
dataset is photographs, re-run the affected scorers; the embeddings matter most, since style
similarity compares them against each other.

Duplicate detection has no checkbox of its own: the perceptual hash (pHash) is computed
once when an image is imported, and the **Technical** scorer does the grouping pass that
compares those hashes and sets `is_duplicate`. Ticking Technical is what runs it. Reviewing
and clearing the groups it finds is its own page — see [Duplicate Resolution](duplicates.md).

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

## Style similarity

A collapsible section at the bottom of the page scores how close each image is to a set of reference images — the tool for keeping a training set stylistically consistent. Because it compares embeddings that already exist, it is CPU-only and runs immediately rather than queueing a job. It has its own page → [Style Similarity](style-similarity.md).

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
