# Quality Scoring

Score every image across aesthetic, technical, watermark, NSFW, and style-similarity metrics, then filter and curate on the results.

Available from: the **Score images** sidebar item on any dataset page, and the **Score** button in the gallery selection toolbar (scoped to the selection).

## Running a scoring run

Tick the scorers you want and start the run — they execute together in one background job, so scoring for several metrics at once costs one pass over the images rather than several.

| Scorer | Cost |
|---|---|
| **Aesthetic score · LAION** — CLIP-based aesthetic predictor (1–10), trained on human ratings | GPU · 2.1 GB |
| **Technical · OpenCV** — blur, noise, near-uniform, color richness, duplicates | CPU only |
| **Watermark detection** — CLIP zero-shot classification for text overlays and logos | GPU · 2.1 GB |
| **Style embeddings · CLIP** — required for the style-similarity workflow below | GPU · 2.1 GB |
| **DINOv2 embeddings** — object-aware embedding; usable alone or alongside CLIP | GPU · 1.2 GB |
| **DINOv2 per-layer embeds** — stores all 12 transformer layer CLS tokens; enables per-layer style similarity (only offered when DINOv2 embeddings is ticked) | GPU · 1.2 GB |
| **NSFW detection · Marqo** — ViT classifier, sets the `is_nsfw` flag | GPU · 1.0 GB |

A **subfolder** dropdown in the page header (shown only when subfolders exist) scopes the run, so you can score one subset at a time without touching the rest of the dataset. An optional **job label** field names the run in the queue and in [Logs](workspace.md#logs).

Embeddings are a prerequisite, not a score: CLIP and DINOv2 embedding scorers write vectors that the style-similarity workflow consumes afterwards. Run them first, or nothing will be there to compare against.

## What each scorer produces

| Scorer | Metrics | GPU |
|---|---|---|
| **Technical** | Blur (Laplacian variance), noise (smooth-region std dev), uniformity (grayscale std dev), color, saturation | CPU only |
| **Aesthetic** | Aesthetic score 1–10 (LAION improved aesthetic predictor, CLIP ViT-L/14), watermark score 0–1 (CLIP zero-shot), CLIP embeddings | ~3.5 GB VRAM |
| **DINOv2** | 768-dim final-layer embedding + all 12 transformer-layer CLS tokens for per-layer style analysis | ~1.2 GB VRAM |
| **NSFW** | NSFW score 0–1 (Marqo `nsfw-image-detection-384` ViT classifier), flag `is_nsfw` | ~0.3 GB VRAM |
| **Style Similarity** | Cosine similarity against reference images using stored embeddings | CPU only |
| **Duplicate Detection** | Perceptual hash (pHash) grouping | CPU only |

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

A seventh quality flag, `has_ai_artifacts`, is not set by scoring and has no threshold. It is set automatically by the **captioning** pipeline when a generated caption contains thinking-blocks or hedging language (see [Captioning](captioning.md)), and appears alongside the scoring flags in dataset flag counts and filters.

The watermark score flags *that* an image has a watermark, not where it is — to locate the region, see [Locating watermarks](detection.md#locating-watermarks).

## Duplicate resolution

After a scoring run that includes duplicate detection, the Quality page groups detected duplicates into thumbnail grids. Each group offers:

- **Keep best** — retains the image with the highest aesthetic score and deletes the rest
- **Keep first** — retains the earliest-uploaded image and deletes the rest
