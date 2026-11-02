"""In-place edits refuse a thumbnail path outside the datasets folder.

The `file_path` gate (B4, `test_out_of_tree_edits_http.py`) stopped an edit
writing through a link, but every replace epilogue then recut the thumbnail
through the raw `img.thumbnail_path`. `generate_thumbnail` mkdirs its parent, so
that was a write anywhere — the reason CLAUDE.md gives for gating
`bulk_thumbnails`. Only inpaint gated both.

Each test points the row's `thumbnail_path` into a folder outside the tree that
does not exist yet, so a write would have to create it: the assertion is that
the folder is never made. A refused thumbnail is a stale tile, never a failed
edit — the pixels are already committed by the time the epilogue runs — so the
jobs count it in `thumbnails_stale` and the single routes still answer 200.

Video re-extraction's twin lives in `test_video_reextract_http.py`, behind that
module's cv2 and scenedetect gates.
"""
from pathlib import Path

import pytest

from backend.models import Image
from backend.models.detection import Detection
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.tests.test_inpaint_http import _add_detection, _run_inpaint
from backend.tests.test_inpaint_http import _install_fake as _install_fake_inpaint
from backend.tests.test_lut_replace_extension_http import _identity_cube
from backend.tests.test_upscale_png_fallback_http import MODEL
from backend.tests.test_upscale_png_fallback_http import _install_fake as _install_fake_upscale


async def _thumb_outside(env, image_id: str, tmp_path: Path) -> Path:
    """Point the row's thumbnail at a not-yet-existing folder outside the tree.
    Returns that folder, which every test then checks was never created."""
    outside_dir = tmp_path / "elsewhere" / "made-by-the-edit"
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        row.thumbnail_path = str(outside_dir / "a.webp")
        await db.commit()
    return outside_dir


def _assert_stale_not_failed(job: dict, *, done_key: str = "processed") -> None:
    assert job["status"] == "completed", job
    rd = job["result_data"]
    assert rd[done_key] == 1 and rd["failed"] == 0, rd
    assert rd["thumbnails_stale"] == 1, rd


# ── The jobs ──────────────────────────────────────────────────────────────────

def test_upscale_replace_refuses_an_out_of_tree_thumbnail(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(20, 10)))
            outside_dir = await _thumb_outside(env, img["id"], tmp_path)

            r = await env.client.post(f"{API}/upscaling/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "model_path": MODEL, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            _assert_stale_not_failed(job)
            assert not outside_dir.exists()

    run(scenario())


def test_lut_replace_refuses_an_out_of_tree_thumbnail(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(20, 10)))
            outside_dir = await _thumb_outside(env, img["id"], tmp_path)
            lut = _identity_cube(tmp_path / "identity.cube")

            r = await env.client.post(f"{API}/lut/run", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
                "lut_path": str(lut), "intensity": 1.0, "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            _assert_stale_not_failed(job)
            assert not outside_dir.exists()

    run(scenario())


def test_detection_crop_replace_refuses_an_out_of_tree_thumbnail(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            async with env.Session() as db:
                db.add(Detection(
                    image_id=img["id"], label="face", bbox=[0.25, 0.25, 0.75, 0.75],
                    score=0.9, model="test", task="detect",
                ))
                await db.commit()
            outside_dir = await _thumb_outside(env, img["id"], tmp_path)

            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]], "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"])

            _assert_stale_not_failed(job, done_key="cropped")
            assert not outside_dir.exists()

    run(scenario())


@pytest.mark.parametrize("route,body", [
    ("resize", {"scale": 0.5}),
    ("crop", {"target_ar": 1.0}),
])
def test_batch_edits_refuse_an_out_of_tree_thumbnail(tmp_path, route, body):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            outside_dir = await _thumb_outside(env, img["id"], tmp_path)

            r = await env.client.post(
                f"{API}/images/batch/{route}", json={"image_ids": [img["id"]], **body}
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])

            _assert_stale_not_failed(job)
            assert not outside_dir.exists()

    run(scenario())


def test_inpaint_replace_still_refuses_an_out_of_tree_thumbnail(tmp_path, monkeypatch):
    """Inpaint gated first; pinned here beside the sites that copied it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_inpaint(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(48, 32)))
            await _add_detection(env, img["id"])
            outside_dir = await _thumb_outside(env, img["id"], tmp_path)

            started = await _run_inpaint(env, ds["id"], [img["id"]], replace=True)
            job = await wait_for_job(env, started["job_id"], timeout=60)

            _assert_stale_not_failed(job, done_key="inpainted")
            assert not outside_dir.exists()

    run(scenario())


# ── The single-image routes ───────────────────────────────────────────────────

@pytest.mark.parametrize("route,body", [
    ("resize", {"scale": 0.5}),
    ("crop", {"x": 0, "y": 0, "width": 10, "height": 10, "replace": True}),
])
def test_single_edits_refuse_an_out_of_tree_thumbnail(tmp_path, route, body):
    """No job, so no counter: the edit lands and the tile is left stale."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            outside_dir = await _thumb_outside(env, img["id"], tmp_path)

            r = await env.client.post(f"{API}/images/{img['id']}/{route}", json=body)

            assert r.status_code == 200, r.text
            assert not outside_dir.exists()
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
            assert (row.width, row.height) != (40, 20), "the edit itself must still land"

    run(scenario())


def test_crop_upscale_replace_refuses_an_out_of_tree_thumbnail(tmp_path, monkeypatch):
    """The thumbnail target is chosen at request time and carried to the job."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes(size=(40, 20)))
            outside_dir = await _thumb_outside(env, img["id"], tmp_path)

            r = await env.client.post(f"{API}/images/{img['id']}/crop", json={
                "x": 0, "y": 0, "width": 16, "height": 16,
                "upscale_model": MODEL, "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)

            assert job["status"] == "completed", job
            assert job["result_data"] == {"processed": 1, "thumbnails_stale": 1}
            assert not outside_dir.exists()

    run(scenario())
