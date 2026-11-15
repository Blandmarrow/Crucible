"""`POST /images/by-ids`: the Compare page's one-request metadata fetch.

Pins what the route promises: request order, duplicates collapsed, unknown ids
dropped, each row's own dataset's license and name, label ids, the aesthetic
model, and the 1..100 bound on the id list.
"""
from sqlalchemy import update

from backend.models import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _by_ids(env, ids):
    return await env.client.post(f"{API}/images/by-ids", json={"image_ids": ids})


def test_request_order_kept_and_duplicates_collapsed(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())
            c = await upload_image(env, ds["id"], "c.png", png_bytes())
            r = await _by_ids(env, [c["id"], a["id"], c["id"], b["id"], a["id"]])
            assert r.status_code == 200, r.text
            assert [i["id"] for i in r.json()] == [c["id"], a["id"], b["id"]]

    run(scenario())


def test_unknown_ids_drop_out(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            r = await _by_ids(env, ["nope", a["id"], "also-nope"])
            assert r.status_code == 200
            assert [i["id"] for i in r.json()] == [a["id"]]
            r = await _by_ids(env, ["nope"])
            assert r.status_code == 200 and r.json() == []

    run(scenario())


def test_each_row_carries_its_own_datasets_license_and_name(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            d1 = await env.create_dataset("first", license="CC0-1.0")
            d2 = await env.create_dataset("second", license="CC-BY-4.0")
            a = await upload_image(env, d1["id"], "a.png", png_bytes())
            b = await upload_image(env, d2["id"], "b.png", png_bytes())
            c = await upload_image(env, d2["id"], "c.png", png_bytes())
            async with env.Session() as s:
                await s.execute(update(Image).where(Image.id == c["id"]).values(license="MIT"))
                await s.commit()
            rows = {i["id"]: i for i in (await _by_ids(env, [a["id"], b["id"], c["id"]])).json()}
            assert rows[a["id"]]["license"] == d1["license"] != ""
            assert rows[b["id"]]["license"] == d2["license"] != ""
            assert d1["license"] != d2["license"]
            assert rows[c["id"]]["license"] == "MIT"
            assert rows[a["id"]]["dataset_name"] == "first"
            assert rows[b["id"]]["dataset_name"] == "second"
            assert rows[c["id"]]["dataset_name"] == "second"

    run(scenario())


def test_label_ids_and_aesthetic_model_are_returned(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes())
            lab = await env.client.post(f"{API}/labels/", json={"name": "keep"})
            assert lab.status_code == 201, lab.text
            asg = await env.client.post(
                f"{API}/labels/assign", json={"image_ids": [a["id"]], "add": [lab.json()["id"]]})
            assert asg.status_code == 200, asg.text
            async with env.Session() as s:
                await s.execute(update(Image).where(Image.id == a["id"]).values(
                    aesthetic_score=6.5, aesthetic_model="some-model",
                    generation_metadata={"prompt": "a lake at dusk"}))
                await s.commit()
            rows = {i["id"]: i for i in (await _by_ids(env, [a["id"], b["id"]])).json()}
            assert rows[a["id"]]["label_ids"] == [lab.json()["id"]]
            assert rows[b["id"]]["label_ids"] == []
            assert rows[a["id"]]["aesthetic_model"] == "some-model"
            assert rows[b["id"]]["aesthetic_model"] is None
            # The shared helper's metadata flag, without the JSON itself.
            assert rows[a["id"]]["has_generation_metadata"] is True
            assert rows[b["id"]]["has_generation_metadata"] is False
            assert "generation_metadata" not in rows[a["id"]]

    run(scenario())


def test_the_id_list_is_bounded(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await _by_ids(env, [])).status_code == 422
            assert (await _by_ids(env, [f"i{n}" for n in range(101)])).status_code == 422
            assert (await _by_ids(env, [f"i{n}" for n in range(100)])).status_code == 200

    run(scenario())
