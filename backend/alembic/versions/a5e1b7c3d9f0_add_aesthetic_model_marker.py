"""add aesthetic_model marker to images and version_image_states

Revision ID: a5e1b7c3d9f0
Revises: a2f4c6e8b0d1
Create Date: 2026-08-02

`aesthetic_score` gains a second producer (Aesthetic Predictor V2.5, SigLIP-based)
alongside LAION's sac+logos+ava1-l14-linearMSE. The two produce non-comparable
distributions, so every stored score needs to say which model made it.

**Why this one backfills and `scores_stale` did not.** The historical value here
is *knowable*: LAION was the only producer that has ever written this column, so
`aesthetic_model = 'laion' WHERE aesthetic_score IS NOT NULL` is correct rather
than a guess. (`scores_stale` had no such fact available — whether an old row's
pixels had been rewritten since scoring was unrecoverable, so it defaulted to
False and let the next in-place edit set it.) The backfill buys the invariant

    aesthetic_score IS NOT NULL  <=>  aesthetic_model IS NOT NULL

which five consumers rely on instead of each re-deriving a three-way rule
(scored-and-marked / scored-but-unknown / unscored). `routers/quality.py`'s
single write site maintains it: the marker is written in the same guarded block
as the score, and set back to NULL when the score is.

`version_image_states` mirrors it for the same reason it mirrors the ten scores:
a restore writes back exactly what the state row holds, so an unmirrored marker
would be blanked — leaving a restored LAION score under whatever marker the live
row happened to carry.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5e1b7c3d9f0"
down_revision: str | Sequence[str] | None = "a2f4c6e8b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("images", "version_image_states"):
        op.add_column(table, sa.Column("aesthetic_model", sa.String(64), nullable=True))
        op.execute(
            f"UPDATE {table} SET aesthetic_model = 'laion' "  # noqa: S608 — `table` is a literal from the tuple above
            "WHERE aesthetic_score IS NOT NULL"
        )


def downgrade() -> None:
    for table in ("images", "version_image_states"):
        op.drop_column(table, "aesthetic_model")
