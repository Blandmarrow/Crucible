"""runs

The automated dataset run of M6 §4 (decision 9): one row per run, holding the
recipe it was started with, its stage list and where it has got to. A run is
stepped forward when each child job ends, so it needs a durable record of its own
rather than a job row: it outlives every child, and a restart must find it to set
it `paused` for an explicit resume.

`plan_id` is SET NULL so a finished run stays readable as history after its plan
is deleted. `current_job_id` has no foreign key, because finished job rows are
swept after 30 days. `pause_requested` gets a server default so the column is
NOT NULL from the start.

Revision ID: f3b9d1a7c5e2
Revises: d4a8c2e6f1b9
Create Date: 2026-11-04 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b9d1a7c5e2"
down_revision: str | None = "d4a8c2e6f1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("recipe", sa.JSON(), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("current_job_id", sa.String(length=36), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["comfy_plans.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_dataset_id", "runs", ["dataset_id"])
    op.create_index("ix_runs_created_at", "runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_index("ix_runs_dataset_id", table_name="runs")
    op.drop_table("runs")
