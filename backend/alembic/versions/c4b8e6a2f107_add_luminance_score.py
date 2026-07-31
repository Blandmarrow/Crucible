"""add luminance_score to images

Revision ID: c4b8e6a2f107
Revises: a7c3e5b1d9f2
Create Date: 2026-07-28 00:00:00.000000

Mean grayscale brightness (0.0 = black, 1.0 = white), written by the technical
scoring pass. No index — `color_score`/`saturation_score` have none either, and
the score filters run as a plain scan. No `version_image_states` mirror: it is
scored, not authored, and is recomputed on demand (see NOT_MIRRORED in
backend/tests/test_video_lineage_mirrors.py).

No backfill is possible — the value needs pixels. Existing rows stay NULL until
re-scored, exactly as `color_score`/`saturation_score` did.

**Superseded, 2026-07-31 — do not act on the "no mirror" sentence above.** A
migration is history and is not rewritten, so the correction lives here: revision
`d1c7b4e9f0a3`, later on this same branch, added the `version_image_states`
mirror for `luminance_score` (with `nsfw_score` and `saturation_score`). A
`*_score` column is authored data as far as a snapshot is concerned — nothing
recomputes one on restore — so all ten are mirrored *and* diffed, and
`NOT_MIRRORED` holds no score. CLAUDE.md, `docs/dev/scoring.md`,
`docs/dev/versioning-service.md` and `test_video_lineage_mirrors.py` all agree on
that; only this docstring predates it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4b8e6a2f107'
down_revision: Union[str, Sequence[str], None] = 'a7c3e5b1d9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('images', sa.Column('luminance_score', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('images', 'luminance_score')
