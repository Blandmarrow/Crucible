"""fix quoted versioning_mode default

Revision ID: a9b0c1d2e3f4
Revises: f4a5b6c7d8e9
Create Date: 2026-07-13

The original a2b3c4d5e6f7 migration used server_default="'off'", which SQLAlchemy
renders as the quoted SQL literal '''off''' — so every DB it created backfilled
threshold_settings.versioning_mode with the 5-char value 'off' (quotes included),
breaking every `mode == "off"` guard. Repair the data, then rebuild the baked-in
column default (SQLite stores DEFAULT in the table DDL, so fixing the migration
file alone only helps fresh installs).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE threshold_settings SET versioning_mode = 'off' "
        "WHERE versioning_mode = '''off'''"
    )
    with op.batch_alter_table("threshold_settings") as batch_op:
        batch_op.alter_column(
            "versioning_mode",
            existing_type=sa.String(16),
            server_default="off",
            existing_nullable=False,
        )


def downgrade() -> None:
    pass  # data repair — not reversible
