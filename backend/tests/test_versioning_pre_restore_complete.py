"""The pre-restore safety snapshot is complete or the restore does not start (GPT-023).

Manual `create_snapshot` catches each per-file copy failure, leaves that row's
`file_hash` NULL and still commits the version with a `[warning]` line. NULL is
read by a restore as "content unchanged", so a pre-restore snapshot with a hole
recorded the edited file as unchanged while the restore overwrote it: restoring
the safety version then put the edited image's metadata over the original's
pixels and reported 0 failed.

`create_snapshot(require_complete=True)` — passed only by the pre-restore call —
now rolls back and raises `SnapshotIncomplete` before anything is committed, and
restore's wrapper turns that into "aborted before any changes were made". A
snapshot the user takes keeps the commit-with-warning behaviour.
"""
import pytest
from PIL import Image as PILImage
from sqlalchemy import func, select

from backend.models.image import Image
from backend.models.versioning import DatasetBranch, DatasetVersion
from backend.services import version_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


async def _mode(env, mode):
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": mode})
    assert r.status_code == 200, r.text


async def _crop(env, img_id, w=40, h=40):
    r = await env.client.post(
        f"{API}/images/{img_id}/crop",
        json={"x": 0, "y": 0, "width": w, "height": h, "replace": True},
    )
    assert r.status_code == 200, r.text


def _size(p):
    with PILImage.open(p) as im:
        return im.size


async def _edited_manual_dataset(env):
    """Manual mode; snapshot "orig" of a 100×80 image, then a crop to 40×40."""
    await _mode(env, "manual")
    ds = await env.create_dataset("d")
    img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
    async with env.Session() as db:
        ver = await version_service.create_snapshot(db, ds["id"], "orig", "")
    await _crop(env, img["id"])
    return ds["id"], img["id"], ver.id


def _break_the_object_store(monkeypatch):
    def boom(folder, src, *a, **k):
        raise OSError("injected backup failure")
    monkeypatch.setattr(version_service, "_store_object", boom)


async def _versions_and_head(env, ds_id):
    async with env.Session() as db:
        n = (await db.execute(
            select(func.count(DatasetVersion.id)).where(DatasetVersion.dataset_id == ds_id)
        )).scalar()
        pre = (await db.execute(
            select(func.count(DatasetVersion.id)).where(
                DatasetVersion.dataset_id == ds_id, DatasetVersion.source == "pre_restore"
            )
        )).scalar()
        head = (await db.execute(
            select(DatasetBranch.head_version_id).where(DatasetBranch.dataset_id == ds_id)
        )).scalar_one()
    return n, pre, head


def test_an_incomplete_pre_restore_snapshot_aborts_the_restore(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, img_id, vid = await _edited_manual_dataset(env)
            before = await _versions_and_head(env, ds_id)
            _break_the_object_store(monkeypatch)

            async with env.Session() as db:
                with pytest.raises(RuntimeError, match="could not be backed up") as ei:
                    await version_service.restore_snapshot(
                        db, ds_id, vid, pre_restore_snapshot=True
                    )
            assert "aborted before any changes were made" in str(ei.value)

            n, pre, head = await _versions_and_head(env, ds_id)
            assert pre == 0                       # no orphan safety version
            assert (n, head) == (before[0], before[2])
            async with env.Session() as db:
                row = await db.get(Image, img_id)
            assert (row.width, row.height) == (40, 40)
            assert _size(row.file_path) == (40, 40)
    run(scenario())


def test_an_incomplete_pre_restore_snapshot_fails_the_job(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, img_id, vid = await _edited_manual_dataset(env)
            _break_the_object_store(monkeypatch)
            r = await env.client.post(
                f"{API}/datasets/{ds_id}/versions/{vid}/restore",
                json={"handle_extra_images": "keep", "pre_restore_snapshot": True},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "failed", job
            assert "1 file(s) could not be backed up" in job["error_msg"]
            async with env.Session() as db:
                row = await db.get(Image, img_id)
            assert _size(row.file_path) == (40, 40)
    run(scenario())


def test_a_user_manual_snapshot_still_commits_with_its_warning(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10)))
            _break_the_object_store(monkeypatch)
            async with env.Session() as db:
                ver = await version_service.create_snapshot(db, ds["id"], "s", "")
            async with env.Session() as db:
                stored = await db.get(DatasetVersion, ver.id)
            assert stored is not None
            assert "[warning] 1 file(s) could not be backed up" in stored.description
    run(scenario())
