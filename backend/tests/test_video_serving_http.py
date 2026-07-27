"""The /videos endpoints: listing, streaming and delete.

Range support is the one that matters and the one that is easy to lose: a
`<video>` element cannot seek without it, and it is not something the code
writes — it comes from returning a `FileResponse`. Swapping that for a
`StreamingResponse` or a hand-rolled read would silently remove seeking while
playback still appeared to work, so the 206 path is pinned here.
"""

from pathlib import Path

from sqlalchemy import select

from backend.models import Image, Video
from backend.tests.conftest import API, api_env, run, upload_image, upload_video


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
            assert detail["has_poster"] is False
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


def test_poster_404s_until_one_is_generated(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4")

            r = await env.client.get(f"{API}/videos/{video['id']}/poster")
            assert r.status_code == 404

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
