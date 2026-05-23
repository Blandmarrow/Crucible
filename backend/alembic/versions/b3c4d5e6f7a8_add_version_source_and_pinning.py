"""add version source and pinning

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-05-23

"""
from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('dataset_versions',
        sa.Column('source', sa.String(32), nullable=False, server_default='manual'))
    op.add_column('dataset_versions',
        sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('dataset_versions', 'is_pinned')
    op.drop_column('dataset_versions', 'source')
