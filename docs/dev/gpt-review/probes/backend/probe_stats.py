import json, subprocess, pathlib
from backend.models.image import Image
from backend.services.dataset_service import _style_quantiles
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

def test_gpt019_percentile_ties(tmp_path):
    q = _style_quantiles(sorted([0.0, 0.5, 1.0]))
    print("\nQ", q)
    assert len(q) == 21 and [i for i, v in enumerate(q) if v == 0.5] == list(range(6, 15))
    here = pathlib.Path(__file__).parent
    out = subprocess.run(["node", str(here / "pct_run.mjs"), json.dumps(q)], capture_output=True, text=True)
    print(out.stdout, out.stderr)
    res = json.loads(out.stdout)
    print("\nPERCENTILES", res)
    assert res["0.5"] == 70          # claim: returns 70
    assert res["0.25"] == 27.5 and res["0.75"] == 72.5 and res["0"] == 0 and res["1"] == 100   # controls
