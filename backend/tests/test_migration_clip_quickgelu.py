"""Migration f3a8d1c6b9e2 — old-build CLIP vectors cleared, old LAION scores re-labelled.

Loaded with importlib and driven against a real SQLite file through a stand-in
`alembic.op`, as `test_migration_strip_subfolder_segments.py` does: CI's pytest
install has no alembic.
"""
import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest
import sqlalchemy as sa

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "f3a8d1c6b9e2_clip_quickgelu_invalidate.py"
)
_spec = importlib.util.spec_from_file_location("_clip_quickgelu", _MIGRATION)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE images (id TEXT PRIMARY KEY, clip_embedding BLOB, "
                "dino_embedding BLOB, aesthetic_score REAL, aesthetic_model TEXT, "
                "watermark_score REAL, quality_flags JSON)")
    con.execute("CREATE TABLE version_image_states (id TEXT PRIMARY KEY, "
                "aesthetic_score REAL, aesthetic_model TEXT, "
                "watermark_score REAL, quality_flags JSON)")
    con.commit()
    con.close()


def _run(path: Path, monkeypatch, fn: str = "upgrade") -> None:
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            fake_alembic = types.ModuleType("alembic")
            fake_alembic.op = types.SimpleNamespace(get_bind=lambda: conn)
            monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
            getattr(_mod, fn)()
    finally:
        engine.dispose()


def _backup_path(db: Path) -> Path:
    return db.parent / "backups" / f"{db.stem}.pre-{_mod.BACKUP_TAG}.db"


_CLIP_FLAGGED = json.dumps({"has_watermark": True, "is_blurry": True})
# A flag a detection scan set (`sync_watermark_flag`): no CLIP watermark score.
_DETECTION_FLAGGED = json.dumps({"has_watermark": True})


def _seed(db: Path) -> None:
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?)", [
        ("laion", b"\x01" * 8, b"\x02" * 8, 6.2, "laion", 0.91, _CLIP_FLAGGED),
        ("v25", b"\x01" * 8, b"\x02" * 8, 5.1, "v2_5", 0.12, None),
        ("unscored", b"\x01" * 8, None, None, None, None, _DETECTION_FLAGGED),
        ("bare", None, None, None, None, None, None),
    ])
    con.executemany("INSERT INTO version_image_states VALUES (?, ?, ?, ?, ?)", [
        ("s-laion", 6.0, "laion", 0.88, _CLIP_FLAGGED),
        ("s-v25", 5.0, "v2_5", None, None),
        ("s-none", None, None, None, None),
    ])
    con.commit()
    con.close()


def _flags(con, table: str) -> dict:
    return {i: json.loads(f) if f else None
            for i, f in con.execute(f"SELECT id, quality_flags FROM {table}")}


def test_upgrade_clears_vectors_and_relabels_laion_in_both_tables(tmp_path, monkeypatch, capsys):
    db = tmp_path / "crucible.db"
    _make_db(db)
    _seed(db)

    _run(db, monkeypatch)

    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(clip_embedding) FROM images").fetchone()[0] == 0
    # DINO is a different model: untouched.
    assert con.execute("SELECT COUNT(dino_embedding) FROM images").fetchone()[0] == 2
    assert dict(con.execute("SELECT id, aesthetic_model FROM images")) == {
        "laion": "laion_gelu", "v25": "v2_5", "unscored": None, "bare": None,
    }
    # The score itself stays: only its label changes.
    assert con.execute("SELECT aesthetic_score FROM images WHERE id = 'laion'").fetchone()[0] == 6.2
    assert dict(con.execute("SELECT id, aesthetic_model FROM version_image_states")) == {
        "s-laion": "laion_gelu", "s-v25": "v2_5", "s-none": None,
    }
    # Watermark: cleared wherever the CLIP check ran, in both tables; other flags stay.
    assert con.execute("SELECT COUNT(watermark_score) FROM images").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(watermark_score) FROM version_image_states").fetchone()[0] == 0
    assert _flags(con, "images") == {
        "laion": {"is_blurry": True},
        "v25": None,
        # No watermark score, so a detection scan set this flag: kept.
        "unscored": {"has_watermark": True},
        "bare": None,
    }
    assert _flags(con, "version_image_states")["s-laion"] == {"is_blurry": True}
    con.close()

    backup = _backup_path(db)
    assert backup.exists()
    old = sqlite3.connect(backup)
    assert old.execute("SELECT COUNT(clip_embedding) FROM images").fetchone()[0] == 3
    assert old.execute("SELECT aesthetic_model FROM images WHERE id = 'laion'").fetchone()[0] == "laion"
    old.close()

    out = capsys.readouterr().out
    assert "Cleared 3 CLIP style embedding(s)" in out
    assert "1 LAION aesthetic score(s)" in out
    assert "Cleared 2 watermark score(s)" in out
    assert str(backup) in out


def test_a_database_with_nothing_to_change_takes_no_backup_and_prints_nothing(tmp_path, monkeypatch, capsys):
    db = tmp_path / "crucible.db"
    _make_db(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO images VALUES ('v25', NULL, NULL, 5.1, 'v2_5', NULL, ?)", (_DETECTION_FLAGGED,))
    con.commit()
    con.close()

    _run(db, monkeypatch)

    assert not _backup_path(db).exists()
    assert capsys.readouterr().out == ""


def test_a_failed_backup_stops_the_upgrade_before_any_write(tmp_path, monkeypatch):
    db = tmp_path / "crucible.db"
    _make_db(db)
    _seed(db)

    def boom(conn):
        raise OSError("disk full")
    monkeypatch.setattr(_mod, "_backup", boom)
    with pytest.raises(OSError):
        _run(db, monkeypatch)

    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(clip_embedding) FROM images").fetchone()[0] == 3
    assert con.execute("SELECT aesthetic_model FROM images WHERE id = 'laion'").fetchone()[0] == "laion"
    con.close()


def test_downgrade_restores_the_label_but_not_the_vectors(tmp_path, monkeypatch):
    db = tmp_path / "crucible.db"
    _make_db(db)
    _seed(db)
    _run(db, monkeypatch)

    _run(db, monkeypatch, "downgrade")

    con = sqlite3.connect(db)
    assert con.execute("SELECT aesthetic_model FROM images WHERE id = 'laion'").fetchone()[0] == "laion"
    assert con.execute("SELECT aesthetic_model FROM version_image_states WHERE id = 's-laion'").fetchone()[0] == "laion"
    assert con.execute("SELECT aesthetic_model FROM images WHERE id = 'v25'").fetchone()[0] == "v2_5"
    assert con.execute("SELECT COUNT(clip_embedding) FROM images").fetchone()[0] == 0
    con.close()
