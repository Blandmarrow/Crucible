"""add performance indexes

Revision ID: a1b2c3d4e5f6
Revises: 368e3cf9b781
Create Date: 2026-05-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '368e3cf9b781'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Speeds up gallery listing (ORDER BY created_at within a dataset)
    op.create_index('ix_images_dataset_created_at', 'images', ['dataset_id', 'created_at'])
    # Speeds up filesystem move/rename/delete DB sync (exact path lookup)
    op.create_index('ix_images_file_path', 'images', ['file_path'])
    # Speeds up caption filter in gallery listing
    op.create_index('ix_images_dataset_caption', 'images', ['dataset_id', 'caption_text'])


def downgrade() -> None:
    op.drop_index('ix_images_dataset_caption', table_name='images')
    op.drop_index('ix_images_file_path', table_name='images')
    op.drop_index('ix_images_dataset_created_at', table_name='images')
