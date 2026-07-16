# Quality Scoring

| Scorer | Metrics | GPU |
|---|---|---|
| **Technical** | Blur (Laplacian variance), noise (smooth-region std dev), uniformity (grayscale std dev), color, saturation | CPU only |
| **Aesthetic** | Aesthetic score 1–10 (LAION improved aesthetic predictor, CLIP ViT-L/14), watermark score 0–1 (CLIP zero-shot), CLIP embeddings | ~3.5 GB VRAM |
| **DINOv2** | 768-dim final-layer embedding + all 12 transformer-layer CLS tokens for per-layer style analysis | ~1.2 GB VRAM |
| **NSFW** | NSFW score 0–1 (Marqo `nsfw-image-detection-384` ViT classifier), flag `is_nsfw` | ~0.3 GB VRAM |
| **Style Similarity** | Cosine similarity against reference images using stored embeddings | CPU only |
| **Duplicate Detection** | Perceptual hash (pHash) grouping | CPU only |

## Style similarity modes

| Mode | Description |
|---|---|
| `clip` | Cosine similarity of CLIP ViT-L-14 embeddings |
| `dino` | Cosine similarity of DINOv2 final-layer (or any of 12 layers) embeddings |
| `combined` | Weighted blend: 38% CLIP + 62% DINOv2 — best overall style consistency signal |
| `dino_all_layers` / `combined_all_layers` | Score each of the 12 DINOv2 layers independently and store all results |

## Quality flags

Quality flags are set automatically when metrics cross thresholds (all configurable in Settings):

| Flag | Default threshold |
|---|---|
| `is_blurry` | Laplacian variance < 100 |
| `is_noisy` | Noise std dev > 15 |
| `is_uniform` | Grayscale std dev < 12 |
| `has_watermark` | CLIP watermark score ≥ 0.6 |
| `is_duplicate` | pHash Hamming distance < 8 vs another image in the dataset |
| `is_nsfw` | NSFW classifier score ≥ 0.5 |

All six thresholds are configurable in Settings — changes take effect on the next scoring run.

A seventh quality flag, `has_ai_artifacts`, is not set by scoring and has no threshold. It is set automatically by the **captioning** pipeline when a generated caption contains thinking-blocks or hedging language (see Captioning), and appears alongside the scoring flags in dataset flag counts and filters.

The scoring run can be scoped to a specific subfolder via a dropdown in the Quality page header (shown only when subfolders exist), so you can score one subset at a time without touching the rest of the dataset.

## Duplicate resolution

After a scoring run that includes duplicate detection, the Quality page groups detected duplicates into thumbnail grids. Each group offers:

- **Keep best** — retains the image with the highest aesthetic score and deletes the rest
- **Keep first** — retains the earliest-uploaded image and deletes the rest
