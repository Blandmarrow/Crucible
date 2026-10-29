"""A crop never asks Pillow for a zero-sized image (the GPT-020 sibling).

`POST /images/{id}/crop` validated none of its sizes, so a `width` or
`output_height` of 0 was a 500 from Pillow, after the route's versioning hook had
already run. A positive `output_width` could still derive a 0 px height (a 100×1
crop scaled to width 10), and `aspect_crop_rect` floored a side to 0 the same way
for a ratio far from the image's own. The sizes are now `gt=0` in the schema, the
derived sides have a 1 px floor, and the float fields on the resize and crop
requests refuse non-finite values: pydantic accepts `"inf"`, and JSON's `1e309`
parses to infinity.
"""
import json
from pathlib import Path

import pytest

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


async def _row(env, image_id: str) -> Image:
    async with env.Session() as db:
        return await db.get(Image, image_id)


async def _post_raw(env, path: str, raw: str):
    return await env.client.post(
        f"{API}{path}", content=raw, headers={"content-type": "application/json"},
    )


@pytest.mark.parametrize("body", [
    {"x": 0, "y": 0, "width": 0, "height": 2},
    {"x": 0, "y": 0, "width": 100, "height": 2, "output_height": 0},
    {"x": 0, "y": 0, "width": 100, "height": -1},
    {"x": 0, "y": 0, "width": 100, "height": 2, "output_width": -10},
    {"x": 0, "y": 0, "width": 100, "height": 2, "upscale_model": "m", "upscale_target_width": 0},
])
@pytest.mark.parametrize("replace", [True, False])
def test_non_positive_crop_size_is_a_422_that_touches_nothing(tmp_path, body, replace):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(100, 2)))
            before = Path((await _row(env, img["id"])).file_path).read_bytes()

            r = await env.client.post(
                f"{API}/images/{img['id']}/crop", json={**body, "replace": replace},
            )
            assert r.status_code == 422, r.text

            after = await _row(env, img["id"])
            assert Path(after.file_path).read_bytes() == before
            assert (after.width, after.height) == (100, 2)
            assert not after.processing_history
            listed = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert len(listed) == 1  # no new-file crop row either

    run(scenario())


def test_derived_output_side_keeps_one_pixel(tmp_path):
    # A 100×1 crop scaled to width 10 rounds its height to 0.1 → 0.
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(100, 2)))

            r = await env.client.post(f"{API}/images/{img['id']}/crop", json={
                "x": 0, "y": 0, "width": 100, "height": 1, "output_width": 10, "replace": True,
            })
            assert r.status_code == 200, r.text
            row = await _row(env, img["id"])
            assert (row.width, row.height) == (10, 1)
            assert [h["op"] for h in row.processing_history] == ["crop"]

    run(scenario())


def test_aspect_crop_far_from_the_image_ratio_keeps_one_pixel(tmp_path):
    # 100×2 cropped to 1000:1 wants a 100×0.1 rect, which used to floor to 100×0
    # and fail the item.
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(100, 2)))

            r = await env.client.post(
                f"{API}/images/batch/crop", json={"image_ids": [img["id"]], "target_ar": 1000},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["failed"] == 0, job["result_data"]
            row = await _row(env, img["id"])
            assert (row.width, row.height) == (100, 1)

    run(scenario())


@pytest.mark.parametrize("raw_value", ['"inf"', '"-inf"', '"nan"', "1e309", "Infinity", "NaN"])
def test_non_finite_floats_are_a_422_that_touches_nothing(tmp_path, raw_value):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 30)))
            before = Path((await _row(env, img["id"])).file_path).read_bytes()
            ids = json.dumps([img["id"]])

            for path, raw in (
                (f"/images/{img['id']}/resize", f'{{"scale": {raw_value}}}'),
                ("/images/batch/resize", f'{{"image_ids": {ids}, "scale": {raw_value}}}'),
                ("/images/batch/crop", f'{{"image_ids": {ids}, "target_ar": {raw_value}}}'),
            ):
                r = await _post_raw(env, path, raw)
                assert r.status_code == 422, (path, raw, r.text)

            after = await _row(env, img["id"])
            assert Path(after.file_path).read_bytes() == before
            assert (after.width, after.height) == (40, 30)
            assert not after.processing_history

    run(scenario())


@pytest.mark.parametrize("target_ar", [0, -1.5])
def test_non_positive_target_ar_is_a_422(tmp_path, target_ar):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 30)))
            r = await env.client.post(
                f"{API}/images/batch/crop", json={"image_ids": [img["id"]], "target_ar": target_ar},
            )
            assert r.status_code == 422, r.text
            assert not (await _row(env, img["id"])).processing_history

    run(scenario())
