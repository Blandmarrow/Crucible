"""add videos table and dataset video stat columns

Videos are sources, not images: they get their own table rather than rows in
`images`, which carries ~20 image-specific columns, FK cascades to detections,
and the "thumbnails are .webp keyed by stem" invariant. Extraction converts a
video into ordinary Image rows at the boundary (a later phase), so nothing
downstream becomes media-aware.

`datasets.video_count` / `video_size_bytes` are NOT NULL with a server default
so existing rows need no backfill; refresh_stats populates them from then on.
Videos stay out of `image_count` and `total_size_bytes` on purpose — see
docs/dev/video.md.

Revision ID: c1d4f7a2b9e3
Revises: d8200045f01b
Create Date: 2026-07-27

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1d4f7a2b9e3"
down_revision: str | Sequence[str] | None = "d8200045f01b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("poster_path", sa.String(length=1024), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("codec", sa.String(length=16), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("license", sa.String(length=64), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("crop_x", sa.Integer(), nullable=True),
        sa.Column("crop_y", sa.Integer(), nullable=True),
        sa.Column("crop_w", sa.Integer(), nullable=True),
        sa.Column("crop_h", sa.Integer(), nullable=True),
        sa.Column("deinterlace", sa.String(length=16), server_default="", nullable=False),
        sa.Column("trim_start_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("trim_end_ms", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "filename", name="uq_dataset_video_filename"),
    )
    op.create_index(op.f("ix_videos_dataset_id"), "videos", ["dataset_id"], unique=False)

    op.add_column("datasets", sa.Column("video_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("datasets", sa.Column("video_size_bytes", sa.BigInteger(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("datasets", "video_size_bytes")
    op.drop_column("datasets", "video_count")
    op.drop_index(op.f("ix_videos_dataset_id"), table_name="videos")
    op.drop_table("videos")
