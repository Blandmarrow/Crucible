from PIL import Image as PILImage
from sqlalchemy import select
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.models.image import Image
from backend.models.detection import Detection
from backend.services import version_service


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
