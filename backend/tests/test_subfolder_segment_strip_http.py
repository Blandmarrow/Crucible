"""Decision 12 at the request level: a folder path's segments are stripped wherever
one arrives, so `knight ` and `knight` are one folder.

The rule itself is `utils._subfolder_parts` (`test_join_subfolder.py`), and rows stored
before it are rewritten by migration b8d2f4a6c1e3
(`test_migration_strip_subfolder_segments.py`). These pin the writers that used to
store a padded spelling: upload, folder import (with and without its on-disk
structure), the folder delete's query, and a saved run recipe's base folder.
"""
from sqlalchemy import select

from backend.models.run_recipe import SavedRunRecipe
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


async def _by_subfolder(env, ds_id: str) -> dict[str, int]:
    rows = (await env.client.get(f"{API}/images/", params={"dataset_id": ds_id, "limit": 500})).json()
    out: dict[str, int] = {}
    for row in rows:
        out[row["subfolder"]] = out.get(row["subfolder"], 0) + 1
    return out


def test_an_upload_into_a_padded_folder_joins_the_plain_one(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("strip-upload")
            await upload_image(env, ds["id"], name="a.png", subfolder="knight")
            await upload_image(env, ds["id"], name="b.png", subfolder=" knight / a b ")
            await upload_image(env, ds["id"], name="c.png", subfolder="knight ")
            assert await _by_subfolder(env, ds["id"]) == {"knight": 2, "knight/a b": 1}

    run(scenario())


def test_folder_import_strips_the_request_folder_and_the_disk_structure(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("strip-import")
            flat = tmp_path / "flat"
            flat.mkdir()
            (flat / "one.png").write_bytes(png_bytes())
            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/import",
                json={"folder_path": str(flat), "subfolder": " run / knight ", "import_captions": False},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            # A segment with a leading space is a legal folder name on every
            # filesystem (a trailing one is not on Windows), so the disk side is
            # pinned with that.
            nested = tmp_path / "nested"
            (nested / " monk" / "sub").mkdir(parents=True)
            (nested / " monk" / "sub" / "two.png").write_bytes(png_bytes())
            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/import",
                json={"folder_path": str(nested), "preserve_structure": True, "import_captions": False},
            )
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            assert await _by_subfolder(env, ds["id"]) == {"run/knight": 1, "monk/sub": 1}

    run(scenario())


def test_folder_import_refuses_a_climbing_folder_before_it_queues(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("strip-import-climb")
            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/import",
                json={"folder_path": str(tmp_path), "subfolder": "a/ .. /b"},
            )
            assert r.status_code == 400, r.text

    run(scenario())


def test_deleting_a_padded_path_removes_the_folder_the_gallery_lists(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("strip-delete")
            await upload_image(env, ds["id"], name="a.png", subfolder="knight/b")
            await upload_image(env, ds["id"], name="z.png", subfolder="zeal")

            r = await env.client.delete(
                f"{API}/datasets/{ds['id']}/subfolders", params={"path": " knight / "})
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": "knight", "images_moved_to_root": 1}
            assert await _by_subfolder(env, ds["id"]) == {"": 1, "zeal": 1}

            r = await env.client.delete(f"{API}/datasets/{ds['id']}/subfolders", params={"path": " / "})
            assert r.status_code == 400, r.text

    run(scenario())


def test_a_started_run_stores_the_stripped_base_folder(tmp_path, monkeypatch):
    from backend.models import Run
    from backend.tests.test_run_stepping import _plan, _start, wait_for_run

    async def scenario():
        async with api_env(tmp_path) as env:
            _fake, ds, plan_id = await _plan(env, monkeypatch)
            code, body = await _start(env, ds["id"], plan_id, ["generate"],
                                      generate={"subfolder": " gen / night "})
            assert code == 200, body
            await wait_for_run(env, body["id"])
            async with env.Session() as db:
                row = await db.get(Run, body["id"])
            assert row.recipe["generate"]["subfolder"] == "gen/night"

    run(scenario())


def test_a_saved_recipe_stores_the_stripped_base_folder(tmp_path):
    recipe = {
        "stages": ["generate"],
        "generate": {"subfolder": " gen / night ", "set_caption": False},
    }

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{API}/run-recipes/", json={"name": "padded", "recipe": recipe})
            assert r.status_code == 200, r.text
            assert r.json()["recipe"]["generate"]["subfolder"] == "gen/night"
            async with env.Session() as db:
                row = (await db.execute(select(SavedRunRecipe))).scalar_one()
            assert row.recipe["generate"]["subfolder"] == "gen/night"

    run(scenario())
