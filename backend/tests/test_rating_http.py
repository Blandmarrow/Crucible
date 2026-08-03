"""`aesthetic_rating` over HTTP — the bulk write, the clear predicate, the filter.

The rating is authored data: `POST /images/bulk-rating` is the only thing that
writes it, and — crucially — the only thing that *clears* `rating_stale`. That
asymmetry is the reason the bit is separate from `scores_stale` at all, so it is
asserted here rather than left to the column comment: a quality run clears the
score bit, and nothing but a human looking again clears this one.

The parse is `utils.parse_rating_filter_param`, shared by all three listing
endpoints through `ImageFilterParams`; `test_image_select_all_scope.py` covers
the three agreeing with each other, and the 400s are asserted there too. What is
here is the endpoint's own behaviour and the `0`-means-unrated OR.
"""
import json

from sqlalchemy import select

from backend.models import Image
from backend.tests.conftest import API, api_env, run, upload_image, wait_for_job


async def _rows(env, dataset_id: str) -> dict[str, Image]:
    async with env.Session() as db:
        rows = (await db.execute(
            select(Image).where(Image.dataset_id == dataset_id)
        )).scalars().all()
    return {r.filename: r for r in rows}


def test_bulk_rating_sets_a_rating_on_a_selection(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png")
            b = await upload_image(env, ds["id"], "b.png")

            r = await env.client.post(
                f"{API}/images/bulk-rating",
                json={"dataset_id": ds["id"], "image_ids": [a["id"]], "rating": 4},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"updated": 1}

            rows = await _rows(env, ds["id"])
            assert rows["a.png"].aesthetic_rating == 4
            # Untouched, not defaulted to anything.
            assert rows["b.png"].aesthetic_rating is None
            assert b["id"]

    run(scenario())


def test_a_null_rating_clears_it(tmp_path):
    """`null` is the clear, with no sentinel: JSON already has the two meanings
    a nullable 1–4 column needs."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")

            await env.client.post(
                f"{API}/images/bulk-rating",
                json={"dataset_id": ds["id"], "image_ids": [img["id"]], "rating": 2},
            )
            r = await env.client.post(
                f"{API}/images/bulk-rating",
                json={"dataset_id": ds["id"], "image_ids": [img["id"]], "rating": None},
            )
            assert r.status_code == 200, r.text
            assert (await _rows(env, ds["id"]))["a.png"].aesthetic_rating is None

    run(scenario())


def test_rating_is_the_sole_clear_site_for_the_stale_bit(tmp_path):
    """An in-place pixel rewrite marks a rated row stale; re-rating is the only
    thing that takes the mark off — even when the value is unchanged, because
    looking again is the event the bit records.

    Clearing to NULL clears it too: no rating, nothing stale.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")

            await env.client.post(
                f"{API}/images/bulk-rating",
                json={"dataset_id": ds["id"], "image_ids": [img["id"]], "rating": 3},
            )
            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 20, "height": 10, "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            rows = await _rows(env, ds["id"])
            assert rows["a.png"].rating_stale is True
            # The rating itself survives the edit — only its currency is in doubt.
            assert rows["a.png"].aesthetic_rating == 3

            # The same value again still counts as looking again.
            await env.client.post(
                f"{API}/images/bulk-rating",
                json={"dataset_id": ds["id"], "image_ids": [img["id"]], "rating": 3},
            )
            rows = await _rows(env, ds["id"])
            assert rows["a.png"].rating_stale is False
            assert rows["a.png"].aesthetic_rating == 3

            # And clearing the rating clears the bit with it.
            await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 10, "height": 10, "maintain_ar": False},
            )
            assert (await _rows(env, ds["id"]))["a.png"].rating_stale is True
            await env.client.post(
                f"{API}/images/bulk-rating",
                json={"dataset_id": ds["id"], "image_ids": [img["id"]], "rating": None},
            )
            rows = await _rows(env, ds["id"])
            assert rows["a.png"].rating_stale is False
            assert rows["a.png"].aesthetic_rating is None

    run(scenario())


def test_an_edit_to_an_unrated_image_does_not_mark_it(tmp_path):
    """The `scores_stale` rule, restated for the rating: a row nobody judged has
    no judgement to invalidate."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")

            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 20, "height": 10, "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            assert (await _rows(env, ds["id"]))["a.png"].rating_stale is False

    run(scenario())


def test_a_scoped_selection_rates_the_whole_subfolder(tmp_path):
    """The scope triple, not just an id list — `_apply_bulk_filters` is shared
    with `bulk_provenance` and the same three shapes must reach it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png")
            await upload_image(env, ds["id"], "b.png")
            r = await env.client.post(
                f"{API}/images/batch/move-subfolder",
                json={
                    "image_ids": [i["id"] for i in (await env.client.get(
                        f"{API}/images/", params={"dataset_id": ds["id"]}
                    )).json()][:1],
                    "subfolder": "keepers",
                },
            )
            assert r.status_code == 200, r.text

            r = await env.client.post(
                f"{API}/images/bulk-rating",
                json={"dataset_id": ds["id"], "subfolder": "keepers", "rating": 4},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"updated": 1}

            rated = [
                row for row in (await _rows(env, ds["id"])).values()
                if row.aesthetic_rating == 4
            ]
            assert len(rated) == 1
            assert rated[0].subfolder == "keepers"

    run(scenario())


def test_the_rating_is_out_of_domain_at_the_schema(tmp_path):
    """1–4 and nothing else: `0` is the filter's "unrated" sentinel, not a tier,
    and must never become a stored value."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            for bad in (0, 5, -1):
                r = await env.client.post(
                    f"{API}/images/bulk-rating",
                    json={"dataset_id": ds["id"], "image_ids": [img["id"]], "rating": bad},
                )
                assert r.status_code == 422, f"rating={bad}: {r.status_code} {r.text}"

    run(scenario())


def test_the_rating_and_its_bit_are_on_both_image_payloads(tmp_path):
    """`ImageOut` (the detail page) and `ImageListItem` (the gallery card) both
    carry them — the badge and its stale marker are drawn from the list payload,
    which is paid per row."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            await env.client.post(
                f"{API}/images/bulk-rating",
                json={"dataset_id": ds["id"], "image_ids": [img["id"]], "rating": 4},
            )

            detail = (await env.client.get(f"{API}/images/{img['id']}")).json()
            assert detail["aesthetic_rating"] == 4
            assert detail["rating_stale"] is False

            listed = (await env.client.get(
                f"{API}/images/", params={"dataset_id": ds["id"]}
            )).json()[0]
            assert listed["aesthetic_rating"] == 4
            assert listed["rating_stale"] is False

    run(scenario())


# ---------------------------------------------------------------------------
# Export — the include threshold, the exclude list, and the unrated advisory
# ---------------------------------------------------------------------------


async def _rate(env, dataset_id: str, image_id: str, rating: int) -> None:
    r = await env.client.post(
        f"{API}/images/bulk-rating",
        json={"dataset_id": dataset_id, "image_ids": [image_id], "rating": rating},
    )
    assert r.status_code == 200, r.text


def test_the_export_preview_counts_the_rating_exclusions_and_the_unrated(tmp_path):
    """`rating_min` behaves like `aesthetic_min` — an unrated image has no value
    to compare and is dropped — and `exclude_ratings` behaves like
    `exclude_flags`, naming tiers and leaving the unrated alone.

    The `unrated_count`/`unrated_will_export` pair is the whole point: it is what
    turns "1 will export" into "1 will export, and 2 were never looked at".
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            keep = await upload_image(env, ds["id"], "keep.png")
            cut = await upload_image(env, ds["id"], "cut.png")
            await upload_image(env, ds["id"], "unrated_a.png")
            await upload_image(env, ds["id"], "unrated_b.png")
            await _rate(env, ds["id"], keep["id"], 4)
            await _rate(env, ds["id"], cut["id"], 1)

            async def preview(**params):
                r = await env.client.get(f"{API}/export/preview/{ds['id']}", params=params)
                assert r.status_code == 200, r.text
                return r.json()

            # No filter: everything ships, and the advisory still states the
            # untriaged population.
            p = await preview()
            assert p["will_export"] == 4
            assert p["excluded_by_rating"] == 0
            assert p["unrated_count"] == 2
            assert p["unrated_will_export"] == 2

            # The include threshold drops Cut *and* both unrated rows.
            p = await preview(rating_min=3)
            assert p["will_export"] == 1
            assert p["excluded_by_rating"] == 3
            assert p["unrated_count"] == 2
            assert p["unrated_will_export"] == 0

            # The exclude list drops only the tier it names.
            p = await preview(exclude_ratings=json.dumps([1]))
            assert p["will_export"] == 3
            assert p["excluded_by_rating"] == 1
            assert p["unrated_will_export"] == 2

    run(scenario())


def test_a_plain_export_applies_both_rating_filters(tmp_path):
    """The preview's exclusion evaluation is a *duplicate* of `_is_excluded`, not
    a call to it, so the two are asserted separately — a clause added to one and
    not the other is a preview that lies about what the export will do."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            keep = await upload_image(env, ds["id"], "keep.png")
            cut = await upload_image(env, ds["id"], "cut.png")
            await upload_image(env, ds["id"], "unrated.png")
            await _rate(env, ds["id"], keep["id"], 4)
            await _rate(env, ds["id"], cut["id"], 1)

            out = tmp_path / "out"
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"],
                "output_dir": str(out),
                "captioned_only": False,
                "rating_min": 3,
            })
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job
            assert job["result_data"]["exported"] == 1
            assert sorted(p.name for p in (out / "images").glob("*.png")) == ["keep.png"]

            out2 = tmp_path / "out2"
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"],
                "output_dir": str(out2),
                "captioned_only": False,
                "exclude_ratings": [1],
            })
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job
            # Cut is dropped; the unrated image is not — it was never named.
            assert sorted(p.name for p in (out2 / "images").glob("*.png")) == ["keep.png", "unrated.png"]

    run(scenario())


def test_the_unrated_sentinel_is_refused_by_the_export_exclude_list(tmp_path):
    """`0` means "unrated" in the gallery's `rating_filter` and is *not* a tier.
    "Drop the unrated" is what `rating_min` already says, so accepting `0` here
    would give one number two meanings across two screens."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            r = await env.client.get(
                f"{API}/export/preview/{ds['id']}", params={"exclude_ratings": json.dumps([0])}
            )
            assert r.status_code == 400, r.text
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(tmp_path / "o"), "exclude_ratings": [0],
            })
            assert r.status_code == 400, r.text

    run(scenario())


# ---------------------------------------------------------------------------
# Versioning — what a restore would cost in ratings
# ---------------------------------------------------------------------------


def test_rating_impact_counts_what_a_restore_would_revert(tmp_path):
    """A restore reverts a rating like any other mirrored column, and a rating is
    hand-made work nothing can recompute — so the confirm dialog states the
    number first.

    The three figures answer three different questions: how many change, how many
    are *cleared* (rated after the snapshot), and how many rated images the
    version does not contain at all — which under `handle_extra_images="remove"`
    are deleted rather than reverted.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png")
            b = await upload_image(env, ds["id"], "b.png")
            await _rate(env, ds["id"], a["id"], 4)

            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{ds['id']}/versions", json={"name": "v1"})
            assert r.status_code in (200, 201, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()[0]["id"]

            async def impact():
                r = await env.client.get(
                    f"{API}/datasets/{ds['id']}/versions/{version_id}/rating-impact"
                )
                assert r.status_code == 200, r.text
                return r.json()

            # Nothing has moved yet.
            assert await impact() == {"will_change": 0, "will_clear": 0, "extras_rated": 0}

            # Re-rate one, and rate one that was unrated when the snapshot ran.
            await _rate(env, ds["id"], a["id"], 1)
            await _rate(env, ds["id"], b["id"], 3)
            got = await impact()
            assert got["will_change"] == 2
            # `b` had no rating in the snapshot, so restoring it clears one.
            assert got["will_clear"] == 1
            assert got["extras_rated"] == 0

            # An image the version does not contain: deleted by a "remove"
            # restore rather than reverted, so it is counted apart.
            c = await upload_image(env, ds["id"], "c.png")
            await _rate(env, ds["id"], c["id"], 4)
            got = await impact()
            assert got["will_change"] == 2
            assert got["extras_rated"] == 1

    run(scenario())


def test_rating_impact_404s_on_another_datasets_version(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            await upload_image(env, a["id"], "a.png")
            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{a['id']}/versions", json={"name": "v1"})
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)
            version_id = (await env.client.get(f"{API}/datasets/{a['id']}/versions")).json()[0]["id"]

            r = await env.client.get(
                f"{API}/datasets/{b['id']}/versions/{version_id}/rating-impact"
            )
            assert r.status_code == 404, r.text

    run(scenario())
