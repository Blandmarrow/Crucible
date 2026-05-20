"""add_is_auto_named_to_image

Revision ID: 8b68472a09b3
Revises: e1f2a3b4c5d6
Create Date: 2026-05-20 21:17:45.819830

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b68472a09b3'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('images', sa.Column('is_auto_named', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('images', 'is_auto_named')
