"""add processing_history to images and version_image_states

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
Create Date: 2026-05-23

"""
from alembic import op
import sqlalchemy as sa

revision = 'c5d6e7f8a9b0'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('images',
        sa.Column('processing_history', sa.JSON(), nullable=True))
    op.add_column('version_image_states',
        sa.Column('processing_history', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('version_image_states', 'processing_history')
    op.drop_column('images', 'processing_history')
