"""Request-level smoke tests for the background-job routers.

The companion of `test_http_smoke_crud.py` for the endpoints that do their work in
a queued coroutine rather than inline: export, folder import/rescan, snapshots and
tag subsumption. Each enqueues over HTTP and drives the worker with `wait_for_job`
(conftest.py) — the request-level equivalent of awaiting the job body, where a
router-shaped crash otherwise stays hidden behind a green service-level suite.
None of these touch a GPU or an ML model.
"""
from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


# --- export --------------------------------------------------------------


def test_export_plain_writes_files(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("exp")
            a = await upload_image(env, ds["id"], "a.png")
            b = await upload_image(env, ds["id"], "b.png", png_bytes((9, 9, 9)))
            for img in (a, b):
                await env.client.put(f"{API}/captions/image/{img['id']}", json={"caption_text": "cap"})

            out = tmp_path / "export"
            r = await env.client.post(f"{API}/export/plain", json={
                "dataset_id": ds["id"], "output_dir": str(out),
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            assert len(list(out.rglob("*.png"))) == 2
            # Plain export accumulates captions into a single JSONL manifest at the
            # export root — not per-image .txt sidecars.
            manifest = out / "captions.jsonl"
            assert manifest.exists()
            assert len(manifest.read_text(encoding="utf-8").splitlines()) == 2

    run(scenario())


def test_export_preview_and_invalid_body(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("exp2")
            await upload_image(env, ds["id"], "a.png")

            r = await env.client.get(f"{API}/export/preview/{ds['id']}")
            assert r.status_code == 200, r.text
            assert r.json()["will_export"] == 1

            # Missing output_dir is a request-validation error.
            assert (await env.client.post(
                f"{API}/export/plain", json={"dataset_id": ds["id"]})).status_code == 422

    run(scenario())


# --- folder import & rescan ----------------------------------------------


def test_dataset_import_from_folder(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("imp")

            src = tmp_path / "src"
            src.mkdir()
            (src / "pic.png").write_bytes(png_bytes())

            r = await env.client.post(f"{API}/datasets/{ds['id']}/import", json={
                "folder_path": str(src), "import_captions": False,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert len(images) == 1

    run(scenario())


def test_dataset_rescan_reports_a_deleted_file(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("rescan")
            img = await upload_image(env, ds["id"], "a.png")

            # Delete the file on disk behind the DB's back, then rescan.
            on_disk = list(env.datasets_dir.rglob("a.png"))
            assert len(on_disk) == 1
            on_disk[0].unlink()

            r = await env.client.post(f"{API}/datasets/{ds['id']}/rescan", json={})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            # Rescan reports a missing file, it never removes the row (the DB is
            # authoritative; a vanished file is surfaced, not silently deleted).
            assert "a.png" in {m["filename"] for m in job["result_data"]["missing"]}
            async with env.Session() as db:
                row = (await db.execute(select(Image).where(Image.id == img["id"]))).scalar_one_or_none()
                assert row is not None

    run(scenario())


# --- versioning ----------------------------------------------------------


def test_snapshot_lifecycle_and_branches(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            ds = await env.create_dataset("ver")
            await upload_image(env, ds["id"], "a.png")

            # Snapshot creation on a missing dataset is a 404 (checked before mode).
            assert (await env.client.post(
                f"{API}/datasets/nope/versions", json={"name": "s"})).status_code == 404

            # Manual mode always snapshots as a background job.
            r = await env.client.post(f"{API}/datasets/{ds['id']}/versions", json={"name": "s1"})
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            versions = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()
            assert len(versions) == 1
            vid = versions[0]["id"]

            assert (await env.client.get(f"{API}/datasets/{ds['id']}/versions/{vid}")).status_code == 200
            r = await env.client.patch(
                f"{API}/datasets/{ds['id']}/versions/{vid}", json={"is_pinned": True})
            assert r.status_code == 200, r.text
            assert r.json()["is_pinned"] is True

            # The main branch is created lazily by the first snapshot.
            branches = (await env.client.get(f"{API}/datasets/{ds['id']}/versions/branches")).json()
            assert len(branches) >= 1

    run(scenario())


# --- tag consolidation (subsume only — no embedder) ----------------------


def test_tag_subsume_dedupes(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("tags")
            img = await upload_image(env, ds["id"], "a.png")
            await env.client.put(f"{API}/captions/image/{img['id']}", json={"caption_text": "long tail, tail"})

            r = await env.client.post(f"{API}/tag-consolidation/dataset/{ds['id']}/subsume", json={})
            assert r.status_code == 200, r.text
            assert r.json()["affected"] == 1

            caption = (await env.client.get(f"{API}/captions/image/{img['id']}")).json()["caption_text"]
            assert "tail" not in [t.strip() for t in caption.split(",")]
            assert "long tail" in caption

    run(scenario())
