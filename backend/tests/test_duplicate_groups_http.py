"""Request-level tests for `GET /quality/duplicates/{dataset_id}`.

`_flag_duplicates` flags only `group[1:]` — the root that every member's
`duplicate_of` points at deliberately keeps a clean `is_duplicate`, because bulk
filters, the Stats "flagged" counts and export exclusions all read that flag and
must see only the removable copies. The listing query therefore could not see the
root at all: a 2-image pair rendered as a group of one, and neither *Keep best*
nor *Keep first* could reduce a cluster to a single image, since the image they
were meant to keep was never in the payload.

These pin the shape the fix returns: root prepended and marked `kept`, members in
`created_at` order, and the flag still off the root.
"""
from datetime import datetime, timedelta

from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _seed_group(env, dataset_id: str, names: list[str]) -> list[dict]:
    """Upload `names` and flag all but the first as duplicates of the first.

    `created_at` is forced to ascending, one minute apart in upload order, so the
    ordering assertions do not ride on same-second timestamps.
    """
    imgs = [
        await upload_image(env, dataset_id, n, png_bytes((10 * i + 5, 20, 30)))
        for i, n in enumerate(names)
    ]
    base = datetime(2026, 1, 1, 12, 0, 0)
    async with env.Session() as db:
        for i, img in enumerate(imgs):
            row = await db.get(Image, img["id"])
            row.created_at = base + timedelta(minutes=i)
            if i > 0:
                row.quality_flags = {"is_duplicate": True, "duplicate_of": imgs[0]["id"]}
        await db.commit()
    return imgs


def test_a_pair_is_a_group_of_two_led_by_the_kept_image(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _seed_group(env, ds["id"], ["keep.png", "copy.png"])

            r = await env.client.get(f"{API}/quality/duplicates/{ds['id']}")
            assert r.status_code == 200, r.text
            groups = r.json()["groups"]
            assert len(groups) == 1
            group = groups[0]

            # The whole point: two entries, not one.
            assert [m["filename"] for m in group] == ["keep.png", "copy.png"]
            assert [m["kept"] for m in group] == [True, False]
            assert group[0]["id"] == imgs[0]["id"]
            assert all("created_at" in m for m in group)

            # `Keep first` keeps group[0] and deletes the rest — one image left.
            r = await env.client.post(f"{API}/quality/duplicates/resolve", json={
                "keep_ids": [group[0]["id"]],
                "delete_ids": [m["id"] for m in group[1:]],
            })
            assert r.status_code == 204, r.text

            async with env.Session() as db:
                remaining = (await db.execute(
                    select(Image).where(Image.dataset_id == ds["id"])
                )).scalars().all()
            assert [row.filename for row in remaining] == ["keep.png"]

            assert r.status_code == 204
            assert (await env.client.get(
                f"{API}/quality/duplicates/{ds['id']}")).json()["groups"] == []

    run(scenario())


def test_members_are_ordered_by_created_at_after_the_root(tmp_path):
    """"Keep first" used to mean SQLite scan order. The root leads; the copies
    follow oldest-first."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _seed_group(env, ds["id"], ["root.png", "b.png", "c.png"])

            # Shuffle the rows' physical order so scan order cannot accidentally
            # agree with created_at: rewrite the middle row's timestamp to last.
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(
                    Image.filename == "b.png"))).scalar_one()
                row.created_at = datetime(2026, 1, 1, 13, 0, 0)
                await db.commit()

            group = (await env.client.get(
                f"{API}/quality/duplicates/{ds['id']}")).json()["groups"][0]
            assert [m["filename"] for m in group] == ["root.png", "c.png", "b.png"]

    run(scenario())


def test_the_kept_root_is_not_itself_flagged(tmp_path):
    """The marker is payload-only. Setting `is_duplicate` on the root instead
    would change what bulk filters, the Stats counts and export exclusions see."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _seed_group(env, ds["id"], ["keep.png", "copy.png"])

            group = (await env.client.get(
                f"{API}/quality/duplicates/{ds['id']}")).json()["groups"][0]
            assert group[0]["kept"] is True

            async with env.Session() as db:
                root = await db.get(Image, imgs[0]["id"])
            assert not (root.quality_flags or {}).get("is_duplicate")

    run(scenario())


def test_a_root_deleted_since_the_scan_leaves_its_copies_grouped(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _seed_group(env, ds["id"], ["gone.png", "a.png", "b.png"])

            r = await env.client.delete(f"{API}/images/{imgs[0]['id']}")
            assert r.status_code in (200, 204), r.text

            groups = (await env.client.get(
                f"{API}/quality/duplicates/{ds['id']}")).json()["groups"]
            assert len(groups) == 1
            assert [m["filename"] for m in groups[0]] == ["a.png", "b.png"]
            assert not any(m["kept"] for m in groups[0])

    run(scenario())
