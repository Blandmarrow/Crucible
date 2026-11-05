"""POST /comfy/plans/{id}/duplicate (M6 §8b): what is copied, what is reset, what is not."""
from sqlalchemy import select

from backend.models.comfy import ComfyBlueprint, ComfyLeaf, ComfyRow
from backend.tests.conftest import API, api_env, run, upload_image
from backend.tests.test_blueprint_http import WORKFLOW, _apply, _complete, _plan, _preview, _rows, _spec


def _dup(env, plan_id, **body):
    return env.client.post(f"{API}/comfy/plans/{plan_id}/duplicate", json=body)


def test_duplicate_without_rows_copies_settings_and_spec_not_draft(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            await env.client.post(f"{API}/comfy/plans/{plan_id}/folders", json={"path": "x/y"})
            assert (await _apply(env, plan_id, _spec(), 6, seed=9)).status_code == 200
            async with env.Session() as db:
                bp = (await db.execute(select(ComfyBlueprint))).scalars().one()
                bp.draft = {"scenarios": []}
                bp.brief = "a brief"
                await db.commit()
            r = await _dup(env, plan_id, name="copy")
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["name"] == "copy" and out["row_count"] == 0 and out["status_counts"] == {}
            new_id = out["id"]
            plan = (await env.client.get(f"{API}/comfy/plans/{new_id}")).json()
            assert plan["workflow_json"] == WORKFLOW and plan["pinned_params"][0]["alias"] == "prompt"
            assert plan["output_node_ids"] == ["9"] and plan["declared_folders"] == ["x", "x/y"]
            assert await _rows(env, new_id) == []
            bp = (await env.client.get(f"{API}/comfy/plans/{new_id}/blueprint")).json()["value"]
            assert bp["seed"] == 9 and bp["spec"]["scenarios"][0]["name"] == "solo"
            assert bp["draft"] is None and bp["brief"] == "a brief"
            async with env.Session() as db:
                new_bp = (await db.execute(
                    select(ComfyBlueprint).where(ComfyBlueprint.plan_id == new_id)
                )).scalars().one()
                assert (await db.execute(
                    select(ComfyLeaf).where(ComfyLeaf.blueprint_id == new_bp.id)
                )).first() is None
    run(go())


def test_duplicate_with_rows_resets_and_remaps_leaves(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds_id, plan_id = await _plan(env)
            assert (await _apply(env, plan_id, _spec(), 6)).status_code == 200
            src_rows = await _rows(env, plan_id)
            await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {"prompt": "hand"}})
            await _complete(env, src_rows[0]["id"], [(await upload_image(env, ds_id))["id"]])
            async with env.Session() as db:
                r0 = await db.get(ComfyRow, src_rows[0]["id"])
                r0.error_msg, r0.prompt_id = "boom", "pid"
                db.add(ComfyRow(plan_id=plan_id, sort_order=99, leaf_id="deleted-leaf-id", values={}))
                await db.commit()
            r = await _dup(env, plan_id, name="copy", include_rows=True)
            assert r.status_code == 200, r.text
            new_id = r.json()["id"]
            assert r.json()["row_count"] == 8 and r.json()["status_counts"] == {"pending": 8}
            new_rows = await _rows(env, new_id)
            assert all(x["status"] == "pending" and x["error_msg"] is None and x["prompt_id"] is None
                       and x["image_id"] is None and x["image_ids"] == [] for x in new_rows)
            async with env.Session() as db:
                new_bp = (await db.execute(
                    select(ComfyBlueprint).where(ComfyBlueprint.plan_id == new_id)
                )).scalars().one()
                new_leaves = {l.id for l in (await db.execute(
                    select(ComfyLeaf).where(ComfyLeaf.blueprint_id == new_bp.id))).scalars()}
                old_leaves = {l.id for l in (await db.execute(select(ComfyLeaf))).scalars()} - new_leaves
            assert len(new_leaves) == 3 and new_leaves.isdisjoint(old_leaves)
            leafy = [x for x in new_rows if x["leaf_id"]]
            assert len(leafy) == 6 and all(x["leaf_id"] in new_leaves and x["blueprint_linked"] for x in leafy)
            assert sum(1 for x in new_rows if x["leaf_id"] is None) == 2  # hand row + the orphan, now NULL
            # Same accounting as the source would give with every row pending.
            pv = await _preview(env, new_id, _spec())
            assert all(l["to_add"] == 0 for l in pv["leaves"]) and pv["rows_to_add"] == 0
            # The source is untouched.
            first = next(x for x in await _rows(env, plan_id) if x["id"] == src_rows[0]["id"])
            assert first["status"] == "completed"
    run(go())


def test_duplicate_name_clash_is_400(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env, name="p")
            r = await _dup(env, plan_id, name="p")
            assert r.status_code == 400 and "already exists" in r.json()["detail"]
            assert (await _dup(env, plan_id, name="")).status_code == 422
            assert (await _dup(env, "nope", name="q")).status_code == 404
    run(go())
