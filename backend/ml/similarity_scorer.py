import numpy as np


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


def compute_combined_similarity(
    reference_clip: list[bytes],
    candidate_clip: list[bytes],
    reference_dino: list[bytes],
    candidate_dino: list[bytes],
    clip_weight: float = 0.38,
    dino_weight: float = 0.62,
) -> list[float]:
    """Weighted blend of CLIP and DINOv2 cosine similarities."""
    clip_scores = compute_style_similarity(reference_clip, candidate_clip)
    dino_scores = compute_style_similarity(reference_dino, candidate_dino)
    return [
        float(round(clip_weight * c + dino_weight * d, 4))
        for c, d in zip(clip_scores, dino_scores)
    ]
