"""Every whole-dataset style-similarity branch keyset-pages its candidates (M9 stage 5).

`POST /quality/style-similarity` runs inside the HTTP request, so a branch that reads
every candidate's blob in one result set holds the whole dataset's embeddings at once:
about 1.8 GB at 100k images for the 18 KB per-layer blob. The two `*_all_layers`
branches were paged first; this pins the other five modes to the same helper.

The page size is shrunk to 2 over five scored images, so the keyset loop runs three
pages. Three things are checked per mode:

- **Bounded.** Every SELECT that reads an embedding column across the dataset carries a
  `LIMIT`, so no branch can load the whole set and then score it a page at a time. A
  spy on `similarity_scorer.compute_style_similarity`, which every one of these branches
  reaches (directly, or through `compute_combined_similarity`), also never sees more
  than one page of candidates in a call.
- **Short write lock.** No UPDATE runs before the last candidate SELECT. SQLite's first
  UPDATE takes the one write lock until the commit, so writing page by page would hold
  it through the whole scan and starve other writers (`_score_paginated`'s docstring).
- **Unchanged.** Each stored score matches the value computed straight from the seeded
  blobs, one candidate at a time, to within one step of the stored 4th decimal. A score
  depends only on its own vector and the reference mean, but a float32 matrix product's
  last bits vary with the batch's shape and row order, so a value on a rounding
  boundary may flip by 0.0001. Anything larger is a real change.
"""
import numpy as np
import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from backend.ml import similarity_scorer
from backend.ml.similarity_scorer import blend_scores, slice_layer_embedding
from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

_DIM = 768
_LAYERS = 12
_PAGE = 2
_SCORED = 5
# One step of the stored 4th decimal, plus slack for the float compare itself.
_ONE_STEP = 1.01e-4

# Captured before any spy is installed, so expectations never pass through it.
_cosine = similarity_scorer.compute_style_similarity


def _emb(seed: int) -> bytes:
    """A deterministic unit-norm float16 embedding blob."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.astype(np.float16).tobytes()


def _layer_emb(seed: int) -> bytes:
    """A (12, 768) per-layer blob, each layer row L2-normalized like the extractor's."""
    rng = np.random.default_rng(seed)
    m = rng.standard_normal((_LAYERS, _DIM)).astype(np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    return m.astype(np.float16).tobytes()


def _blobs(i: int) -> dict[str, bytes]:
    return {
        "clip_embedding": _emb(i),
        "dino_embedding": _emb(500 + i),
        "dino_layer_embeddings": _layer_emb(i),
    }


def _expected(mode: str, layer: int | None, ref: dict, cand: dict) -> float:
    """The score for one candidate, computed without the router."""
    def dino(blobs: dict) -> bytes:
        if layer is None:
            return blobs["dino_embedding"]
        return slice_layer_embedding(blobs["dino_layer_embeddings"], layer)

    if mode == "clip":
        return _cosine([ref["clip_embedding"]], [cand["clip_embedding"]])[0]
    if mode == "dino":
        return _cosine([dino(ref)], [dino(cand)])[0]
    return blend_scores(
        _cosine([ref["clip_embedding"]], [cand["clip_embedding"]]),
        _cosine([dino(ref)], [dino(cand)]),
    )[0]


@pytest.mark.parametrize(("mode", "layer"), [
    ("clip", None),
    ("dino", None),
    ("dino", 7),
    ("combined", None),
    ("combined", 5),
])
def test_each_branch_pages_and_scores_as_before(tmp_path, monkeypatch, mode, layer):
    import backend.routers.quality as quality

    monkeypatch.setattr(quality, "_STYLE_PAGE_SIZE", _PAGE)
    seen: list[int] = []

    def spy(reference_embeddings, candidate_embeddings, *args, **kwargs):
        seen.append(len(candidate_embeddings))
        return _cosine(reference_embeddings, candidate_embeddings, *args, **kwargs)

    monkeypatch.setattr(similarity_scorer, "compute_style_similarity", spy)
    sql: list[str] = []  # every statement the request sent, in order

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("paging")
            blobs_by_id: dict[str, dict] = {}
            for i in range(_SCORED):
                img = await upload_image(env, ds["id"], f"s{i}.png", png_bytes((i * 40, 20, 60)))
                blobs_by_id[img["id"]] = _blobs(i)
            # A sixth image with no embeddings at all: counted as skipped, never scored.
            bare = await upload_image(env, ds["id"], "bare.png", png_bytes((250, 250, 250)))
            async with env.Session() as db:
                for image_id, blobs in blobs_by_id.items():
                    img = await db.get(Image, image_id)
                    for col, value in blobs.items():
                        setattr(img, col, value)
                await db.commit()

            ref_id = next(iter(blobs_by_id))

            def record(_conn, _cursor, statement, *_args):
                sql.append(" ".join(statement.split()))

            event.listen(Engine, "before_cursor_execute", record)
            try:
                r = await env.client.post(f"{API}/quality/style-similarity", json={
                    "dataset_id": ds["id"],
                    "reference_image_ids": [ref_id],
                    "embedding_type": mode,
                    "dino_layer": layer,
                })
            finally:
                event.remove(Engine, "before_cursor_execute", record)
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == _SCORED
            assert r.json()["skipped"] == 1

            async with env.Session() as db:
                stored = dict((await db.execute(
                    select(Image.id, Image.style_similarity_score).where(Image.dataset_id == ds["id"])
                )).all())
            assert stored[bare["id"]] is None
            for image_id, blobs in blobs_by_id.items():
                expected = _expected(mode, layer, blobs_by_id[ref_id], blobs)
                assert stored[image_id] == pytest.approx(expected, abs=_ONE_STEP), image_id

    run(scenario())

    # Every candidate went through the scorer, and never more than a page at a time.
    assert seen, "the branch never reached compute_style_similarity"
    assert max(seen) <= _PAGE, seen

    # A candidate scan reads an embedding column with the dataset filter; the reference
    # lookup filters by id alone, and the count reads no blob.
    blob_cols = ("images.clip_embedding", "images.dino_embedding", "images.dino_layer_embeddings")
    scans = [
        n for n, s in enumerate(sql)
        if s.startswith("SELECT") and "images.dataset_id" in s and any(c in s for c in blob_cols)
    ]
    assert len(scans) >= 3, sql  # three pages of two, then the empty page that ends the loop
    for n in scans:
        assert " LIMIT " in sql[n], sql[n]
    writes = [n for n, s in enumerate(sql) if s.startswith("UPDATE images")]
    assert writes, sql
    assert min(writes) > max(scans), "an UPDATE ran mid-scan, holding the write lock"
