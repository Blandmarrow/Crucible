from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[str] = mapped_column(String(36), ForeignKey("images.id"), nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id"), nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), default="general")
    source: Mapped[str] = mapped_column(String(64), default="manual")

    image: Mapped["Image"] = relationship("Image", back_populates="tags")  # noqa: F821 — SQLAlchemy resolves the string forward ref via its registry

    __table_args__ = (
        UniqueConstraint("image_id", "tag", name="uq_image_tag"),
        Index("ix_tags_dataset_tag", "dataset_id", "tag"),
    )
