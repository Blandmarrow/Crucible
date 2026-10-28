"""Tag counts are per image, not per occurrence (GPT-035).

`GET /datasets/{id}/tag-cooccurrence` and `GET /captions/dataset/{id}/tag-stats`
split each caption on commas and counted every piece, so a tag repeated k times
in one caption added k² to its co-occurrence diagonal, k to each of its pairings
and k to its tag-stats count — while `StatsPage` labels those numbers "N images".
Both now count each tag once per caption, on the exact string.
"""
from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _image_with_caption(env, dataset_id: str, name: str, caption: str) -> None:
    img = await upload_image(env, dataset_id, name, png_bytes())
    async with env.Session() as db:
        row = await db.get(Image, img["id"])
        row.caption_text = caption
        await db.commit()


async def _cooccurrence(env, dataset_id: str) -> dict:
    r = await env.client.get(f"{API}/datasets/{dataset_id}/tag-cooccurrence")
    assert r.status_code == 200, r.text
    return r.json()


async def _tag_stats(env, dataset_id: str) -> dict[str, int]:
    r = await env.client.get(f"{API}/captions/dataset/{dataset_id}/tag-stats")
    assert r.status_code == 200, r.text
    return {item["tag"]: item["count"] for item in r.json()}


def test_a_repeated_tag_counts_once_in_its_caption(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            await _image_with_caption(env, ds, "a.png", "cat, cat, blue")

            assert await _cooccurrence(env, ds) == {
                "tags": ["cat", "blue"], "matrix": [[1, 1], [1, 1]],
            }
            assert await _tag_stats(env, ds) == {"cat": 1, "blue": 1}

    run(scenario())


def test_counts_across_images_are_image_counts(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            await _image_with_caption(env, ds, "a.png", "cat, cat")
            await _image_with_caption(env, ds, "b.png", "cat, blue")

            co = await _cooccurrence(env, ds)
            assert co["tags"] == ["cat", "blue"]
            # cat is on both images; blue on one, which also has cat.
            assert co["matrix"] == [[2, 1], [1, 1]]
            assert await _tag_stats(env, ds) == {"cat": 2, "blue": 1}

    run(scenario())


def test_case_variants_stay_separate_tags(tmp_path):
    # Exact-string dedupe only: merging case variants is subsume_tags' job.
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            await _image_with_caption(env, ds, "a.png", "Cat, cat")

            assert await _tag_stats(env, ds) == {"Cat": 1, "cat": 1}
            assert (await _cooccurrence(env, ds))["matrix"] == [[1, 1], [1, 1]]

    run(scenario())
