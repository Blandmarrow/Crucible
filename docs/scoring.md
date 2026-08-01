# Quality Scoring

Score every image across aesthetic, technical, watermark, NSFW, and style-similarity metrics, then filter and curate on the results.

Available from: the **Score images** sidebar item on any dataset page, and the **Score** button in the gallery selection toolbar (scoped to the selection).

## Running a scoring run

Tick the scorers you want and start the run — they execute together in one background job, so scoring for several metrics at once costs one pass over the images rather than several.

| Scorer | Cost |
|---|---|
| **Aesthetic score · LAION** — CLIP-based aesthetic predictor (1–10), trained on human ratings | GPU · 2.1 GB |
| **Technical · OpenCV** — blur, noise, near-uniform, color richness, saturation, brightness, duplicates | CPU only |
| **Watermark detection** — CLIP zero-shot classification for text overlays and logos | GPU · 2.1 GB |
| **Style embeddings · CLIP** — required for the style-similarity workflow below | GPU · 2.1 GB |
| **DINOv2 embeddings** — object-aware embedding; usable alone or alongside CLIP | GPU · 1.2 GB |
| **DINOv2 per-layer embeds** — stores all 12 transformer layer CLS tokens; enables per-layer style similarity (only offered when DINOv2 embeddings is ticked) | GPU · 1.2 GB |
| **NSFW detection · Marqo** — ViT classifier, sets the `is_nsfw` flag | GPU · 1.0 GB |

These costs are not additive. Aesthetic, Watermark and Style embeddings are three uses of
a single CLIP ViT-L/14 load, so ticking all three costs that 2.1 GB once, not three times
over. DINOv2 and NSFW are separate models, so those do add to the total.

A **subfolder** dropdown in the page header (shown only when subfolders exist) scopes the run, so you can score one subset at a time without touching the rest of the dataset. An optional **job label** field names the run in the queue and in [Logs](workspace.md#logs).

Embeddings are a prerequisite, not a score: CLIP and DINOv2 embedding scorers write vectors that the style-similarity workflow consumes afterwards. Run them first, or nothing will be there to compare against.

## What each scorer produces

| Scorer | Metrics |
|---|---|
| **Technical** | Blur (Laplacian variance), noise (smooth-region std dev), uniformity (grayscale std dev), color, saturation, brightness (mean grayscale, 0–1) — then duplicate grouping across the dataset |
| **Aesthetic** | Aesthetic score 1–10 (LAION improved aesthetic predictor, CLIP ViT-L/14) |
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
compares those hashes and sets `is_duplicate`. Ticking Technical is what runs it.

## Style similarity

A collapsible section at the bottom of the page scores how close each image is to a set of reference images — the tool for keeping a training set stylistically consistent. Because it compares embeddings that already exist, it is CPU-only and runs immediately rather than queueing a job.

- **Embedding model** — *CLIP* for general images, *DINOv2* for object-shape similarity, or *CLIP + DINOv2* to blend both (0.38 × CLIP + 0.62 × DINOv2). All require the matching embeddings to have been computed first.
- **DINOv2 layer** — when using DINOv2 or the blend, pick which of the 12 transformer layers to compare on; each block captures increasingly abstract features. *Final* uses the standard embedding; the rest require per-layer embeddings. **All layers** scores every layer independently and stores the results side by side for comparison in the image detail view.
- **Reference images** — pick them from the dataset, or drag in local files from outside it. Local files are always embedded with CLIP, so choosing them restricts the run to the CLIP model.

Scoring writes a `style_similarity_score` per image, which the gallery and Statistics page can then filter and chart on.

### Style similarity modes

| Mode | Description |
|---|---|
| `clip` | Cosine similarity of CLIP ViT-L-14 embeddings |
| `dino` | Cosine similarity of DINOv2 final-layer (or any of 12 layers) embeddings |
| `combined` | Weighted blend: 38% CLIP + 62% DINOv2 — best overall style consistency signal |
| `dino_all_layers` / `combined_all_layers` | Score each of the 12 DINOv2 layers independently and store all results |

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

## Duplicate resolution

After a scoring run that includes duplicate detection, the Score images page groups detected duplicates into thumbnail grids, oldest first, with a green outline and a **kept** label on the one the scan chose to keep. The group header names the distance threshold actually in force, from Settings → Thresholds. Each group offers:

- **Keep best** — retains the image with the highest aesthetic score and deletes the rest. Images with no aesthetic score rank **last**, never first: unscored means unknown, not bad. If no image in the group has been scored at all, the button is disabled and says so — use *Keep first* instead
- **Keep first** — retains the image marked *kept*, which is always first in the group, and deletes the rest

Both buttons ask for confirmation on a group whose frames all came from one video — see below.

Long groups show their first ten images with a **+N more** button to reveal the rest, and the list itself shows 25 groups at a time with a **Show 25 more** button below it.

### Clearing many groups at once

A scoring run over a big dataset can produce a hundred or more groups, so the top of the panel carries the same two actions over every group at once:

- **Keep best in N groups** — applies *Keep best* to each. Groups where nothing has an aesthetic score are **skipped**, not resolved by some other rule; the button says how many it is skipping, and disables itself if that is all of them
- **Keep first in N groups** — applies *Keep first* to each

Next to them are three filters — **All**, **From one video**, and **Mixed or no video** — so you can clear the safe groups en masse and hand-check the risky ones. They appear only when at least one group is entirely frames from a single video. **The filter decides what the bulk buttons cover**: the count in each button is the number of groups matching the active filter, including the ones further down the list than you have scrolled, not the ones currently on screen.

Bulk actions always ask for confirmation. The dialog states how many images will be deleted across how many groups, which one it keeps in each, how many of those groups are entirely frames from one video, and how many were skipped for having no score. A run over many groups reports progress in the button, and if it fails partway it tells you how many groups it got through — the ones already resolved stay resolved.

### Duplicates that came from the same video

A perceptual hash cannot tell a held animation cel or a stretch of recycled footage from a redundant copy, so frames extracted from one video often land in the same group legitimately. When every frame in a group came from the same video, Crucible says so above the thumbnails and names the video, and each thumbnail shows its timestamp and shot number so you can check before deleting. Groups that mix sources label each frame with its own video instead.

They are still ordinary duplicates and both buttons still work on them — but on a same-source group each asks for confirmation first, naming the video and the timestamps it is about to delete, and saying which image it keeps: *Keep best* keeps the highest-scoring one, *Keep first* keeps the one the duplicate scan picked, which is not necessarily the best. The risk is the group's, not the ranking's — either button deletes the same frames on one click. See [Videos](video.md) for where the frames came from.
