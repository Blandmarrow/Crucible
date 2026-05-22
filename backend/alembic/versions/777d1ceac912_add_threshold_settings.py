"""add_threshold_settings

Revision ID: 777d1ceac912
Revises: 8b68472a09b3
Create Date: 2026-05-22 15:46:20.456714

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '777d1ceac912'
down_revision: Union[str, Sequence[str], None] = '8b68472a09b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('threshold_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('blur_threshold', sa.Float(), server_default='100.0', nullable=False),
    sa.Column('noise_threshold', sa.Float(), server_default='15.0', nullable=False),
    sa.Column('uniformity_threshold', sa.Float(), server_default='12.0', nullable=False),
    sa.Column('duplicate_threshold', sa.Float(), server_default='8.0', nullable=False),
    sa.Column('watermark_threshold', sa.Float(), server_default='0.6', nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('threshold_settings')
