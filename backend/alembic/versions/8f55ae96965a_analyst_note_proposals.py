"""analyst note proposals, and the notes' chat columns (M12 phase 2d)

- `chat_proposals`: one change to the notes that a reply proposed in its
  `crucible-notes` block, pending until the user accepts or rejects it. It cascades
  with its message. `target_item_id` has no foreign key: it names an item in either
  note table, and an item gone since is what makes a proposal stale.
- `dataset_note_items.source_chat_id`, `app_note_items.source_chat_id` and
  `note_item_history.chat_id`: the chat a proposal came from, all `SET NULL`, since
  deleting a chat must never delete a note.

The new table's FK is a table-level `ForeignKeyConstraint` (see c2a8f6b3d417). The three
new columns carry foreign keys, which SQLite cannot add with ALTER TABLE, so each goes
in through a `batch_alter_table` rebuild with a naming convention, as f7e8d9c0b1a2 did.
The history table's named CHECK constraint is reflected and kept by the rebuild.

Revision ID: 8f55ae96965a
Revises: d4b8e2a6c1f9
Create Date: 2026-11-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f55ae96965a"
down_revision: str | Sequence[str] | None = "d4b8e2a6c1f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

# (table, column) pairs that gain a SET NULL foreign key to chats.id.
_CHAT_COLUMNS = (
    ("dataset_note_items", "source_chat_id"),
    ("app_note_items", "source_chat_id"),
    ("note_item_history", "chat_id"),
)


def upgrade() -> None:
    op.create_table(
        "chat_proposals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=False),
        sa.Column("target_item_id", sa.String(length=36), nullable=True),
        sa.Column("target_text", sa.Text(), nullable=True),
        sa.Column("new_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("applied_item_id", sa.String(length=36), nullable=True),
        sa.Column("applied_before", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_proposals_message_id"), "chat_proposals", ["message_id"], unique=False)

    for table, column in _CHAT_COLUMNS:
        with op.batch_alter_table(table, naming_convention=_NAMING) as batch_op:
            batch_op.add_column(sa.Column(column, sa.String(length=36), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_{column}_chats", "chats", [column], ["id"], ondelete="SET NULL"
            )


def downgrade() -> None:
    for table, column in reversed(_CHAT_COLUMNS):
        with op.batch_alter_table(table, naming_convention=_NAMING) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_{column}_chats", type_="foreignkey")
            batch_op.drop_column(column)
    op.drop_index(op.f("ix_chat_proposals_message_id"), table_name="chat_proposals")
    op.drop_table("chat_proposals")
