#!/usr/bin/env python3
"""Drift check: do the Alembic migrations still build the schema the models declare?

Run from anywhere, with the venv active (it imports alembic, SQLAlchemy and the
models):

    python scripts/check_migrations.py

Checks (each sets a non-zero exit code):
  1. FAIL  the revision graph has more than one head
  2. FAIL  `alembic upgrade head` does not run clean on an empty throwaway DB
  3. FAIL  autogenerate finds a schema difference that is not in ACCEPTED_DRIFT
  4. WARN  an ACCEPTED_DRIFT entry no longer appears (stale — delete the line)

Why a throwaway DB and an env override: the app's default `database_url` points at
the live `dataset_manager.db` at the repo root, and this check must never touch it.
`backend/config.py` reads `DATABASE_URL` from the environment and `alembic/env.py`
resolves the URL lazily inside `get_url()`, so setting it before the first import is
enough — the same override `frontend/e2e/serve.sh` uses.

Why the chdir: revision d9f4a1c7b2e8 imports `backend.utils` at module scope, which
only resolves once `env.py` has put the repo root on `sys.path`. Running alembic from
any directory but `backend/` crashes before that happens.

Why ACCEPTED_DRIFT is a list of exact fingerprints and not a filter by diff *kind*:
this repo starts from 14 pre-existing differences, none of them SQLite reflection
noise — they are real, deliberate decisions recorded in the migrations (see the
grouped comments below). A category filter ("ignore modify_nullable") would also
swallow the next one, which is the whole point of the check. Every entry is one
fingerprint; anything unlisted fails.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- Accepted, pre-existing drift -----------------------------------------
#
# A fingerprint here means "the models and the migrations genuinely disagree, and
# the migrations are right". Do not add to it to make a red check green: the
# normal fix for new drift is a migration.
ACCEPTED_DRIFT = {
    # Columns typed non-Optional on the model (`Mapped[dict]`, `Mapped[datetime]`)
    # so SQLAlchemy infers NOT NULL, while the CREATE TABLE that shipped them left
    # them nullable. Every one is always populated by a Python-side default, and
    # adding NOT NULL on SQLite means a full table rebuild — not worth the risk.
    "modify_nullable:comfy_plans.workflow_json:True->False",
    "modify_nullable:comfy_plans.pinned_params:True->False",
    "modify_nullable:comfy_plans.output_node_ids:True->False",
    "modify_nullable:comfy_rows.values:True->False",
    "modify_nullable:comfy_rows.image_ids:True->False",
    "modify_nullable:dataset_branches.created_at:True->False",
    "modify_nullable:dataset_versions.description:True->False",
    "modify_nullable:dataset_versions.created_at:True->False",
    # Circular self-references between dataset_branches.head_version_id and
    # dataset_versions.parent_id/id. The models declare them (they document the
    # relationship and drive the ORM); migration a2b3c4d5e6f7 created both columns
    # as plain String(36) on purpose, because SQLite cannot add a FK after the fact
    # and a use_alter cycle is not expressible in one CREATE TABLE pass.
    "add_fk:dataset_branches:head_version_id",
    "add_fk:dataset_versions:parent_id",
    # Indexes created by migration and never mirrored onto the model's
    # __table_args__. Dropping them would cost query performance; they stay.
    "remove_index:dataset_versions:uq_version_branch_name",   # d6e7f8a9b0c1 (per-branch name uniqueness)
    "remove_index:images:ix_images_dataset_caption",          # a1b2c3d4e5f6 (performance indexes)
    "remove_index:images:ix_images_dataset_created_at",       # a1b2c3d4e5f6
    "remove_index:images:ix_images_file_path",                # a1b2c3d4e5f6
}


def _fingerprint(diff) -> str:
    """Stable one-line identity for an autogenerate diff.

    Deliberately coarse — table + column/constraint name and, for alterations, the
    direction of the change. Fine enough that two different problems never collide,
    stable enough that an unrelated model edit does not invalidate the baseline.
    """
    if isinstance(diff, list):
        # Column alterations arrive as a list of ops against the same column.
        return " | ".join(_fingerprint(d) for d in diff)
    if not isinstance(diff, tuple) or not diff:
        return repr(diff)
    kind = diff[0]
    if kind in ("add_index", "remove_index"):
        idx = diff[1]
        return f"{kind}:{idx.table.name}:{idx.name}"
    if kind in ("add_fk", "remove_fk"):
        fk = diff[1]
        return f"{kind}:{fk.table.name}:{','.join(fk.column_keys)}"
    if kind in ("add_constraint", "remove_constraint"):
        con = diff[1]
        return f"{kind}:{getattr(con.table, 'name', '?')}:{con.name}"
    if kind in ("add_column", "remove_column"):
        return f"{kind}:{diff[2]}:{diff[3].name}"
    if kind in ("add_table", "remove_table"):
        return f"{kind}:{diff[1].name}"
    if kind.startswith("modify_"):
        # (kind, schema, table, column, kwargs, existing, new)
        return f"{kind}:{diff[2]}.{diff[3]}:{diff[5]!r}->{diff[6]!r}"
    return f"{kind}:{diff[1:]!r}"


def main() -> int:
    tmp_dir = Path(tempfile.mkdtemp(prefix="crucible-migcheck-"))
    sys.path.insert(0, str(REPO_ROOT))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_dir / 'check.db'}"
    os.chdir(REPO_ROOT / "backend")

    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    cfg = Config(str(REPO_ROOT / "backend" / "alembic.ini"))

    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) != 1:
        print(f"FAIL  expected exactly one migration head, found {len(heads)}: {list(heads)}")
        print("      Two branches added a migration in parallel — merge them with `alembic merge`.")
        return 1  # a multi-head graph cannot be upgraded, so stop here

    try:
        command.upgrade(cfg, "head")
    except Exception as exc:  # noqa: BLE001 — surface whatever alembic raised
        print(f"FAIL  `alembic upgrade head` failed on an empty database: {exc}")
        return 1

    import backend.models  # noqa: F401 — register every model on Base
    from backend.database import Base

    # compare_metadata logs an INFO line per difference it finds, including every
    # accepted one — in CI that reads like 14 failures under a green check. The
    # script's own output is the report; keep the upgrade log, drop this.
    for name in ("alembic.autogenerate", "alembic.autogenerate.compare", "alembic.runtime.plugins"):
        logging.getLogger(name).setLevel(logging.WARNING)

    engine = create_engine(f"sqlite:///{tmp_dir / 'check.db'}")
    with engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()

    seen = {_fingerprint(d) for d in diffs}
    unexpected = sorted(seen - ACCEPTED_DRIFT)
    stale = sorted(ACCEPTED_DRIFT - seen)

    for entry in stale:
        print(f"WARN  ACCEPTED_DRIFT entry no longer applies, delete it: {entry}")

    if unexpected:
        print(f"FAIL  {len(unexpected)} schema difference(s) between the migrations and the models:")
        for entry in unexpected:
            print(f"      {entry}")
        print(
            "\n      The models changed without a matching migration. Generate one from\n"
            "      backend/ with `alembic revision --autogenerate -m \"...\"`, then trim it\n"
            "      to the intended ops — autogenerate also re-emits the accepted drift\n"
            "      listed at the top of this script."
        )
        return 1

    print(f"OK  single head ({heads[0]}), upgrade clean, no new schema drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
