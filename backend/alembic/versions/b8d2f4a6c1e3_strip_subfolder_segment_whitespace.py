"""strip subfolder segment whitespace: rewrite every stored virtual-folder path

Revision ID: b8d2f4a6c1e3
Revises: a3c7e9b1d5f2
Create Date: 2026-11-08 00:00:00.000000

Decision 12: `utils._subfolder_parts` now strips the whitespace around each segment, so
`knight ` and `knight` are one gallery folder (interior spaces are kept). Rows written
before kept the padding, and every folder match is an exact compare, so this rewrites
them by the same rule, inlined because a migration must not import app code that later
changes. It is `join_subfolder`'s form, so a `..` segment is dropped (`strip_segments`
says why):

- strings: `images.subfolder`, `comfy_rows.subfolder` and the snapshot mirror
  `version_image_states.subfolder` (rewritten too, or restoring an old snapshot would
  bring the padded folder back);
- JSON lists: `datasets.declared_subfolders` and `comfy_plans.declared_folders`, each
  entry stripped, blanks dropped, duplicates removed in order;
- JSON recipes: `runs.recipe` and `run_recipes.recipe` -> `generate.subfolder`.

`comfy_leaves.path` is built with its segments already stripped, so it is only counted;
rewriting it could collide on its `(blueprint_id, path)` unique index.

Two folders that differed only by padding merge, which is the point. Nothing on disk
moves (gallery folders are labels), and `updated_at` is left alone. Before the first
write, and only when there is one, the database is copied to
`backups/<stem>.pre-subfolder-strip.db` (outside `db_maintenance`'s rotation glob, as
`write_named_backup_sync` names its copies); a failed copy raises, so the upgrade stops
rather than rewriting unprotected rows. Not reversible: `downgrade` is a no-op.

The `plan_*` functions are pure and tested directly
(`test_migration_strip_subfolder_segments.py`), which is why `op` and `sqlalchemy` are
imported inside `upgrade()`: CI's pytest install has no alembic.
"""
import json
from collections.abc import Iterable, Sequence

revision: str = "b8d2f4a6c1e3"
down_revision: str | None = "a3c7e9b1d5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHUNK = 1000
BACKUP_TAG = "subfolder-strip"

# (table, column) holding one folder path per row.
STRING_COLUMNS = (
    ("images", "subfolder"),
    ("comfy_rows", "subfolder"),
    ("version_image_states", "subfolder"),
)
# (table, column) holding a JSON list of folder paths.
LIST_COLUMNS = (
    ("datasets", "declared_subfolders"),
    ("comfy_plans", "declared_folders"),
)
# (table, column) holding a JSON run recipe whose `generate.subfolder` is a folder path.
RECIPE_COLUMNS = (
    ("runs", "recipe"),
    ("run_recipes", "recipe"),
)


def strip_segments(raw: str) -> str:
    """`utils.join_subfolder`'s rule as of decision 12: backslashes folded, each segment
    stripped, blank, `.` and `..` segments dropped.

    `..` is dropped, not kept, because stripping is what would otherwise *make* one: the
    old rule stored ` .. ` as an ordinary name, and a stored `..` is refused by every
    route that normalizes its folder (rename, move, delete, the job filters) while a
    run files that row's images where `join_subfolder` puts them, without it. Dropping
    it also repairs the `..` rows written before the write-time guard existed."""
    parts = (p.strip() for p in raw.replace("\\", "/").split("/"))
    return "/".join(p for p in parts if p and p not in (".", ".."))


def _load(value):
    """A JSON column as SQLite hands it back: text, or already decoded."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def plan_strings(rows: Iterable[tuple[str, str | None]]) -> list[tuple[str, str]]:
    """`(id, path)` rows -> `(id, new_path)` for each path the rule changes."""
    return [(row_id, new) for row_id, path in rows
            if path and (new := strip_segments(path)) != path]


def plan_lists(rows: Iterable[tuple[str, object]]) -> list[tuple[str, list]]:
    """`(id, json list)` rows -> `(id, new_list)` for each list the rule changes.
    Entries are stripped, emptied ones dropped and duplicates removed, first kept."""
    updates: list[tuple[str, list]] = []
    for row_id, value in rows:
        entries = _load(value)
        if not isinstance(entries, list):
            continue
        new = list(dict.fromkeys(
            s for e in entries
            if (s := strip_segments(e) if isinstance(e, str) else e) not in ("", None)
        ))
        if new != entries:
            updates.append((row_id, new))
    return updates


def plan_recipes(rows: Iterable[tuple[str, object]]) -> list[tuple[str, dict]]:
    """`(id, json recipe)` rows -> `(id, new_recipe)` where `generate.subfolder` changes."""
    updates: list[tuple[str, dict]] = []
    for row_id, value in rows:
        recipe = _load(value)
        generate = recipe.get("generate") if isinstance(recipe, dict) else None
        sub = generate.get("subfolder") if isinstance(generate, dict) else None
        if not isinstance(sub, str) or (new := strip_segments(sub)) == sub:
            continue
        updates.append((row_id, {**recipe, "generate": {**generate, "subfolder": new}}))
    return updates


def _backup(conn) -> str | None:
    """Copy the database before the first write. Returns the copy's path, or None for
    a database with no file (in-memory). Raises if the copy fails."""
    import os
    import sqlite3
    from pathlib import Path

    main = next((r[2] for r in conn.exec_driver_sql("PRAGMA database_list") if r[1] == "main"), "")
    if not main:
        return None
    raw = conn.connection.dbapi_connection
    # sqlite3's backup() retries SQLITE_BUSY for ever, and a source connection holding
    # a write transaction gets exactly that: the upgrade would hang with no message.
    # Today nothing has written by this point (alembic's SQLite impl commits each
    # revision, and this one only reads first), so this guards a future edit.
    if raw.in_transaction:
        raise RuntimeError("b8d2f4a6c1e3: the connection holds a write transaction, so the "
                           "pre-change backup cannot be taken; nothing was rewritten")
    path = Path(main)
    dest = path.parent / "backups" / f"{path.stem}.pre-{BACKUP_TAG}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        # Through the migration's own connection, which has read but not written.
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

    def rows(table: str, column: str, where: str = ""):
        # Streamed, not fetched: the snapshot table can hold millions of rows.
        return conn.execute(sa.text(f"SELECT id, {column} FROM {table} {where}"))

    planned: list[tuple[str, str, list]] = []
    for table, column in STRING_COLUMNS:
        planned.append((table, column, plan_strings(
            (r[0], r[1]) for r in rows(table, column, f"WHERE {column} != ''"))))
    for table, column in LIST_COLUMNS:
        planned.append((table, column, [(i, json.dumps(v)) for i, v in plan_lists(
            (r[0], r[1]) for r in rows(table, column))]))
    for table, column in RECIPE_COLUMNS:
        planned.append((table, column, [(i, json.dumps(v)) for i, v in plan_recipes(
            (r[0], r[1]) for r in rows(table, column))]))

    padded_leaves = sum(1 for r in rows("comfy_leaves", "path") if r[1] and strip_segments(r[1]) != r[1])
    if padded_leaves:
        print(f"WARNING: {padded_leaves} blueprint leaf path(s) are not in the stripped form "
              "and were left as they are; re-apply their blueprint to rebuild them.")

    if not any(updates for _, _, updates in planned):
        return

    backup = _backup(conn)
    for table, column, updates in planned:
        if not updates:
            continue
        stmt = sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :id")
        params = [{"id": row_id, "v": value} for row_id, value in updates]
        for start in range(0, len(params), _CHUNK):
            conn.execute(stmt, params[start : start + _CHUNK])
        print(f"Stripped folder-segment whitespace in {len(updates)} {table}.{column} row(s).")
    if backup:
        print(f"The database before this change is at {backup}.")


def downgrade() -> None:
    # The strip is not reversible: which rows were padded is not recorded.
    pass
