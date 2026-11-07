"""Upscale and LUT keep a failure's reason after the job ends (B1, PM-023).

Both loops swallow a per-image failure and the job still ends `completed`, so
the live `Failed:` message is the only place the reason appeared, and it is gone
on reload. Both now write `failed_count` and `failure_summary` beside their
counts, the convention restore and captioning use: the detail page reads the
summary into its error toast, and the Logs page keys its amber line on
`failed_count` (`docs/dev/frontend-jobs.md`).

Follow-up 8 brought batch resize and crop, detection crop, inpaint and video
re-extraction into the same convention; re-extraction's tests are in
`test_video_reextract_http.py`, beside its refusals.
"""
import pytest

from backend.models.detection import Detection
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.tests.test_inpaint_http import _add_detection, _run_inpaint
from backend.tests.test_inpaint_http import _install_fake as _install_fake_inpaint
from backend.tests.test_lut_replace_extension_http import _identity_cube
from backend.tests.test_upscale_png_fallback_http import MODEL, _install_fake


def _broken_upscale(*_a, **_kw):
    raise RuntimeError("CUDA out of memory")


def test_a_failed_upscale_records_why(tmp_path, monkeypatch):
    async def scenario():
        import backend.routers.upscaling as upscale_router

        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            monkeypatch.setattr(upscale_router, "upscale_image_sync", _broken_upscale)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes())

            r = await env.client.post(f"{API}/upscaling/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "model_path": MODEL, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            # Still `completed`: one bad image never fails the run.
            assert job["status"] == "completed", job
            rd = job["result_data"]
            assert rd["processed"] == 0 and rd["failed"] == 1
            assert rd["failed_count"] == 1
            assert "shot.png" in rd["failure_summary"]
            assert "CUDA out of memory" in rd["failure_summary"]

    run(scenario())


def test_a_failed_lut_records_why(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes())
            broken = tmp_path / "broken.cube"
            broken.write_text("LUT_3D_SIZE 2\nnot a number\n", encoding="utf-8")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(broken), "intensity": 1.0, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            assert job["status"] == "completed", job
            rd = job["result_data"]
            assert rd["processed"] == 0 and rd["failed"] == 1
            assert rd["failed_count"] == 1
            assert "shot.png" in rd["failure_summary"]

    run(scenario())


def test_a_clean_run_records_no_failure(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            rd = job["result_data"]
            assert rd["failed_count"] == 0 and rd["failure_summary"] is None

    run(scenario())


# Follow-up 8: the other edit jobs keep the reason too. Each fails its edit with
# a known message, which must reach `failure_summary` with the file's name.

def _broken_edit(*_a, **_kw):
    raise RuntimeError("the codec gave up")


def _assert_kept_why(job: dict, done_key: str) -> None:
    assert job["status"] == "completed", job
    rd = job["result_data"]
    assert rd[done_key] == 0 and rd["failed"] == 1, rd
    assert rd["failed_count"] == 1, rd
    assert "shot.png" in rd["failure_summary"], rd
    assert "the codec gave up" in rd["failure_summary"], rd


@pytest.mark.parametrize("route,body,fn", [
    ("resize", {"scale": 0.5}, "resize_image"),
    ("crop", {"target_ar": 1.0}, "crop_image_to_dest"),
])
def test_a_failed_batch_edit_records_why(tmp_path, monkeypatch, route, body, fn):
    async def scenario():
        import backend.routers.images as images_router

        async with api_env(tmp_path) as env:
            monkeypatch.setattr(images_router, fn, _broken_edit)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes(size=(40, 20)))

            r = await env.client.post(
                f"{API}/images/batch/{route}", json={"image_ids": [img["id"]], **body}
            )
            job = await wait_for_job(env, r.json()["job_id"])
            _assert_kept_why(job, "processed")

    run(scenario())


@pytest.mark.parametrize("replace", [True, False])
def test_a_failed_detection_crop_records_why(tmp_path, monkeypatch, replace):
    async def scenario():
        import backend.routers.detection as detection_router

        async with api_env(tmp_path) as env:
            monkeypatch.setattr(detection_router, "crop_image_to_dest", _broken_edit)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes(size=(40, 20)))
            async with env.Session() as db:
                db.add(Detection(
                    image_id=img["id"], label="face", bbox=[0.25, 0.25, 0.75, 0.75],
                    score=0.9, model="test", task="detect",
                ))
                await db.commit()

            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]], "replace": replace,
            })
            job = await wait_for_job(env, r.json()["job_id"])
            _assert_kept_why(job, "cropped")

    run(scenario())


def test_a_failed_inpaint_records_why(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_inpaint(monkeypatch, fake=_broken_edit)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes(size=(48, 32)))
            await _add_detection(env, img["id"])

            started = await _run_inpaint(env, ds["id"], [img["id"]])
            job = await wait_for_job(env, started["job_id"], timeout=60)
            _assert_kept_why(job, "inpainted")

    run(scenario())


def test_a_clean_batch_resize_records_no_failure(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes(size=(40, 20)))

            r = await env.client.post(
                f"{API}/images/batch/resize", json={"image_ids": [img["id"]], "scale": 0.5}
            )
            rd = (await wait_for_job(env, r.json()["job_id"]))["result_data"]
            assert rd["processed"] == 1
            assert rd["failed_count"] == 0 and rd["failure_summary"] is None

    run(scenario())


# Follow-up 4: a one-image run that did nothing has to say which skip it was.
# The detail page used to blame a name collision for every processed-0 run.

def _vanish_on_the_job_load(monkeypatch, job_module):
    """`chunked` that answers the request's lookup and finds nothing at the
    job's load: to the job, the row was deleted between enqueue and run.

    One counter across `image_scope`, which makes the request's lookup, and
    `job_module`, which loads the rows (`image_edit_job` for upscale and LUT)."""
    import backend.services.image_scope as image_scope

    calls = 0

    def chunked(seq, size=10_000):
        nonlocal calls
        calls += 1
        return [list(seq)] if calls == 1 else []
    monkeypatch.setattr(image_scope, "chunked", chunked)
    monkeypatch.setattr(job_module, "chunked", chunked)


def test_an_upscale_whose_row_vanished_counts_it_missing(tmp_path, monkeypatch):
    async def scenario():
        import backend.services.image_edit_job as image_edit_job

        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes())
            _vanish_on_the_job_load(monkeypatch, image_edit_job)

            r = await env.client.post(f"{API}/upscaling/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "model_path": MODEL, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            rd = job["result_data"]
            assert (rd["processed"], rd["skipped"], rd["missing"]) == (0, 1, 1), rd

    run(scenario())


def test_a_lut_whose_row_vanished_counts_it_missing(tmp_path, monkeypatch):
    async def scenario():
        import backend.services.image_edit_job as image_edit_job

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "shot.png", png_bytes())
            lut = _identity_cube(tmp_path / "identity.cube")
            _vanish_on_the_job_load(monkeypatch, image_edit_job)

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            rd = job["result_data"]
            assert (rd["processed"], rd["skipped"], rd["missing"]) == (0, 1, 1), rd

    run(scenario())
