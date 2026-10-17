import hashlib
from pathlib import Path
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.models.image import Image
from probe_versioning_021 import _mode, _snap, sha


def test_gpt021_auto_mode_name_reuse(tmp_path):
    """Auto mode (file_hash NULL, pure-rename path): does B get clobbered? (B's file is *moved over* by A's)"""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            await _mode(env, "auto")
            A = await env.create_dataset("reused-name")
            a_img = await upload_image(env, A["id"], "x.png", png_bytes((200, 10, 10)))
            a_hash = hashlib.sha256(png_bytes((200, 10, 10))).hexdigest()
            vid = await _snap(env, A["id"], "snapA")
            r = await env.client.patch(f"{API}/datasets/{A['id']}", json={"name": "new-root-name"})
            B = await env.create_dataset("reused-name")
            b_img = await upload_image(env, B["id"], "x.png", png_bytes((10, 10, 200)))
            async with env.Session() as db:
                b_path = (await db.get(Image, b_img["id"])).file_path
            b_before = sha(b_path)
            r = await env.client.post(f"{API}/datasets/{A['id']}/versions/{vid}/restore",
                                      json={"handle_extra_images": "keep", "pre_restore_snapshot": True})
            j = await wait_for_job(env, r.json()["job_id"])
            print("job", j["status"], j.get("error_msg"))
            print("B before", b_before[:12], "after", sha(b_path)[:12], "A orig", a_hash[:12])
            assert sha(b_path) == a_hash
    run(scenario())
