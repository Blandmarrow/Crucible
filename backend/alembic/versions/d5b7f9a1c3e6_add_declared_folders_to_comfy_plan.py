"""add declared_folders to comfy_plans

Revision ID: d5b7f9a1c3e6
Revises: c4a8e2f6b9d3
Create Date: 2026-11-05 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5b7f9a1c3e6"
down_revision: str | None = "c4a8e2f6b9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comfy_plans",
        sa.Column("declared_folders", sa.JSON, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("comfy_plans", "declared_folders")
