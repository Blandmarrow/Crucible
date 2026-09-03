"""add timeout_s to openai_providers

Revision ID: c8d1f3a5b709
Revises: b5e8c1a3d907
Create Date: 2026-09-03

"""
from alembic import op
import sqlalchemy as sa

revision = "c8d1f3a5b709"
down_revision = "b5e8c1a3d907"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "openai_providers",
        sa.Column("timeout_s", sa.Integer(), nullable=False, server_default="300"),
    )


def downgrade() -> None:
    op.drop_column("openai_providers", "timeout_s")
