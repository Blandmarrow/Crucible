"""dataset health report and notes (M12 phase 1)

- `datasets.goal` (what the dataset is for), `datasets.target_resolution` (the short
  side it trains at) and `datasets.health_summary` (the last report's severity counts,
  for the card badge).
- `dataset_versions.findings_summary`: the findings when a snapshot was taken, so the
  Health page can show "was N at vX". Existing snapshots get NULL, "no baseline".
- `dataset_note_items`, `app_note_items` and `note_item_history`: the notes, stored as
  separate items with soft delete; the history is the undo.

Foreign keys are table-level `ForeignKeyConstraint`s in `create_table`, never
column-level inline ones (see c2a8f6b3d417). The new columns are plain ADD COLUMNs;
only the downgrade needs `batch_alter_table`, since a column drop is a table rebuild.

Revision ID: a9c4e2f7b1d8
Revises: f3a8d1c6b9e2
Create Date: 2026-11-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e2f7b1d8"
down_revision: str | Sequence[str] | None = "f3a8d1c6b9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("goal", sa.Text(), nullable=False, server_default=""))
    op.add_column("datasets", sa.Column("target_resolution", sa.Integer(), nullable=True))
    op.add_column("datasets", sa.Column("health_summary", sa.JSON(), nullable=True))
    op.add_column("dataset_versions", sa.Column("findings_summary", sa.JSON(), nullable=True))

    op.create_table(
        "dataset_note_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("finding_id", sa.String(length=64), nullable=True),
        sa.Column("fingerprint", sa.JSON(), nullable=True),
        sa.Column("permanent", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_dataset_note_items_dataset_id"), "dataset_note_items", ["dataset_id"], unique=False
    )

    op.create_table(
        "app_note_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "note_item_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dataset_item_id", sa.String(length=36), nullable=True),
        sa.Column("app_item_id", sa.String(length=36), nullable=True),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("before_text", sa.Text(), nullable=True),
        sa.Column("after_text", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(dataset_item_id IS NULL) != (app_item_id IS NULL)",
            name="ck_note_history_one_item",
        ),
        sa.ForeignKeyConstraint(["app_item_id"], ["app_note_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_item_id"], ["dataset_note_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_note_item_history_app_item_id"), "note_item_history", ["app_item_id"], unique=False
    )
    op.create_index(
        op.f("ix_note_item_history_dataset_item_id"),
        "note_item_history",
        ["dataset_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_note_item_history_dataset_item_id"), table_name="note_item_history")
    op.drop_index(op.f("ix_note_item_history_app_item_id"), table_name="note_item_history")
    op.drop_table("note_item_history")
    op.drop_table("app_note_items")
    op.drop_index(op.f("ix_dataset_note_items_dataset_id"), table_name="dataset_note_items")
    op.drop_table("dataset_note_items")
    with op.batch_alter_table("dataset_versions") as batch_op:
        batch_op.drop_column("findings_summary")
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_column("health_summary")
        batch_op.drop_column("target_resolution")
        batch_op.drop_column("goal")
