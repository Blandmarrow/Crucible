"""Top up (M6 §2c) end to end against FakeComfy: Apply, reset the blueprint's failed
rows, run its pending ones; a second press does nothing once every folder is full."""
from sqlalchemy import select

from backend.models import Image
from backend.models.comfy import ComfyRow
from backend.tests.conftest import API, api_env, run, wait_for_job
from backend.tests.fake_comfy import FakeComfy
from backend.tests.test_comfy_run_body import _setup, _workflow

SPEC = {
    "axes": {"character": [{"id": "k", "label": "Iron Knight", "descriptor": "an iron knight"},
                           {"id": "w", "label": "Salt Witch", "descriptor": "a salt witch"}]},
    "variations": {"camera": ["low angle", "wide shot"]},
    "scenarios": [{"name": "solo", "combine": ["character"], "count": 2,
                   "path": "knights/{character}", "template": "{character}, {camera}"}],
}


async def _preview(env, plan_id):
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/preview", json={"spec": SPEC, "seed": 3})
    assert r.status_code == 200, r.text
    return r.json()


async def _top_up(env, plan_id, confirm_total, **extra):
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/top-up", json={
        "spec": SPEC, "seed": 3, "confirm_total": confirm_total, "subfolder": "gen", **extra,
    })
    assert r.status_code == 200, r.text
    out = r.json()
    if out["job_id"]:
        job = await wait_for_job(env, out["job_id"])
        assert job["status"] == "completed", job
    return out


async def _images(env, ds_id):
    return (await env.client.get(f"{API}/images/", params={"dataset_id": ds_id})).json()


def test_top_up_fills_only_what_is_short_and_a_second_press_does_nothing(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])

            pv = await _preview(env, plan_id)
            assert (pv["rows_to_add"], pv["images_short"]) == (4, 4)
            out = await _top_up(env, plan_id, 4)
            assert (out["created"], out["reset"], out["total"]) == (4, 0, 4)
            images = await _images(env, ds["id"])
            assert sorted(i["subfolder"] for i in images) == [
                "gen/knights/iron_knight", "gen/knights/iron_knight",
                "gen/knights/salt_witch", "gen/knights/salt_witch",
            ]

            # Every folder is full: nothing added, nothing queued.
            pv = await _preview(env, plan_id)
            assert (pv["rows_to_add"], pv["images_short"]) == (0, 0)
            out = await _top_up(env, plan_id, 0)
            assert out == {"created": 0, "reset": 0, "deleted_rows": 0, "job_id": None, "total": 0}

            # Delete one image in the gallery: that folder alone is short by one.
            gone = next(i for i in images if i["subfolder"] == "gen/knights/salt_witch")
            r = await env.client.delete(f"{API}/images/{gone['id']}")
            assert r.status_code == 204, r.text
            pv = await _preview(env, plan_id)
            short = {l["path"]: l["short"] for l in pv["leaves"]}
            assert short == {"knights/iron_knight": 0, "knights/salt_witch": 1}
            out = await _top_up(env, plan_id, 1)
            assert (out["created"], out["total"]) == (1, 1)
            assert len(await _images(env, ds["id"])) == 4
            assert fake.violations == []
    run(go())


def test_a_failed_row_is_reset_and_rerun_not_duplicated(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
            await _top_up(env, plan_id, 4)

            # Leave one row as a failed run would: no image, an error.
            async with env.Session() as db:
                row = (await db.execute(select(ComfyRow).where(ComfyRow.plan_id == plan_id))).scalars().first()
                img = await db.get(Image, row.image_ids[0])
                image_id = img.id
                row.status, row.error_msg, row.image_ids, row.image_id = "failed", "out of memory", [], None
                await db.commit()
            r = await env.client.delete(f"{API}/images/{image_id}")
            assert r.status_code == 204, r.text

            pv = await _preview(env, plan_id)
            # The failed row is a future image, so no row is added — it is re-run.
            assert (pv["rows_to_add"], pv["images_short"], pv["rows_failed"]) == (0, 1, 1)
            out = await _top_up(env, plan_id, 0)
            assert (out["created"], out["reset"], out["total"]) == (0, 1, 1)
            rows = (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json()
            assert len(rows) == 4 and all(r["status"] == "completed" for r in rows)
            assert all(r["error_msg"] is None for r in rows)
            assert len(await _images(env, ds["id"])) == 4
    run(go())


def test_top_up_without_comfyui_writes_nothing(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            _, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
            r = await env.client.patch(f"{API}/settings/thresholds", json={"comfyui_url": ""})
            assert r.status_code == 200, r.text
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/top-up",
                                      json={"spec": SPEC, "seed": 3, "confirm_total": 4})
            assert r.status_code == 400, r.text
            assert "No ComfyUI URL" in r.json()["detail"]
            assert (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json() == []
    run(go())


def test_after_an_edit_top_up_runs_the_new_folders_not_the_orphans(tmp_path, monkeypatch):
    """Apply and the leaf lookup share one session with expire_on_commit=False: read off a
    cached `bp.leaves`, the lookup still saw the deleted leaves and ran their orphans."""
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
            # Apply only: four pending rows under knights/, none run.
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/apply",
                                      json={"spec": SPEC, "seed": 3, "confirm_total": 4})
            assert r.status_code == 200, r.text
            old_ids = {r["id"] for r in (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json()}

            moved = {**SPEC, "scenarios": [{**SPEC["scenarios"][0], "path": "heroes/{character}"}]}
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/top-up", json={
                "spec": moved, "seed": 3, "confirm_total": 4, "subfolder": "gen",
            })
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["created"] == 4 and out["total"] == 4 and out["job_id"]
            assert (await wait_for_job(env, out["job_id"]))["status"] == "completed"

            rows = (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json()
            assert all(r["status"] == "pending" for r in rows if r["id"] in old_ids)
            assert all(r["status"] == "completed" for r in rows if r["id"] not in old_ids)
            assert {i["subfolder"] for i in await _images(env, ds["id"])} == {
                "gen/heroes/iron_knight", "gen/heroes/salt_witch",
            }
    run(go())


def test_a_dotdot_base_folder_is_refused_before_anything_is_written(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            _, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/top-up", json={
                "spec": SPEC, "seed": 3, "confirm_total": 4, "subfolder": "../escape",
            })
            assert r.status_code == 400, r.text
            assert (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json() == []
    run(go())


def test_a_row_left_running_by_a_hard_kill_is_reset_and_run(tmp_path, monkeypatch):
    async def go():
        async with api_env(tmp_path) as env:
            fake = FakeComfy(canvas_running=False, complete=True)
            ds, plan_id = await _setup(env, monkeypatch, fake, _workflow(("9", "SaveImage")), ["9"])
            r = await env.client.post(f"{API}/comfy/plans/{plan_id}/blueprint/apply",
                                      json={"spec": SPEC, "seed": 3, "confirm_total": 4})
            assert r.status_code == 200, r.text
            async with env.Session() as db:
                row = (await db.execute(select(ComfyRow).where(ComfyRow.plan_id == plan_id))).scalars().first()
                row.status = "running"
                await db.commit()

            out = await _top_up(env, plan_id, 0)
            assert (out["created"], out["reset"], out["total"]) == (0, 1, 4)
            rows = (await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")).json()
            assert all(r["status"] == "completed" for r in rows)
            assert len(await _images(env, ds["id"])) == 4
    run(go())
