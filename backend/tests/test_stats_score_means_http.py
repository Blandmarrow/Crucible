"""`/stats` carries the mean of each score column, as `score_means` (M9 stage 4).

StatsPage used to compute these in the browser from `/score-values`, the raw
lists with one entry per image. It now loads those lists only for a histogram
with custom edges, so the "Mean aesthetic" card and the CSV's MEAN SCORES read
`score_means` instead. What has to hold:

- each mean equals the mean of that column's raw list, so the numbers the page
  shows did not move when their source did;
- a NULL score is skipped, never counted as zero;
- a column nothing has scored is absent, which the page renders as a dash;
- a subfolder scope narrows the means the same way it narrows the lists.

Scores are written straight to the columns: scoring for real needs cv2/torch.
"""
import pytest

from backend.services.dataset_service import _STATS_SCORE_FIELDS
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


# Spelled out, not read off `_STATS_SCORE_FIELDS` alone: `/stats` and
# `/score-values` both read that tuple, so a column dropped from it would vanish
# from both and every comparison below would still pass. These are the nine keys
# the page's CSV export asks `score_means` for by name.
EXPECTED_SCORE_FIELDS = {
    "aesthetic_score", "blur_score", "noise_score", "uniformity_score",
    "watermark_score", "color_score", "saturation_score", "luminance_score",
    "style_similarity_score",
}


def test_the_score_field_list_is_the_nine_the_page_reads():
    assert set(_STATS_SCORE_FIELDS) == EXPECTED_SCORE_FIELDS
    assert len(_STATS_SCORE_FIELDS) == len(EXPECTED_SCORE_FIELDS)


async def _set(env, image_id: str, **cols) -> None:
    from backend.models import Image
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        for col, val in cols.items():
            setattr(row, col, val)
        await db.commit()


async def _get(env, dataset_id: str, endpoint: str, **params) -> dict:
    r = await env.client.get(f"{API}/datasets/{dataset_id}/{endpoint}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_every_score_mean_matches_its_raw_list(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = [
                await upload_image(env, ds["id"], f"{i}.png", png_bytes((i, i, i)))
                for i in range(1, 5)
            ]
            # Three distinct values per column, none of them round in binary, and
            # a fourth image left unscored.
            for n, img in enumerate(imgs[:3], start=1):
                await _set(env, img["id"], **{
                    f: 0.1 * n + 0.01 * i for i, f in enumerate(_STATS_SCORE_FIELDS)
                })

            means = (await _get(env, ds["id"], "stats"))["score_means"]
            lists = await _get(env, ds["id"], "score-values")

            assert set(means) == EXPECTED_SCORE_FIELDS
            for field in _STATS_SCORE_FIELDS:
                values = lists[field]
                assert len(values) == 3, field
                assert means[field] == pytest.approx(sum(values) / len(values), abs=1e-9), field

    run(scenario())


def test_a_null_score_is_skipped_and_an_unscored_column_is_absent(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes((1, 1, 1)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes((2, 2, 2)))
            await upload_image(env, ds["id"], "c.png", png_bytes((3, 3, 3)))

            # Nothing scored yet: the field is there and empty.
            assert (await _get(env, ds["id"], "stats"))["score_means"] == {}

            await _set(env, a["id"], aesthetic_score=4.0)
            await _set(env, b["id"], aesthetic_score=6.0)
            means = (await _get(env, ds["id"], "stats"))["score_means"]
            # 5.0 over the two scored images, not 10 / 3 over all three.
            assert means == {"aesthetic_score": 5.0}

            # A real 0.0 is a score, and counts.
            await _set(env, a["id"], watermark_score=0.0)
            await _set(env, b["id"], watermark_score=1.0)
            means = (await _get(env, ds["id"], "stats"))["score_means"]
            assert means["watermark_score"] == 0.5

    run(scenario())


def test_score_means_follow_the_subfolder_scope(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes((1, 1, 1)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes((2, 2, 2)))
            await _set(env, a["id"], aesthetic_score=2.0, subfolder="keep")
            await _set(env, b["id"], aesthetic_score=8.0, subfolder="other")

            whole = (await _get(env, ds["id"], "stats"))["score_means"]
            assert whole["aesthetic_score"] == 5.0

            scoped = (await _get(env, ds["id"], "stats", subfolder="keep"))["score_means"]
            assert scoped == {"aesthetic_score": 2.0}

    run(scenario())
