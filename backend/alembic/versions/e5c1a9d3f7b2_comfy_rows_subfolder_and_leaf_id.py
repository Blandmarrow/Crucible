"""comfy_rows subfolder and leaf_id

Gives each queued generation its own destination folder, so a run files each
row's outputs under `{run base folder}/{row folder}` instead of piling every
image into one place. `leaf_id` is column-only for now — it names the blueprint
leaf that minted the row once structured generation exists — and carries no
foreign key on purpose: a row whose leaf was deleted keeps naming it as
provenance, and an inline column-level reference would read back off SQLite with
no `ondelete`, which `scripts/check_migrations.py` reports as permanent drift.

Revision ID: e5c1a9d3f7b2
Revises: a4d7e2f9c188
Create Date: 2026-08-15 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5c1a9d3f7b2"
down_revision: str | None = "a4d7e2f9c188"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comfy_rows",
        sa.Column("subfolder", sa.String(512), nullable=False, server_default=""),
    )
    op.add_column("comfy_rows", sa.Column("leaf_id", sa.String(36), nullable=True))
    op.create_index("ix_comfy_rows_leaf_id", "comfy_rows", ["leaf_id"])


def downgrade() -> None:
    op.drop_index("ix_comfy_rows_leaf_id", table_name="comfy_rows")
    op.drop_column("comfy_rows", "leaf_id")
    op.drop_column("comfy_rows", "subfolder")
