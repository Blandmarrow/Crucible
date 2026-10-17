import hashlib, shutil
from pathlib import Path
import pytest
from PIL import Image as PILImage
from sqlalchemy import select
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.models.image import Image
from backend.models.detection import Detection
from backend.models.versioning import DatasetVersion, DatasetBranch, VersionImageState
from backend.services import version_service
from backend.services.dataset_busy import busy


async def _mode(env, mode):
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": mode})
    assert r.status_code == 200, r.text


async def _snap(env, ds_id, name, branch_id=None):
    r = await env.client.post(f"{API}/datasets/{ds_id}/versions", json={"name": name, "branch_id": branch_id})
    assert r.status_code == 200, r.text
    body = r.json()
    if "job_id" in body:
        j = await wait_for_job(env, body["job_id"])
        assert j["status"] == "completed", j
    vs = (await env.client.get(f"{API}/datasets/{ds_id}/versions")).json()
    return next(v for v in vs if v["name"] == name)["id"]


async def _crop(env, img_id, x=0, y=0, w=40, h=40):
    r = await env.client.post(f"{API}/images/{img_id}/crop",
                              json={"x": x, "y": y, "width": w, "height": h, "replace": True})
    assert r.status_code == 200, r.text


def _size(p):
    with PILImage.open(p) as im:
        return im.size


async def _row(env, img_id):
    async with env.Session() as db:
        r = await db.get(Image, img_id)
        return r.file_path, r.width, r.height


async def _restore_svc(env, ds_id, vid, **kw):
    async with env.Session() as db:
        return await version_service.restore_snapshot(db, ds_id, vid, **kw)


def test_gpt022_auto_to_manual_unprotected(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
            vid = await _snap(env, ds["id"], "autosnap")
            async with env.Session() as db:
                st = (await db.execute(select(VersionImageState).where(VersionImageState.version_id == vid))).scalars().one()
                print("auto snapshot hash:", st.file_hash)
                assert st.file_hash is None
            await _mode(env, "manual")
            await _crop(env, img["id"])
            path, w, h = await _row(env, img["id"])
            print("after crop", _size(path), w, h)
            res = await _restore_svc(env, ds["id"], vid, pre_restore_snapshot=False)
            path, w, h = await _row(env, img["id"])
            print("restore result", res, "DB", (w, h), "pixels", _size(path))
            assert res["files_failed"] == 0 and res["files_unavailable"] == 0
            assert (w, h) == (100, 80) and _size(path) == (40, 40)
    run(scenario())


def test_gpt022_control_stay_auto(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
            vid = await _snap(env, ds["id"], "autosnap")
            await _crop(env, img["id"])
            res = await _restore_svc(env, ds["id"], vid, pre_restore_snapshot=False)
            path, w, h = await _row(env, img["id"])
            print("control", res, (w, h), _size(path))
            assert (w, h) == (100, 80) and _size(path) == (100, 80)
    run(scenario())


def test_gpt023_failed_backup_does_not_abort_restore(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
            vid = await _snap(env, ds["id"], "orig")
            await _crop(env, img["id"])
            real = version_service._store_object
            calls = {"n": 0}
            def boom(folder, src, *a, **k):
                calls["n"] += 1
                raise OSError("injected backup failure")
            monkeypatch.setattr(version_service, "_store_object", boom)
            res = await _restore_svc(env, ds["id"], vid, pre_restore_snapshot=True)
            monkeypatch.setattr(version_service, "_store_object", real)
            print("restore result", res, "store calls", calls)
            safety = res["pre_restore_version_id"]
            async with env.Session() as db:
                sv = await db.get(DatasetVersion, safety)
                st = (await db.execute(select(VersionImageState).where(VersionImageState.version_id == safety))).scalars().one()
                print("safety desc:", repr(sv.description), "hash:", st.file_hash, "dims", st.width, st.height)
            path, w, h = await _row(env, img["id"])
            print("after restore1 DB", (w, h), "pixels", _size(path))
            assert (w, h) == (100, 80)
            assert st.file_hash is None and "[warning]" in sv.description
            res2 = await _restore_svc(env, ds["id"], safety, pre_restore_snapshot=False)
            path, w, h = await _row(env, img["id"])
            print("restore safety:", res2, "DB", (w, h), "pixels", _size(path))
            assert res2["files_failed"] == 0 and res2["files_unavailable"] == 0
            assert (w, h) == (40, 40) and _size(path) == (100, 80)
    run(scenario())


def test_gpt024_foreign_branch(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            A = await env.create_dataset("A"); B = await env.create_dataset("B")
            await upload_image(env, A["id"], "a.png", png_bytes((1, 2, 3)))
            await upload_image(env, B["id"], "b.png", png_bytes((3, 2, 1)))
            await _snap(env, A["id"], "a1"); await _snap(env, B["id"], "b1")
            bb = (await env.client.get(f"{API}/datasets/{B['id']}/versions/branches")).json()[0]
            ab = (await env.client.get(f"{API}/datasets/{A['id']}/versions/branches")).json()[0]
            print("B branch head before", bb["head_version_id"])
            r = await env.client.post(f"{API}/datasets/{A['id']}/versions", json={"name": "x", "branch_id": bb["id"]})
            print("status", r.status_code, r.text[:300])
            assert r.status_code == 200
            vid = r.json()["id"]
            bb2 = (await env.client.get(f"{API}/datasets/{B['id']}/versions/branches")).json()[0]
            ver = (await env.client.get(f"{API}/datasets/{A['id']}/versions/{vid}")).json()
            print("B head after", bb2["head_version_id"], "== A's new version?", bb2["head_version_id"] == vid, "version.dataset_id", ver["dataset_id"], "branch_id", ver["branch_id"])
            assert bb2["head_version_id"] == vid and ver["dataset_id"] == A["id"]
    run(scenario())


def test_gpt025_failed_copy_job_still_completed(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
            vid = await _snap(env, ds["id"], "orig")
            await _crop(env, img["id"])
            real = shutil.copy2
            hits = {"n": 0}
            def selective(src, dst, *a, **k):
                if "/.versions/objects/" in str(src) and ".versions" not in str(dst):
                    hits["n"] += 1
                    raise OSError("injected object->target copy failure")
                return real(src, dst, *a, **k)
            monkeypatch.setattr(version_service.shutil, "copy2", selective)
            r = await env.client.post(f"{API}/datasets/{ds['id']}/versions/{vid}/restore",
                                      json={"handle_extra_images": "keep", "pre_restore_snapshot": False})
            j = await wait_for_job(env, r.json()["job_id"])
            print("job", j["status"], "error_msg", j.get("error_msg"), "result_data", j.get("result_data"), "hits", hits)
            path, w, h = await _row(env, img["id"])
            print("DB", (w, h), "pixels", _size(path))
            assert j["status"] == "completed" and not j.get("error_msg") and not j.get("result_data")
            assert (w, h) == (100, 80) and _size(path) == (40, 40) and hits["n"] == 1
    run(scenario())


def test_gpt025_service_returns_files_failed(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
            vid = await _snap(env, ds["id"], "orig")
            await _crop(env, img["id"])
            real = shutil.copy2
            def selective(src, dst, *a, **k):
                if "/.versions/objects/" in str(src) and ".versions" not in str(dst):
                    raise OSError("injected")
                return real(src, dst, *a, **k)
            monkeypatch.setattr(version_service.shutil, "copy2", selective)
            res = await _restore_svc(env, ds["id"], vid, pre_restore_snapshot=False)
            print("service result", res)
            assert res["files_failed"] == 1
    run(scenario())


def test_gpt026_detection_geometry_not_restored(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((200, 10, 10), size=(100, 80)))
            async with env.Session() as db:
                db.add(Detection(image_id=img["id"], label="face", bbox=[0.2, 0.2, 0.4, 0.4], score=0.9, model="m", task="t"))
                await db.commit()
            vid = await _snap(env, ds["id"], "orig")
            await _crop(env, img["id"], x=10, y=8, w=50, h=40)
            async def bbox():
                async with env.Session() as db:
                    d = (await db.execute(select(Detection).where(Detection.image_id == img["id"]))).scalars().one()
                    return d.bbox
            print("after crop bbox", await bbox())
            await _restore_svc(env, ds["id"], vid, pre_restore_snapshot=False)
            path, w, h = await _row(env, img["id"])
            print("after restore bbox", await bbox(), "DB", (w, h), "pixels", _size(path))
            assert _size(path) == (100, 80)
            b = await bbox()
            assert [round(v, 3) for v in b] == [0.2, 0.2, 0.6, 0.6]
    run(scenario())


def test_gpt015_rename_and_delete_bypass_busy(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((1, 2, 3)))
            old = Path(ds["folder_path"])
            with busy(ds["id"], "restore"):
                # control: guarded endpoint
                r = await env.client.delete(f"{API}/images/{img['id']}")
                print("control image DELETE under busy:", r.status_code, r.text[:80])
                assert r.status_code == 409
                r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"name": "renamed"})
                print("PATCH rename under busy:", r.status_code, r.json().get("folder_path"))
                assert r.status_code == 200
                assert not old.exists() and Path(r.json()["folder_path"]).exists()
            ds2 = await env.create_dataset("d2")
            with busy(ds2["id"], "restore"):
                r = await env.client.delete(f"{API}/datasets/{ds2['id']}")
                print("DELETE dataset under busy:", r.status_code)
                assert r.status_code == 204
    run(scenario())
