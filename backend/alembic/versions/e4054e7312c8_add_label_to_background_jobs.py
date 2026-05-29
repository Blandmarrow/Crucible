"""add_label_to_background_jobs

Revision ID: e4054e7312c8
Revises: b4c5d6e7f8a9
Create Date: 2026-05-29 15:41:05.034211

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4054e7312c8'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('background_jobs', sa.Column('label', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('background_jobs', 'label')
