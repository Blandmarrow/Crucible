"""add clip_weight/dino_weight to style_similarity_runs

Revision ID: c2b8e4f6a1d7
Revises: b1a7f3d5c2e9
Create Date: 2026-08-04

The descriptor records which mode and which layer produced the values in
`images.style_similarity_score`, but a `combined` run is also a *blend*, and the
blend weights were a constant in the code rather than a fact about the run. That
was survivable only while the constant never moved. It is about to: the measured
retune takes `combined` from 0.38/0.62 to 0.30/0.70. Without these columns, every
row written on either side of that change reads identically, and two scores that
are not comparable become indistinguishable rather than merely different.

**This migration deliberately lands before the retune**, so no run exists whose
weights are unrecorded. A bisect that stopped between the two would otherwise
leave rows whose meaning is unknowable.

**Nullable, and NULL carries two meanings** — "written before these columns
existed" and "this mode does not blend" (`clip`, `dino`, `dino_all_layers`). That
is safe rather than sloppy because `embedding_type` sits in the same row and
disambiguates them: a NULL weight on a `combined` row is a pre-columns run, and a
NULL weight on a `clip` row is the only correct value. No backfill: a `combined`
row already in the table was scored at 0.38/0.62, but writing that in would claim
a record where there is only an inference, and the UI's "not recorded" wording
already covers the case.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2b8e4f6a1d7"
down_revision: str | Sequence[str] | None = "b1a7f3d5c2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("style_similarity_runs", sa.Column("clip_weight", sa.Float(), nullable=True))
    op.add_column("style_similarity_runs", sa.Column("dino_weight", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("style_similarity_runs", "dino_weight")
    op.drop_column("style_similarity_runs", "clip_weight")
