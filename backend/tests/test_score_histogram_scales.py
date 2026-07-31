"""Pin every score's histogram bucket against a value in that score's real range.

The saturation histogram shipped with `color_score`'s bucket edges — `[10, 20, 40, 60]`
on a 0–100 Hasler-Süsstrunk range — while `saturation_score` is mean HSV S on 0–1. Every
image therefore fell below the first edge, and `_bucket` returns `labels[0]` for that
rather than erroring, so the panel rendered one bar and the click-through filtered on
bounds matching every row. See `docs/dev/postmortems/PM-020-saturation-histogram-scale.md`.

The generalizable rule made executable: a score's edges belong to *that* score's numeric
range, so each one is asserted against a value taken from the middle of its range and the
bucket it must land in. A future scale mismatch between two scores that share an edge
shape fails here instead of in the UI.

Scoring for real needs cv2, which CI does not have, so the columns are written straight
through the session — the aggregation under test is pure Python over the rows.
"""
from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

# score column → (value inside that column's real range, bucket label it must produce)
SCORE_BUCKETS = {
    "blur_score":             (100.0, "80–150"),
    "noise_score":            (12.0,  "10–15"),
    "uniformity_score":       (25.0,  "20–40"),
    "color_score":            (30.0,  "20–40"),
    "saturation_score":       (0.5,   "0.4–0.6"),
    # Brightness is the other 0–1 score whose edges are fractions, and it landed
    # after this file was first written — it is exactly the neighbour the rule is
    # about, so it is pinned here rather than left for the next mismatch to find.
    "luminance_score":        (0.4,   "0.3–0.5"),
    "watermark_score":        (0.75,  "0.7–0.8"),
    "style_similarity_score": (0.35,  "0.3–0.4"),
}

DISTRIBUTION_KEY = {
    "blur_score":             "blur_distribution",
    "noise_score":            "noise_distribution",
    "uniformity_score":       "uniformity_distribution",
    "color_score":            "color_distribution",
    "saturation_score":       "saturation_distribution",
    "luminance_score":        "luminance_distribution",
    "watermark_score":        "watermark_distribution",
    "style_similarity_score": "style_similarity_distribution",
}


async def _score(env, image_id: str, **columns) -> None:
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        for col, value in columns.items():
            setattr(img, col, value)
        await db.commit()


async def _stats(env, dataset_id: str) -> dict:
    r = await env.client.get(f"{API}/datasets/{dataset_id}/stats")
    assert r.status_code == 200, r.text
    return r.json()


def test_each_score_buckets_on_its_own_numeric_range(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())

            await _score(env, img["id"], **{c: v for c, (v, _) in SCORE_BUCKETS.items()})
            stats = await _stats(env, ds["id"])

            for column, (value, label) in SCORE_BUCKETS.items():
                dist = stats[DISTRIBUTION_KEY[column]]
                assert dist == {label: 1}, (
                    f"{column}={value} landed in {dist}, expected {{{label!r}: 1}} — "
                    "the bucket edges do not match this score's numeric range"
                )

    run(scenario())


def test_saturation_spreads_across_buckets_on_the_0_to_1_scale(tmp_path):
    """The regression itself: four distinct 0–1 saturations must not collapse into one bar."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            for i, sat in enumerate([0.05, 0.15, 0.3, 0.8]):
                img = await upload_image(env, ds["id"], f"a{i}.png", png_bytes())
                await _score(env, img["id"], saturation_score=sat)

            dist = (await _stats(env, ds["id"]))["saturation_distribution"]
            assert dist == {"<0.1": 1, "0.1–0.2": 1, "0.2–0.4": 1, "0.6+": 1}, dist

    run(scenario())


def test_aesthetic_score_buckets_on_its_1_to_10_range(tmp_path):
    """`score_distribution` is a fixed three-band split, not `_bucket` — pin it too."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            for i, score in enumerate([3.0, 5.0, 8.0]):
                img = await upload_image(env, ds["id"], f"a{i}.png", png_bytes())
                await _score(env, img["id"], aesthetic_score=score)

            dist = (await _stats(env, ds["id"]))["score_distribution"]
            assert dist == {"low (0-4)": 1, "mid (4-6)": 1, "high (6-10)": 1, "unscored": 0}, dist

    run(scenario())
