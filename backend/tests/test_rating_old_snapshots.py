"""A snapshot older than the rating says nothing about it.

`VersionImageState.aesthetic_rating` keeps NULL for "not recorded" and stores
unrated as `0` — the `detections` rule (GPT-026). Read the other way, NULL as
"unrated", restoring any snapshot taken before the rating existed would clear
every rating made since. The pre-migration row is simulated by nulling a fresh
snapshot's two mirror columns, which is exactly what the migration leaves on every
existing row.
"""
from sqlalchemy import update

from backend.models import Image, VersionImageState
from backend.services import version_service
from backend.tests.conftest import API, api_env, run, upload_image, wait_for_job


async def _rate(env, dataset_id: str, image_id: str, rating: int | None) -> None:
    r = await env.client.post(
        f"{API}/images/bulk-rating",
        json={"dataset_id": dataset_id, "image_ids": [image_id], "rating": rating},
    )
    assert r.status_code == 200, r.text


async def _snapshot(env, dataset_id: str, name: str) -> str:
    async with env.Session() as db:
        ver = await version_service.create_snapshot(db, dataset_id, name, "")
        return ver.id


async def _forget_ratings(env, version_id: str) -> None:
    """Make a snapshot look like it predates the rating columns."""
    async with env.Session() as db:
        await db.execute(
            update(VersionImageState)
            .where(VersionImageState.version_id == version_id)
            .values(aesthetic_rating=None, rating_stale=None)
        )
        await db.commit()


async def _restore(env, dataset_id: str, version_id: str) -> None:
    r = await env.client.post(
        f"{API}/datasets/{dataset_id}/versions/{version_id}/restore",
        json={"handle_extra_images": "keep", "pre_restore_snapshot": False},
    )
    assert r.status_code == 200, r.text
    job = await wait_for_job(env, r.json()["job_id"])
    assert job["status"] == "completed", job


async def _rating(env, image_id: str) -> tuple[int | None, bool]:
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        return img.aesthetic_rating, img.rating_stale


async def _setup(env):
    await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
    ds = await env.create_dataset("d")
    img = await upload_image(env, ds["id"], "a.png")
    return ds["id"], img["id"]


def test_a_new_snapshot_records_unrated_as_zero(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, img_id = await _setup(env)
            vid = await _snapshot(env, ds_id, "v1")
            async with env.Session() as db:
                state = (await db.execute(
                    VersionImageState.__table__.select().where(
                        VersionImageState.version_id == vid
                    )
                )).one()
            assert (state.aesthetic_rating, state.rating_stale) == (0, False)
    run(scenario())


def test_restoring_a_snapshot_taken_while_unrated_clears_a_later_rating(tmp_path):
    """The `0` round-trip: recorded unrated is still a value a restore writes."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, img_id = await _setup(env)
            vid = await _snapshot(env, ds_id, "v1")
            await _rate(env, ds_id, img_id, 4)
            await _restore(env, ds_id, vid)
            assert await _rating(env, img_id) == (None, False)
    run(scenario())


def test_restoring_a_snapshot_older_than_the_rating_keeps_it(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, img_id = await _setup(env)
            vid = await _snapshot(env, ds_id, "v1")
            await _forget_ratings(env, vid)
            await _rate(env, ds_id, img_id, 3)
            await _restore(env, ds_id, vid)
            assert await _rating(env, img_id) == (3, False)
    run(scenario())


def test_rating_impact_does_not_count_a_row_that_recorded_nothing(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, img_id = await _setup(env)
            vid = await _snapshot(env, ds_id, "v1")
            await _forget_ratings(env, vid)
            await _rate(env, ds_id, img_id, 3)
            r = await env.client.get(f"{API}/datasets/{ds_id}/versions/{vid}/rating-impact")
            assert r.status_code == 200, r.text
            assert r.json() == {"will_change": 0, "will_clear": 0, "extras_rated": 0}
    run(scenario())


def test_the_diff_reports_a_rating_only_when_both_sides_recorded_one(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, img_id = await _setup(env)
            old = await _snapshot(env, ds_id, "old")
            await _forget_ratings(env, old)
            mid = await _snapshot(env, ds_id, "mid")
            await _rate(env, ds_id, img_id, 4)
            new = await _snapshot(env, ds_id, "new")

            async def diff(a, b):
                r = await env.client.get(
                    f"{API}/datasets/{ds_id}/versions/diff", params={"v1": a, "v2": b}
                )
                assert r.status_code == 200, r.text
                return r.json()

            # Not recorded on one side: no row, rather than a false "rated".
            assert (await diff(old, new))["modified"] == []
            # Both recorded: unrated → Keep, shown as null → 4, never 0 → 4.
            [entry] = (await diff(mid, new))["modified"]
            assert entry["changes"]["aesthetic_rating"] == {"from": None, "to": 4}
    run(scenario())


def test_an_old_snapshot_that_brings_back_older_pixels_marks_the_kept_rating_stale(tmp_path):
    """The rating is kept, but it was given about the pixels the restore replaced.

    Edited in place after the snapshot, then rated: the rating is current. The
    restore writes the snapshot's older pixels back, so the kept rating now
    describes pixels that are gone — the same statement an in-place edit makes.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, img_id = await _setup(env)
            vid = await _snapshot(env, ds_id, "v1")
            await _forget_ratings(env, vid)
            r = await env.client.post(
                f"{API}/images/{img_id}/crop",
                json={"x": 0, "y": 0, "width": 8, "height": 8, "replace": True},
            )
            assert r.status_code == 200, r.text
            await _rate(env, ds_id, img_id, 4)
            assert await _rating(env, img_id) == (4, False)

            await _restore(env, ds_id, vid)
            assert await _rating(env, img_id) == (4, True)
    run(scenario())
