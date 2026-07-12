"""add comfy_workflow_dir to threshold_settings

Revision ID: b6d8e0f1a2c3
Revises: a1c9e2f4d5b6
Create Date: 2026-07-12

Default folder scanned for ComfyUI workflow .json files by the ComfyPage
"Scan folder" feature (Settings -> ComfyUI tab).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6d8e0f1a2c3"
down_revision: str | Sequence[str] | None = "a1c9e2f4d5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "threshold_settings",
        sa.Column("comfy_workflow_dir", sa.String(length=1000), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "comfy_workflow_dir")
