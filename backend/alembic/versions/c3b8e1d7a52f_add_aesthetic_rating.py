"""add aesthetic_rating and rating_stale to images and version_image_states

The keep/cut decision a human makes about an image: 4 = Keep, 3 = Probably,
2 = Probably not, 1 = Cut, NULL = not yet rated. Higher is better, matching every
other numeric column in the app and every photo tool's star rating, so that a
"Rating ↓" sort reads best-first beside "Aesthetic ↓".

**Not named `*_score`, and no `info={"qualifies": ...}`.** `score_columns()` in
`backend/utils.py` is derived by the `*_score` suffix and pinned to exactly ten
names by `test_scores_stale.py`; `qualifies` means "this column says how to read
a score", and a rating qualifies nothing — it is independent authored data, not a
measurement. Enrolment into the rebuild-path guards is instead
`info={"carried": True}`, which those guards read directly.

**`rating_stale` is its own bit, never a reuse of `scores_stale`.** The two
columns are written by the same single writer (`utils.record_in_place`, which an
AST guard keeps single) but their *clear* predicates diverge: a quality run that
re-measures clears `scores_stale`, while nothing but a human looking again clears
`rating_stale`. One bit would let a re-score silently declare a judgement current.

**Mirrored on `version_image_states`, and diffed.** The mirror is enforced in both
directions by `test_video_lineage_mirrors.py`; an unmirrored column is blanked by
every restore. Both columns are *mutable* authored state, so unlike the immutable
lineage carve-out in `_DIFF_COLS` they are compared as well as carried.

**No batch mode** on either table. A nullable integer and a boolean with a server
default — no FK, no CHECK, no constraint for SQLite reflection to lose — so
neither table needs a rebuild. The DDL shape is `b2f6c8d0e1a4`'s `scores_stale`.

**The index is `(dataset_id, aesthetic_rating)`**, matching every other composite
in `Image.__table_args__`: the column is both filtered (the gallery rating chips,
the export include/exclude) and sorted, always within one dataset.

**No backfill.** Every existing row is genuinely unrated, and `False` is the
correct historical answer for the bit — it means "we observed a rewrite after a
rating", and no pre-migration row carries a rating to have observed one against.
`downgrade()` is a clean drop: nothing here moves data or touches disk.

Revision ID: c3b8e1d7a52f
Revises: b1a7f3d5c2e9
Create Date: 2026-08-03

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3b8e1d7a52f"
down_revision: str | Sequence[str] | None = "b1a7f3d5c2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("images", sa.Column("aesthetic_rating", sa.Integer(), nullable=True))
    op.add_column(
        "images",
        sa.Column("rating_stale", sa.Boolean(), server_default="0", nullable=False),
    )
    op.add_column(
        "version_image_states", sa.Column("aesthetic_rating", sa.Integer(), nullable=True)
    )
    op.add_column(
        "version_image_states",
        sa.Column("rating_stale", sa.Boolean(), server_default="0", nullable=False),
    )
    op.create_index(
        "ix_images_dataset_rating", "images", ["dataset_id", "aesthetic_rating"]
    )


def downgrade() -> None:
    op.drop_index("ix_images_dataset_rating", table_name="images")
    op.drop_column("version_image_states", "rating_stale")
    op.drop_column("version_image_states", "aesthetic_rating")
    op.drop_column("images", "rating_stale")
    op.drop_column("images", "aesthetic_rating")
