"""add scores_stale to images and version_image_states

Ten code paths rewrite an image's pixels in place — batch and single resize,
batch and single crop, LUT grading, upscaling, crop-to-detection, and video
frame re-extraction — and every one of them deliberately leaves the ten
`*_score` columns and `quality_flags` alone. Nothing recomputes a score, so the
row keeps numbers measured against pixels that no longer exist. `blur_score` is
Laplacian variance against a fixed threshold and so is resolution-dependent: a
score from a 1024px triage frame is systematically wrong for the 4K frame that
replaced it. The consequence lands weeks later at export, where `exclude_flags`
drops images on flags derived from those numbers.

This column records the fact. It is set by `utils.record_in_place` — the single
writer of `processing_history`, which is why the two can never drift apart — and
cleared by a quality run that refreshes every score the row actually carries.

**Not named `*_score`.** `SCORE_COLUMNS` in
`backend/tests/test_video_lineage_mirrors.py` is derived by suffix, and a boolean
enrolled in those float-seeding guards would fail in a confusing way.

**Mirrored on `version_image_states`, and diffed.** The mirror is not optional:
the structural guards run in both directions, and an unmirrored column is blanked
by every restore — a snapshot restoring stale scores without the bit that
qualifies them would silently declare them trustworthy. `_DIFF_COLS`' carve-out
is for *immutable* lineage columns; this one is mutable, and an in-place rewrite
between two snapshots flips it and nothing else.

**No batch mode** on either table. A plain boolean with a server default: no FK,
no index, no constraint for SQLite reflection to lose, so the `images` half needs
no rebuild (unlike `a7c3e5b1d9f2`, which needed one only for its FK). The DDL
shape is `8b68472a09b3`'s `is_auto_named`.

**No backfill.** `False` is the correct historical answer for both tables: the
bit means "we observed a rewrite", and for every pre-migration row we observed
nothing. Backfilling from `processing_history` was considered and rejected — it
would flag rows whose scores were computed *after* the rewrite as stale forever,
since nothing clears a bit whose scores are already correct except a re-score the
user has no reason to run.

Revision ID: b2f6c8d0e1a4
Revises: d1c7b4e9f0a3
Create Date: 2026-07-31

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2f6c8d0e1a4"
down_revision: str | Sequence[str] | None = "d1c7b4e9f0a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column("scores_stale", sa.Boolean(), server_default="0", nullable=False),
    )
    op.add_column(
        "version_image_states",
        sa.Column("scores_stale", sa.Boolean(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("version_image_states", "scores_stale")
    op.drop_column("images", "scores_stale")
