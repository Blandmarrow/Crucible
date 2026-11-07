"""What `POST /captioning/run` puts in scope, pinned before its start is lifted into
`start_caption_job` (M6 §4). An automated run starts caption jobs from code, so the
query, the "nothing to do" answer, the label and the stored config must come out of
the extraction unchanged.

One behaviour is pinned on purpose although it is a trap: `image_ids: []` widens to
the whole dataset (`if body.image_ids:`), unlike `image_scope.resolve_scope_ids`,
where `[]` means nothing. The run guards against it by skipping a stage with no ids;
the HTTP route keeps it (docs/dev/cleanup-plan/README.md § Found during stages 2 and 3).

Torch-free: the provider call is stubbed, as in `test_caption_empty_guard.py`.
"""
from sqlalchemy import select

from backend.models.image import Image
from backend.models.job import BackgroundJob
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job

STUB_CAPTION = "a stub caption"


async def _provider(env) -> dict:
    r = await env.client.post(
        f"{API}/providers/",
        json={"name": "lmstudio", "base_url": "http://127.0.0.1:1234/v1", "default_model": "qwen-vl"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _stub(monkeypatch):
    import backend.ml.openai_compat_captioner as mod

    async def _fake(image_path, **kwargs):
        return STUB_CAPTION

    monkeypatch.setattr(mod, "caption_image", _fake)


async def _seed(env, monkeypatch, n: int = 3, subfolders=None):
    ds = await env.create_dataset("d")
    imgs = []
    for i in range(n):
        sub = subfolders[i] if subfolders else ""
        imgs.append(await upload_image(env, ds["id"], f"i{i}.png", png_bytes(color=(i * 40, 10, 10)), subfolder=sub))
    prov = await _provider(env)
    _stub(monkeypatch)
    return ds, imgs, f"openai_compat:{prov['id']}:qwen-vl"


async def _post(env, ds_id: str, model: str, **body):
    r = await env.client.post(f"{API}/captioning/run", json={"dataset_id": ds_id, "model": model, **body})
    assert r.status_code == 200, r.text
    return r.json()


async def _captioned(env, ds_id: str) -> set[str]:
    async with env.Session() as db:
        rows = (await db.execute(
            select(Image.filename).where(Image.dataset_id == ds_id, Image.caption_text == STUB_CAPTION)
        )).scalars().all()
    return set(rows)


async def _set(env, image_id: str, **cols):
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        for k, v in cols.items():
            setattr(img, k, v)
        await db.commit()


def test_explicit_ids_narrow_the_scope(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            out = await _post(env, ds["id"], model, image_ids=[imgs[1]["id"]])
            assert out["total"] == 1
            job = await wait_for_job(env, out["job_id"])
            assert job["status"] == "completed", job
            assert await _captioned(env, ds["id"]) == {"i1.png"}

    run(scenario())


def test_an_empty_id_list_widens_to_the_whole_dataset(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _imgs, model = await _seed(env, monkeypatch)
            out = await _post(env, ds["id"], model, image_ids=[])
            assert out["total"] == 3
            await wait_for_job(env, out["job_id"])
            assert await _captioned(env, ds["id"]) == {"i0.png", "i1.png", "i2.png"}

    run(scenario())


def test_subfolder_narrows_when_no_ids_are_given(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _imgs, model = await _seed(env, monkeypatch, subfolders=["x", "", "x"])
            out = await _post(env, ds["id"], model, subfolder="x")
            assert out["total"] == 2
            await wait_for_job(env, out["job_id"])
            assert await _captioned(env, ds["id"]) == {"i0.png", "i2.png"}

    run(scenario())


def test_captioned_images_are_skipped_unless_overwrite_or_a_delimiter_mode(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            await _set(env, imgs[0]["id"], caption_text="already")

            assert (await _post(env, ds["id"], model, image_ids=[imgs[0]["id"]]))["job_id"] is None

            out = await _post(env, ds["id"], model, overwrite=False)
            assert out["total"] == 2

            out2 = await _post(env, ds["id"], model, image_ids=[imgs[0]["id"]], overwrite=True)
            assert out2["total"] == 1

            out3 = await _post(env, ds["id"], model, image_ids=[imgs[0]["id"]], delimiter_mode="append")
            assert out3["total"] == 1
            for job_id in (out["job_id"], out2["job_id"], out3["job_id"]):
                await wait_for_job(env, job_id)

    run(scenario())


def test_flag_and_aesthetic_filters_narrow_the_scope(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            await _set(env, imgs[0]["id"], quality_flags={"is_blurry": True})
            await _set(env, imgs[1]["id"], aesthetic_score=2.0)
            await _set(env, imgs[2]["id"], aesthetic_score=7.0)

            out = await _post(env, ds["id"], model, exclude_flags=["is_blurry"], min_aesthetic_score=5.0)
            assert out["total"] == 1
            await wait_for_job(env, out["job_id"])
            assert await _captioned(env, ds["id"]) == {"i2.png"}

    run(scenario())


def test_nothing_in_scope_answers_a_null_job_and_writes_no_row(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, _imgs, model = await _seed(env, monkeypatch)
            out = await _post(env, ds["id"], model, subfolder="nowhere")
            assert out == {"job_id": None, "message": "No images to caption"}
            async with env.Session() as db:
                assert (await db.execute(select(BackgroundJob))).scalars().all() == []

    run(scenario())


def test_label_and_config_are_stored_on_the_job(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            auto = await _post(env, ds["id"], model, image_ids=[imgs[0]["id"], imgs[1]["id"]])
            named = await _post(env, ds["id"], model, image_ids=[imgs[2]["id"]], label="mine")
            a = await wait_for_job(env, auto["job_id"])
            b = await wait_for_job(env, named["job_id"])
            assert a["label"] == "OpenAI-compat (qwen-vl) — 2 images"
            assert b["label"] == "mine"
            assert a["job_type"] == "caption"
            async with env.Session() as db:
                row = await db.get(BackgroundJob, auto["job_id"])
            assert row.config["model"] == model
            assert row.config["image_ids"] == [imgs[0]["id"], imgs[1]["id"]]
            assert "run_id" not in row.config

    run(scenario())
