"""A restore that loses files says so, and leaves each row matching its pixels (GPT-025).

`restore_snapshot` counted `files_failed`/`files_unavailable` and the router's
`_run` threw the return value away, so the job ended `completed` with
`result_data={}` and the modal toasted "Restore complete". Worse, Pass 2 had
already committed the snapshot's metadata, and `shutil.copy2` straight onto the
target truncated it first: a copy that failed left 100×80 in the DB over 40×40
pixels (or over no pixels at all).

Now:
- the object is copied to a `.tmp-*` in `objects/` and swapped in with
  `replace_retrying`, so a failed write leaves the old file intact and no temp;
- every counted failure is named in a capped `failed` list, with
  `failed_count`/`failure_summary` (PM-023's convention) written to the job's
  `result_data` by the restore and checkout routes;
- a row whose content was not written gets its pre-restore pixel values back and
  is re-measured, and a re-created row with no pixels is deleted. Its caption and
  other curation keep the snapshot's values — the metadata restore succeeded.
"""
import shutil
from pathlib import Path

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from backend.models import Dataset
from backend.models.image import Image
from backend.models.versioning import DatasetBranch
from backend.services import version_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.utils import FileInUseError


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


async def _row(env, img_id):
    async with env.Session() as db:
        return await db.get(Image, img_id)


async def _set(env, img_id, **values):
    async with env.Session() as db:
        img = await db.get(Image, img_id)
        for k, v in values.items():
            setattr(img, k, v)
        await db.commit()


async def _objects_dir(env, ds_id) -> Path:
    async with env.Session() as db:
        return Path((await db.get(Dataset, ds_id)).folder_path) / ".versions" / "objects"


async def _edited(env, mode="manual"):
    """A 100×80 image snapshotted as "orig", then replace-cropped to 40×40."""
    await _mode(env, mode)
    ds = await env.create_dataset("d")
    img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
    async with env.Session() as db:
        ver = await version_service.create_snapshot(db, ds["id"], "orig", "")
    await _crop(env, img["id"])
    return ds["id"], img["id"], ver.id


def _fail_object_copies(monkeypatch):
    """Break the object → temp copy half way through, leaving a partial temp."""
    real = shutil.copy2
    hits = {"n": 0}

    def selective(src, dst, *a, **k):
        if "/.versions/objects/" in Path(src).as_posix() and Path(dst).name.startswith(".tmp-"):
            hits["n"] += 1
            Path(dst).write_bytes(b"partial")
            raise OSError("injected object copy failure")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(version_service.shutil, "copy2", selective)
    return hits


async def _restore_job(env, ds_id, vid, **body):
    r = await env.client.post(
        f"{API}/datasets/{ds_id}/versions/{vid}/restore",
        json={"handle_extra_images": "keep", "pre_restore_snapshot": False, **body},
    )
    assert r.status_code == 200, r.text
    return await wait_for_job(env, r.json()["job_id"])


def test_a_failed_copy_completes_with_a_warning_and_keeps_db_and_pixels_agreeing(
    tmp_path, monkeypatch
):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, img_id, vid = await _edited(env)
            hits = _fail_object_copies(monkeypatch)
            job = await _restore_job(env, ds_id, vid)

            assert hits["n"] == 1
            assert job["status"] == "completed" and not job.get("error_msg")
            rd = job["result_data"]
            assert rd["failed_count"] == 1 and rd["files_failed"] == 1
            assert "x.png" in rd["failure_summary"] and len(rd["failure_summary"]) <= 300
            assert rd["failed"][0]["file"] == "x.png"

            row = await _row(env, img_id)
            assert (row.width, row.height) == (40, 40)
            assert _size(row.file_path) == (40, 40)
            assert row.file_size_bytes == Path(row.file_path).stat().st_size
            assert not list((await _objects_dir(env, ds_id)).glob(".tmp-*"))
    run(scenario())


def test_the_service_reports_the_failure(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, _, vid = await _edited(env)
            _fail_object_copies(monkeypatch)
            async with env.Session() as db:
                res = await version_service.restore_snapshot(
                    db, ds_id, vid, pre_restore_snapshot=False
                )
            assert res["files_failed"] == 1 and res["files_restored"] == 0
            assert res["failed"] == [
                {"file": "x.png", "error": "injected object copy failure"}
            ]
    run(scenario())


def test_a_locked_target_keeps_the_old_pixels(tmp_path, monkeypatch):
    """Windows: the swap can find the target held open. `replace_retrying`
    gives up with FileInUseError, and the file is untouched."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, img_id, vid = await _edited(env)

            async def locked(src, dst, **kw):
                raise FileInUseError("'x.png' is open in another program")
            monkeypatch.setattr(version_service, "replace_retrying", locked)
            async with env.Session() as db:
                res = await version_service.restore_snapshot(
                    db, ds_id, vid, pre_restore_snapshot=False
                )
            assert res["files_failed"] == 1
            assert "another program" in res["failed"][0]["error"]
            row = await _row(env, img_id)
            assert (row.width, row.height) == (40, 40) and _size(row.file_path) == (40, 40)
            assert not list((await _objects_dir(env, ds_id)).glob(".tmp-*"))
    run(scenario())


def test_pixel_values_come_back_while_the_snapshot_caption_stays(tmp_path, monkeypatch):
    """Pins the stated limit: only pixel-derived columns are reconciled."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            img = await upload_image(
                env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80))
            )
            await _set(env, img["id"], caption_text="snapshot caption",
                       aesthetic_score=1.0, blur_score=2.0)
            async with env.Session() as db:
                ver = await version_service.create_snapshot(db, ds["id"], "orig", "")
            await _crop(env, img["id"])
            await _set(env, img["id"], caption_text="later caption",
                       aesthetic_score=9.0, blur_score=8.0, scores_stale=False)

            _fail_object_copies(monkeypatch)
            async with env.Session() as db:
                await version_service.restore_snapshot(
                    db, ds["id"], ver.id, pre_restore_snapshot=False
                )
            row = await _row(env, img["id"])
            assert (row.aesthetic_score, row.blur_score) == (9.0, 8.0)
            assert row.scores_stale is False
            assert row.caption_text == "snapshot caption"
            assert (row.width, row.height) == (40, 40)
    run(scenario())


def test_a_failed_re_creation_leaves_no_row(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "b.png", png_bytes((5, 5, 5), size=(30, 20)))
            async with env.Session() as db:
                ver = await version_service.create_snapshot(db, ds["id"], "s", "")
            assert (await env.client.delete(f"{API}/images/{img['id']}")).status_code == 204

            _fail_object_copies(monkeypatch)
            async with env.Session() as db:
                res = await version_service.restore_snapshot(
                    db, ds["id"], ver.id, pre_restore_snapshot=False
                )
            assert res["images_re_created"] == 0 and res["failed_count"] == 1
            assert await _row(env, img["id"]) is None
            listing = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            assert listing["image_count"] == 0
    run(scenario())


def test_a_failed_pure_rename_is_counted(tmp_path, monkeypatch):
    """Auto mode, renamed since the snapshot and never overwritten: Pass 3
    moves the file back. A failed move used to be logged and nothing else."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((1, 2, 3)))
            async with env.Session() as db:
                ver = await version_service.create_snapshot(db, ds["id"], "s", "")
            old = Path((await _row(env, img["id"])).file_path)
            new = old.with_name("y.png")
            old.rename(new)
            await _set(env, img["id"], filename="y.png", file_path=str(new))

            import backend.utils as utils

            def refuse(src, dst):
                raise PermissionError(13, "injected rename failure")
            monkeypatch.setattr(utils, "rename_with_sidecar", refuse)
            async with env.Session() as db:
                res = await version_service.restore_snapshot(
                    db, ds["id"], ver.id, pre_restore_snapshot=False
                )
            assert res["files_failed"] == 1 and res["failed_count"] == 1
            assert res["failed"][0]["file"] == "x.png"
            row = await _row(env, img["id"])
            assert row.file_path == str(new) and new.exists()
    run(scenario())


def test_checkout_writes_result_data_and_still_switches_branch(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            img = await upload_image(
                env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80))
            )
            async with env.Session() as db:
                side, _ = await version_service.create_branch(db, ds["id"], "side")
            await _crop(env, img["id"])

            _fail_object_copies(monkeypatch)
            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions/branches/{side.id}/checkout",
                json={"pre_restore_snapshot": False},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job
            rd = job["result_data"]
            assert rd["branch_id"] == side.id and rd["restored"] is True
            assert rd["failed_count"] == 1 and rd["failed"][0]["file"] == "x.png"
            async with env.Session() as db:
                assert (await db.get(Dataset, ds["id"])).current_branch_id == side.id
    run(scenario())


def test_a_clean_restore_reports_no_failures(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, img_id, vid = await _edited(env)
            job = await _restore_job(env, ds_id, vid)
            assert job["status"] == "completed"
            rd = job["result_data"]
            assert rd["failed_count"] == 0 and rd["failure_summary"] is None
            assert rd["failed"] == [] and rd["files_restored"] == 1
            row = await _row(env, img_id)
            assert _size(row.file_path) == (100, 80) == (row.width, row.height)
    run(scenario())


def test_a_non_utf8_sidecar_does_not_abort_the_restore(tmp_path):
    """`_sync_caption_sidecar` caught only OSError, so a UnicodeDecodeError
    escaped Pass 3b and skipped the final commit — the head never moved and
    every compensating fixup was lost."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds_id, img_id, vid = await _edited(env)
            path = Path((await _row(env, img_id)).file_path)
            path.with_suffix(".txt").write_bytes(b"\xff\xfe not utf-8 \x80")
            # A non-empty snapshot caption, so the sync reads the sidecar.
            from backend.models.versioning import VersionImageState
            async with env.Session() as db:
                st = (await db.execute(select(VersionImageState).where(
                    VersionImageState.version_id == vid))).scalars().one()
                st.caption_text = "hello"
                await db.commit()
            async with env.Session() as db:
                ver = await version_service.create_snapshot(db, ds_id, "later", "")

            async with env.Session() as db:
                res = await version_service.restore_snapshot(
                    db, ds_id, vid, pre_restore_snapshot=False
                )
            assert res["failed_count"] == 0
            async with env.Session() as db:
                head = (await db.execute(
                    select(DatasetBranch.head_version_id).where(
                        DatasetBranch.dataset_id == ds_id)
                )).scalar_one()
            assert head == vid != ver.id
            assert _size(path) == (100, 80)
    run(scenario())


@pytest.mark.parametrize("n", [1, 5])
def test_failure_summary_is_bounded_and_names_at_most_three(n):
    failures = [{"file": f"{'f' * 120}{i}.png", "error": "e" * 200} for i in range(n)]
    s = version_service._failure_summary(n, failures)
    assert len(s) <= 300 and s.startswith(f"{n} file(s) could not be restored")
    assert version_service._failure_summary(0, []) is None
