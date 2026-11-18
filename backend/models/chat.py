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
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

CHAT_ROLES = ("user", "assistant")
MESSAGE_STATUSES = ("complete", "partial", "cancelled", "error")


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

    __table_args__ = (UniqueConstraint("chat_id", "seq", name="uq_chat_messages_chat_seq"),)


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
