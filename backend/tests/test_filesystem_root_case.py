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
from backend.routers import filesystem as fs_router
from backend.tests.conftest import API, api_env, run, upload_image

FS = f"{API}/filesystem"


def _casefold_same(a: str, b: str) -> bool:
    """Stands in for `os.path.samefile`: on Linux the swapcased path is not on disk, so
    the real call would answer False. Case variants count as one file."""
    return a.casefold() == b.casefold()


def _never_same(a: str, b: str) -> bool:
    return False


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
    monkeypatch.setattr(fs_router, "_same_file", _casefold_same)

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
    monkeypatch.setattr(fs_router, "_same_file", _casefold_same)

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


# -- A case drift below the root: one file's stored path differs only in case --
#
# `_flip_root_case` above moves the *root*. Here the stored path of a single file
# differs from the request in the dataset folder and file name, as a folder renamed
# by case outside the app would leave it. The disk is untouched, so the request
# names the real file; only the row's spelling drifts.

async def _respell_file_row(env, ds: dict, image_id: str) -> str:
    """Swapcase everything below the root in the image's stored paths. Returns the real path."""
    root = str(Path(ds["folder_path"]).parent)
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        real = img.file_path
        img.file_path = root + real[len(root):].swapcase()
        img.thumbnail_path = root + img.thumbnail_path[len(root):].swapcase()
        await db.commit()
    return real


def test_deleting_a_file_removes_a_row_stored_in_another_case(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    monkeypatch.setattr(fs_router, "_same_file", _casefold_same)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "pic.png")
            real = await _respell_file_row(env, ds, img["id"])

            r = await env.client.post(f"{FS}/delete", json={"path": real})
            assert r.status_code == 200, r.text
            assert not Path(real).exists()
            async with env.Session() as db:
                assert await db.get(Image, img["id"]) is None

    run(scenario())


def test_deleting_a_file_leaves_a_case_variant_row_where_the_filesystem_is_exact(tmp_path, monkeypatch):
    # PATHS_FOLD_CASE False is Linux behaviour: `PIC.PNG` is another file, so its row
    # is not this file's. The match is a string compare in SQL, so it holds on any host.
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", False)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "pic.png")
            real = await _respell_file_row(env, ds, img["id"])

            r = await env.client.post(f"{FS}/delete", json={"path": real})
            assert r.status_code == 200, r.text
            async with env.Session() as db:
                assert await db.get(Image, img["id"]) is not None

    run(scenario())


def test_renaming_a_file_follows_a_row_stored_in_another_case(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    monkeypatch.setattr(fs_router, "_same_file", _casefold_same)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "pic.png")
            real = await _respell_file_row(env, ds, img["id"])

            r = await env.client.post(f"{FS}/rename", json={"path": real, "new_name": "renamed.png"})
            assert r.status_code == 200, r.text
            new_path = Path(real).parent / "renamed.png"
            assert new_path.exists() and not Path(real).exists()
            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                assert row.filename == "renamed.png"
                assert row.file_path == str(new_path)

    run(scenario())


def test_moving_a_file_refuses_a_row_stored_in_another_case(tmp_path, monkeypatch):
    # A registered file is not the file browser's to re-home (409). Missing the row
    # because of its spelling would let the move through and strand it.
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    monkeypatch.setattr(fs_router, "_same_file", _casefold_same)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            other = await env.create_dataset("e")
            img = await upload_image(env, ds["id"], "pic.png")
            real = await _respell_file_row(env, ds, img["id"])

            r = await env.client.post(
                f"{FS}/move", json={"src": real, "dst_dir": str(Path(other["folder_path"]) / "images")}
            )
            assert r.status_code == 409, r.text
            assert Path(real).exists()

    run(scenario())


# -- Two rows for one folder entry: the exact spelling and a case variant ------

async def _add_variant_row(env, ds: dict, filename: str) -> str:
    """A second Image row whose `file_path` differs from the real file only in case.
    Inserted directly: Windows cannot hold both files on disk, and the row is all
    the test needs."""
    async with env.Session() as db:
        row = Image(
            dataset_id=ds["id"],
            filename=filename,
            original_filename=filename,
            file_path=str(Path(ds["folder_path"]) / "images" / filename),
        )
        db.add(row)
        await db.commit()
        return row.id


def test_renaming_takes_the_exact_row_when_a_case_variant_row_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    monkeypatch.setattr(fs_router, "_same_file", _casefold_same)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            variant_id = await _add_variant_row(env, ds, "A.PNG")
            real = Path(ds["folder_path"]) / "images" / "a.png"

            r = await env.client.post(f"{FS}/rename", json={"path": str(real), "new_name": "b.png"})
            assert r.status_code == 200, r.text   # not a 500 from two matching rows
            async with env.Session() as db:
                assert (await db.get(Image, img["id"])).file_path == str(real.parent / "b.png")
                assert (await db.get(Image, variant_id)).file_path == str(real.parent / "A.PNG")

    run(scenario())


def test_deleting_removes_every_row_that_names_the_same_file(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    monkeypatch.setattr(fs_router, "_same_file", _casefold_same)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            variant_id = await _add_variant_row(env, ds, "A.PNG")
            real = Path(ds["folder_path"]) / "images" / "a.png"

            r = await env.client.post(f"{FS}/delete", json={"path": str(real)})
            assert r.status_code == 200, r.text
            async with env.Session() as db:
                assert await db.get(Image, img["id"]) is None
                assert await db.get(Image, variant_id) is None

    run(scenario())


def test_deleting_leaves_a_variant_row_that_is_another_file(tmp_path, monkeypatch):
    # A case-sensitive directory under PATHS_FOLD_CASE: `A.PNG` is not `a.png`.
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    monkeypatch.setattr(fs_router, "_same_file", _never_same)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            variant_id = await _add_variant_row(env, ds, "A.PNG")
            real = Path(ds["folder_path"]) / "images" / "a.png"

            r = await env.client.post(f"{FS}/delete", json={"path": str(real)})
            assert r.status_code == 200, r.text
            async with env.Session() as db:
                assert await db.get(Image, img["id"]) is None
                assert await db.get(Image, variant_id) is not None

    run(scenario())
