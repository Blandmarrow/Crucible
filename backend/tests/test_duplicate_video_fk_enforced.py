"""`duplicate_dataset --include-videos` under real foreign-key enforcement.

The rest of the suite runs with SQLite's `foreign_keys` pragma OFF — the default
for every connection, and this harness builds its schema with `create_all` on its
own engine, so it never gets the `PRAGMA foreign_keys=ON` that
`backend/database.py` installs on the app engine. Every FK in the schema is
unenforced there, which is fine for tests about rows and files but blind to one
whole failure class.

It hid a real one. `Image.source_video_id` is a genuine FK to `videos.id`, and
the video-copy step adds its `Video` rows to the session without flushing them —
so the copied frames' INSERT reached the database before the video they name.
Under the app's engine that is `IntegrityError: FOREIGN KEY constraint failed`
and the whole duplicate job fails; under the harness's it passed silently, and
only running the real app surfaced it. This module opts the pragma back on for
exactly that path so it cannot come back.

Nothing else here needs the pragma: this is the only place the branch creates a
row that points at another row created moments earlier in the same transaction.
"""

from sqlalchemy import select

from backend.models import Image, Video
from backend.tests.conftest import (
    API,
    api_env,
    needs_cv2,
    run,
    upload_image,
    upload_video,
    wait_for_job,
)


@needs_cv2
def test_copied_frames_reference_a_video_row_that_already_exists(tmp_path):
    """The copied `Video` rows must be *in the database*, not merely in the
    session, before the first copied `Image` that names one is inserted."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            src = await env.create_dataset("src")
            video = await upload_video(env, src["id"], "clip.mp4")
            frame = await upload_image(env, src["id"], "frame.png")
            async with env.Session() as db:
                row = await db.get(Image, frame["id"])
                row.source_video_id = video["id"]
                row.source_timestamp_ms = 4321
                row.source_shot_index = 7
                await db.commit()

            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate",
                json={"new_name": "copy", "include_videos": True},
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            # Before the fix this was "failed" with FOREIGN KEY constraint failed
            # and result_data {} — the whole job lost, not just the videos.
            assert job["status"] == "completed", job
            assert job["result_data"]["videos_added"] == 1
            assert job["result_data"]["images_added"] == 1

            async with env.Session() as db:
                new_ds_id = (await db.execute(
                    select(Image.dataset_id).where(Image.dataset_id != src["id"])
                )).scalars().first()
                copied_video = (await db.execute(
                    select(Video).where(Video.dataset_id == new_ds_id)
                )).scalar_one()
                copied_frame = (await db.execute(
                    select(Image).where(Image.dataset_id == new_ds_id)
                )).scalar_one()
            assert copied_frame.source_video_id == copied_video.id

    run(scenario())


@needs_cv2
def test_a_frame_whose_video_was_not_carried_still_inserts(tmp_path):
    """The NULL fallback has to be a real NULL, not an id no row answers to —
    which only an enforced FK can tell apart."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            src = await env.create_dataset("src")
            video = await upload_video(env, src["id"], "clip.mp4")
            frame = await upload_image(env, src["id"], "frame.png")
            async with env.Session() as db:
                row = await db.get(Image, frame["id"])
                row.source_video_id = video["id"]
                await db.commit()

            # Toggle off: the videos do not travel, so every map lookup misses.
            r = await env.client.post(
                f"{API}/datasets/{src['id']}/duplicate", json={"new_name": "copy"}
            )
            assert r.status_code in (200, 202), r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "completed", job

            async with env.Session() as db:
                new_ds_id = (await db.execute(
                    select(Image.dataset_id).where(Image.dataset_id != src["id"])
                )).scalars().first()
                copied = (await db.execute(
                    select(Image).where(Image.dataset_id == new_ds_id)
                )).scalar_one()
            assert copied.source_video_id is None

    run(scenario())
