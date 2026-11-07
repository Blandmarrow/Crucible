"""comfy_rows prompt_origin

Records where a row's prompt text came from, so the optional LLM rewrite pass
(M6 §3) touches only text that a blueprint template wrote: `template` (blueprint
Apply), `rewritten` (the pass), `manual` (a hand edit through the row endpoints),
NULL (flat generation, bulk add, a hand-added row). Without it the pass could not
tell a template prompt from one the user had already corrected, and would
overwrite the correction.

Rows a blueprint minted before this column existed are backfilled `template`:
Apply is their only writer, and a hand edit since cannot be told apart.

Revision ID: d4a8c2e6f1b9
Revises: b7d2f4a9c1e3
Create Date: 2026-11-03 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a8c2e6f1b9"
down_revision: str | None = "b7d2f4a9c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("comfy_rows", sa.Column("prompt_origin", sa.String(16), nullable=True))
    op.execute("UPDATE comfy_rows SET prompt_origin = 'template' WHERE leaf_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("comfy_rows", "prompt_origin")
