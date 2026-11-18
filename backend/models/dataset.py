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
    # Videos are counted separately and deliberately excluded from image_count
    # and total_size_bytes: a video is ~100x the size of the frames it yields,
    # so folding it in would make every dataset card read as bloated, and
    # image_count is what a user compares against an export manifest.
    video_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    video_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

    current_branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # What the dataset is for (M12), read by the analyst. Separate from `description`,
    # which usually says where the images came from. "" means no goal set.
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # The short side the dataset is meant to train at; NULL means use the health
    # report's default. A number beside the goal so no check has to parse prose.
    target_resolution: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The last health report's severity counts, for the dataset card's badge:
    # {"error", "warn", "info", "computed_at"}. Written by `health_service` without
    # bumping `updated_at` (part of the stats cache validator). NULL = never computed.
    health_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Provenance defaults — inherited by any image whose own field is NULL/empty.
    # "" means unset (see backend/licenses.py::resolve_provenance).
    source_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    license: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    attribution: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    images: Mapped[list["Image"]] = relationship(  # noqa: F821 — SQLAlchemy resolves the string forward ref via its registry
        "Image", back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True
    )
    videos: Mapped[list["Video"]] = relationship(  # noqa: F821 — SQLAlchemy resolves the string forward ref via its registry
        "Video", back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True
    )
