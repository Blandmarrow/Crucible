"""Dataset notes, app-wide notes, dismissals and the dataset goal fields (M12 phase 1)."""
from sqlalchemy import func, select

from backend.models import Dataset
from backend.models.notes import DatasetNoteItem, NoteItemHistory
from backend.schemas.dataset import DatasetOut
from backend.services.notes_service import APP_NOTES_CAP_CHARS, DATASET_NOTES_CAP_CHARS
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


async def _history_ops(env, item_id: str) -> list[str]:
    async with env.Session() as db:
        rows = (await db.execute(
            select(NoteItemHistory.op)
            .where((NoteItemHistory.dataset_item_id == item_id) | (NoteItemHistory.app_item_id == item_id))
            .order_by(NoteItemHistory.id)
        )).scalars().all()
    return list(rows)


def test_dataset_notes_crud_history_and_order(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("n")
            base = f"{API}/datasets/{ds['id']}/notes"
            a = (await env.client.post(base, json={"section": "preference", "text": "Trigger word zxc first."})).json()
            b = (await env.client.post(base, json={"section": "preference", "text": "The grain is the style."})).json()
            d = (await env.client.post(base, json={"section": "decision", "text": "Dropped 30 blurry.",
                                                   "decided_at": "2026-11-18T10:00:00"})).json()
            assert (a["position"], b["position"], d["position"]) == (0, 1, 0)
            assert d["decided_at"].startswith("2026-11-18")

            r = await env.client.patch(f"{base}/{a['id']}", json={"text": "Trigger word zxc comes first."})
            assert r.status_code == 200 and r.json()["text"] == "Trigger word zxc comes first."
            # Move b to the top of its section.
            await env.client.patch(f"{base}/{b['id']}", json={"position": 0})
            prefs = [i for i in (await env.client.get(base)).json()["items"] if i["section"] == "preference"]
            assert [i["id"] for i in prefs] == [b["id"], a["id"]]

            r = await env.client.delete(f"{base}/{a['id']}")
            assert r.status_code == 200 and r.json()["deleted_at"]
            assert a["id"] not in {i["id"] for i in (await env.client.get(base)).json()["items"]}
            listed = (await env.client.get(base, params={"include_deleted": True})).json()["items"]
            assert a["id"] in {i["id"] for i in listed}
            assert (await env.client.patch(f"{base}/{a['id']}", json={"text": "x"})).status_code == 409
            r = await env.client.post(f"{base}/{a['id']}/restore")
            assert r.status_code == 200 and r.json()["deleted_at"] is None

            assert await _history_ops(env, a["id"]) == ["add", "edit", "remove", "restore"]

            # Bad section, empty text, another dataset's note.
            assert (await env.client.post(base, json={"section": "about_me", "text": "x"})).status_code == 422
            assert (await env.client.post(base, json={"section": "decision", "text": "   "})).status_code == 422
            other = await env.create_dataset("other")
            r = await env.client.patch(f"{API}/datasets/{other['id']}/notes/{a['id']}", json={"text": "y"})
            assert r.status_code == 404

    run(scenario())


def test_size_cap_per_scope(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("cap")
            base = f"{API}/datasets/{ds['id']}/notes"
            chunk = "x" * 2000
            assert (await env.client.post(base, json={"section": "open_item", "text": chunk})).status_code == 201
            assert (await env.client.post(base, json={"section": "open_item", "text": chunk})).status_code == 201
            r = await env.client.post(base, json={"section": "open_item", "text": "one more"})
            assert r.status_code == 422 and str(DATASET_NOTES_CAP_CHARS) in r.text
            notes = (await env.client.get(base)).json()
            assert notes["used_chars"] == 4000 and notes["cap_chars"] == DATASET_NOTES_CAP_CHARS

            r = await env.client.post(f"{API}/app-notes", json={"text": "y" * APP_NOTES_CAP_CHARS})
            assert r.status_code == 201
            assert (await env.client.post(f"{API}/app-notes", json={"text": "z"})).status_code == 422
            # Removing frees the room; restoring past the cap is refused.
            item_id = r.json()["id"]
            await env.client.delete(f"{API}/app-notes/{item_id}")
            assert (await env.client.post(f"{API}/app-notes", json={"text": "z"})).status_code == 201
            assert (await env.client.post(f"{API}/app-notes/{item_id}/restore")).status_code == 422

    run(scenario())


def test_notes_cascade_with_their_dataset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("gone")
            await env.client.post(f"{API}/datasets/{ds['id']}/notes", json={"section": "preference", "text": "keep"})
            assert (await env.client.delete(f"{API}/datasets/{ds['id']}")).status_code == 204
            async with env.Session() as db:
                assert await db.scalar(select(func.count()).select_from(DatasetNoteItem)) == 0
                assert await db.scalar(select(func.count()).select_from(NoteItemHistory)) == 0

    run(scenario())


def test_dismissal_resurfaces_when_the_count_more_than_doubles(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("dismiss")
            await env.client.patch(f"{API}/datasets/{ds['id']}", json={"goal": "x"})
            await upload_image(env, ds["id"], "0.png", png_bytes((1, 2, 3)))
            url = f"{API}/datasets/{ds['id']}/health"

            r = await env.client.post(f"{url}/dismiss", json={"finding_id": "uncaptioned", "reason": "captions later"})
            assert r.status_code == 201, r.text
            note = r.json()
            assert note["section"] == "preference" and note["finding_id"] == "uncaptioned"
            assert note["text"] == "captions later"
            report = (await env.client.get(url)).json()
            dismissed = {f["id"]: f for f in report["dismissed"]}
            assert dismissed["uncaptioned"]["dismissal"]["count_then"] == 1

            # 2 is not more than double 1; 3 is.
            await upload_image(env, ds["id"], "1.png", png_bytes((4, 5, 6)))
            assert "uncaptioned" in {f["id"] for f in (await env.client.get(url)).json()["dismissed"]}
            await upload_image(env, ds["id"], "2.png", png_bytes((7, 8, 9)))
            report = (await env.client.get(url)).json()
            back = {f["id"]: f for f in report["findings"]}["uncaptioned"]
            assert back["resurfaced"] is True
            assert back["dismissal"]["reason"] == "captions later"

            # A permanent dismissal replaces the old one and stays hidden.
            r = await env.client.post(f"{url}/dismiss", json={"finding_id": "uncaptioned", "permanent": True})
            assert r.json()["text"].startswith("Dismissed the health finding")
            for i in range(3, 9):
                await upload_image(env, ds["id"], f"{i}.png", png_bytes((10 + i, 8, 9)))
            report = (await env.client.get(url)).json()
            assert "uncaptioned" in {f["id"] for f in report["dismissed"]}
            live = [i for i in (await env.client.get(f"{API}/datasets/{ds['id']}/notes")).json()["items"]
                    if i["finding_id"] == "uncaptioned"]
            assert len(live) == 1 and live[0]["permanent"] is True

            r = await env.client.post(f"{url}/dismiss", json={"finding_id": "no_such_finding"})
            assert r.status_code == 404

    run(scenario())


def test_list_datasets_fills_every_dataset_out_field(tmp_path):
    """`list_datasets` builds `DatasetOut` field by field, so a field it forgets
    silently reads as its default. Set every field to a non-default and compare."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("full")
            await env.client.patch(f"{API}/datasets/{ds['id']}", json={
                "goal": "a goal", "target_resolution": 768, "category": "cat", "description": "d",
                "source_name": "s", "source_url": "https://example.com", "license": "cc0-1.0", "attribution": "a",
            })
            await upload_image(env, ds["id"], "a.png", png_bytes())
            async with env.Session() as db:
                row = await db.get(Dataset, ds["id"])
                row.video_count, row.video_size_bytes = 2, 99
                row.current_branch_id = "branch-x"
                row.health_summary = {"error": 0, "warn": 1, "info": 2, "computed_at": "t"}
                await db.commit()
            single = (await env.client.get(f"{API}/datasets/{ds['id']}")).json()
            listed = next(d for d in (await env.client.get(f"{API}/datasets/")).json() if d["id"] == ds["id"])
            for field, info in DatasetOut.model_fields.items():
                if field == "preview_image_ids":
                    assert listed[field], field
                    continue
                assert listed[field] == single[field], field
                assert listed[field] != info.default, f"{field} still reads its default"

    run(scenario())


def test_duplicate_carries_the_goal_but_not_the_notes(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("src")
            await env.client.patch(f"{API}/datasets/{ds['id']}", json={"goal": "watercolour LoRA", "target_resolution": 1024})
            await env.client.post(f"{API}/datasets/{ds['id']}/notes", json={"section": "preference", "text": "keep"})
            r = await env.client.post(f"{API}/datasets/{ds['id']}/duplicate", json={"new_name": "copy"})
            assert r.status_code in (200, 202), r.text
            if "job_id" in r.json():
                assert (await wait_for_job(env, r.json()["job_id"], timeout=60))["status"] == "completed"
            copy = next(d for d in (await env.client.get(f"{API}/datasets/")).json() if d["name"] == "copy")
            assert copy["goal"] == "watercolour LoRA" and copy["target_resolution"] == 1024
            assert (await env.client.get(f"{API}/datasets/{copy['id']}/notes")).json()["items"] == []

    run(scenario())


def test_create_takes_the_goal(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{API}/datasets/", json={"name": "g", "goal": "concept", "target_resolution": 512})
            assert r.status_code == 201, r.text
            assert r.json()["goal"] == "concept" and r.json()["target_resolution"] == 512
            r = await env.client.post(f"{API}/datasets/", json={"name": "h", "goal": "x" * 2001})
            assert r.status_code == 422

    run(scenario())


def test_decided_at_only_on_decisions_and_stored_as_utc(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("dates")
            base = f"{API}/datasets/{ds['id']}/notes"
            d = (await env.client.post(base, json={"section": "decision", "text": "x",
                                                   "decided_at": "2026-11-18T12:00:00+02:00"})).json()
            assert d["decided_at"].startswith("2026-11-18T10:00:00")
            p = (await env.client.post(base, json={"section": "preference", "text": "y",
                                                   "decided_at": "2026-11-18T12:00:00"})).json()
            assert p["decided_at"] is None
            r = await env.client.patch(f"{base}/{p['id']}", json={"decided_at": "2026-11-18T12:00:00"})
            assert r.json()["decided_at"] is None
            r = await env.client.patch(f"{base}/{d['id']}", json={"decided_at": None})
            assert r.json()["decided_at"] is None

    run(scenario())


def test_an_old_dismissal_cannot_be_restored_beside_a_newer_one(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("two")
            await upload_image(env, ds["id"], "a.png", png_bytes())
            url = f"{API}/datasets/{ds['id']}/health/dismiss"
            old = (await env.client.post(url, json={"finding_id": "uncaptioned", "reason": "first"})).json()
            await env.client.post(url, json={"finding_id": "uncaptioned", "reason": "second"})
            r = await env.client.post(f"{API}/datasets/{ds['id']}/notes/{old['id']}/restore")
            assert r.status_code == 409

    run(scenario())


def test_a_blank_goal_is_stored_as_none(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("blank")
            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"goal": "   "})
            assert r.json()["goal"] == ""

    run(scenario())
