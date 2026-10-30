"""add app-stored prompts: presets, declared categories, workflow, comfy gen settings

Revision ID: f2a6c8e4b1d7
Revises: a6d2e8f4c1b3
Create Date: 2026-10-30 00:00:00.000000

User-written data that lived only in browser storage, where clearing it lost the data
and each browser and origin held its own copy. No backfill is possible here: only a
browser can read what it holds, so `POST /app-data/legacy-import` carries it over.
Both JSON columns start NULL, meaning "never saved", which is the state that import
fills. See docs/dev/decisions/03-app-stored-prompts.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2a6c8e4b1d7"
down_revision: Union[str, None] = "a6d2e8f4c1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "caption_prompt_presets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=500), nullable=False),
        sa.Column("style", sa.String(length=100), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "declared_categories",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.add_column("threshold_settings", sa.Column("captioning_workflow", sa.JSON(), nullable=True))
    op.add_column("comfy_plans", sa.Column("gen_settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("comfy_plans", "gen_settings")
    op.drop_column("threshold_settings", "captioning_workflow")
    op.drop_table("declared_categories")
    op.drop_table("caption_prompt_presets")
