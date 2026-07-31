"""Request-level tests for `POST /detection/crop` in replace mode.

The replace branch overwrote the file with `tmp_path.replace(src_path)`, then ran
an un-`try`-wrapped `generate_thumbnail`, and the loop's only `commit()` sat after
the loop — the third instance of PM-013's worse shape, where the blast radius is
not one image's stale geometry but every image already overwritten on disk. These
pin the fixed order: write → assign → per-image `commit()` → epilogue.
"""
from pathlib import Path

from sqlalchemy import select

from backend.models.detection import Detection
from backend.models.image import Image
from backend.tests.conftest import (
    API,
    api_env,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)


async def _two_images_with_detections(env, dataset_id: str) -> list[dict]:
    """Two 40×20 images, each with one centred bbox so the crop rect is a real
    sub-rect (not None, not the full image — both of which the worker skips)."""
    imgs = [
        await upload_image(env, dataset_id, "a.png", png_bytes((10, 20, 30), (40, 20))),
        await upload_image(env, dataset_id, "b.png", png_bytes((40, 50, 60), (40, 20))),
    ]
    async with env.Session() as db:
        for img in imgs:
            db.add(Detection(
                image_id=img["id"], label="face", bbox=[0.25, 0.25, 0.75, 0.75],
                score=0.9, model="test", task="detect",
            ))
        await db.commit()
    return imgs


async def _rows(env, dataset_id: str) -> dict[str, Image]:
    async with env.Session() as db:
        rows = (await db.execute(
            select(Image).where(Image.dataset_id == dataset_id)
        )).scalars().all()
    return {row.id: row for row in rows}


def test_replace_crop_commits_geometry_and_history_per_image(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _two_images_with_detections(env, ds["id"])

            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"],
                "image_ids": [i["id"] for i in imgs],
                "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"] == {
                "cropped": 2, "skipped_no_detection": 0, "skipped_noop": 0,
                "failed": 0, "thumbnails_stale": 0,
            }

            rows = await _rows(env, ds["id"])
            for i in imgs:
                row = rows[i["id"]]
                assert (row.width, row.height) == (20, 10)
                assert [h["op"] for h in row.processing_history] == ["crop_to_detection"]

    run(scenario())


def test_a_failed_thumbnail_still_commits_both_rows(tmp_path):
    """The thumbnail cut is a post-commit epilogue: it cannot undo the crop.

    It used to sit between the `tmp_path.replace` and the only commit, so an
    OSError there rolled back the geometry, `processing_history` and detection
    remaps of every image the run had already overwritten (PM-013).
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _two_images_with_detections(env, ds["id"])

            from backend.routers import detection as detection_router
            original = detection_router.generate_thumbnail

            def failing(*a, **kw):
                raise OSError("thumbnails/ is read-only")

            detection_router.generate_thumbnail = failing
            try:
                r = await env.client.post(f"{API}/detection/crop", json={
                    "dataset_id": ds["id"],
                    "image_ids": [i["id"] for i in imgs],
                    "replace": True,
                })
                assert r.status_code == 200, r.text
                job = await wait_for_job(env, r.json()["job_id"])
            finally:
                detection_router.generate_thumbnail = original

            assert job["status"] == "completed", job
            assert job["result_data"]["cropped"] == 2
            assert job["result_data"]["thumbnails_stale"] == 2

            rows = await _rows(env, ds["id"])
            for i in imgs:
                assert (rows[i["id"]].width, rows[i["id"]].height) == (20, 10)

    run(scenario())


def test_a_late_thumbnail_failure_does_not_roll_back_its_predecessor(tmp_path):
    """The blast radius, and the pin the per-image commit exists for.

    A raise on image *two* is what the old shape could not survive: it escaped the
    loop with the single trailing `commit()` unreached, so image one — already
    cropped on disk — kept its pre-crop `width`/`height`/`phash` and lost its
    `processing_history` entry. Note this is not the `crop_image_to_dest` failure,
    which the loop has always caught and counted; it is a raise from the step
    *after* the overwrite.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _two_images_with_detections(env, ds["id"])
            doomed = imgs[1]

            from backend.routers import detection as detection_router
            original = detection_router.generate_thumbnail

            def selective(src, *a, **kw):
                if Path(src).name == doomed["filename"]:
                    raise OSError("thumbnails/ is read-only")
                return original(src, *a, **kw)

            detection_router.generate_thumbnail = selective
            try:
                r = await env.client.post(f"{API}/detection/crop", json={
                    "dataset_id": ds["id"],
                    "image_ids": [i["id"] for i in imgs],
                    "replace": True,
                })
                assert r.status_code == 200, r.text
                job = await wait_for_job(env, r.json()["job_id"])
            finally:
                detection_router.generate_thumbnail = original

            assert job["status"] == "completed", job
            assert job["result_data"]["cropped"] == 2
            assert job["result_data"]["thumbnails_stale"] == 1

            rows = await _rows(env, ds["id"])
            survivor = rows[imgs[0]["id"]]
            assert (survivor.width, survivor.height) == (20, 10), \
                "the committed image was rolled back by its neighbour's failure"
            assert [h["op"] for h in survivor.processing_history] == ["crop_to_detection"]
            # The one whose epilogue failed still succeeded: its file is cropped
            # and its row says so. A failed epilogue is not a failed item.
            assert (rows[doomed["id"]].width, rows[doomed["id"]].height) == (20, 10)

    run(scenario())


def test_a_crop_failure_is_counted_and_leaves_its_row_alone(tmp_path):
    """Pre-existing behaviour, previously untested: a raise from
    `crop_image_to_dest` is caught before anything touches the file, so the item
    counts `failed` and its row keeps its original geometry."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _two_images_with_detections(env, ds["id"])
            doomed = imgs[1]

            from backend.routers import detection as detection_router
            original = detection_router.crop_image_to_dest

            def selective(file_path, *a, **kw):
                if Path(file_path).name == doomed["filename"]:
                    raise RuntimeError("decode failed")
                return original(file_path, *a, **kw)

            detection_router.crop_image_to_dest = selective
            try:
                r = await env.client.post(f"{API}/detection/crop", json={
                    "dataset_id": ds["id"],
                    "image_ids": [i["id"] for i in imgs],
                    "replace": True,
                })
                assert r.status_code == 200, r.text
                job = await wait_for_job(env, r.json()["job_id"])
            finally:
                detection_router.crop_image_to_dest = original

            assert job["status"] == "completed", job
            assert job["result_data"]["cropped"] == 1
            assert job["result_data"]["failed"] == 1

            rows = await _rows(env, ds["id"])
            failed_row = rows[doomed["id"]]
            assert (failed_row.width, failed_row.height) == (40, 20)
            assert not failed_row.processing_history
            # no `_croptmp` leftover
            assert not list(Path(failed_row.file_path).parent.glob("*_croptmp*"))

    run(scenario())
