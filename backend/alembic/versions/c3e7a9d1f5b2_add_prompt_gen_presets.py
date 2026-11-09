"""add prompt_gen_presets: saved instructions for the Generate prompts modal

Revision ID: c3e7a9d1f5b2
Revises: b8d2f4a6c1e3
Create Date: 2026-11-09 00:00:00.000000

Separate from caption_prompt_presets: the two lists feed different prompts.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3e7a9d1f5b2"
down_revision: Union[str, None] = "b8d2f4a6c1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_gen_presets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("prompt_gen_presets")
