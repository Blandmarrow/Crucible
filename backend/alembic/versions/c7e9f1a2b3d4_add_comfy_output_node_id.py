"""add output_node_id to comfy_plans

Revision ID: c7e9f1a2b3d4
Revises: b6d8e0f1a2c3
Create Date: 2026-07-12

Optional per-plan workflow node whose history outputs are imported (any image
type, including temp previews from PreviewImage nodes). NULL = default
behavior: import all type=="output" images (SaveImage nodes).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e9f1a2b3d4"
down_revision: str | Sequence[str] | None = "b6d8e0f1a2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("comfy_plans", sa.Column("output_node_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("comfy_plans", "output_node_id")
