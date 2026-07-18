"""add_sam3_threshold

Revision ID: b7d1e4f8a2c5
Revises: a1c2d3e4f5b6
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d1e4f8a2c5"
down_revision: Union[str, None] = "a1c2d3e4f5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "threshold_settings",
        sa.Column("sam3_threshold", sa.Float(), server_default="0.5", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "sam3_threshold")
