"""Upscale and LUT keep a failure's reason after the job ends (B1, PM-023).

Both loops swallow a per-image failure and the job still ends `completed`, so
the live `Failed:` message is the only place the reason appeared, and it is gone
on reload. Both now write `failed_count` and `failure_summary` beside their
counts, the convention restore and captioning use: the detail page reads the
summary into its error toast, and the Logs page keys its amber line on
`failed_count` (`docs/dev/frontend-jobs.md`).
"""
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
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
