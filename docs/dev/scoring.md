# Quality scoring and flags

This file covers the per-image measurement half of quality: the quality scorers and the columns they write, the failure contract every scorer follows when it cannot measure, the flag thresholds and where they are configured, and what the frontend e2e suite reaches.

Two subjects that used to share this title now have their own files. `docs/dev/scores-stale.md` covers the `scores_stale` bit that qualifies every score documented here — one writer, one clear predicate, three rendered surfaces. `docs/dev/image-similarity.md` covers the comparisons *between* images: pHash duplicate grouping and the CLIP/DINOv2 style-similarity flow.

The scorers are loaded and evicted through the shared model manager — see `docs/dev/ml-models.md`.

## The scorers and their columns

Quality scorers and what they add to `Image`:
| Module | Columns written | Notes |
|---|---|---|
| `ml/technical_scorer.py` | `blur_score`, `noise_score`, `uniformity_score`, `color_score`, `saturation_score`, `luminance_score`; flags `is_blurry`, `is_noisy`, `is_uniform` | Pure OpenCV/numpy, no GPU |
| `ml/aesthetic_scorer.py` | `aesthetic_score` (1–10), `watermark_score` (0–1), flag `has_watermark`, `clip_embedding` (BLOB, float16) | CLIP ViT-L-14; text encoder used for zero-shot watermark; image encoder for embeddings |
| `ml/dino_scorer.py` | `dino_embedding` (BLOB, float16), `dino_layer_embeddings` (BLOB, float16) | `dino_embedding`: final-layer CLS token, 768-dim. `dino_layer_embeddings`: all 12 transformer-layer CLS tokens concatenated, 18 432 bytes (12 × 768 × float16); layer N (1-indexed) at offset `(N-1)*768*2`. `slice_layer_embedding(blob, layer)` extracts one layer's bytes. |
| `ml/nsfw_scorer.py` | `nsfw_score` (0–1, rounded to 4 dp); flag `is_nsfw` | `Marqo/nsfw-image-detection-384` ViT at 384 px, ~1.0 GB through `model_manager.load_nsfw`; threshold `nsfw_threshold` |
| `ml/similarity_scorer.py` | — | CPU-only. `compute_style_similarity(ref_bytes, cand_bytes)` — cosine similarity of candidates to mean reference. `compute_combined_similarity(ref_clip, cand_clip, ref_dino, cand_dino, clip_w=0.38, dino_w=0.62)` — weighted blend of CLIP and DINOv2 cosine similarities. |

`luminance_score` was added by migration `c4b8e6a2f107` — no index, and no backfill is possible, since the value needs pixels. It is mean grayscale normalised to 0–1 (0 = black, 1 = white), taken from the `gray` array `score_technical_sync` already computes for blur/noise/uniformity — no extra decode. It has **no quality flag**, deliberately: a score with no flag needs no `threshold_settings` column, no Settings row and no `ALLOWED_FLAG_KEYS` entry, and `is_uniform` already flags a solid black frame. It **is** mirrored on `VersionImageState`, like every `*_score` column — `c4b8e6a2f107`'s own docstring says the opposite and is superseded by `d1c7b4e9f0a3`, which added the mirror later on the same branch: nothing recomputes a technical score without a manual quality job, so a snapshot is the only record of an old one (`test_every_score_column_is_mirrored_on_version_image_state` is the guard, and `NOT_MIRRORED` in `backend/tests/test_video_lineage_mirrors.py` holds no score). The score exists for **all** images, but the question it answers is a video-frame one: a triage pass dumps hundreds of frames into a subfolder and the usable-brightness ones have to be separable from night scenes, fades to black and blown-out flashes. See `docs/dev/video-extract.md`.

The column is NULL until the technical scorer runs again, and nothing detects that: `score_coverage["technical"]` counts `blur_score` alone, so a dataset scored before the column existed reports full technical coverage beside an empty Brightness histogram. Stats carries the re-score hint that says so — see `docs/dev/statistics.md` (`score_coverage`). The same staleness applies to `color_score`/`saturation_score` on anything last scored before quality-v2, and to whatever technical column is added next.

`backend/tests/test_luminance_score_http.py` pins the **wiring** rather than the formula, which is where a new score field actually breaks: that `luminance_score` is in `_ALLOWED_SCORE_FIELDS` for both filter forms (an omission is a 400), that it appears in the `score_filters` array form, that `dataset-stats` and `score-values` carry it, and that a NULL brightness is absent from both the histogram and the filter rather than counting as 0. The formula itself is deliberately untested — it lives in `score_technical_sync`, which imports cv2 — matching the coverage shape of every other technical score. (Not for want of cv2 in CI: both workflows install `opencv-python-headless` for the video suite, per `docs/dev/image-similarity.md` § Duplicate detection. The formula is untested because a numeric assertion on a mean-grayscale value pins arithmetic, not behaviour.)

## The failure contract

**The technical scorer's failure contract is "nothing measured", not zeros.** Both branches — `cv2.imread` returning None for an unreadable file, and an exception out of the executor in `score_images_technical` — return the shared `_unmeasured()` dict: all six scores `None`, all three flags `False`, one logged warning each. A zero is a *measurement*; writing 0.0 claimed the image was pitch black, perfectly uniform, fully desaturated and (via `is_blurry=True`) out of focus, about a file no decoder ever read. The booleans stay `False` rather than `None` because `routers/quality.py` folds them into a JSON flags dict via `t.get(..., False)`, where `None` is not expressible. Every consumer is already null-safe — nullable `Float` columns assigned straight through, histogram buckets guarded on `is not None`, `get_score_values` skipping `None`, nullable pydantic schemas and TypeScript types. The visible consequence is that such a file now reads as **unscored** rather than inflating `score_coverage["technical"]` and sitting in the darkest brightness bucket; `docs/scoring.md` says so for users.

**That contract is now the file's, not just the technical scorer's.** Aesthetic, watermark and NSFW wrote `0.0` on an exception out of the executor until 2026-07-31 — an aesthetic 0.0 being outside the column's own 1–10 range, and the watermark branch logging nothing at all — which is the same defect in three more columns. All three now write NULL with a logged warning, and their booleans stay `False` for the same reason the technical scorer's do. `backend/tests/test_scorer_failure_contract.py` holds that half.

`backend/tests/test_technical_scorer_failures.py` is what holds that contract: both failure branches (an unreadable file, a missing file, and an exception raised out of the executor) against a readable control, plus a structural check that the assertion list covers **every** field the scorer writes — so a seventh score column added without a null default fails there rather than in a histogram. Three of the five cases call `score_technical_sync` for real and carry `@needs_cv2`; the executor branch monkeypatches the scorer to raise before it imports anything, and the structural check only reads `_unmeasured()`, so those two need no decoder at all. All five run in CI, which installs cv2 — the marker is for a local venv without it.

## Flag thresholds

Flag thresholds:
| Flag | Column | Default threshold | Source |
|---|---|---|---|
| `is_blurry` | `blur_score` (Laplacian variance) | < 100 | `blur_threshold` in `threshold_settings` DB table |
| `is_noisy` | `noise_score` (smooth-region std dev) | > 15 | `noise_threshold` in `threshold_settings` DB table |
| `is_uniform` | `uniformity_score` (grayscale std dev) | < 12 | `uniformity_threshold` in `threshold_settings` DB table |
| `has_watermark` | `watermark_score` (CLIP zero-shot, 0–1) | ≥ 0.6 | `watermark_threshold` in `threshold_settings` DB table |
| `is_duplicate` | `phash` (perceptual hash Hamming distance) | < 8 | `duplicate_threshold` in `threshold_settings` DB table |
| `is_nsfw` | `nsfw_score` (Marqo classifier, 0–1) | ≥ 0.5 | `nsfw_threshold` in `threshold_settings` DB table |

`gdino_threshold` (default 0.35) in `threshold_settings` controls the Grounding DINO box confidence cutoff passed to SAM2 `text_prompt` detection — read via `get_thresholds()` at the start of each SAM2 detection job. `text_threshold` scales with it (`gdino_threshold - 0.10`, floored at 0.01).

`sam3_threshold` (default 0.5) in `threshold_settings` is the SAM 3 instance confidence cutoff — read at the start of each SAM3 detection job and applied as `Sam3Processor.confidence_threshold` (plus a defensive per-instance filter in `predict_sync`).

All thresholds are user-configurable via Settings (`/settings` → `GET/PATCH /api/v1/settings/thresholds`). Quality flag thresholds take effect on the next scoring run; `gdino_threshold`/`sam3_threshold` take effect on the next SAM2/SAM3 detection run. Constants in `technical_scorer.py` serve only as parameter defaults — the quality router always passes DB-fetched values via `backend/services/threshold_service.py::get_thresholds()`.

## Frontend coverage

**Frontend coverage**: `frontend/e2e/quality.spec.ts` drives the GPU-free half of `QualityPage` — the three panels, the default scoring selection (aesthetic + technical on), the DINOv2-conditional per-layer row, the subfolder scope select, and that the duplicates query resolves. It never clicks *Run scoring*: the e2e workflow installs cv2 (it is the video ingest gate) but **not** torch, and the default selection includes aesthetic scoring, so that click would fail the job body rather than the page.
