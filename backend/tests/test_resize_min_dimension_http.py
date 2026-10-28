"""A positive resize never rounds a side to zero (GPT-020).

`resize_image` floors every derived dimension, so a 100×1 image at `scale=0.5`
asked Pillow for a 50×0 image and the single route answered 500 (the batch job
counted it `failed`). The derived size now has a 1 px floor, and a request whose
own width, height or scale is zero or negative is a 422 — refused by the schema,
before the route's versioning hook or the resize ever run.
"""
from pathlib import Path

import pytest

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


async def _row(env, image_id: str) -> Image:
    async with env.Session() as db:
        return await db.get(Image, image_id)


def test_single_resize_keeps_a_one_pixel_side(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(100, 1)))

            r = await env.client.post(f"{API}/images/{img['id']}/resize", json={"scale": 0.5})
            assert r.status_code == 200, r.text
            row = await _row(env, img["id"])
            assert (row.width, row.height) == (50, 1)
            assert [h["op"] for h in row.processing_history] == ["resize"]

    run(scenario())


def test_fit_inside_one_by_one_box_is_one_by_one(tmp_path):
    # maintain_ar fits 100×1 into 1×1 with ratio 0.01, which floors the height to 0.
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(100, 1)))

            r = await env.client.post(
                f"{API}/images/{img['id']}/resize",
                json={"width": 1, "height": 1, "maintain_ar": True},
            )
            assert r.status_code == 200, r.text
            row = await _row(env, img["id"])
            assert (row.width, row.height) == (1, 1)

    run(scenario())


def test_batch_resize_processes_a_one_pixel_side(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(100, 1)))

            r = await env.client.post(
                f"{API}/images/batch/resize", json={"image_ids": [img["id"]], "scale": 0.5},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["processed"] == 1
            assert job["result_data"]["failed"] == 0
            row = await _row(env, img["id"])
            assert (row.width, row.height) == (50, 1)

    run(scenario())


@pytest.mark.parametrize("body", [
    {"scale": 0},
    {"scale": -0.5},
    {"width": -5},
    {"width": 0, "height": 10},
    {"height": 0},
])
def test_non_positive_size_is_a_422_that_touches_nothing(tmp_path, body):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 30)))
            before = await _row(env, img["id"])
            before_bytes = Path(before.file_path).read_bytes()

            r = await env.client.post(f"{API}/images/{img['id']}/resize", json=body)
            assert r.status_code == 422, r.text
            r = await env.client.post(
                f"{API}/images/batch/resize", json={"image_ids": [img["id"]], **body},
            )
            assert r.status_code == 422, r.text

            after = await _row(env, img["id"])
            assert Path(after.file_path).read_bytes() == before_bytes
            assert (after.width, after.height) == (40, 30)
            assert not after.processing_history

    run(scenario())
