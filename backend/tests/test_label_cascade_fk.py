"""`image_labels` rows go when their image or label goes — via the **DB cascade**.

Every scenario here opens `api_env(tmp_path, foreign_keys=True)`. That is not a
detail: the harness defaults `PRAGMA foreign_keys` **OFF** (conftest builds the
schema with `create_all` on its own engine, so it never gets the pragma
`backend/database.py` installs on the app engine), and without the opt-in every
assertion below would pass *vacuously* — the orphan rows would simply still be
there and nothing would have proved the cascade exists.

The cascade is load-bearing rather than a nicety, because three of the four
deletion paths never load an `ImageLabel`:

- `delete_image` deletes the ORM object, but there is no `Image.labels`
  relationship to cascade through (deliberately — a lazy load on an async session
  raises `MissingGreenlet` only on the live path).
- `batch_delete` and `bulk_delete_filtered` issue a Core bulk `delete(Image)`,
  which has no ORM cascade at all.
- `DELETE /labels/{id}` deletes the `Label` row alone.
"""
from sqlalchemy import func, select

from backend.models import Image, ImageLabel
from backend.tests.conftest import API, api_env, run


async def _seed(env, dataset_id: str, n: int = 3) -> list[str]:
    async with env.Session() as db:
        rows = [
            Image(
                dataset_id=dataset_id,
                filename=f"i{i}.png",
                original_filename=f"i{i}.png",
                file_path=str(env.datasets_dir / dataset_id / "images" / f"i{i}.png"),
                caption_text="a caption" if i == 0 else "",
            )
            for i in range(n)
        ]
        db.add_all(rows)
        await db.commit()
        return [r.id for r in rows]


async def _join_count(env) -> int:
    async with env.Session() as db:
        return (await db.execute(select(func.count(ImageLabel.id)))).scalar_one()


async def _label_and_attach(env, ids: list[str], name: str = "fx") -> str:
    r = await env.client.post(f"{API}/labels/", json={"name": name})
    label_id = r.json()["id"]
    assert (await env.client.post(
        f"{API}/labels/assign", json={"image_ids": ids, "add": [label_id]}
    )).status_code == 200
    return label_id


def test_delete_image_takes_its_labels(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"])
            await _label_and_attach(env, ids)
            assert await _join_count(env) == 3

            assert (await env.client.delete(f"{API}/images/{ids[0]}")).status_code == 204
            assert await _join_count(env) == 2

    run(scenario())


def test_batch_delete_takes_their_labels(tmp_path):
    """Core bulk `delete(Image)` — no ORM cascade, entirely the DB's FK."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"])
            await _label_and_attach(env, ids)

            r = await env.client.request(
                "DELETE", f"{API}/images/batch/delete", json=ids[:2]
            )
            assert r.status_code == 204, r.text
            assert await _join_count(env) == 1

    run(scenario())


def test_bulk_delete_filtered_takes_their_labels(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"])
            await _label_and_attach(env, ids)

            # Scoped by a filter rather than by an id list — the other Core
            # bulk path. `image_ids` narrows the scope here; the point is the
            # `delete(Image)` statement it runs, not how the scope was built.
            r = await env.client.post(
                f"{API}/images/bulk-delete",
                json={"dataset_id": ds["id"], "image_ids": ids[:2]},
            )
            assert r.status_code == 200, r.text
            assert r.json()["deleted"] == 2
            assert await _join_count(env) == 1

    run(scenario())


def test_deleting_a_label_takes_its_assignments(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            ids = await _seed(env, ds["id"])
            label_id = await _label_and_attach(env, ids)
            assert await _join_count(env) == 3

            assert (await env.client.delete(f"{API}/labels/{label_id}")).status_code == 204
            assert await _join_count(env) == 0
            # The images themselves survive, obviously.
            assert len((await env.client.get(
                f"{API}/images/", params={"dataset_id": ds["id"]}
            )).json()) == 3

    run(scenario())
