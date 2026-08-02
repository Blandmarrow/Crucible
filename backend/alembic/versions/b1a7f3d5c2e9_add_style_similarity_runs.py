"""add style_similarity_runs table

Revision ID: b1a7f3d5c2e9
Revises: a5e1b7c3d9f0
Create Date: 2026-08-02

`images.style_similarity_score` is a raw cosine, and nothing recorded which of the
five modes, which DINOv2 layer or which references produced it. The same "0.62"
means a mediocre CLIP match, a strong DINOv2 one, or nothing at all on a
layer-3 run where every image lands in 0.90–0.99. This table is the missing
descriptor: one row per dataset, overwritten by every successful run.

**No backfill, and none is possible.** An already-scored dataset could have used
any of the five modes against any reference set, and neither the mode nor the
references are recoverable from the column. Existing datasets therefore get no
row, and the UI says so explicitly ("run details not recorded") rather than
inventing a mode. `duplicate_dataset` deliberately does not carry the descriptor
across either — the clone's `reference_image_ids` would point at the *source*
dataset's images — so a clone lands in the same "no row" state and gets the same
message.

**Why a table rather than columns on `datasets`.** `datasets.updated_at` is half
the `get_dataset_stats` cache validator, so a descriptor written onto that row
would evict the entire Stats aggregation on every style run; `DatasetOut` is also
hand-built field by field in `list_datasets`, where an omitted column silently
reads as zero. The unique index on `dataset_id` is what makes the write an upsert
rather than a log — the descriptor describes the values currently in the column,
and there is only ever one set of those.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1a7f3d5c2e9"
down_revision: str | Sequence[str] | None = "a5e1b7c3d9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "style_similarity_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("embedding_type", sa.String(length=32), nullable=False),
        sa.Column("dino_layer", sa.Integer(), nullable=True),
        sa.Column("reference_image_ids", sa.JSON(), nullable=True),
        sa.Column("reference_count", sa.Integer(), nullable=False),
        sa.Column("external_reference_count", sa.Integer(), nullable=False),
        sa.Column("scored_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("scoped_image_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_style_similarity_runs_dataset_id"),
        "style_similarity_runs",
        ["dataset_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_style_similarity_runs_dataset_id"), table_name="style_similarity_runs")
    op.drop_table("style_similarity_runs")
