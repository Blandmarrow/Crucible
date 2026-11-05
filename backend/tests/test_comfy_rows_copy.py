"""`rows/copy` (M6 §8c): rows copied or moved from another plan, in any dataset, keep
their folder and the values that still mean something in the target."""
from backend.tests.conftest import API, api_env, run

WORKFLOW = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "cfg": 7}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    "9": {"class_type": "SaveImage", "inputs": {}},
}


def _pin(node, inp, alias, *, prompt=False, per_row=True):
    return {"node_id": node, "input": inp, "alias": alias, "is_prompt": prompt, "per_row": per_row}


async def _mk_plan(env, ds_id, name, pins):
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds_id, "name": name, "workflow_json": WORKFLOW,
        "pinned_params": pins, "output_node_ids": ["9"],
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _row(env, plan_id, values, subfolder=""):
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": values})
    assert r.status_code == 200, r.text
    row = r.json()
    if subfolder:
        r = await env.client.patch(f"{API}/comfy/rows/{row['id']}", json={"subfolder": subfolder})
        assert r.status_code == 200, r.text
    return row["id"]


async def _rows(env, plan_id):
    r = await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")
    assert r.status_code == 200, r.text
    return r.json()


async def _link(env, plan_id, *row_ids):
    """Give the rows one current blueprint leaf, so the plan counts them as linked."""
    from backend.models.comfy import ComfyBlueprint, ComfyLeaf, ComfyRow

    async with env.Session() as db:
        bp = ComfyBlueprint(plan_id=plan_id, spec={}, seed=1)
        db.add(bp)
        await db.flush()
        leaf = ComfyLeaf(blueprint_id=bp.id, scenario="s", path="L", target=2)
        db.add(leaf)
        await db.flush()
        for rid in row_ids:
            (await db.get(ComfyRow, rid)).leaf_id = leaf.id
        await db.commit()


def _copy(env, target, **body):
    return env.client.post(f"{API}/comfy/plans/{target}/rows/copy", json=body)


async def _setup(env):
    """Source names its prompt `text` and pins seed and steps per row; the target names
    its prompt `prompt`, pins seed per row, steps as a run default, and has its own `text`
    pin on another input so a carried source prompt alias would land somewhere wrong."""
    src_ds = await env.create_dataset("copy-src")
    dst_ds = await env.create_dataset("copy-dst")
    source = await _mk_plan(env, src_ds["id"], "src", [
        _pin("6", "text", "text", prompt=True), _pin("3", "seed", "seed"), _pin("3", "steps", "steps"),
    ])
    target = await _mk_plan(env, dst_ds["id"], "dst", [
        _pin("6", "text", "prompt", prompt=True), _pin("3", "seed", "seed"),
        _pin("3", "steps", "steps", per_row=False), _pin("3", "cfg", "text"),
    ])
    return source, target


def test_copy_keeps_folder_and_only_meaningful_values(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            source, target = await _setup(env)
            await _row(env, target, {"prompt": "already here"})
            r1 = await _row(env, source, {"text": "red knight", "seed": 5, "steps": 30}, "knights/red")
            r2 = await _row(env, source, {"text": "fen at dusk"})
            empty = await _row(env, source, {"seed": 9})     # no prompt anywhere: skipped

            r = await _copy(env, target, source_plan_id=source, row_ids=[r2, empty, r1])
            assert r.status_code == 200, r.text
            assert r.json() == {"copied": 2, "skipped": 1, "unlinked": 0}

            rows = await _rows(env, target)
            assert [x["values"] for x in rows] == [
                {"prompt": "already here"},
                # Source order, not request order. `steps` is a run default in the target and
                # `text` is a different pin there, so neither is carried.
                {"prompt": "red knight", "seed": 5},
                {"prompt": "fen at dusk"},
            ]
            assert [x["subfolder"] for x in rows] == ["", "knights/red", ""]
            assert [x["sort_order"] for x in rows] == [0, 1, 2]
            new = rows[1]
            assert new["status"] == "pending" and new["leaf_id"] is None
            assert new["prompt_origin"] is None and new["image_id"] is None and new["image_ids"] == []
            # A copy leaves the source alone.
            assert len(await _rows(env, source)) == 3

            # A folder override files every copy there.
            r = await _copy(env, target, source_plan_id=source, row_ids=[r1], subfolder="/dropped/")
            assert (await _rows(env, target))[-1]["subfolder"] == "dropped"
    run(go())


def test_move_deletes_the_source_rows_and_takes_its_guards(tmp_path):
    from backend.models import BackgroundJob
    from backend.models.comfy import ComfyRow

    async def go():
        async with api_env(tmp_path) as env:
            source, target = await _setup(env)
            keep = await _row(env, source, {"text": "keep"})
            go_row = await _row(env, source, {"text": "go"}, "a")
            busy = await _row(env, source, {"text": "busy"})

            async with env.Session() as db:
                (await db.get(ComfyRow, busy)).status = "running"
                await db.commit()
            r = await _copy(env, target, source_plan_id=source, row_ids=[go_row, busy], move=True)
            assert r.status_code == 409 and "running row" in r.json()["detail"]

            async with env.Session() as db:
                (await db.get(ComfyRow, busy)).status = "pending"
                job = BackgroundJob(job_type="comfy_prompts", status="running", config={"plan_id": source})
                db.add(job)
                await db.commit()
                job_id = job.id
            r = await _copy(env, target, source_plan_id=source, row_ids=[go_row], move=True)
            assert r.status_code == 409, r.text
            # A copy is not a write to the source, so the job does not stop it.
            r = await _copy(env, target, source_plan_id=source, row_ids=[keep])
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                (await db.get(BackgroundJob, job_id)).status = "completed"
                await db.commit()
            r = await _copy(env, target, source_plan_id=source, row_ids=[go_row], move=True)
            assert r.json() == {"moved": 1, "skipped": 0, "unlinked": 0}
            assert {x["id"] for x in await _rows(env, source)} == {keep, busy}
            assert [(x["values"]["prompt"], x["subfolder"]) for x in await _rows(env, target)] == [
                ("keep", ""), ("go", "a"),
            ]
    run(go())


def test_move_applies_linked_modes_to_source_blueprint_rows(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            source, target = await _setup(env)
            linked = await _row(env, source, {"text": "linked"}, "L")
            free = await _row(env, source, {"text": "free"}, "L")
            again = await _row(env, source, {"text": "again"}, "L")
            await _link(env, source, linked, again)
            body = {"source_plan_id": source, "row_ids": [linked, free], "move": True}

            r = await _copy(env, target, **body)
            assert r.status_code == 409 and "1 of these rows" in r.json()["detail"]
            r = await _copy(env, target, **body, linked="skip")
            assert r.json() == {"moved": 1, "skipped": 1, "unlinked": 0}
            assert [x["id"] for x in await _rows(env, source)] == [linked, again]
            r = await _copy(env, target, **{**body, "row_ids": [linked]}, linked="unlink")
            assert r.json() == {"moved": 1, "skipped": 0, "unlinked": 1}
            assert [x["id"] for x in await _rows(env, source)] == [again]
            # A copy never asks: the source keeps its row and its link.
            r = await _copy(env, target, source_plan_id=source, row_ids=[again])
            assert r.status_code == 200 and r.json()["copied"] == 1
            assert (await _rows(env, source))[0]["blueprint_linked"] is True
    run(go())


def test_copy_validates_plans_and_ids(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            source, target = await _setup(env)
            ds = await env.create_dataset("copy-nopin")
            no_pin = await _mk_plan(env, ds["id"], "nopin", [_pin("3", "seed", "seed")])
            row = await _row(env, source, {"text": "x"})
            stranger = await _row(env, target, {"prompt": "y"})

            r = await _copy(env, source, source_plan_id=source, row_ids=[row])
            assert r.status_code == 400, r.text
            r = await _copy(env, no_pin, source_plan_id=source, row_ids=[row])
            assert r.status_code == 400 and "prompt" in r.json()["detail"].lower()
            r = await _copy(env, target, source_plan_id=source, row_ids=[row, stranger])
            assert r.status_code == 400, r.text
            r = await _copy(env, target, source_plan_id="nope", row_ids=[row])
            assert r.status_code == 404, r.text
            r = await _copy(env, target, source_plan_id=source, row_ids=[])
            assert r.status_code == 422, r.text
    run(go())
