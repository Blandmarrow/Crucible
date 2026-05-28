"""add openai providers table

Revision ID: f1e2d3c4b5a6
Revises: 685abb78b177
Create Date: 2026-05-28 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1e2d3c4b5a6'
down_revision: Union[str, Sequence[str], None] = '685abb78b177'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'openai_providers',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('base_url', sa.String(1024), nullable=False),
        sa.Column('api_key', sa.Text(), nullable=False, server_default=''),
        sa.Column('default_model', sa.String(255), nullable=False, server_default=''),
        sa.Column('max_image_px', sa.Integer(), nullable=False, server_default='1024'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )


def downgrade() -> None:
    op.drop_table('openai_providers')
