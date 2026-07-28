"""add frame lineage columns to images

Extraction turns a video into ordinary `Image` rows; these three columns are the
only trace of where a frame came from. They are plain indexed columns rather than
keys inside `images.source_meta`, which is deferred=True and unindexable — the
"frames from video X" filter and any group-by-video need real queries.

`source_video_id` is `ON DELETE SET NULL`: deleting a source video must never
destroy the curated frames extracted from it, so a frame outlives its video with
the timestamp and shot index intact. `DELETE /videos/{id}` also NULLs the column
explicitly rather than relying on the FK — the test harness builds its schema
with `create_all` and never gets the `PRAGMA foreign_keys=ON` that
`backend/database.py` installs, so the FK's behaviour is untestable there.

**Why batch_alter_table**, despite `images` being the largest table in the schema
(seven indexes plus `uq_dataset_filename`) and batch mode being a copy-and-rebuild:
the cheap form, `op.add_column(..., inline_references=True)`, emits exactly the
right DDL — one `ALTER TABLE ADD COLUMN` with `REFERENCES videos (id) ON DELETE
SET NULL`, and `PRAGMA foreign_key_list` confirms SQLite enforces it. But
SQLAlchemy's SQLite reflection only parses `ON DELETE` off *table-level* named
constraints, so it reads a column-level inline reference back with no `ondelete`
at all. `scripts/check_migrations.py` then reports a permanent add_fk/remove_fk
pair for a schema that is in fact correct. The choice is a rebuild or a
`ACCEPTED_DRIFT` entry that would mask the next real FK change; the rebuild wins.
All three columns go in one batch so the table is copied once.

Revision ID: a7c3e5b1d9f2
Revises: c1d4f7a2b9e3
Create Date: 2026-07-27

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c3e5b1d9f2"
down_revision: str | Sequence[str] | None = "c1d4f7a2b9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("images") as batch_op:
        batch_op.add_column(sa.Column("source_video_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("source_timestamp_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("source_shot_index", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_images_source_video_id_videos",
            "videos",
            ["source_video_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        op.f("ix_images_source_video_id"), "images", ["source_video_id"], unique=False
    )

    # The snapshot mirror. Three plain columns, no FK and no index: this table is
    # written in bulk and read by version_id, and a snapshot must survive the
    # deletion of the video it references — which is exactly when the lineage
    # record is worth the most. Plain add_column, no batch: nothing here needs a
    # constraint, so there is nothing for reflection to lose.
    op.add_column("version_image_states", sa.Column("source_video_id", sa.String(length=36), nullable=True))
    op.add_column("version_image_states", sa.Column("source_timestamp_ms", sa.Integer(), nullable=True))
    op.add_column("version_image_states", sa.Column("source_shot_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("version_image_states", "source_shot_index")
    op.drop_column("version_image_states", "source_timestamp_ms")
    op.drop_column("version_image_states", "source_video_id")

    op.drop_index(op.f("ix_images_source_video_id"), table_name="images")
    with op.batch_alter_table("images") as batch_op:
        batch_op.drop_constraint("fk_images_source_video_id_videos", type_="foreignkey")
        batch_op.drop_column("source_shot_index")
        batch_op.drop_column("source_timestamp_ms")
        batch_op.drop_column("source_video_id")
