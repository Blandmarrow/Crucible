"""Request-level tests for the style-run descriptor written beside every style score.

`images.style_similarity_score` is a raw cosine whose scale depends entirely on the
mode that produced it, so a `StyleSimilarityRun` row records the mode, the layer,
the references and the scope. Everything below drives the **real** POST rather than
calling the recorder directly, because the thing most worth pinning is *when* the
descriptor is written: `POST /style-similarity` is a thin wrapper whose whole reason
for existing is that the six success returns inside `_score_style_similarity` write
it once, and its failure paths — which all raise `HTTPException` — write it never.

Driving the real endpoint is possible in CI because every branch now reaches only
`backend/ml/similarity_scorer.py`, which imports numpy and nothing else.
`slice_layer_embedding` used to live in `dino_scorer` (`import torch` at module
scope), which put the per-layer and `combined` branches out of reach of a torch-free
runner — and so left the blend weights, the branch with the most arithmetic in it,
with no coverage at all.

Embeddings are seeded as float16 blobs straight through `env.Session()` — extracting
real ones needs CLIP.
"""
import numpy as np
from sqlalchemy import select

from backend.models.image import Image
from backend.models.style_run import StyleSimilarityRun
from backend.ml.similarity_scorer import (
    DEFAULT_DINO_LAYER,
    STYLE_CLIP_WEIGHT,
    STYLE_DINO_WEIGHT,
    blend_scores,
    compute_style_similarity,
    slice_layer_embedding,
)
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

_DIM = 768
_LAYERS = 12


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


async def _seed(env, image_id: str, **columns) -> None:
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        for col, value in columns.items():
            setattr(img, col, value)
        await db.commit()


async def _run_row(env, dataset_id: str) -> StyleSimilarityRun | None:
    async with env.Session() as db:
        return (await db.execute(
            select(StyleSimilarityRun).where(StyleSimilarityRun.dataset_id == dataset_id)
        )).scalar_one_or_none()


async def _rows(env, dataset_id: str) -> list[StyleSimilarityRun]:
    async with env.Session() as db:
        return list((await db.execute(
            select(StyleSimilarityRun).where(StyleSimilarityRun.dataset_id == dataset_id)
        )).scalars().all())


async def _three_clip_images(env) -> tuple[dict, list[dict]]:
    ds = await env.create_dataset("style")
    imgs = []
    for i in range(3):
        img = await upload_image(env, ds["id"], f"i{i}.png", png_bytes((i * 40, 10, 10)))
        await _seed(env, img["id"], clip_embedding=_emb(i))
        imgs.append(img)
    return ds, imgs


def test_a_clip_run_records_its_descriptor(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_clip_images(env)

            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "clip",
            })
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 3

            run_row = await _run_row(env, ds["id"])
            assert run_row is not None
            assert run_row.embedding_type == "clip"
            assert run_row.dino_layer is None
            assert run_row.reference_image_ids == [imgs[0]["id"]]
            assert run_row.reference_count == 1
            assert run_row.external_reference_count == 0
            assert run_row.scored_count == 3
            assert run_row.skipped_count == 0
            # Whole-dataset run: NULL is what tells the UI the descriptor covers
            # every scored image rather than a selection.
            assert run_row.scoped_image_count is None

    run(scenario())


def test_a_second_run_overwrites_rather_than_appends(tmp_path):
    """One descriptor per dataset — it describes the values *currently* in the column."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_clip_images(env)
            for img in imgs:
                await _seed(env, img["id"], dino_embedding=_emb(100 + hash(img["id"]) % 50))

            first = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "clip",
            })
            assert first.status_code == 200, first.text

            second = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[1]["id"], imgs[2]["id"]],
                "embedding_type": "dino",
            })
            assert second.status_code == 200, second.text

            rows = await _rows(env, ds["id"])
            assert len(rows) == 1, "the unique index on dataset_id makes this an upsert"
            assert rows[0].embedding_type == "dino"
            assert rows[0].reference_count == 2

    run(scenario())


def test_a_scoped_run_records_how_many_images_it_covered(tmp_path):
    """The SelectionToolbar path. The count is what lets the UI qualify the rest."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_clip_images(env)

            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "image_ids": [imgs[0]["id"], imgs[1]["id"]],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "clip",
            })
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 2

            run_row = await _run_row(env, ds["id"])
            assert run_row.scoped_image_count == 2
            assert run_row.scored_count == 2

    run(scenario())


def test_a_failed_run_leaves_the_previous_descriptor_intact(tmp_path):
    """The whole reason the route is a wrapper.

    A run that raised wrote no scores, so the descriptor still correctly describes
    what is in the column. Overwriting it would relabel every stored value with a
    mode that never ran.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_clip_images(env)

            ok = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "clip",
            })
            assert ok.status_code == 200, ok.text

            # No DINOv2 embeddings anywhere → the dino branch 400s before scoring.
            bad = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "dino",
            })
            assert bad.status_code == 400, bad.text

            run_row = await _run_row(env, ds["id"])
            assert run_row.embedding_type == "clip"

    run(scenario())


def test_an_unknown_embedding_type_is_422_and_records_nothing(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_clip_images(env)

            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "nonsense",
            })
            assert r.status_code == 422, r.text
            assert await _run_row(env, ds["id"]) is None

    run(scenario())


def test_dino_all_layers_records_its_mode(tmp_path):
    """Also `_score_all_layers_paginated`'s first test of any kind."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("layers")
            imgs = []
            for i in range(3):
                img = await upload_image(env, ds["id"], f"l{i}.png", png_bytes((i * 30, 20, 20)))
                await _seed(env, img["id"], dino_layer_embeddings=_layer_emb(i))
                imgs.append(img)

            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "dino_all_layers",
            })
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 3

            run_row = await _run_row(env, ds["id"])
            assert run_row.embedding_type == "dino_all_layers"
            # The layer whose value is in `style_similarity_score` — the request
            # carried no layer, but the run picked one and the descriptor says which.
            assert run_row.dino_layer == DEFAULT_DINO_LAYER
            assert run_row.scored_count == 3

            # The stored score is the default layer, and the breakdown is complete.
            async with env.Session() as db:
                img = await db.get(Image, imgs[1]["id"])
                assert sorted(img.dino_layer_scores, key=int) == [str(i) for i in range(1, 13)]
                assert img.style_similarity_score == img.dino_layer_scores[str(DEFAULT_DINO_LAYER)]

    run(scenario())


def test_a_per_layer_run_records_the_layer(tmp_path):
    """`dino_layer` is half the descriptor's meaning: below layer ~10 every score
    compresses into 0.90–0.99, so "0.94" without the layer says nothing."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("layer7")
            imgs = []
            for i in range(3):
                img = await upload_image(env, ds["id"], f"p{i}.png", png_bytes((i * 25, 40, 60)))
                await _seed(env, img["id"], dino_layer_embeddings=_layer_emb(i))
                imgs.append(img)

            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "dino",
                "dino_layer": 7,
            })
            assert r.status_code == 200, r.text

            run_row = await _run_row(env, ds["id"])
            assert run_row.embedding_type == "dino"
            assert run_row.dino_layer == 7

    run(scenario())


async def _three_combined_images(env, name: str = "combined") -> tuple[dict, list[dict]]:
    """Three images carrying CLIP, final-layer DINOv2 and per-layer DINOv2 blobs."""
    ds = await env.create_dataset(name)
    imgs = []
    for i in range(3):
        img = await upload_image(env, ds["id"], f"c{i}.png", png_bytes((i * 35, 15, 45)))
        await _seed(
            env, img["id"],
            clip_embedding=_emb(i),
            dino_embedding=_emb(500 + i),
            dino_layer_embeddings=_layer_emb(i),
        )
        imgs.append(img)
    return ds, imgs


def test_combined_all_layers_writes_the_default_layer_as_the_headline(tmp_path):
    """`style_similarity_score` is the value of one layer, and which one is a decision.

    The stored score is what the gallery meter, the Stats histogram and every score
    filter read; the other eleven layers are only visible in `DinoLayerBreakdown`.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_combined_images(env, "cal")

            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "combined_all_layers",
            })
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 3

            async with env.Session() as db:
                img = await db.get(Image, imgs[1]["id"])
                assert sorted(img.dino_layer_scores, key=int) == [str(i) for i in range(1, 13)]
                assert img.style_similarity_score == img.dino_layer_scores[str(DEFAULT_DINO_LAYER)]

    run(scenario())


def test_combined_records_the_weights_it_used(tmp_path):
    """A score is only comparable to another made at the same blend, so the weights
    are a fact about the run rather than a constant read at display time."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_combined_images(env, "recorded")

            for mode in ("combined", "combined_all_layers"):
                r = await env.client.post(f"{API}/quality/style-similarity", json={
                    "dataset_id": ds["id"],
                    "reference_image_ids": [imgs[0]["id"]],
                    "embedding_type": mode,
                })
                assert r.status_code == 200, r.text

                run_row = await _run_row(env, ds["id"])
                assert run_row.clip_weight == STYLE_CLIP_WEIGHT, mode
                assert run_row.dino_weight == STYLE_DINO_WEIGHT, mode

            # And the GET carries them — the payload is hand-built field by field,
            # so an added column is invisible rather than an error.
            g = await env.client.get(f"{API}/quality/style-similarity/{ds['id']}")
            assert g.status_code == 200, g.text
            assert g.json()["run"]["clip_weight"] == STYLE_CLIP_WEIGHT
            assert g.json()["run"]["dino_weight"] == STYLE_DINO_WEIGHT

    run(scenario())


def test_clip_and_dino_runs_leave_the_weights_null(tmp_path):
    """NULL on a non-blending mode is the correct value, not a missing one — and
    `embedding_type` is what tells it apart from a run predating the columns."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_combined_images(env, "unblended")

            for mode in ("clip", "dino", "dino_all_layers"):
                r = await env.client.post(f"{API}/quality/style-similarity", json={
                    "dataset_id": ds["id"],
                    "reference_image_ids": [imgs[0]["id"]],
                    "embedding_type": mode,
                })
                assert r.status_code == 200, r.text

                run_row = await _run_row(env, ds["id"])
                assert run_row.embedding_type == mode
                assert run_row.clip_weight is None, mode
                assert run_row.dino_weight is None, mode

    run(scenario())


def test_a_later_unblended_run_clears_the_weights(tmp_path):
    """The descriptor is overwritten, not merged. A `combined` run followed by a
    `clip` one must not leave the blend behind describing a run that did not use it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_combined_images(env, "cleared")

            for mode in ("combined", "clip"):
                r = await env.client.post(f"{API}/quality/style-similarity", json={
                    "dataset_id": ds["id"],
                    "reference_image_ids": [imgs[0]["id"]],
                    "embedding_type": mode,
                })
                assert r.status_code == 200, r.text

            run_row = await _run_row(env, ds["id"])
            assert run_row.embedding_type == "clip"
            assert run_row.clip_weight is None
            assert run_row.dino_weight is None

    run(scenario())


def test_combined_all_layers_blends_at_the_shipped_weights(tmp_path):
    """The assertion the inline literals never had.

    `combined_all_layers` re-implements the blend in `routers/quality.py` — it hoists
    the CLIP score out of the layer loop for speed — so it is a second copy of a
    number that lives in `similarity_scorer`. Nothing had ever checked the two agree.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_combined_images(env, "weights")

            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "combined_all_layers",
            })
            assert r.status_code == 200, r.text

            # Recompute layer 3 for image 1 straight from the seeded blobs.
            ref_clip, ref_layers = _emb(0), _layer_emb(0)
            cand_clip, cand_layers = _emb(1), _layer_emb(1)
            expected = blend_scores(
                compute_style_similarity([ref_clip], [cand_clip]),
                compute_style_similarity(
                    [slice_layer_embedding(ref_layers, 3)],
                    [slice_layer_embedding(cand_layers, 3)],
                ),
            )[0]

            async with env.Session() as db:
                img = await db.get(Image, imgs[1]["id"])
                assert img.dino_layer_scores["3"] == expected

    run(scenario())


def test_combined_at_a_layer_matches_the_shipped_blend(tmp_path):
    """The single-layer `combined` branch, which goes through
    `compute_combined_similarity` rather than the hoisted per-layer loop."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_combined_images(env, "one-layer")

            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "combined",
                "dino_layer": 5,
            })
            assert r.status_code == 200, r.text

            expected = blend_scores(
                compute_style_similarity([_emb(0)], [_emb(2)]),
                compute_style_similarity(
                    [slice_layer_embedding(_layer_emb(0), 5)],
                    [slice_layer_embedding(_layer_emb(2), 5)],
                ),
            )[0]

            async with env.Session() as db:
                img = await db.get(Image, imgs[2]["id"])
                assert img.style_similarity_score == expected

    run(scenario())


def test_the_final_embedding_and_layer_12_are_different_vectors(tmp_path):
    """The trap the layer picker used to hide.

    `dino_layer: null` scores against `dino_embedding` — the **post**-layernorm CLS
    token — while `dino_layer_embeddings`' layer 12 is `hidden_states[12]`,
    **pre**-layernorm. They are not the same vector, so "Layer 12" and "no layer" are
    two different runs and a picker that folds one into the other is lying. Seeded
    blobs make the point unambiguously; real ones differ for the reason above.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs = await _three_combined_images(env, "final-vs-12")

            final = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "dino",
            })
            assert final.status_code == 200, final.text
            async with env.Session() as db:
                final_score = (await db.get(Image, imgs[1]["id"])).style_similarity_score

            at_12 = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "dino",
                "dino_layer": 12,
            })
            assert at_12.status_code == 200, at_12.text
            async with env.Session() as db:
                layer_12_score = (await db.get(Image, imgs[1]["id"])).style_similarity_score

            assert final_score != layer_12_score

            # And the descriptor distinguishes them, which is the only way a reader
            # of the column can tell which run wrote it.
            run_row = await _run_row(env, ds["id"])
            assert run_row.dino_layer == 12

    run(scenario())
