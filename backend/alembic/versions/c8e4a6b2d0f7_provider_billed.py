"""provider billed

Per-provider "billed" setting for OpenAI-compatible LLM providers. Automated runs
(M6 section 5) cap LLM calls only on paid providers, and the app cannot know which
bill: the remote-URL test counts a LAN LM Studio as paid. NULL means "follow the
URL", so existing rows keep today's behaviour.

Revision ID: c8e4a6b2d0f7
Revises: f3b9d1a7c5e2
Create Date: 2026-11-04 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e4a6b2d0f7"
down_revision: str | None = "f3b9d1a7c5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("openai_providers") as batch_op:
        batch_op.add_column(sa.Column("billed", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("openai_providers") as batch_op:
        batch_op.drop_column("billed")
