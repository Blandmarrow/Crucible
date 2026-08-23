"""add_auto_unload_after_scoring

Revision ID: b5e8c1a3d907
Revises: a4d7e2f9c188
Create Date: 2026-08-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5e8c1a3d907"
down_revision: Union[str, None] = "a4d7e2f9c188"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default="1": existing rows get the on behavior, matching the new
    # install default in threshold_service.DEFAULTS.
    op.add_column(
        "threshold_settings",
        sa.Column(
            "auto_unload_after_scoring",
            sa.Boolean(),
            server_default="1",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "auto_unload_after_scoring")
