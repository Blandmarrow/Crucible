"""add_nsfw_score_and_threshold

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("images", sa.Column("nsfw_score", sa.Float(), nullable=True))
    op.add_column(
        "threshold_settings",
        sa.Column("nsfw_threshold", sa.Float(), server_default="0.5", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "nsfw_threshold")
    op.drop_column("images", "nsfw_score")
