"""Copy-on-write protection is gated on lazy snapshot rows, not on the mode (GPT-022).

An Auto snapshot records `file_hash=NULL` for every image — "unchanged since the
snapshot" — and relies on the overwrite hook to back the bytes up before they
change. `protect_file_before_overwrite` used to return early outside Auto, and
`mark_image_deleted_in_versions` in Off, so after Auto → Manual (or Off) an
overwrite left the lazy row NULL over a file that no longer matched it. A restore
read the NULL as "unchanged": it reported 0 failed and wrote the snapshot's
100×80 metadata over 40×40 pixels.

Both hooks now fire in every mode and open with `_has_lazy_rows`, so the rule is
"fill the sentinel before falsifying it", whatever the configuration says.
"""
from pathlib import Path

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from backend.models import Dataset
from backend.models.image import Image
from backend.models.versioning import VersionImageState
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


async def _row(env, img_id):
    async with env.Session() as db:
        return await db.get(Image, img_id)


async def _state(env, version_id):
    async with env.Session() as db:
        return (await db.execute(
            select(VersionImageState).where(VersionImageState.version_id == version_id)
        )).scalars().one()


async def _objects(env, ds_id) -> set[str]:
    async with env.Session() as db:
        folder = (await db.get(Dataset, ds_id)).folder_path
    root = Path(folder) / ".versions" / "objects"
    return {p.name for p in root.rglob("*") if p.is_file()} if root.exists() else set()


async def _auto_snapshot(env, ds_id, name="autosnap"):
    async with env.Session() as db:
        ver = await version_service.create_snapshot(db, ds_id, name, "")
    assert (await _state(env, ver.id)).file_hash is None   # lazy
    return ver.id


@pytest.mark.parametrize("later_mode", ["manual", "off"])
def test_an_auto_snapshot_stays_restorable_after_the_mode_changes(tmp_path, later_mode):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            ds = await env.create_dataset("d")
            img = await upload_image(
                env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80))
            )
            vid = await _auto_snapshot(env, ds["id"])

            await _mode(env, later_mode)
            await _crop(env, img["id"])
            # The overwrite filled the lazy row first.
            assert (await _state(env, vid)).file_hash is not None

            if later_mode == "manual":
                r = await env.client.post(
                    f"{API}/datasets/{ds['id']}/versions/{vid}/restore",
                    json={"handle_extra_images": "keep", "pre_restore_snapshot": False},
                )
                assert r.status_code == 200, r.text
                job = await wait_for_job(env, r.json()["job_id"])
                assert job["status"] == "completed", job
            else:
                # Off refuses the restore route, so restore through the service:
                # what matters is that the snapshot was kept restorable.
                async with env.Session() as db:
                    res = await version_service.restore_snapshot(
                        db, ds["id"], vid, pre_restore_snapshot=False
                    )
                assert res["files_failed"] == 0 and res["files_unavailable"] == 0
                assert res["files_restored"] == 1

            row = await _row(env, img["id"])
            assert (row.width, row.height) == (100, 80)
            assert _size(row.file_path) == (100, 80)
    run(scenario())


def test_control_staying_in_auto_restores(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            ds = await env.create_dataset("d")
            img = await upload_image(
                env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80))
            )
            vid = await _auto_snapshot(env, ds["id"])
            await _crop(env, img["id"])
            async with env.Session() as db:
                res = await version_service.restore_snapshot(
                    db, ds["id"], vid, pre_restore_snapshot=False
                )
            assert res["files_restored"] == 1
            row = await _row(env, img["id"])
            assert (row.width, row.height) == (100, 80)
            assert _size(row.file_path) == (100, 80)
    run(scenario())


def test_a_delete_in_off_mode_backs_up_the_lazy_rows(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            ds = await env.create_dataset("d")
            img = await upload_image(
                env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80))
            )
            vid = await _auto_snapshot(env, ds["id"])

            await _mode(env, "off")
            assert (await env.client.delete(f"{API}/images/{img['id']}")).status_code == 204
            st = await _state(env, vid)
            assert st.file_hash is not None
            async with env.Session() as db:
                folder = (await db.get(Dataset, ds["id"])).folder_path
            assert version_service._object_store_path(folder, st.file_hash).is_file()

            async with env.Session() as db:
                res = await version_service.restore_snapshot(
                    db, ds["id"], vid, pre_restore_snapshot=False
                )
            assert res["images_re_created"] == 1 and res["files_restored"] == 1
            row = await _row(env, img["id"])
            assert row is not None and _size(row.file_path) == (100, 80)
    run(scenario())


def test_a_fully_hashed_manual_snapshot_stores_nothing_on_overwrite(tmp_path):
    """The other side of the gate: with no lazy rows the hook is one query and
    no copy, so Manual mode pays nothing new for the change."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            img = await upload_image(
                env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80))
            )
            async with env.Session() as db:
                ver = await version_service.create_snapshot(db, ds["id"], "m", "")
            hashed = (await _state(env, ver.id)).file_hash
            assert hashed is not None
            before = await _objects(env, ds["id"])

            await _crop(env, img["id"])
            assert await _objects(env, ds["id"]) == before
            assert (await _state(env, ver.id)).file_hash == hashed
    run(scenario())
