"""One stored spelling for the datasets root (PM-034): `on_disk_spelling`,
`respelled` and the startup repair `repair_root_spelling`.

`_take_backup` is always patched: the real one resolves the app's real database file
from `settings.database_url`, not the test's throwaway one.
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from backend import utils
from backend.config import settings
from backend.models import Dataset, Image, Video
from backend.models.versioning import DatasetVersion, VersionImageState
from backend.services import db_maintenance, root_spelling
from backend.services.root_spelling import (
    PATH_COLUMNS,
    repair_root_spelling,
    respelled,
    settle_root_spelling,
)
from backend.tests.conftest import api_env, needs_symlinks, run, upload_image
from backend.utils import on_disk_spelling


# -- on_disk_spelling --------------------------------------------------------

def test_on_disk_spelling_takes_each_components_listed_case(tmp_path):
    (tmp_path / "Foo" / "Bar").mkdir(parents=True)
    got = on_disk_spelling(tmp_path / "foo" / "bar")
    # `Path ==` is case-blind on Windows, so compare the spelled parts.
    assert got.parts[len(tmp_path.parts):] == ("Foo", "Bar")
    assert got.parts[: len(tmp_path.parts)] == tmp_path.parts


def test_on_disk_spelling_prefers_the_exact_name_and_stops_at_an_ambiguous_one(tmp_path):
    (tmp_path / "Foo").mkdir()
    (tmp_path / "foo").mkdir(exist_ok=True)
    if len(os.listdir(tmp_path)) != 2:
        pytest.skip("case-insensitive filesystem: Foo and foo are one directory")
    (tmp_path / "foo" / "Inner").mkdir()
    assert on_disk_spelling(tmp_path / "foo" / "inner").parts[-2:] == ("foo", "Inner")
    # Two case-blind matches and no exact one: kept as typed, and so is the rest.
    assert on_disk_spelling(tmp_path / "FOO" / "inner").parts[-2:] == ("FOO", "inner")


def test_on_disk_spelling_keeps_a_missing_tail_as_typed(tmp_path):
    (tmp_path / "Foo").mkdir()
    got = on_disk_spelling(tmp_path / "foo" / "Nope" / "x.PNG")
    assert got.parts[len(tmp_path.parts):] == ("Foo", "Nope", "x.PNG")


def test_on_disk_spelling_stops_at_dot_dot(tmp_path):
    (tmp_path / "Foo").mkdir()
    got = on_disk_spelling(tmp_path / "foo" / ".." / "foo")
    assert got.parts[len(tmp_path.parts):] == ("Foo", "..", "foo")


@needs_symlinks
def test_on_disk_spelling_does_not_resolve_a_link(tmp_path):
    (tmp_path / "Target" / "Sub").mkdir(parents=True)
    os.symlink(tmp_path / "Target", tmp_path / "Lnk", target_is_directory=True)
    got = on_disk_spelling(tmp_path / "lnk" / "sub")
    assert got.parts[len(tmp_path.parts):] == ("Lnk", "Sub")
    assert "Target" not in got.parts


def test_on_disk_spelling_returns_a_relative_path_unchanged():
    assert on_disk_spelling(Path("a/B/c")) == Path("a/B/c")


# -- respelled ---------------------------------------------------------------

ROOT = os.sep + os.path.join("data", "Datasets")


def test_respelled_rewrites_a_case_variant_of_the_root():
    value = os.path.join(ROOT.swapcase(), "ds", "images", "a.png")
    assert respelled(value, ROOT) == os.path.join(ROOT, "ds", "images", "a.png")


@pytest.mark.parametrize("value", [
    os.path.join(ROOT, "ds", "a.png"),                                # already exact
    os.path.join(os.sep + "elsewhere", "Datasets", "ds", "a.png"),    # another root
    ROOT.swapcase() + "X" + os.sep + "ds",                            # sibling, no separator
    ROOT.swapcase(),                                                  # the root itself
    "",
    None,
])
def test_respelled_leaves_everything_else_alone(value):
    assert respelled(value, ROOT) is None


# -- repair_root_spelling ----------------------------------------------------

class _Backups:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return Path("fake-backup.db")


def _same_by_casefold(a: str, b: str) -> bool:
    """Stands in for `os.path.samefile` where the swapcased root does not exist on disk
    (Linux CI): a case variant of the root is "the same folder"."""
    return a.casefold() == b.casefold()


def _declare_same_folder(monkeypatch, value=_same_by_casefold):
    monkeypatch.setattr(
        root_spelling, "_same_folder", value if callable(value) else (lambda a, b: value)
    )


STAMP = datetime(2001, 2, 3, 4, 5, 6)


def _swap_root(value: str, root: str) -> str:
    assert value.startswith(root)
    return root.swapcase() + value[len(root):]


async def _seed_all_columns(env) -> dict:
    """A dataset with an image, a video and a snapshot state, all under the root with
    its case flipped, plus a second dataset under a foreign root. Returns the ids."""
    ds = await env.create_dataset("d")
    img = await upload_image(env, ds["id"], "pic.png")
    other = await env.create_dataset("other")
    root = str(env.datasets_dir)
    async with env.Session() as db:
        video = Video(
            dataset_id=ds["id"], filename="clip.mp4", original_filename="clip.mp4",
            file_path=str(Path(ds["folder_path"]) / "videos" / "clip.mp4"),
            poster_path=str(Path(ds["folder_path"]) / "videos" / "thumbnails" / "clip.webp"),
        )
        version = DatasetVersion(dataset_id=ds["id"], name="s1")
        db.add_all([video, version])
        await db.flush()
        state = VersionImageState(
            version_id=version.id, image_id=img["id"], filename="pic.png",
            file_path=str(Path(ds["folder_path"]) / "images" / "pic.png"),
        )
        db.add(state)
        await db.flush()
        ids = {"ds": ds["id"], "img": img["id"], "video": video.id, "state": state.id,
               "other": other["id"]}

        row = await db.get(Dataset, ds["id"])
        row.folder_path = _swap_root(row.folder_path, root)
        row.updated_at = STAMP
        image = await db.get(Image, img["id"])
        image.updated_at = STAMP
        video.updated_at = STAMP
        image.file_path = _swap_root(image.file_path, root)
        image.thumbnail_path = _swap_root(image.thumbnail_path, root)
        video.file_path = _swap_root(video.file_path, root)
        video.poster_path = _swap_root(video.poster_path, root)
        state.file_path = _swap_root(state.file_path, root)
        # Another root entirely: a database copied from elsewhere.
        (await db.get(Dataset, other["id"])).folder_path = os.sep + os.path.join("elsewhere", "x")
        await db.commit()
    return ids


async def _stored(env, ids) -> dict:
    async with env.Session() as db:
        ds = await db.get(Dataset, ids["ds"])
        img = await db.get(Image, ids["img"])
        vid = await db.get(Video, ids["video"])
        st = await db.get(VersionImageState, ids["state"])
        other = await db.get(Dataset, ids["other"])
        return {
            "Dataset.folder_path": ds.folder_path,
            "Image.file_path": img.file_path,
            "Image.thumbnail_path": img.thumbnail_path,
            "Video.file_path": vid.file_path,
            "Video.poster_path": vid.poster_path,
            "VersionImageState.file_path": st.file_path,
            "other": other.folder_path,
        }


def test_path_columns_cover_the_six_columns():
    assert {(m.__name__, a) for m, a in PATH_COLUMNS} == {
        ("Dataset", "folder_path"), ("Image", "file_path"), ("Image", "thumbnail_path"),
        ("Video", "file_path"), ("Video", "poster_path"), ("VersionImageState", "file_path"),
    }


def test_the_repair_rewrites_every_column_once(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    _declare_same_folder(monkeypatch)
    backups = _Backups()
    monkeypatch.setattr(root_spelling, "_take_backup", backups)

    async def scenario():
        async with api_env(tmp_path) as env:
            ids = await _seed_all_columns(env)
            root = str(env.datasets_dir)
            before = await _stored(env, ids)
            assert all(v.startswith(root.swapcase()) for k, v in before.items() if k != "other")

            async with env.Session() as db:
                assert await repair_root_spelling(db) == 6
            assert backups.calls == 1

            after = await _stored(env, ids)
            for key, value in after.items():
                if key == "other":
                    assert value == before["other"]      # foreign root untouched
                else:
                    assert value.startswith(root + os.sep), key
                    assert value[len(root):] == before[key][len(root):], key  # only the root moved

            async with env.Session() as db:
                assert await repair_root_spelling(db) == 0
            assert backups.calls == 1                    # idempotent, no second backup

    run(scenario())


def test_the_repair_is_a_no_op_off_a_case_folding_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", False)
    _declare_same_folder(monkeypatch)
    backups = _Backups()
    monkeypatch.setattr(root_spelling, "_take_backup", backups)

    async def scenario():
        async with api_env(tmp_path) as env:
            ids = await _seed_all_columns(env)
            before = await _stored(env, ids)
            async with env.Session() as db:
                assert await repair_root_spelling(db) == 0
            assert await _stored(env, ids) == before
            assert backups.calls == 0

    run(scenario())


def test_a_failed_backup_declines_and_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    _declare_same_folder(monkeypatch)
    calls = []

    def failing():
        calls.append(1)
        raise OSError("disk full")

    monkeypatch.setattr(root_spelling, "_take_backup", failing)

    async def scenario():
        async with api_env(tmp_path) as env:
            ids = await _seed_all_columns(env)
            before = await _stored(env, ids)
            async with env.Session() as db:
                assert await repair_root_spelling(db) is None   # declined, not "0 rows"
            assert calls == [1]
            assert await _stored(env, ids) == before

    run(scenario())


def test_a_healthy_database_takes_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    backups = _Backups()
    monkeypatch.setattr(root_spelling, "_take_backup", backups)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "pic.png")
            async with env.Session() as db:
                assert await repair_root_spelling(db) == 0
            assert backups.calls == 0

    run(scenario())


def test_the_repair_keeps_updated_at(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    _declare_same_folder(monkeypatch)
    monkeypatch.setattr(root_spelling, "_take_backup", _Backups())

    async def scenario():
        async with api_env(tmp_path) as env:
            ids = await _seed_all_columns(env)
            async with env.Session() as db:
                assert await repair_root_spelling(db) == 6
            async with env.Session() as db:
                assert (await db.get(Dataset, ids["ds"])).updated_at == STAMP
                assert (await db.get(Image, ids["img"])).updated_at == STAMP
                assert (await db.get(Video, ids["video"])).updated_at == STAMP

    run(scenario())


def test_a_variant_that_is_not_the_same_folder_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    _declare_same_folder(monkeypatch, False)
    backups = _Backups()
    monkeypatch.setattr(root_spelling, "_take_backup", backups)

    async def scenario():
        async with api_env(tmp_path) as env:
            ids = await _seed_all_columns(env)
            before = await _stored(env, ids)
            async with env.Session() as db:
                assert await repair_root_spelling(db) == 0
            assert await _stored(env, ids) == before
            assert backups.calls == 0

    run(scenario())


def test_a_real_case_variant_is_rewritten_on_a_case_insensitive_filesystem(tmp_path, monkeypatch):
    # No `_same_folder` patch: the real `os.path.samefile` decides.
    (tmp_path / "Root").mkdir()
    if not (tmp_path / "root").exists():
        pytest.skip("case-sensitive filesystem: root and Root are two folders")
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    backups = _Backups()
    monkeypatch.setattr(root_spelling, "_take_backup", backups)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            async with env.Session() as db:
                (await db.get(Dataset, ds["id"])).folder_path = str(tmp_path / "root" / "d")
                await db.commit()
            async with env.Session() as db:
                assert await repair_root_spelling(db, tmp_path / "Root") == 1
            assert backups.calls == 1
            async with env.Session() as db:
                got = (await db.get(Dataset, ds["id"])).folder_path
                assert got == str(tmp_path / "Root" / "d")
                assert got.startswith(str(tmp_path / "Root"))

    run(scenario())


def test_a_real_distinct_folder_is_left_alone_on_a_case_sensitive_directory(tmp_path, monkeypatch):
    (tmp_path / "Root").mkdir()
    (tmp_path / "root").mkdir(exist_ok=True)
    if len(os.listdir(tmp_path)) != 2:
        pytest.skip("case-insensitive filesystem: Root and root are one folder")
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    backups = _Backups()
    monkeypatch.setattr(root_spelling, "_take_backup", backups)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            other = str(tmp_path / "root" / "d")
            async with env.Session() as db:
                (await db.get(Dataset, ds["id"])).folder_path = other
                await db.commit()
            async with env.Session() as db:
                assert await repair_root_spelling(db, tmp_path / "Root") == 0
            assert backups.calls == 0
            async with env.Session() as db:
                assert (await db.get(Dataset, ds["id"])).folder_path == other

    run(scenario())


# -- settle_root_spelling ----------------------------------------------------

def _respell_to_swapcase(monkeypatch):
    monkeypatch.setattr(utils, "on_disk_spelling", lambda p: Path(str(p).swapcase()))


def test_settle_adopts_the_new_spelling_after_the_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    _declare_same_folder(monkeypatch)
    backups = _Backups()
    monkeypatch.setattr(root_spelling, "_take_backup", backups)
    _respell_to_swapcase(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            original = env.datasets_dir
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "pic.png")
            spelled = str(original).swapcase()
            try:
                async with env.Session() as db:
                    await settle_root_spelling(db)
                assert str(settings.datasets_dir) == spelled
                assert backups.calls == 1
                async with env.Session() as db:
                    assert (await db.get(Dataset, ds["id"])).folder_path.startswith(spelled + os.sep)
            finally:
                settings.datasets_dir = original

    run(scenario())


def test_settle_keeps_the_old_spelling_when_the_backup_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    _declare_same_folder(monkeypatch)

    def failing():
        raise OSError("disk full")

    monkeypatch.setattr(root_spelling, "_take_backup", failing)
    _respell_to_swapcase(monkeypatch)

    async def scenario():
        async with api_env(tmp_path) as env:
            original = env.datasets_dir
            ds = await env.create_dataset("d")
            try:
                async with env.Session() as db:
                    before = (await db.get(Dataset, ds["id"])).folder_path
                async with env.Session() as db:
                    await settle_root_spelling(db)
                assert str(settings.datasets_dir) == str(original)
                async with env.Session() as db:
                    assert (await db.get(Dataset, ds["id"])).folder_path == before
            finally:
                settings.datasets_dir = original

    run(scenario())


def test_settle_does_nothing_off_a_case_folding_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", False)

    def boom(_p):
        raise AssertionError("on_disk_spelling must not run")

    monkeypatch.setattr(utils, "on_disk_spelling", boom)

    async def scenario():
        async with api_env(tmp_path) as env:
            original = env.datasets_dir
            async with env.Session() as db:
                await settle_root_spelling(db)
            assert str(settings.datasets_dir) == str(original)

    run(scenario())


# -- write_named_backup_sync -------------------------------------------------

def _make_db(tmp_path, monkeypatch) -> Path:
    db_file = tmp_path / "dbdir" / "app.db"
    db_file.parent.mkdir()
    conn = sqlite3.connect(db_file)
    conn.execute("create table t (x integer)")
    conn.execute("insert into t values (1)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_file}")
    return db_file


def test_a_named_backup_is_written_and_overwritten_outside_the_rotation(tmp_path, monkeypatch):
    db_file = _make_db(tmp_path, monkeypatch)
    first = db_maintenance.write_named_backup_sync("x")
    assert first == db_file.parent / "backups" / "app.pre-x.db"
    conn = sqlite3.connect(first)
    assert conn.execute("select x from t").fetchall() == [(1,)]
    conn.close()

    second = db_maintenance.write_named_backup_sync("x")
    assert second == first
    assert sorted(p.name for p in first.parent.iterdir()) == ["app.pre-x.db"]   # no .tmp left

    # Not the rotation's: never counted as "a recent backup", never pruned.
    assert db_maintenance._newest_backup_age(db_file) is None
    assert db_maintenance._prune_backups(db_file, keep=0) == 0
    assert first.exists()


def test_a_failed_copy_leaves_no_temp_and_does_not_block_the_next(tmp_path, monkeypatch):
    """A leftover `.tmp` is not a database: copying into it failed at every boot."""
    db_file = _make_db(tmp_path, monkeypatch)
    backups = db_file.parent / "backups"
    backups.mkdir()
    (backups / "app.pre-x.db.tmp").write_bytes(b"not a database")   # an earlier crash's leftover

    real_copy = db_maintenance._copy_database

    def junk_then_fail(src, dest):
        Path(dest).write_bytes(b"junk")
        raise OSError("disk full")

    monkeypatch.setattr(db_maintenance, "_copy_database", junk_then_fail)
    with pytest.raises(OSError):
        db_maintenance.write_named_backup_sync("x")
    assert sorted(p.name for p in backups.iterdir()) == []

    monkeypatch.setattr(db_maintenance, "_copy_database", real_copy)
    (backups / "app.pre-x.db.tmp").write_bytes(b"not a database")
    dest = db_maintenance.write_named_backup_sync("x")
    assert sorted(p.name for p in backups.iterdir()) == ["app.pre-x.db"]
    conn = sqlite3.connect(dest)
    assert conn.execute("select x from t").fetchall() == [(1,)]
    conn.close()


def test_a_named_backup_without_a_database_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'nope.db'}")
    with pytest.raises(FileNotFoundError):
        db_maintenance.write_named_backup_sync("x")


def test_a_named_backup_of_a_failing_database_raises_and_writes_nothing(tmp_path, monkeypatch):
    db_file = _make_db(tmp_path, monkeypatch)
    monkeypatch.setattr(db_maintenance, "_quick_check", lambda _p: False)
    with pytest.raises(sqlite3.DatabaseError):
        db_maintenance.write_named_backup_sync("x")
    assert not (db_file.parent / "backups" / "app.pre-x.db").exists()
