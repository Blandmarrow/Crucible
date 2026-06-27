"""Sentence-transformer tag embedder for semantic tag consolidation.

Embeds the unique-tag vocabulary of a dataset with all-MiniLM-L6-v2 and clusters
tags by cosine similarity so synonymous terms (``car`` / ``automobile``) can be
collapsed to one canonical form. Loaded/unloaded through ``model_manager`` like the
image scorers. See docs/dev/tag-consolidation.md.
"""
import logging

import numpy as np

from backend.ml import device as _device

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Bound the n^2 similarity matrix: with a normalised float32 matrix, 4000 tags is
# ~64 MB for the similarity matrix — comfortable. Larger vocabularies are truncated
# to the most frequent MAX_VOCAB tags by the service before embedding.
MAX_VOCAB = 4000


def embed_texts_sync(texts: list[str], model_entry) -> np.ndarray:
    """Encode texts into L2-normalised float32 embeddings (shape (N, 384))."""
    model = model_entry.model
    embs = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        batch_size=256,
        show_progress_bar=False,
    )
    return np.asarray(embs, dtype=np.float32)


def cluster_texts_sync(texts: list[str], embeddings: np.ndarray, threshold: float) -> list[list[int]]:
    """Group texts whose pairwise cosine similarity is >= threshold.

    Embeddings are already L2-normalised, so cosine similarity is the dot product.
    Uses union-find over the similarity graph (connected components). Returns a list
    of clusters as index lists; only clusters with more than one member are returned.
    """
    n = len(texts)
    if n < 2:
        return []
    sim = embeddings @ embeddings.T

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        row = sim[i]
        # only scan the upper triangle; np.where keeps this vectorised
        for j in np.where(row[i + 1:] >= threshold)[0]:
            union(i, i + 1 + int(j))

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [idxs for idxs in groups.values() if len(idxs) > 1]
