"""The gallery list says whether an image has generation metadata, never what it is.

`generation_metadata` averages about 55 KB a row on ComfyUI output, so a page of
100 shipped 5.6 MB that the card never read: it only asks whether there is any
(M9 stage 11). The list now carries `has_generation_metadata`, computed in SQL,
and the gallery's modal fetches the JSON from `GET /images/{id}`.

The flag has to see through three ways of storing "nothing". An `Image` built
with `generation_metadata=None` stores the JSON text `'null'`, not SQL NULL, so
a plain `IS NOT NULL` would put a button on every uploaded photo.
"""
from sqlalchemy import String, null, select, type_coerce, update

from backend.models import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

META = {"source": "comfyui", "prompt": "a red hat", "seed": 7}


async def _set_meta(env, image_id: str, value) -> None:
    async with env.Session() as s:
        await s.execute(update(Image).where(Image.id == image_id).values(generation_metadata=value))
        await s.commit()


def test_the_list_flags_metadata_and_omits_the_json(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = {}
            for name in ("dict", "json_null", "empty", "sql_null"):
                ids[name] = (await upload_image(env, ds["id"], f"{name}.png", png_bytes()))["id"]

            await _set_meta(env, ids["dict"], META)
            await _set_meta(env, ids["json_null"], None)  # the JSON text 'null'
            await _set_meta(env, ids["empty"], {})
            await _set_meta(env, ids["sql_null"], null())

            r = await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})
            assert r.status_code == 200, r.text
            rows = {row["id"]: row for row in r.json()}

            assert {name: rows[i]["has_generation_metadata"] for name, i in ids.items()} == {
                "dict": True, "json_null": False, "empty": False, "sql_null": False,
            }
            assert all("generation_metadata" not in row for row in rows.values())

            # The modal's source still carries the whole thing.
            detail = await env.client.get(f"{API}/images/{ids['dict']}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["generation_metadata"] == META

    run(scenario())


def test_the_flag_storage_forms_are_what_this_file_assumes(tmp_path):
    """Pins the premise above: `None` through the JSON type is the text 'null',
    and only `null()` is SQL NULL. If SQLAlchemy ever changed that, the first
    test would still pass and stop covering the 'null' case."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = (await upload_image(env, ds["id"], "a.png", png_bytes()))["id"]
            b = (await upload_image(env, ds["id"], "b.png", png_bytes()))["id"]
            await _set_meta(env, a, None)
            await _set_meta(env, b, null())
            async with env.Session() as s:
                raw = dict((await s.execute(
                    select(Image.id, type_coerce(Image.generation_metadata, String))
                )).all())
            assert raw[a] == "null"
            assert raw[b] is None

    run(scenario())
