"""`GET /images/?source_video_id=` — the "frames from video X" gallery filter.

Its whole reason to exist is that the subfolder an extraction landed in stops
being a handle the moment curation moves a frame, while the lineage column does
not move. So the test that matters here moves a frame *out* of its extraction
subfolder and asserts the filter still finds it.

The other half is the FK's `ondelete="SET NULL"`: deleting a source video must
drop its frames from this filter without destroying the rows themselves — a frame
outlives its video, it just stops being addressable this way.
"""
import pytest
from sqlalchemy import select

from backend.models import Image
from backend.services import video_extract
from backend.tests.conftest import (
    API,
    api_env,
    mp4_shots_bytes,
    run,
    upload_image,
    upload_video,
    wait_for_job,
)

pytestmark = pytest.mark.skipif(
    not video_extract.capabilities()["shot_detection"],
    reason="scenedetect is not installed",
)

SHOTS_MP4 = mp4_shots_bytes()


async def _extract_and_wait(env, video_id: str):
    r = await env.client.post(f"{API}/videos/extract", json={"video_ids": [video_id]})
    assert r.status_code == 200, r.text
    body = r.json()
    for j in body["jobs"]:
        job = await wait_for_job(env, j["job_id"], timeout=120)
        assert job["status"] == "completed", job
    return body


async def _filtered_ids(env, dataset_id: str, video_id: str) -> list[str]:
    r = await env.client.get(
        f"{API}/images/", params={"dataset_id": dataset_id, "source_video_id": video_id}
    )
    assert r.status_code == 200, r.text
    return sorted(i["id"] for i in r.json())


def test_the_filter_survives_a_move_to_another_subfolder(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            await _extract_and_wait(env, video["id"])

            async with env.Session() as db:
                frames = (await db.execute(select(Image).order_by(Image.filename))).scalars().all()
                frame_ids = sorted(f.id for f in frames)
            assert len(frame_ids) == 3

            assert await _filtered_ids(env, ds["id"], video["id"]) == frame_ids

            # Curate: move one frame out of the extraction subfolder entirely.
            r = await env.client.post(
                f"{API}/images/batch/move-subfolder",
                json={"image_ids": [frame_ids[0]], "subfolder": "keepers", "rename_on_move": True},
            )
            assert r.status_code == 200, r.text

            # The subfolder that answered "where did this extraction land" now
            # holds two of the three. The lineage filter still holds all three.
            r = await env.client.get(
                f"{API}/images/", params={"dataset_id": ds["id"], "subfolder": "clip"}
            )
            assert len(r.json()) == 2
            assert await _filtered_ids(env, ds["id"], video["id"]) == frame_ids

    run(scenario())


def test_an_unknown_video_id_returns_no_rows(tmp_path):
    """No allowlist guards the value — it is an opaque uuid, and an id that
    matches nothing is a correct empty answer, not a 400."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png")

            r = await env.client.get(
                f"{API}/images/",
                params={"dataset_id": ds["id"], "source_video_id": "no-such-video"},
            )
            assert r.status_code == 200, r.text
            assert r.json() == []

            # An empty value is not a filter at all — the plain image is back.
            r = await env.client.get(
                f"{API}/images/", params={"dataset_id": ds["id"], "source_video_id": ""}
            )
            assert len(r.json()) == 1

    run(scenario())


def test_deleting_the_video_drops_the_frames_from_the_filter_but_keeps_them(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            video = await upload_video(env, ds["id"], "clip.mp4", SHOTS_MP4)
            await _extract_and_wait(env, video["id"])
            assert len(await _filtered_ids(env, ds["id"], video["id"])) == 3

            r = await env.client.delete(f"{API}/videos/{video['id']}")
            assert r.status_code in (200, 204), r.text

            assert await _filtered_ids(env, ds["id"], video["id"]) == []

            # The frames themselves outlive the video, with the timestamp and
            # shot index intact — only the FK went NULL.
            r = await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})
            assert len(r.json()) == 3
            async with env.Session() as db:
                frames = (await db.execute(select(Image))).scalars().all()
            assert all(f.source_video_id is None for f in frames)
            assert all(f.source_timestamp_ms is not None for f in frames)
            assert all(f.source_shot_index is not None for f in frames)

    run(scenario())
