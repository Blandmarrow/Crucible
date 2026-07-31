"""Request-level tests for `POST /images/batch/resize` and `/batch/crop`.

Both endpoints were declared **after** `POST /images/{image_id}/resize` and
`/{image_id}/crop`, and `image_id: str` accepts the literal segment "batch", so
FastAPI's declaration-order match handed every request to the single-image
handler: resize 404'd out of `db.get(Image, "batch")` and crop 422'd on the
single-crop body model. Nothing had ever executed these two job bodies — which is
why they still carried the pre-PM-013 shape (a single `commit()` outside the loop,
after an irreversible overwrite) and were the only mutating endpoints in
`images.py` with no `ensure_not_busy`. PM-018.

The first test here is the route-order pin; the rest exercise the handlers.
"""
from pathlib import Path

from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import (
    API,
    api_env,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)


async def _rows(env, dataset_id: str) -> dict[str, Image]:
    async with env.Session() as db:
        rows = (await db.execute(
            select(Image).where(Image.dataset_id == dataset_id)
        )).scalars().all()
    return {row.id: row for row in rows}


async def _two_images(env, dataset_id: str) -> list[dict]:
    return [
        await upload_image(env, dataset_id, "a.png", png_bytes((10, 20, 30), (40, 20))),
        await upload_image(env, dataset_id, "b.png", png_bytes((40, 50, 60), (40, 20))),
    ]


def test_batch_routes_are_not_shadowed_by_the_single_image_ones(tmp_path):
    """An empty body must be rejected by the *batch* body model.

    The pin is the 422 detail naming `image_ids`. On the shadowed ordering the
    resize path answered 404 ("Image not found", from `db.get(Image, "batch")`)
    and the crop path answered a 422 naming `x`/`y` — the single-crop rect fields.
    Asserting only the status code would not catch a regression of the ordering.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            for path in ("resize", "crop"):
                r = await env.client.post(f"{API}/images/batch/{path}", json={})
                assert r.status_code == 422, f"{path}: {r.status_code} {r.text}"
                named = {
                    loc[-1] for err in r.json()["detail"]
                    for loc in [err["loc"]]
                }
                assert "image_ids" in named, f"{path} was answered by another route: {named}"

    run(scenario())


def test_batch_resize_commits_geometry_and_history_per_image(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _two_images(env, ds["id"])

            r = await env.client.post(
                f"{API}/images/batch/resize",
                json={"image_ids": [i["id"] for i in imgs], "width": 20, "height": 10,
                      "maintain_ar": False},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"] == {
                "processed": 2, "skipped": 0, "failed": 0, "thumbnails_stale": 0,
            }
            # A single-dataset selection names its dataset on the job row, and the
            # label is descriptive rather than the old static "Batch resize".
            assert job["dataset_id"] == ds["id"]
            assert job["label"] == "Batch resize — 2 images"

            rows = await _rows(env, ds["id"])
            for i in imgs:
                row = rows[i["id"]]
                assert (row.width, row.height) == (20, 10)
                assert [h["op"] for h in row.processing_history] == ["resize"]
                from PIL import Image as PilImage
                with PilImage.open(row.file_path) as f:
                    assert f.size == (20, 10)

    run(scenario())


def test_batch_crop_commits_geometry_and_history_per_image(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _two_images(env, ds["id"])  # 40x20, AR 2.0

            r = await env.client.post(
                f"{API}/images/batch/crop",
                json={"image_ids": [i["id"] for i in imgs], "target_ar": 1.0},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["processed"] == 2
            assert job["label"] == "Batch crop — 1 AR, 2 images"

            rows = await _rows(env, ds["id"])
            for i in imgs:
                row = rows[i["id"]]
                assert row.width == row.height == 20
                assert [h["op"] for h in row.processing_history] == ["crop_aspect"]

    run(scenario())


def test_a_failed_thumbnail_still_commits_both_rows(tmp_path):
    """The thumbnail cut is a post-commit epilogue: it cannot undo the resize.

    It used to sit *before* the only commit, so an OSError there rolled back the
    geometry of every image the run had already rewritten on disk (PM-013).
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _two_images(env, ds["id"])

            from backend.routers import images as images_router
            original = images_router.generate_thumbnail

            def failing(*a, **kw):
                raise OSError("thumbnails/ is read-only")

            images_router.generate_thumbnail = failing
            try:
                r = await env.client.post(
                    f"{API}/images/batch/resize",
                    json={"image_ids": [i["id"] for i in imgs], "width": 20,
                          "height": 10, "maintain_ar": False},
                )
                assert r.status_code == 200, r.text
                job = await wait_for_job(env, r.json()["job_id"])
            finally:
                images_router.generate_thumbnail = original

            assert job["status"] == "completed", job
            assert job["result_data"]["processed"] == 2
            assert job["result_data"]["thumbnails_stale"] == 2

            rows = await _rows(env, ds["id"])
            for i in imgs:
                assert (rows[i["id"]].width, rows[i["id"]].height) == (20, 10)

    run(scenario())


def test_one_failed_image_does_not_roll_back_its_predecessors(tmp_path):
    """The blast radius. One `resize_image` raise used to abort the job with the
    single trailing `commit()` unreached, so every image already overwritten on
    disk kept its *old* row — the whole batch, not the one that failed."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = await _two_images(env, ds["id"])
            doomed = imgs[1]

            from backend.routers import images as images_router
            original = images_router.resize_image

            def selective(file_path, *a, **kw):
                if Path(file_path).name == doomed["filename"]:
                    raise RuntimeError("decode failed")
                return original(file_path, *a, **kw)

            images_router.resize_image = selective
            try:
                r = await env.client.post(
                    f"{API}/images/batch/resize",
                    json={"image_ids": [i["id"] for i in imgs], "width": 20,
                          "height": 10, "maintain_ar": False},
                )
                assert r.status_code == 200, r.text
                job = await wait_for_job(env, r.json()["job_id"])
            finally:
                images_router.resize_image = original

            assert job["status"] == "completed", job
            assert job["result_data"] == {
                "processed": 1, "skipped": 0, "failed": 1, "thumbnails_stale": 0,
            }

            rows = await _rows(env, ds["id"])
            survivor = rows[imgs[0]["id"]]
            assert (survivor.width, survivor.height) == (20, 10), \
                "the committed image was rolled back by its neighbour's failure"
            assert [h["op"] for h in survivor.processing_history] == ["resize"]
            failed_row = rows[doomed["id"]]
            assert (failed_row.width, failed_row.height) == (40, 20)
            assert not failed_row.processing_history

    run(scenario())


def test_batch_endpoints_409_while_the_dataset_is_busy(tmp_path):
    """Both overwrite files in place, so both need the guard every other mutating
    endpoint in this router has — and it has to fire before the job row is
    created, not inside the worker."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.models import BackgroundJob
            from backend.services import dataset_busy

            ds = await env.create_dataset("d")
            imgs = await _two_images(env, ds["id"])
            ids = [i["id"] for i in imgs]

            with dataset_busy.busy(ds["id"], "versioning"):
                r = await env.client.post(
                    f"{API}/images/batch/resize",
                    json={"image_ids": ids, "width": 20, "height": 10},
                )
                assert r.status_code == 409, r.text
                r = await env.client.post(
                    f"{API}/images/batch/crop",
                    json={"image_ids": ids, "target_ar": 1.0},
                )
                assert r.status_code == 409, r.text

            async with env.Session() as db:
                jobs = (await db.execute(select(BackgroundJob))).scalars().all()
            assert jobs == [], "a refused request must not leave a job row behind"

            rows = await _rows(env, ds["id"])
            assert all((rows[i].width, rows[i].height) == (40, 20) for i in ids)

    run(scenario())
