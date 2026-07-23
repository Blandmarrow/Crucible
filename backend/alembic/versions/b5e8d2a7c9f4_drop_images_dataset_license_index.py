"""drop the unused ix_images_dataset_license index

Revision ID: b5e8d2a7c9f4
Revises: a4d7c1e9b3f2
Create Date: 2026-07-22

The index was added on the assumption that it would back the license filters, but
every one of them filters on the *effective* license — `COALESCE(images.license,
datasets.license)` — which is not sargable against an index on `images.license`.

`EXPLAIN QUERY PLAN` does show SQLite picking it for the stats breakdown on a
fresh database, but only as a covering scan of `(dataset_id, …)`: any of the six
other `(dataset_id, X)` indexes serves that identically, so the index buys no read
benefit it does not already have — while costing write amplification on every
image insert and update.

Dropped separately from the migration that created it so a revert is cheap.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "b5e8d2a7c9f4"
down_revision: str | Sequence[str] | None = "a4d7c1e9b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_images_dataset_license", table_name="images")


def downgrade() -> None:
    op.create_index("ix_images_dataset_license", "images", ["dataset_id", "license"])
