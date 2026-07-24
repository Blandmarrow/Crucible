"""Request-level tests for the `get_dataset_stats` / `get_score_values` cache.

Both endpoints load every image row in the dataset into Python, and StatsPage
polls them live, so each call is keyed on a cheap validator query (row count +
newest `Image.updated_at`, plus `Dataset.updated_at` for stats). What has to hold:

- an unchanged dataset is served from the cache (not recomputed);
- any image write busts it, because `Image.updated_at` has an `onupdate`;
- the two payload kinds ("stats" vs "scores") never share an entry.

The cache-hit half is asserted by poisoning the cached payload with a value the
aggregation could never produce, then requesting again — a plain "same JSON twice"
assertion would pass whether or not the cache exists.
"""
from backend.services import dataset_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _stats(env, dataset_id: str) -> dict:
    r = await env.client.get(f"{API}/datasets/{dataset_id}/stats")
    assert r.status_code == 200, r.text
    return r.json()


async def _caption(env, image_id: str, text: str) -> None:
    r = await env.client.put(f"{API}/captions/image/{image_id}", json={"caption_text": text})
    assert r.status_code == 200, r.text


def test_stats_are_cached_until_an_image_changes(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())

            first = await _stats(env, ds["id"])
            assert first["image_count"] == 1
            assert first["captioned_count"] == 0

            # Poison the cached payload: a second request that recomputed would
            # report 1 again.
            dataset_service._stats_cache[(ds["id"], None, "stats")][1]["image_count"] = 4242
            assert (await _stats(env, ds["id"]))["image_count"] == 4242

            # A caption write bumps Image.updated_at → validator differs → recompute.
            await _caption(env, img["id"], "a red hat")
            third = await _stats(env, ds["id"])
            assert third["image_count"] == 1
            assert third["captioned_count"] == 1

    run(scenario())


def test_stats_cache_busts_when_an_image_is_added(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png", png_bytes((1, 1, 1)))
            assert (await _stats(env, ds["id"]))["image_count"] == 1

            await upload_image(env, ds["id"], "b.png", png_bytes((2, 2, 2)))
            assert (await _stats(env, ds["id"]))["image_count"] == 2

    run(scenario())


def test_score_values_cache_is_separate_and_busts_on_edit(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())

            r = await env.client.get(f"{API}/datasets/{ds['id']}/score-values")
            assert r.status_code == 200, r.text
            assert r.json()["caption_words"] == [0]

            # Distinct cache entry from the stats payload for the same scope.
            assert (ds["id"], None, "scores") in dataset_service._stats_cache
            assert (ds["id"], None, "stats") not in dataset_service._stats_cache

            await _caption(env, img["id"], "one two three")
            r = await env.client.get(f"{API}/datasets/{ds['id']}/score-values")
            assert r.json()["caption_words"] == [3]

    run(scenario())
