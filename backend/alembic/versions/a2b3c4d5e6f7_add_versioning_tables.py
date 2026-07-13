"""add_versioning_tables

Revision ID: a2b3c4d5e6f7
Revises: 777d1ceac912
Create Date: 2026-05-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = '777d1ceac912'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dataset_branches',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('datasets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('head_version_id', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_dataset_branches_dataset_id', 'dataset_branches', ['dataset_id'])
    op.create_index('uq_branch_dataset_name', 'dataset_branches', ['dataset_id', 'name'], unique=True)

    op.create_table(
        'dataset_versions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('datasets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('branch_id', sa.String(36), sa.ForeignKey('dataset_branches.id', ondelete='SET NULL'), nullable=True),
        sa.Column('parent_id', sa.String(36), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True, server_default=''),
        sa.Column('image_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_dataset_versions_dataset_id', 'dataset_versions', ['dataset_id'])
    op.create_index('ix_dataset_versions_branch_id', 'dataset_versions', ['branch_id'])
    # Partial unique index for named versions — SQLite-specific syntax
    op.execute(
        "CREATE UNIQUE INDEX uq_version_dataset_name "
        "ON dataset_versions (dataset_id, name) WHERE name IS NOT NULL"
    )

    op.create_table(
        'version_image_states',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('version_id', sa.String(36), sa.ForeignKey('dataset_versions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('image_id', sa.String(36), nullable=True),
        sa.Column('filename', sa.String(512), nullable=False, server_default=''),
        sa.Column('original_filename', sa.String(512), nullable=False, server_default=''),
        sa.Column('subfolder', sa.String(512), nullable=False, server_default=''),
        sa.Column('file_path', sa.String(1024), nullable=False, server_default=''),
        sa.Column('file_hash', sa.String(64), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('format', sa.String(16), nullable=True),
        sa.Column('caption_text', sa.Text(), nullable=False, server_default=''),
        sa.Column('tags_json', sa.JSON(), nullable=True),
        sa.Column('quality_flags', sa.JSON(), nullable=True),
        sa.Column('aesthetic_score', sa.Float(), nullable=True),
        sa.Column('blur_score', sa.Float(), nullable=True),
        sa.Column('noise_score', sa.Float(), nullable=True),
        sa.Column('uniformity_score', sa.Float(), nullable=True),
        sa.Column('watermark_score', sa.Float(), nullable=True),
        sa.Column('color_score', sa.Float(), nullable=True),
        sa.Column('style_similarity_score', sa.Float(), nullable=True),
        sa.Column('dino_layer_scores', sa.JSON(), nullable=True),
        sa.Column('generation_metadata', sa.JSON(), nullable=True),
        sa.Column('is_present', sa.Boolean(), nullable=False, server_default='1'),
    )
    op.create_index('ix_version_image_states_version_id', 'version_image_states', ['version_id'])
    op.create_index('ix_version_image_states_image_id', 'version_image_states', ['image_id'])

    op.add_column('datasets', sa.Column('current_branch_id', sa.String(36), nullable=True))
    op.add_column('threshold_settings', sa.Column('versioning_mode', sa.String(16), nullable=False, server_default="off"))


def downgrade() -> None:
    op.drop_column('threshold_settings', 'versioning_mode')
    op.drop_column('datasets', 'current_branch_id')
    op.drop_table('version_image_states')
    op.drop_table('dataset_versions')
    op.drop_table('dataset_branches')
