"""In-place crops and resizes: stage, flush, swap, commit (GPT-010, GPT-016).

Every replace-mode crop overwrote the original and only *then* remapped the
image's detections — DB work that can fail (a locked database, a malformed
stored mask) — before the commit. A failure there rolled the row back onto
pixels that no longer matched it: the disk held the crop, the row the old
geometry, and the `processing_history` entry that pass 2's re-extraction guard
reads was gone (PM-010, PM-013).

Now each site writes to a temp file, stages every row field and the remap,
flushes while the original is still intact, and swaps the output over it last.
These pin both halves: a failure anywhere before the swap leaves the original
pixels, the row and the temp directory exactly as they were; a success leaves
the row describing the written file — including the `file_size_bytes` and
`phash` that resize and batch crop used to leave stale (GPT-016).
"""

import io
import json
import random
from pathlib import Path

import httpx
import imagehash
from PIL import Image as PilImage
from sqlalchemy import select, text

from backend.models.detection import Detection
from backend.models.image import Image
from backend.tests.conftest import API, api_env, run, upload_image, wait_for_job
from backend.utils import FileInUseError


def _patterned_png(w: int = 64, h: int = 48) -> bytes:
    """Not a flat colour: a solid image's pHash is all zeros at any size, which
    would make every phash assertion here pass vacuously."""
    rnd = random.Random(0)
    im = PilImage.new("RGB", (w, h))
    px = im.load()
    cols = [(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(40)]
    for y in range(h):
        for x in range(w):
            px[x, y] = cols[((x // 6) * 7 + (y // 6) * 13) % 40]
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _client():
    from backend.main import app
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


async def _row(env, image_id) -> Image:
    async with env.Session() as db:
        return await db.get(Image, image_id)


def _disk_size(path) -> tuple[int, int]:
    with PilImage.open(path) as f:
        return f.size


def _assert_row_describes_its_file(row: Image) -> None:
    path = Path(row.file_path)
    assert (row.width, row.height) == _disk_size(path)
    assert row.file_size_bytes == path.stat().st_size
    with PilImage.open(path) as f:
        assert row.phash == str(imagehash.phash(f.convert("RGB")))


def _no_temp_files(row: Image) -> None:
    leftovers = [p.name for p in Path(row.file_path).parent.iterdir() if "tmp" in p.stem]
    assert leftovers == [], leftovers


async def _boom(*_a, **_k):
    raise OSError("simulated remap failure")


async def _with_detection(env, image_id, bbox=(0.1, 0.1, 0.9, 0.9)):
    async with env.Session() as db:
        db.add(Detection(
            image_id=image_id, label="face", bbox=list(bbox), score=0.9, model="test", task="detect",
        ))
        await db.commit()


# ── GPT-010: a failure before the swap leaves everything as it was ────────────

def test_a_failed_remap_leaves_the_single_crop_untouched(tmp_path, monkeypatch):
    import backend.routers.images as images_router

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            before = await _row(env, img["id"])
            monkeypatch.setattr(images_router, "remap_detections_for_crop", _boom)

            async with _client() as c:
                r = await c.post(f"{API}/images/{img['id']}/crop",
                                 json={"x": 0, "y": 0, "width": 16, "height": 16, "replace": True})
            assert r.status_code == 500

            after = await _row(env, img["id"])
            assert _disk_size(after.file_path) == (64, 48)
            assert (after.width, after.height) == (64, 48)
            assert not after.processing_history
            assert after.phash == before.phash
            _no_temp_files(after)

    run(scenario())


def test_a_locked_original_409s_the_single_crop_and_changes_nothing(tmp_path, monkeypatch):
    import backend.routers.images as images_router

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            await _with_detection(env, img["id"])

            async def locked(*_a, **_k):
                raise FileInUseError("'a.png' is open in another program")

            monkeypatch.setattr(images_router, "replace_retrying", locked)
            async with _client() as c:
                r = await c.post(f"{API}/images/{img['id']}/crop",
                                 json={"x": 0, "y": 0, "width": 16, "height": 16, "replace": True})
            assert r.status_code == 409, r.text

            after = await _row(env, img["id"])
            assert _disk_size(after.file_path) == (64, 48) == (after.width, after.height)
            assert not after.processing_history
            async with env.Session() as db:
                det = (await db.execute(select(Detection))).scalar_one()
            assert det.bbox == [0.1, 0.1, 0.9, 0.9], "the remap was not rolled back"
            _no_temp_files(after)

    run(scenario())


def test_a_failed_batch_crop_item_leaves_it_untouched_and_the_next_one_crops(tmp_path, monkeypatch):
    """The first item's rollback expires the session; the second must still run."""
    import backend.routers.images as images_router

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", _patterned_png())
            b = await upload_image(env, ds["id"], "b.png", _patterned_png(48, 64))
            original = images_router.remap_detections_for_crop

            async def fail_first(session, image_id, *args):
                if image_id == a["id"]:
                    raise OSError("simulated remap failure")
                return await original(session, image_id, *args)

            monkeypatch.setattr(images_router, "remap_detections_for_crop", fail_first)
            r = await env.client.post(f"{API}/images/batch/crop",
                                      json={"image_ids": [a["id"], b["id"]], "target_ar": 1.0})
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["failed"] == 1 and job["result_data"]["processed"] == 1

            row_a = await _row(env, a["id"])
            assert _disk_size(row_a.file_path) == (64, 48) == (row_a.width, row_a.height)
            assert not row_a.processing_history
            row_b = await _row(env, b["id"])
            assert (row_b.width, row_b.height) == (48, 48)
            assert [h["op"] for h in row_b.processing_history] == ["crop_aspect"]
            _assert_row_describes_its_file(row_b)
            _no_temp_files(row_a)

    run(scenario())


def test_a_failed_detection_crop_item_leaves_it_untouched_and_the_next_one_crops(tmp_path, monkeypatch):
    import backend.routers.detection as detection_router

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", _patterned_png(40, 20))
            b = await upload_image(env, ds["id"], "b.png", _patterned_png(40, 20))
            for img in (a, b):
                await _with_detection(env, img["id"], (0.25, 0.25, 0.75, 0.75))
            original = detection_router.remap_detections_for_crop

            async def fail_first(session, image_id, *args):
                if image_id == a["id"]:
                    raise OSError("simulated remap failure")
                return await original(session, image_id, *args)

            monkeypatch.setattr(detection_router, "remap_detections_for_crop", fail_first)
            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [a["id"], b["id"]], "replace": True,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["failed"] == 1 and job["result_data"]["cropped"] == 1

            row_a = await _row(env, a["id"])
            assert _disk_size(row_a.file_path) == (40, 20) == (row_a.width, row_a.height)
            assert not row_a.processing_history
            row_b = await _row(env, b["id"])
            assert (row_b.width, row_b.height) == (20, 10)
            _assert_row_describes_its_file(row_b)
            _no_temp_files(row_a)

    run(scenario())


def test_a_failed_remap_leaves_crop_upscale_untouched(tmp_path, monkeypatch):
    import backend.routers.images as images_router
    from backend.tests.test_upscale_png_fallback_http import MODEL, _install_fake

    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            monkeypatch.setattr(images_router, "remap_detections_for_crop", _boom)

            r = await env.client.post(f"{API}/images/{img['id']}/crop", json={
                "x": 0, "y": 0, "width": 16, "height": 16, "replace": True, "upscale_model": MODEL,
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["status"] == "failed", job

            after = await _row(env, img["id"])
            assert _disk_size(after.file_path) == (64, 48) == (after.width, after.height)
            assert not after.processing_history
            _no_temp_files(after)

    run(scenario())


def test_a_malformed_stored_mask_no_longer_blocks_a_crop(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            await _with_detection(env, img["id"])
            async with env.Session() as db:
                # Raw SQL: the ORM's mask_area listener would compute on the way in.
                await db.execute(text("UPDATE detections SET mask=:m"), {"m": json.dumps({"polygons": 5})})
                await db.commit()

            r = await env.client.post(f"{API}/images/{img['id']}/crop",
                                      json={"x": 0, "y": 0, "width": 32, "height": 24, "replace": True})
            assert r.status_code == 200, r.text
            row = await _row(env, img["id"])
            assert (row.width, row.height) == (32, 24)
            async with env.Session() as db:
                det = (await db.execute(select(Detection))).scalar_one()
            assert det.mask is None and det.bbox is not None

    run(scenario())


# ── GPT-016: the row describes the written file ───────────────────────────────

def test_single_resize_refreshes_size_phash_and_dataset_totals(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            r = await env.client.post(f"{API}/images/{img['id']}/resize", json={"scale": 0.5})
            assert r.status_code == 200, r.text

            row = await _row(env, img["id"])
            assert (row.width, row.height) == (32, 24)
            _assert_row_describes_its_file(row)
            _no_temp_files(row)
            total = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()["total_size_bytes"]
            assert total == row.file_size_bytes

    run(scenario())


def test_batch_resize_refreshes_size_phash_and_dataset_totals(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            r = await env.client.post(f"{API}/images/batch/resize",
                                      json={"image_ids": [img["id"]], "scale": 0.5})
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            row = await _row(env, img["id"])
            assert (row.width, row.height) == (32, 24)
            _assert_row_describes_its_file(row)
            total = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()["total_size_bytes"]
            assert total == row.file_size_bytes

    run(scenario())


def test_batch_crop_refreshes_size_phash_and_dataset_totals(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            r = await env.client.post(f"{API}/images/batch/crop",
                                      json={"image_ids": [img["id"]], "target_ar": 1.0})
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            row = await _row(env, img["id"])
            assert (row.width, row.height) == (48, 48)
            _assert_row_describes_its_file(row)
            total = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()["total_size_bytes"]
            assert total == row.file_size_bytes

    run(scenario())


def test_a_failed_stats_refresh_does_not_fail_a_batch_whose_edits_committed(tmp_path, monkeypatch):
    """`refresh_stats` ends in a commit; a failed one leaves the session needing a
    rollback, and without it the job's own `result_data` write raised
    `PendingRollbackError` and failed a run whose every edit had committed."""
    import backend.routers.images as images_router

    async def broken_refresh(db, _dataset_id):
        db.add(Image())  # NOT NULL columns unset: the flush inside commit fails
        await db.commit()

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            monkeypatch.setattr(images_router, "refresh_stats", broken_refresh)
            r = await env.client.post(f"{API}/images/batch/resize",
                                      json={"image_ids": [img["id"]], "scale": 0.5})
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            assert job["result_data"]["processed"] == 1
            assert (await _row(env, img["id"])).width == 32

    run(scenario())


def test_a_detection_crop_refreshes_dataset_totals(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png(40, 20))
            await _with_detection(env, img["id"], (0.25, 0.25, 0.75, 0.75))
            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]], "replace": True,
            })
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            row = await _row(env, img["id"])
            _assert_row_describes_its_file(row)
            total = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()["total_size_bytes"]
            assert total == row.file_size_bytes

    run(scenario())


def test_crop_upscale_refreshes_the_phash(tmp_path, monkeypatch):
    """`upscale_image_sync` reports no phash, so the row kept the pre-crop one."""
    from backend.tests.test_upscale_png_fallback_http import MODEL, _install_fake

    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake(monkeypatch)
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            r = await env.client.post(f"{API}/images/{img['id']}/crop", json={
                "x": 0, "y": 0, "width": 16, "height": 16, "replace": True, "upscale_model": MODEL,
            })
            assert r.status_code == 200, r.text
            assert (await wait_for_job(env, r.json()["job_id"], timeout=60))["status"] == "completed"
            row = await _row(env, img["id"])
            _assert_row_describes_its_file(row)
            _no_temp_files(row)

    run(scenario())


def test_a_rewrite_never_writes_over_an_image_named_like_its_temp_file(tmp_path):
    """The temp names were fixed (`{stem}_croptmp{suffix}`), and the images folder
    is the user's: a registered `a_croptmp.png` — a stray a rescan adopted — was
    overwritten by the crop and then swapped away onto `a.png`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", _patterned_png())
            other = await upload_image(env, ds["id"], "a_croptmp.png", _patterned_png(40, 40))
            other_path = Path((await _row(env, other["id"])).file_path)
            assert other_path.name == "a_croptmp.png"
            before = other_path.read_bytes()

            r = await env.client.post(f"{API}/images/{img['id']}/crop", json={
                "x": 0, "y": 0, "width": 16, "height": 16, "replace": True,
            })
            assert r.status_code == 200, r.text
            assert other_path.read_bytes() == before
            _assert_row_describes_its_file(await _row(env, img["id"]))
            _assert_row_describes_its_file(await _row(env, other["id"]))

    run(scenario())
