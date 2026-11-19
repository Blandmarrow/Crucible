"""Analyst chats (M12 phase 2): a dataset's conversations, their messages, and the token
usage every LLM call records.

- **A chat belongs to one dataset** and cascades with it (decision 21). Its provider is
  `SET NULL` on delete, so removing a provider keeps the history and asks for a new one.
- **`privacy` on a chat is an override** of the provider's defaults, NULL meaning "follow
  the provider". It is cleared whenever the chat's provider changes, so a toggle chosen
  for a local server never carries over to a paid remote one
  (`backend/services/analyst_context.py`).
- **`llm_usage` is the budget's only source**: one row per call, summed when a budget is
  checked, never a running counter. Its `chat_id` is `SET NULL`, so deleting a chat does
  not hand its tokens back to the daily budget.

See docs/dev/analyst/chat.md.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

CHAT_ROLES = ("user", "assistant")
MESSAGE_STATUSES = ("complete", "partial", "cancelled", "error")
PROPOSAL_OPS = ("add", "edit", "remove")
PROPOSAL_STATUSES = ("pending", "accepted", "rejected", "stale")


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    provider_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("openai_providers.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    # {category: bool} over the provider's defaults; NULL follows the provider.
    privacy: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Order within the chat, 0-based; unique per chat.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="complete", server_default="complete")
    # Set on `error`: a short machine kind (`transient`, `fatal`, `rejected`, `timeout`,
    # `interrupted`, `budget`) and the message shown to the user. Never the API key.
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The model that wrote an assistant reply, kept because the chat's model can change.
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True when the server reported no usage and the counts are a character estimate.
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # On an assistant row: which categories the context sent and how big each was, and
    # the note numbers the model was shown (what a proposal's "remove open_item 3" means).
    context_manifest: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    # On an assistant row: the findings and image count its context showed, so the next
    # message can say what changed since ("Blurry 30 -> 0").
    findings_summary: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    image_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # On an assistant row: the finding ids Crucible shows under the reply, chosen by
    # Crucible rather than the model.
    related_findings: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # A reply's note proposals (phase 2d), always loaded with the message: every
    # `ChatMessageOut` carries them, and a lazy load on an async session would raise.
    proposals: Mapped[list["ChatProposal"]] = relationship(
        "ChatProposal", order_by="ChatProposal.seq", lazy="selectin",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    # The tool calls the reply made (phase 3d), loaded with it for the same reason.
    tool_calls: Mapped[list["ChatToolCall"]] = relationship(
        "ChatToolCall", order_by="ChatToolCall.id", lazy="selectin",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    __table_args__ = (UniqueConstraint("chat_id", "seq", name="uq_chat_messages_chat_seq"),)


class ChatToolCall(Base):
    """One tool call a reply made, and exactly what its result sent the model (M12
    phase 3d, docs/dev/analyst/tool-loop.md).

    Hung off the reply's row rather than stored as `role=tool` messages, so a turn keeps
    one reply row (the user's ruling of 2026-11-19). Later turns do not resend these:
    history carries the reply text only. `arguments` is the raw text the model sent,
    valid JSON or not; `categories` are the privacy categories `result_text` carries.
    """

    __tablename__ = "chat_tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 0 for the first call to the model in the turn, 1 for the second, and so on.
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    call_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # `ok` or `error`: whether the tool ran, not whether it found anything.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    categories: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ChatProposal(Base):
    """One change to the notes that a reply proposed in its `crucible-notes` block,
    waiting for the user (docs/dev/analyst/proposals.md).

    `target_item_id` has no foreign key: it names an item in either note table, and an
    item gone since is exactly what makes a proposal `stale`. `applied_item_id` and
    `applied_before` are what an accepted proposal's undo needs.
    """

    __tablename__ = "chat_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Order within the reply's block.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    # A dataset section (`preference`, `decision`, `open_item`) or `about_me`.
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    # Edit and remove: the item the reply's numbering pointed at, and its text then.
    target_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Add and edit: the proposed text.
    new_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    # Once accepted: the item it added or changed, and an edit's or removal's text before.
    applied_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class LlmUsage(Base):
    """One LLM call's tokens. Budgets sum these rows (see the module docstring)."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The local calendar date of the call, `YYYY-MM-DD`: the daily budget's key.
    day: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    chat_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chats.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    provider_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("openai_providers.id", ondelete="SET NULL"), nullable=True
    )
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Whether the provider counted as billed at call time; only billed calls meet a budget.
    billed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
