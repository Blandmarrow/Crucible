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
from backend.models.video import Video
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

LINEAGE_KEYS = (
    "source_video_id", "source_timestamp_ms", "source_shot_index", "source_video_name",
)


async def _seed_group(
    env,
    dataset_id: str,
    names: list[str],
    *,
    video_ids: list[str | None] | None = None,
) -> list[dict]:
    """Upload `names` and flag all but the first as duplicates of the first.

    `created_at` is forced to ascending, one minute apart in upload order, so the
    ordering assertions do not ride on same-second timestamps.

    `video_ids` — one entry per name, `None` for "not a frame" — writes frame
    lineage onto the rows. Written directly rather than by extracting: the
    lineage annotation is a read-side join, so routing these through a real
    decode would only make the core assertions cv2-gated.
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
            if video_ids and video_ids[i]:
                row.source_video_id = video_ids[i]
                row.source_timestamp_ms = 1000 * (i + 1)
                row.source_shot_index = i
        await db.commit()
    return imgs


async def _make_video(env, dataset_id: str, filename: str) -> str:
    """A bare `Video` row. No file and no decode — `get_duplicates` only reads
    `Video.filename` to resolve the annotation."""
    async with env.Session() as db:
        vid = Video(
            dataset_id=dataset_id,
            filename=filename,
            original_filename=filename,
            file_path=f"/tmp/{dataset_id}/videos/{filename}",
        )
        db.add(vid)
        await db.commit()
        return vid.id


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


# ---------------------------------------------------------------------------
# Frame lineage annotation
#
# Frames from one video — held animation cels, recycled footage, a locked-off
# shot — land inside the pHash threshold legitimately, get grouped, and *Keep
# best* deletes them with nothing on screen saying they share a source. The fix
# is read-side only: `_flag_duplicates` is untouched, because `is_duplicate`
# feeds bulk filters, the Stats flagged counts, export exclusions and the gallery
# badge, and two frames from one shot often *are* duplicates the user wants gone.
# The defect is silence, not the grouping.
# ---------------------------------------------------------------------------


def test_a_lineage_free_group_is_byte_identical_apart_from_four_nulls(tmp_path):
    """The overwhelmingly common case must not change shape. Every pre-existing
    key keeps its value and the four new ones are null — a client that ignores
    them sees exactly what it saw before."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _seed_group(env, ds["id"], ["keep.png", "copy.png"])

            groups = (await env.client.get(
                f"{API}/quality/duplicates/{ds['id']}")).json()["groups"]
            assert len(groups) == 1
            group = groups[0]
            assert [m["filename"] for m in group] == ["keep.png", "copy.png"]
            assert [m["kept"] for m in group] == [True, False]
            for m in group:
                assert all(m[k] is None for k in LINEAGE_KEYS), m

    run(scenario())


def test_an_all_same_source_group_names_the_video_on_every_row(tmp_path):
    """What the banner is rendered from. The root is fetched by a *separate*
    query from the flagged members, so the annotation has to reach both — a
    resolution that only covered the flagged rows would leave the root's
    `source_video_name` null and silently demote the group to "mixed"."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            vid = await _make_video(env, ds["id"], "clip.mp4")
            await _seed_group(
                env, ds["id"], ["a.png", "b.png", "c.png"],
                video_ids=[vid, vid, vid],
            )

            group = (await env.client.get(
                f"{API}/quality/duplicates/{ds['id']}")).json()["groups"][0]
            assert len(group) == 3
            assert {m["source_video_id"] for m in group} == {vid}
            assert {m["source_video_name"] for m in group} == {"clip.mp4"}
            # Timestamps and shot index ride along per member, which is what the
            # confirm dialog lists before deleting.
            assert [m["source_timestamp_ms"] for m in group] == [1000, 2000, 3000]
            assert [m["source_shot_index"] for m in group] == [0, 1, 2]
            # The root leads and is still unflagged — unchanged by the annotation.
            assert group[0]["kept"] is True

    run(scenario())


def test_a_mixed_group_annotates_only_its_frames(tmp_path):
    """A group holding one frame and one ordinary upload is not same-source, and
    the client renders per-thumbnail labels rather than a banner. The payload has
    to make that distinguishable: the non-frame row's four keys stay null."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            vid = await _make_video(env, ds["id"], "clip.mp4")
            await _seed_group(
                env, ds["id"], ["frame.png", "upload.png"],
                video_ids=[vid, None],
            )

            group = (await env.client.get(
                f"{API}/quality/duplicates/{ds['id']}")).json()["groups"][0]
            frame, upload = group
            assert frame["filename"] == "frame.png"
            assert frame["source_video_name"] == "clip.mp4"
            assert upload["filename"] == "upload.png"
            assert all(upload[k] is None for k in LINEAGE_KEYS)

    run(scenario())


def test_two_videos_in_one_group_each_resolve_their_own_name(tmp_path):
    """The name lookup is one batched query over the distinct ids, so a group
    spanning two videos must not collapse onto whichever came back first."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            first = await _make_video(env, ds["id"], "one.mp4")
            second = await _make_video(env, ds["id"], "two.mp4")
            await _seed_group(
                env, ds["id"], ["a.png", "b.png"], video_ids=[first, second],
            )

            group = (await env.client.get(
                f"{API}/quality/duplicates/{ds['id']}")).json()["groups"][0]
            assert [m["source_video_name"] for m in group] == ["one.mp4", "two.mp4"]

    run(scenario())


def test_a_frame_whose_video_was_deleted_reports_no_name(tmp_path):
    """`source_video_id` is `ON DELETE SET NULL`, so a frame outlives its video
    with the timestamp intact. The annotation must degrade to null rather than
    error — the timestamps are still worth showing."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            vid = await _make_video(env, ds["id"], "clip.mp4")
            await _seed_group(env, ds["id"], ["a.png", "b.png"], video_ids=[vid, vid])

            # The row goes away but the images keep pointing at the id — the
            # state a delete leaves behind on a connection without the FK pragma,
            # and the one a hand-edited DB can reach regardless.
            async with env.Session() as db:
                row = await db.get(Video, vid)
                await db.delete(row)
                await db.commit()

            group = (await env.client.get(
                f"{API}/quality/duplicates/{ds['id']}")).json()["groups"][0]
            assert all(m["source_video_name"] is None for m in group)
            assert [m["source_timestamp_ms"] for m in group] == [1000, 2000]

    run(scenario())


def test_the_response_is_still_a_list_of_lists(tmp_path):
    """Promoting a group to an object with a `same_source` field would break
    `DuplicateGroup` and every assertion above for a boolean the client derives
    in one line. Pinned so the temptation is caught in review, not in the UI."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            vid = await _make_video(env, ds["id"], "clip.mp4")
            await _seed_group(env, ds["id"], ["a.png", "b.png"], video_ids=[vid, vid])

            payload = (await env.client.get(f"{API}/quality/duplicates/{ds['id']}")).json()
            assert isinstance(payload["groups"], list)
            assert all(isinstance(g, list) for g in payload["groups"])
            assert all(isinstance(m, dict) for g in payload["groups"] for m in g)

    run(scenario())
