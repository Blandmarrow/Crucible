"""add_image_sort_order

Revision ID: a3b4c5d6e7f8
Revises: e4054e7312c8
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "e4054e7312c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("images", sa.Column("sort_order", sa.Integer(), nullable=True))
    op.create_index("ix_images_dataset_sort_order", "images", ["dataset_id", "sort_order"])


def downgrade() -> None:
    op.drop_index("ix_images_dataset_sort_order", table_name="images")
    op.drop_column("images", "sort_order")
