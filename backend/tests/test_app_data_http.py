"""Request-level tests for app-stored prompts and the one-time browser import.

The import's whole job is not to lose anything a browser held, and its rules only
show up across repeated calls: a re-run must change nothing, the first copy of a
single-value section must win, and one bad entry must not cost the rest of the batch.
"""
import asyncio

from sqlalchemy import text

from backend.routers.app_data import MAX_WORKFLOW_BYTES
from backend.tests.conftest import API, api_env, run

BASE = f"{API}/app-data"
PRESET = {"name": "p", "model": "m", "style": "s", "prompt": "text"}


async def _create_plan(env, name: str = "plan") -> str:
    ds = await env.create_dataset(f"{name}-ds")
    r = await env.client.post(f"{API}/comfy/plans", json={"dataset_id": ds["id"], "name": name, "workflow_json": {}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _is_sql_null(env, table: str, column: str) -> bool:
    async with env.Session() as session:
        result = await session.execute(text(f"SELECT {column} IS NULL FROM {table}"))
        return bool(result.scalar_one())


# --- presets --------------------------------------------------------------


def test_presets_crud(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{BASE}/presets", json=PRESET)
            assert r.status_code == 200, r.text
            preset_id = r.json()["id"]

            # A preset saved before a model was chosen has none, and that must stay valid.
            r = await env.client.post(f"{BASE}/presets", json={**PRESET, "name": "no-model", "model": ""})
            assert r.status_code == 200, r.text

            names = [p["name"] for p in (await env.client.get(f"{BASE}/presets")).json()]
            assert names == ["p", "no-model"]

            assert (await env.client.delete(f"{BASE}/presets/{preset_id}")).status_code == 200
            assert (await env.client.delete(f"{BASE}/presets/{preset_id}")).status_code == 404

            r = await env.client.post(f"{BASE}/presets", json={**PRESET, "name": "   "})
            assert r.status_code == 422

    run(scenario())


# --- categories -----------------------------------------------------------


def test_categories_crud_handles_a_name_with_a_slash(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            for _ in range(2):
                r = await env.client.post(f"{BASE}/categories", json={"name": "a/b"})
                assert r.status_code == 200, r.text
            assert (await env.client.get(f"{BASE}/categories")).json() == ["a/b"]

            r = await env.client.post(f"{BASE}/categories/rename", json={"old_name": "a/b", "new_name": "c/d"})
            assert r.json() == {"renamed": True}
            assert (await env.client.get(f"{BASE}/categories")).json() == ["c/d"]

            r = await env.client.delete(f"{BASE}/categories", params={"name": "c/d"})
            assert r.json() == {"deleted": 1}
            assert (await env.client.get(f"{BASE}/categories")).json() == []

    run(scenario())


def test_renaming_an_undeclared_category_does_not_declare_it(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{BASE}/categories/rename", json={"old_name": "x", "new_name": "y"})
            assert r.json() == {"renamed": False}
            assert (await env.client.get(f"{BASE}/categories")).json() == []

    run(scenario())


def test_a_category_name_only_a_dataset_could_hold_never_fails_or_touches_another(tmp_path):
    """`Dataset.category` is neither stripped nor bounded, and by the time these routes run
    the datasets have already changed, so a 422 here would report a false failure."""
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.post(f"{BASE}/categories", json={"name": "foo"})

            r = await env.client.delete(f"{BASE}/categories", params={"name": "foo "})
            assert r.json() == {"deleted": 0}
            r = await env.client.delete(f"{BASE}/categories", params={"name": "   "})
            assert r.status_code == 200, r.text

            r = await env.client.post(f"{BASE}/categories/rename", json={"old_name": "foo ", "new_name": "bar"})
            assert r.json() == {"renamed": False}
            assert (await env.client.get(f"{BASE}/categories")).json() == ["foo"]

            r = await env.client.post(f"{BASE}/categories/rename", json={"old_name": "foo", "new_name": "x" * 300})
            assert r.status_code == 200, r.text
            assert (await env.client.get(f"{BASE}/categories")).json() == []

    run(scenario())


def test_renaming_onto_an_existing_category_merges_them(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.post(f"{BASE}/categories", json={"name": "x"})
            await env.client.post(f"{BASE}/categories", json={"name": "y"})
            r = await env.client.post(f"{BASE}/categories/rename", json={"old_name": "x", "new_name": "y"})
            assert r.status_code == 200, r.text
            assert (await env.client.get(f"{BASE}/categories")).json() == ["y"]

    run(scenario())


# --- captioning workflow --------------------------------------------------


def test_workflow_round_trip_and_delete_restores_sql_null(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            url = f"{BASE}/captioning-workflow"
            assert (await env.client.get(url)).json() == {"value": None}

            # With no settings row yet: DELETE must not fail, and PUT must create the row.
            assert (await env.client.delete(url)).status_code == 200
            r = await env.client.put(url, json={"value": {"customPrompt": "hello"}})
            assert r.status_code == 200, r.text
            assert (await env.client.get(url)).json() == {"value": {"customPrompt": "hello"}}

            assert (await env.client.delete(url)).status_code == 200
            assert (await env.client.get(url)).json() == {"value": None}
            # SQL NULL and not JSON `null`, or the import's "only if never saved" misfires.
            assert await _is_sql_null(env, "threshold_settings", "captioning_workflow")

    run(scenario())


def test_workflow_put_refuses_null_and_oversized_values(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            url = f"{BASE}/captioning-workflow"
            assert (await env.client.put(url, json={"value": None})).status_code == 422
            r = await env.client.put(url, json={"value": {"customPrompt": "x" * MAX_WORKFLOW_BYTES}})
            assert r.status_code == 413
            assert (await env.client.get(url)).json() == {"value": None}

    run(scenario())


def test_workflow_save_leaves_thresholds_alone(tmp_path):
    """The workflow lives on the singleton settings row, so writing it first must seed
    that row with the real defaults rather than leaving other settings blank."""
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.put(f"{BASE}/captioning-workflow", json={"value": {"a": 1}})
            r = await env.client.get(f"{API}/settings/thresholds")
            assert r.status_code == 200, r.text
            assert r.json()["blur_threshold"] == 100.0

            r = await env.client.patch(f"{API}/settings/thresholds", json={"blur_threshold": 50.0})
            assert r.status_code == 200, r.text
            assert (await env.client.get(f"{BASE}/captioning-workflow")).json() == {"value": {"a": 1}}

    run(scenario())


# --- comfy gen settings ---------------------------------------------------


def test_gen_settings_stay_null_until_saved_and_do_not_bump_updated_at(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            plan_id = await _create_plan(env)
            url = f"{API}/comfy/plans/{plan_id}/gen-settings"
            assert (await env.client.get(url)).json() == {"value": None}

            before = (await env.client.get(f"{API}/comfy/plans/{plan_id}")).json()["updated_at"]
            r = await env.client.put(url, json={"instructions": "be brief", "batch_size": 3})
            assert r.status_code == 200, r.text
            value = (await env.client.get(url)).json()["value"]
            assert value["instructions"] == "be brief"
            assert value["batch_size"] == 3
            assert value["target_count"] == 20

            # ComfyPage keys the workflow editor on updated_at.
            after = (await env.client.get(f"{API}/comfy/plans/{plan_id}")).json()["updated_at"]
            assert after == before

    run(scenario())


def test_gen_settings_bounds_and_missing_plan(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            plan_id = await _create_plan(env)
            url = f"{API}/comfy/plans/{plan_id}/gen-settings"
            assert (await env.client.put(url, json={"instructions": "x" * 20001})).status_code == 422
            assert (await env.client.put(url, json={"batch_size": 11})).status_code == 422
            assert (await env.client.get(f"{API}/comfy/plans/nope/gen-settings")).status_code == 404
            assert (await env.client.put(f"{API}/comfy/plans/nope/gen-settings", json={})).status_code == 404

    run(scenario())


# --- legacy import --------------------------------------------------------


def test_import_rerun_changes_nothing(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            plan_id = await _create_plan(env)
            body = {
                "presets": [PRESET],
                "categories": ["a", "b"],
                "captioning_workflow": {"customPrompt": "hello", "n": 1},
                "comfy_gen_settings": {plan_id: {"instructions": "be brief"}},
            }
            first = (await env.client.post(f"{BASE}/legacy-import", json=body)).json()
            assert first == {
                "presets": {"created": 1, "skipped": 0, "rejected": []},
                "categories": {"created": 2, "rejected": []},
                "captioning_workflow": "stored",
                "comfy_gen_settings": {plan_id: "stored"},
            }

            second = (await env.client.post(f"{BASE}/legacy-import", json=body)).json()
            assert second == {
                "presets": {"created": 0, "skipped": 1, "rejected": []},
                "categories": {"created": 0, "rejected": []},
                "captioning_workflow": "same",
                "comfy_gen_settings": {plan_id: "same"},
            }
            assert len((await env.client.get(f"{BASE}/presets")).json()) == 1

    run(scenario())


def test_import_leaves_out_sections_it_was_not_given(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.put(f"{BASE}/captioning-workflow", json={"value": {"a": 1}})
            r = await env.client.post(f"{BASE}/legacy-import", json={"categories": ["c"]})
            assert r.json() == {"categories": {"created": 1, "rejected": []}}
            assert (await env.client.get(f"{BASE}/captioning-workflow")).json() == {"value": {"a": 1}}

    run(scenario())


def test_import_workflow_first_copy_wins_and_key_order_is_not_a_conflict(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            url = f"{BASE}/legacy-import"
            r = await env.client.post(url, json={"captioning_workflow": {"a": 1, "b": 2}})
            assert r.json()["captioning_workflow"] == "stored"

            r = await env.client.post(url, json={"captioning_workflow": {"b": 2, "a": 1}})
            assert r.json()["captioning_workflow"] == "same"

            r = await env.client.post(url, json={"captioning_workflow": {"a": 9}})
            assert r.json()["captioning_workflow"] == "conflict"
            assert (await env.client.get(f"{BASE}/captioning-workflow")).json() == {"value": {"a": 1, "b": 2}}

    run(scenario())


def test_import_comfy_conflict_and_missing_plan(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            plan_id = await _create_plan(env)
            url = f"{BASE}/legacy-import"
            await env.client.post(url, json={"comfy_gen_settings": {plan_id: {"instructions": "first"}}})

            r = await env.client.post(url, json={"comfy_gen_settings": {
                plan_id: {"instructions": "second"},
                "deleted-plan": {"instructions": "gone"},
            }})
            assert r.json()["comfy_gen_settings"] == {plan_id: "conflict", "deleted-plan": "discarded"}

            value = (await env.client.get(f"{API}/comfy/plans/{plan_id}/gen-settings")).json()["value"]
            assert value["instructions"] == "first"

    run(scenario())


def test_import_comfy_does_not_bump_updated_at(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            plan_id = await _create_plan(env)
            before = (await env.client.get(f"{API}/comfy/plans/{plan_id}")).json()["updated_at"]
            await env.client.post(f"{BASE}/legacy-import", json={"comfy_gen_settings": {plan_id: {"instructions": "x"}}})
            after = (await env.client.get(f"{API}/comfy/plans/{plan_id}")).json()["updated_at"]
            assert after == before

    run(scenario())


def test_import_one_bad_entry_does_not_cost_the_batch(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            plan_a = await _create_plan(env, "a")
            plan_b = await _create_plan(env, "b")
            r = await env.client.post(f"{BASE}/legacy-import", json={
                "presets": [PRESET, {"name": ""}, "not-a-preset", {**PRESET, "name": "second"}],
                "categories": ["ok", "", 7, "x" * 256],
                "captioning_workflow": ["not", "a", "dict"],
                "comfy_gen_settings": {plan_a: {"batch_size": 99}, plan_b: {"instructions": "fine"}},
            })
            assert r.status_code == 200, r.text
            assert r.json() == {
                "presets": {"created": 2, "skipped": 0, "rejected": [1, 2]},
                "categories": {"created": 1, "rejected": [1, 2, 3]},
                "captioning_workflow": "rejected",
                "comfy_gen_settings": {plan_a: "rejected", plan_b: "stored"},
            }
            assert (await env.client.get(f"{BASE}/categories")).json() == ["ok"]

    run(scenario())


def test_import_dedupes_presets_within_one_batch(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{BASE}/legacy-import", json={"presets": [PRESET, dict(PRESET)]})
            assert r.json()["presets"] == {"created": 1, "skipped": 1, "rejected": []}

    run(scenario())


def test_two_tabs_importing_together_do_not_duplicate_a_preset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            url = f"{BASE}/legacy-import"
            responses = await asyncio.gather(
                *[env.client.post(url, json={"presets": [PRESET]}) for _ in range(4)]
            )
            assert all(r.status_code == 200 for r in responses)
            assert len((await env.client.get(f"{BASE}/presets")).json()) == 1

    run(scenario())


def test_import_keeps_a_preset_differing_only_in_prompt(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{BASE}/legacy-import", json={"presets": [PRESET, {**PRESET, "prompt": "other"}]})
            assert r.json()["presets"]["created"] == 2

    run(scenario())
