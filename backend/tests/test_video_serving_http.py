"""The /videos endpoints: listing, streaming and delete.

Range support is the one that matters and the one that is easy to lose: a
`<video>` element cannot seek without it, and it is not something the code
writes — it comes from returning a `FileResponse`. Swapping that for a
`StreamingResponse` or a hand-rolled read would silently remove seeking while
playback still appeared to work, so the 206 path is pinned here.
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.models import Image, Video
from backend.tests.conftest import API, api_env, mp4_bytes, run, upload_image, upload_video, wait_for_job

pytest.importorskip("cv2", reason="opencv is not installed")


def test_list_and_detail(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_video(env, ds["id"], "b.mp4")
            await upload_video(env, ds["id"], "a.mp4")

            listing = (await env.client.get(f"{API}/videos/", params={"dataset_id": ds["id"]})).json()
            assert [v["filename"] for v in listing] == ["a.mp4", "b.mp4"]

            detail = (await env.client.get(f"{API}/videos/{listing[0]['id']}")).json()
            assert detail["filename"] == "a.mp4"
            assert detail["original_filename"] == "a.mp4"
            assert detail["has_poster"] is True
            assert detail["deinterlace"] == ""
            assert detail["trim_start_ms"] == 0 and detail["trim_end_ms"] == 0
            assert detail["crop_x"] is None

    run(scenario())


def test_list_404s_for_an_unknown_dataset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/videos/", params={"dataset_id": "nope"})
            assert r.status_code == 404

    run(scenario())


def test_file_serves_the_whole_video_and_advertises_ranges(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            r = await env.client.get(f"{API}/videos/{video['id']}/file")
            assert r.status_code == 200, r.text
            assert r.headers["accept-ranges"] == "bytes"
            assert r.headers["content-type"] == "video/mp4"
            assert len(r.content) == video["file_size_bytes"]

    run(scenario())


def test_a_range_request_gets_a_206_with_the_right_slice(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            total = video["file_size_bytes"]

            full = (await env.client.get(f"{API}/videos/{video['id']}/file")).content
            r = await env.client.get(
                f"{API}/videos/{video['id']}/file", headers={"Range": "bytes=0-99"}
            )

            assert r.status_code == 206, r.text
            assert r.headers["content-range"] == f"bytes 0-99/{total}"
            assert r.content == full[:100]

    run(scenario())


def test_an_unsatisfiable_range_gets_a_416(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            r = await env.client.get(
                f"{API}/videos/{video['id']}/file",
                headers={"Range": f"bytes={video['file_size_bytes'] + 1000}-"},
            )
            assert r.status_code == 416

    run(scenario())


def test_mkv_gets_its_own_content_type(tmp_path):
    """mimetypes.guess_type is unreliable for .mkv across platforms, and the
    browser picks its decoder from this header."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mkv")

            r = await env.client.get(f"{API}/videos/{video['id']}/file")
            assert r.headers["content-type"] == "video/x-matroska"

    run(scenario())


def test_poster_serves_the_webp_written_at_ingest(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            r = await env.client.get(f"{API}/videos/{video['id']}/poster")
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "image/webp"
            assert r.content[:4] == b"RIFF"

    run(scenario())


def test_poster_is_backfilled_on_demand_for_a_row_that_has_none(tmp_path):
    """The Phase 0 heal path: rows that predate poster generation get one the
    first time anything looks at them, and the row is updated so the next read
    goes straight to disk."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            # Return the row to its Phase 0 shape: no poster on disk, path NULL.
            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
                Path(row.poster_path).unlink()
                row.poster_path = None
                await db.commit()

            r = await env.client.get(f"{API}/videos/{video['id']}/poster")
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "image/webp"

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
            assert row.poster_path.endswith("/videos/thumbnails/clip.webp")
            assert Path(row.poster_path).exists()

    run(scenario())


def test_poster_is_regenerated_when_the_file_vanished_from_disk(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                poster = Path((await db.execute(select(Video))).scalar_one().poster_path)
            poster.unlink()

            r = await env.client.get(f"{API}/videos/{video['id']}/poster")
            assert r.status_code == 200, r.text
            assert poster.exists()

    run(scenario())


def test_poster_404s_when_the_video_itself_cannot_be_decoded(tmp_path):
    """A poster is a nicety; a video that will not decode still has a row, and
    the endpoint says 404 rather than failing the request."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
                Path(row.poster_path).unlink()
                row.poster_path = None
                await db.commit()
                # Truncate the source: the row survives, decoding does not.
                Path(row.file_path).write_bytes(b"not a video at all")

            r = await env.client.get(f"{API}/videos/{video['id']}/poster")
            assert r.status_code == 404

            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalar_one().poster_path is None

    run(scenario())


def test_a_failed_poster_is_not_retried_on_every_request(tmp_path):
    """VideoStrip points an <img> at /poster for every card regardless of
    `has_poster`, so without a backoff an undecodable video re-runs a full cv2
    open on every render of every gallery visit."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.routers import videos as videos_router

            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
                Path(row.poster_path).unlink()
                row.poster_path = None
                await db.commit()
                Path(row.file_path).write_bytes(b"not a video at all")

            assert (await env.client.get(f"{API}/videos/{video['id']}/poster")).status_code == 404
            assert video["id"] in videos_router._poster_failures

            calls = []
            original = videos_router.generate_poster
            videos_router.generate_poster = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
            try:
                assert (await env.client.get(f"{API}/videos/{video['id']}/poster")).status_code == 404
                assert calls == [], "generation was retried while the backoff was live"
            finally:
                videos_router.generate_poster = original
                videos_router._poster_failures.pop(video["id"], None)

    run(scenario())


def test_the_backfill_does_not_steal_a_siblings_poster_stem(tmp_path):
    """Rescan can register `clip.mp4` and `clip.mkv` side by side, so healing a
    row's missing poster must resolve its stem against the siblings rather than
    re-deriving it from the video's own name — otherwise the heal overwrites the
    other video's poster and both rows point at one file."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            vdir = Path(ds["folder_path"]) / "videos"
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / "clip.mp4").write_bytes(mp4_bytes(frames=10))
            (vdir / "clip.mkv").write_bytes(mp4_bytes(frames=10))
            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            await wait_for_job(env, r.json()["job_id"])

            # Return one row to its un-postered state, keeping the sibling's.
            async with env.Session() as db:
                rows = (await db.execute(select(Video))).scalars().all()
                target = next(v for v in rows if v.filename == "clip.mkv")
                sibling_poster = next(v.poster_path for v in rows if v.filename == "clip.mp4")
                Path(target.poster_path).unlink()
                target.poster_path = None
                target_id = target.id
                await db.commit()

            sibling_bytes = Path(sibling_poster).read_bytes()
            assert (await env.client.get(f"{API}/videos/{target_id}/poster")).status_code == 200

            async with env.Session() as db:
                healed = (await db.execute(select(Video).where(Video.id == target_id))).scalar_one()
            assert healed.poster_path != sibling_poster
            assert Path(healed.poster_path).exists()
            assert Path(sibling_poster).read_bytes() == sibling_bytes, "the sibling's poster was overwritten"

    run(scenario())


def test_two_same_stem_backfills_do_not_claim_one_poster(tmp_path):
    """The concurrent form of the test above, which is how it actually happens.

    `VideoStrip` paints every card at once, so `clip.mp4` and `clip.mkv` heal in
    parallel: without the per-dataset lock both resolve their stem against the
    same empty directory, both pick `clip.webp`, and the second write clobbers
    the first — PM-007's shape, two rows pointing at one picture.

    No `threading.Barrier` to force the interleave: it would deadlock against the
    very lock under test. A 50 ms sleep inside the executor widens the window
    instead, and the assertion is on the outcome either way.
    """
    async def scenario():
        import time as _time

        from backend.routers import videos as videos_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            vdir = Path(ds["folder_path"]) / "videos"
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / "clip.mp4").write_bytes(mp4_bytes(frames=10))
            (vdir / "clip.mkv").write_bytes(mp4_bytes(frames=10))
            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            await wait_for_job(env, r.json()["job_id"])

            async with env.Session() as db:
                rows = (await db.execute(select(Video))).scalars().all()
                ids = sorted(v.id for v in rows)
                for v in rows:
                    Path(v.poster_path).unlink(missing_ok=True)
                    v.poster_path = None
                await db.commit()

            original = videos_router.generate_poster

            def slow(*a, **k):
                _time.sleep(0.05)  # executor thread, so the loop stays live
                return original(*a, **k)

            videos_router.generate_poster = slow
            try:
                responses = await asyncio.gather(
                    *(env.client.get(f"{API}/videos/{vid}/poster") for vid in ids)
                )
            finally:
                videos_router.generate_poster = original

            assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]

            async with env.Session() as db:
                healed = (await db.execute(select(Video))).scalars().all()
            posters = [v.poster_path for v in healed]
            assert len(set(posters)) == 2, f"both rows claimed one poster: {posters}"
            for p in posters:
                assert Path(p).stat().st_size > 0

    run(scenario())


def test_a_raising_poster_generator_parks_the_video_instead_of_500ing(tmp_path):
    """An exception from `generate_poster` is a False like any other.

    Letting it escape bypassed the negative cache, so every card of every
    subsequent render re-ran a full cv2 open + seek + decode — the unbounded
    cliff the backoff exists to prevent — and reported an app fault for what
    CLAUDE.md calls a nicety, never a gate.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.routers import videos as videos_router

            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
                Path(row.poster_path).unlink()
                row.poster_path = None
                await db.commit()

            calls = []
            original = videos_router.generate_poster

            def boom(*a, **k):
                calls.append(1)
                raise OSError("decoder exploded")

            videos_router.generate_poster = boom
            try:
                first = await env.client.get(f"{API}/videos/{video['id']}/poster")
                assert first.status_code == 404, first.text
                assert video["id"] in videos_router._poster_failures

                second = await env.client.get(f"{API}/videos/{video['id']}/poster")
                assert second.status_code == 404
                assert calls == [1], "generation was retried while the backoff was live"
            finally:
                videos_router.generate_poster = original
                videos_router._poster_failures.pop(video["id"], None)

            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalar_one().poster_path is None

    run(scenario())


def test_file_404s_when_the_row_outlives_the_file(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
            Path(row.file_path).unlink(missing_ok=True)

            r = await env.client.get(f"{API}/videos/{video['id']}/file")
            assert r.status_code == 404

    run(scenario())


def test_delete_removes_file_row_and_refreshes_stats(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            async with env.Session() as db:
                path = Path((await db.execute(select(Video))).scalar_one().file_path)
            assert path.exists()

            r = await env.client.delete(f"{API}/videos/{video['id']}")
            assert r.status_code == 204, r.text

            assert not path.exists()
            async with env.Session() as db:
                assert (await db.execute(select(Video))).scalars().all() == []

            detail = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert detail["video_count"] == 0
            assert detail["video_size_bytes"] == 0

    run(scenario())


def test_delete_leaves_images_alone(tmp_path):
    """Frames extracted from a video are ordinary Image rows; deleting the
    source must never destroy curated data."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "frame.png")
            video = await upload_video(env, ds["id"], "clip.mp4")

            await env.client.delete(f"{API}/videos/{video['id']}")

            async with env.Session() as db:
                assert len((await db.execute(select(Image))).scalars().all()) == 1

    run(scenario())


def test_delete_404s_for_an_unknown_video(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.delete(f"{API}/videos/nope")
            assert r.status_code == 404

    run(scenario())


def test_delete_409s_while_the_dataset_is_busy(tmp_path):
    """The file assertion is the point: delete unlinks the video and its poster
    *before* deleting the row, so a guard that ran too late would already have
    destroyed bytes by the time it raised."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.services import dataset_busy

            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")
            async with env.Session() as db:
                row = await db.get(Video, video["id"])
                path, poster = row.file_path, row.poster_path

            with dataset_busy.busy(ds["id"], "versioning"):
                r = await env.client.delete(f"{API}/videos/{video['id']}")
            assert r.status_code == 409, r.text

            assert Path(path).exists()
            assert poster and Path(poster).exists()
            async with env.Session() as db:
                assert await db.get(Video, video["id"]) is not None

    run(scenario())


def test_detail_404s_for_an_unknown_video(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/videos/nope")
            assert r.status_code == 404

    run(scenario())
