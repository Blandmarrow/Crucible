"""The run recipe library (M6 §6): `routers/run_recipes.py`.

What is pinned (docs/dev/comfyui/recipes.md):
- names are unique folded for case and spaces; a clash is a 409 unless `replace`,
  which keeps the recipe's id; rename refuses a clash too;
- a saved export folder must be absolute, and a draft may leave it empty;
- the export document carries no export folder and names providers rather than
  giving this machine's ids; an import resolves them by name or blanks them, with a note;
- nothing an import carries beyond the recipe's own fields is stored: an `api_key` or
  a `plan_id`, at the top or nested, never reaches the database;
- an imported name that is taken is numbered, not refused.
"""
import json

from sqlalchemy import select

from backend.models.run_recipe import SavedRunRecipe
from backend.tests.conftest import API, api_env, run

RECIPE = {
    "stages": ["prompts", "generate", "caption", "export"],
    "generate": {"subfolder": "gen", "set_caption": True},
    "score": {"run_aesthetic": False, "aesthetic_model": "laion"},
    "caption": {"model": "florence2_large", "style": "detailed"},
    "export": {"kind": "plain", "output_dir": ""},
}


async def _provider(env, name: str) -> str:
    r = await env.client.post(f"{API}/providers/", json={"name": name, "base_url": "http://127.0.0.1:1234/v1"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _save(env, name: str, recipe: dict, **extra):
    return await env.client.post(f"{API}/run-recipes/", json={"name": name, "recipe": recipe, **extra})


async def _stored(env) -> list[SavedRunRecipe]:
    async with env.Session() as db:
        return list((await db.execute(select(SavedRunRecipe))).scalars())


def test_names_are_unique_folded_and_replace_keeps_the_id(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            r = await _save(env, "  Night   set ", RECIPE)
            assert r.status_code == 200, r.text
            first = r.json()
            assert first["name"] == "Night set"

            r = await _save(env, "night set", RECIPE)
            assert r.status_code == 409, r.text

            changed = {**RECIPE, "generate": {"subfolder": "other", "set_caption": True}}
            r = await _save(env, "NIGHT SET", changed, replace=True)
            assert r.status_code == 200, r.text
            assert r.json()["id"] == first["id"]
            assert r.json()["recipe"]["generate"]["subfolder"] == "other"
            assert len(await _stored(env)) == 1

            r = await _save(env, "Day set", RECIPE)
            assert r.status_code == 200
            r = await env.client.patch(f"{API}/run-recipes/{r.json()['id']}", json={"name": "night SET"})
            assert r.status_code == 409, r.text

            listed = (await env.client.get(f"{API}/run-recipes/")).json()
            assert [x["name"] for x in listed] == ["Day set", "NIGHT SET"]
    run(go())


def test_an_export_folder_must_be_absolute_but_may_be_empty(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            relative = {**RECIPE, "export": {"kind": "plain", "output_dir": "relative/out"}}
            r = await _save(env, "rel", relative)
            assert r.status_code == 400, r.text
            r = await _save(env, "empty", RECIPE)
            assert r.status_code == 200, r.text
            absolute = {**RECIPE, "export": {"kind": "plain", "output_dir": str(tmp_path / "out")}}
            r = await _save(env, "abs", absolute)
            assert r.status_code == 200, r.text
            r = await _save(env, "climb", {**RECIPE, "generate": {"subfolder": "../x", "set_caption": True}})
            assert r.status_code == 400, r.text
    run(go())


def test_the_document_strips_this_machines_values_and_an_import_resolves_them(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            lm = await _provider(env, "LM Studio")
            out_dir = str(tmp_path / "out")
            recipe = {
                **RECIPE,
                "stages": ["prompts", "rewrite", "generate", "caption", "export"],
                "caption": {"model": f"openai_compat:{lm}:qwen-vl", "style": "detailed"},
                "export": {"kind": "plain", "output_dir": out_dir},
                "rewrite": {"provider_id": lm, "model_name": "qwen", "scenarios": ["solo"]},
            }
            r = await _save(env, "Shared", recipe)
            assert r.status_code == 200, r.text
            doc = (await env.client.get(f"{API}/run-recipes/{r.json()['id']}/export")).json()
            assert doc["format"] == "crucible.run-recipe" and doc["version"] == 1
            text = json.dumps(doc)
            assert out_dir not in text and lm not in text
            assert doc["providers"] == {"caption": "LM Studio", "rewrite": "LM Studio"}
            assert doc["recipe"]["caption"]["model"] == "openai_compat::qwen-vl"

            # Imported here, the provider resolves by name; the folder stays empty.
            r = await env.client.post(f"{API}/run-recipes/import", json=doc)
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["recipe"]["name"] == "Shared (2)"
            assert out["recipe"]["recipe"]["caption"]["model"] == f"openai_compat:{lm}:qwen-vl"
            assert out["recipe"]["recipe"]["rewrite"]["provider_id"] == lm
            assert out["recipe"]["recipe"]["export"]["output_dir"] == ""
            assert any("export folder" in n for n in out["notes"])

            # On a machine with no such provider, both are blanked, with a note each.
            doc["providers"] = {"caption": "Elsewhere", "rewrite": "Elsewhere"}
            out = (await env.client.post(f"{API}/run-recipes/import", json=doc)).json()
            assert out["recipe"]["recipe"]["caption"]["model"] == ""
            assert out["recipe"]["recipe"]["rewrite"]["provider_id"] == ""
            assert sum("Elsewhere" in n for n in out["notes"]) == 2
    run(go())


def test_an_import_never_stores_an_api_key_a_plan_id_or_a_foreign_folder(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            doc = {
                "format": "crucible.run-recipe", "version": 1, "name": "Sneaky",
                "api_key": "sk-top", "plan_id": "plan-top",
                "providers": {"caption": None, "rewrite": None, "api_key": "sk-providers"},
                "recipe": {
                    **RECIPE,
                    "plan_id": "plan-nested", "api_key": "sk-recipe",
                    "caption": {"model": "florence2_large", "api_key": "sk-caption"},
                    "export": {"kind": "plain", "output_dir": "D:/somebody/else", "api_key": "sk-export"},
                },
            }
            r = await env.client.post(f"{API}/run-recipes/import", json=doc)
            assert r.status_code == 200, r.text
            [row] = await _stored(env)
            stored = json.dumps(row.recipe)
            for secret in ("sk-", "plan-top", "plan-nested", "somebody"):
                assert secret not in stored, secret
            assert row.recipe["export"]["output_dir"] == ""
    run(go())


def test_an_import_refuses_what_is_not_a_recipe(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            for bad in (
                {"format": "something-else", "version": 1, "recipe": RECIPE},
                {"format": "crucible.run-recipe", "version": 99, "recipe": RECIPE},
                {"format": "crucible.run-recipe", "version": 1},
                {"format": "crucible.run-recipe", "version": 1, "recipe": {"stages": ["nonsense"]}},
            ):
                r = await env.client.post(f"{API}/run-recipes/import", json=bad)
                assert r.status_code == 400, (bad, r.text)
            assert await _stored(env) == []
    run(go())


def test_delete_and_a_missing_recipe(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            rid = (await _save(env, "gone", RECIPE)).json()["id"]
            assert (await env.client.delete(f"{API}/run-recipes/{rid}")).status_code == 204
            assert (await env.client.delete(f"{API}/run-recipes/{rid}")).status_code == 404
            assert (await env.client.get(f"{API}/run-recipes/{rid}/export")).status_code == 404
            assert (await env.client.patch(f"{API}/run-recipes/{rid}", json={"name": "x"})).status_code == 404
    run(go())
