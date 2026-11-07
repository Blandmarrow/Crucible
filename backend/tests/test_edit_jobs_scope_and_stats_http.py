"""Upscale, LUT and inpaint: chunked id lookups (B3) and dataset stats (B2).

From the M8 cleanup plan (`docs/dev/cleanup-plan/README.md` § Stage 1).

**B3.** A whole-dataset run passed every id to one `Image.id.in_()`, past
SQLite's bind limit on a big dataset. The call is pinned rather than the crash,
as in `test_video_extract_http.py::test_the_replace_delete_is_chunked`: the limit
is a compile-time option, and lowering it with `setlimit` would also have to sit
below `chunked`'s 10,000 default while staying above the binds one `images`
INSERT needs, which is no test of the real shape. The spy returns one id per
chunk, which proves the caller loops rather than using `chunked` for show.
Stage 1 follow-up 3 adds detection crop, the detection run and the batch
resize and crop jobs, which reach the same select-all path.

**B2.** `Dataset.image_count` and `total_size_bytes` are stored columns, and
none of the three jobs refreshed them: copy mode adds a row, replace mode changes
a size. The refresh covers every dataset the run touched (PM-029).
"""
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.models import Image
from backend.models.detection import Detection
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


def _spy_router_and_scope(monkeypatch, router, calls: list[list[str]]) -> None:
    """One call log across `image_scope`, which resolves the request-time scope
    since M8 stage 2, and `router`, the module whose job loads the rows (for
    upscale and LUT, `image_edit_job`): the log still reads in call order."""
    import backend.services.image_scope as image_scope

    spy = _spy(calls)
    monkeypatch.setattr(router, "chunked", spy)
    monkeypatch.setattr(image_scope, "chunked", spy)


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
        import backend.services.image_edit_job as image_edit_job

        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())
            ids = {a["id"], b["id"]}

            calls: list[list[str]] = []
            _spy_router_and_scope(monkeypatch, image_edit_job, calls)
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
        import backend.services.image_edit_job as image_edit_job

        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())

            calls: list[list[str]] = []
            _spy_router_and_scope(monkeypatch, image_edit_job, calls)
            r = await env.client.post(f"{API}/upscaling/run", json={
                "dataset_id": ds["id"], "model_path": MODEL, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["result_data"]["processed"] == 2
            assert [set(c) for c in calls] == [{a["id"], b["id"]}], calls

    run(scenario())


def test_lut_chunks_both_id_lookups(tmp_path, monkeypatch):
    async def scenario():
        import backend.services.image_edit_job as image_edit_job

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")
            ids = {a["id"], b["id"]}

            calls: list[list[str]] = []
            _spy_router_and_scope(monkeypatch, image_edit_job, calls)
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
            _spy_router_and_scope(monkeypatch, inpaint_router, calls)
            started = await _run_inpaint(env, ds["id"], [a["id"], b["id"], "no-such-image"])
            job = await wait_for_job(env, started["job_id"], timeout=60)
            assert job["result_data"]["inpainted"] == 2
            assert set(calls[0]) == {a["id"], b["id"], "no-such-image"}, calls

    run(scenario())


# Follow-up 3: the same select-all path, through detection and the batch edits.

def test_detection_crop_chunks_the_request_time_lookup(tmp_path, monkeypatch):
    async def scenario():
        import backend.routers.detection as detection_router
        from backend.models.detection import Detection

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes(size=(40, 20)))
            async with env.Session() as db:
                for img in (a, b):
                    db.add(Detection(
                        image_id=img["id"], label="face", bbox=[0.25, 0.25, 0.75, 0.75],
                        score=0.9, model="test", task="detect",
                    ))
                await db.commit()

            calls: list[list[str]] = []
            _spy_router_and_scope(monkeypatch, detection_router, calls)
            # The bbox fetch chunks the same ids, so it is marked in the log:
            # without the fix, the first chunked call came from inside it.
            real_fetch = detection_router._fetch_bboxes_by_image

            async def marked_fetch(*args, **kwargs):
                calls.append(["<bbox fetch>"])
                return await real_fetch(*args, **kwargs)

            monkeypatch.setattr(detection_router, "_fetch_bboxes_by_image", marked_fetch)
            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [a["id"], b["id"]], "replace": False,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["result_data"]["cropped"] == 2, job

            assert set(calls[0]) == {a["id"], b["id"]}, calls

    run(scenario())


def test_detection_run_chunks_the_request_time_lookup(tmp_path, monkeypatch):
    """Only the request side is pinned: the job needs a model, and how it ends
    without one is not this test's business. The loader is stubbed to fail, so
    a machine with torch never loads real SAM2 weights for it."""
    async def scenario():
        import backend.routers.detection as detection_router
        from backend.ml.model_manager import model_manager

        async def no_sam2(**_kwargs):
            raise RuntimeError("no model in this test")

        monkeypatch.setattr(model_manager, "load_sam2", no_sam2)

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())

            calls: list[list[str]] = []
            monkeypatch.setattr(detection_router, "chunked", _spy(calls))
            r = await env.client.post(f"{API}/detection/run", json={
                "dataset_id": ds["id"], "image_ids": [a["id"], b["id"]],
                "model": "sam2", "task": "text_prompt", "custom_prompt": "cat",
            })
            assert r.status_code == 200, r.text
            await wait_for_job(env, r.json()["job_id"])

            assert set(calls[0]) == {a["id"], b["id"]}, calls

    run(scenario())


@pytest.mark.parametrize("route,body", [
    ("resize", {"scale": 0.5}),
    ("crop", {"target_ar": 1.0}),
])
def test_batch_edits_chunk_the_job_id_load(tmp_path, monkeypatch, route, body):
    async def scenario():
        import backend.routers.images as images_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes(size=(40, 20)))
            ids = {a["id"], b["id"]}

            calls: list[list[str]] = []
            monkeypatch.setattr(images_router, "chunked", _spy(calls))
            r = await env.client.post(
                f"{API}/images/batch/{route}", json={"image_ids": [a["id"], b["id"]], **body}
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["result_data"]["processed"] == 2, job

            # The request's busy guard, then the job's load.
            assert [set(c) for c in calls] == [ids, ids], calls

    run(scenario())


def test_a_repeated_id_is_edited_once_across_chunks(tmp_path, monkeypatch):
    """One `IN` collapsed a repeated id; chunks do not, and the spy puts each
    id in its own chunk, as a list past `chunked`'s size would. Without the
    dedupe the image is halved twice."""
    async def scenario():
        import backend.routers.images as images_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))

            monkeypatch.setattr(images_router, "chunked", _spy([]))
            r = await env.client.post(f"{API}/images/batch/resize", json={
                "image_ids": [img["id"], img["id"]], "scale": 0.5,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["result_data"]["processed"] == 1, job

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert (row.width, row.height) == (20, 10)

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


# Follow-up 5: a raise outside the per-image `try` failed the job with earlier
# images committed, and skipped the tail refresh that would have counted them.

def _raise_on_the_second_backup(monkeypatch):
    """The versioning hook runs before each replace and outside the per-image
    `try` in all three jobs, so failing it on the second image raises out of the
    loop with the first image's replace already committed."""
    from backend.services import version_service

    real = version_service.protect_file_before_overwrite
    calls = 0

    async def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("the database went away mid-run")
        return await real(*args, **kwargs)

    monkeypatch.setattr(version_service, "protect_file_before_overwrite", flaky)


async def _assert_refreshed_after_failure(env, ds_id, job, before) -> None:
    assert job["status"] == "failed", job
    after = await _stored_stats(env, ds_id)
    assert after == await _true_stats(env, ds_id)
    assert after[1] != before[1], "the first image's replace should have moved the size"


def test_upscale_refreshes_stats_when_it_fails_partway(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())
            before = await _stored_stats(env, ds["id"])
            _raise_on_the_second_backup(monkeypatch)

            job = await _upscale(env, ds["id"], [a["id"], b["id"]], replace=True)
            await _assert_refreshed_after_failure(env, ds["id"], job, before)

    run(scenario())


def test_lut_refreshes_stats_when_it_fails_partway(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.jpg", jpeg_bytes())
            b = await upload_image(env, ds["id"], "b.jpg", jpeg_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")
            before = await _stored_stats(env, ds["id"])
            _raise_on_the_second_backup(monkeypatch)

            job = await _lut(env, ds["id"], [a["id"], b["id"]], lut, replace=True)
            await _assert_refreshed_after_failure(env, ds["id"], job, before)

    run(scenario())


def test_inpaint_refreshes_stats_when_it_fails_partway(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_inpaint(monkeypatch)
            ds = await env.create_dataset("d")
            # JPEGs, re-encoded on save, so the first replace moves the size.
            a = await upload_image(env, ds["id"], "a.jpg", jpeg_bytes(size=(48, 32)))
            b = await upload_image(env, ds["id"], "b.jpg", jpeg_bytes(size=(48, 32)))
            await _add_detection(env, a["id"])
            await _add_detection(env, b["id"])
            before = await _stored_stats(env, ds["id"])
            _raise_on_the_second_backup(monkeypatch)

            started = await _run_inpaint(env, ds["id"], [a["id"], b["id"]], replace=True)
            job = await wait_for_job(env, started["job_id"], timeout=60)
            await _assert_refreshed_after_failure(env, ds["id"], job, before)

    run(scenario())


# Follow-up 10: batch resize, batch crop and detection crop had the same gap —
# the backup and its commit sit outside the per-image `try` in all three.

@pytest.mark.parametrize("route,body", [
    ("resize", {"scale": 0.5}),
    ("crop", {"target_ar": 1.0}),
])
def test_batch_edits_refresh_stats_when_they_fail_partway(tmp_path, monkeypatch, route, body):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes(size=(40, 20)))
            before = await _stored_stats(env, ds["id"])
            _raise_on_the_second_backup(monkeypatch)

            r = await env.client.post(
                f"{API}/images/batch/{route}", json={"image_ids": [a["id"], b["id"]], **body}
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            await _assert_refreshed_after_failure(env, ds["id"], job, before)

    run(scenario())


def test_detection_crop_refreshes_stats_when_it_fails_partway(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes(size=(40, 20)))
            async with env.Session() as db:
                for img in (a, b):
                    db.add(Detection(
                        image_id=img["id"], label="face", bbox=[0.25, 0.25, 0.75, 0.75],
                        score=0.9, model="test", task="detect",
                    ))
                await db.commit()
            before = await _stored_stats(env, ds["id"])
            _raise_on_the_second_backup(monkeypatch)

            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [a["id"], b["id"]], "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            await _assert_refreshed_after_failure(env, ds["id"], job, before)

    run(scenario())


# Follow-up 11: a dataset joins `touched` *before* the replace's commit. After
# it, a cancel landing between the two left a committed replace out of the
# abort refresh. Detection crop had the widest window — its add sat after the
# whole thumbnail epilogue — so it is the one with a seam to test; elsewhere
# only the commit itself lay between them.

def test_detection_crop_counts_a_replace_cancelled_in_its_epilogue(tmp_path, monkeypatch):
    import asyncio

    import backend.routers.detection as detection_router

    real = detection_router.contained_path

    def cancel_at_the_thumbnail(path, root, *, context, **kwargs):
        if context == "Detection crop thumbnail":
            raise asyncio.CancelledError()
        return real(path, root, context=context, **kwargs)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            async with env.Session() as db:
                db.add(Detection(
                    image_id=a["id"], label="face", bbox=[0.25, 0.25, 0.75, 0.75],
                    score=0.9, model="test", task="detect",
                ))
                await db.commit()
            before = await _stored_stats(env, ds["id"])
            monkeypatch.setattr(detection_router, "contained_path", cancel_at_the_thumbnail)

            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [a["id"]], "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])

            assert job["status"] == "cancelled", job
            after = await _stored_stats(env, ds["id"])
            assert after == await _true_stats(env, ds["id"])
            assert after[1] != before[1], "the replace committed, so the size moved"

    run(scenario())
