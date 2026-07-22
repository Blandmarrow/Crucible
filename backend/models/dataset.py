from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    folder_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    declared_subfolders: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Denormalized stats — refreshed via refresh_stats()
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    captioned_count: Mapped[int] = mapped_column(Integer, default=0)
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)

    current_branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Provenance defaults — inherited by any image whose own field is NULL/empty.
    # "" means unset (see backend/licenses.py::resolve_provenance).
    source_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    license: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    attribution: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    images: Mapped[list["Image"]] = relationship(
        "Image", back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True
    )
