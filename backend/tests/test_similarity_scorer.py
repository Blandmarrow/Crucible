"""The blend weights and the layer slicer — the two things `combined` scoring is.

`backend/ml/similarity_scorer.py` is numpy-only by design, so all of this runs on a
torch-free runner. That is the point: while `slice_layer_embedding` lived in
`dino_scorer` (which is `import torch` at module scope), the quality router's
combined branch could not even be imported in CI, and the blend weights had no
coverage of any kind.

A silent retune has no symptom a user could report — every score simply moves — so
the constants are asserted directly, and so is the fact that the two independent
implementations of the blend agree.
"""
import numpy as np
import pytest

from backend.ml.similarity_scorer import (
    DEFAULT_DINO_LAYER,
    STYLE_CLIP_WEIGHT,
    STYLE_DINO_WEIGHT,
    _LAYER_BLOB_SIZE,
    blend_scores,
    compute_combined_similarity,
    compute_style_similarity,
    slice_layer_embedding,
)

_DIM = 768
_LAYERS = 12


def _emb(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.astype(np.float16).tobytes()


def _layer_emb(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    m = rng.standard_normal((_LAYERS, _DIM)).astype(np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    return m.astype(np.float16).tobytes()


def test_the_shipped_blend_is_38_62():
    """The constants *and* that the function actually applies them.

    Asserting only the constants would pass a `blend_scores` that ignored them.
    """
    assert STYLE_CLIP_WEIGHT == 0.38
    assert STYLE_DINO_WEIGHT == 0.62
    assert blend_scores([0.5], [0.9]) == [round(0.38 * 0.5 + 0.62 * 0.9, 4)]


def test_the_weights_sum_to_one():
    """A `0.30/0.62` typo puts every score off-scale with nothing else to see."""
    assert STYLE_CLIP_WEIGHT + STYLE_DINO_WEIGHT == pytest.approx(1.0)


def test_the_default_dino_layer_is_in_range():
    assert 1 <= DEFAULT_DINO_LAYER <= _LAYERS


def test_blend_scores_matches_compute_combined_similarity():
    """The test that makes the single source of truth real.

    `compute_combined_similarity` and `routers/quality.py`'s per-layer loop are two
    implementations of one blend; both now route through `blend_scores`. This pins
    the first half of that — that delegating changed nothing.
    """
    ref_clip = [_emb(1), _emb(2)]
    cand_clip = [_emb(10), _emb(11), _emb(12)]
    ref_dino = [_emb(21), _emb(22)]
    cand_dino = [_emb(30), _emb(31), _emb(32)]

    combined = compute_combined_similarity(ref_clip, cand_clip, ref_dino, cand_dino)
    by_hand = blend_scores(
        compute_style_similarity(ref_clip, cand_clip),
        compute_style_similarity(ref_dino, cand_dino),
    )
    assert combined == by_hand


def test_blend_scores_returns_json_serialisable_floats():
    """A numpy scalar reaching the `dino_layer_scores` JSON column is not encodable."""
    clip = compute_style_similarity([_emb(1)], [_emb(2)])
    dino = compute_style_similarity([_emb(3)], [_emb(4)])
    for v in blend_scores(clip, dino):
        assert type(v) is float


def test_slice_layer_embedding_returns_the_right_slice():
    blob = _layer_emb(7)
    for layer in range(1, _LAYERS + 1):
        got = slice_layer_embedding(blob, layer)
        assert got == blob[(layer - 1) * _DIM * 2 : layer * _DIM * 2]
        assert len(got) == _DIM * 2


def test_slice_layer_embedding_rejects_a_wrong_sized_blob():
    """Travels with the moved function. A `dino_embedding` (768 dims) passed where a
    per-layer blob (12 × 768) belongs must raise rather than silently slice garbage."""
    with pytest.raises(ValueError):
        slice_layer_embedding(_emb(1), 3)
    assert len(_layer_emb(1)) == _LAYER_BLOB_SIZE


@pytest.mark.parametrize("layer", [0, 13, -1])
def test_slice_layer_embedding_rejects_an_out_of_range_layer(layer):
    with pytest.raises(ValueError):
        slice_layer_embedding(_layer_emb(1), layer)


def test_the_module_imports_no_torch():
    """The whole reason the slicer moved here. Structural, not a string search — the
    module docstring says "import torch" while explaining why it must not do it."""
    import ast
    import backend.ml.similarity_scorer as mod

    tree = ast.parse(open(mod.__file__).read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "torch" not in imported, f"similarity_scorer must stay numpy-only; imports {imported}"
