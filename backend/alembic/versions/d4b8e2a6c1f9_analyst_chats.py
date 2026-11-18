"""analyst chats, their messages and token usage (M12 phase 2)

- `chats`: one conversation about one dataset (CASCADE), its provider (SET NULL, so a
  removed provider keeps the history) and an optional privacy override.
- `chat_messages`: the turns, with status, usage and the manifest of what was sent.
- `llm_usage`: one row per LLM call, summed by the token budgets. `chat_id` is SET NULL
  so deleting a chat does not refund the daily budget.
- `threshold_settings` gains the two token budgets; `openai_providers` gains
  `analyst_privacy`, NULL meaning "follow `billed`".

Foreign keys are table-level `ForeignKeyConstraint`s in `create_table`, never
column-level inline ones (see c2a8f6b3d417). The new columns are plain ADD COLUMNs;
only the downgrade needs `batch_alter_table`.

Revision ID: d4b8e2a6c1f9
Revises: a9c4e2f7b1d8
Create Date: 2026-11-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b8e2a6c1f9"
down_revision: str | Sequence[str] | None = "a9c4e2f7b1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "threshold_settings",
        sa.Column("analyst_chat_budget_tokens", sa.Integer(), server_default="200000", nullable=False),
    )
    op.add_column(
        "threshold_settings",
        sa.Column("analyst_daily_budget_tokens", sa.Integer(), server_default="1000000", nullable=False),
    )
    op.add_column("openai_providers", sa.Column("analyst_privacy", sa.JSON(), nullable=True))

    op.create_table(
        "chats",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), server_default="", nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=True),
        sa.Column("model", sa.String(length=255), server_default="", nullable=False),
        sa.Column("privacy", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["openai_providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chats_dataset_id"), "chats", ["dataset_id"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="complete", nullable=False),
        sa.Column("error_kind", sa.String(length=32), nullable=True),
        sa.Column("error_detail", sa.String(length=500), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("tokens_estimated", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("context_manifest", sa.JSON(), nullable=True),
        sa.Column("findings_summary", sa.JSON(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=True),
        sa.Column("related_findings", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "seq", name="uq_chat_messages_chat_seq"),
    )
    op.create_index(op.f("ix_chat_messages_chat_id"), "chat_messages", ["chat_id"], unique=False)

    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("chat_id", sa.String(length=36), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("provider_id", sa.String(length=36), nullable=True),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("billed", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["openai_providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_usage_chat_id"), "llm_usage", ["chat_id"], unique=False)
    op.create_index(op.f("ix_llm_usage_day"), "llm_usage", ["day"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_usage_day"), table_name="llm_usage")
    op.drop_index(op.f("ix_llm_usage_chat_id"), table_name="llm_usage")
    op.drop_table("llm_usage")
    op.drop_index(op.f("ix_chat_messages_chat_id"), table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_chats_dataset_id"), table_name="chats")
    op.drop_table("chats")
    with op.batch_alter_table("openai_providers") as batch_op:
        batch_op.drop_column("analyst_privacy")
    with op.batch_alter_table("threshold_settings") as batch_op:
        batch_op.drop_column("analyst_daily_budget_tokens")
        batch_op.drop_column("analyst_chat_budget_tokens")
