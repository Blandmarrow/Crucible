"""Per-row destination folders for a ComfyUI run.

A row carries its own `subfolder`; a run files each row's outputs under
`{run base folder}/{row folder}`, declares the whole tree before the first image
lands, and names files after the row's own folder leaf. Request-level for the
same reason as `test_provenance_http.py`: most of this lives in the job body of
a router, where no service-level test reaches.
"""
from pathlib import Path

from backend.tests.conftest import API, api_env, png_bytes, run, wait_for_job
from backend.tests.test_provenance_http import _FakeComfyClient

WORKFLOW = {"9": {"class_type": "SaveImage", "inputs": {}}}


async def _plan(env, ds_name="comfy-sub-ds", plan_name="plan"):
    """A dataset + a one-SaveImage plan, with ComfyClient already stubbed."""
    await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": "http://fake:8188"})
    ds = await env.create_dataset(ds_name)
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds["id"], "name": plan_name,
        "workflow_json": WORKFLOW, "output_node_ids": ["9"],
    })
    assert r.status_code == 200, r.text
    return ds, r.json()["id"]


async def _add_row(env, plan_id, **patch) -> dict:
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {}})
    assert r.status_code == 200, r.text
    row = r.json()
    if patch:
        r = await env.client.patch(f"{API}/comfy/rows/{row['id']}", json=patch)
        assert r.status_code == 200, r.text
        row = r.json()
    return row


async def _rows(env, plan_id) -> list[dict]:
    r = await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")
    assert r.status_code == 200, r.text
    return r.json()


async def _run_plan(env, plan_id, **body) -> dict:
    r = await env.client.post(f"{API}/comfy/run", json={"plan_id": plan_id, **body})
    assert r.status_code == 200, r.text
    return await wait_for_job(env, r.json()["job_id"])


# ── The column and the row endpoints ──────────────────────────────────────────

def test_row_subfolder_defaults_empty_and_round_trips(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            row = await _add_row(env, plan_id)
            assert row["subfolder"] == ""
            assert row["leaf_id"] is None

            r = await env.client.patch(f"{API}/comfy/rows/{row['id']}", json={"subfolder": "a/b"})
            assert r.status_code == 200, r.text
            assert r.json()["subfolder"] == "a/b"
            assert (await _rows(env, plan_id))[0]["subfolder"] == "a/b"

    run(scenario())


def test_row_subfolder_is_normalized_on_write(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            row = await _add_row(env, plan_id, subfolder="/a//b/")
            assert row["subfolder"] == "a/b"

            r = await env.client.patch(f"{API}/comfy/rows/{row['id']}", json={"subfolder": "a/../b"})
            assert r.status_code == 400, r.text
            assert (await _rows(env, plan_id))[0]["subfolder"] == "a/b"

    run(scenario())


def test_subfolder_edit_does_not_reset_a_completed_row(tmp_path, monkeypatch):
    """Both halves of the carve-out: a folder edit keeps the result, a values edit resets."""
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            _, plan_id = await _plan(env)
            row = await _add_row(env, plan_id)
            job = await _run_plan(env, plan_id)
            assert job["status"] == "completed", job

            done = (await _rows(env, plan_id))[0]
            assert done["status"] == "completed" and done["image_id"]

            r = await env.client.patch(f"{API}/comfy/rows/{row['id']}", json={"subfolder": "later"})
            assert r.status_code == 200, r.text
            after = r.json()
            assert after["status"] == "completed"
            assert after["image_id"] == done["image_id"]

            # A values edit still resets — the carve-out is folder-only.
            r = await env.client.patch(f"{API}/comfy/rows/{row['id']}", json={"values": {"x": "y"}})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "pending"

    run(scenario())


# ── The bulk endpoint ─────────────────────────────────────────────────────────

def test_set_subfolder_scopes_to_row_ids_and_skips_no_ops(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            rows = [await _add_row(env, plan_id) for _ in range(3)]
            picked = [rows[0]["id"], rows[2]["id"]]

            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows/set-subfolder",
                json={"subfolder": "batch/one", "row_ids": picked},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"updated": 2}

            by_id = {row["id"]: row for row in await _rows(env, plan_id)}
            assert by_id[rows[0]["id"]]["subfolder"] == "batch/one"
            assert by_id[rows[2]["id"]]["subfolder"] == "batch/one"
            assert by_id[rows[1]["id"]]["subfolder"] == ""

            # Repeating it changes nothing, and says so.
            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows/set-subfolder",
                json={"subfolder": "batch/one", "row_ids": picked},
            )
            assert r.json() == {"updated": 0}

            # `None` means every row; `[]` means none (bulk_edit_rows' convention).
            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows/set-subfolder", json={"subfolder": "all"}
            )
            assert r.json() == {"updated": 3}
            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows/set-subfolder",
                json={"subfolder": "none", "row_ids": []},
            )
            assert r.json() == {"updated": 0}

    run(scenario())


def test_set_subfolder_rejects_dot_dot(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            await _add_row(env, plan_id, subfolder="keep")

            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows/set-subfolder", json={"subfolder": "../evil"}
            )
            assert r.status_code == 400, r.text
            assert (await _rows(env, plan_id))[0]["subfolder"] == "keep"

    run(scenario())


def test_set_subfolder_does_not_reset_completed_rows(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            _, plan_id = await _plan(env)
            await _add_row(env, plan_id)
            assert (await _run_plan(env, plan_id))["status"] == "completed"

            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows/set-subfolder", json={"subfolder": "moved"}
            )
            assert r.json() == {"updated": 1}
            after = (await _rows(env, plan_id))[0]
            assert after["status"] == "completed"
            assert after["subfolder"] == "moved"
            assert after["image_id"]

    run(scenario())


# ── The run ───────────────────────────────────────────────────────────────────

def test_run_stamps_combined_per_row_subfolder(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            ds, plan_id = await _plan(env)
            await _add_row(env, plan_id, subfolder="a")
            await _add_row(env, plan_id, subfolder="b/c")

            assert (await _run_plan(env, plan_id, subfolder="run"))["status"] == "completed"

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert sorted(i["subfolder"] for i in images) == ["run/a", "run/b/c"]

    run(scenario())


def test_run_declares_targets_and_ancestors_before_importing(tmp_path, monkeypatch):
    """The whole tree must be in the sidebar as the run starts, not row by row.

    `declare_subfolders` commits, so it cannot run inside the row loop without
    breaking the per-row rollback — which makes "declared before the first image
    lands" the observable consequence of doing it correctly.
    """
    seen_at_first_fetch: list[list[str]] = []

    class _ProbeClient(_FakeComfyClient):
        async def fetch_image(self, filename, subfolder="", type="output"):
            if not seen_at_first_fetch:
                from sqlalchemy import select

                from backend.database import AsyncSessionLocal
                from backend.models import Dataset

                async with AsyncSessionLocal() as s:
                    ds_row = (await s.execute(select(Dataset))).scalars().first()
                    seen_at_first_fetch.append(sorted(ds_row.declared_subfolders or []))
            return png_bytes()

    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _ProbeClient)
            ds, plan_id = await _plan(env)
            await _add_row(env, plan_id, subfolder="a")
            await _add_row(env, plan_id, subfolder="b/c")

            assert (await _run_plan(env, plan_id, subfolder="run"))["status"] == "completed"

            paths = {s["path"] for s in (
                await env.client.get(f"{API}/datasets/{ds['id']}/subfolders")
            ).json()}
            assert {"run", "run/a", "run/b", "run/b/c"} <= paths

            # ...and every one of them was already declared before the first import.
            assert seen_at_first_fetch and set(seen_at_first_fetch[0]) >= {
                "run", "run/a", "run/b", "run/b/c"
            }

    run(scenario())


def test_run_never_declares_an_empty_subfolder(tmp_path, monkeypatch):
    """`declare_subfolder("")` would append a nameless entry that never goes away."""
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            ds, plan_id = await _plan(env)
            await _add_row(env, plan_id)

            assert (await _run_plan(env, plan_id))["status"] == "completed"

            # `list_subfolders` reports a "" bucket for root-level images regardless;
            # what must stay clean is the declared list, which is permanent.
            async with env.Session() as s:
                from sqlalchemy import select

                from backend.models import Dataset

                ds_row = (await s.execute(select(Dataset).where(Dataset.id == ds["id"]))).scalar_one()
                assert "" not in (ds_row.declared_subfolders or [])

    run(scenario())


def test_run_filename_stem_comes_from_the_row_folder_leaf(tmp_path, monkeypatch):
    """A foldered run is named after its folders, not `plan_0001`…`plan_5000`.

    The stem is the row's **own** leaf, so a run-level base folder alone leaves
    the plan-name stem exactly as it was.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            ds, plan_id = await _plan(env, plan_name="Medieval Set")
            await _add_row(env, plan_id, subfolder="biome/knight")
            await _add_row(env, plan_id, subfolder="fen")
            await _add_row(env, plan_id)

            assert (await _run_plan(env, plan_id, subfolder="run"))["status"] == "completed"

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            stems = sorted(i["filename"].rsplit(".", 1)[0] for i in images)
            assert stems == ["fen", "knight", "medieval_set"], stems

    run(scenario())


def test_run_filename_uniqueness_spans_folders(tmp_path, monkeypatch):
    """One occupancy set per run: `uq_dataset_filename` and `thumbnails/` are flat."""
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            ds, plan_id = await _plan(env)
            await _add_row(env, plan_id, subfolder="a/close")
            await _add_row(env, plan_id, subfolder="b/close")

            assert (await _run_plan(env, plan_id))["status"] == "completed"

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            names = sorted(i["filename"] for i in images)
            assert names == ["close.png", "close_001.png"], names
            thumbs = sorted(
                p.name for p in (Path(ds["folder_path"]) / "thumbnails").glob("*.webp")
            )
            assert thumbs == ["close.webp", "close_001.webp"], thumbs

    run(scenario())


def test_run_tolerates_a_dot_dot_subfolder_written_directly(tmp_path, monkeypatch):
    """A row can hold a `..` the write-time guard never saw — the job must not fail it.

    Gallery subfolders are virtual labels, so this is a wrong folder name rather
    than a path escape; `join_subfolder` drops the segment and the row completes.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router

            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            ds, plan_id = await _plan(env)
            row = await _add_row(env, plan_id)

            async with env.Session() as s:
                from backend.models.comfy import ComfyRow

                db_row = await s.get(ComfyRow, row["id"])
                db_row.subfolder = "../evil"
                await s.commit()

            assert (await _run_plan(env, plan_id))["status"] == "completed"

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert [i["subfolder"] for i in images] == ["evil"], images

    run(scenario())


def test_filename_stem_follows_the_normalized_folder(tmp_path, monkeypatch):
    """A directly written `a/b/` or `a/b/..` names the file after the folder it lands in.

    The stem used to come from the raw string, so a trailing slash fell back to the
    plan slug and a trailing `..` slugified to `image`, while the image was filed
    under the normalized path.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            import backend.routers.comfy as comfy_router
            from backend.models.comfy import ComfyRow

            monkeypatch.setattr(comfy_router, "ComfyClient", _FakeComfyClient)
            ds, plan_id = await _plan(env)
            first = await _add_row(env, plan_id)
            second = await _add_row(env, plan_id)

            async with env.Session() as s:
                (await s.get(ComfyRow, first["id"])).subfolder = "fen/knight/"
                (await s.get(ComfyRow, second["id"])).subfolder = "fen/knight/../squire"
                await s.commit()

            assert (await _run_plan(env, plan_id))["status"] == "completed"

            images = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            by_folder = {i["subfolder"]: i["filename"] for i in images}
            assert by_folder["fen/knight"].startswith("knight"), images
            assert by_folder["fen/knight/squire"].startswith("squire"), images

    run(scenario())


# ── Plan folders: create, move, delete (M6 §8b) ───────────────────────────────

def _f(plan_id, op=""):
    return f"{API}/comfy/plans/{plan_id}/folders{op}"


def test_folder_create_adds_ancestors_and_refuses_an_existing_one(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            r = await env.client.post(_f(plan_id), json={"path": "/a/b/c/"})
            assert r.status_code == 200, r.text
            assert r.json()["declared_folders"] == ["a", "a/b", "a/b/c"]
            assert (await env.client.post(_f(plan_id), json={"path": "a/b"})).status_code == 409
            assert (await env.client.post(_f(plan_id), json={"path": ""})).status_code == 400
            assert (await env.client.post(_f(plan_id), json={"path": "../x"})).status_code == 400
            # A folder implied by a row exists too, ancestors included.
            await _add_row(env, plan_id, subfolder="r/s")
            assert (await env.client.post(_f(plan_id), json={"path": "r"})).status_code == 409
            assert (await env.client.post(_f(plan_id), json={"path": "r/s"})).status_code == 409
            got = (await env.client.get(f"{API}/comfy/plans/{plan_id}")).json()
            assert got["declared_folders"] == ["a", "a/b", "a/b/c"]
    run(scenario())


def test_folder_move_errors_and_effects(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            r_exact = await _add_row(env, plan_id, subfolder="a_b")
            r_desc = await _add_row(env, plan_id, subfolder="a_b/deep")
            r_other = await _add_row(env, plan_id, subfolder="axb")
            r_root = await _add_row(env, plan_id)
            await env.client.post(_f(plan_id), json={"path": "a_b/empty"})
            await env.client.post(_f(plan_id), json={"path": "taken"})

            def move(p, n):
                return env.client.post(_f(plan_id, "/move"), json={"path": p, "new_path": n})

            assert (await move("", "x")).status_code == 400
            assert (await move("a_b", "")).status_code == 400
            assert (await move("a_b", "a_b")).status_code == 400
            assert (await move("a_b", "a_b/inner")).status_code == 400
            assert (await move("nope", "x")).status_code == 404
            assert (await move("a_b", "taken")).status_code == 409
            r = await move("a_b", "z/q")
            assert r.status_code == 200, r.text
            assert r.json() == {"updated": 2}
            by_id = {x["id"]: x["subfolder"] for x in await _rows(env, plan_id)}
            assert by_id[r_exact["id"]] == "z/q"
            assert by_id[r_desc["id"]] == "z/q/deep"
            assert by_id[r_other["id"]] == "axb"  # the LIKE wildcard did not match
            assert by_id[r_root["id"]] == ""
            declared = (await env.client.get(f"{API}/comfy/plans/{plan_id}")).json()["declared_folders"]
            assert "z/q/empty" in declared and "z" in declared and "a_b/empty" not in declared
            assert "taken" in declared
    run(scenario())


def test_folder_delete_removes_rows_and_prunes_declared(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            await _add_row(env, plan_id, subfolder="a_b")
            await _add_row(env, plan_id, subfolder="a_b/deep")
            keep = await _add_row(env, plan_id, subfolder="axb")
            await env.client.post(_f(plan_id), json={"path": "a_b/empty"})

            def dele(p):
                return env.client.post(_f(plan_id, "/delete"), json={"path": p})

            assert (await dele("")).status_code == 400
            assert (await dele("nope")).status_code == 404
            r = await dele("a_b")
            assert r.status_code == 200 and r.json() == {"deleted": 2}
            assert [x["id"] for x in await _rows(env, plan_id)] == [keep["id"]]
            assert (await env.client.get(f"{API}/comfy/plans/{plan_id}")).json()["declared_folders"] == []
    run(scenario())


def test_folder_writes_refuse_while_a_job_or_run_is_live(tmp_path, monkeypatch):
    from backend.models import BackgroundJob, Run

    async def scenario():
        async with api_env(tmp_path) as env:
            ds, plan_id = await _plan(env)
            await _add_row(env, plan_id, subfolder="a")
            async with env.Session() as db:
                job = BackgroundJob(job_type="comfy_generate", status="pending", config={"plan_id": plan_id})
                db.add(job)
                await db.commit()
                job_id = job.id
            bodies = [("", {"path": "n"}), ("/move", {"path": "a", "new_path": "b"}), ("/delete", {"path": "a"})]
            for op, body in bodies:
                assert (await env.client.post(_f(plan_id, op), json=body)).status_code == 409
            async with env.Session() as db:
                (await db.get(BackgroundJob, job_id)).status = "completed"
                db.add(Run(dataset_id=ds["id"], plan_id=plan_id, name="x", recipe={}, stages=[],
                           context={}, status="running"))
                await db.commit()
            for op, body in bodies:
                r = await env.client.post(_f(plan_id, op), json=body)
                assert r.status_code == 409 and "automated run" in r.json()["detail"]
    run(scenario())


async def _link_row(env, plan_id, row_id, path="L"):
    from backend.models.comfy import ComfyBlueprint, ComfyLeaf, ComfyRow

    async with env.Session() as db:
        bp = ComfyBlueprint(plan_id=plan_id, spec={}, seed=1)
        db.add(bp)
        await db.flush()
        leaf = ComfyLeaf(blueprint_id=bp.id, scenario="s", path=path, target=2)
        db.add(leaf)
        await db.flush()
        (await db.get(ComfyRow, row_id)).leaf_id = leaf.id
        await db.commit()


def test_set_subfolder_linked_modes(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            linked = await _add_row(env, plan_id, subfolder="L")
            free = await _add_row(env, plan_id, subfolder="L")
            await _link_row(env, plan_id, linked["id"])
            rows = {x["id"]: x for x in await _rows(env, plan_id)}
            assert rows[linked["id"]]["blueprint_linked"] is True
            assert rows[free["id"]]["blueprint_linked"] is False
            url = f"{API}/comfy/plans/{plan_id}/rows/set-subfolder"
            # refuse is the default and moves nothing, not even the free row
            r = await env.client.post(url, json={"subfolder": "M"})
            assert r.status_code == 409 and "1 of these rows came from the blueprint" in r.json()["detail"]
            assert {x["subfolder"] for x in await _rows(env, plan_id)} == {"L"}
            # skip leaves the linked row
            r = await env.client.post(url, json={"subfolder": "M", "linked": "skip"})
            assert r.json() == {"updated": 1}
            rows = {x["id"]: x for x in await _rows(env, plan_id)}
            assert rows[linked["id"]]["subfolder"] == "L" and rows[linked["id"]]["blueprint_linked"]
            assert rows[free["id"]]["subfolder"] == "M"
            # a no-op target never counts as a change, so it is never refused
            r = await env.client.post(url, json={"subfolder": "L", "row_ids": [linked["id"]]})
            assert r.status_code == 200 and r.json() == {"updated": 0}
            # unlink moves it and cuts the link
            r = await env.client.post(url, json={"subfolder": "M", "linked": "unlink"})
            assert r.json() == {"updated": 1}
            rows = {x["id"]: x for x in await _rows(env, plan_id)}
            assert rows[linked["id"]]["subfolder"] == "M"
            assert rows[linked["id"]]["leaf_id"] is None and not rows[linked["id"]]["blueprint_linked"]
    run(scenario())


def test_patch_row_linked_modes(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            row = await _add_row(env, plan_id, subfolder="L")
            await _link_row(env, plan_id, row["id"])
            url = f"{API}/comfy/rows/{row['id']}"
            assert (await env.client.patch(url, json={"subfolder": "M"})).status_code == 409
            r = await env.client.patch(url, json={"subfolder": "M", "linked": "skip"})
            assert r.status_code == 200 and r.json()["subfolder"] == "L"
            r = await env.client.patch(url, json={"subfolder": "L"})  # unchanged: fine
            assert r.status_code == 200
            r = await env.client.patch(url, json={"subfolder": "M", "linked": "unlink"})
            assert r.json()["subfolder"] == "M" and r.json()["leaf_id"] is None
    run(scenario())


def test_folder_writes_match_the_subtree_case_sensitively(tmp_path, monkeypatch):
    """SQLite's LIKE ignores ASCII case, so a LIKE prefix for `knight/` also took
    `Knight/a`: a delete of one folder deleted another folder's rows (review, §8b)."""
    async def scenario():
        async with api_env(tmp_path) as env:
            _, plan_id = await _plan(env)
            lower = await _add_row(env, plan_id, subfolder="knight/b")
            upper = await _add_row(env, plan_id, subfolder="Knight/a")
            r = await env.client.post(_f(plan_id, "/move"), json={"path": "knight", "new_path": "monk"})
            assert r.status_code == 200 and r.json() == {"updated": 1}
            by_id = {x["id"]: x["subfolder"] for x in await _rows(env, plan_id)}
            assert by_id == {lower["id"]: "monk/b", upper["id"]: "Knight/a"}
            r = await env.client.post(_f(plan_id, "/delete"), json={"path": "monk"})
            assert r.status_code == 200 and r.json() == {"deleted": 1}
            assert [x["id"] for x in await _rows(env, plan_id)] == [upper["id"]]
    run(scenario())


def test_an_unlink_is_refused_while_an_automated_run_is_live(tmp_path, monkeypatch):
    """An unlink leaves its leaf short, and a running or paused run would refill it on
    its next pass, as Apply's own refusal exists to stop. Refuse and skip are unaffected."""
    from backend.models import Run

    async def scenario():
        async with api_env(tmp_path) as env:
            ds, plan_id = await _plan(env)
            linked = await _add_row(env, plan_id, subfolder="L")
            free = await _add_row(env, plan_id, subfolder="L")
            await _link_row(env, plan_id, linked["id"])
            async with env.Session() as db:
                db.add(Run(dataset_id=ds["id"], plan_id=plan_id, name="x", recipe={}, stages=[],
                           context={}, status="paused"))
                await db.commit()
            url = f"{API}/comfy/plans/{plan_id}/rows/set-subfolder"
            r = await env.client.post(url, json={"subfolder": "M", "linked": "unlink"})
            assert r.status_code == 409 and "automated run" in r.json()["detail"]
            r = await env.client.patch(f"{API}/comfy/rows/{linked['id']}", json={"subfolder": "M", "linked": "unlink"})
            assert r.status_code == 409 and "automated run" in r.json()["detail"]
            # Moving only hand-made rows still works mid-run.
            r = await env.client.post(url, json={"subfolder": "M", "linked": "skip"})
            assert r.status_code == 200 and r.json() == {"updated": 1}
            rows = {x["id"]: x for x in await _rows(env, plan_id)}
            assert rows[linked["id"]]["blueprint_linked"] and rows[free["id"]]["subfolder"] == "M"
    run(scenario())
