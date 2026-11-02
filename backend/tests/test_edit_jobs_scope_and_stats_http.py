"""Upscale, LUT and inpaint: chunked id lookups (B3) and dataset stats (B2).

From the M8 cleanup plan (`docs/dev/cleanup-plan/README.md` § Stage 1).

**B3.** A whole-dataset run passed every id to one `Image.id.in_()`, past
SQLite's bind limit on a big dataset. The call is pinned rather than the crash,
as in `test_video_extract_http.py::test_the_replace_delete_is_chunked`: the limit
is a compile-time option, and lowering it with `setlimit` would also have to sit
below `chunked`'s 10,000 default while staying above the binds one `images`
INSERT needs, which is no test of the real shape. The spy returns one id per
chunk, which proves the caller loops rather than using `chunked` for show.

**B2.** `Dataset.image_count` and `total_size_bytes` are stored columns, and
none of the three jobs refreshed them: copy mode adds a row, replace mode changes
a size. The refresh covers every dataset the run touched (PM-029).
"""
from pathlib import Path

from sqlalchemy import func, select

from backend.models import Image
from backend.tests.conftest import (
    API,
    api_env,
    jpeg_bytes,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)
from backend.tests.test_inpaint_http import _add_detection, _run_inpaint
from backend.tests.test_inpaint_http import _install_fake as _install_fake_inpaint
from backend.tests.test_lut_replace_extension_http import _identity_cube
from backend.tests.test_upscale_png_fallback_http import MODEL
from backend.tests.test_upscale_png_fallback_http import _install_fake as _install_fake_upscale


def _spy(calls: list[list[str]]):
    def spy(seq, size=10_000):
        items = list(seq)
        calls.append(items)
        return [[item] for item in items]
    return spy


async def _upscale(env, ds_id, image_ids, *, replace):
    r = await env.client.post(f"{API}/upscaling/run", json={
        "dataset_id": ds_id, "image_ids": image_ids, "model_path": MODEL, "replace": replace,
    })
    assert r.status_code == 200, r.text
    return await wait_for_job(env, r.json()["job_id"], timeout=60)


async def _lut(env, ds_id, image_ids, lut: Path, *, replace):
    r = await env.client.post(f"{API}/lut/run", json={
        "dataset_id": ds_id, "image_ids": image_ids,
        "lut_path": str(lut), "intensity": 1.0, "replace": replace,
    })
    assert r.status_code == 200, r.text
    return await wait_for_job(env, r.json()["job_id"], timeout=60)


async def _stored_stats(env, ds_id) -> tuple[int, int]:
    ds = (await env.client.get(f"{API}/datasets/{ds_id}")).json()
    return ds["image_count"], ds["total_size_bytes"]


async def _true_stats(env, ds_id) -> tuple[int, int]:
    async with env.Session() as db:
        count, size = (await db.execute(
            select(func.count(Image.id), func.coalesce(func.sum(Image.file_size_bytes), 0))
            .where(Image.dataset_id == ds_id)
        )).one()
    return count, size


# ── B3: chunked id lookups ────────────────────────────────────────────────────

def test_upscale_chunks_both_id_lookups(tmp_path, monkeypatch):
    async def scenario():
        import backend.routers.upscaling as upscale_router

        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())
            ids = {a["id"], b["id"]}

            calls: list[list[str]] = []
            monkeypatch.setattr(upscale_router, "chunked", _spy(calls))
            job = await _upscale(env, ds["id"], [a["id"], b["id"]], replace=True)
            assert job["status"] == "completed", job
            assert job["result_data"]["processed"] == 2

            # The request-time lookup of the client's ids, then the job's load.
            assert [set(c) for c in calls] == [ids, ids], calls

    run(scenario())


def test_upscale_chunks_the_whole_dataset_scope(tmp_path, monkeypatch):
    """The scope branch builds its id list from a query, and that list is the
    one a whole-dataset run makes large."""
    async def scenario():
        import backend.routers.upscaling as upscale_router

        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())

            calls: list[list[str]] = []
            monkeypatch.setattr(upscale_router, "chunked", _spy(calls))
            r = await env.client.post(f"{API}/upscaling/run", json={
                "dataset_id": ds["id"], "model_path": MODEL, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["result_data"]["processed"] == 2
            assert [set(c) for c in calls] == [{a["id"], b["id"]}], calls

    run(scenario())


def test_lut_chunks_both_id_lookups(tmp_path, monkeypatch):
    async def scenario():
        import backend.routers.lut as lut_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")
            ids = {a["id"], b["id"]}

            calls: list[list[str]] = []
            monkeypatch.setattr(lut_router, "chunked", _spy(calls))
            job = await _lut(env, ds["id"], [a["id"], b["id"]], lut, replace=True)
            assert job["status"] == "completed", job
            assert job["result_data"]["processed"] == 2
            assert [set(c) for c in calls] == [ids, ids], calls

    run(scenario())


def test_inpaint_chunks_the_request_time_lookup(tmp_path, monkeypatch):
    """Inpaint's job body already chunked; its lookup of the client's ids did
    not. The id of no image tells that lookup apart from the mask fetch after
    it, which only ever sees ids the lookup found."""
    async def scenario():
        import backend.routers.inpaint as inpaint_router

        async with api_env(tmp_path) as env:
            _install_fake_inpaint(monkeypatch)
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes(size=(48, 32)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes(size=(48, 32)))
            await _add_detection(env, a["id"])
            await _add_detection(env, b["id"])

            calls: list[list[str]] = []
            monkeypatch.setattr(inpaint_router, "chunked", _spy(calls))
            started = await _run_inpaint(env, ds["id"], [a["id"], b["id"], "no-such-image"])
            job = await wait_for_job(env, started["job_id"], timeout=60)
            assert job["result_data"]["inpainted"] == 2
            assert set(calls[0]) == {a["id"], b["id"], "no-such-image"}, calls

    run(scenario())


# ── B2: stats refreshed after the run ─────────────────────────────────────────

def test_upscale_copy_refreshes_the_image_count(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())

            job = await _upscale(env, ds["id"], [img["id"]], replace=False)
            assert job["result_data"]["processed"] == 1, job

            assert await _stored_stats(env, ds["id"]) == await _true_stats(env, ds["id"])
            assert (await _stored_stats(env, ds["id"]))[0] == 2

    run(scenario())


def test_upscale_replace_refreshes_the_total_size(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            before = await _stored_stats(env, ds["id"])

            job = await _upscale(env, ds["id"], [img["id"]], replace=True)
            assert job["result_data"]["processed"] == 1, job

            after = await _stored_stats(env, ds["id"])
            assert after == await _true_stats(env, ds["id"])
            assert after[1] != before[1], "the fake doubles the image, so the size moved"

    run(scenario())


def test_upscale_refreshes_every_dataset_the_selection_touched(tmp_path, monkeypatch):
    """PM-029: an id list can span datasets, so refreshing `body.dataset_id`
    alone would leave the other one stale."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            d1 = await env.create_dataset("d1")
            d2 = await env.create_dataset("d2")
            a = await upload_image(env, d1["id"], "a.png", png_bytes())
            b = await upload_image(env, d2["id"], "b.png", png_bytes())

            job = await _upscale(env, d1["id"], [a["id"], b["id"]], replace=False)
            assert job["result_data"]["processed"] == 2, job

            for ds in (d1, d2):
                stored = await _stored_stats(env, ds["id"])
                assert stored == await _true_stats(env, ds["id"])
                assert stored[0] == 2

    run(scenario())


def test_lut_copy_refreshes_the_image_count(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")

            job = await _lut(env, ds["id"], [img["id"]], lut, replace=False)
            assert job["result_data"]["processed"] == 1, job

            stored = await _stored_stats(env, ds["id"])
            assert stored == await _true_stats(env, ds["id"])
            assert stored[0] == 2

    run(scenario())


def test_lut_replace_refreshes_the_total_size(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            # A JPEG is re-encoded on save, so its size moves even through an
            # identity LUT.
            img = await upload_image(env, ds["id"], "a.jpg", jpeg_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")
            before = await _stored_stats(env, ds["id"])

            job = await _lut(env, ds["id"], [img["id"]], lut, replace=True)
            assert job["result_data"]["processed"] == 1, job
            after = await _stored_stats(env, ds["id"])
            assert after == await _true_stats(env, ds["id"])
            assert after[1] != before[1], "the re-encode did not move the size"

    run(scenario())


def test_inpaint_copy_refreshes_the_image_count(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_inpaint(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(48, 32)))
            await _add_detection(env, img["id"])

            started = await _run_inpaint(env, ds["id"], [img["id"]], replace=False)
            job = await wait_for_job(env, started["job_id"], timeout=60)
            assert job["result_data"]["inpainted"] == 1, job

            stored = await _stored_stats(env, ds["id"])
            assert stored == await _true_stats(env, ds["id"])
            assert stored[0] == 2

    run(scenario())
