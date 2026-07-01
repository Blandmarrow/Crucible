"""add_auto_rescan_on_open

Revision ID: b9d0e1f2a3c4
Revises: a8c3e1f2b9d0
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9d0e1f2a3c4"
down_revision: Union[str, None] = "a8c3e1f2b9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "threshold_settings",
        sa.Column("auto_rescan_on_open", sa.Boolean(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "auto_rescan_on_open")
