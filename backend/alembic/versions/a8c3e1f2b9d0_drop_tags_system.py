"""drop tags system

Revision ID: a8c3e1f2b9d0
Revises: e8f9a0b1c2d3
Create Date: 2026-06-22

"""
import sqlalchemy as sa
from alembic import op

revision = "a8c3e1f2b9d0"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    result = bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"), {"t": table_name})
    return result.fetchone() is not None


def _column_exists(bind, table_name: str, col_name: str) -> bool:
    rows = bind.execute(sa.text(f"PRAGMA table_info({table_name})")).all()
    return any(row[1] == col_name for row in rows)


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "tags"):
        op.drop_table("tags")

    if _table_exists(bind, "images") and _column_exists(bind, "images", "tags_json"):
        op.drop_column("images", "tags_json")

    if _table_exists(bind, "dataset_version_image_states") and _column_exists(bind, "dataset_version_image_states", "tags_json"):
        op.drop_column("dataset_version_image_states", "tags_json")


def downgrade() -> None:
    raise NotImplementedError(
        "This migration is irreversible — tags_json columns and the tags table were dropped "
        "with no rollback path. To downgrade, restore from a database backup taken before "
        "running this migration."
    )
