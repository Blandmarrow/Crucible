"""Batch B probes (GPT-008/009/010/011/016/017/020). Report-only; never touches repo files."""
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

import backend.models  # noqa
from backend.models import Image
from backend.models.detection import Detection
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


def _locked(msg="locked"):
    e = PermissionError(13, msg)
    e.winerror = 32
    return e


def _client(app):
    # real status codes instead of re-raised app exceptions
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                             base_url="http://test")


async def _row(env, iid):
    async with env.Session() as db:
        return await db.get(Image, iid)


async def _ds(env, dsid):
    return (await env.client.get(f"{API}/datasets/{dsid}")).json()


# ---------------------------------------------------------------- GPT-017
def test_gpt017_polygon_outside_crop_gets_invented_mask(tmp_path):
    from backend.ml.mask_utils import remap_detection_geometry
    from backend.main import app

    mask = json.dumps({"polygons": [[[0, 0], [0.6, 0], [0, 0.6]]]})
    out = remap_detection_geometry(mask, [0, 0, 0.6, 0.6], (40, 40, 60, 60), (100, 100))
    print("helper:", out)
    assert out is not None and out[0] is not None
    polys = json.loads(out[0])["polygons"]
    assert polys == [[[0.0, 0.0], [0.3333, 0.0], [0.0, 0.3333]]]

    # geometric truth: triangle is x/100+y/100<=0.6; crop region needs x>=40,y>=40 -> x+y>=80 > 60
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(100, 100)))
            async with env.Session() as db:
                d = Detection(image_id=img["id"], label="x", bbox=[0, 0, 0.6, 0.6], model="m", task="t")
                db.add(d)
                await db.flush()
                d.mask = mask
                await db.commit()
            r = await env.client.post(f"{API}/images/{img['id']}/crop",
                                      json={"x": 40, "y": 40, "width": 60, "height": 60, "replace": True})
            assert r.status_code == 200, r.text
            async with env.Session() as db:
                ds_ = (await db.execute(select(Detection).where(Detection.image_id == img["id"]))).scalars().all()
            print("persisted", [(x.bbox, x.mask, x.mask_area) for x in ds_])
            assert len(ds_) == 1 and ds_[0].mask is not None and ds_[0].mask_area > 0.05
    run(scenario())


def test_gpt017_control_polygon_on_one_side_is_dropped(tmp_path):
    """Control: a polygon entirely left of the crop collapses to zero-area and IS dropped."""
    from backend.ml.mask_utils import remap_detection_geometry
    mask = json.dumps({"polygons": [[[0.0, 0.5], [0.3, 0.5], [0.0, 0.9]]]})
    out = remap_detection_geometry(mask, [0.0, 0.5, 0.45, 0.9], (50, 0, 50, 100), (100, 100))
    print("control:", out)
    assert out is None or out[0] is None


# ---------------------------------------------------------------- GPT-020
def test_gpt020_zero_dimension_resize(tmp_path):
    from backend.main import app

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(100, 1)))
            before = Path((await _row(env, img["id"])).file_path).read_bytes()
            async with _client(app) as c:
                r = await c.post(f"{API}/images/{img['id']}/resize", json={"scale": 0.5})
            print("status", r.status_code)
            assert r.status_code == 500
            assert Path((await _row(env, img["id"])).file_path).read_bytes() == before
            after = await _row(env, img["id"])
            assert (after.width, after.height) == (100, 1) and not after.processing_history

            # control: batch job -> counted failed, not job-fatal
            r = await env.client.post(f"{API}/images/batch/resize",
                                      json={"image_ids": [img["id"]], "scale": 0.5})
            job = await wait_for_job(env, r.json()["job_id"])
            print("batch job", job["status"], job["result_data"])
            # other paths to 0: width-only maintain_ar
            async with _client(app) as c:
                r3 = await c.post(f"{API}/images/{img['id']}/resize", json={"height": 1, "width": 1,
                                                                        "maintain_ar": True})
            print("w=1,h=1,maintain_ar status", r3.status_code)
    run(scenario())
