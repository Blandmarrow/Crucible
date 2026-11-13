# Quality Scoring

Score every image across aesthetic, technical, watermark and NSFW metrics, and compute the embeddings [Style Similarity](style-similarity.md) compares, then filter and curate on the results.

Available from: the **Score images** sidebar item on any dataset page, and the **Score** button in the gallery selection toolbar (scoped to the selection).

## Running a scoring run

Tick the scorers you want and start the run — they execute together in one background job, so scoring for several metrics at once costs one pass over the images rather than several.

| Scorer | Cost |
|---|---|
| **Aesthetic score** — a learned aesthetic predictor (1–10), trained on human ratings; pick which model runs it, see [Choosing the aesthetic model](reading-scores.md#choosing-the-aesthetic-model) | GPU · 2.0–2.1 GB |
| **Technical · OpenCV** — blur, noise, near-uniform, color richness, saturation, brightness, duplicates | CPU only |
| **Watermark detection** — CLIP zero-shot classification for text overlays and logos | GPU · 2.1 GB |
| **Style embeddings · CLIP** — required for [style similarity](style-similarity.md) | GPU · 2.1 GB |
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

### Stopping and finishing a run

A run saves its scores as it goes, about 1,000 images at a time, one scorer after another. If a run stops partway — you cancel it, the app restarts, or the machine goes down — the images it already finished keep their scores. Only the chunk in progress is lost.

To finish the run, press **Score what's missing** beside **Run scoring**, with the same scorers ticked. Each scorer then runs only on the images that lack its result, plus any image whose scores are marked stale (see [Stale scores](reading-scores.md#stale-scores)). Images that already have a result are skipped, so the run picks up where the last one stopped. An image a scorer failed to read counts as missing too, so the button also retries those. When nothing is left, it says so and starts no job.

You can also finish a stopped run from [Logs](workspace.md#logs): its **Retry** button does the same as **Score what's missing**, with the scorers and scope that run used, so you don't need to tick them again. Like that button, it skips every image that already has a result, so it cannot finish a run that was replacing existing scores; press **Run scoring** again for that. A stopped **Re-score N with …** run is simply run again, which skips the images it already moved onto the new model.

The progress bar covers the whole run, across every scorer. The text under it counts images for the scorer running now, and says which step of how many it is on.

### VRAM after a run

When a run finishes, the models it loaded are unloaded and their VRAM is returned — including when the run is cancelled or fails partway. Only the scoring models go: a captioning or detection model you loaded for other work stays where it is.

Turn this off in **Settings → Quality Thresholds → Unload scoring models when a run finishes** if you re-score repeatedly and would rather keep the models warm than pay the reload each time. With it off, an **Unload models · N GB** button appears in the Score images panel header whenever a scoring model is resident; it names how much it will free, and disappears once it has. The button is also how you free the models left behind by uploading reference images for style similarity, and how you clean up after a cancelled run. It is disabled while a run is going, and refuses with *A scoring run is in progress* if you reach it anyway — from a second pane, say, or a tab opened after the run started. Pulling a model out from under a running scorer would fail the run. If another part of the app is using one of the scoring models at that moment, that model is left loaded and the toast says so; everything else is still freed.

Embeddings are a prerequisite, not a score: CLIP and DINOv2 embedding scorers write vectors that the [style-similarity](style-similarity.md) workflow consumes afterwards. Run them first, or nothing will be there to compare against.

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
compares those hashes and sets `is_duplicate` — and clears it from every image that is no longer a duplicate, so re-running Technical is the repair for a stale mark. Ticking Technical is what runs it. Reviewing
and clearing the groups it finds is its own page — see [Duplicate Resolution](duplicates.md).

The quality flags these scores set, when scores go stale, and choosing between the two aesthetic models are on [Reading your scores](reading-scores.md).
