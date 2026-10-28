"""add the version_image_states.detections mirror (GPT-026)

Revision ID: a6d2e8f4c1b3
Revises: e3b7a9c2d4f1
Create Date: 2026-10-28

A snapshot now records each image's `detections` rows, and a restore writes them
back, so detection geometry stays in the frame of the pixels the restore put on
disk.

Two things worth noting about the DDL:

- The column is **nullable with no server default**, unlike `label_ids`
  (c2a8f6b3d417). Existing rows stay NULL, which the restore reads as "not
  recorded" and leaves that image's detections untouched — today's behaviour for
  every snapshot taken before this release. A `'[]'` default would instead read
  as "recorded: no detections" and wipe them on restoring an old snapshot, and a
  backfill from today's rows would record geometry in a pixel frame the snapshot
  may never have had. So no data is migrated.
- The upgrade is a plain ADD COLUMN (SQLite supports it). The *downgrade* needs
  `batch_alter_table` because a column drop is a SQLite table rebuild — cf.
  f4a5b6c7d8e9.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6d2e8f4c1b3"
down_revision: str | Sequence[str] | None = "e3b7a9c2d4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "version_image_states",
        sa.Column("detections", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("version_image_states") as batch_op:
        batch_op.drop_column("detections")
