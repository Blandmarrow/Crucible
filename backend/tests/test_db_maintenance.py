"""Startup database backup + integrity check (`services/db_maintenance.py`).

The routine runs unattended on every boot and is the only copy of the database that
exists, so the two rules worth a test are the ones whose failure is silent:

- a database that fails `PRAGMA quick_check` is NOT backed up — rotating a corrupt
  copy in would age out the newest good one, turning the safety net into the thing
  that loses the data;
- rotation keeps exactly `BACKUP_KEEP` copies, oldest first.

Nothing here goes near the live database: `settings.database_url` is pointed at a
throwaway file, which is also what proves `database_file_path()` parses the URL form
the app actually uses.
"""
import sqlite3

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

    dbm.run_startup_maintenance_sync()
    good = _backups(db)
    assert len(good) == 1

    db.write_bytes(b"this is not a database" * 100)
    dbm.run_startup_maintenance_sync()

    # The good copy survives and no second one joined it.
    assert _backups(db) == good


def test_missing_database_file_is_not_an_error(tmp_path, monkeypatch):
    db = tmp_path / "never-created.db"
    _point_settings_at(monkeypatch, db)
    dbm.run_startup_maintenance_sync()
    assert not (tmp_path / dbm.BACKUP_DIR_NAME).exists()
