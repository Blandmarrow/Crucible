"""index images on (dataset_id, updated_at)

The Stats page checks its cache on every poll (every 5 s while a job runs, twice
per poll) with `count(*), max(updated_at)` over one dataset
(`dataset_service._image_validator`). No index carried `updated_at`, so each
check read every row of the dataset: 294 ms at 100,000 images, against 7.1 ms
with this index, which answers both from the index alone (M9 stage 11). It also
gives the gallery's `updated_at` sort an index.

The cost is one more index to maintain on every image write, and `updated_at`
moves on every one of them. That is why it came in its own stage rather than
with stage 9's free fixes.

Plain `create_index`, no table rebuild.

Revision ID: d5f1a8c3e7b2
Revises: c3e7a9d1f5b2
Create Date: 2026-11-10

"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d5f1a8c3e7b2"
down_revision: str | Sequence[str] | None = "c3e7a9d1f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_images_dataset_updated_at",
        "images",
        ["dataset_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_images_dataset_updated_at", table_name="images")
