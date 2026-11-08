"""Migration b8d2f4a6c1e3 — stored folder paths take decision 12's segment strip.

Loaded with importlib, as `test_migration_nest_library_categories.py` does: CI's pytest
install has no alembic, which is why the migration imports `op` inside `upgrade()`
only. `upgrade()` itself is driven against a real SQLite file through a stand-in
`alembic.op` whose `get_bind()` hands over a SQLAlchemy connection.
"""
import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.utils import join_subfolder, normalize_subfolder

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "b8d2f4a6c1e3_strip_subfolder_segment_whitespace.py"
)
_spec = importlib.util.spec_from_file_location("_strip_subfolder_segments", _MIGRATION)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


@pytest.mark.parametrize("raw", [
    "knight ", " knight /  a b ", "knight /  ", "\tx\t/y", " . / a", "a\\ b ",
    "/a//b/", "", "   ", "Case/Kept", "dark knight",
])
def test_the_inlined_rule_matches_the_app_rule(raw):
    """The migration copies the rule rather than importing it."""
    assert _mod.strip_segments(raw) == normalize_subfolder(raw) == join_subfolder(raw)


@pytest.mark.parametrize("raw", ["a/ .. /b", "..", " .. ", "a/../b", "../x"])
def test_a_dot_dot_is_dropped_as_join_subfolder_drops_it(raw):
    """The old rule stored ` .. ` as a plain name; stripped, it would become a `..` that
    every normalizing route refuses. So the migration takes `join_subfolder`'s form."""
    assert _mod.strip_segments(raw) == join_subfolder(raw)
    assert ".." not in _mod.strip_segments(raw).split("/")


def test_plan_strings_changes_only_padded_paths():
    rows = [("1", "knight "), ("2", "knight"), ("3", " a / b "), ("4", ""), ("5", None)]
    assert _mod.plan_strings(rows) == [("1", "knight"), ("3", "a/b")]


def test_plan_lists_strips_drops_blanks_and_dedupes_in_order():
    rows = [
        ("d1", json.dumps(["knight", "knight ", " a / b", "  ", "c"])),
        ("d2", json.dumps(["clean", "also/clean"])),   # unchanged: no update
        ("d3", ["already", "decoded "]),               # a driver may decode JSON itself
        ("d4", "not json"),                            # unreadable: left alone
        ("d5", None),
    ]
    assert _mod.plan_lists(rows) == [
        ("d1", ["knight", "a/b", "c"]),
        ("d3", ["already", "decoded"]),
    ]


def test_plan_recipes_rewrites_only_generate_subfolder():
    padded = {"generate": {"subfolder": " run / knight ", "count": 4}, "export": None}
    rows = [
        ("r1", json.dumps(padded)),
        ("r2", json.dumps({"generate": {"subfolder": "clean"}})),
        ("r3", json.dumps({"generate": {}})),
        ("r4", json.dumps({"other": 1})),
        ("r5", "[]"),
    ]
    assert _mod.plan_recipes(rows) == [
        ("r1", {"generate": {"subfolder": "run/knight", "count": 4}, "export": None}),
    ]


# ── upgrade() against a real file ────────────────────────────────────────────

_TABLES = {
    "images": "subfolder TEXT NOT NULL DEFAULT ''",
    "comfy_rows": "subfolder TEXT NOT NULL DEFAULT ''",
    "version_image_states": "subfolder TEXT NOT NULL DEFAULT ''",
    "datasets": "declared_subfolders TEXT NOT NULL DEFAULT '[]'",
    "comfy_plans": "declared_folders TEXT NOT NULL DEFAULT '[]'",
    "runs": "recipe TEXT NOT NULL DEFAULT '{}'",
    "run_recipes": "recipe TEXT NOT NULL DEFAULT '{}'",
    "comfy_leaves": "path TEXT NOT NULL",
}


def _make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    for table, column in _TABLES.items():
        con.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, {column})")
    con.commit()
    con.close()


def _upgrade(path: Path, monkeypatch) -> None:
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            fake_alembic = types.ModuleType("alembic")
            fake_alembic.op = types.SimpleNamespace(get_bind=lambda: conn)
            monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
            _mod.upgrade()
    finally:
        engine.dispose()


def _backup_path(db: Path) -> Path:
    return db.parent / "backups" / f"{db.stem}.pre-{_mod.BACKUP_TAG}.db"


def test_upgrade_rewrites_every_column_after_a_backup(tmp_path, monkeypatch, capsys):
    db = tmp_path / "crucible.db"
    _make_db(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO images VALUES ('i1', 'knight '), ('i2', 'knight'), ('i3', '')")
    con.execute("INSERT INTO comfy_rows VALUES ('c1', ' run / a ')")
    con.execute("INSERT INTO version_image_states VALUES ('v1', 'knight ')")
    con.execute("INSERT INTO datasets VALUES ('d1', ?)", (json.dumps(["knight", "knight "]),))
    con.execute("INSERT INTO comfy_plans VALUES ('p1', ?)", (json.dumps([" a"]),))
    con.execute("INSERT INTO runs VALUES ('r1', ?)", (json.dumps({"generate": {"subfolder": "x "}}),))
    con.execute("INSERT INTO run_recipes VALUES ('s1', ?)", (json.dumps({"generate": {"subfolder": "y "}}),))
    con.execute("INSERT INTO comfy_leaves VALUES ('l1', 'leaf')")
    con.commit()
    con.close()

    _upgrade(db, monkeypatch)

    con = sqlite3.connect(db)
    assert dict(con.execute("SELECT id, subfolder FROM images")) == {"i1": "knight", "i2": "knight", "i3": ""}
    assert con.execute("SELECT subfolder FROM comfy_rows").fetchone()[0] == "run/a"
    assert con.execute("SELECT subfolder FROM version_image_states").fetchone()[0] == "knight"
    assert json.loads(con.execute("SELECT declared_subfolders FROM datasets").fetchone()[0]) == ["knight"]
    assert json.loads(con.execute("SELECT declared_folders FROM comfy_plans").fetchone()[0]) == ["a"]
    assert json.loads(con.execute("SELECT recipe FROM runs").fetchone()[0]) == {"generate": {"subfolder": "x"}}
    assert json.loads(con.execute("SELECT recipe FROM run_recipes").fetchone()[0]) == {"generate": {"subfolder": "y"}}
    con.close()

    # The copy holds the rows as they were before the rewrite.
    backup = _backup_path(db)
    assert backup.exists()
    old = sqlite3.connect(backup)
    assert old.execute("SELECT subfolder FROM images WHERE id = 'i1'").fetchone()[0] == "knight "
    old.close()
    out = capsys.readouterr().out
    assert "2 images.subfolder" not in out and "1 images.subfolder" in out
    assert str(backup) in out


def test_a_clean_database_takes_no_backup_and_prints_nothing(tmp_path, monkeypatch, capsys):
    db = tmp_path / "crucible.db"
    _make_db(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO images VALUES ('i1', 'knight'), ('i2', 'a b/c')")
    con.execute("INSERT INTO datasets VALUES ('d1', '[\"knight\"]')")
    con.commit()
    con.close()

    _upgrade(db, monkeypatch)

    assert not _backup_path(db).exists()
    assert capsys.readouterr().out == ""


def test_a_failed_backup_stops_the_upgrade_before_any_write(tmp_path, monkeypatch):
    db = tmp_path / "crucible.db"
    _make_db(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO images VALUES ('i1', 'knight ')")
    con.commit()
    con.close()

    def boom(conn):
        raise OSError("disk full")
    monkeypatch.setattr(_mod, "_backup", boom)
    with pytest.raises(OSError):
        _upgrade(db, monkeypatch)

    con = sqlite3.connect(db)
    assert con.execute("SELECT subfolder FROM images").fetchone()[0] == "knight "
    con.close()


def test_a_padded_dot_dot_folder_lands_where_a_run_would_file_it(tmp_path, monkeypatch):
    db = tmp_path / "crucible.db"
    _make_db(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO images VALUES ('i1', 'a/ .. /b'), ('i2', ' .. ')")
    con.commit()
    con.close()

    _upgrade(db, monkeypatch)

    con = sqlite3.connect(db)
    assert dict(con.execute("SELECT id, subfolder FROM images")) == {"i1": "a/b", "i2": ""}
    con.close()


def test_the_backup_refuses_a_connection_that_has_written_rather_than_hanging(tmp_path):
    """sqlite3's backup() retries SQLITE_BUSY for ever against a source connection that
    holds a write transaction, so `_backup` must refuse before calling it."""
    db = tmp_path / "crucible.db"
    _make_db(db)
    engine = sa.create_engine(f"sqlite:///{db}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO images VALUES ('i1', 'x')"))
            with pytest.raises(RuntimeError, match="write transaction"):
                _mod._backup(conn)
    finally:
        engine.dispose()
    assert not _backup_path(db).exists()


def test_a_padded_leaf_is_reported_not_rewritten(tmp_path, monkeypatch, capsys):
    db = tmp_path / "crucible.db"
    _make_db(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO comfy_leaves VALUES ('l1', 'leaf ')")
    con.commit()
    con.close()

    _upgrade(db, monkeypatch)

    con = sqlite3.connect(db)
    assert con.execute("SELECT path FROM comfy_leaves").fetchone()[0] == "leaf "
    con.close()
    assert "1 blueprint leaf path(s)" in capsys.readouterr().out
