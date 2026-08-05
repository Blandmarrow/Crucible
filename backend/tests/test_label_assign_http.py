"""`POST /labels/assign` — the single attach/detach endpoint.

One endpoint serves the detail panel (1 image), a hotkey (1 image) and the
gallery toolbar (up to `SELECT_ALL_ID_CAP`), so its whole contract is here:
idempotency from the unique constraint rather than a read-then-write, an honest
"newly added" count from `rowcount`, a **400** (not a 500) for an unknown label
id, and silent skipping of unknown *image* ids, because a selection is a
client-side set that can go stale and `useSelectionStore` spans datasets.
"""
from backend.models import Image, Label
from backend.routers import images as images_router
from backend.tests.conftest import API, api_env, run


async def _seed(env, dataset_id: str, n: int) -> list[str]:
    async with env.Session() as db:
        rows = [
            Image(
                dataset_id=dataset_id,
                filename=f"i{i:04d}.png",
                original_filename=f"i{i:04d}.png",
                file_path=f"/tmp/{dataset_id}/i{i:04d}.png",
            )
            for i in range(n)
        ]
        db.add_all(rows)
        await db.commit()
        return [r.id for r in rows]


async def _label(env, name: str) -> str:
    r = await env.client.post(f"{API}/labels/", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_double_assign_inserts_one_row(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], 3)
            fx = await _label(env, "fx")

            r = await env.client.post(f"{API}/labels/assign", json={"image_ids": ids, "add": [fx]})
            assert r.json() == {"images": 3, "added": 3, "removed": 0}

            # ON CONFLICT DO NOTHING, so the second call adds nothing and does
            # not raise — and `rowcount` says so honestly.
            r = await env.client.post(f"{API}/labels/assign", json={"image_ids": ids, "add": [fx]})
            assert r.json() == {"images": 3, "added": 0, "removed": 0}

            assert (await env.client.get(f"{API}/labels/")).json()[0]["usage_count"] == 3

    run(scenario())


def test_add_and_remove_in_one_call(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], 2)
            fx = await _label(env, "fx")
            reject = await _label(env, "reject")

            await env.client.post(f"{API}/labels/assign", json={"image_ids": ids, "add": [fx]})
            r = await env.client.post(
                f"{API}/labels/assign",
                json={"image_ids": ids, "add": [reject], "remove": [fx]},
            )
            assert r.json() == {"images": 2, "added": 2, "removed": 2}

            detail = (await env.client.get(f"{API}/images/{ids[0]}")).json()
            assert detail["label_ids"] == [reject]

    run(scenario())


def test_overlapping_add_and_remove_is_400(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], 1)
            fx = await _label(env, "fx")
            r = await env.client.post(
                f"{API}/labels/assign", json={"image_ids": ids, "add": [fx], "remove": [fx]}
            )
            assert r.status_code == 400, r.text

    run(scenario())


def test_unknown_label_id_is_400_not_500(tmp_path):
    """FK enforcement is on in the app, so an unvalidated bad id would be an
    IntegrityError → 500. The router checks the ids first."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], 1)
            r = await env.client.post(
                f"{API}/labels/assign", json={"image_ids": ids, "add": ["no-such-label"]}
            )
            assert r.status_code == 400, f"{r.status_code} {r.text}"
            # …and on the remove side too, where no insert would even happen.
            r = await env.client.post(
                f"{API}/labels/assign", json={"image_ids": ids, "remove": ["no-such-label"]}
            )
            assert r.status_code == 400, r.text

    run(scenario())


def test_unknown_image_ids_are_skipped_silently(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], 2)
            fx = await _label(env, "fx")
            r = await env.client.post(
                f"{API}/labels/assign",
                json={"image_ids": [*ids, "gone-1", "gone-2"], "add": [fx]},
            )
            assert r.status_code == 200, r.text
            # `images` reports what actually matched, so the toast is honest.
            assert r.json() == {"images": 2, "added": 2, "removed": 0}

    run(scenario())


def test_empty_bodies_are_400(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], 1)
            fx = await _label(env, "fx")
            assert (await env.client.post(
                f"{API}/labels/assign", json={"image_ids": [], "add": [fx]}
            )).status_code == 400
            assert (await env.client.post(
                f"{API}/labels/assign", json={"image_ids": ids}
            )).status_code == 400

    run(scenario())


def test_over_the_select_all_cap_is_400(tmp_path, monkeypatch):
    """The same bound `GET /images/ids` hands out, so the toolbar can never
    build a body this refuses."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], 5)
            fx = await _label(env, "fx")
            monkeypatch.setattr(images_router, "SELECT_ALL_ID_CAP", 4)
            r = await env.client.post(f"{API}/labels/assign", json={"image_ids": ids, "add": [fx]})
            assert r.status_code == 400, r.text
            monkeypatch.setattr(images_router, "SELECT_ALL_ID_CAP", 5)
            assert (await env.client.post(
                f"{API}/labels/assign", json={"image_ids": ids, "add": [fx]}
            )).status_code == 200

    run(scenario())


def test_a_600_image_assign_crosses_a_chunk_boundary(tmp_path, monkeypatch):
    """Chunking is on the **row** count (images × labels), not the image count.

    The real ceiling is SQLite's 32,766 binds; the constant is shrunk here so 600
    images × 2 labels crosses several statement boundaries without seeding 20k
    rows.
    """
    async def scenario():
        from backend.routers import labels as labels_router

        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"], 600)
            fx = await _label(env, "fx")
            reject = await _label(env, "reject")
            monkeypatch.setattr(labels_router, "ROWS_PER_STATEMENT", 101)

            r = await env.client.post(
                f"{API}/labels/assign", json={"image_ids": ids, "add": [fx, reject]}
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"images": 600, "added": 1200, "removed": 0}

            r = await env.client.post(
                f"{API}/labels/assign", json={"image_ids": ids, "remove": [fx]}
            )
            assert r.json()["removed"] == 600

            async with env.Session() as db:
                from sqlalchemy import func, select

                from backend.models import ImageLabel

                total = (await db.execute(select(func.count(ImageLabel.id)))).scalar_one()
                assert total == 600
                assert (await db.execute(select(Label.id).where(Label.id == reject))).scalar_one()

    run(scenario())
