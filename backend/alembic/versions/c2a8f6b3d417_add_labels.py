"""add labels, image_labels and the version_image_states.label_ids mirror

Revision ID: c2a8f6b3d417
Revises: b1a7f3d5c2e9
Create Date: 2026-08-05

Labels are a second facet of organisation alongside the subfolder tree: a small,
global, controlled vocabulary of short strings attached to images and filterable
in the gallery and at export. They are deliberately *not* the tags system dropped
in a8c3e1f2b9d0 — a label never touches caption text.

Three things worth noting about the DDL:

- The FKs are declared as **table-level** `sa.ForeignKeyConstraint(...)` inside
  `create_table`, never column-level inline FKs: column-level breaks
  SQLAlchemy's SQLite reflection and causes permanent `check_migrations.py`
  drift.
- `labels.id` is a uuid string, not an autoincrement integer. The id is
  persisted outside its own table (the snapshot mirror, the gallery's persisted
  filter blob, export presets) and SQLite recycles `max(rowid)+1`, so an integer
  id would let a delete-then-create silently reattach the wrong concept.
- `version_image_states.label_ids` is a plain ADD COLUMN (SQLite supports it),
  and its `server_default` of `'[]'` backfills existing rows so no NULL/`[]`
  split appears in the diff. The *downgrade* needs `batch_alter_table` because a
  column drop is a SQLite table rebuild — cf. f4a5b6c7d8e9.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2a8f6b3d417"
down_revision: str | Sequence[str] | None = "b1a7f3d5c2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "labels",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("color", sa.String(length=16), server_default="#6b7280", nullable=False),
        sa.Column("hotkey", sa.String(length=1), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("hotkey"),
    )
    op.create_index("ix_labels_sort_order", "labels", ["sort_order"], unique=False)

    op.create_table(
        "image_labels",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("image_id", sa.String(length=36), nullable=False),
        sa.Column("label_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["label_id"], ["labels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_id", "label_id", name="uq_image_label"),
    )
    op.create_index(op.f("ix_image_labels_image_id"), "image_labels", ["image_id"], unique=False)
    op.create_index(op.f("ix_image_labels_label_id"), "image_labels", ["label_id"], unique=False)

    op.add_column(
        "version_image_states",
        sa.Column("label_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    with op.batch_alter_table("version_image_states") as batch_op:
        batch_op.drop_column("label_ids")
    op.drop_index(op.f("ix_image_labels_label_id"), table_name="image_labels")
    op.drop_index(op.f("ix_image_labels_image_id"), table_name="image_labels")
    op.drop_table("image_labels")
    op.drop_index("ix_labels_sort_order", table_name="labels")
    op.drop_table("labels")
