"""File-browser paths are canonicalized once, at entry, into the stored form (GPT-011).

The file-browser routes match a client-supplied path against *stored* strings —
`Image.file_path` for `/delete`'s row scope, `Dataset.folder_path` for
`/rename`'s folder guards. `sanitize_abs_path` returned a bare `Path`, which keeps
`..`, so `{ds}/images/../images` matched no row while `rmtree` resolved it:
the bytes went, the rows stayed (`GET /file` 404), the snapshot backup was
skipped, and a later restore reported nothing to restore.

`utils.canonical_abs_path` resolves, then rebases anything under
`datasets_dir.resolve()` back onto the unresolved `datasets_dir` the stored
strings are built from — so it is also correct when `datasets_dir` itself sits
under a symlink, which plain `.resolve()` would not be.
"""

import os
from pathlib import Path

from backend.models import Image
from backend.services import version_service
from backend.tests.conftest import (
    API,
    api_env,
    needs_symlinks,
    png_bytes,
    run,
    upload_image,
)
from backend.utils import canonical_abs_path


async def _row(env, image_id):
    async with env.Session() as db:
        return await db.get(Image, image_id)


def test_canonical_abs_path_collapses_dotdot_into_the_stored_form(tmp_path):
    root = tmp_path / "datasets"
    (root / "d" / "images").mkdir(parents=True)
    assert canonical_abs_path(f"{root}/d/images/../images", root) == root / "d" / "images"
    assert canonical_abs_path(str(root), root) == root


@needs_symlinks
def test_canonical_abs_path_rebases_through_a_symlinked_root(tmp_path):
    real = tmp_path / "real"
    (real / "datasets" / "d").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    root = link / "datasets"
    # The stored form survives, and the resolved spelling maps back onto it.
    assert canonical_abs_path(str(root / "d"), root) == root / "d"
    assert canonical_abs_path(str(real / "datasets" / "d"), root) == root / "d"


@needs_symlinks
def test_canonical_abs_path_keeps_a_final_symlink(tmp_path):
    root = tmp_path / "datasets"
    root.mkdir()
    target = root / "a.png"
    target.write_bytes(b"x")
    link = root / "link.png"
    link.symlink_to(target)
    assert canonical_abs_path(str(link), root) == link


async def _auto(env):
    r = await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "auto"})
    assert r.status_code == 200, r.text


def test_an_aliased_folder_delete_removes_the_rows_and_stays_restorable(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            await _auto(env)
            ds = await env.create_dataset("alias")
            img = await upload_image(env, ds["id"], "a.png", png_bytes((1, 2, 3)))
            fp = Path((await _row(env, img["id"])).file_path)
            async with env.Session() as db:
                snap = await version_service.create_snapshot(db, ds["id"], "s1", "")

            r = await env.client.post(
                f"{API}/filesystem/delete", json={"path": f"{fp.parent}/../images"}
            )
            assert r.status_code == 200, r.text
            assert not fp.exists()
            assert await _row(env, img["id"]) is None

            async with env.Session() as db:
                res = await version_service.restore_snapshot(
                    db, ds["id"], snap.id, pre_restore_snapshot=False
                )
            assert res["files_restored"] == 1, res
            assert fp.exists()

    run(scenario())


def test_an_aliased_file_delete_removes_its_row(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            fp = Path((await _row(env, img["id"])).file_path)

            r = await env.client.post(
                f"{API}/filesystem/delete", json={"path": f"{fp.parent}/../images/{fp.name}"}
            )
            assert r.status_code == 200, r.text
            assert not fp.exists()
            assert await _row(env, img["id"]) is None

    run(scenario())


def test_an_aliased_rename_of_a_structural_folder_is_refused(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            images_dir = Path((await _row(env, img["id"])).file_path).parent

            canonical = await env.client.post(
                f"{API}/filesystem/rename", json={"path": str(images_dir), "new_name": "pics"}
            )
            aliased = await env.client.post(
                f"{API}/filesystem/rename",
                json={"path": f"{images_dir}/../images", "new_name": "pics"},
            )
            assert canonical.status_code >= 400
            assert aliased.status_code == canonical.status_code, aliased.text
            assert images_dir.is_dir()
            assert (await env.client.get(f"{API}/images/{img['id']}/file")).status_code == 200

    run(scenario())


@needs_symlinks
def test_deleting_a_symlink_removes_the_link_not_its_target(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            fp = Path((await _row(env, img["id"])).file_path)
            link = fp.parent.parent / "link.png"
            link.symlink_to(fp)

            r = await env.client.post(f"{API}/filesystem/delete", json={"path": str(link)})
            assert r.status_code == 200, r.text
            assert not os.path.lexists(link)
            assert fp.exists()
            assert await _row(env, img["id"]) is not None

    run(scenario())


@needs_symlinks
def test_a_symlinked_datasets_dir_still_matches_rows(tmp_path):
    """Both spellings — the stored one and the resolved one — find the row."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    async def scenario():
        async with api_env(link) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes((1, 1, 1)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes((2, 2, 2)))
            fa = Path((await _row(env, a["id"])).file_path)
            fb = Path((await _row(env, b["id"])).file_path)
            assert str(fa).startswith(str(link))

            r = await env.client.post(f"{API}/filesystem/delete", json={"path": str(fa)})
            assert r.status_code == 200, r.text
            assert await _row(env, a["id"]) is None

            resolved = fb.resolve()
            assert resolved != fb
            r = await env.client.post(f"{API}/filesystem/delete", json={"path": str(resolved)})
            assert r.status_code == 200, r.text
            assert await _row(env, b["id"]) is None

    run(scenario())


@needs_symlinks
def test_canonical_abs_path_keeps_a_final_windows_junction(tmp_path, monkeypatch):
    """A junction is not a symlink to `is_symlink()` but `resolve()` follows it,
    so it must be detected on its own or a delete of the link would act on the
    target. Simulated with a symlink that reports itself only as a junction."""
    import pathlib

    root = tmp_path / "datasets"
    target = root / "real"
    target.mkdir(parents=True)
    junction = root / "junction"
    junction.symlink_to(target, target_is_directory=True)

    real_is_symlink = pathlib.Path.is_symlink
    monkeypatch.setattr(pathlib.Path, "is_symlink", lambda self: False if self == junction else real_is_symlink(self))
    monkeypatch.setattr(pathlib.Path, "is_junction", lambda self: self == junction, raising=False)
    assert canonical_abs_path(str(junction), root) == junction


@needs_symlinks
def test_canonical_abs_path_keeps_the_spelling_through_a_symlinked_dataset_folder(tmp_path):
    """A dataset folder kept on another drive is a link *inside* the tree. Its
    stored paths run through the link, so resolving it out of `root` would match
    no row while `rmtree` deleted the bytes behind it."""
    root = tmp_path / "datasets"
    root.mkdir()
    drive = tmp_path / "other_drive" / "d"
    (drive / "images").mkdir(parents=True)
    (root / "d").symlink_to(drive, target_is_directory=True)
    assert canonical_abs_path(str(root / "d" / "images"), root) == root / "d" / "images"
    assert canonical_abs_path(str(root / "d" / "images" / "a.png"), root) == root / "d" / "images" / "a.png"


@needs_symlinks
def test_deleting_inside_a_symlinked_dataset_folder_removes_the_rows(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("linked")
            folder = Path(ds["folder_path"])
            drive = tmp_path / "other_drive" / folder.name
            drive.parent.mkdir()
            folder.rename(drive)
            folder.symlink_to(drive, target_is_directory=True)

            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            fp = Path((await _row(env, img["id"])).file_path)
            assert str(fp).startswith(str(folder))  # stored through the link

            r = await env.client.post(f"{API}/filesystem/delete", json={"path": str(fp.parent)})
            assert r.status_code == 200, r.text
            assert not (drive / "images" / fp.name).exists()
            assert await _row(env, img["id"]) is None

    run(scenario())
