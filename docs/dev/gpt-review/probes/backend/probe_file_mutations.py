"""Batch B probes, remaining: GPT-020 (grouping (e)). Report-only; never touches repo files."""
from pathlib import Path

import httpx
import pytest

import backend.models  # noqa
from backend.models import Image
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
