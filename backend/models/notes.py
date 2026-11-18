"""Dataset notes and app-wide notes (M12, the dataset analyst).

A simplified version of how this repository keeps its own context: per dataset, standing
**preferences**, dated **decisions** and **open items**; app-wide, notes **about the user**.
Stored as separate items rather than one text blob, so a weak model can later propose "add
an item" or "remove item 4" without rewriting a document (see `docs/dev/analyst/notes.md`).

- **Soft delete plus `note_item_history` is the undo.** A removed item keeps its row with
  `deleted_at` set; every add, edit, remove and restore writes a history row.
- **A dismissal is a note item**: a `preference` whose `finding_id` names the dismissed
  health finding and whose `fingerprint` records the finding's state when it was
  dismissed, so the report can tell when things got worse (`health_service`).
- **Notes are not dataset content.** They cascade with their dataset, are never part of a
  snapshot, and `duplicate_dataset` does not copy them.

Phase 2 adds the chat columns (`source_chat_id`, the history's `chat_id`) once the
`chats` table they point at exists.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

DATASET_NOTE_SECTIONS = ("preference", "decision", "open_item")
APP_NOTE_SECTIONS = ("about_me",)
NOTE_OPS = ("add", "edit", "remove", "restore")
NOTE_ACTORS = ("user", "llm")


class DatasetNoteItem(Base):
    __tablename__ = "dataset_note_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Decisions carry the date the user made them; the other sections leave it NULL.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set only on a dismissal: the health finding it hides and that finding's state then.
    finding_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # A permanent dismissal stays hidden however much worse the finding gets.
    permanent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AppNoteItem(Base):
    __tablename__ = "app_note_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NoteItemHistory(Base):
    """One change to a note item. Exactly one of the two item ids is set.

    Two foreign keys rather than one polymorphic id, so a history row is deleted with
    its item, and a dataset's history with the dataset.
    """

    __tablename__ = "note_item_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("dataset_note_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    app_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("app_note_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    before_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "(dataset_item_id IS NULL) != (app_item_id IS NULL)",
            name="ck_note_history_one_item",
        ),
    )
