"""add subfolder to image

Revision ID: f3a9b2c1d4e7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-20 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9b2c1d4e7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column("subfolder", sa.String(512), nullable=False, server_default=""),
    )
    op.create_index("ix_images_dataset_subfolder", "images", ["dataset_id", "subfolder"])


def downgrade() -> None:
    op.drop_index("ix_images_dataset_subfolder", table_name="images")
    op.drop_column("images", "subfolder")
