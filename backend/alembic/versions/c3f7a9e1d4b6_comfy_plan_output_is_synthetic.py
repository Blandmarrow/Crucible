"""comfy_plans: per-plan "output is synthetic" toggle

Revision ID: c3f7a9e1d4b6
Revises: b5e8d2a7c9f4
Create Date: 2026-07-22

Replaces the all-or-nothing dataset gate that decided whether ComfyUI output was
stamped synthetic or inherited the dataset's provenance defaults. That gate could
not tell a text2img plan (self-created output) from a derivative one, so a dataset
recording any provenance default silently switched every plan to inheritance.

Defaults to true: the overwhelmingly common case is a plan whose output is its own
work, and true is also what the old gate produced for a dataset asserting nothing.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f7a9e1d4b6"
down_revision: str | Sequence[str] | None = "b5e8d2a7c9f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comfy_plans",
        sa.Column(
            "output_is_synthetic",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("comfy_plans", "output_is_synthetic")
