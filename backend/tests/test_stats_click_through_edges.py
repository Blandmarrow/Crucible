"""A Statistics histogram bar lists exactly the images it counts (GPT-018).

The stats endpoint buckets half-open (`dataset_service._bucket`: `edge <= val <
next_edge`), and the bar click-through used to send the bucket's upper edge as
the *inclusive* `max_score` — so a value sitting exactly on an edge was counted
in one bar and listed under two. The click-through now sends the exclusive
`max_score_lt`, and file size (which has no exclusive param) sends `edge - 1`
bytes.

The `*_params` helpers below mirror `frontend/src/pages/StatsPage.tsx`'s
builders (`scoreEntries`/`mkScore`, `AESTHETIC_FILTER_MAP`, `wmEntries`/
`ssimEntries`, `mkFs`); the browser half is `frontend/e2e/stats.spec.ts`. Every
test compares a bar's count against what its click returns, so a fix that only
moved one side would still fail here.
"""
import math

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _set(env, iid, **cols):
    async with env.Session() as db:
        img = await db.get(Image, iid)
        for k, v in cols.items():
            setattr(img, k, v)
        await db.commit()


async def _seed(env, ds, column, values):
    for i, v in enumerate(values):
        img = await upload_image(env, ds, f"a{i}.png", png_bytes((i * 40 % 256, 10, 10)))
        await _set(env, img["id"], **{column: v})


async def _stats(env, ds):
    r = await env.client.get(f"{API}/datasets/{ds}/stats")
    assert r.status_code == 200, r.text
    return r.json()


async def _count(env, ds, **p):
    p = {k: v for k, v in p.items() if v is not None}
    r = await env.client.get(f"{API}/images/", params={"dataset_id": ds, "limit": 500, **p})
    assert r.status_code == 200, r.text
    return len(r.json())


# ── Mirrors of the StatsPage builders ─────────────────────────────────────────

def score_bar_params(edges, field, i):
    """`scoreEntries` / `mkScore`: min inclusive, max exclusive, top bucket open."""
    return dict(
        score_field=field,
        min_score=edges[i - 1] if i > 0 else None,
        max_score_lt=edges[i] if i < len(edges) else None,
    )


def tenths_bar_params(label, field):
    """`wmEntries` / `ssimEntries`: parse "lo–hi", and leave the 0.9–1.0 bin open."""
    lo, hi = (float(x) for x in label.split("–"))
    return dict(score_field=field, min_score=lo, max_score_lt=None if hi >= 1 else hi)


def fs_bar_params(edges_mb, i):
    """`mkFs`: whole bytes, so `mb < edge` is `bytes <= ceil(edge * 1 MiB) - 1`."""
    def edge_bytes(mb):
        return math.ceil(mb * 1_048_576)
    return dict(
        file_size_min=edge_bytes(edges_mb[i - 1]) if i > 0 else None,
        file_size_max=edge_bytes(edges_mb[i]) - 1 if i < len(edges_mb) else None,
    )


SAT_EDGES = [0.1, 0.2, 0.4, 0.6]
SAT_LABELS = ["<0.1", "0.1–0.2", "0.2–0.4", "0.4–0.6", "0.6+"]
FS_EDGES_MB = [0.1, 0.5, 1.0, 2.0, 5.0]
FS_LABELS = ["<0.1 MB", "0.1–0.5 MB", "0.5–1 MB", "1–2 MB", "2–5 MB", "5+ MB"]


async def _assert_bars_match(env, ds, dist, labels, params_for):
    clicked = {lbl: await _count(env, ds, **params_for(i)) for i, lbl in enumerate(labels) if lbl in dist}
    assert clicked == {lbl: dist[lbl] for lbl in clicked}
    return clicked


def test_a_value_on_an_edge_is_listed_by_one_bar_only(tmp_path):
    """Saturation 0.2, 0.4 and 0.6 each sit on an edge. Stats counts each in the
    bucket it opens; the inclusive `max_score` also listed it under the bucket
    below, so "0.2–0.4: 1" opened a panel of 2."""
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            await _seed(env, ds, "saturation_score", [None, 0.2, 0.4, 0.6, 1.0])
            dist = (await _stats(env, ds))["saturation_distribution"]
            assert dist["0.2–0.4"] == 1 and dist["0.4–0.6"] == 1

            clicked = await _assert_bars_match(
                env, ds, dist, SAT_LABELS,
                lambda i: score_bar_params(SAT_EDGES, "saturation_score", i),
            )
            assert clicked["0.2–0.4"] == 1 and clicked["0.4–0.6"] == 1
    run(sc())


def test_values_inside_buckets_still_match(tmp_path):
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            await _seed(env, ds, "saturation_score", [0.15, 0.3, 0.5, 0.8])
            dist = (await _stats(env, ds))["saturation_distribution"]
            await _assert_bars_match(
                env, ds, dist, SAT_LABELS,
                lambda i: score_bar_params(SAT_EDGES, "saturation_score", i),
            )
    run(sc())


def test_aesthetic_bands_do_not_overlap_at_4_and_6(tmp_path):
    """`AESTHETIC_FILTER_MAP`: low {0, <4}, mid {4, <6}, high {6, open}."""
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            await _seed(env, ds, "aesthetic_score", [3.9, 4.0, 6.0, 7.0])
            dist = (await _stats(env, ds))["score_distribution"]
            assert (dist["low (0-4)"], dist["mid (4-6)"], dist["high (6-10)"]) == (1, 1, 2)
            low = await _count(env, ds, min_score=0, max_score_lt=4)
            mid = await _count(env, ds, min_score=4, max_score_lt=6)
            high = await _count(env, ds, min_score=6)
            assert (low, mid, high) == (1, 1, 2)
    run(sc())


def test_the_top_tenths_bin_keeps_a_score_of_one(tmp_path):
    """`_watermark_bucket` folds 1.0 into "0.9–1.0", so that bar's upper edge stays
    open; an exclusive `< 1.0` would drop the image the bar counts."""
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            for column, key in (
                ("watermark_score", "watermark_distribution"),
                ("style_similarity_score", "style_similarity_distribution"),
            ):
                ds = (await env.create_dataset(column))["id"]
                await _seed(env, ds, column, [0.8, 0.9, 0.95, 1.0])
                dist = (await _stats(env, ds))[key]
                assert dist == {"0.8–0.9": 1, "0.9–1.0": 3}

                for label, n in dist.items():
                    assert await _count(env, ds, **tenths_bar_params(label, column)) == n, (column, label)
                # Why the bin is left open rather than sent as `< 1.0`.
                assert await _count(env, ds, score_field=column, min_score=0.9, max_score_lt=1.0) == 2
    run(sc())


def test_file_size_bars_split_on_the_exact_byte(tmp_path):
    """0.1 MB is 104857.6 bytes and 0.5 MB is exactly 524288: one image either side
    of each edge. File size takes no exclusive param; the bar sends `edge - 1`."""
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            await _seed(env, ds, "file_size_bytes", [104857, 104858, 524287, 524288])
            dist = (await _stats(env, ds))["file_size_distribution"]
            assert dist == {"<0.1 MB": 1, "0.1–0.5 MB": 2, "0.5–1 MB": 1}
            await _assert_bars_match(env, ds, dist, FS_LABELS, lambda i: fs_bar_params(FS_EDGES_MB, i))
    run(sc())


def test_max_score_stays_inclusive(tmp_path):
    """The exclusive bound is a new param, not a change to the one people type."""
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            await _seed(env, ds, "saturation_score", [0.2, 0.4, None])
            field = "saturation_score"
            assert await _count(env, ds, score_field=field, max_score=0.4) == 2
            assert await _count(env, ds, score_field=field, max_score_lt=0.4) == 1
            # Under `score_is_null` every bound is skipped, `max_score_lt` included
            # — the "unscored" bar must not lose its NULL rows to a stray bound.
            assert await _count(env, ds, score_field=field, score_is_null="true", max_score_lt=0.4) == 1
    run(sc())
