from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Label(Base):
    """A global, app-wide vocabulary entry attached to images as a second facet.

    Deliberately *not* tags (dropped in a8c3e1f2b9d0 for overlapping with
    captions): a label never touches ``Image.caption_text`` and is never written
    to a caption sidecar or exported as a caption token.
    """

    __tablename__ = "labels"

    # A uuid PK, not SQLite's INTEGER PRIMARY KEY, because a label id is
    # persisted in three places outside this table — the `VersionImageState`
    # snapshot mirror, the gallery's `gallery-state-${datasetId}` blob, and
    # export presets. SQLite recycles `max(rowid)+1`, so deleting the last label
    # and creating another would silently reuse its id and a restore would
    # reattach the wrong concept.
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # Case-insensitive uniqueness is enforced in the router (SQLite's default
    # collation is case-sensitive); this constraint is the backstop.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(16), nullable=False, server_default="#6b7280")
    hotkey: Mapped[str | None] = mapped_column(String(1), nullable=True, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_labels_sort_order", "sort_order"),)


class ImageLabel(Base):
    """Join row attaching a `Label` to an `Image`.

    **No `dataset_id` column, deliberately.** It is derivable via
    `images.dataset_id`, and its absence is *why* `batch_move_dataset` needs zero
    changes — that path UPDATEs `Image.dataset_id` in place and never changes
    `Image.id`, so every join row follows the image for free. Denormalizing it
    here for faster per-dataset counts would silently break every cross-dataset
    move; `test_labels_survive_rebuild_paths.py` pins the current behaviour.

    **No `Image.labels` relationship either.** `Image.source_meta` is the
    standing lesson: a lazy load on an async session raises `MissingGreenlet`
    only on the live path, never in a helper-level unit test. Every read is an
    explicit `select(ImageLabel...)` via `services/label_service.py`.
    """

    __tablename__ = "image_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("labels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("image_id", "label_id", name="uq_image_label"),)
