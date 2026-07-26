"""Startup database backup + integrity check (`services/db_maintenance.py`).

The routine runs unattended on every boot and is the only copy of the database that
exists, so the two rules worth a test are the ones whose failure is silent:

- a database that fails `PRAGMA quick_check` is NOT backed up — rotating a corrupt
  copy in would age out the newest good one, turning the safety net into the thing
  that loses the data;
- rotation keeps exactly `BACKUP_KEEP` copies, oldest first;
- a run within `BACKUP_MIN_AGE_SECONDS` of the newest copy does nothing at all —
  uvicorn re-runs the lifespan on every `--reload` file save, and without the guard
  a handful of saves would rotate the whole pre-session history out.

The two tests that drive the routine repeatedly pin `BACKUP_MIN_AGE_SECONDS` to 0, or
the guard would suppress their second run and they would pass without exercising the
behaviour they name.

Nothing here goes near the live database: `settings.database_url` is pointed at a
throwaway file, which is also what proves `database_file_path()` parses the URL form
the app actually uses.
"""
import os
import sqlite3
import time

import backend.services.db_maintenance as dbm
from backend.config import settings


def _make_db(path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('hello')")
    conn.commit()
    conn.close()


def _point_settings_at(monkeypatch, path) -> None:
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")


def _backups(path):
    return sorted((path.parent / dbm.BACKUP_DIR_NAME).glob(f"{path.stem}-*.db"))


def test_database_file_path_parses_the_app_url_form(tmp_path, monkeypatch):
    db = tmp_path / "dataset_manager.db"
    _point_settings_at(monkeypatch, db)
    assert dbm.database_file_path() == db

    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
    assert dbm.database_file_path() is None
    monkeypatch.setattr(settings, "database_url", "postgresql://host/db")
    assert dbm.database_file_path() is None


def test_backup_is_written_and_readable(tmp_path, monkeypatch):
    db = tmp_path / "dataset_manager.db"
    _make_db(db)
    _point_settings_at(monkeypatch, db)

    dbm.run_startup_maintenance_sync()

    copies = _backups(db)
    assert len(copies) == 1
    conn = sqlite3.connect(str(copies[0]))
    try:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "hello"
    finally:
        conn.close()


def test_rotation_keeps_only_the_newest_copies(tmp_path, monkeypatch):
    db = tmp_path / "dataset_manager.db"
    _make_db(db)
    _point_settings_at(monkeypatch, db)
    monkeypatch.setattr(dbm, "BACKUP_MIN_AGE_SECONDS", 0)  # else runs 2..n are skipped

    for _ in range(dbm.BACKUP_KEEP + 3):
        dbm.run_startup_maintenance_sync()

    copies = _backups(db)
    assert len(copies) == dbm.BACKUP_KEEP
    # Filenames sort chronologically, so the survivors are the tail of the sequence.
    assert copies == sorted(copies)


def test_a_corrupt_database_is_not_backed_up(tmp_path, monkeypatch):
    db = tmp_path / "dataset_manager.db"
    _make_db(db)
    _point_settings_at(monkeypatch, db)
    # Off, so the second run is refused by quick_check — the rule under test — rather
    # than short-circuited by the age guard before it ever gets there.
    monkeypatch.setattr(dbm, "BACKUP_MIN_AGE_SECONDS", 0)

    dbm.run_startup_maintenance_sync()
    good = _backups(db)
    assert len(good) == 1

    db.write_bytes(b"this is not a database" * 100)
    dbm.run_startup_maintenance_sync()

    # The good copy survives and no second one joined it.
    assert _backups(db) == good


def test_a_recent_backup_suppresses_the_next_run(tmp_path, monkeypatch):
    """The hot-reload case: two boots inside the window leave one copy, not two."""
    db = tmp_path / "dataset_manager.db"
    _make_db(db)
    _point_settings_at(monkeypatch, db)

    dbm.run_startup_maintenance_sync()
    first = _backups(db)
    assert len(first) == 1

    dbm.run_startup_maintenance_sync()
    assert _backups(db) == first


def test_a_backup_older_than_the_window_is_refreshed(tmp_path, monkeypatch):
    """...but the guard is an age check, not a once-per-process latch."""
    db = tmp_path / "dataset_manager.db"
    _make_db(db)
    _point_settings_at(monkeypatch, db)

    dbm.run_startup_maintenance_sync()
    copies = _backups(db)
    assert len(copies) == 1

    # Backdate just past the window rather than sleeping through it.
    stale = time.time() - dbm.BACKUP_MIN_AGE_SECONDS - 60
    os.utime(copies[0], (stale, stale))

    dbm.run_startup_maintenance_sync()
    assert len(_backups(db)) == 2


def test_a_future_dated_backup_does_not_wedge_the_guard(tmp_path, monkeypatch):
    """Clock skew must fail towards an extra copy, never a permanently skipped one."""
    db = tmp_path / "dataset_manager.db"
    _make_db(db)
    _point_settings_at(monkeypatch, db)

    dbm.run_startup_maintenance_sync()
    copies = _backups(db)
    ahead = time.time() + 86_400
    os.utime(copies[0], (ahead, ahead))

    dbm.run_startup_maintenance_sync()
    assert len(_backups(db)) == 2


def test_missing_database_file_is_not_an_error(tmp_path, monkeypatch):
    db = tmp_path / "never-created.db"
    _point_settings_at(monkeypatch, db)
    dbm.run_startup_maintenance_sync()
    assert not (tmp_path / dbm.BACKUP_DIR_NAME).exists()
