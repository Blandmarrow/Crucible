"""A job's image scope, resolved once in `services/image_scope.py` (M8 stage 2).

Upscale, LUT, inpaint and detection crop used to carry the same block four
times. These pin the rules that block had, so the shared helper keeps them:
explicit ids win and bypass subfolder and flags, ids are not held to the
dataset (a selection can span datasets, PM-029), unknown flags are dropped,
flags combine with AND, and the subfolder match is exact.
"""
from sqlalchemy import update

from backend.models import Image
from backend.services.image_scope import resolve_scope_ids
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job
from backend.tests.test_upscale_png_fallback_http import MODEL
from backend.tests.test_upscale_png_fallback_http import _install_fake as _install_fake_upscale


async def _set(env, image_id: str, **values) -> None:
    async with env.Session() as db:
        await db.execute(update(Image).where(Image.id == image_id).values(**values))
        await db.commit()


async def _resolve(env, **kwargs) -> list[str]:
    scope = {"dataset_id": None, "image_ids": None, "subfolder": None, "quality_flags": None}
    async with env.Session() as db:
        return await resolve_scope_ids(db, **(scope | kwargs))


def test_ids_are_deduplicated_and_the_missing_ones_dropped(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())

            ids = await _resolve(env, image_ids=[a["id"], b["id"], a["id"], "no-such-image"])
            assert sorted(ids) == sorted([a["id"], b["id"]])

    run(scenario())


def test_an_empty_id_list_means_nothing_not_the_dataset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png", png_bytes())

            assert await _resolve(env, dataset_id=ds["id"], image_ids=[]) == []

    run(scenario())


def test_ids_bypass_subfolder_flags_and_the_dataset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            other = await env.create_dataset("other")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, other["id"], "b.png", png_bytes())
            await _set(env, a["id"], quality_flags={"is_blurry": True})

            ids = await _resolve(
                env, dataset_id=ds["id"], image_ids=[a["id"], b["id"]],
                subfolder="elsewhere", quality_flags=["is_blurry"],
            )
            assert sorted(ids) == sorted([a["id"], b["id"]])

    run(scenario())


def test_the_dataset_scope_excludes_every_named_flag(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            clean = await upload_image(env, ds["id"], "clean.png", png_bytes())
            blurry = await upload_image(env, ds["id"], "blurry.png", png_bytes())
            noisy = await upload_image(env, ds["id"], "noisy.png", png_bytes())
            cleared = await upload_image(env, ds["id"], "cleared.png", png_bytes())
            await _set(env, blurry["id"], quality_flags={"is_blurry": True})
            await _set(env, noisy["id"], quality_flags={"is_noisy": True})
            await _set(env, cleared["id"], quality_flags={"is_blurry": False})

            ids = await _resolve(
                env, dataset_id=ds["id"], quality_flags=["is_blurry", "is_noisy"],
            )
            assert sorted(ids) == sorted([clean["id"], cleared["id"]])

    run(scenario())


def test_unknown_flags_are_dropped_not_refused(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            # Set, so an applied filter would exclude it: a missing key passes
            # `IS NOT TRUE` either way and could not tell dropped from applied.
            await _set(env, a["id"], quality_flags={"no_such_flag": True})

            assert await _resolve(env, dataset_id=ds["id"], quality_flags=["no_such_flag"]) == [a["id"]]

    run(scenario())


def test_the_subfolder_match_is_exact(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            top = await upload_image(env, ds["id"], "top.png", png_bytes())
            inside = await upload_image(env, ds["id"], "inside.png", png_bytes())
            nested = await upload_image(env, ds["id"], "nested.png", png_bytes())
            await _set(env, inside["id"], subfolder="cats")
            await _set(env, nested["id"], subfolder="cats/kittens")

            assert await _resolve(env, dataset_id=ds["id"], subfolder="/cats/") == [inside["id"]]
            assert top["id"] in await _resolve(env, dataset_id=ds["id"])

    run(scenario())


def test_an_upscale_leaves_out_the_flagged_images(tmp_path, monkeypatch):
    """Through the route: nothing else posts `quality_flags` to a job."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _install_fake_upscale(monkeypatch)
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "keep.png", png_bytes())
            flagged = await upload_image(env, ds["id"], "flagged.png", png_bytes())
            await _set(env, flagged["id"], quality_flags={"has_watermark": True})

            r = await env.client.post(f"{API}/upscaling/run", json={
                "dataset_id": ds["id"], "model_path": MODEL, "replace": False,
                "quality_flags": ["has_watermark"],
            })
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 1
            job = await wait_for_job(env, r.json()["job_id"], timeout=60)
            assert job["result_data"]["processed"] == 1, job

    run(scenario())
