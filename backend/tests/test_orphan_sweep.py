"""The startup orphan sweep, and the two cases where it must remove nothing.

`sweep_orphan_dataset_folders` deletes every folder under datasets_dir that no
dataset row names. That is right for a folder a lost row left behind, and a disaster
when the DB and the tree do not belong together: a database copied from another
machine (its rows name that machine's paths) or a datasets folder on a network share
that is not mounted. Then every folder looks orphaned. These tests pin the refusal.
"""
import os
from pathlib import Path

from backend import utils
from backend.models import Dataset
from backend.services.dataset_service import sweep_orphan_dataset_folders
from backend.tests.conftest import api_env, run


def _folder(root: Path, name: str) -> Path:
    path = root / name
    (path / "images").mkdir(parents=True)
    (path / "images" / "a.png").write_bytes(b"not really a png")
    return path


async def _add(env, name: str, folder: str) -> None:
    async with env.Session() as db:
        db.add(Dataset(name=name, folder_path=folder))
        await db.commit()


async def _sweep(env) -> list[str]:
    async with env.Session() as db:
        return await sweep_orphan_dataset_folders(db)


def test_a_folder_with_no_row_is_removed_and_a_named_one_kept(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            kept = _folder(env.datasets_dir, "kept")
            orphan = _folder(env.datasets_dir, "orphan")
            await _add(env, "kept", str(kept))

            assert await _sweep(env) == ["orphan"]
            assert kept.is_dir()
            assert not orphan.exists()

    run(go())


def test_a_row_outside_the_datasets_folder_stops_the_sweep(tmp_path):
    """A DB moved from another machine names that machine's paths, outside this folder."""
    async def go():
        async with api_env(tmp_path) as env:
            copied = _folder(env.datasets_dir, "cats")
            await _add(env, "cats", str(tmp_path / "elsewhere" / "cats"))

            assert await _sweep(env) == []
            assert (copied / "images" / "a.png").is_file()

    run(go())


def test_rows_whose_folders_are_all_missing_stop_the_sweep(tmp_path):
    """An unmounted share: the rows are right, the tree under them is not there."""
    async def go():
        async with api_env(tmp_path) as env:
            stray = _folder(env.datasets_dir, "stray")
            await _add(env, "a", str(env.datasets_dir / "a"))
            await _add(env, "b", str(env.datasets_dir / "b"))

            assert await _sweep(env) == []
            assert stray.is_dir()

    run(go())


def test_one_missing_folder_among_present_ones_does_not_stop_the_sweep(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            present = _folder(env.datasets_dir, "present")
            orphan = _folder(env.datasets_dir, "orphan")
            await _add(env, "present", str(present))
            await _add(env, "gone", str(env.datasets_dir / "gone"))

            assert await _sweep(env) == ["orphan"]
            assert present.is_dir()
            assert not orphan.exists()

    run(go())


def test_a_path_written_on_the_other_os_stops_the_sweep(tmp_path):
    """Not absolute on this OS, so resolve() would join it to the working directory —
    which could land inside the datasets folder. It must count as outside regardless."""
    foreign = "/mnt/nas/crucible/datasets/cats" if os.name == "nt" else "D:\\Crucible\\data\\datasets\\cats"

    async def go():
        async with api_env(tmp_path) as env:
            copied = _folder(env.datasets_dir, "cats")
            await _add(env, "cats", foreign)

            assert await _sweep(env) == []
            assert copied.is_dir()

    run(go())


def test_an_empty_database_beside_dataset_folders_removes_nothing(tmp_path):
    """A second install handed another one's DATASETS_DIR starts with no rows: every
    folder there is somebody's dataset, not an orphan."""
    async def go():
        async with api_env(tmp_path) as env:
            theirs = _folder(env.datasets_dir, "theirs")
            assert await _sweep(env) == []
            assert theirs.is_dir()

    run(go())


def test_an_empty_database_still_clears_folders_that_hold_no_dataset(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            stray = env.datasets_dir / "stray"
            stray.mkdir()
            assert await _sweep(env) == ["stray"]
            assert not stray.exists()

    run(go())


def test_a_row_naming_a_folder_in_another_case_keeps_it(tmp_path, monkeypatch):
    """A dataset folder renamed by case outside the app (`Mixed` on disk, `mixed` in the
    row). Where the filesystem folds case the row still names it; swept, a live dataset
    would be rmtree'd. The exact `kept` row satisfies the "folders exist" guard, so the
    case fold is what decides. On Linux `mixed` is not on disk and the fold is forced."""
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)

    async def go():
        async with api_env(tmp_path) as env:
            kept = _folder(env.datasets_dir, "kept")
            mixed = _folder(env.datasets_dir, "Mixed")
            orphan = _folder(env.datasets_dir, "orphan")
            await _add(env, "kept", str(kept))
            await _add(env, "mixed", str(env.datasets_dir / "mixed"))

            assert await _sweep(env) == ["orphan"]
            assert mixed.is_dir()
            assert not orphan.exists()

    run(go())
