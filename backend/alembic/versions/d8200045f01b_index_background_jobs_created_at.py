"""index background_jobs.created_at

Revision ID: d8200045f01b
Revises: c3f7a9e1d4b6
Create Date: 2026-07-24 20:42:20.744120

Autogenerate also reported a batch of unrelated diffs (JSON NOT NULL on the comfy
tables, the partial `uq_version_branch_name` index, the `ix_images_*` composites)
— those are SQLite reflection artefacts, not schema changes this revision intends,
so only the new index is kept here.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8200045f01b'
down_revision: Union[str, Sequence[str], None] = 'c3f7a9e1d4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(op.f('ix_background_jobs_created_at'), 'background_jobs', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_background_jobs_created_at'), table_name='background_jobs')
