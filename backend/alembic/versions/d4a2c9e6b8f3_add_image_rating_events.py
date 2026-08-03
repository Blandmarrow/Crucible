"""add image_rating_events table

`images.aesthetic_rating` is overwritten in place and `images.updated_at` is a
generic `onupdate` that any edit bumps, so nothing recorded when a rating was
given or that one had ever been revised. This table is that history: one
append-only row per rating write, from which the self-agreement ceiling (how
often you give the same image the same answer twice) is computed.

**Why an integer autoincrement PK rather than the uuid string every other table
uses.** A bulk write stamps one `datetime.utcnow()` across the whole batch, so
`created_at` ties are the norm — the monotonic id is the only deterministic
ordering key for "consecutive events", and the ceiling is defined over
consecutive pairs. `version_image_states` and `detections` set the precedent.

**`dataset_id` carries no foreign key**, mirroring `version_image_states.image_id`.
The event records the dataset the rating was *given in*, which is the frame the
rating is calibrated against; a `datasets.id` FK with CASCADE would destroy the
history of an image that had since been moved out of a since-deleted dataset.
`image_id` does cascade: with the image gone there are no pixels the judgement
was about.

**The backfill stamps `updated_at`, not `created_at`.** Every existing rating is
worth exactly one event — without it the *first* deliberate re-rate would produce
no comparable pair and the ceiling would need two passes before it counted
anything. `updated_at` is the tightest available upper bound on when the rating
was given (any later edit only pushes it forward); `created_at` is the image's
ingest time, always earlier than the rating, and would fabricate a false ordering
against the real events that follow. `batch_size` is left NULL, which reads
correctly as "nothing is known about the write that produced this".

**No batch mode** — SQLite is only creating a table here, nothing is being
altered, so there is no constraint for reflection to lose.

Revision ID: d4a2c9e6b8f3
Revises: c3b8e1d7a52f
Create Date: 2026-08-03

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4a2c9e6b8f3"
down_revision: str | Sequence[str] | None = "c3b8e1d7a52f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "image_rating_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("image_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("batch_size", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_image_rating_events_image", "image_rating_events", ["image_id", "id"]
    )
    # Raw SQL, never the ORM: a migration that imports models breaks the moment
    # those models move on from the schema this revision describes.
    op.execute(
        "INSERT INTO image_rating_events "
        "(image_id, dataset_id, rating, batch_size, created_at) "
        "SELECT id, dataset_id, aesthetic_rating, NULL, updated_at "
        "FROM images WHERE aesthetic_rating IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_image_rating_events_image", table_name="image_rating_events")
    op.drop_table("image_rating_events")
