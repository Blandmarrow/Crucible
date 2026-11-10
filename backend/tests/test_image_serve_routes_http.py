"""The image file and thumbnail routes: what they serve, and what they refuse.

Both read only the path they serve rather than the whole row (M9 stage 11): a
gallery page asks for one thumbnail per tile, and `db.get(Image, …)` loaded
every column, 55 KB of `generation_metadata` included on ComfyUI output. These
tests pin the behaviour that narrowing must keep: a stored thumbnail is served,
a missing one is generated and recorded, an unknown id is a 404, and a stored
path outside the datasets folder is still a 403 (the path traversal guard).
"""
from pathlib import Path

from sqlalchemy import select, update

from backend.models import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _row(env, image_id: str) -> Image:
    async with env.Session() as s:
        return (await s.execute(select(Image).where(Image.id == image_id))).scalar_one()


async def _set(env, image_id: str, **values) -> None:
    async with env.Session() as s:
        await s.execute(update(Image).where(Image.id == image_id).values(**values))
        await s.commit()


def test_both_routes_serve_the_stored_files(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            row = await _row(env, img["id"])

            f = await env.client.get(f"{API}/images/{img['id']}/file")
            assert f.status_code == 200
            assert f.content == Path(row.file_path).read_bytes()

            t = await env.client.get(f"{API}/images/{img['id']}/thumbnail")
            assert t.status_code == 200
            assert row.thumbnail_path and t.content == Path(row.thumbnail_path).read_bytes()

    run(scenario())


def test_a_missing_thumbnail_is_generated_and_recorded(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            stored = (await _row(env, img["id"])).thumbnail_path
            Path(stored).unlink()
            await _set(env, img["id"], thumbnail_path=None)

            t = await env.client.get(f"{API}/images/{img['id']}/thumbnail")
            assert t.status_code == 200
            regenerated = (await _row(env, img["id"])).thumbnail_path
            assert regenerated and Path(regenerated).is_file()
            assert t.content == Path(regenerated).read_bytes()

    run(scenario())


def test_an_unknown_id_is_a_404_on_both_routes(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            for route in ("file", "thumbnail"):
                r = await env.client.get(f"{API}/images/no-such-image/{route}")
                assert r.status_code == 404, route
                assert r.json()["detail"] == "Image not found"

    run(scenario())


def test_a_stored_path_outside_the_datasets_folder_is_refused(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            outside = tmp_path / "outside.png"
            outside.write_bytes(png_bytes())

            await _set(env, img["id"], file_path=str(outside))
            assert (await env.client.get(f"{API}/images/{img['id']}/file")).status_code == 403

            await _set(env, img["id"], thumbnail_path=str(outside))
            assert (await env.client.get(f"{API}/images/{img['id']}/thumbnail")).status_code == 403

    run(scenario())
