"""add the case-insensitive uniqueness index on labels.name

Revision ID: a4d7e2f9c188
Revises: c2a8f6b3d417
Create Date: 2026-08-05

`labels.name` already carries `UNIQUE`, but SQLite's default collation is
case-sensitive, so that constraint only backstops an *exact-case* duplicate:
"Reject" and "reject" both persist. The router pre-checks with
`func.lower(name) == …` and answers 409, and this index is what makes that check
true rather than merely conventional — a lost race between two concurrent creates
now hits a constraint (turned back into a 409 by `_commit_unique`) instead of
writing the second row.

A functional index rather than `COLLATE NOCASE` on the column: the collation
would also change every ORDER BY and comparison on `name`, and it would need a
SQLite table rebuild to apply.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4d7e2f9c188"
down_revision: str | Sequence[str] | None = "c2a8f6b3d417"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Raw DDL: `op.create_index` renders its expressions through the column list,
    # which cannot express `lower(name)`.
    op.execute("CREATE UNIQUE INDEX uq_labels_name_lower ON labels (lower(name))")


def downgrade() -> None:
    op.execute("DROP INDEX uq_labels_name_lower")
