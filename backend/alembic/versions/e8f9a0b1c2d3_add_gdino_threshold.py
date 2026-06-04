"""add_gdino_threshold

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "threshold_settings",
        sa.Column("gdino_threshold", sa.Float(), server_default="0.35", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "gdino_threshold")
