"""The structural `/datasets/{id}` routes honour the dataset-busy guard (GPT-015).

A versioning job (restore, checkout, snapshot, branch-init, prune) holds
`dataset_busy.busy(id, ...)` for its whole run. A dataset rename landing inside
that window moves the folder and rebases the live paths while the job is still
writing to the old ones — the race that feeds GPT-021 — and a dataset delete
pulls the rows and folder out from under the job entirely. Both used to answer
200/204. Subfolder delete rewrites `Image.subfolder`, a column a restore also
writes, so it takes the same guard as the subfolder repath endpoint.

A category- or provenance-only PATCH must keep working while busy: DatasetsPage
sends those in bulk, and nothing on disk moves.
"""

from pathlib import Path

from backend.models import Dataset, Image
from backend.services.dataset_busy import busy
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


def test_structural_dataset_routes_refuse_while_busy(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "x.png", png_bytes((1, 2, 3)))
            r = await env.client.post(f"{API}/datasets/{ds['id']}/subfolders", json={"path": "sub"})
            assert r.status_code == 201, r.text
            folder = Path(ds["folder_path"])

            with busy(ds["id"], "restore"):
                # Control: an already-guarded endpoint.
                r = await env.client.delete(f"{API}/images/{img['id']}")
                assert r.status_code == 409

                r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"name": "renamed"})
                assert r.status_code == 409, r.text
                assert folder.is_dir()
                assert not (folder.parent / "renamed").exists()

                r = await env.client.delete(f"{API}/datasets/{ds['id']}")
                assert r.status_code == 409, r.text
                assert folder.is_dir()

                r = await env.client.delete(
                    f"{API}/datasets/{ds['id']}/subfolders", params={"path": "sub"}
                )
                assert r.status_code == 409, r.text

                # Category-only PATCH: nothing structural, stays allowed.
                r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"category": "cat"})
                assert r.status_code == 200, r.text
                assert r.json()["category"] == "cat"

            async with env.Session() as db:
                row = await db.get(Dataset, ds["id"])
                assert row is not None and row.name == "d"
                assert row.folder_path == str(folder)
                assert "sub" in (row.declared_subfolders or [])
                assert await db.get(Image, img["id"]) is not None

            # Released: the same rename goes through.
            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"name": "renamed"})
            assert r.status_code == 200, r.text
            assert Path(r.json()["folder_path"]).is_dir()
            assert not folder.exists()

    run(scenario())
