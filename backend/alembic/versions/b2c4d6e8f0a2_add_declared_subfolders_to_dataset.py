"""add declared_subfolders to dataset

Revision ID: b2c4d6e8f0a2
Revises: f3a9b2c1d4e7
Create Date: 2026-05-20 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c4d6e8f0a2"
down_revision: str | None = "f3a9b2c1d4e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("declared_subfolders", sa.JSON, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("datasets", "declared_subfolders")
