"""run cancel_requested

A run's cancel asked for while its snapshot stage runs (M6 section 6). Snapshot jobs
never stop partway, so the run is cancelled when the snapshot ends instead of reading
cancelled while the job still writes. Existing rows get false.

Revision ID: a6d2f8c4e0b3
Revises: c8e4a6b2d0f7
Create Date: 2026-11-04 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6d2f8c4e0b3"
down_revision: str | None = "c8e4a6b2d0f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("cancel_requested")
