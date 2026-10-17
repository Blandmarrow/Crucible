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

async def _stats(env, ds):
    r = await env.client.get(f"{API}/datasets/{ds}/stats"); assert r.status_code == 200
    return r.json()

async def _count(env, ds, **p):
    p = {k: v for k, v in p.items() if v is not None}
    r = await env.client.get(f"{API}/images/", params={"dataset_id": ds, "limit": 500, **p})
    assert r.status_code == 200, r.text
    return len(r.json())

# Mirror of StatsPage.scoreEntries (lines 120-137): min=edges[i-1] if i>0, max=edges[i] if i<len(edges)
def score_entries_params(labels, edges, field, i):
    return dict(score_field=field,
                min_score=edges[i-1] if i > 0 else None,
                max_score=edges[i] if i < len(edges) else None)

SAT_EDGES = [0.1, 0.2, 0.4, 0.6]
SAT_LABELS = ["<0.1", "0.1–0.2", "0.2–0.4", "0.4–0.6", "0.6+"]

def test_gpt018_saturation_boundary_click_through(tmp_path):
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            for i, s in enumerate([None, 0.2, 0.4, 0.6, 1.0]):
                img = await upload_image(env, ds, f"a{i}.png", png_bytes((i*40, 10, 10)))
                await _set(env, img["id"], saturation_score=s)
            dist = (await _stats(env, ds))["saturation_distribution"]
            print("\nSTATS", dist)
            clicked = {}
            for i, lbl in enumerate(SAT_LABELS):
                if lbl in dist:
                    clicked[lbl] = await _count(env, ds, **score_entries_params(SAT_LABELS, SAT_EDGES, "saturation_score", i))
            print("CLICK", clicked)
            # claim: stats says 1 for 0.2-0.4, click-through returns 2
            assert dist["0.2–0.4"] == 1 and clicked["0.2–0.4"] == 2
            # Which buckets disagree
            mism = {l: (dist[l], clicked[l]) for l in clicked if dist[l] != clicked[l]}
            print("MISMATCH", mism)
    run(sc())

def test_gpt018_nonboundary_control(tmp_path):
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            for i, s in enumerate([0.15, 0.3, 0.5, 0.8]):
                img = await upload_image(env, ds, f"a{i}.png", png_bytes((i*40, 10, 10)))
                await _set(env, img["id"], saturation_score=s)
            dist = (await _stats(env, ds))["saturation_distribution"]
            for i, lbl in enumerate(SAT_LABELS):
                if lbl in dist:
                    assert await _count(env, ds, **score_entries_params(SAT_LABELS, SAT_EDGES, "saturation_score", i)) == dist[lbl], lbl
    run(sc())

def test_gpt018_aesthetic_edges(tmp_path):
    # AESTHETIC_FILTER_MAP: low {0,4}, mid {4,6}, high {6,-}
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            for i, s in enumerate([3.9, 4.0, 6.0, 7.0]):
                img = await upload_image(env, ds, f"a{i}.png", png_bytes((i*40, 10, 10)))
                await _set(env, img["id"], aesthetic_score=s)
            dist = (await _stats(env, ds))["score_distribution"]
            low = await _count(env, ds, min_score=0, max_score=4)
            mid = await _count(env, ds, min_score=4, max_score=6)
            high = await _count(env, ds, min_score=6)
            print("\nAES stats", dist, "click", low, mid, high)
            assert (dist["low (0-4)"], dist["mid (4-6)"], dist["high (6-10)"]) == (1, 1, 2)
            assert (low, mid, high) == (2, 2, 2)
    run(sc())

def test_gpt018_other_filters_use_strict_upper(tmp_path):
    # control: caption words / megapixels / aspect ratio use `<` for max
    async def sc():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = (await env.create_dataset("d"))["id"]
            img = await upload_image(env, ds, "a.png", png_bytes())
            await _set(env, img["id"], caption_text="a b c d e")          # 5 words
            img2 = await upload_image(env, ds, "b.png", png_bytes((9,9,9)))
            await _set(env, img2["id"], caption_text="a b c d e f")       # 6 words
            st = (await _stats(env, ds))["caption_length_distribution"]
            c15 = await _count(env, ds, caption_words_min=1, caption_words_max=6)
            c610 = await _count(env, ds, caption_words_min=6, caption_words_max=11)
            print("\nWC", st, c15, c610)
            assert c15 == 1 and c610 == 1
    run(sc())

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
