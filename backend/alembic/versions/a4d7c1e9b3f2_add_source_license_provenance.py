"""add source & license provenance to datasets, images and version_image_states

Revision ID: a4d7c1e9b3f2
Revises: c9e2f4a6b8d1
Create Date: 2026-07-22

Per-image provenance (source_name, source_url, license, attribution, source_meta)
is nullable — NULL means "inherit the dataset default", resolved at read time by
backend/licenses.py::resolve_provenance. The dataset-level defaults are NOT NULL
with a "" server default, so existing rows need no backfill.

version_image_states mirrors the five image columns; without that a snapshot
restore would silently wipe provenance.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4d7c1e9b3f2"
down_revision: str | Sequence[str] | None = "c9e2f4a6b8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("source_name", sa.String(255), nullable=False, server_default=""))
    op.add_column("datasets", sa.Column("source_url", sa.String(1024), nullable=False, server_default=""))
    op.add_column("datasets", sa.Column("license", sa.String(64), nullable=False, server_default=""))
    op.add_column("datasets", sa.Column("attribution", sa.Text(), nullable=False, server_default=""))

    for table in ("images", "version_image_states"):
        op.add_column(table, sa.Column("source_name", sa.String(255), nullable=True))
        op.add_column(table, sa.Column("source_url", sa.String(1024), nullable=True))
        op.add_column(table, sa.Column("license", sa.String(64), nullable=True))
        op.add_column(table, sa.Column("attribution", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("source_meta", sa.JSON(), nullable=True))

    op.create_index("ix_images_dataset_license", "images", ["dataset_id", "license"])


def downgrade() -> None:
    op.drop_index("ix_images_dataset_license", table_name="images")

    for table in ("images", "version_image_states"):
        op.drop_column(table, "source_meta")
        op.drop_column(table, "attribution")
        op.drop_column(table, "license")
        op.drop_column(table, "source_url")
        op.drop_column(table, "source_name")

    op.drop_column("datasets", "attribution")
    op.drop_column("datasets", "license")
    op.drop_column("datasets", "source_url")
    op.drop_column("datasets", "source_name")
