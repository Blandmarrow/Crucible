"""`PATCH /datasets/{id}` with a changed name — the folder rename underneath it.

`rename_dataset` renames `{datasets_dir}/{old_slug}` to `{new_slug}` and then has
to rewrite every stored path that pointed into it. It rewrote `Image.file_path`
and `Image.thumbnail_path` and stopped there, so `Video.file_path` and
`Video.poster_path` were left naming a folder that no longer existed.

Not `test_http_smoke_crud.py`, whose dataset case is a smoke test, and not
`test_video_rename_http.py`, which is `PATCH /videos/{id}/rename` — a different
rename entirely.
"""

from pathlib import Path

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
def test_renaming_a_dataset_carries_its_videos(tmp_path):
    """The defect. Both video columns must land inside the new folder, and both
    read routes must still serve — a `Video` row is the one thing the old
    rewrite never touched."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("before")
            vid = await upload_video(env, ds["id"], "clip.mp4")

            old_root = Path(ds["folder_path"])
            async with env.Session() as db:
                before = (await db.execute(select(Video).where(Video.id == vid["id"]))).scalar_one()
                poster_rel = Path(before.poster_path).relative_to(old_root)

            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"name": "after"})
            assert r.status_code == 200, r.text
            new_root = Path(r.json()["folder_path"])
            assert new_root != old_root and not old_root.exists()

            async with env.Session() as db:
                row = (await db.execute(select(Video).where(Video.id == vid["id"]))).scalar_one()
            assert row.file_path == str(new_root / "videos" / "clip.mp4")
            assert row.poster_path == str(new_root / poster_rel)
            assert Path(row.file_path).exists()
            assert Path(row.poster_path).exists()

            assert (await env.client.get(f"{API}/videos/{vid['id']}/file")).status_code == 200
            assert (await env.client.get(f"{API}/videos/{vid['id']}/poster")).status_code == 200

    run(scenario())


def test_renaming_a_dataset_carries_its_images(tmp_path):
    """The behaviour that already worked, pinned so the `_rebase` refactor
    cannot lose it — including the `.txt` sidecar, which travels with the folder
    and has no column of its own to go stale."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("before")
            img = await upload_image(env, ds["id"], "a.png")
            await env.client.put(
                f"{API}/captions/image/{img['id']}", json={"caption_text": "a caption"}
            )

            old_root = Path(ds["folder_path"])
            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"name": "after"})
            assert r.status_code == 200, r.text
            new_root = Path(r.json()["folder_path"])
            assert new_root != old_root and not old_root.exists()

            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one()
            assert row.file_path == str(new_root / "images" / "a.png")
            assert row.thumbnail_path == str(new_root / "thumbnails" / "a.webp")
            assert Path(row.file_path).exists()
            assert Path(row.thumbnail_path).exists()
            assert (new_root / "images" / "a.txt").read_text(encoding="utf-8") == "a caption"

            assert (await env.client.get(f"{API}/images/{img['id']}/file")).status_code == 200

    run(scenario())


@needs_cv2
def test_a_rescan_after_the_rename_neither_re_adds_nor_reports_the_videos(tmp_path):
    """Why the defect was silent, stated as a test.

    `_rescan_videos` keys `by_filename` on each row's `filename`, and the files
    in the *new* folder carry those same names — so with the stale `file_path`
    every one of them still hit `continue`: nothing re-added, `videos_missing`
    empty, and a row whose bytes 404 that no report ever named. A rescan is the
    one thing a user would run to find out, and it said the dataset was fine.

    After the fix the same numbers are correct rather than accidental: one row,
    zero added, nothing missing."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("before")
            vid = await upload_video(env, ds["id"], "clip.mp4")

            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"name": "after"})
            assert r.status_code == 200, r.text

            r2 = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            assert r2.status_code == 200, r2.text
            job = await wait_for_job(env, r2.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["videos_added"] == 0
            assert job["result_data"]["videos_missing"] == []

            async with env.Session() as db:
                row = (await db.execute(select(Video))).scalar_one()
            assert row.id == vid["id"]
            assert Path(row.file_path).exists()

    run(scenario())
