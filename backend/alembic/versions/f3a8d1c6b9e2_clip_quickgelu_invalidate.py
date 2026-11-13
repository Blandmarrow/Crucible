"""clip quickgelu: clear old CLIP vectors and watermark results, re-label old LAION scores

Revision ID: f3a8d1c6b9e2
Revises: e4b9c2d6f1a3
Create Date: 2026-11-13 00:00:00.000000

Decision 17. Until this change the app built OpenAI's CLIP ViT-L/14 with plain GELU,
while its weights were trained with QuickGELU (`aesthetic_scorer.build_clip_vit_l14`
now builds it correctly). Everything that model produced is slightly off, and the
new build's values must never be compared with the old ones:

- `images.clip_embedding` is **cleared**. A vector has no marker saying which build
  made it, and NULL is already the honest "not embedded" state every reader skips:
  the Quality page reports the gap, and *Score what's missing* with Style embeddings
  ticked refills it. `version_image_states` holds no vectors, so there is nothing to
  clear there.
- `aesthetic_model = 'laion'` becomes **`'laion_gelu'`**, in `images` and in the
  snapshot mirror `version_image_states` (or restoring an old snapshot would bring an
  old-build score back under the new build's marker). The scores stay readable, and
  the marker machinery treats them as another model's: the coverage line names them,
  *Re-score N with LAION* offers to redo them, and the duplicate and export mixed-model
  warnings apply. `v2_5` scores (SigLIP) are not touched.
- `watermark_score` is **cleared, and `has_watermark` removed from `quality_flags`,
  on every row that has a watermark score**, in `images` and in
  `version_image_states`. The CLIP zero-shot watermark check uses both towers, and
  on the dev library 83 of 1,033 flags flipped at the 0.6 threshold (decision 17).
  The flag has other writers — a detection scan's `sync_watermark_flag`, and
  watermark removal setting it False — and which one wrote it last is not recorded.
  So it is removed only where a watermark score exists, i.e. where the CLIP check
  ran and a re-scan would overwrite it anyway; a flag on a row with no score came
  from a detection scan and is kept. *Score what's missing* with Watermark ticked
  refills the cleared rows. Snapshots are cleared too, or restoring one would bring
  an old-build watermark result back with nothing to mark it.

Existing `style_similarity_score` values are left alone: each style run wrote its
whole scope from one build, so they are consistent until the next run replaces them.

Before the first write, and only when there is one, the database is copied to
`backups/<stem>.pre-clip-quickgelu.db`, as `b8d2f4a6c1e3` does: migrations run before
the app's startup backup, so this is the only copy. A failed copy raises and nothing
is changed. `downgrade` re-labels `laion_gelu` back to `laion`; the cleared vectors
and watermark results cannot come back except from that backup.

`op` and `sqlalchemy` are imported inside the functions: CI's pytest install has no
alembic (`test_migration_clip_quickgelu.py` drives `upgrade()` with a stand-in).
"""
from collections.abc import Sequence

revision: str = "f3a8d1c6b9e2"
down_revision: str | None = "e4b9c2d6f1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKUP_TAG = "clip-quickgelu"
OLD_MARKER = "laion"
NEW_MARKER = "laion_gelu"
MARKER_TABLES = ("images", "version_image_states")
WATERMARK_TABLES = ("images", "version_image_states")


def _backup(conn) -> str | None:
    """Copy the database before the first write. Returns the copy's path, or None for
    a database with no file (in-memory). Raises if the copy fails.

    The same copy as `b8d2f4a6c1e3._backup`, inlined because a migration must not
    import another migration or app code that may later change."""
    import os
    import sqlite3
    from pathlib import Path

    main = next((r[2] for r in conn.exec_driver_sql("PRAGMA database_list") if r[1] == "main"), "")
    if not main:
        return None
    raw = conn.connection.dbapi_connection
    # sqlite3's backup() retries SQLITE_BUSY for ever against a source connection that
    # holds a write transaction, so refuse rather than hang with no message.
    if raw.in_transaction:
        raise RuntimeError(f"{revision}: the connection holds a write transaction, so the "
                           "pre-change backup cannot be taken; nothing was changed")
    path = Path(main)
    dest = path.parent / "backups" / f"{path.stem}.pre-{BACKUP_TAG}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        target = sqlite3.connect(str(tmp))
        try:
            raw.backup(target)
        finally:
            target.close()
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return str(dest)


def upgrade() -> None:
    import sqlalchemy as sa
    from alembic import op

    conn = op.get_bind()

    def count(sql: str, **params) -> int:
        return int(conn.execute(sa.text(sql), params).scalar() or 0)

    vectors = count("SELECT COUNT(*) FROM images WHERE clip_embedding IS NOT NULL")
    markers = {
        t: count(f"SELECT COUNT(*) FROM {t} WHERE aesthetic_model = :m", m=OLD_MARKER)  # noqa: S608 — literal table
        for t in MARKER_TABLES
    }
    watermarks = {
        t: count(f"SELECT COUNT(*) FROM {t} WHERE watermark_score IS NOT NULL")  # noqa: S608 — literal table
        for t in WATERMARK_TABLES
    }
    if not vectors and not any(markers.values()) and not any(watermarks.values()):
        return

    backup = _backup(conn)
    print("Updating stored CLIP results for the QuickGELU fix; on a large library this "
          "takes a few minutes (about 3 for a million images).", flush=True)
    # One UPDATE per table, not one per change: each pass rewrites every row it touches,
    # and an `images` row carries ~20 KB of embedding blobs. On a 1M-row, 17 GB bench
    # library (`bench_scaling.py`, photo profile) three passes took 350 s and this one
    # 166 s, plus 28 s for the backup. Every SET
    # expression reads the row's *old* values, so the CASEs below see the marker and
    # watermark score as they were before this statement.
    params = {"new": NEW_MARKER, "old": OLD_MARKER}
    conn.execute(sa.text(
        "UPDATE images SET "
        "clip_embedding = NULL, "
        "aesthetic_model = CASE WHEN aesthetic_model = :old THEN :new ELSE aesthetic_model END, "
        # json_remove(NULL, ...) is NULL, so a row with no flags stays that way.
        "quality_flags = CASE WHEN watermark_score IS NOT NULL "
        "THEN json_remove(quality_flags, '$.has_watermark') ELSE quality_flags END, "
        "watermark_score = NULL "
        "WHERE clip_embedding IS NOT NULL OR aesthetic_model = :old OR watermark_score IS NOT NULL"
    ), params)
    conn.execute(sa.text(
        "UPDATE version_image_states SET "
        "aesthetic_model = CASE WHEN aesthetic_model = :old THEN :new ELSE aesthetic_model END, "
        "quality_flags = CASE WHEN watermark_score IS NOT NULL "
        "THEN json_remove(quality_flags, '$.has_watermark') ELSE quality_flags END, "
        "watermark_score = NULL "
        "WHERE aesthetic_model = :old OR watermark_score IS NOT NULL"
    ), params)

    print("CLIP is now built with QuickGELU, the activation its weights were trained with.")
    if vectors:
        print(f"  Cleared {vectors} CLIP style embedding(s) made by the old build. Re-embed them "
              "from the Quality page: Score what's missing, with Style embeddings ticked.")
    if markers["images"]:
        print(f"  {markers['images']} LAION aesthetic score(s) are now labelled 'LAION (old build)'. "
              "Re-score them from the Quality page with the LAION model selected.")
    if watermarks["images"]:
        print(f"  Cleared {watermarks['images']} watermark score(s) and their watermark flags. "
              "Re-check them from the Quality page: Score what's missing, with Watermark ticked.")
    if backup:
        print(f"  The database before this change is at {backup}.")


def downgrade() -> None:
    import sqlalchemy as sa
    from alembic import op

    conn = op.get_bind()
    for table in MARKER_TABLES:
        conn.execute(
            sa.text(f"UPDATE {table} SET aesthetic_model = :old WHERE aesthetic_model = :new"),  # noqa: S608
            {"new": NEW_MARKER, "old": OLD_MARKER},
        )
    # The cleared vectors and watermark results are not restored: only the
    # pre-change backup holds them.
