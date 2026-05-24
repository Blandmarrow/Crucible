"""fix version name unique constraint to be per-branch

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-05-24

"""
from alembic import op

revision = 'd6e7f8a9b0c1'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index('uq_version_dataset_name', 'dataset_versions')
    op.execute(
        "CREATE UNIQUE INDEX uq_version_branch_name "
        "ON dataset_versions (dataset_id, branch_id, name) "
        "WHERE name IS NOT NULL AND branch_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index('uq_version_branch_name', 'dataset_versions')
    op.execute(
        "CREATE UNIQUE INDEX uq_version_dataset_name "
        "ON dataset_versions (dataset_id, name) WHERE name IS NOT NULL"
    )
