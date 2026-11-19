"""the analyst's tool calls, one row per call a reply made (M12 phase 3d)

- `chat_tool_calls`: a call's round, id, name, raw arguments, whether it ran, and the
  exact result text sent to the model with the privacy categories it carried. It
  cascades with its reply, as `chat_proposals` does.

The FK is a table-level `ForeignKeyConstraint` (see c2a8f6b3d417).

Revision ID: 6c65a3a41ae0
Revises: bcbc83198eb1
Create Date: 2026-11-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6c65a3a41ae0"
down_revision: str | Sequence[str] | None = "bcbc83198eb1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(length=128), server_default="", nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("arguments", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_text", sa.Text(), server_default="", nullable=False),
        sa.Column("categories", sa.JSON(), nullable=True),
        sa.Column("chars", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_tool_calls_message_id"), "chat_tool_calls", ["message_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_tool_calls_message_id"), table_name="chat_tool_calls")
    op.drop_table("chat_tool_calls")
