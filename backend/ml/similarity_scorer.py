"""Style similarity — the numpy-only half.

Deliberately free of `import torch`: `backend/routers/quality.py` reaches for
`slice_layer_embedding` at the head of its combined branch, and while that
function lived in `dino_scorer` (which is `import torch` at module scope) a
`combined` request on a torch-free runner was an ImportError → 500. That is why
the blend weights had no test coverage at all. Everything here is byte-slicing
and arithmetic, so it imports on any runner and the weights are assertable.

`dino_scorer` re-exports `slice_layer_embedding` and `_LAYER_BLOB_SIZE` for the
callers that already knew where they lived.
"""

import numpy as np

# The shipped blend for `embedding_type="combined"`. Two independent
# implementations read these — `compute_combined_similarity` below and the
# per-layer loop in `routers/quality.py`, which hoists the CLIP score out of the
# layer loop for speed — so they are constants rather than literals and
# `backend/tests/test_similarity_scorer.py` asserts the two agree.
STYLE_CLIP_WEIGHT = 0.38
STYLE_DINO_WEIGHT = 0.62

# The DINOv2 transformer layer the `*_all_layers` modes report as the headline
# `style_similarity_score`, and the layer the frontend pickers default to.
# `frontend/src/constants/styleModes.ts` carries the same number; the drift guard
# in `backend/tests/test_similarity_scorer.py` asserts they agree.
DEFAULT_DINO_LAYER = 12

_LAYER_BLOB_SIZE = 12 * 768 * 2  # 12 layers × 768 dims × float16


def slice_layer_embedding(blob: bytes, layer: int) -> bytes:
    """Return the 768-dim float16 bytes for a specific DINOv2 layer (1-indexed)."""
    if not (1 <= layer <= 12):
        raise ValueError(f"layer must be 1–12, got {layer}")
    if len(blob) != _LAYER_BLOB_SIZE:
        raise ValueError(f"Expected {_LAYER_BLOB_SIZE}-byte layer blob, got {len(blob)}")
    offset = (layer - 1) * 768 * 2
    return blob[offset : offset + 768 * 2]


def compute_style_similarity(
    reference_embeddings: list[bytes],
    candidate_embeddings: list[bytes],
    embedding_dim: int = 768,
) -> list[float]:
    """
    Cosine similarity of each candidate to the mean reference embedding.

    Both lists contain float16 numpy array bytes of shape (embedding_dim,).
    Returns scores in [-1, 1] — higher means more similar to the reference style.
    """
    ref_arrays = [
        np.frombuffer(b, dtype=np.float16).astype(np.float32)
        for b in reference_embeddings
    ]
    ref_matrix = np.stack(ref_arrays)       # (R, dim)
    mean_ref = ref_matrix.mean(axis=0)
    norm = float(np.linalg.norm(mean_ref))
    mean_ref = mean_ref / (norm + 1e-8)

    cand_arrays = [
        np.frombuffer(b, dtype=np.float16).astype(np.float32)
        for b in candidate_embeddings
    ]
    cand_matrix = np.stack(cand_arrays)     # (C, dim)
    scores = cand_matrix @ mean_ref         # (C,)

    return [float(round(float(s), 4)) for s in scores]


def blend_scores(
    clip_scores: list[float],
    dino_scores: list[float],
    clip_weight: float = STYLE_CLIP_WEIGHT,
    dino_weight: float = STYLE_DINO_WEIGHT,
) -> list[float]:
    """Blend two aligned score lists into one, rounded to 4 decimals.

    The arithmetic tail of `combined` scoring, and nothing else — the embeddings
    seam is not where the duplication lives. Both callers already hold the
    per-mode cosines: `compute_combined_similarity` computes them, and
    `routers/quality.py`'s `combined_all_layers` loop has them per layer with the
    CLIP half hoisted out.

    Takes **pre-rounded** 4-dp components, matching `compute_style_similarity`'s
    own output, so blending is one rounding step rather than two conventions. The
    `float(...)` wrap is load-bearing: a numpy scalar reaching the
    `dino_layer_scores` JSON column is not serialisable.
    """
    return [
        float(round(clip_weight * c + dino_weight * d, 4))
        for c, d in zip(clip_scores, dino_scores)
    ]


def compute_combined_similarity(
    reference_clip: list[bytes],
    candidate_clip: list[bytes],
    reference_dino: list[bytes],
    candidate_dino: list[bytes],
    clip_weight: float = STYLE_CLIP_WEIGHT,
    dino_weight: float = STYLE_DINO_WEIGHT,
) -> list[float]:
    """Weighted blend of CLIP and DINOv2 cosine similarities."""
    clip_scores = compute_style_similarity(reference_clip, candidate_clip)
    dino_scores = compute_style_similarity(reference_dino, candidate_dino)
    return blend_scores(clip_scores, dino_scores, clip_weight, dino_weight)
