"""llm_gpu_handoff

Settings → ComfyUI's "unload a local Strata server while ComfyUI generates" (M6 §9).

Revision ID: a3c7e9b1d5f2
Revises: e7d1c5a9b3f2
Create Date: 2026-11-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3c7e9b1d5f2"
down_revision: Union[str, None] = "e7d1c5a9b3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default="0": off for every existing install, matching threshold_service.DEFAULTS.
    # On a card with room for both, unloading would only cost a reload.
    op.add_column(
        "threshold_settings",
        sa.Column("llm_gpu_handoff", sa.Boolean(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "llm_gpu_handoff")
