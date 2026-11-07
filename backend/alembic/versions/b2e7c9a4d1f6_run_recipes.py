"""run recipes

The run recipe library (M6 section 6): named, global recipes saved from the Runs
page. No dataset or plan foreign key, like the prompt library. `name_key` is the name
folded for case and spaces, unique, so "Night set" and "night  set" are one recipe.

Revision ID: b2e7c9a4d1f6
Revises: a6d2f8c4e0b3
Create Date: 2026-11-04 19:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2e7c9a4d1f6"
down_revision: str | None = "a6d2f8c4e0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_recipes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("name_key", sa.String(length=120), nullable=False),
        sa.Column("recipe", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name_key"),
    )


def downgrade() -> None:
    op.drop_table("run_recipes")
