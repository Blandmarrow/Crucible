"""add dataset category

Revision ID: 685abb78b177
Revises: d6e7f8a9b0c1
Create Date: 2026-05-27 10:08:43.052074

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '685abb78b177'
down_revision: Union[str, Sequence[str], None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add category column to datasets table."""
    op.add_column('datasets', sa.Column('category', sa.String(length=255), server_default='', nullable=False))


def downgrade() -> None:
    """Remove category column from datasets table."""
    op.drop_column('datasets', 'category')
