"""add ComfyUI generation queue tables + comfyui_url setting

Revision ID: a1c9e2f4d5b6
Revises: d9f4a1c7b2e8
Create Date: 2026-07-12

Adds comfy_plans (per-dataset workflow template + pinned params) and
comfy_rows (per-row values, status, output image links) for the ComfyUI
generation queue feature, plus the global comfyui_url on threshold_settings.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c9e2f4d5b6"
down_revision: str | Sequence[str] | None = "d9f4a1c7b2e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comfy_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("workflow_json", sa.JSON(), nullable=True),
        sa.Column("pinned_params", sa.JSON(), nullable=True),
        sa.Column("seed_mode", sa.String(length=16), nullable=False, server_default="fixed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_comfy_plans_dataset_id", "comfy_plans", ["dataset_id"])
    op.create_index("uq_comfy_plan_dataset_name", "comfy_plans", ["dataset_id", "name"], unique=True)

    op.create_table(
        "comfy_rows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("image_id", sa.String(length=36), nullable=True),
        sa.Column("image_ids", sa.JSON(), nullable=True),
        sa.Column("prompt_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["comfy_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_comfy_rows_plan_id", "comfy_rows", ["plan_id"])

    op.add_column(
        "threshold_settings",
        sa.Column("comfyui_url", sa.String(length=500), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "comfyui_url")
    op.drop_index("ix_comfy_rows_plan_id", table_name="comfy_rows")
    op.drop_table("comfy_rows")
    op.drop_index("uq_comfy_plan_dataset_name", table_name="comfy_plans")
    op.drop_index("ix_comfy_plans_dataset_id", table_name="comfy_plans")
    op.drop_table("comfy_plans")
