"""add detections table

Revision ID: e1f2a3b4c5d6
Revises: b2c4d6e8f0a2
Create Date: 2026-05-20 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "b2c4d6e8f0a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "detections",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("image_id", sa.String(36), sa.ForeignKey("images.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(256), nullable=False),
        sa.Column("bbox", sa.JSON, nullable=False),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("detected_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_detections_image_id", "detections", ["image_id"])
    op.create_index("ix_detections_label", "detections", ["label"])


def downgrade() -> None:
    op.drop_index("ix_detections_label", table_name="detections")
    op.drop_index("ix_detections_image_id", table_name="detections")
    op.drop_table("detections")
