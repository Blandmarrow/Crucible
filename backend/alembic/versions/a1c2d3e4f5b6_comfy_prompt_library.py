"""comfy_library_prompts: global prompt library with free-text categories

Revision ID: a1c2d3e4f5b6
Revises: a9b0c1d2e3f4
Create Date: 2026-07-14

Prompts saved from plan queues (or added directly) for reuse across plans
and datasets, grouped by a free-text category.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2d3e4f5b6"
down_revision: str | Sequence[str] | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comfy_library_prompts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_comfy_library_prompts_category"),
        "comfy_library_prompts",
        ["category"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_comfy_library_prompts_category"), table_name="comfy_library_prompts")
    op.drop_table("comfy_library_prompts")
