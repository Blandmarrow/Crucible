"""mirror nsfw/saturation/luminance scores on version_image_states

`Image` carries ten `*_score` columns; seven were mirrored here and three —
`nsfw_score`, `saturation_score`, `luminance_score` — were not. **This reverses
lines 9-11 of `c4b8e6a2f107`'s docstring**, which wrote that split up as a rule
("scored, not authored, and recomputed on demand"). It was never a decision. This
table was created after `saturation_score` already existed on `images` and its
`CREATE TABLE` simply omitted it; `nsfw_score` landed later and never got a
mirror; `c4b8e6a2f107` then copied the omission forward as precedent.

Nothing recomputes a technical score. Quality scoring is a manual job the user
starts, and `score_coverage["technical"]` counts `blur_score` alone — so a
dataset missing these three does not even report as needing a re-score, and a
snapshot is the only record of an old value. The seven mirrored siblings settle
it from the other side: `color_score` and `saturation_score` come out of the same
scorer in the same pass, and a restore preserved one and blanked the other.

**Diffed as well as mirrored.** `_DIFF_COLS`' carve-out is for *immutable*
columns — frame lineage, written once by extraction and never touched again, so a
comparison is a guaranteed "unchanged". A score changes every time the quality
job reruns, which is precisely a difference between two snapshots worth showing.

**No batch mode.** Three nullable Floats: no FK, no index, no server default —
nothing for SQLite reflection to lose, so plain `add_column` is right (the same
call and the same reasoning as `a7c3e5b1d9f2`'s `version_image_states` half; its
`images` half needed a rebuild only because of an FK).

**The backfill** fills pre-migration state rows from `images` by `image_id`,
asserting *today's* value for a historical row. No other value is knowable — the
old score was never recorded anywhere — and today's value is precisely what a
restore of that snapshot leaves in place right now, since the current code never
writes these three back. So the backfill makes an old snapshot claim what a
restore of it already does, and no spurious diff row appears between two
pre-migration versions. Without it, restore's unconditional assignment (unlike
`width`/`height`, which are guarded by a truthiness check) would blank three live
columns from an old snapshot — the exact regression that ruled out the other
option here, dropping the seven mirrors instead of adding three.

Rows whose image has since been deleted stay NULL, which is what a snapshot of a
never-scored image looks like anyway. `ix_version_image_states_image_id` already
exists, so the correlated subquery is indexed.

Revision ID: d1c7b4e9f0a3
Revises: e2f9a4c6d8b1
Create Date: 2026-07-30

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1c7b4e9f0a3"
down_revision: str | Sequence[str] | None = "e2f9a4c6d8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS = ("nsfw_score", "saturation_score", "luminance_score")


def upgrade() -> None:
    for col in _COLUMNS:
        op.add_column("version_image_states", sa.Column(col, sa.Float(), nullable=True))

    for col in _COLUMNS:
        op.execute(
            sa.text(
                f"UPDATE version_image_states "
                f"   SET {col} = (SELECT i.{col} FROM images i "
                f"                 WHERE i.id = version_image_states.image_id) "
                f" WHERE image_id IS NOT NULL"
            )
        )


def downgrade() -> None:
    for col in reversed(_COLUMNS):
        op.drop_column("version_image_states", col)
