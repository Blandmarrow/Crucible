"""max concurrent caption requests, per provider and for Ollama

M10 T2: a caption job can keep several requests in flight to an
OpenAI-compatible provider or to Ollama. `openai_providers.max_concurrent` is the
per-provider setting; Ollama has no provider row, so its setting is a
`threshold_settings` column. Both default to 1, today's one request at a time.

Plain `add_column` with a server default, no table rebuild.

Revision ID: e4b9c2d6f1a3
Revises: d5f1a8c3e7b2
Create Date: 2026-11-12

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e4b9c2d6f1a3"
down_revision: str | Sequence[str] | None = "d5f1a8c3e7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "openai_providers",
        sa.Column("max_concurrent", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "threshold_settings",
        sa.Column("ollama_max_concurrent", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("threshold_settings", "ollama_max_concurrent")
    op.drop_column("openai_providers", "max_concurrent")
