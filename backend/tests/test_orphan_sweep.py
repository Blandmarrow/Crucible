"""The startup orphan sweep: it moves data aside and never deletes it, and it touches
nothing when the DB and the tree do not belong together.

`sweep_orphan_dataset_folders` moves every folder under datasets_dir that no dataset
row names into `datasets_dir/.orphaned/<name>-<UTC stamp>`, and deletes one only when
its tree holds no file. A folder whose row is missing is not always one a lost row
left behind: restoring an older DB backup leaves every newer dataset without a row, so
deleting it lost that dataset for good (M9 stage 2). And when the DB and the tree do
not belong together at all (a database copied from another machine, a datasets folder
on a network share that is not mounted) every folder looks orphaned; the sweep then
refuses outright.
"""
import os
from pathlib import Path

import pytest

from backend import utils
from backend.models import Dataset
from backend.services import dataset_service
from backend.services.dataset_service import ORPHANED_DIRNAME, sweep_orphan_dataset_folders
from backend.tests.conftest import api_env, needs_symlinks, run


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


def _parked(root: Path, name: str) -> list[Path]:
    """The folders under `.orphaned` that the sweep made from the folder `name`."""
    orphaned = root / ORPHANED_DIRNAME
    if not orphaned.is_dir():
        return []
    return sorted(p for p in orphaned.iterdir() if p.name.startswith(f"{name}-"))


def _assert_moved_intact(root: Path, name: str, count: int = 1) -> list[Path]:
    assert not (root / name).exists()
    parked = _parked(root, name)
    assert len(parked) == count, parked
    for p in parked:
        assert (p / "images" / "a.png").read_bytes() == b"not really a png"
    return parked


def test_a_folder_with_no_row_is_moved_aside_and_a_named_one_kept(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            kept = _folder(env.datasets_dir, "kept")
            _folder(env.datasets_dir, "orphan")
            await _add(env, "kept", str(kept))

            assert await _sweep(env) == ["orphan"]
            assert kept.is_dir()
            (parked,) = _assert_moved_intact(env.datasets_dir, "orphan")
            # `orphan-YYYYmmdd-HHMMSS`
            stamp = parked.name.removeprefix("orphan-")
            assert len(stamp) == 15 and stamp[8] == "-", stamp

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
            _folder(env.datasets_dir, "orphan")
            await _add(env, "present", str(present))
            await _add(env, "gone", str(env.datasets_dir / "gone"))

            assert await _sweep(env) == ["orphan"]
            assert present.is_dir()
            _assert_moved_intact(env.datasets_dir, "orphan")

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
    """No `images/` inside, so not a dataset: swept even with no rows. Empty, so deleted."""
    async def go():
        async with api_env(tmp_path) as env:
            stray = env.datasets_dir / "stray"
            stray.mkdir()
            assert await _sweep(env) == ["stray"]
            assert not stray.exists()
            assert not (env.datasets_dir / ORPHANED_DIRNAME).exists()

    run(go())


def test_an_empty_database_moves_a_non_dataset_folder_that_holds_files(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            notes = env.datasets_dir / "notes"
            notes.mkdir()
            (notes / "todo.txt").write_text("keep me")
            assert await _sweep(env) == ["notes"]
            (parked,) = _parked(env.datasets_dir, "notes")
            assert (parked / "todo.txt").read_text() == "keep me"

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
            _folder(env.datasets_dir, "orphan")
            await _add(env, "kept", str(kept))
            await _add(env, "mixed", str(env.datasets_dir / "mixed"))

            assert await _sweep(env) == ["orphan"]
            assert mixed.is_dir()
            _assert_moved_intact(env.datasets_dir, "orphan")

    run(go())


def test_a_second_sweep_leaves_the_orphaned_folder_alone(tmp_path):
    """`.orphaned` has no row either. Swept, it would be moved into itself (or, before
    M9, deleted with everything the first sweep put there)."""
    async def go():
        async with api_env(tmp_path) as env:
            kept = _folder(env.datasets_dir, "kept")
            _folder(env.datasets_dir, "orphan")
            await _add(env, "kept", str(kept))

            assert await _sweep(env) == ["orphan"]
            parked = _assert_moved_intact(env.datasets_dir, "orphan")
            assert await _sweep(env) == []
            assert _assert_moved_intact(env.datasets_dir, "orphan") == parked

    run(go())


def test_the_same_name_twice_in_one_second_does_not_collide(tmp_path, monkeypatch):
    """A restore that orphans `cats`, a re-created `cats` orphaned again: two sweeps with
    one stamp. The second must not land on (or inside) the first."""
    from datetime import datetime as _real_datetime

    class _FixedClock:
        @staticmethod
        def now(tz=None):
            return _real_datetime(2026, 11, 8, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(dataset_service, "datetime", _FixedClock)

    async def go():
        async with api_env(tmp_path) as env:
            kept = _folder(env.datasets_dir, "kept")
            await _add(env, "kept", str(kept))
            _folder(env.datasets_dir, "cats")
            assert await _sweep(env) == ["cats"]
            _folder(env.datasets_dir, "cats")
            assert await _sweep(env) == ["cats"]

            parked = _assert_moved_intact(env.datasets_dir, "cats", count=2)
            assert [p.name for p in parked] == ["cats-20261108-120000", "cats-20261108-120000-2"]

    run(go())


def test_a_folder_holding_only_empty_folders_is_deleted_not_moved(tmp_path):
    """A dataset whose creation failed halfway: `images/` and `thumbnails/`, no file."""
    async def go():
        async with api_env(tmp_path) as env:
            kept = _folder(env.datasets_dir, "kept")
            await _add(env, "kept", str(kept))
            shell = env.datasets_dir / "shell"
            (shell / "images" / "thumbnails").mkdir(parents=True)

            assert await _sweep(env) == ["shell"]
            assert not shell.exists()
            assert not (env.datasets_dir / ORPHANED_DIRNAME).exists()

    run(go())


def test_a_folder_that_cannot_be_moved_stays_and_the_rest_still_move(tmp_path, monkeypatch):
    """A locked folder (PM-021 on Windows) must neither stop the sweep nor be deleted."""
    real = dataset_service.rename_retrying

    async def _rename(src, dst, **kw):
        if src.name == "locked":
            raise utils.FileInUseError("'locked' is open in another program")
        await real(src, dst, **kw)

    monkeypatch.setattr(dataset_service, "rename_retrying", _rename)

    async def go():
        async with api_env(tmp_path) as env:
            kept = _folder(env.datasets_dir, "kept")
            await _add(env, "kept", str(kept))
            locked = _folder(env.datasets_dir, "locked")
            _folder(env.datasets_dir, "orphan")

            assert await _sweep(env) == ["orphan"]
            assert (locked / "images" / "a.png").is_file()
            assert _parked(env.datasets_dir, "locked") == []
            _assert_moved_intact(env.datasets_dir, "orphan")

    run(go())


def test_an_orphaned_path_that_is_a_file_moves_nothing_and_deletes_nothing(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            kept = _folder(env.datasets_dir, "kept")
            await _add(env, "kept", str(kept))
            orphan = _folder(env.datasets_dir, "orphan")
            (env.datasets_dir / ORPHANED_DIRNAME).write_text("in the way")

            assert await _sweep(env) == []
            assert (orphan / "images" / "a.png").is_file()

    run(go())


def test_dot_folders_are_never_touched(tmp_path):
    """No dataset folder can start with a dot (`_name_to_slug` strips them), so a dot
    folder is another tool's (`.stfolder`, `.Trash-1000`), never an orphan."""
    async def go():
        async with api_env(tmp_path) as env:
            kept = _folder(env.datasets_dir, "kept")
            await _add(env, "kept", str(kept))
            marker = env.datasets_dir / ".stfolder"
            marker.mkdir()
            trash = _folder(env.datasets_dir, ".Trash-1000")

            assert await _sweep(env) == []
            assert marker.is_dir()
            assert (trash / "images" / "a.png").is_file()

    run(go())


@pytest.mark.skipif(os.name == "nt", reason="chmod cannot make a directory unreadable on Windows")
def test_a_tree_the_walk_cannot_read_counts_as_holding_files(tmp_path):
    """os.walk skips what it cannot list. Read as empty, that tree would be deleted."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads every directory")
    shell = tmp_path / "shell"
    sealed = shell / "sealed"
    sealed.mkdir(parents=True)
    (sealed / "a.png").write_bytes(b"x")
    sealed.chmod(0)
    try:
        assert dataset_service._empty_tree_dirs(shell) is None
    finally:
        sealed.chmod(0o755)


def _kept_and_root(env) -> Path:
    """A named dataset beside the orphans, so the "folders exist" guard is satisfied."""
    return _folder(env.datasets_dir, "kept")


@needs_symlinks
def test_a_link_inside_an_otherwise_empty_orphan_counts_as_content(tmp_path):
    """A link to an empty folder elsewhere: walked into, the tree would read empty and
    be deleted. Counted as content, it is moved, and the target is untouched."""
    async def go():
        async with api_env(tmp_path) as env:
            kept = _kept_and_root(env)
            await _add(env, "kept", str(kept))
            target = tmp_path / "elsewhere"
            target.mkdir()
            shell = env.datasets_dir / "shell"
            (shell / "images").mkdir(parents=True)
            (shell / "images" / "link").symlink_to(target, target_is_directory=True)

            assert await _sweep(env) == ["shell"]
            (parked,) = _parked(env.datasets_dir, "shell")
            assert (parked / "images" / "link").is_symlink()
            assert target.is_dir()

    run(go())


@needs_symlinks
def test_an_orphan_that_is_itself_a_link_moves_the_link_not_the_target(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            kept = _kept_and_root(env)
            await _add(env, "kept", str(kept))
            target = _folder(tmp_path, "elsewhere")
            (env.datasets_dir / "pointer").symlink_to(target, target_is_directory=True)

            assert await _sweep(env) == ["pointer"]
            (parked,) = _parked(env.datasets_dir, "pointer")
            assert parked.is_symlink()
            assert (target / "images" / "a.png").is_file()

    run(go())


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_a_junction_inside_an_otherwise_empty_orphan_counts_as_content(tmp_path):
    """`os.path.islink` is False for a junction and `os.walk` walks into one, so
    without `isjunction` this tree read as empty."""
    import _winapi

    async def go():
        async with api_env(tmp_path) as env:
            kept = _kept_and_root(env)
            await _add(env, "kept", str(kept))
            target = tmp_path / "elsewhere"
            target.mkdir()
            shell = env.datasets_dir / "shell"
            (shell / "images").mkdir(parents=True)
            _winapi.CreateJunction(str(target), str(shell / "images" / "junction"))

            assert await _sweep(env) == ["shell"]
            (parked,) = _parked(env.datasets_dir, "shell")
            assert os.path.isjunction(parked / "images" / "junction")
            assert target.is_dir()

    run(go())


def test_empty_folders_are_still_removed_when_nothing_can_be_moved(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            kept = _kept_and_root(env)
            await _add(env, "kept", str(kept))
            orphan = _folder(env.datasets_dir, "orphan")
            (env.datasets_dir / "shell" / "images").mkdir(parents=True)
            (env.datasets_dir / ORPHANED_DIRNAME).write_text("in the way")

            assert await _sweep(env) == ["shell"]
            assert not (env.datasets_dir / "shell").exists()
            assert (orphan / "images" / "a.png").is_file()

    run(go())


def test_a_folder_the_row_names_by_another_spelling_is_kept_by_identity(tmp_path, monkeypatch):
    """A case-insensitive mount where PATHS_FOLD_CASE is False: the strings differ, the
    folder is the same, and the file identity keeps it."""
    probe = tmp_path / "CaseProbe"
    probe.mkdir()
    if not (tmp_path / "caseprobe").exists():
        pytest.skip("this filesystem is case-sensitive")
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", False)

    async def go():
        async with api_env(tmp_path) as env:
            kept = _kept_and_root(env)
            mixed = _folder(env.datasets_dir, "Mixed")
            await _add(env, "kept", str(kept))
            await _add(env, "mixed", str(env.datasets_dir / "mixed"))

            assert await _sweep(env) == []
            assert (mixed / "images" / "a.png").is_file()

    run(go())
