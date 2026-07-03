"""add caption_token_count to image (persisted GPT-2 token count) + backfill

Revision ID: d9f4a1c7b2e8
Revises: f7e8d9c0b1a2
Create Date: 2026-07-02

Adds images.caption_token_count so caption tokenization can be dropped from the
Stats and gallery-filter hot paths. The column is kept in sync at runtime by an
attribute listener on Image.caption_text (see backend/models/image.py). This
migration adds the column + composite index and backfills existing rows. The
backfill is best-effort: if tokenization fails (e.g. tiktoken cannot download
the GPT-2 BPE vocab on a fresh offline/firewalled install), it is skipped with a
warning and rows stay NULL — consumers coalesce NULL→0 and the caption_text
listener heals each row on its next caption write.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.utils import count_caption_tokens

revision: str = "d9f4a1c7b2e8"
down_revision: str | Sequence[str] | None = "f7e8d9c0b1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("images", sa.Column("caption_token_count", sa.Integer(), nullable=True))
    op.create_index(
        "ix_images_dataset_caption_tokens", "images", ["dataset_id", "caption_token_count"]
    )

    # Backfill existing rows. NULL/empty caption → 0; otherwise GPT-2 BPE token count.
    # Best-effort: tokenization can fail on a fresh offline/firewalled install when
    # tiktoken cannot download the GPT-2 BPE vocab. Skip on failure and leave rows
    # NULL — consumers coalesce NULL→0 and the caption_text listener heals each row
    # on its next caption write.
    try:
        conn = op.get_bind()
        rows = conn.execute(sa.text("SELECT id, caption_text FROM images")).fetchall()
        updates = [
            {"row_id": r[0], "tc": count_caption_tokens(r[1])}
            for r in rows
        ]
        stmt = sa.text("UPDATE images SET caption_token_count = :tc WHERE id = :row_id")
        for start in range(0, len(updates), 1000):
            conn.execute(stmt, updates[start : start + 1000])
    except Exception as e:
        print(
            f"WARNING: caption_token_count backfill skipped ({e}); rows left NULL. "
            "They will be filled on the next caption write per image."
        )


def downgrade() -> None:
    op.drop_index("ix_images_dataset_caption_tokens", table_name="images")
    op.drop_column("images", "caption_token_count")
