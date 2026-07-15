"""drop unused tags_json from version_image_states

Revision ID: f4a5b6c7d8e9
Revises: e2b3c4d5f6a7
Create Date: 2026-07-13

The tags_json column was created in a2b3c4d5e6f7 but is referenced by no model,
service, or query — captions are stored in caption_text. Drop the dead column via a
SQLite-safe batch (table-rebuild) migration.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "e2b3c4d5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("version_image_states") as batch_op:
        batch_op.drop_column("tags_json")


def downgrade() -> None:
    with op.batch_alter_table("version_image_states") as batch_op:
        batch_op.add_column(sa.Column("tags_json", sa.JSON(), nullable=True))
