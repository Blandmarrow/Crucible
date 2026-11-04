"""run form

The Runs page's start form as last left (M6 section 6), kept on the settings row
like the captioning workflow. NULL means never saved.

Revision ID: c4a8e2f6b9d3
Revises: b2e7c9a4d1f6
Create Date: 2026-11-04 20:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a8e2f6b9d3"
down_revision: str | None = "b2e7c9a4d1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("threshold_settings", sa.Column("run_form", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("threshold_settings") as batch_op:
        batch_op.drop_column("run_form")
