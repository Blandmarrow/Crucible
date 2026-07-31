"""index images on (source_video_id, source_timestamp_ms)

The gallery can now sort by frame lineage — "Video timeline" and "Shot order" —
and that sort is almost always reached through the frames-from-video filter, so
the query is `WHERE source_video_id = ? ORDER BY source_timestamp_ms`. This
composite serves that as an index scan, and the leading column alone still serves
the unfiltered whole-dataset timeline sort's nulls-last walk.

`ix_images_source_video_id` (from `a7c3e5b1d9f2`) is now a redundant prefix of
this one. It is deliberately kept: `DELETE /videos/{id}` NULLs the column with a
bulk UPDATE and `frames-summary` groups by it, so dropping it is a separate
change with its own plan-checking, not a free tidy-up alongside a new index.

Plain `create_index` — no table rebuild, so nothing here interacts with the
`PRAGMA foreign_keys` reasoning in `a7c3e5b1d9f2`'s docstring.

Revision ID: c8a1d3f5b7e2
Revises: b2f6c8d0e1a4
Create Date: 2026-07-31

"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c8a1d3f5b7e2"
down_revision: str | Sequence[str] | None = "b2f6c8d0e1a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_images_source_video_timeline",
        "images",
        ["source_video_id", "source_timestamp_ms"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_images_source_video_timeline", table_name="images")
