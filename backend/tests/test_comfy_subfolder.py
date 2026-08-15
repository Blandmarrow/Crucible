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
