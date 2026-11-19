"""the analyst's tool-call probe result, per provider and model (M12 phase 3b)

- `openai_providers.tools_probe`: JSON, `{model: {ok, checked_at, detail}}`. NULL means
  no model on the provider was ever probed, so the analyst chat offers it no tools.

A nullable column with no foreign key, so a plain ADD COLUMN; the downgrade drops it
through a batch rebuild, as d4b8e2a6c1f9 did for `analyst_privacy`.

Revision ID: bcbc83198eb1
Revises: 8f55ae96965a
Create Date: 2026-11-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bcbc83198eb1"
down_revision: str | Sequence[str] | None = "8f55ae96965a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("openai_providers", sa.Column("tools_probe", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("openai_providers") as batch_op:
        batch_op.drop_column("tools_probe")
