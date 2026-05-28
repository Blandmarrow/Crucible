"""add max_tokens to openai_providers

Revision ID: b4c5d6e7f8a9
Revises: f1e2d3c4b5a6
Create Date: 2026-05-28

"""
from alembic import op
import sqlalchemy as sa

revision = "b4c5d6e7f8a9"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "openai_providers",
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="2048"),
    )


def downgrade() -> None:
    op.drop_column("openai_providers", "max_tokens")
