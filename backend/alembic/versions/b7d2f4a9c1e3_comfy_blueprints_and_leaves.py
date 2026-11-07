"""comfy_blueprints and comfy_leaves

Structured generation (M6 §2): a plan's blueprint (axes, variation pieces and
scenarios) and the leaves it expands to, one per rendered folder. `brief` and
`draft` belong to the outline call (§2b) and arrive here too, so the branch adds
one migration for the whole of §2 rather than three.

`comfy_rows.leaf_id` keeps no foreign key to `comfy_leaves` — see
`e5c1a9d3f7b2`: a row whose leaf an edit deleted keeps naming it as provenance.

Revision ID: b7d2f4a9c1e3
Revises: e5c1a9d3f7b2
Create Date: 2026-11-03 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f4a9c1e3"
down_revision: str | None = "e5c1a9d3f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comfy_blueprints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column("draft", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["comfy_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_comfy_blueprints_plan_id", "comfy_blueprints", ["plan_id"], unique=True)

    op.create_table(
        "comfy_leaves",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("blueprint_id", sa.String(length=36), nullable=False),
        sa.Column("scenario", sa.String(length=100), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column("entity_ids", sa.JSON(), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["blueprint_id"], ["comfy_blueprints.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_comfy_leaves_blueprint_id", "comfy_leaves", ["blueprint_id"])
    op.create_index("uq_comfy_leaves_blueprint_path", "comfy_leaves", ["blueprint_id", "path"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_comfy_leaves_blueprint_path", table_name="comfy_leaves")
    op.drop_index("ix_comfy_leaves_blueprint_id", table_name="comfy_leaves")
    op.drop_table("comfy_leaves")
    op.drop_index("uq_comfy_blueprints_plan_id", table_name="comfy_blueprints")
    op.drop_table("comfy_blueprints")
