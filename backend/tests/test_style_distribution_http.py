"""Request-level tests for `GET /quality/style-similarity/{dataset_id}`.

The payload is what makes a raw style cosine readable: 21 breakpoints over the
dataset's own scores, so a client can place one image inside its own distribution
without knowing which of the five modes produced the numbers.

Two of the cases below exist to generate the exact degenerate inputs the frontend's
`percentileOf` guards are written for — one scored image (21 identical breakpoints)
and three scores (repeated breakpoints). There is no JS test runner in this repo,
so pinning the shapes from this side is the closest the guards get to a unit test:
if the arithmetic here ever stops producing them, the guards stop being exercised
by anything at all.

Scores are written straight through the session (`test_score_histogram_scales.py`'s
approach — real scoring needs CLIP), except in the cache-busting test, which drives
the real POST on purpose.
"""
import numpy as np

from backend.models.image import Image
from backend.services import dataset_service
from backend.services.dataset_service import STYLE_QUANTILE_COUNT
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


def _emb(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(768).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.astype(np.float16).tobytes()


async def _seed(env, image_id: str, **columns) -> None:
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        for col, value in columns.items():
            setattr(img, col, value)
        await db.commit()


async def _dist(env, dataset_id: str) -> dict:
    r = await env.client.get(f"{API}/quality/style-similarity/{dataset_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def _scored_dataset(env, scores: list[float], unscored: int = 0) -> dict:
    ds = await env.create_dataset("d")
    for i, s in enumerate(scores):
        img = await upload_image(env, ds["id"], f"s{i}.png", png_bytes((i * 7 % 255, 10, 10)))
        await _seed(env, img["id"], style_similarity_score=s)
    for i in range(unscored):
        await upload_image(env, ds["id"], f"u{i}.png", png_bytes((5, i * 9 % 255, 10)))
    return ds


def test_quantiles_span_the_scored_values_and_ascend(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            scores = [round(0.30 + i * 0.01, 4) for i in range(40)]
            ds = await _scored_dataset(env, scores)

            payload = await _dist(env, ds["id"])
            q = payload["quantiles"]
            assert len(q) == STYLE_QUANTILE_COUNT == 21
            assert payload["quantile_step"] == 5
            # q0 and q100 are exactly min and max — the contract the client's
            # clamp is defined against.
            assert q[0] == min(scores)
            assert q[-1] == max(scores)
            assert q == sorted(q)
            assert payload["scored"] == 40
            assert payload["total"] == 40
            assert payload["run"] is None

    run(scenario())


def test_unscored_images_are_counted_but_excluded_from_the_quantiles(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await _scored_dataset(env, [0.2, 0.4, 0.6, 0.8], unscored=3)

            payload = await _dist(env, ds["id"])
            assert payload["scored"] == 4
            assert payload["total"] == 7
            assert payload["quantiles"][0] == 0.2
            assert payload["quantiles"][-1] == 0.8

    run(scenario())


def test_an_unknown_or_empty_dataset_returns_an_empty_payload_not_a_404(tmp_path):
    """Like its sibling `aesthetic-coverage`: the caller is a gallery card, not a
    navigation, and a 404 would turn a normal never-scored dataset into an error."""
    async def scenario():
        async with api_env(tmp_path) as env:
            empty = await env.create_dataset("empty")
            payload = await _dist(env, empty["id"])
            assert payload == {
                "scored": 0, "total": 0, "quantiles": [], "quantile_step": 5, "run": None,
            }

            unknown = await _dist(env, "no-such-dataset")
            assert unknown["scored"] == 0
            assert unknown["quantiles"] == []
            assert unknown["run"] is None

    run(scenario())


def test_one_scored_image_yields_21_identical_breakpoints(tmp_path):
    """A degenerate distribution the client must *suppress* the meter for: every
    breakpoint is the same number, so no score can be placed against it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await _scored_dataset(env, [0.71])

            q = (await _dist(env, ds["id"]))["quantiles"]
            assert len(q) == 21
            assert set(q) == {0.71}

    run(scenario())


def test_three_scores_yield_repeated_breakpoints(tmp_path):
    """Fewer scored images than breakpoints is normal, not an error — the array
    repeats, and the client answers with the low edge of each flat run."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await _scored_dataset(env, [0.1, 0.5, 0.9])

            q = (await _dist(env, ds["id"]))["quantiles"]
            assert len(q) == 21
            assert set(q) == {0.1, 0.5, 0.9}
            assert q[0] == 0.1
            assert q[-1] == 0.9
            assert q == sorted(q)

    run(scenario())


def test_the_payload_is_cached_until_something_changes(tmp_path):
    """Asserted by poisoning the cached payload — a "same JSON twice" check would
    pass whether or not the cache exists."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await _scored_dataset(env, [0.2, 0.4, 0.6])
            assert (await _dist(env, ds["id"]))["scored"] == 3

            dataset_service._stats_cache[(ds["id"], None, "style")][1]["scored"] = 4242
            assert (await _dist(env, ds["id"]))["scored"] == 4242

            # Any image write moves `Image.updated_at`, so the validator differs.
            img = await upload_image(env, ds["id"], "extra.png", png_bytes((3, 3, 3)))
            await _seed(env, img["id"], style_similarity_score=0.8)
            assert (await _dist(env, ds["id"]))["scored"] == 4

    run(scenario())


def test_the_style_slot_does_not_collide_with_stats_or_scores(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await _scored_dataset(env, [0.3, 0.6])

            await _dist(env, ds["id"])
            assert (ds["id"], None, "style") in dataset_service._stats_cache
            assert (ds["id"], None, "stats") not in dataset_service._stats_cache
            assert (ds["id"], None, "scores") not in dataset_service._stats_cache

            assert (await env.client.get(f"{API}/datasets/{ds['id']}/stats")).status_code == 200
            assert (await env.client.get(f"{API}/datasets/{ds['id']}/score-values")).status_code == 200
            for slot in ("style", "stats", "scores"):
                assert (ds["id"], None, slot) in dataset_service._stats_cache
            # The style payload survived the other two being computed.
            assert (await _dist(env, ds["id"]))["scored"] == 2

    run(scenario())


def test_a_real_style_run_busts_the_cache_and_lands_in_the_payload(tmp_path):
    """End-to-end pin on the fact the cache design rests on: `db.execute(update(Image),
    [...])` *does* apply `Image.updated_at`'s Python-side `onupdate` per row, so a
    Core executemany moves the validator exactly like an ORM write."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = []
            for i in range(4):
                img = await upload_image(env, ds["id"], f"c{i}.png", png_bytes((i * 20, 5, 5)))
                await _seed(env, img["id"], clip_embedding=_emb(i))
                imgs.append(img)

            before = await _dist(env, ds["id"])
            assert before["scored"] == 0
            assert before["quantiles"] == []
            assert before["run"] is None

            r = await env.client.post(f"{API}/quality/style-similarity", json={
                "dataset_id": ds["id"],
                "reference_image_ids": [imgs[0]["id"]],
                "embedding_type": "clip",
            })
            assert r.status_code == 200, r.text

            after = await _dist(env, ds["id"])
            assert after["scored"] == 4
            assert len(after["quantiles"]) == 21
            assert after["run"]["embedding_type"] == "clip"
            assert after["run"]["reference_image_ids"] == [imgs[0]["id"]]
            assert after["run"]["scoped_image_count"] is None
            assert after["run"]["updated_at"]

    run(scenario())
