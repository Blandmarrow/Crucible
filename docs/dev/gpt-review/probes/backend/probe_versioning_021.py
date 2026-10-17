import hashlib
from pathlib import Path
from sqlalchemy import select
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.models.image import Image
from backend.models.versioning import VersionImageState


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


async def _mode(env, mode):
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": mode})
    assert r.status_code == 200, r.text


async def _snap(env, ds_id, name):
    r = await env.client.post(f"{API}/datasets/{ds_id}/versions", json={"name": name})
    assert r.status_code == 200, r.text
    body = r.json()
    if "job_id" in body:
        j = await wait_for_job(env, body["job_id"])
        assert j["status"] == "completed", j
    vs = (await env.client.get(f"{API}/datasets/{ds_id}/versions")).json()
    return next(v for v in vs if v["name"] == name)["id"]


def test_gpt021_name_reuse_overwrites_B(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            A = await env.create_dataset("reused-name")
            a_img = await upload_image(env, A["id"], "x.png", png_bytes((200, 10, 10)))
            a_hash = hashlib.sha256(png_bytes((200, 10, 10))).hexdigest()
            vid = await _snap(env, A["id"], "snapA")
            old_root = Path((await env.client.get(f"{API}/datasets/{A['id']}")).json()["folder_path"])
            r = await env.client.patch(f"{API}/datasets/{A['id']}", json={"name": "new-root-name"})
            assert r.status_code == 200, r.text
            new_root = Path(r.json()["folder_path"])
            print("old_root", old_root, "new_root", new_root)
            B = await env.create_dataset("reused-name")
            b_folder = Path(B["folder_path"])
            assert b_folder == old_root, (b_folder, old_root)
            b_img = await upload_image(env, B["id"], "x.png", png_bytes((10, 10, 200)))
            b_before = sha(b_img["file_path"]) if "file_path" in b_img else None
            async with env.Session() as db:
                b_row = await db.get(Image, b_img["id"])
                b_path = b_row.file_path
                a_row = await db.get(Image, a_img["id"])
                print("A row path after rename", a_row.file_path)
                st = (await db.execute(select(VersionImageState).where(VersionImageState.image_id == a_img["id"]))).scalars().first()
                print("state path", st.file_path)
            b_before = sha(b_path)
            assert b_before != a_hash
            r = await env.client.post(f"{API}/datasets/{A['id']}/versions/{vid}/restore",
                                      json={"handle_extra_images": "keep", "pre_restore_snapshot": True})
            assert r.status_code == 200, r.text
            j = await wait_for_job(env, r.json()["job_id"])
            print("job", j["status"], j.get("error_msg"), j.get("result_data"))
            assert j["status"] == "completed"
            b_after = sha(b_path)
            async with env.Session() as db:
                a_row = await db.get(Image, a_img["id"]); b_row = await db.get(Image, b_img["id"])
                print("A row path", a_row.file_path, "B row path", b_row.file_path)
                print("A new-root file exists", (new_root / "images" / "x.png").exists())
            print("B before", b_before[:12], "after", b_after[:12], "A original", a_hash[:12])
            assert b_after != b_before          # claim: B overwritten
            assert b_after == a_hash            # with A's original bytes
            assert a_row.file_path == b_row.file_path
    run(scenario())


def test_gpt021_control_no_reuse(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "manual")
            A = await env.create_dataset("reused-name")
            a_img = await upload_image(env, A["id"], "x.png", png_bytes((200, 10, 10)))
            a_hash = hashlib.sha256(png_bytes((200, 10, 10))).hexdigest()
            vid = await _snap(env, A["id"], "snapA")
            old_root = Path(A["folder_path"])
            r = await env.client.patch(f"{API}/datasets/{A['id']}", json={"name": "new-root-name"})
            new_root = Path(r.json()["folder_path"])
            B = await env.create_dataset("other-name")
            b_img = await upload_image(env, B["id"], "x.png", png_bytes((10, 10, 200)))
            async with env.Session() as db:
                b_path = (await db.get(Image, b_img["id"])).file_path
            b_before = sha(b_path)
            r = await env.client.post(f"{API}/datasets/{A['id']}/versions/{vid}/restore",
                                      json={"handle_extra_images": "keep", "pre_restore_snapshot": True})
            j = await wait_for_job(env, r.json()["job_id"])
            print("job", j["status"], j.get("error_msg"), j.get("result_data"))
            assert sha(b_path) == b_before   # B untouched
            async with env.Session() as db:
                a_row = await db.get(Image, a_img["id"])
                print("A row path", a_row.file_path, "owned by old root?", Path(a_row.file_path).is_relative_to(old_root))
                print("old_root exists", old_root.exists(), "file exists", Path(a_row.file_path).exists(),
                      "new_root file exists", (new_root/"images"/"x.png").exists())
                print("A thumbnail", a_row.thumbnail_path)
            # claim: old root recreated and A's row points outside its folder
            assert Path(a_row.file_path).is_relative_to(old_root)
            assert not Path(a_row.file_path).is_relative_to(new_root)
            assert old_root.exists()
    run(scenario())
