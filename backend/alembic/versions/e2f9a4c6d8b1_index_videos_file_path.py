"""index videos.file_path

Revision ID: e2f9a4c6d8b1
Revises: c4b8e6a2f107
Create Date: 2026-07-29 00:00:00.000000

`routers/filesystem.py` asks "is there a row at this exact path?" once per
affected file on every move, rename and delete. The `images` side has had an
index for that lookup since a1b2c3d4e5f6; the `videos` side did not, so each of
those became a full scan of `videos`.

Plain `CREATE INDEX` — no batch, no table rebuild, so none of the SQLite FK
hazards that apply to the `images` rebuilds are in play here.

Done on **both** sides deliberately: `index=True` on the model as well as this
migration, so no `ACCEPTED_DRIFT` entry is needed. `ix_images_file_path` is
allowlisted in `scripts/check_migrations.py` only because it exists in a
migration and never on the model — this fixes the asymmetry in the correct
direction rather than copying the drift. Folding `Image.file_path` onto its
model is the follow-up; it would mean deleting an allowlist line in a change
about videos.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e2f9a4c6d8b1"
down_revision: Union[str, None] = "c4b8e6a2f107"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f("ix_videos_file_path"), "videos", ["file_path"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_videos_file_path"), table_name="videos")
