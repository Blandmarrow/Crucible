"""Blueprint save, preview and Apply over HTTP (M6 §2a): what the preview promises is
what Apply writes, the guard rails, fill accounting from rows and images, and orphans."""
import asyncio

from sqlalchemy import select

from backend.models import BackgroundJob
from backend.models.comfy import ComfyBlueprint, ComfyLeaf, ComfyRow
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

WORKFLOW = {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "template prompt"}},
    "9": {"class_type": "SaveImage", "inputs": {}},
}
PROMPT_PIN = {"node_id": "6", "input": "text", "alias": "prompt", "is_prompt": True}


def _spec(count=2, labels=("Iron Knight", "Salt Witch", "Monk")):
    return {
        "axes": {
            "character": [{"id": f"c{i}", "label": l, "descriptor": f"desc {l}"} for i, l in enumerate(labels)],
            "biome": [{"id": "fen", "label": "Salt Fen", "descriptor": "a fen"}],
        },
        "variations": {"camera": ["low", "wide", "close"]},
        "scenarios": [{"name": "solo", "combine": ["character", "biome"], "count": count,
                       "path": "{biome}/{character}", "template": "{character}, {biome}, {camera}"}],
    }


async def _plan(env, name="p", pins=(PROMPT_PIN,)):
    ds = await env.create_dataset(name + "-ds")
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds["id"], "name": name, "workflow_json": WORKFLOW,
        "pinned_params": list(pins), "output_node_ids": ["9"],
    })
    assert r.status_code == 200, r.text
    return ds["id"], r.json()["id"]


async def _preview(env, plan_id, spec, seed=5, **extra):
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/preview",
                              json={"spec": spec, "seed": seed, **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def _apply(env, plan_id, spec, confirm_total, seed=5, **extra):
    return await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/apply",
                                 json={"spec": spec, "seed": seed, "confirm_total": confirm_total, **extra})


async def _rows(env, plan_id):
    r = await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")
    assert r.status_code == 200, r.text
    return r.json()


async def _complete(env, row_id, image_ids, status="completed"):
    """Mark a row as a run would have left it."""
    async with env.Session() as db:
        row = await db.get(ComfyRow, row_id)
        row.status = status
        row.image_ids = list(image_ids)
        row.image_id = image_ids[0] if image_ids else None
        await db.commit()


def test_get_is_null_until_saved_and_put_round_trips(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            r = await env.client.get(f"{API}/comfy/plans/{plan_id}/blueprint")
            assert r.json() == {"value": None}
            r = await env.client.put(f"{API}/comfy/plans/{plan_id}/blueprint", json={"spec": _spec(), "seed": 9})
            assert r.status_code == 200, r.text
            value = (await env.client.get(f"{API}/comfy/plans/{plan_id}/blueprint")).json()["value"]
            assert value["seed"] == 9
            assert value["spec"]["scenarios"][0]["path"] == "{biome}/{character}"
            assert value["draft"] is None
            # Saving writes no rows.
            assert await _rows(env, plan_id) == []
    run(go())


def test_preview_is_what_apply_writes(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            pv = await _preview(env, plan_id, _spec())
            assert pv["errors"] == []
            assert pv["leaf_count"] == 3
            assert pv["rows_to_add"] == 6

            r = await _apply(env, plan_id, _spec(), 6)
            assert r.status_code == 200, r.text
            assert r.json() == {"created": 6, "leaves": 3, "deleted_rows": 0}

            rows = await _rows(env, plan_id)
            assert len(rows) == 6
            by_folder: dict[str, list[dict]] = {}
            for row in rows:
                by_folder.setdefault(row["subfolder"], []).append(row)
            assert set(by_folder) == {l["path"] for l in pv["leaves"]}
            for leaf in pv["leaves"]:
                got = by_folder[leaf["path"]]
                assert len({r["leaf_id"] for r in got}) == 1 and got[0]["leaf_id"]
                assert [r["values"]["prompt"] for r in got] == leaf["samples"][:2]
            # sort_order is one continuous run.
            assert sorted(r["sort_order"] for r in rows) == list(range(6))

            # The rows exist now, so a second Apply adds nothing.
            assert (await _preview(env, plan_id, _spec()))["rows_to_add"] == 0
            r = await _apply(env, plan_id, _spec(), 0)
            assert r.json()["created"] == 0
            assert len(await _rows(env, plan_id)) == 6

            # Raising the count continues each leaf's walk rather than repeating it.
            pv = await _preview(env, plan_id, _spec(count=3))
            assert pv["rows_to_add"] == 3
            r = await _apply(env, plan_id, _spec(count=3), 3)
            assert r.json()["created"] == 3
            rows = await _rows(env, plan_id)
            for folder in {r["subfolder"] for r in rows}:
                prompts = [r["values"]["prompt"] for r in rows if r["subfolder"] == folder]
                assert len(prompts) == 3 and len(set(prompts)) == 3
    run(go())


def test_confirm_total_mismatch_is_a_409_and_writes_nothing(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            r = await _apply(env, plan_id, _spec(), 3)
            assert r.status_code == 409, r.text
            assert "not the 3 you reviewed" in r.json()["detail"]
            assert await _rows(env, plan_id) == []
            assert (await env.client.get(f"{API}/comfy/plans/{plan_id}/blueprint")).json()["value"] is None
    run(go())


def test_errors_are_a_400_and_the_row_cap_needs_an_override(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            bad = _spec()
            bad["scenarios"][0]["path"] = "{biome}"
            pv = await _preview(env, plan_id, bad)
            assert pv["errors"] and pv["leaves"] == []
            r = await _apply(env, plan_id, bad, 0)
            assert r.status_code == 400, r.text
            assert "is made by both" in r.json()["detail"]

            big = _spec(count=700)  # 3 leaves x 700 = 2,100 > 2,000
            pv = await _preview(env, plan_id, big)
            assert pv["rows_to_add"] == 2100 and pv["over_cap"] and pv["row_cap"] == 2000
            r = await _apply(env, plan_id, big, 2100)
            assert r.status_code == 400, r.text
            assert "allow a larger expansion" in r.json()["detail"]
            assert await _rows(env, plan_id) == []

            pv = await _preview(env, plan_id, big, allow_over_cap=True)
            assert not pv["over_cap"]
            r = await _apply(env, plan_id, big, 2100, allow_over_cap=True)
            assert r.status_code == 200, r.text
            assert len(await _rows(env, plan_id)) == 2100
    run(go())


def test_apply_needs_a_prompt_pin(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env, pins=())
            r = await _apply(env, plan_id, _spec(), 6)
            assert r.status_code == 400, r.text
            assert "marked as the prompt" in r.json()["detail"]
    run(go())


def test_apply_is_a_409_while_a_run_or_prompt_job_is_live_on_that_plan(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env, "a")
            _, other_id = await _plan(env, "b")
            for job_type in ("comfy_generate", "comfy_prompts"):
                async with env.Session() as db:
                    job = BackgroundJob(job_type=job_type, status="pending", config={"plan_id": plan_id})
                    db.add(job)
                    await db.commit()
                    job_id = job.id
                r = await _apply(env, plan_id, _spec(), 6)
                assert r.status_code == 409, r.text
                assert "queued or running" in r.json()["detail"]
                # The same job does not block another plan.
                r = await _apply(env, other_id, _spec(), 6 if job_type == "comfy_generate" else 0)
                assert r.status_code == 200, r.text
                async with env.Session() as db:
                    (await db.get(BackgroundJob, job_id)).status = "completed"
                    await db.commit()
            r = await _apply(env, plan_id, _spec(), 6)
            assert r.status_code == 200, r.text
    run(go())


def test_fill_counts_images_that_still_exist_and_open_rows(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds_id, plan_id = await _plan(env)
            spec = _spec(count=2, labels=("Knight",))
            assert (await _apply(env, plan_id, spec, 2)).status_code == 200
            r1, r2 = await _rows(env, plan_id)

            # A failed row is a future image, not a missing one.
            await _complete(env, r2["id"], [], status="failed")
            assert (await _preview(env, plan_id, spec))["leaves"][0]["to_add"] == 0

            a = await upload_image(env, ds_id, "a.png", png_bytes((1, 2, 3)))
            b = await upload_image(env, ds_id, "b.png", png_bytes((4, 5, 6)))
            await _complete(env, r1["id"], [a["id"]])
            await _complete(env, r2["id"], [b["id"]])
            pv = await _preview(env, plan_id, spec)
            assert pv["leaves"][0]["kept"] == 2 and pv["rows_to_add"] == 0

            # Deleting an image in the gallery makes the leaf short again.
            r = await env.client.delete(f"{API}/images/{b['id']}")
            assert r.status_code == 204, r.text
            pv = await _preview(env, plan_id, spec)
            assert pv["leaves"][0]["kept"] == 1 and pv["rows_to_add"] == 1

            # A workflow that makes 2 images per row needs half the rows, and a
            # pending row counts as one row's worth of images.
            c = await upload_image(env, ds_id, "c.png", png_bytes((7, 8, 9)))
            await _complete(env, r1["id"], [a["id"], c["id"]])
            await _complete(env, r2["id"], [], status="pending")
            pv = await _preview(env, plan_id, _spec(count=6, labels=("Knight",)))
            # kept 2 (a, c) + 1 open row x yield 2 = 4 -> (6 - 4) / 2 = 1 row
            assert pv["leaves"][0]["yield"] == 2.0
            assert pv["rows_to_add"] == 1
    run(go())


def test_a_label_edit_orphans_the_old_leaf_and_can_delete_only_its_pending_rows(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            ds_id, plan_id = await _plan(env)
            old = _spec(count=2, labels=("Knight",))
            assert (await _apply(env, plan_id, old, 2)).status_code == 200
            done, pending = await _rows(env, plan_id)
            img = await upload_image(env, ds_id)
            await _complete(env, done["id"], [img["id"]])

            new = _spec(count=2, labels=("Iron Knight",))
            pv = await _preview(env, plan_id, new)
            assert pv["orphans"] == {"leaves": 1, "pending": 1, "running": 0, "failed": 0,
                                     "completed": 1, "images": 1}
            assert pv["rows_to_add"] == 2  # orphans count toward nothing

            r = await _apply(env, plan_id, new, 2, delete_orphan_pending=True)
            assert r.status_code == 200, r.text
            assert r.json() == {"created": 2, "leaves": 1, "deleted_rows": 1}

            rows = await _rows(env, plan_id)
            ids = {r["id"] for r in rows}
            assert done["id"] in ids and pending["id"] not in ids
            # The completed row keeps naming its deleted leaf, as provenance.
            kept = next(r for r in rows if r["id"] == done["id"])
            async with env.Session() as db:
                leaves = (await db.execute(select(ComfyLeaf))).scalars().all()
            assert [l.path for l in leaves] == ["salt_fen/iron_knight"]
            assert kept["leaf_id"] not in {l.id for l in leaves}
            assert (await _preview(env, plan_id, new))["orphans"]["pending"] == 0
    run(go())


def test_deleting_the_plan_deletes_its_blueprint_and_leaves(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            assert (await _apply(env, plan_id, _spec(), 6)).status_code == 200
            r = await env.client.delete(f"{API}/comfy/plans/{plan_id}")
            assert r.status_code == 204, r.text
            async with env.Session() as db:
                assert (await db.execute(select(ComfyBlueprint))).first() is None
                assert (await db.execute(select(ComfyLeaf))).first() is None
    run(go())


def test_a_re_made_folder_adopts_its_old_rows_instead_of_refilling_from_zero(tmp_path):
    """Rename a label and back: the folder comes back under a new leaf id, and must
    count the rows and images already filed there rather than ask for a second target."""
    async def go():
        async with api_env(tmp_path) as env:
            ds_id, plan_id = await _plan(env)
            spec = _spec(count=2, labels=("Knight",))
            assert (await _apply(env, plan_id, spec, 2)).status_code == 200
            r1, r2 = await _rows(env, plan_id)
            img = await upload_image(env, ds_id)
            await _complete(env, r1["id"], [img["id"]])

            renamed = _spec(count=2, labels=("Iron Knight",))
            assert (await _apply(env, plan_id, renamed, 2)).status_code == 200
            assert len(await _rows(env, plan_id)) == 4

            pv = await _preview(env, plan_id, spec)
            back = next(l for l in pv["leaves"] if l["path"] == "salt_fen/knight")
            assert (back["kept"], back["open"], back["to_add"]) == (1, 1, 0)
            # Only the renamed folder's own rows remain orphans; the re-made one's are adopted.
            assert pv["orphans"]["pending"] == 2 and pv["orphans"]["images"] == 0

            r = await _apply(env, plan_id, spec, 0)
            assert r.status_code == 200, r.text
            assert r.json()["created"] == 0
            rows = {r["id"]: r for r in await _rows(env, plan_id)}
            async with env.Session() as db:
                leaf = (await db.execute(select(ComfyLeaf))).scalars().one()
            assert leaf.path == "salt_fen/knight" and leaf.cursor == 2
            assert rows[r1["id"]]["leaf_id"] == leaf.id and rows[r2["id"]]["leaf_id"] == leaf.id
    run(go())


def test_a_spec_with_errors_reports_no_orphans(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            assert (await _apply(env, plan_id, _spec(), 6)).status_code == 200
            mid_edit = _spec(labels=("Iron Knight", "Salt Witch", ""))  # a fresh, empty entity
            pv = await _preview(env, plan_id, mid_edit)
            assert pv["errors"]
            assert pv["orphans"]["leaves"] == 0 and pv["orphans"]["pending"] == 0
    run(go())


def test_a_double_clicked_apply_adds_the_rows_once(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            first, second = await asyncio.gather(
                _apply(env, plan_id, _spec(), 6), _apply(env, plan_id, _spec(), 6)
            )
            assert sorted([first.status_code, second.status_code]) == [200, 409]
            assert len(await _rows(env, plan_id)) == 6
    run(go())


def test_apply_continues_the_plans_sort_order(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            for text in ("hand one", "hand two"):
                r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {"prompt": text}})
                assert r.status_code == 200, r.text
            assert (await _apply(env, plan_id, _spec(), 6)).status_code == 200
            rows = await _rows(env, plan_id)
            hand = [r["sort_order"] for r in rows if r["leaf_id"] is None]
            made = [r["sort_order"] for r in rows if r["leaf_id"]]
            assert len(made) == 6 and min(made) > max(hand)
            assert len(set(hand + made)) == 8
    run(go())
