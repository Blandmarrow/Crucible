from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

async def _set(env, iid, **cols):
    async with env.Session() as db:
        img = await db.get(Image, iid)
        for k, v in cols.items():
            setattr(img, k, v)
        await db.commit()

def test_gpt035_cooccurrence(tmp_path):
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            for name, cap in (("dup", "cat, cat, blue"), ("ctl", "cat, blue")):
                ds = (await env.create_dataset(name))["id"]
                img = await upload_image(env, ds, "a.png", png_bytes())
                await _set(env, img["id"], caption_text=cap)
                r = await env.client.get(f"{API}/datasets/{ds}/tag-cooccurrence"); assert r.status_code == 200
                print("\nCOOC", cap, r.json())
                if name == "dup":
                    assert r.json() == {"tags": ["cat", "blue"], "matrix": [[4, 2], [2, 1]]}
                else:
                    assert r.json()["matrix"] == [[1, 1], [1, 1]]
            # does the top-tags endpoint also count repeats per caption?
            ds = (await env.create_dataset("t"))["id"]
            img = await upload_image(env, ds, "a.png", png_bytes())
            await _set(env, img["id"], caption_text="cat, cat, blue")
            r = await env.client.get(f"{API}/captions/dataset/{ds}/tag-stats")
            print("\nTAGSTATS", r.status_code, r.text[:300])
    run(sc())
