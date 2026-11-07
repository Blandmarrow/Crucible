"""The File Browser's prefix tests must survive a change in the root's spelling.

Every stored path begins with `settings.datasets_dir`, and on Windows that spelling
follows the launch folder's typed case (`c:\\crucible` vs `C:\\Crucible`) or a `.env`
edit, while `canonical_abs_path` rebases each request onto the *current* spelling.
So rows written under one launch must still match the next launch's requests.

PM-034's first cut made these prefix tests exact everywhere. A review caught the
regression: with the root's case flipped in the stored rows, renaming a dataset's
own folder returned 200 (stranding the dataset), and deleting `{ds}/images` removed
the bytes while the rows, and their versioning backup, stayed behind — PM-014's
shape. `utils.sql_has_path_prefix` now folds case where the filesystem does.

Both tests force `PATHS_FOLD_CASE = True`, so Linux CI runs the Windows behaviour
too; the guards they exercise are DB queries, which do not need the filesystem to
agree. The Linux half (exact, so `MY_DS/` cannot take `my_ds/`'s rows) is
`test_filesystem_delete_http.py::test_a_case_variant_folder_does_not_delete_a_datasets_rows`.
"""
from pathlib import Path

from backend import utils
from backend.models import Dataset, Image
from backend.tests.conftest import API, api_env, run, upload_image

FS = f"{API}/filesystem"


async def _flip_root_case(env, ds: dict, image_id: str) -> None:
    """Rewrite the stored rows as an earlier launch with another root spelling would have."""
    root = str(Path(ds["folder_path"]).parent)
    other = root.swapcase()
    assert other != root
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        img.file_path = img.file_path.replace(root, other)
        img.thumbnail_path = img.thumbnail_path.replace(root, other)
        row = await db.get(Dataset, ds["id"])
        row.folder_path = row.folder_path.replace(root, other)
        await db.commit()


def test_renaming_a_datasets_folder_is_refused_under_another_root_spelling(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "pic.png")
            await _flip_root_case(env, ds, img["id"])

            r = await env.client.post(f"{FS}/rename", json={"path": ds["folder_path"], "new_name": "zzz"})
            assert r.status_code == 409, r.text
            assert Path(ds["folder_path"]).is_dir()

    run(scenario())


def test_deleting_a_folder_removes_rows_stored_under_another_root_spelling(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "pic.png")
            await _flip_root_case(env, ds, img["id"])

            images_dir = Path(ds["folder_path"]) / "images"
            r = await env.client.post(f"{FS}/delete", json={"path": str(images_dir)})
            assert r.status_code == 200, r.text
            assert not images_dir.exists()
            async with env.Session() as db:
                assert await db.get(Image, img["id"]) is None

    run(scenario())
