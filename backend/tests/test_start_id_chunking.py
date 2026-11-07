"""An automated run can pass up to 100k image ids to the caption and score starts,
past SQLite's bind-parameter limit, so both run their `IN` through `utils.chunked`.
The chunk size is shrunk here instead of creating tens of thousands of rows."""
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


def test_score_start_gathers_ids_across_chunks(tmp_path, monkeypatch):
    import backend.routers.quality as quality
    from backend.utils import chunked

    sizes = []

    def small(seq, size=2):
        out = list(chunked(seq, 2))
        sizes.append([len(c) for c in out])
        return out

    monkeypatch.setattr(quality, "chunked", small)

    async def go():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            imgs = [await upload_image(env, ds["id"], f"i{i}.png", png_bytes(color=(i * 30, 5, 5))) for i in range(5)]
            ids = [i["id"] for i in imgs]
            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "image_ids": ids + ids[:2],  # repeats collapse
                "run_technical": False, "run_aesthetic": False,
            })
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 5
            await wait_for_job(env, r.json()["job_id"], timeout=60)
    run(go())
    # Five distinct ids, cut into chunks of 2: three queries, not one `IN` of five.
    assert sizes == [[2, 2, 1]]


def test_caption_start_gathers_ids_across_chunks(tmp_path, monkeypatch):
    import backend.routers.captioning as captioning
    from backend.tests.test_caption_start_scope import _post, _seed
    from backend.utils import chunked

    sizes = []

    def small(seq, size=2):
        out = list(chunked(seq, 2))
        sizes.append([len(c) for c in out])
        return out

    monkeypatch.setattr(captioning, "chunked", small)

    async def go():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch, n=5)
            ids = [i["id"] for i in imgs]
            out = await _post(env, ds["id"], model, image_ids=ids + ids[:1])
            assert out["total"] == 5
            await wait_for_job(env, out["job_id"])
    run(go())
    assert sizes == [[2, 2, 1]]
