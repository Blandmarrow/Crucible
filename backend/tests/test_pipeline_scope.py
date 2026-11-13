"""Which images `POST /captioning/pipeline` takes, pinned after PM-038.

A pipeline started with *Uncaptioned only* recaptioned every image: the route had no
empty-caption filter, and the per-step `overwrite` it accepted was never read. The
scope is now one request-level `overwrite`, under /run's rule with step 1's
`delimiter_mode` standing in for the request's (docs/dev/captioning.md § Captioning
post-processing).

Torch-free: the provider is stubbed, as in `test_caption_start_scope.py`.
"""
from sqlalchemy import select

from backend.models.image import Image
from backend.tests.conftest import API, api_env, run, wait_for_job
from backend.tests.test_caption_start_scope import STUB_CAPTION, _seed, _set

KEPT = "a caption the user wrote"


def _steps(model: str, first_mode: str = "overwrite") -> list[dict]:
    return [{"model": model, "delimiter_mode": first_mode}, {"model": model}]


async def _post(env, ds_id: str, model: str, **body) -> dict:
    r = await env.client.post(f"{API}/captioning/pipeline", json={
        "dataset_id": ds_id, "steps": body.pop("steps", _steps(model)), **body,
    })
    assert r.status_code == 200, r.text
    return r.json()


async def _captions(env, ds_id: str) -> dict[str, str]:
    async with env.Session() as db:
        rows = (await db.execute(
            select(Image.filename, Image.caption_text).where(Image.dataset_id == ds_id)
        )).all()
    return {r.filename: r.caption_text for r in rows}


async def _finish(env, out: dict) -> None:
    job = await wait_for_job(env, out["job_id"])
    assert job["status"] == "completed", job


def test_uncaptioned_only_leaves_a_captioned_image_alone(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            await _set(env, imgs[0]["id"], caption_text=KEPT)

            out = await _post(env, ds["id"], model, overwrite=False)
            assert out["total"] == 2 * 2          # two uncaptioned images, two steps
            await _finish(env, out)

            assert await _captions(env, ds["id"]) == {
                "i0.png": KEPT, "i1.png": STUB_CAPTION, "i2.png": STUB_CAPTION,
            }

    run(scenario())


def test_an_appending_first_step_takes_the_captioned_images_too(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            await _set(env, imgs[0]["id"], caption_text=KEPT)

            out = await _post(env, ds["id"], model, overwrite=False,
                              steps=_steps(model, first_mode="append"))
            assert out["total"] == 3 * 2

    run(scenario())


def test_overwrite_omitted_takes_every_image(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            await _set(env, imgs[0]["id"], caption_text=KEPT)

            out = await _post(env, ds["id"], model)
            assert out["total"] == 3 * 2
            await _finish(env, out)
            assert (await _captions(env, ds["id"]))["i0.png"] == STUB_CAPTION

    run(scenario())


def test_nothing_uncaptioned_starts_no_job(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            for img in imgs:
                await _set(env, img["id"], caption_text=KEPT)

            out = await _post(env, ds["id"], model, overwrite=False)
            assert out == {"job_id": None, "message": "No images to caption"}

    run(scenario())


def test_explicit_ids_are_narrowed_to_the_uncaptioned_ones(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            await _set(env, imgs[0]["id"], caption_text=KEPT)

            out = await _post(env, ds["id"], model, overwrite=False,
                              image_ids=[imgs[0]["id"], imgs[1]["id"]])
            assert out["total"] == 1 * 2
            await _finish(env, out)

            assert await _captions(env, ds["id"]) == {
                "i0.png": KEPT, "i1.png": STUB_CAPTION, "i2.png": "",
            }

    run(scenario())


def test_overwrite_true_recaptions_captioned_ids(tmp_path, monkeypatch):
    """The Selected scope: the page sends `overwrite: true` with the selection's ids, and
    a selected image that already has a caption is recaptioned, as on /run."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            await _set(env, imgs[0]["id"], caption_text=KEPT)

            out = await _post(env, ds["id"], model, overwrite=True,
                              image_ids=[imgs[0]["id"], imgs[1]["id"]])
            assert out["total"] == 2 * 2
            await _finish(env, out)

            assert await _captions(env, ds["id"]) == {
                "i0.png": STUB_CAPTION, "i1.png": STUB_CAPTION, "i2.png": "",
            }

    run(scenario())


def test_the_old_per_step_overwrite_is_inert(tmp_path, monkeypatch):
    """A tab or script from before the fix sends `steps[0].overwrite` and no request
    field. It is ignored, not a 422, and the request default takes every image, which
    is what such a caller always got."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds, imgs, model = await _seed(env, monkeypatch)
            await _set(env, imgs[0]["id"], caption_text=KEPT)

            out = await _post(env, ds["id"], model,
                              steps=[{"model": model, "overwrite": False}, {"model": model}])
            assert out["total"] == 3 * 2

    run(scenario())
